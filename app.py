"""
LocalLens - FastAPI backend for local discovery.

Proxies Nominatim (geocoding) and Overpass API (POI discovery)
with caching, rate limiting, and transparent scoring.

Serves the frontend from static/index.html at / and exposes
JSON API at /api/*.
"""

import os
import re
import time
import hashlib
import json
import asyncio
from typing import Optional
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import httpx

from scoring import (
    score_place,
    extract_place_data,
    haversine_m,
    VALID_INTERESTS,
    FOOD_CATEGORIES,
)
from opening_hours import get_open_status


app = FastAPI(
    title="LocalLens API",
    description="Real local discovery powered by OpenStreetMap. Find things to do near you.",
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files (JS, CSS, images) from /static/
app.mount("/static", StaticFiles(directory="static"), name="static")

# ── Config ────────────────────────────────────────────────────────────────────

USER_AGENT = "LocalLens/1.1 (local-discovery; https://locallens.app)"
NOMINATIM_URL = "https://nominatim.openstreetmap.org"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_KUMI = "https://overpass.kumi.systems/api/interpreter"

_geocode_cache: dict = {}
_search_cache: dict = {}
_GEOCODE_CACHE_MAX_AGE = 86400  # 24h for geocode results
_SEARCH_CACHE_MAX_AGE = 1800    # 30 min for search results
_CACHE_MAX_SIZE = 300

_rate_limit: dict = {}
_RATE_LIMIT_WINDOW = 1.0
_RATE_LIMIT_MAX = 2

# Max Overpass timeout
OVERPASS_TIMEOUT_S = 15.0
OVERPASS_TOTAL_TIMEOUT_S = 20.0

# Maximum results to return
MAX_RESULTS = 30

# Minimum score threshold (hide very weak matches)
MIN_SCORE_THRESHOLD = 3.0


# ── Cache ──────────────────────────────────────────────────────────────────────

def _cache_key(prefix: str, args: dict) -> str:
    raw = json.dumps(args, sort_keys=True, default=str)
    h = hashlib.md5(f"{prefix}:{raw}".encode()).hexdigest()[:12]
    return f"{prefix}:{h}"


def _cache_get(cache: dict, key: str, max_age: float):
    entry = cache.get(key)
    if entry is None:
        return None
    if time.time() - entry["timestamp"] > max_age:
        del cache[key]
        return None
    return entry["data"]


def _cache_set(cache: dict, key: str, data):
    if len(cache) >= _CACHE_MAX_SIZE:
        oldest = min(cache.items(), key=lambda x: x[1]["timestamp"])
        del cache[oldest[0]]
    cache[key] = {"data": data, "timestamp": time.time()}


# ── Rate limit ─────────────────────────────────────────────────────────────────

def _check_rate_limit(client_ip: str) -> bool:
    now = time.time()
    if client_ip not in _rate_limit:
        _rate_limit[client_ip] = {"count": 1, "window_start": now}
        return True
    entry = _rate_limit[client_ip]
    if now - entry["window_start"] > _RATE_LIMIT_WINDOW:
        _rate_limit[client_ip] = {"count": 1, "window_start": now}
        return True
    entry["count"] += 1
    if entry["count"] > _RATE_LIMIT_MAX:
        return False
    return True


# ── HTTP helpers ───────────────────────────────────────────────────────────────

async def _http_get(url: str, params: dict = None, headers: dict = None, timeout: float = 15.0):
    headers = headers or {}
    headers.setdefault("User-Agent", USER_AGENT)

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(2):
            try:
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 429:
                    await asyncio.sleep(1.5 ** attempt)
                    continue
                else:
                    raise httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}: {resp.text[:200]}",
                        request=resp.request,
                        response=resp,
                    )
            except httpx.TimeoutException:
                if attempt == 1:
                    raise
                continue
            except Exception:
                if attempt == 1:
                    raise
                await asyncio.sleep(0.5)
    raise httpx.HTTPStatusError("Request failed", request=None, response=None)


async def _http_post(url: str, data: dict, headers: dict = None, timeout: float = 20.0):
    headers = headers or {}
    headers.setdefault("User-Agent", USER_AGENT)

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(2):
            try:
                resp = await client.post(url, data=data, headers=headers)
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 429:
                    await asyncio.sleep(1.5 ** attempt)
                    continue
                else:
                    raise httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}: {resp.text[:200]}",
                        request=resp.request,
                        response=resp,
                    )
            except httpx.TimeoutException:
                if attempt == 1:
                    raise
                continue
            except Exception:
                if attempt == 1:
                    raise
                await asyncio.sleep(0.5)
    raise httpx.HTTPStatusError("Request failed", request=None, response=None)


# ── URL Safety ─────────────────────────────────────────────────────────────────

SAFE_PROTOCOLS = ("https:", "http:")

def sanitize_url(url: str) -> Optional[str]:
    """Sanitize a URL to prevent XSS. Only allows http/https protocols."""
    if not url:
        return None
    url = url.strip()
    # Check for dangerous protocols
    lower = url.lower()
    for proto in ["javascript:", "data:", "vbscript:", "file:", "ftp:"]:
        if lower.startswith(proto):
            return None
    # Ensure it starts with http or https
    if not (lower.startswith("https:") or lower.startswith("http:")):
        # Might be a relative URL or missing protocol
        if lower.startswith("//"):
            url = "https:" + url
        elif "." in lower and " " not in url:
            url = "https://" + url
        else:
            return None
    return url


# ── Geocoding ──────────────────────────────────────────────────────────────────

async def geocode(query: str) -> dict:
    cache_key = _cache_key("geo", {"q": query})
    cached = _cache_get(_geocode_cache, cache_key, _GEOCODE_CACHE_MAX_AGE)
    if cached:
        return cached

    params = {"q": query, "format": "json", "limit": 1, "addressdetails": 1}
    try:
        data = await _http_get(f"{NOMINATIM_URL}/search", params=params, timeout=10.0)
        if data and len(data) > 0:
            result = {
                "lat": float(data[0]["lat"]),
                "lon": float(data[0]["lon"]),
                "display_name": data[0].get("display_name", query),
                "type": data[0].get("type", "unknown"),
                "source": "Nominatim (OpenStreetMap)",
                "query": query,
            }
            _cache_set(_geocode_cache, cache_key, result)
            return result
        raise ValueError(f"No results for '{query}'")
    except ValueError:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Geocoding failed: {str(e)}")


async def reverse_geocode(lat: float, lon: float) -> str:
    """Reverse geocode coordinates to a human-readable place name."""
    cache_key = _cache_key("revgeo", {"lat": round(lat, 4), "lon": round(lon, 4)})
    cached = _cache_get(_geocode_cache, cache_key, _GEOCODE_CACHE_MAX_AGE)
    if cached:
        return cached.get("display_name", f"{lat:.4f}, {lon:.4f}")

    params = {"lat": lat, "lon": lon, "format": "json", "zoom": 16, "addressdetails": 1}
    try:
        data = await _http_get(f"{NOMINATIM_URL}/reverse", params=params, timeout=10.0)
        if data and data.get("display_name"):
            display = data["display_name"]
            # Build a shorter display name from address components
            addr = data.get("address", {})
            parts = []
            for key in ["neighbourhood", "suburb", "city_district", "town", "city",
                         "village", "municipality", "state", "country"]:
                if key in addr and addr[key] not in parts:
                    parts.append(addr[key])
                    if len(parts) >= 3:
                        break
            if not parts:
                for key in ["road", "borough", "county"]:
                    if key in addr:
                        parts.append(addr[key])
                        break
                parts.append(addr.get("city", ""))
                parts.append(addr.get("country", ""))
            short_name = ", ".join(p for p in parts if p)
            if len(short_name) > 60:
                short_name = ", ".join(display.split(",")[0:3])
            if not short_name:
                short_name = ", ".join(display.split(",")[0:2])
            result = {"display_name": short_name}
            _cache_set(_geocode_cache, cache_key, result)
            return result["display_name"]
    except Exception:
        pass

    return f"{lat:.4f}, {lon:.4f}"


# ── POI search ─────────────────────────────────────────────────────────────────

async def search_pois(lat: float, lon: float, radius_m: float,
                      categories: list[str] = None, limit: int = 100) -> list[dict]:
    if not categories:
        categories = [
            "restaurant", "cafe", "bar", "pub", "park", "museum",
            "theatre", "cinema", "arts_centre", "library", "shop",
            "sports_centre", "nightclub", "fast_food", "food_court",
            "biergarten", "playground", "tourism", "hotel", "hostel",
            "gym", "swimming_pool", "college", "university", "pharmacy",
            "marketplace", "supermarket", "church", "viewpoint",
            "nature_reserve", "beach", "zoo", "amusement_ride",
            "casino", "gallery", "studio", "workshop",
        ]

    amenity_cats = [c for c in categories if c in {
        "restaurant", "cafe", "bar", "pub", "nightclub", "fast_food", "food_court",
        "biergarten", "theatre", "cinema", "arts_centre", "library", "museum",
        "hotel", "hostel", "gym", "swimming_pool", "pharmacy", "marketplace",
        "supermarket", "college", "university", "police", "fire_station", "bank",
        "atm", "post_office", "doctor", "dentist", "hospital", "school",
        "toilet", "community_centre", "event_venue", "studio", "workshop",
        "tourism_information", "attraction", "gallery", "ice_cream",
        "amusement_ride", "casino", "stripclub", "dance_floor",
    }]
    leisure_cats = [c for c in categories if c in {
        "park", "playground", "sports_centre", "swimming_pool", "garden",
        "nature_reserve", "recreation_ground", "pitch", "common",
        "water_park", "dog_park",
    }]
    tourism_cats = [c for c in categories if c in {
        "museum", "theatre", "cinema", "arts_centre", "library", "gallery",
        "tourism", "attraction", "hotel", "hostel", "camp_site", "chalet",
        "bed_and_breakfast", "guest_house", "motel", "viewpoint",
    }]
    shop_cats = [c for c in categories if c in {
        "shop", "supermarket", "marketplace", "bakery", "greengrocer",
        "butcher", "fishmonger", "confectionery", "department_store",
    }]

    query_parts = []
    if amenity_cats:
        pattern = "|".join(amenity_cats)
        query_parts.append(f'node["amenity"~"{pattern}"](around:{int(radius_m)},{lat},{lon});')
        query_parts.append(f'way["amenity"~"{pattern}"](around:{int(radius_m)},{lat},{lon});')
    if leisure_cats:
        pattern = "|".join(leisure_cats)
        query_parts.append(f'node["leisure"~"{pattern}"](around:{int(radius_m)},{lat},{lon});')
        query_parts.append(f'way["leisure"~"{pattern}"](around:{int(radius_m)},{lat},{lon});')
    if tourism_cats:
        pattern = "|".join(tourism_cats)
        query_parts.append(f'node["tourism"~"{pattern}"](around:{int(radius_m)},{lat},{lon});')
        query_parts.append(f'way["tourism"~"{pattern}"](around:{int(radius_m)},{lat},{lon});')
    if shop_cats:
        pattern = "|".join(shop_cats)
        query_parts.append(f'node["shop"~"{pattern}"](around:{int(radius_m)},{lat},{lon});')
        query_parts.append(f'way["shop"~"{pattern}"](around:{int(radius_m)},{lat},{lon});')

    if not query_parts:
        return []

    overpass_query = f"""[out:json][timeout:45];
(
{"".join(query_parts)}
);
out center;
"""

    # Check search cache
    cache_args = {"lat": round(lat, 4), "lon": round(lon, 4),
                  "radius": int(radius_m), "cats": sorted(categories)}
    cache_key = _cache_key("search", cache_args)
    cached = _cache_get(_search_cache, cache_key, _SEARCH_CACHE_MAX_AGE)
    if cached:
        return cached

    try:
        data = await _http_post(OVERPASS_URL, {"data": overpass_query},
                                timeout=OVERPASS_TOTAL_TIMEOUT_S)
    except Exception:
        try:
            data = await _http_post(OVERPASS_KUMI, {"data": overpass_query},
                                    timeout=OVERPASS_TOTAL_TIMEOUT_S)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Overpass API unavailable: {str(e)}")

    elements = data.get("elements", [])
    places = []
    for elem in elements:
        place = extract_place_data(elem)
        if place.get("lat") and place.get("lon"):
            dist = haversine_m(lat, lon, place["lat"], place["lon"])
            if dist <= radius_m:
                place["distance_m"] = round(dist, 1)
                places.append(place)

    places.sort(key=lambda p: p.get("distance_m", 999999))

    seen = set()
    deduped = []
    for p in places:
        key = (p["name"].lower().strip(), round(p.get("distance_m", 0) / 50))
        if key not in seen:
            seen.add(key)
            deduped.append(p)

    result = deduped[:limit]
    _cache_set(_search_cache, cache_key, result)
    return result


# ── Frontend ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the frontend HTML from static/index.html."""
    try:
        with open("static/index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    except FileNotFoundError:
        return HTMLResponse(
            content="<!DOCTYPE html><html><head><title>LocalLens</title></head>"
                    "<body><h1>LocalLens</h1><p>Frontend not found. Ensure static/index.html exists.</p></body></html>",
            status_code=404,
        )


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "LocalLens",
        "version": "1.1.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Reverse geocode (dedicated endpoint) ───────────────────────────────────────

@app.post("/api/reverse-geocode")
async def api_reverse_geocode(body: dict):
    lat = body.get("lat")
    lon = body.get("lon")
    if lat is None or lon is None:
        raise HTTPException(status_code=400, detail="lat and lon are required")
    try:
        name = await reverse_geocode(float(lat), float(lon))
    except Exception:
        name = f"{float(lat):.4f}, {float(lon):.4f}"
    return {"display_name": name, "lat": float(lat), "lon": float(lon)}


# ── Recommend ──────────────────────────────────────────────────────────────────

@app.post("/api/recommend")
async def recommend(body: dict):
    location = (body.get("location") or "").strip()
    if not location:
        raise HTTPException(status_code=400, detail="location is required")

    budget = float(body.get("budget", 0))
    budget_currency = body.get("budget_currency", "USD")
    time_available = float(body.get("time_available_hours", 0))
    interests = body.get("interests", [])
    alone = body.get("alone", True)
    max_distance_km = float(body.get("max_distance_km", 5.0))
    start_time = body.get("start_time")

    # Validate interests
    valid_interests = []
    for interest in interests:
        if isinstance(interest, str) and interest.lower().strip() in VALID_INTERESTS:
            valid_interests.append(interest.lower().strip())
    interests = valid_interests

    # Validate and clamp distance
    max_distance_km = max(0.5, min(max_distance_km, 50.0))
    max_distance_m = max_distance_km * 1000

    # Parse start_time if provided
    check_time = None
    if start_time:
        try:
            # start_time could be "HH:MM" or ISO datetime
            now = datetime.now(timezone.utc)
            if re.match(r'^\d{1,2}:\d{2}$', str(start_time)):
                parts = str(start_time).split(":")
                h, m = int(parts[0]), int(parts[1])
                check_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
            else:
                check_time = datetime.fromisoformat(str(start_time))
        except (ValueError, TypeError):
            check_time = None

    # Geocode
    geo_result = await geocode(location)
    user_lat, user_lon = geo_result["lat"], geo_result["lon"]

    # Determine categories to search based on interests
    interest_categories = set()
    interest_cat_map = {
        "food": ["restaurant", "cafe", "fast_food", "bar", "pub", "food_court"],
        "drinks": ["bar", "pub", "nightclub"],
        "culture": ["museum", "theatre", "cinema", "arts_centre", "library"],
        "art": ["arts_centre", "gallery", "museum"],
        "music": ["bar", "pub", "nightclub", "theatre"],
        "history": ["museum", "tourism"],
        "outdoors": ["park", "playground", "nature_reserve", "beach", "sports_centre"],
        "nature": ["park", "nature_reserve", "beach", "viewpoint"],
        "sports": ["sports_centre", "gym", "swimming_pool", "pitch"],
        "shopping": ["shop", "supermarket", "marketplace"],
        "party": ["nightclub", "bar", "pub"],
        "relax": ["cafe", "park", "library", "bar"],
        "learning": ["library", "museum", "workshop"],
        "family": ["playground", "park", "zoo", "swimming_pool", "food_court"],
        "nightlife": ["nightclub", "bar", "pub"],
        "solo": ["cafe", "library", "museum", "park", "cinema", "theatre"],
        "group": ["restaurant", "sports_centre", "food_court", "event_venue"],
    }
    for interest in interests:
        cats = interest_cat_map.get(interest, [])
        interest_categories.update(cats)

    # Default broad search if no interests
    if not interest_categories:
        interest_categories = [
            "restaurant", "cafe", "bar", "pub", "park", "museum",
            "theatre", "cinema", "arts_centre", "library", "shop",
            "sports_centre", "nightclub", "fast_food", "food_court",
            "playground", "tourism",
        ]

    search_cats = list(dict.fromkeys(interest_categories))

    # Search
    try:
        places = await search_pois(user_lat, user_lon, max_distance_m, search_cats, limit=100)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"POI search failed: {str(e)}")

    if not places:
        return {
            "query": {
                "location": location,
                "geocoded_to": geo_result.get("display_name"),
                "lat": user_lat,
                "lon": user_lon,
                "budget": budget,
                "budget_currency": budget_currency,
                "time_available_hours": time_available,
                "interests": interests,
                "alone": alone,
                "max_distance_km": max_distance_km,
                "start_time": start_time,
            },
            "results": [],
            "data_sources": ["nominatim_osm"],
            "coverage_note": "No places found in this area. OpenStreetMap coverage may be sparse here.",
            "suggestions": [
                "Try a nearby city or larger town.",
                "Broaden your interests to include more categories.",
                "Increase the search radius.",
                "Try without specifying interests for broader results.",
            ],
        }

    # Score all places
    user_constraints = {"lat": user_lat, "lon": user_lon}
    scored = []
    for place in places:
        result = score_place(
            place=place,
            user_constraints=user_constraints,
            max_distance_m=max_distance_m,
            available_hours=time_available,
            budget_amount=budget,
            currency=budget_currency,
            alone=alone,
            interests=interests,
            check_time=check_time,
        )
        scored.append(result)

    # Apply time-awareness when a specific start time is requested
    if check_time is not None:
        # Categorize every result without changing scores (keeps breakdown bars consistent)
        open_places = []
        unknown_hours = []
        closed_places = []
        for r in scored:
            if r.open_status and r.open_status.has_hours_data:
                if r.open_status.is_open:
                    open_places.append(r)
                else:
                    closed_places.append(r)
            else:
                unknown_hours.append(r)

        # If we have enough open/unknown options, exclude clearly-closed places.
        # Unknown-hours places are down-ranked (kept below open ones) since
        # we can't confirm they'll be open.
        if len(open_places) + len(unknown_hours) >= 5:
            scored = open_places + unknown_hours
        else:
            # Too few options — include closed ones at the very bottom
            scored = open_places + unknown_hours + closed_places

    scored.sort(key=lambda r: r.score, reverse=True)

    # When a specific start time was requested, keep time-awareness as the
    # primary ordering: open-now first, unknown-hours next, closed last.
    # This down-ranks unknown-hours results instead of pretending they're open.
    if check_time is not None:
        def _time_tier(r):
            if r.open_status and r.open_status.has_hours_data:
                return 2 if r.open_status.is_open else 0
            return 1
        scored.sort(key=lambda r: (_time_tier(r), r.score), reverse=True)

    # Filter out unnamed places unless there are very few named results
    filtered = []
    unnamed = []
    for r in scored:
        name = r.place.get("name", "")
        if name.startswith("Unnamed "):
            unnamed.append(r)
        elif r.score >= MIN_SCORE_THRESHOLD:
            filtered.append(r)
    if not filtered:
        # Only unnamed results exist — keep the best ones but cap their score
        filtered = [r for r in unnamed[:10]]
        for r in filtered:
            r.score = min(r.score, 6.0)

    results = []
    for r in filtered[:MAX_RESULTS]:
        place = r.place
        tags = place.get("tags", {})
        website = sanitize_url(tags.get("website", ""))
        phone = tags.get("phone") or tags.get("contact:phone")

        results.append({
            "name": place["name"],
            "category": place["category"],
            "lat": place.get("lat"),
            "lon": place.get("lon"),
            "distance_km": r.distance_km,
            "estimated_time_hours": r.est_time,
            "cost_estimate": r.cost_estimate,
            "opening_hours": tags.get("opening_hours", "Unknown"),
            "open_status": {
                "is_open": r.open_status.is_open if r.open_status else None,
                "status_text": r.open_status.status_text if r.open_status else "Unknown",
                "next_open": r.open_status.next_open_text if r.open_status else "",
            } if r.open_status else None,
            "address": tags.get("addr:full")
                       or tags.get("addr:street")
                       or tags.get("address")
                       or None,
            "phone": phone,
            "website": website,
            "osm_tags": {k: v for k, v in tags.items()
                         if k not in ("name", "addr:full", "addr:street", "address",
                                       "phone", "website", "contact:phone",
                                       "opening_hours")},
            "why_it_matches": r.why,
            "score": r.score,
            "breakdown": r.breakdown,
            "confidence": r.confidence,
            "confidence_details": r.confidence_details,
            "source": place.get("source", "OpenStreetMap via Overpass API"),
            "osm_id": place.get("id"),
            "osm_type": place.get("osm_type"),
        })

    categories_found = set(p["category"] for p in places)

    return {
        "query": {
            "location": location,
            "geocoded_to": geo_result.get("display_name"),
            "lat": user_lat,
            "lon": user_lon,
            "budget": budget,
            "budget_currency": budget_currency,
            "time_available_hours": time_available,
            "interests": interests,
            "alone": alone,
            "max_distance_km": max_distance_km,
            "start_time": start_time,
        },
        "results": results,
        "data_sources": ["nominatim_osm", "overpass_osm"],
        "coverage": {
            "places_found": len(places),
            "categories_found": sorted(list(categories_found)),
            "search_radius_m": max_distance_m,
        },
        "scoring_model": {
            "factors": {
                "distance": "20% — closer is better",
                "budget": "25% — fits your budget (neutral when no budget set)",
                "time": "20% — fits your available time",
                "interests": "25% — matches your interests",
                "companion": "10% — solo/group suitability",
            },
        },
    }


# ── Refine ─────────────────────────────────────────────────────────────────────

@app.post("/api/refine")
async def refine(body: dict):
    prev_results = body.get("previous_results", [])
    refinement = (body.get("refinement") or "").strip()
    new_constraints = body.get("new_constraints", {})

    if not prev_results:
        raise HTTPException(status_code=400, detail="No previous results to refine")

    refinement_lower = refinement.lower()

    # If new constraints provided, re-score properly with the scoring engine
    if new_constraints:
        budget = float(new_constraints.get("budget",
                       (prev_results[0].get("query") or {}).get("budget", 0)))
        budget_currency = new_constraints.get("budget_currency",
                        (prev_results[0].get("query") or {}).get("budget_currency", "USD"))
        time_available = float(new_constraints.get("time_available_hours",
                                    (prev_results[0].get("query") or {}).get("time_available_hours", 0)))
        interests = new_constraints.get("interests",
                        (prev_results[0].get("query") or {}).get("interests", []))
        alone = new_constraints.get("alone",
                       (prev_results[0].get("query") or {}).get("alone", True))
        max_distance_km = float(new_constraints.get("max_distance_km",
                                    (prev_results[0].get("query") or {}).get("max_distance_km", 5.0)))

        places = []
        for pr in prev_results:
            place = {
                "name": pr.get("name", ""),
                "category": pr.get("category", "unknown"),
                "lat": pr.get("lat"),
                "lon": pr.get("lon"),
                "tags": pr.get("osm_tags", {}),
                "id": pr.get("osm_id"),
                "osm_type": pr.get("osm_type"),
                "source": pr.get("source"),
            }
            # Reconstruct opening_hours from the result
            if pr.get("opening_hours") and pr["opening_hours"] != "Unknown":
                place["tags"]["opening_hours"] = pr["opening_hours"]
            if place.get("lat") is None or place.get("lon") is None:
                q = (prev_results[0].get("query") or {})
                place["lat"] = q.get("lat")
                place["lon"] = q.get("lon")
            place["distance_m"] = pr.get("distance_km", 0) * 1000
            places.append(place)

        user_constraints = {
            "lat": (prev_results[0].get("query") or {}).get("lat", 0),
            "lon": (prev_results[0].get("query") or {}).get("lon", 0),
        }
        max_distance_m = max_distance_km * 1000

        scored = []
        for place in places:
            result = score_place(
                place=place,
                user_constraints=user_constraints,
                max_distance_m=max_distance_m,
                available_hours=time_available,
                budget_amount=budget,
                currency=budget_currency,
                alone=alone,
                interests=interests,
            )
            scored.append(result)

        scored.sort(key=lambda r: r.score, reverse=True)

        refined_results = _format_scored_results(scored, prev_results)

        return {
            "refinement_applied": refinement,
            "new_constraints": new_constraints,
            "results": refined_results,
            "note": "Results re-scored based on updated constraints.",
        }

    # ── Conversational refinement ──

    # Detect "top N" requests
    top_match = re.search(r'\btop\s+(\d+)\b', refinement_lower)
    if top_match:
        n = min(int(top_match.group(1)), MAX_RESULTS)
        # Sort by score descending and take top N
        sorted_results = sorted(prev_results, key=lambda r: r.get("score", 0), reverse=True)
        top_results = sorted_results[:n]
        return {
            "refinement_applied": refinement,
            "results": top_results,
            "note": f"Showing top {n} results by score.",
        }

    # Detect exclusion patterns: "no food", "nothing involving food", etc.
    exclude_food = any(w in refinement_lower for w in [
        "no food", "not food", "nothing involving food",
        "no restaurants", "no eating", "avoid food", "without food",
        "exclude food", "remove food", "no cafe", "no bar", "no pub",
        "no fast food", "no food places",
    ])
    exclude_nightlife = any(w in refinement_lower for w in [
        "no nightlife", "no bars", "no clubs", "no drinking",
        "no alcohol", "without nightlife",
    ])
    exclude_shopping = any(w in refinement_lower for w in [
        "no shopping", "no shops", "no stores", "without shopping",
    ])

    # Detect "make it cheaper" / "more affordable"
    want_cheaper = any(w in refinement_lower for w in [
        "cheaper", "budget", "save money", "less expensive",
        "low cost", "affordable", "free", "inexpensive",
        "more affordable", "lower cost", "not expensive",
    ])
    want_expensive = any(w in refinement_lower for w in [
        "expensive", "fancy", "upscale", "splurge",
        "premium", "luxury", "good quality", "high end", "fine dining",
    ])
    want_evening = any(w in refinement_lower for w in [
        "after 8", "after 8pm", "after 20", "evening", "night",
        "late", "open late", "tonight", "after dark",
    ])
    want_culture = any(w in refinement_lower for w in [
        "culture", "cultural", "art", "museum", "history", "theatre",
        "cinema", "gallery", "exhibit", "show", "performance", "play", "film",
    ])
    want_outdoors = any(w in refinement_lower for w in [
        "nature", "outdoors", "outdoor", "park", "walk", "hike", "outside",
        "green", "garden", "trail", "fresh air",
    ])
    want_closer = any(w in refinement_lower for w in [
        "closer", "near", "nearby", "close by",
        "walking distance", "walking", "short walk",
    ])
    want_farther = any(w in refinement_lower for w in [
        "farther", "further", "wider", "more distance",
        "travel", "drive", "longer trip",
    ])
    want_quick = any(w in refinement_lower for w in [
        "quick", "fast", "short time", "brief", "under an hour",
        "quick visit", "short",
    ])

    # Check if any recognized refinement was detected
    recognized = (exclude_food or exclude_nightlife or exclude_shopping or
                  want_cheaper or want_expensive or want_evening or
                  want_culture or want_outdoors or want_closer or
                  want_farther or want_quick or top_match is not None)

    if not recognized:
        return {
            "refinement_applied": refinement,
            "results": prev_results[:MAX_RESULTS],
            "note": f"Refinement \"{refinement}\" was not understood. "
                    f"Try: \"Top 3\", \"Nothing involving food\", \"Make it cheaper\", "
                    f"\"After 8pm\", \"More cultural\", \"Outdoors\", \"Closer\".",
            "unrecognized": True,
        }

    # Apply refinement filters and score adjustments
    adjusted = []
    for pr in prev_results:
        score = pr.get("score", 5.0)
        category = (pr.get("category") or "").lower()
        cat_tag = (pr.get("osm_tags", {}).get("amenity", "") or
                   pr.get("osm_tags", {}).get("leisure", "") or
                   pr.get("osm_tags", {}).get("shop", "") or
                   pr.get("osm_tags", {}).get("tourism", "") or "").lower()
        combined_cat = f"{category} {cat_tag}"

        skip = False
        adjustments = []

        # ── Exclusion filters ──
        if exclude_food and any(fc in combined_cat for fc in FOOD_CATEGORIES):
            skip = True
        if exclude_nightlife and any(nc in combined_cat for nc in ["nightclub", "bar", "pub", "dance_floor", "stripclub"]):
            skip = True
        if exclude_shopping and any(sc in combined_cat for sc in ["shop", "supermarket", "marketplace"]):
            skip = True

        if skip:
            continue

        # ── Cheaper boost ──
        if want_cheaper:
            cost = pr.get("cost_estimate", {}).get("amount", 0)
            tier = pr.get("cost_estimate", {}).get("tier", "varies")
            if tier == "free":
                score += 2.0
                adjustments.append("boosted — free")
            elif tier == "budget":
                score += 1.5
                adjustments.append("boosted — budget-friendly")
            elif tier == "moderate":
                score += 0.5
                adjustments.append("slightly boosted")
            elif tier == "expensive":
                score -= 1.5
                adjustments.append("reduced — higher cost")

        # ── Expensive boost ──
        if want_expensive:
            tier = pr.get("cost_estimate", {}).get("tier", "varies")
            if tier == "expensive":
                score += 2.0
                adjustments.append("boosted — higher-end")
            elif tier == "moderate":
                score += 0.5
            elif tier in ("free", "budget"):
                score -= 1.0

        # ── Evening filter boost ──
        if want_evening:
            oh = (pr.get("opening_hours") or "").lower()
            open_status = pr.get("open_status", {})
            if open_status and open_status.get("is_open"):
                score += 1.0
                adjustments.append("open now")
            elif any(t in oh for t in ["22:", "23:", "0:", "1:", "2:", "20:", "21:"]):
                score += 1.5
                adjustments.append("open late")
            if any(nc in combined_cat for nc in ["nightclub", "bar", "pub"]):
                score += 1.0
                adjustments.append("likely open late")

        # ── Culture boost ──
        if want_culture:
            if any(cc in combined_cat for cc in ["museum", "theatre", "cinema", "arts_centre", "gallery", "tourism", "attraction", "library"]):
                score += 2.0
                adjustments.append("boosted — cultural venue")
            else:
                score -= 1.0

        # ── Outdoors boost ──
        if want_outdoors:
            if any(oc in combined_cat for oc in ["park", "leisure", "garden", "nature_reserve", "playground", "beach", "viewpoint", "recreation_ground"]):
                score += 2.0
                adjustments.append("boosted — outdoor venue")
            else:
                score -= 1.0

        # ── Closer boost ──
        if want_closer:
            dist = pr.get("distance_km", 0)
            if dist < 1.0:
                score += 1.5
                adjustments.append("boosted — very close")
            elif dist < 3.0:
                score += 0.5
                adjustments.append("boosted — close by")

        # ── Farther boost ──
        if want_farther:
            dist = pr.get("distance_km", 0)
            if dist > 1.0:
                score += 0.5 * min(dist / 5, 1.0)
                adjustments.append("boosted for being farther away")

        # ── Quick boost ──
        if want_quick:
            est = pr.get("estimated_time_hours", 999)
            if est <= 0.5:
                score += 2.0
                adjustments.append("boosted — quick visit")
            elif est <= 1.0:
                score += 1.0
                adjustments.append("boosted — short duration")

        score = max(0.0, min(10.0, score))

        adjusted.append({
            "name": pr.get("name"),
            "category": pr.get("category"),
            "lat": pr.get("lat"),
            "lon": pr.get("lon"),
            "distance_km": pr.get("distance_km"),
            "estimated_time_hours": pr.get("estimated_time_hours"),
            "cost_estimate": pr.get("cost_estimate"),
            "opening_hours": pr.get("opening_hours"),
            "open_status": pr.get("open_status"),
            "address": pr.get("address"),
            "phone": pr.get("phone"),
            "website": pr.get("website"),
            "why_it_matches": pr.get("why_it_matches", ""),
            "score": round(score, 1),
            "breakdown": pr.get("breakdown"),
            "confidence": pr.get("confidence"),
            "confidence_details": pr.get("confidence_details"),
            "source": pr.get("source"),
            "osm_tags": pr.get("osm_tags"),
            "osm_id": pr.get("osm_id"),
            "osm_type": pr.get("osm_type"),
        })

    adjusted.sort(key=lambda r: r.get("score", 0), reverse=True)

    # Build note
    changes = []
    if exclude_food:
        changes.append("removed food venues")
    if exclude_nightlife:
        changes.append("removed nightlife venues")
    if exclude_shopping:
        changes.append("removed shopping venues")
    if want_cheaper:
        changes.append("prioritized budget-friendly options")
    if want_expensive:
        changes.append("prioritized higher-end options")
    if want_evening:
        changes.append("prioritized evening/late options")
    if want_culture:
        changes.append("prioritized cultural venues")
    if want_outdoors:
        changes.append("prioritized outdoor venues")
    if want_closer:
        changes.append("prioritized closer options")
    if want_farther:
        changes.append("prioritized farther options")
    if want_quick:
        changes.append("prioritized shorter activities")

    note = f"Refined: {', '.join(changes) if changes else 'general re-ranking'}."
    note += f" {len(adjusted)} results."

    return {
        "refinement_applied": refinement,
        "results": adjusted[:MAX_RESULTS],
        "note": note,
    }


def _format_scored_results(scored: list, prev_results: list) -> list:
    """Convert ScoredResult objects to the standard result format."""
    results = []
    for r in scored:
        place = r.place
        tags = place.get("tags", {})
        # Try to get original result data for fields we don't recompute
        original = None
        for pr in prev_results:
            if pr.get("name") == place.get("name"):
                original = pr
                break

        website = sanitize_url(tags.get("website", "") or (original or {}).get("website", ""))
        phone = tags.get("phone") or tags.get("contact:phone") or (original or {}).get("phone")

        results.append({
            "name": place.get("name", ""),
            "category": place.get("category", "unknown"),
            "lat": place.get("lat"),
            "lon": place.get("lon"),
            "distance_km": r.distance_km,
            "estimated_time_hours": r.est_time,
            "cost_estimate": r.cost_estimate,
            "opening_hours": tags.get("opening_hours", "Unknown"),
            "open_status": {
                "is_open": r.open_status.is_open if r.open_status else None,
                "status_text": r.open_status.status_text if r.open_status else "Unknown",
                "next_open": r.open_status.next_open_text if r.open_status else "",
            } if r.open_status else None,
            "address": tags.get("addr:full")
                       or tags.get("addr:street")
                       or tags.get("address")
                       or (original or {}).get("address")
                       or None,
            "phone": phone,
            "website": website,
            "osm_tags": {k: v for k, v in tags.items()
                         if k not in ("name", "addr:full", "addr:street", "address",
                                       "phone", "website", "contact:phone",
                                       "opening_hours")},
            "why_it_matches": r.why,
            "score": r.score,
            "breakdown": r.breakdown,
            "confidence": r.confidence,
            "confidence_details": r.confidence_details,
            "source": place.get("source") or (original or {}).get("source"),
            "osm_id": place.get("id") or (original or {}).get("osm_id"),
            "osm_type": place.get("osm_type") or (original or {}).get("osm_type"),
        })
    return results


# ── Error handler ──────────────────────────────────────────────────────────────

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": True,
            "status_code": exc.status_code,
            "detail": exc.detail,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


if __name__ == "__main__":
    import uvicorn
    print("LocalLens starting on http://0.0.0.0:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
