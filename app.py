"""
LocalLens - FastAPI backend for local discovery.

Proxies Nominatim (geocoding) and Overpass API (POI discovery)
with caching, rate limiting, and transparent scoring.

Serves the frontend from static/index.html at / and exposes
JSON API at /api/*.
"""

import os
import time
import hashlib
import json
import asyncio
from typing import Optional
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
import httpx

from scoring import (
    score_place,
    extract_place_data,
    haversine_m,
)


app = FastAPI(
    title="LocalLens API",
    description="Real local discovery powered by OpenStreetMap. Find things to do near you.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Config ────────────────────────────────────────────────────────────────────

USER_AGENT = "LocalLens/1.0 (local discovery app)"
NOMINATIM_URL = "https://nominatim.openstreetmap.org"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_KUMI = "https://overpass.kumi.systems/api/interpreter"

_cache: dict = {}
_CACHE_MAX_AGE = 3600
_CACHE_MAX_SIZE = 200

_rate_limit: dict = {}
_RATE_LIMIT_WINDOW = 1.0
_RATE_LIMIT_MAX = 1


# ── Cache ──────────────────────────────────────────────────────────────────────

def _cache_key(prefix: str, args: dict) -> str:
    raw = json.dumps(args, sort_keys=True, default=str)
    h = hashlib.md5(f"{prefix}:{raw}".encode()).hexdigest()[:12]
    return f"{prefix}:{h}"


def _cache_get(key: str):
    entry = _cache.get(key)
    if entry is None:
        return None
    if time.time() - entry["timestamp"] > _CACHE_MAX_AGE:
        del _cache[key]
        return None
    return entry["data"]


def _cache_set(key: str, data: dict):
    if len(_cache) >= _CACHE_MAX_SIZE:
        oldest = min(_cache.items(), key=lambda x: x[1]["timestamp"])
        del _cache[oldest[0]]
    _cache[key] = {"data": data, "timestamp": time.time()}


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

async def _http_get(url: str, params: dict = None, headers: dict = None, timeout: float = 30.0):
    headers = headers or {}
    headers.setdefault("User-User-Agent", USER_AGENT)

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(3):
            try:
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 429:
                    await asyncio.sleep(2 ** attempt)
                    continue
                else:
                    raise httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}: {resp.text[:200]}",
                        request=resp.request,
                        response=resp,
                    )
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(1)
    raise httpx.HTTPStatusError("Max retries exceeded", request=None, response=None)


async def _http_post(url: str, data: dict, headers: dict = None, timeout: float = 60.0):
    headers = headers or {}
    headers.setdefault("User-Agent", USER_AGENT)

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(3):
            try:
                resp = await client.post(url, data=data, headers=headers)
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 429:
                    await asyncio.sleep(2 ** attempt)
                    continue
                else:
                    raise httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}: {resp.text[:200]}",
                        request=resp.request,
                        response=resp,
                    )
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(1)
    raise httpx.HTTPStatusError("Max retries exceeded", request=None, response=None)


# ── Geocoding ──────────────────────────────────────────────────────────────────

async def geocode(query: str) -> dict:
    cache_key = _cache_key("geo", {"q": query})
    cached = _cache_get(cache_key)
    if cached:
        return cached

    params = {"q": query, "format": "json", "limit": 1, "addressdetails": 1}
    try:
        data = await _http_get(f"{NOMINATIM_URL}/search", params=params, timeout=15.0)
        if data and len(data) > 0:
            result = {
                "lat": float(data[0]["lat"]),
                "lon": float(data[0]["lon"]),
                "display_name": data[0].get("display_name", query),
                "type": data[0].get("type", "unknown"),
                "source": "Nominatim (OpenStreetMap)",
                "query": query,
            }
            _cache_set(cache_key, result)
            return result
        raise ValueError(f"No results for '{query}'")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Geocoding failed: {str(e)}")


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

    try:
        data = await _http_post(OVERPASS_URL, {"data": overpass_query}, timeout=50.0)
    except Exception:
        try:
            data = await _http_post(OVERPASS_KUMI, {"data": overpass_query}, timeout=50.0)
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

    return deduped[:limit]


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
        "version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


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

    max_distance_m = max_distance_km * 1000

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
        cats = interest_cat_map.get(interest.lower().strip(), [])
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
        )
        scored.append(result)

    scored.sort(key=lambda r: r.score, reverse=True)

    results = []
    for r in scored[:30]:
        place = r.place
        results.append({
            "name": place["name"],
            "category": place["category"],
            "distance_km": r.distance_km,
            "estimated_time_hours": r.est_time,
            "cost_estimate": r.cost_estimate,
            "opening_hours": place["tags"].get("opening_hours", "Unknown"),
            "address": place["tags"].get("addr:full")
                       or place["tags"].get("addr:street")
                       or place["tags"].get("address")
                       or "Location only",
            "phone": place["tags"].get("phone"),
            "website": place["tags"].get("website"),
            "osm_tags": {k: v for k, v in place["tags"].items()
                         if k not in ("name", "addr:full", "addr:street", "address",
                                       "phone", "website")},
            "why_it_matches": r.why,
            "score": r.score,
            "breakdown": r.breakdown,
            "confidence": r.confidence,
            "confidence_details": r.confidence_details,
            "source": r.place.get("source", "OpenStreetMap via Overpass API"),
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
                "budget": "25% — fits your budget",
                "time": "20% — fits your available time",
                "interests": "25% — matches your interests",
                "companion": "10% — solo/group suitability",
            },
            "confidence_tiers": {
                "verified": "Data directly from OpenStreetMap",
                "estimated": "Derived from available data or typical values",
                "unknown": "Insufficient data for reliable scoring",
            },
        },
    }


# ── Refine ─────────────────────────────────────────────────────────────────────

@app.post("/api/refine")
async def refine(body: dict):
    prev_results = body.get("previous_results", [])
    refinement = (body.get("refinement") or "").strip().lower()
    new_constraints = body.get("new_constraints", {})

    if not prev_results:
        raise HTTPException(status_code=400, detail="No previous results to refine")

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

        refined_results = []
        for r in scored[:30]:
            refined_results.append({
                "name": r.place["name"],
                "category": r.place["category"],
                "distance_km": r.distance_km,
                "estimated_time_hours": r.est_time,
                "cost_estimate": r.cost_estimate,
                "opening_hours": r.place["tags"].get("opening_hours", "Unknown"),
                "why_it_matches": r.why,
                "score": r.score,
                "breakdown": r.breakdown,
                "confidence": r.confidence,
                "confidence_details": r.confidence_details,
                "source": r.place.get("source"),
            })

        return {
            "refinement_applied": refinement,
            "new_constraints": new_constraints,
            "results": refined_results,
            "note": "Results re-scored based on updated constraints.",
        }

    # Conversational refinement — interpret and adjust scores heuristically
    adjusted = []
    for pr in prev_results:
        orig_score = pr.get("score", 5.0)
        new_score = orig_score
        adjustments = []

        cost = pr.get("cost_estimate", {}).get("amount", 999)
        budget_orig = (pr.get("query") or {}).get("budget", 0)

        # --- "cheaper" / "budget" / "save money" ---
        if any(w in refinement for w in ["cheaper", "budget", "save money", "less expensive",
                                           "low cost", "affordable", "free", "inexpensive"]):
            if budget_orig > 0 and cost > 0:
                ratio = cost / budget_orig
                if ratio < 0.3:
                    new_score += 2.0
                    adjustments.append("boosted for being budget-friendly")
                elif ratio < 0.6:
                    new_score += 1.0
                    adjustments.append("boosted for fitting budget")

        # --- "expensive" / "fancy" / "upscale" ---
        if any(w in refinement for w in ["expensive", "fancy", "upscale", "splurge",
                                           "premium", "luxury", "good quality"]):
            if budget_orig > 0 and cost > 0:
                ratio = cost / budget_orig
                if ratio > 0.8:
                    new_score += 1.5
                    adjustments.append("boosted for being higher-end")

        # --- "food" / "eat" / "dinner" / "drink" ---
        if any(w in refinement for w in ["food", "eat", "dinner", "lunch", "brunch", "meal",
                                           "drink", "drinks", "bar", "pub", "alcohol"]):
            cat = pr.get("category", "").lower()
            if any(c in cat for c in ["restaurant", "cafe", "bar", "pub", "fast_food", "food_court"]):
                new_score += 2.0
                adjustments.append("boosted for food/drink relevance")
            else:
                new_score -= 1.0
                adjustments.append("slight penalty for not being food-related")

        # --- "no food" / "not food" ---
        if any(w in refinement for w in ["no food", "not food", "nothing involving food",
                                           "no restaurants", "no eating", "avoid food"]):
            cat = pr.get("category", "").lower()
            if any(c in cat for c in ["restaurant", "cafe", "bar", "pub", "fast_food",
                                       "food_court", "biergarten", "ice_cream"]):
                new_score -= 5.0
                adjustments.append("removed — this is a food venue")
            else:
                new_score += 0.5
                adjustments.append("slight boost for non-food")

        # --- "after 8pm" / "evening" / "night" ---
        if any(w in refinement for w in ["after 8", "after 8pm", "after 20", "evening", "night",
                                           "late", "open late"]):
            hours = pr.get("opening_hours", "").lower()
            if hours and "unknown" not in hours:
                if any(h in hours for h in ["22:", "23:", "0:", "1:", "2:", "20:", "21:"]):
                    new_score += 2.0
                    adjustments.append("boosted for evening/late hours")
            cat = pr.get("category", "").lower()
            if any(c in cat for c in ["nightclub", "bar", "pub"]):
                new_score += 1.5
                adjustments.append("likely open late based on category")

        # --- "morning" / "early" / "before noon" ---
        if any(w in refinement for w in ["morning", "early", "before noon", "before 12",
                                           "breakfast", "daytime", "afternoon"]):
            cat = pr.get("category", "").lower()
            if any(c in cat for c in ["cafe", "restaurant", "museum", "park", "library", "gallery"]):
                new_score += 1.0
                adjustments.append("fits daytime activity")

        # --- "further" / "travel farther" ---
        if any(w in refinement for w in ["farther", "further", "wider", "more distance",
                                           "travel", "drive", "longer trip"]):
            dist = pr.get("distance_km", 0)
            if dist > 1.0:
                new_score += 0.5 * min(dist / 5, 1.0)
                adjustments.append("slightly boosted for being farther away")

        # --- "closer" / "near" / "nearby" ---
        if any(w in refinement for w in ["closer", "near", "nearby", "close by",
                                           "walking distance", "walking", "short walk"]):
            dist = pr.get("distance_km", 0)
            if dist < 1.0:
                new_score += 1.5
                adjustments.append("boosted for proximity")
            elif dist < 3.0:
                new_score += 0.5
                adjustments.append("slightly boosted for being close")

        # --- "culture" / "art" / "museum" ---
        if any(w in refinement for w in ["culture", "art", "museum", "history", "theatre",
                                           "cinema", "gallery", "exhibit", "show", "performance",
                                           "play", "film"]):
            cat = pr.get("category", "").lower()
            if any(c in cat for c in ["museum", "theatre", "cinema", "arts_centre", "gallery",
                                       "tourism", "attraction", "library"]):
                new_score += 2.0
                adjustments.append("boosted for culture/arts relevance")

        # --- "nature" / "outdoors" / "park" ---
        if any(w in refinement for w in ["nature", "outdoors", "park", "walk", "hike", "outside",
                                           "green", "garden", "trail", "fresh air"]):
            cat = pr.get("category", "").lower()
            if any(c in cat for c in ["park", "leisure", "garden", "nature_reserve", "playground",
                                       "beach", "viewpoint", "recreation_ground"]):
                new_score += 2.0
                adjustments.append("boosted for outdoors/nature relevance")

        # --- "shopping" / "shop" / "market" ---
        if any(w in refinement for w in ["shopping", "shop", "buy", "market", "mall", "browse",
                                           "store"]):
            cat = pr.get("category", "").lower()
            if any(c in cat for c in ["shop", "supermarket", "marketplace", "shopping_centre"]):
                new_score += 2.0
                adjustments.append("boosted for shopping relevance")

        # --- "alone" / "solo" ---
        if any(w in refinement for w in ["alone", "solo", "by myself", "on my own", "by my self"]):
            cat = pr.get("category", "").lower()
            if any(c in cat for c in ["cafe", "library", "museum", "park", "cinema", "theatre",
                                       "gallery", "bookshop", "church"]):
                new_score += 1.5
                adjustments.append("boosted for solo-friendly venue")

        # --- "with friends" / "group" / "family" ---
        if any(w in refinement for w in ["with friends", "with others", "with people", "group",
                                           "friends", "family", "together", "social", "company"]):
            cat = pr.get("category", "").lower()
            if any(c in cat for c in ["restaurant", "bar", "pub", "sports_centre", "food_court",
                                       "event_venue", "amusement_ride"]):
                new_score += 1.5
                adjustments.append("boosted for group-friendly venue")

        # --- "quick" / "fast" / "short" ---
        if any(w in refinement for w in ["quick", "fast", "short time", "brief", "under an hour",
                                           "quick visit", "short"]):
            est_time = pr.get("estimated_time_hours", 999)
            if est_time <= 1.0:
                new_score += 1.5
                adjustments.append("boosted for being a quick activity")
            elif est_time <= 2.0:
                new_score += 0.5
                adjustments.append("slightly boosted for moderate duration")

        # --- "long" / "spend time" ---
        if any(w in refinement for w in ["long", "hours", "spend time", "kill time", "all morning",
                                           "all afternoon", "all day"]):
            est_time = pr.get("estimated_time_hours", 0)
            if est_time >= 2.0:
                new_score += 1.0
                adjustments.append("boosted for longer activity")

        new_score = max(0.0, min(10.0, new_score))

        adjusted.append({
            "name": pr.get("name"),
            "category": pr.get("category"),
            "distance_km": pr.get("distance_km"),
            "estimated_time_hours": pr.get("estimated_time_hours"),
            "cost_estimate": pr.get("cost_estimate"),
            "opening_hours": pr.get("opening_hours"),
            "why_it_matches": pr.get("why_it_matches", ""),
            "score": round(new_score, 1),
            "original_score": round(orig_score, 1),
            "adjustments": adjustments,
            "confidence": pr.get("confidence"),
            "confidence_details": pr.get("confidence_details"),
            "source": pr.get("source"),
        })

    adjusted.sort(key=lambda r: r["score"], reverse=True)
    filtered = [r for r in adjusted if r["score"] > 0]

    return {
        "refinement_applied": refinement,
        "interpretation": _interpret_refinement(refinement),
        "results": filtered[:30],
        "note": f"Adjusted {len(prev_results)} results based on: '{refinement}'. "
                f"Shows {len(filtered)} results with score > 0.",
    }


def _interpret_refinement(refinement: str) -> dict:
    r = refinement.lower()
    changes = []

    if any(w in r for w in ["cheaper", "budget", "save"]):
        changes.append("Boosted budget-friendly options")
    if any(w in r for w in ["expensive", "fancy", "upscale", "splurge"]):
        changes.append("Prioritized higher-end options")
    if any(w in r for w in ["food", "eat", "drink"]):
        changes.append("Prioritized food & drink venues")
    if any(w in r for w in ["no food", "not food"]):
        changes.append("Removed food venues; boosted non-food options")
    if any(w in r for w in ["after 8", "evening", "night", "late"]):
        changes.append("Prioritized venues open in the evening")
    if any(w in r for w in ["further", "further", "wider"]):
        changes.append("Slightly boosted farther-away options")
    if any(w in r for w in ["closer", "near", "nearby"]):
        changes.append("Boosted nearby/closer options")
    if any(w in r for w in ["morning", "early"]):
        changes.append("Prioritized daytime activities")
    if any(w in r for w in ["culture", "art", "museum"]):
        changes.append("Prioritized cultural/arts venues")
    if any(w in r for w in ["nature", "outdoors", "park"]):
        changes.append("Prioritized outdoor/nature venues")
    if any(w in r for w in ["shopping", "shop", "market"]):
        changes.append("Prioritized shopping venues")
    if any(w in r for w in ["alone", "solo"]):
        changes.append("Boosted solo-friendly venues")
    if any(w in r for w in ["friends", "group", "with"]):
        changes.append("Boosted group-friendly venues")
    if any(w in r for w in ["quick", "fast", "short"]):
        changes.append("Prioritized shorter activities")
    if any(w in r for w in ["long", "hours", "spend time"]):
        changes.append("Prioritized longer activities")

    if not changes:
        changes.append("General re-ranking based on refinement")

    return {"what_user_meant": refinement, "changes_made": changes}


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
