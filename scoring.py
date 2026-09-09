"""
LocalLens - Transparent local recommendation scoring.

Each result gets a score 0-10 with a full breakdown so users understand
WHY something was ranked where it was.
"""

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone
import math

from opening_hours import get_open_status, OpenStatus


# ── Configuration ─────────────────────────────────────────────────────────────

FACTOR_WEIGHT = {
    "distance": 0.20,
    "budget": 0.25,
    "time": 0.20,
    "interests": 0.25,
    "companion": 0.10,
}

CATEGORY_COST_TIERS = {
    "restaurant": ("moderate", (15, 60)),
    "cafe": ("budget", (5, 20)),
    "fast_food": ("budget", (3, 15)),
    "bar": ("moderate", (10, 40)),
    "pub": ("moderate", (10, 35)),
    "nightclub": ("expensive", (25, 80)),
    "park": ("free", (0, 5)),
    "leisure": ("free", (0, 10)),
    "playground": ("free", (0, 0)),
    "sports_centre": ("moderate", (10, 30)),
    "cinema": ("moderate", (8, 20)),
    "theatre": ("expensive", (20, 60)),
    "museum": ("budget", (5, 20)),
    "library": ("free", (0, 0)),
    "arts_centre": ("moderate", (10, 30)),
    "shop": ("varies", (5, 100)),
    "supermarket": ("varies", (5, 50)),
    "marketplace": ("varies", (5, 50)),
    "hotel": ("expensive", (80, 300)),
    "hostel": ("budget", (20, 60)),
    "gym": ("moderate", (15, 40)),
    "swimming_pool": ("moderate", (5, 20)),
    "college": ("free", (0, 5)),
    "school": ("free", (0, 0)),
    "university": ("free", (0, 5)),
    "pharmacy": ("budget", (3, 20)),
    "hospital": ("free", (0, 0)),
    "doctor": ("varies", (5, 30)),
    "dentist": ("varies", (10, 50)),
    "viewpoint": ("free", (0, 0)),
    "nature_reserve": ("free", (0, 0)),
    "beach": ("free", (0, 0)),
    "gallery": ("budget", (5, 15)),
    "studio": ("moderate", (10, 40)),
    "workshop": ("moderate", (10, 30)),
    "tourism": ("varies", (5, 50)),
    "attraction": ("varies", (5, 50)),
    "zoo": ("moderate", (10, 40)),
    "amusement_ride": ("moderate", (10, 30)),
    "casino": ("expensive", (20, 100)),
    "food_court": ("budget", (5, 15)),
    "biergarten": ("budget", (5, 20)),
    "ice_cream": ("budget", (3, 10)),
    "stripclub": ("expensive", (20, 80)),
    "dance_floor": ("moderate", (10, 40)),
}

INTEREST_CATEGORY_MAP = {
    "food": ["restaurant", "cafe", "fast_food", "bar", "pub", "food_court", "biergarten"],
    "drinks": ["bar", "pub", "nightclub", "biergarten"],
    "culture": ["museum", "theatre", "cinema", "arts_centre", "library", "gallery", "tourism"],
    "art": ["arts_centre", "gallery", "museum"],
    "music": ["bar", "pub", "nightclub", "theatre", "arts_centre", "tourism"],
    "history": ["museum", "tourism", "church", "graveyard", "castle"],
    "outdoors": ["park", "leisure", "playground", "sports_centre", "pitch", "nature_reserve", "beach", "viewpoint"],
    "nature": ["park", "leisure", "nature_reserve", "beach", "viewpoint", "spring", "waterfall", "geological"],
    "sports": ["sports_centre", "pitch", "swimming_pool", "gym", "stadium", "tennis", "golf_course"],
    "shopping": ["shop", "supermarket", "marketplace", "shopping_centre"],
    "party": ["nightclub", "bar", "pub", "dance_floor", "stripclub"],
    "relax": ["cafe", "park", "bar", "library", "beach", "viewpoint"],
    "learning": ["library", "museum", "college", "university", "workshop", "tourism_information"],
    "family": ["playground", "park", "zoo", "amusement_ride", "swimming_pool", "food_court"],
    "nightlife": ["nightclub", "bar", "pub", "dance_floor", "stripclub"],
    "solo": ["cafe", "library", "museum", "park", "cinema", "theatre"],
    "group": ["restaurant", "sports_centre", "party", "food_court", "event_venue", "amusement_ride"],
}

INTEREST_KEYWORDS = {
    "food": ["eat", "dinner", "lunch", "brunch", "meal", "restaurant", "cuisine", "food"],
    "drinks": ["drink", "cocktail", "beer", "wine", "bar", "pub"],
    "culture": ["culture", "art", "exhibit", "show", "performance", "theatre", "cinema", "museum"],
    "art": ["art", "exhibit", "gallery", "paint", "sculpture", "creative", "studio"],
    "music": ["music", "concert", "live", "band", "dj", "gig", "jazz", "rock", "pop"],
    "history": ["history", "historic", "old", "heritage", "ancient", "archaeological", "monument"],
    "outdoors": ["outdoor", "walk", "hike", "nature", "fresh_air", "green", "trail", "garden"],
    "nature": ["nature", "wildlife", "animal", "bird", "forest", "tree", "lake", "river", "sea"],
    "sports": ["sport", "exercise", "workout", "game", "match", "play", "team", "fitness"],
    "shopping": ["shop", "buy", "market", "mall", "store", "browse", "souvenir"],
    "party": ["party", "dance", "celebrate", "fun", "night", "late", "club"],
    "relax": ["relax", "chill", "quiet", "peaceful", "calm", "slow", "leisurely", "coffee"],
    "learning": ["learn", "study", "class", "workshop", "lecture", "talk", "education", "knowledge"],
    "family": ["kids", "children", "family", "child-friendly", "play", "fun"],
    "nightlife": ["night", "evening", "late", "party", "drinks", "club", "dance"],
    "solo": ["solo", "alone", "by_myself", "quiet", "peaceful", "reflective"],
    "group": ["friends", "group", "family", "together", "social", "team"],
}

# Food-related categories for "no food" refinement filtering
FOOD_CATEGORIES = {"restaurant", "cafe", "fast_food", "bar", "pub", "nightclub",
                   "food_court", "biergarten", "ice_cream"}

# Valid interest whitelist
VALID_INTERESTS = set(INTEREST_CATEGORY_MAP.keys())


@dataclass
class ScoredResult:
    place: dict
    score: float = 0.0
    breakdown: dict = field(default_factory=dict)
    why: str = ""
    confidence: str = "unknown"
    confidence_details: dict = field(default_factory=dict)
    cost_estimate: dict = field(default_factory=dict)
    distance_km: float = 0.0
    est_time: float = 0.0
    open_status: Optional[OpenStatus] = None


def estimate_cost(place: dict, currency: str = "USD") -> dict:
    tags = place.get("tags", {})
    category = (
        tags.get("amenity", "")
        or tags.get("leisure", "")
        or tags.get("shop", "")
        or tags.get("sport", "")
        or tags.get("tourism", "")
        or "unknown"
    )

    # Check for explicit OSM price data
    if "price_range" in tags:
        pr = tags["price_range"].lower()
        if any(w in pr for w in ["low", "budget", "cheap"]):
            tier, amount = "budget", 10
        elif any(w in pr for w in ["mid", "moderate", "medium"]):
            tier, amount = "moderate", 30
        elif any(w in pr for w in ["high", "expensive"]):
            tier, amount = "expensive", 70
        else:
            tier, amount = "varies", 30
        return {"amount": amount, "currency": currency, "tier": tier,
                "source": "osm_price_range", "verified": True}

    for cost_tag in ["fee", "entrance_fee", "min_price", "price"]:
        if cost_tag in tags:
            try:
                val = float(tags[cost_tag])
                if 0 <= val <= 500:
                    tier = "budget" if val < 15 else "moderate" if val < 40 else "expensive"
                    return {"amount": val, "currency": currency, "tier": tier,
                            "source": f"osm_{cost_tag}", "verified": True}
            except (ValueError, TypeError):
                pass

    price_hint = tags.get("cheap") or tags.get("expensive") or tags.get("moderate")
    if price_hint:
        val = str(price_hint).lower()
        if val in ("yes", "true", "cheap", "low"):
            return {"amount": 10, "currency": currency, "tier": "budget",
                    "source": "osm_price_hint", "verified": True}
        elif val in ("expensive", "high"):
            return {"amount": 70, "currency": currency, "tier": "expensive",
                    "source": "osm_price_hint", "verified": True}

    # Fall back to category estimates
    cat_lower = category.lower()
    for key, (tier, (lo, hi)) in CATEGORY_COST_TIERS.items():
        if key in cat_lower:
            amount = (lo + hi) / 2
            return {"amount": round(amount), "currency": currency, "tier": tier,
                    "source": "estimated_typical", "verified": False}

    return {"amount": 20, "currency": currency, "tier": "varies",
            "source": "estimated_typical", "verified": False}


def compute_distance_score(distance_m: float, max_distance_m: float) -> float:
    if max_distance_m <= 0:
        return 0.0
    ratio = 1.0 - (distance_m / max_distance_m)
    return max(0.0, min(1.0, ratio))


def compute_budget_score(cost_amount: float, budget_amount: float) -> float:
    """
    Budget scoring:
    - budget_amount <= 0 means user didn't specify a budget → neutral score (0.5)
    - cost_amount <= 0 means free place → always good (1.0)
    - Otherwise, compare cost to budget
    """
    if budget_amount <= 0:
        return 0.5  # Neutral: user didn't specify budget
    if cost_amount <= 0:
        return 1.0
    ratio = cost_amount / budget_amount
    if ratio <= 0.5:
        return 1.0
    elif ratio <= 1.0:
        return 1.0 - (ratio - 0.5) * 1.0
    else:
        return max(0.0, 0.5 - (ratio - 1.0) * 2.0)


def compute_time_score(estimated_hours: float, available_hours: float) -> float:
    if available_hours <= 0:
        return 0.5  # Neutral when no time specified
    if estimated_hours <= 0:
        return 1.0
    ratio = estimated_hours / available_hours
    if ratio <= 0.3:
        return 1.0
    elif ratio <= 0.7:
        return 1.0 - (ratio - 0.3) * 1.0
    elif ratio <= 1.0:
        return 0.6 - (ratio - 0.7) * 2.0
    else:
        return max(0.0, 0.3 - (ratio - 1.0) * 1.0)


def compute_interest_score(place: dict, interests: list[str]) -> float:
    if not interests:
        return 0.5

    tags = place.get("tags", {})
    category = (
        (tags.get("amenity", "") or "") + " " +
        (tags.get("leisure", "") or "") + " " +
        (tags.get("shop", "") or "") + " " +
        (tags.get("tourism", "") or "")
    ).lower()
    name = (tags.get("name", "") or "").lower()
    cuisine = (tags.get("cuisine", "") or "").lower()
    all_text = f"{category} {name} {cuisine}"

    matched_interests = set()
    interest_score = 0.0

    for interest in interests:
        interest_lower = interest.lower().strip()
        matched_cats = INTEREST_CATEGORY_MAP.get(interest_lower, [])
        for mc in matched_cats:
            if mc in all_text:
                matched_interests.add(interest_lower)
                interest_score += 1.0
                break

        keywords = INTEREST_KEYWORDS.get(interest_lower, [])
        for kw in keywords:
            if kw in all_text:
                matched_interests.add(interest_lower)
                interest_score += 0.5
                break

    if not matched_interests:
        return 0.2

    max_possible = len(interests) * 1.5
    return min(1.0, interest_score / max_possible)


def compute_companion_score(place: dict, alone: bool) -> float:
    tags = place.get("tags", {})
    category = ((tags.get("amenity", "") or "") + " " + (tags.get("leisure", "") or "")).lower()

    solo_friendly = ["cafe", "library", "museum", "park", "cinema", "theatre",
                     "art", "gallery", "bookshop", "church", "viewpoint", "nature_reserve"]
    group_friendly = ["nightclub", "bar", "pub", "restaurant", "sports_centre",
                      "stadium", "amusement_ride", "theme_park", "casino", "stripclub"]

    for sf in solo_friendly:
        if sf in category:
            return 1.0 if alone else 0.6
    for gf in group_friendly:
        if gf in category:
            return 0.6 if alone else 1.0

    return 0.8


def _format_distance(distance_km: float) -> str:
    if distance_km < 0.5:
        return f"just {distance_km*1000:.0f}m away — walking distance"
    elif distance_km < 1.0:
        return f"{distance_km*1000:.0f}m away — very close"
    elif distance_km < 3.0:
        return f"{distance_km:.1f} km away — close by"
    else:
        return f"{distance_km:.1f} km away — a short trip"


def _format_budget(cost_est: dict, budget_amount: float) -> str:
    tier = cost_est.get("tier", "unknown")
    amount = cost_est.get("amount", 0)
    curr = cost_est.get("currency", "")
    is_estimated = not cost_est.get("verified", False)
    est_prefix = "~" if is_estimated else ""

    if tier == "free":
        return "free / nominal cost"
    elif budget_amount <= 0:
        # No budget specified — just describe the cost without referencing a budget
        return f"{est_prefix}{amount} {curr} (estimated)" if is_estimated else f"{amount} {curr}"
    elif tier == "budget":
        return f"{est_prefix}{amount} {curr} — well within your {budget_amount} {curr} budget"
    elif tier == "moderate":
        return f"{est_prefix}{amount} {curr} — fits your {budget_amount} {curr} budget"
    elif tier == "expensive":
        return f"{est_prefix}{amount} {curr} — may stretch your {budget_amount} {curr} budget"
    elif budget_amount > 0 and amount > budget_amount:
        return f"{est_prefix}{amount} {curr} — over your {budget_amount} {curr} budget"
    return f"{est_prefix}{amount} {curr}" if is_estimated else f"{amount} {curr}"


def _format_time(est_hours: float, available_hours: float) -> str:
    if est_hours <= 0.5:
        return "~30 min — quick activity"
    elif available_hours <= 0:
        return f"~{est_hours:.1f}h estimated duration"
    elif est_hours <= 1.0:
        return f"~{est_hours:.1f}h — fits comfortably in your {available_hours}h window"
    elif est_hours <= available_hours:
        return f"~{est_hours:.1f}h — uses most of your {available_hours}h window"
    else:
        return f"~{est_hours:.1f}h — more time than you have, but could be a partial visit"


def _format_interests(matched: list[str]) -> str:
    if matched:
        suffix = "s" if len(matched) > 1 else ""
        return f"matches {', '.join(matched)} interest{suffix}"
    return "no specific interest match, but may still be worth exploring"


def _format_companion(alone: bool, comp_pct: float) -> str:
    if alone:
        if comp_pct >= 0.8:
            return "solo-friendly"
        elif comp_pct >= 0.5:
            return "works for solo visitors"
        return "may be better with others"
    else:
        if comp_pct >= 0.8:
            return "great for groups"
        elif comp_pct >= 0.5:
            return "suitable for groups"
        return "may be better for solo visitors"


def _format_open_status(open_status: Optional[OpenStatus]) -> str:
    if not open_status or not open_status.has_hours_data:
        return "hours not listed"
    if open_status.is_open:
        return "open now"
    elif open_status.next_open_text:
        return open_status.next_open_text.lower()
    return "closed"


def build_why(score: float, breakdown: dict, distance_km: float,
              cost_est: dict, est_time: float, available_hours: float,
              matched_interests: list[str], alone: bool, budget_amount: float,
              open_status: Optional[OpenStatus] = None) -> str:
    parts = [f"{round(score * 10)}/10 match"]
    parts.append(_format_distance(distance_km))
    parts.append(_format_budget(cost_est, budget_amount))
    parts.append(_format_time(est_time, available_hours))
    parts.append(_format_interests(matched_interests))
    parts.append(_format_companion(alone, breakdown.get("companion_score", 0)))
    if open_status and open_status.has_hours_data:
        parts.append(_format_open_status(open_status))
    return f"{score:.1f}/10 — " + "; ".join(parts) + "."


def score_place(
    place: dict,
    user_constraints: dict,
    max_distance_m: float,
    available_hours: float,
    budget_amount: float,
    currency: str,
    alone: bool,
    interests: list[str],
    check_time: Optional[datetime] = None,
) -> ScoredResult:
    tags = place.get("tags", {})
    lat = place.get("lat", place.get("latitude"))
    lon = place.get("lon", place.get("longitude"))

    if lat is None or lon is None:
        return ScoredResult(
            place=place, score=0.0,
            breakdown={"error": "missing coordinates"},
            why="Cannot score: missing location data",
            confidence="unknown",
            confidence_details={"coordinates": "missing"},
            cost_estimate={"amount": 0, "currency": currency, "tier": "unknown", "source": "none"},
            distance_km=0.0, est_time=0.0,
        )

    user_lat = user_constraints.get("lat", 0)
    user_lon = user_constraints.get("lon", 0)
    distance_m = haversine_m(lat, lon, user_lat, user_lon)

    cost_est = estimate_cost(place, currency)
    cost_amount = cost_est.get("amount", 0)

    cat = ((tags.get("amenity", "") or "") + " " + (tags.get("leisure", "") or "") +
           " " + (tags.get("shop", "") or "") + " " + (tags.get("tourism", "") or "")).lower()
    est_time = estimate_duration_hours(cat)

    matched_interests = []
    interest_pct = compute_interest_score(place, interests)
    for interest in interests:
        interest_lower = interest.lower().strip()
        matched_cats = INTEREST_CATEGORY_MAP.get(interest_lower, [])
        for mc in matched_cats:
            if mc in cat:
                matched_interests.append(interest)
                break
        else:
            keywords = INTEREST_KEYWORDS.get(interest_lower, [])
            all_text = f"{cat} {tags.get('name', '').lower()} {tags.get('cuisine', '').lower()}"
            for kw in keywords:
                if kw in all_text:
                    matched_interests.append(interest)
                    break

    d_pct = compute_distance_score(distance_m, max_distance_m)
    b_pct = compute_budget_score(cost_amount, budget_amount)
    t_pct = compute_time_score(est_time, available_hours)
    i_pct = interest_pct
    c_pct = compute_companion_score(place, alone)

    # Compute opening status
    if check_time is None:
        check_time = datetime.now(timezone.utc)
    open_status = get_open_status(tags.get("opening_hours"), check_time)

    breakdown = {
        "distance_score": round(d_pct, 3),
        "budget_score": round(b_pct, 3),
        "time_score": round(t_pct, 3),
        "interest_score": round(i_pct, 3),
        "companion_score": round(c_pct, 3),
        "factors": {
            "distance_km": round(distance_m / 1000, 2),
            "estimated_cost": cost_amount,
            "estimated_duration_hours": round(est_time, 1),
            "matched_interests": matched_interests,
            "solo_friendly": c_pct >= 0.8 if alone else True,
        }
    }

    total = (
        d_pct * FACTOR_WEIGHT["distance"] +
        b_pct * FACTOR_WEIGHT["budget"] +
        t_pct * FACTOR_WEIGHT["time"] +
        i_pct * FACTOR_WEIGHT["interests"] +
        c_pct * FACTOR_WEIGHT["companion"]
    ) * 10.0

    # Determine confidence honestly
    confidence = "estimated"  # Default: most data is estimated
    confidence_details = {
        "name_source": "overpass_osm",
        "location_source": "overpass_osm",
        "cost_source": cost_est.get("source", "unknown"),
        "cost_verified": cost_est.get("verified", False),
        "hours_source": "overpass_osm" if "opening_hours" in tags else "unknown",
        "hours_available": "opening_hours" in tags,
    }

    # Only mark as verified if the name and location come from OSM
    # and the cost data is also from OSM (not estimated)
    if tags.get("name") and cost_est.get("verified", False):
        confidence = "verified"
    elif not tags.get("name"):
        confidence = "unknown"

    why = build_why(total, breakdown, distance_m / 1000, cost_est, est_time,
                    available_hours, matched_interests, alone, budget_amount,
                    open_status)

    return ScoredResult(
        place=place,
        score=round(total, 1),
        breakdown=breakdown,
        why=why,
        confidence=confidence,
        confidence_details=confidence_details,
        cost_estimate=cost_est,
        distance_km=round(distance_m / 1000, 2),
        est_time=round(est_time, 1),
        open_status=open_status,
    )


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def estimate_duration_hours(category: str) -> float:
    cat = category.lower()

    quick = ["fast_food", "bar", "pub", "toilet", "atm", "bank", "pharmacy",
             "post_office", "shop", "supermarket", "marketplace", "ice_cream",
             "food_court", "biergarten", "kiosk"]
    for q in quick:
        if q in cat:
            return 0.5

    medium = ["restaurant", "museum", "cinema", "library", "gallery",
              "theme_park", "zoo", "aquarium", "swimming_pool", "gym", "casino",
              "nightclub", "amusement_ride", "tourism", "attraction", "workshop", "arts_centre"]
    for m in medium:
        if m in cat:
            return 1.5

    long = ["theatre", "college", "university", "stadium", "sports_centre",
            "golf_course", "ski_resort", "water_park", "event_venue", "concert_hall",
            "opera", "ballet", "community_centre", "dance_floor"]
    for l_item in long:
        if l_item in cat:
            return 2.5

    outdoors = ["park", "leisure", "playground", "garden", "nature_reserve", "beach",
                "viewpoint", "peak", "spring", "waterfall", "geological", "forest",
                "wood", "grassland", "heath", "moor", "common", "recreation_ground"]
    for o in outdoors:
        if o in cat:
            return 1.5

    return 1.0


def extract_place_data(element: dict) -> dict:
    tags = element.get("tags", {})
    lat = element.get("lat")
    lon = element.get("lon")

    if lat is None and "center" in element:
        center = element["center"]
        lat = center.get("lat")
        lon = center.get("lon")
    if lat is None and "bounds" in element:
        bounds = element["bounds"]
        lat = (bounds.get("minlat", 0) + bounds.get("maxlat", 0)) / 2
        lon = (bounds.get("minlon", 0) + bounds.get("maxlon", 0)) / 2

    category = "unknown"
    for tag_key in ["amenity", "leisure", "shop", "tourism", "sport"]:
        if tag_key in tags:
            category = tags[tag_key]
            break

    name = tags.get("name") or tags.get("operator") or tags.get("brand") or tags.get("shop")
    if not name:
        name = f"Unnamed {category}"

    return {
        "id": str(element.get("id", "")),
        "name": name,
        "category": category,
        "lat": lat,
        "lon": lon,
        "tags": tags,
        "osm_type": element.get("type", "unknown"),
        "source": "OpenStreetMap via Overpass API",
    }
