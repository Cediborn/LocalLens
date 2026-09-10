"""
LocalLens - FastAPI backend for local discovery.

Proxies Nominatim (geocoding) and Overpass API (POI discovery)
with caching, rate limiting, and transparent scoring.

The frontend sends the raw user query; the backend resolves its intent
(categories, budget + currency, open-now, time & distance hints, sort).
Explicit form controls only override the query where they clearly should,
and the query drives the category search (the single biggest gap the
original version had - it searched a fixed soup of categories).

Honesty rules enforced here:
  - GHS is the default currency, never USD.
  - Prices/ratings shown are only real data (or visibly a category-level
    "typical" reference) - nothing is fabricated.
  - Images, if any, are real per-place images (images.py).

Serves the frontend from static/index.html at / and exposes JSON API at /api/*.
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
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import httpx

from scoring import score_place, extract_place_data, haversine_m, relevance_pass
from categories import (
    tags_for_keys,
    category_label,
    INTEREST_CATEGORY_MAP,
    VALID_INTERESTS,
    BROAD_SEARCH,
)
from intent import Intent, hour_from_hint
from images import enrich_images


app = FastAPI(
    title="LocalLens API",
    description="Real local discovery powered by OpenStreetMap. Find things to do near you.",
    version="2.0.0",
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

USER_AGENT = "LocalLens/2.0 (local-discovery; https://locallens.app)"
NOMINATIM_URL = "https://nominatim.openstreetmap.org"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_KUMI = "https://overpass.kumi.systems/api/interpreter"
OVERPASS_MIRRORS = [
    OVERPASS_URL,
    OVERPASS_KUMI,
    "https://overpass.osm.ch/api/interpreter",
]

DEFAULT_CURRENCY = "GHS"

_geocode_cache: dict = {}
_search_cache: dict = {}
_GEOCODE_CACHE_MAX_AGE = 86400   # 24h for geocode results
_SEARCH_CACHE_MAX_AGE = 1800     # 30 min for search results
_CACHE_MAX_SIZE = 300

_rate_limit: dict = {}
_RATE_LIMIT_WINDOW = 1.0
_RATE_LIMIT_MAX = 2

OVERPASS_TIMEOUT_S = 40
OVERPASS_TOTAL_TIMEOUT_S = 55

MAX_RESULTS = 30
SEARCH_LIMIT = 250
MIN_SCORE_THRESHOLD = 3.0

# Food / nightlife / shopping category values used by conversational refine.
FOOD_CATEGORIES = [
    "restaurant", "fast_food", "food_court", "cafe", "ice_cream", "bakery",
    "bar", "pub", "biergarten", "street_food", "food_truck",
]


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


async def _http_post(url: str, data: dict, headers: dict = None, timeout: float = 30.0,
                     attempts: int = 2):
    headers = headers or {}
    headers.setdefault("User-Agent", USER_AGENT)

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(attempts):
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
                if attempt == attempts - 1:
                    raise
                continue
            except Exception:
                if attempt == attempts - 1:
                    raise
                await asyncio.sleep(0.5)
    raise httpx.HTTPStatusError("Request failed", request=None, response=None)


# ── URL Safety ─────────────────────────────────────────────────────────────────

def sanitize_url(url: str) -> Optional[str]:
    """Sanitize a URL to prevent XSS. Only allows http/https protocols."""
    if not url:
        return None
    url = url.strip()
    lower = url.lower()
    for proto in ["javascript:", "data:", "vbscript:", "file:", "ftp:"]:
        if lower.startswith(proto):
            return None
    if not (lower.startswith("https:") or lower.startswith("http:")):
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

def _build_overpass_query(specs: list[dict], lat: float, lon: float,
                          radius_m: float) -> str:
    """Build an Overpass QL query from category tag specs."""
    parts = []
    for spec in specs:
        key = spec["key"]
        values = spec["values"]
        if not values:
            continue
        if len(values) == 1:
            tm = '["{}"="{}"]'.format(key, values[0])
        else:
            tm = '["{}"~"^({})$"]'.format(key, "|".join(values))
        extra = ""
        if spec.get("extra"):
            for ek, ev in spec["extra"].items():
                extra += '["{}"="{}"]'.format(ek, ev)
        parts.append(f'node{tm}{extra}(around:{int(radius_m)},{lat},{lon});')
        parts.append(f'way{tm}{extra}(around:{int(radius_m)},{lat},{lon});')
    return f"""[out:json][timeout:{OVERPASS_TIMEOUT_S}];
(
{"".join(parts)}
);
out center;
"""


async def search_overpass(overpass_query: str) -> dict:
    """
    Run an Overpass query across mirrors (one attempt each). Overpass public
    servers are occasionally slow, overloaded, or answer with a valid-but-empty
    document (stale coverage / runtime timeout), so an empty answer is NOT
    treated as success while later mirrors may still hold real places. Empty
    is only accepted when every mirror agrees it is empty - and even then a
    second sweep through the mirrors is made in case of a transient hiccup.
    """
    empty_result = None
    last_error = None
    for sweep in range(2):
        for mirror in OVERPASS_MIRRORS:
            try:
                data = await _http_post(mirror, {"data": overpass_query},
                                        timeout=OVERPASS_TOTAL_TIMEOUT_S, attempts=1)
                if data.get("elements"):
                    return data
                empty_result = data
            except Exception as e:
                last_error = e
    if empty_result is not None:
        return empty_result
    raise last_error if last_error else RuntimeError("No Overpass mirror available")


async def search_pois(lat: float, lon: float, radius_m: float,
                      category_keys: Optional[list[str]] = None,
                      limit: int = SEARCH_LIMIT) -> list[dict]:
    if not category_keys:
        category_keys = list(BROAD_SEARCH)

    specs = tags_for_keys(category_keys)
    if not specs:
        return []

    overpass_query = _build_overpass_query(specs, lat, lon, radius_m)

    cache_args = {"lat": round(lat, 4), "lon": round(lon, 4),
                  "radius": int(radius_m), "cats": sorted(category_keys)}
    cache_key = _cache_key("search", cache_args)
    cached = _cache_get(_search_cache, cache_key, _SEARCH_CACHE_MAX_AGE)
    if cached:
        return cached

    try:
        data = await search_overpass(overpass_query)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Overpass API unavailable ({type(e).__name__}): {str(e)[:120]}",
        )

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
    if result:
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
        "version": "2.0.0",
        "default_currency": DEFAULT_CURRENCY,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Categories ─────────────────────────────────────────────────────────────────

@app.post("/api/resolve-query")
async def resolve_query(body: dict):
    """Debug/intent endpoint: what did LocalLens understand from this query?"""
    query = (body.get("query") or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    intent = Intent(query)
    return {
        "query": query,
        "structured": intent.to_structured(),
        "intent": intent.to_overrides(),
        "categories": [category_label(c) for c in intent.categories[:6]],
        "start_time": hour_from_hint(intent.time_hint),
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

    query = (body.get("query") or "").strip()
    budget = float(body.get("budget", 0) or 0)
    body_currency = (body.get("budget_currency") or "").upper().strip()
    time_available = float(body.get("time_available_hours", 0) or 0)
    interests = body.get("interests", [])
    alone = body.get("alone", True)
    max_distance_km = float(body.get("max_distance_km", 5.0) or 5.0)
    start_time = (body.get("start_time") or "").strip()
    open_now = bool(body.get("open_now", False))
    sort = (body.get("sort") or "").strip().lower()

    # ── Intent from the natural-language query ──
    intent = Intent(query) if query else None
    query_labels = []
    if intent:
        query_labels = [category_label(c, singular=True) for c in intent.categories[:4]]
    specific = bool(intent) and intent.specific
    relevance_keywords = []
    if intent:
        relevance_keywords = list(
            dict.fromkeys(intent.keywords + intent.subcategory_keywords))
    resolved_intent = None
    if intent:
        resolved_intent = {
            "category": intent.structured.category,
            "categories": list(intent.categories),
            "subcategory": intent.structured.subcategory,
            "keywords": list(intent.keywords),
            "budget": dict(intent.structured.budget),
            "distance": dict(intent.structured.distance),
            "rating": dict(intent.structured.rating),
            "location": dict(intent.structured.location),
            "open_now": intent.structured.open_now,
            "time_hint": intent.structured.time_hint,
            "confidence": intent.confidence,
            "ambiguous": intent.ambiguous,
            "labels": query_labels,
        }

    # ── Validate + clamp ──
    max_distance_km = max(0.5, min(max_distance_km, 50.0))
    max_distance_m = max_distance_km * 1000

    valid_interests = []
    for interest in interests:
        if isinstance(interest, str) and interest.lower().strip() in VALID_INTERESTS:
            valid_interests.append(interest.lower().strip())
    interests = valid_interests

    # ── Currency: query hints win unless the user explicitly chose one ──
    currency = DEFAULT_CURRENCY
    if body_currency and body_currency != DEFAULT_CURRENCY:
        currency = body_currency
    if intent and intent.currency:
        currency = intent.currency
    if intent and intent.budget is not None and intent.budget > 0:
        budget = intent.budget
    budget_hint = intent.budget_hint if intent else None
    if budget_hint == "free":
        budget = 0

    # ── Check time: explicit start time > open-now > query time hint > now ──
    check_time = None
    time_aware = False
    if start_time:
        try:
            if re.match(r'^\d{1,2}:\d{2}$', start_time):
                now = datetime.now(timezone.utc)
                parts = start_time.split(":")
                check_time = now.replace(hour=int(parts[0]), minute=int(parts[1]),
                                         second=0, microsecond=0)
            else:
                check_time = datetime.fromisoformat(start_time)
            time_aware = True
        except (ValueError, TypeError):
            check_time = None
    if intent and intent.time_hint:
        hint_time = hour_from_hint(intent.time_hint)
        if hint_time:
            now = datetime.now(timezone.utc)
            hh, mm = hint_time.split(":")
            check_time = now.replace(hour=int(hh), minute=int(mm),
                                     second=0, microsecond=0)
            time_aware = True
    if open_now or (intent and intent.open_now):
        check_time = datetime.now(timezone.utc)
        time_aware = True

    # ── Sort: explicit control > query hint ──
    if sort not in ("distance", "price", "rating"):
        sort = intent.sort if intent and intent.sort else None

    # ── Radius tuning from distance hints ──
    if intent and intent.structured.distance.get("max_distance"):
        radius_m = max(500.0, min(intent.max_distance_km * 1000, 50000.0))
    elif intent and intent.distance_hint == "close":
        radius_m = min(max_distance_m, 3000)
    elif intent and intent.distance_hint == "far":
        radius_m = max(max_distance_m, 10000)
    else:
        radius_m = max_distance_m

    # ── Geocode ──
    geo_result = await geocode(location)
    user_lat, user_lon = geo_result["lat"], geo_result["lon"]

    # ── Category selection ──
    if intent:
        category_keys = list(intent.categories)
    else:
        category_keys = []
        for interest in interests:
            category_keys.extend(INTEREST_CATEGORY_MAP.get(interest, []))
        if not category_keys:
            category_keys = list(BROAD_SEARCH)
    category_keys = list(dict.fromkeys(category_keys))

    # ── Search ──
    try:
        places = await search_pois(user_lat, user_lon, radius_m,
                                   category_keys, limit=SEARCH_LIMIT)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"POI search failed: {str(e)}")

    if not places:
        return _empty_response(location, geo_result, budget, currency,
                               time_available, interests, alone, max_distance_km,
                               start_time, open_now, sort, query, time_aware,
                               radius_m, resolved_intent)

    # ── Score ──
    user_constraints = {"lat": user_lat, "lon": user_lon}
    scored = []
    for place in places:
        result = score_place(
            place=place,
            user_constraints=user_constraints,
            max_distance_m=max_distance_m,
            available_hours=time_available,
            budget_amount=budget,
            currency=currency,
            alone=alone,
            interests=interests,
            check_time=check_time,
            intent_categories=category_keys,
            budget_hint=budget_hint,
            time_aware=time_aware,
            relevance_keywords=relevance_keywords,
        )
        scored.append(result)

    # ── Hard relevance gate for specific intents ──
    # A category mismatch must not survive on rating/distance alone
    # (the audit's 4.6/10 pharmacy problem).
    if specific:
        scored = [r for r in scored if relevance_pass(
            r.breakdown["relevance_score"],
            r.breakdown["factors"].get("matched_categories", []),
            specific=True)]

    # ── Time-aware ordering (keeps open/unknown/closed as before) ──
    if time_aware:
        open_places, unknown_hours, closed_places = [], [], []
        for r in scored:
            if r.open_status and r.open_status.has_hours_data:
                if r.open_status.is_open:
                    open_places.append(r)
                else:
                    closed_places.append(r)
            else:
                unknown_hours.append(r)
        if len(open_places) + len(unknown_hours) >= 5:
            scored = open_places + unknown_hours
        else:
            scored = open_places + unknown_hours + closed_places

    # ── Sorting ──
    if sort == "distance":
        scored.sort(key=lambda r: (r.distance_km, -r.score))
    elif sort == "price":
        def _price_key(r):
            amt = r.cost_estimate.get("amount")
            if amt is None:
                return (1, 999999.0, -r.score)
            return (0, amt, -r.score)
        scored.sort(key=_price_key)
    elif sort == "rating":
        scored.sort(key=lambda r: (
            -1 if r.rating is not None else 0,
            -(r.rating or 0), -r.score))
    else:
        scored.sort(key=lambda r: r.score, reverse=True)
        if time_aware:
            def _time_tier(r):
                if r.open_status and r.open_status.has_hours_data:
                    return 2 if r.open_status.is_open else 0
                return 1
            scored.sort(key=lambda r: (_time_tier(r), r.score), reverse=True)

    # ── Real per-place images for the top candidates ──
    image_idx = {}
    try:
        top_places = [r.place for r in scored[:40]]
        image_idx = await enrich_images(top_places, max_places=20)
    except Exception:
        image_idx = {}

    # ── Filter unnamed / low score ──
    filtered = []
    unnamed = []
    for idx, r in enumerate(scored):
        if r.place.get("name", "").startswith("Unnamed "):
            unnamed.append(r)
            continue
        if r.score >= MIN_SCORE_THRESHOLD:
            if idx in image_idx:
                r.place["_image"] = image_idx[idx]
            filtered.append(r)
    if not filtered:
        filtered = [r for r in unnamed[:10]]
        for r in filtered:
            r.score = min(r.score, 6.0)

    results = [_format_result(r) for r in filtered[:MAX_RESULTS]]

    categories_found = sorted({p["category"] for p in places})

    return {
        "query": _query_echo(location, geo_result, budget, currency,
                             time_available, interests, alone, max_distance_km,
                             start_time, open_now, sort, query, time_aware,
                             resolved_intent),
        "results": results,
        "data_sources": ["nominatim_osm", "overpass_osm"],
        "coverage": {
            "places_found": len(places),
            "categories_found": categories_found,
            "search_radius_m": radius_m,
            "result_limit": MAX_RESULTS,
        },
        "scoring_model": {
            "factors": {
                "relevance": "35% — matches what you searched for",
                "budget": "20% — fits your budget (neutral when no price listed)",
                "distance": "20% — closer is better",
                "availability": "15% — open now + fits available time",
                "quality": "10% — real rating + data completeness",
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

    if new_constraints:
        return _refine_rescore(prev_results, refinement, new_constraints)

    refinement_lower = refinement.lower()

    top_match = re.search(r'\btop\s+(\d+)\b', refinement_lower)
    if top_match:
        n = min(int(top_match.group(1)), MAX_RESULTS)
        sorted_results = sorted(prev_results, key=lambda r: r.get("score", 0), reverse=True)
        return {
            "refinement_applied": refinement,
            "results": sorted_results[:n],
            "note": f"Showing top {n} results by score.",
        }

    exclude_food = any(w in refinement_lower for w in [
        "no food", "not food", "nothing involving food", "no restaurants",
        "no eating", "avoid food", "without food", "exclude food",
        "remove food", "no cafe", "no bar", "no pub", "no fast food",
        "no food places",
    ])
    exclude_nightlife = any(w in refinement_lower for w in [
        "no nightlife", "no bars", "no clubs", "no drinking",
        "no alcohol", "without nightlife",
    ])
    exclude_shopping = any(w in refinement_lower for w in [
        "no shopping", "no shops", "no stores", "without shopping",
    ])
    want_cheaper = any(w in refinement_lower for w in [
        "cheaper", "budget", "save money", "less expensive", "low cost",
        "affordable", "free", "inexpensive", "more affordable",
        "lower cost", "not expensive",
    ])
    want_expensive = any(w in refinement_lower for w in [
        "expensive", "fancy", "upscale", "splurge", "premium", "luxury",
        "good quality", "high end", "fine dining",
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
        "closer", "near", "nearby", "close by", "walking distance",
        "walking", "short walk",
    ])
    want_farther = any(w in refinement_lower for w in [
        "farther", "further", "wider", "more distance", "travel", "drive",
        "longer trip",
    ])
    want_quick = any(w in refinement_lower for w in [
        "quick", "fast", "short time", "brief", "under an hour",
        "quick visit", "short",
    ])

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

    adjusted = []
    for pr in prev_results:
        score = pr.get("score", 5.0)
        combined_cat = " ".join([
            (pr.get("category") or "").lower(),
            (pr.get("osm_tags", {}).get("amenity", "") or "").lower(),
            (pr.get("osm_tags", {}).get("leisure", "") or "").lower(),
            (pr.get("osm_tags", {}).get("shop", "") or "").lower(),
            (pr.get("osm_tags", {}).get("tourism", "") or "").lower(),
        ])
        adjustments = []

        if exclude_food and any(fc in combined_cat for fc in FOOD_CATEGORIES):
            continue
        if exclude_nightlife and any(nc in combined_cat for nc in
                                     ["nightclub", "bar", "pub", "dance_floor", "stripclub"]):
            continue
        if exclude_shopping and any(sc in combined_cat for sc in
                                    ["shop", "supermarket", "marketplace"]):
            continue

        tier = (pr.get("cost_estimate") or {}).get("tier", "unknown")
        amount = (pr.get("cost_estimate") or {}).get("amount")
        if want_cheaper:
            if amount is not None and amount == 0:
                score += 2.0
                adjustments.append("boosted — free")
            elif tier == "free" or (amount is not None and amount == 0):
                score += 2.0
                adjustments.append("boosted — free")
            elif tier == "budget":
                score += 1.5
                adjustments.append("boosted — budget-friendly")
            elif tier == "moderate":
                score += 0.5
            elif tier == "expensive":
                score -= 1.5
        if want_expensive:
            if tier == "expensive":
                score += 2.0
            elif tier == "moderate":
                score += 0.5
            elif tier in ("free", "budget"):
                score -= 1.0
        if want_evening:
            open_status = pr.get("open_status", {})
            if open_status and open_status.get("is_open"):
                score += 1.0
                adjustments.append("open now")
            elif any(t in (pr.get("opening_hours") or "").lower()
                     for t in ["22:", "23:", "0:", "1:", "2:", "20:", "21:"]):
                score += 1.5
                adjustments.append("open late")
            if any(nc in combined_cat for nc in ["nightclub", "bar", "pub"]):
                score += 1.0
        if want_culture:
            if any(cc in combined_cat for cc in ["museum", "theatre", "cinema",
                                                 "arts_centre", "gallery",
                                                 "tourism", "attraction", "library"]):
                score += 2.0
                adjustments.append("boosted — cultural venue")
            else:
                score -= 1.0
        if want_outdoors:
            if any(oc in combined_cat for oc in ["park", "leisure", "garden",
                                                 "nature_reserve", "playground",
                                                 "beach", "viewpoint",
                                                 "recreation_ground"]):
                score += 2.0
                adjustments.append("boosted — outdoor venue")
            else:
                score -= 1.0
        if want_closer:
            dist = pr.get("distance_km", 0)
            if dist < 1.0:
                score += 1.5
                adjustments.append("boosted — very close")
            elif dist < 3.0:
                score += 0.5
        if want_farther:
            dist = pr.get("distance_km", 0)
            if dist > 1.0:
                score += 0.5 * min(dist / 5, 1.0)
        if want_quick:
            est = pr.get("estimated_time_hours", 999)
            if est <= 0.5:
                score += 2.0
                adjustments.append("boosted — quick visit")
            elif est <= 1.0:
                score += 1.0

        score = max(0.0, min(10.0, score))

        adjusted.append(_copy_result(pr, round(score, 1)))

    adjusted.sort(key=lambda r: r.get("score", 0), reverse=True)

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


def _refine_rescore(prev_results: list, refinement: str, new_constraints: dict):
    budget = float(new_constraints.get("budget", 0) or 0)
    currency = (new_constraints.get("budget_currency") or
                (prev_results[0] or {}).get("query", {}).get("budget_currency") or
                DEFAULT_CURRENCY)
    time_available = float(new_constraints.get("time_available_hours",
                                               (prev_results[0] or {}).get("query", {}).get("time_available_hours", 0) or 0))
    interests = new_constraints.get("interests",
                    (prev_results[0] or {}).get("query", {}).get("interests", []))
    alone = new_constraints.get("alone",
                   (prev_results[0] or {}).get("query", {}).get("alone", True))
    max_distance_km = float(new_constraints.get("max_distance_km",
                            (prev_results[0] or {}).get("query", {}).get("max_distance_km", 5.0)))
    open_now = bool(new_constraints.get("open_now", False))

    check_time = datetime.now(timezone.utc) if open_now else None
    time_aware = open_now

    places = []
    for pr in prev_results:
        place = {
            "name": pr.get("name", ""),
            "category": pr.get("category", "unknown"),
            "lat": pr.get("lat"),
            "lon": pr.get("lon"),
            "tags": dict(pr.get("osm_tags", {})),
            "id": pr.get("osm_id"),
            "osm_type": pr.get("osm_type"),
            "source": pr.get("source"),
        }
        oh = pr.get("opening_hours")
        if oh and oh != "Unknown":
            place["tags"]["opening_hours"] = oh
        if place.get("lat") is None or place.get("lon") is None:
            q = (prev_results[0] or {}).get("query", {})
            place["lat"] = q.get("lat")
            place["lon"] = q.get("lon")
        place["distance_m"] = pr.get("distance_km", 0) * 1000
        if pr.get("_image"):
            place["_image"] = pr["_image"]
        if pr.get("rating"):
            place["tags"]["rating"] = str(pr["rating"])
            if pr.get("rating_count"):
                place["tags"]["reviews"] = str(pr["rating_count"])
        places.append(place)

    user_constraints = {
        "lat": (prev_results[0] or {}).get("query", {}).get("lat", 0),
        "lon": (prev_results[0] or {}).get("query", {}).get("lon", 0),
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
            currency=currency,
            alone=alone,
            interests=interests,
            check_time=check_time,
            time_aware=time_aware,
        )
        scored.append(result)

    scored.sort(key=lambda r: r.score, reverse=True)

    return {
        "refinement_applied": refinement,
        "new_constraints": new_constraints,
        "results": [_format_result(r) for r in scored[:MAX_RESULTS]],
        "note": "Results re-scored based on updated constraints.",
    }


# ── Result formatting ──────────────────────────────────────────────────────────

def _copy_result(pr: dict, score: float) -> dict:
    """Copy a plain result dict, preserving display fields."""
    copy = dict(pr)
    copy["score"] = score
    return copy


def _format_result(r) -> dict:
    place = r.place
    tags = place.get("tags", {})
    website = sanitize_url(tags.get("website", "") or tags.get("contact:website", ""))
    phone = tags.get("phone") or tags.get("contact:phone")

    result = {
        "name": place["name"],
        "category": place["category"],
        "category_label": category_label(place["category"], singular=True),
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
        "rating": r.rating,
        "rating_count": r.rating_count,
        "osm_tags": {k: v for k, v in tags.items()
                     if k not in ("name", "addr:full", "addr:street", "address",
                                   "phone", "website", "contact:phone",
                                   "contact:website", "opening_hours")},
        "why_it_matches": r.why,
        "score": r.score,
        "breakdown": r.breakdown,
        "confidence": r.confidence,
        "confidence_details": r.confidence_details,
        "source": place.get("source", "OpenStreetMap via Overpass API"),
        "osm_id": place.get("id"),
        "osm_type": place.get("osm_type"),
    }
    image = place.get("_image")
    if image:
        result["image"] = image
    return result


def _query_echo(location, geo_result, budget, currency, time_available,
                interests, alone, max_distance_km, start_time, open_now,
                sort, query, time_aware, resolved_intent=None) -> dict:
    return {
        "location": location,
        "geocoded_to": geo_result.get("display_name"),
        "lat": geo_result.get("lat"),
        "lon": geo_result.get("lon"),
        "query": query,
        "resolved_intent": resolved_intent,
        "budget": budget,
        "budget_currency": currency,
        "time_available_hours": time_available,
        "interests": interests,
        "alone": alone,
        "max_distance_km": max_distance_km,
        "start_time": start_time,
        "open_now": open_now,
        "sort": sort,
        "time_aware": time_aware,
    }


def _empty_response(location, geo_result, budget, currency, time_available,
                    interests, alone, max_distance_km, start_time, open_now,
                    sort, query, time_aware, radius_m, resolved_intent=None):
    return {
        "query": _query_echo(location, geo_result, budget, currency,
                             time_available, interests, alone, max_distance_km,
                             start_time, open_now, sort, query, time_aware,
                             resolved_intent),
        "results": [],
        "data_sources": ["nominatim_osm"],
        "coverage": {
            "places_found": 0,
            "categories_found": [],
            "search_radius_m": radius_m,
        },
        "coverage_note": "No places found in this area. OpenStreetMap coverage may be sparse here.",
        "suggestions": [
            "Try a nearby city or larger town.",
            "Broaden your search to include more categories.",
            "Increase the search radius.",
            "Try a different search term.",
        ],
    }


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