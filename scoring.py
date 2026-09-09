"""
LocalLens - Transparent local recommendation scoring.

Builds a 0-10 score with a per-factor breakdown so users always understand
WHY something was ranked where it was.

Factors (transparent, shown in the UI):
  - Relevance  35%  does the place match what the user actually searched for
  - Budget fit 20%  known prices vs the user's budget (neutral when unknown)
  - Distance   20%  real distance from the user's location
  - Availability 15%  open now (when relevant) + fits available time
  - Quality & confidence 10%  real rating when available + data completeness

Critical honesty rules:
  - Prices are only shown when they come from real data; no fabricated
    amounts. A category may carry a *typical* tier used as a weak, labeled
    signal - never presented as a real price.
  - Ratings come from real sources only; no AI-generated customer ratings.
  - AI confidence affects ranking mildly and is reported separately.
"""

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone
import math
import re

from opening_hours import get_open_status, OpenStatus
from categories import (
    CATALOG,
    INTEREST_CATEGORY_MAP,
    category_label,
    matched_categories_for_place,
    matched_categories_for_place_family,
    price_model_label,
)

# ── Configuration ─────────────────────────────────────────────────────────────

FACTOR_WEIGHT = {
    "relevance": 0.35,
    "budget": 0.20,
    "distance": 0.20,
    "availability": 0.15,
    "quality": 0.10,
}

# Weak, category-level affordability reference. Never a real price - just
# lets budget-aware ranking prefer cheaper *kinds* of places when the
# specific place has no listed price.
TIER_RANK = {"free": 0.0, "budget": 1.0, "moderate": 2.0, "expensive": 3.0,
             "unknown": 2.0}


# ── Real price extraction ─────────────────────────────────────────────────────

NUMERIC_PRICE_TAGS = [
    "entrance_fee", "tourist_fee", "admission", "price", "fee",
    "cost", "min_price", "price:adult", "price:primary",
]

QUOTED_PRICE_TAGS = ["price_range", "price_level", "pricing"]


def _price_level_to_tier(value: str) -> Optional[str]:
    low = value.strip().lower()
    if low in ("", "unknown", "none", "yes"):
        return None
    # Symbol count: "$$$" style
    if set(low) <= {"$"}:
        return {1: "budget", 2: "moderate", 3: "expensive", 4: "expensive"}.get(
            len(low.strip()), "moderate")
    if set(low) <= {"₵", "¢"}:
        return {1: "budget", 2: "moderate", 3: "expensive"}.get(
            len(low.strip()), "moderate")
    if any(w in low for w in ("cheap", "low", "budget", "inexpensive")):
        return "budget"
    if any(w in low for w in ("expensive", "high", "posh", "premium", "luxury", "pricey")):
        return "expensive"
    if any(w in low for w in ("moderate", "mid", "medium", "average", "fair")):
        return "moderate"
    return None


def _category_for_place(place: dict) -> str:
    tags = place.get("tags", {})
    for tag_key in ["amenity", "leisure", "tourism", "shop", "craft", "office"]:
        v = tags.get(tag_key)
        if v:
            return v
    return "unknown"


def estimate_cost(place: dict, currency: str = "GHS") -> dict:
    """
    Resolve price information for a place. Returns a dict describing real
    data when present; otherwise an honest 'no price listed' record.

    Never returns a fabricated amount.
    """
    tags = place.get("tags", {})

    price_model = "variable"
    typical_tier = "unknown"
    # Find the first catalog entry whose OSM tag pair matches this place.
    place_tag_values = {k: tags.get(k) for k in
                        ["amenity", "leisure", "tourism", "shop", "craft",
                         "office", "natural", "historic"]}
    for key, entry in CATALOG.items():
        for tag_key, tag_val in entry.get("tags", []):
            if place_tag_values.get(tag_key) == tag_val:
                price_model = entry.get("price_model", "variable")
                typical_tier = entry.get("typical_tier", "unknown")
                break
        if price_model != "variable" or typical_tier != "unknown":
            break

    base = {
        "amount": None,
        "currency": currency,
        "tier": "unknown",
        "verified": False,
        "available": False,
        "source": "none",
        "price_model": price_model,
        "price_model_label": price_model_label(price_model),
        "typical_tier": typical_tier if typical_tier != "unknown" else None,
        "display_amount": None,
    }

    # 1) numeric price tags
    for tag in NUMERIC_PRICE_TAGS:
        raw = tags.get(tag)
        if raw is None or str(raw).strip() in ("", "yes", "no", "unknown"):
            continue
        # values like "5 GBP" or "GH₵10" or "10"
        m = re.match(r'^\s*([\d]+(?:\.\d{1,2})?)', str(raw))
        if not m:
            continue
        amount = float(m.group(1))
        if amount < 0 or amount > 100000:
            continue
        rec = dict(base)
        rec.update({
            "amount": amount,
            "verified": True,
            "available": True,
            "source": f"osm_{tag}",
            "display_amount": f"{amount:g} {currency}",
            "tier": "free" if amount == 0 else "quoted",
        })
        return rec

    # 2) quoted/graded price levels (no exact amount)
    for tag in QUOTED_PRICE_TAGS:
        raw = tags.get(tag)
        if raw is None:
            continue
        tier = _price_level_to_tier(str(raw))
        if tier:
            rec = dict(base)
            rec.update({
                "tier": tier,
                "verified": True,
                "available": True,
                "source": f"osm_{tag}",
            })
            return rec

    if any(tags.get(k) in ("free", "yes") for k in
           ("fee", "content_fee", "charge")):
        rec = dict(base)
        rec.update({"tier": "free", "verified": True, "available": True,
                    "source": "osm_fee", "display_amount": "Free"})
        return rec

    # 3) no real price data for this place
    rec = dict(base)
    rec["tier"] = typical_tier if typical_tier in TIER_RANK else "unknown"
    rec["source"] = "none" if typical_tier == "unknown" else "category_typical"
    rec["available"] = False
    return rec


# ── Factor computations ───────────────────────────────────────────────────────

def compute_distance_score(distance_m: float, max_distance_m: float) -> float:
    if max_distance_m <= 0:
        return 0.0
    ratio = 1.0 - (distance_m / max_distance_m)
    return max(0.0, min(1.0, ratio))


def compute_budget_score(cost_est: dict, budget_amount: float,
                         budget_hint: Optional[str] = None) -> float:
    """Budget fit. Strict when a real price exists; a weak, labeled category
    prior when the exact price is unknown."""
    amount = cost_est.get("amount")
    tier = cost_est.get("tier", "unknown")
    want_cheap = budget_hint in ("cheap", "free")

    if budget_amount <= 0 and not want_cheap:
        return 0.6  # no budget given -> neutral

    if amount is not None and amount == 0:
        return 1.0  # explicitly free

    if amount is not None and amount > 0 and budget_amount > 0:
        ratio = amount / budget_amount
        if ratio <= 0.5:
            return 1.0
        if ratio <= 0.85:
            return 0.95
        if ratio <= 1.0:
            return 0.85
        if ratio <= 1.3:
            return 0.5
        if ratio <= 1.75:
            return 0.25
        return 0.08

    # No exact amount: use the category-level tier as a weak signal.
    cheap_boost = {"free": 0.9, "budget": 0.8, "moderate": 0.5,
                   "expensive": 0.2}.get(tier, 0.5)
    neutral = {"free": 0.85, "budget": 0.7, "moderate": 0.5,
               "expensive": 0.25}.get(tier, 0.5)
    return cheap_boost if want_cheap else neutral


def compute_time_score(estimated_hours: float, available_hours: float) -> float:
    if available_hours <= 0:
        return 0.65  # neutral: no time window given
    if estimated_hours <= 0:
        return 1.0
    ratio = estimated_hours / available_hours
    if ratio <= 0.35:
        return 1.0
    if ratio <= 0.7:
        return 1.0 - (ratio - 0.35) * 1.0
    if ratio <= 1.0:
        return 0.6 - (ratio - 0.7) * 2.0
    return max(0.0, 0.3 - (ratio - 1.0) * 1.0)


def _open_score(open_status: Optional[OpenStatus], time_aware: bool) -> float:
    if not open_status or not open_status.has_hours_data:
        return 0.5 if time_aware else 0.6  # unknown hours: neutral
    if open_status.is_open:
        return 1.0
    if time_aware:
        return 0.15  # explicitly closed at the requested time
    return 0.5


def compute_availability_score(open_status: Optional[OpenStatus],
                               est_time: float, available_hours: float,
                               time_aware: bool) -> float:
    open_part = _open_score(open_status, time_aware)
    time_part = compute_time_score(est_time, available_hours)
    return 0.5 * open_part + 0.5 * time_part


def compute_relevance_score(place: dict, intent_categories: list[str],
                            interests: list[str]) -> tuple[float, list[str]]:
    """
    How well the place matches the user's stated intent. The query drives
    this; a place outside the intent gets a low relevance and sinks.
    """
    q_matched = matched_categories_for_place(place, intent_categories)
    i_matched = []
    if interests:
        interest_keys = []
        for interest in interests:
            interest_keys.extend(INTEREST_CATEGORY_MAP.get(interest, []))
        i_matched = matched_categories_for_place(place, interest_keys)

    if q_matched:
        labels = sorted({category_label(k, singular=True) for k in q_matched})
        return 1.0, labels
    if i_matched:
        labels = sorted({category_label(k, singular=True) for k in i_matched})
        return 0.85, labels

    combined = list(intent_categories) + (
        [k for i in interests for k in INTEREST_CATEGORY_MAP.get(i, [])])
    if combined:
        family = matched_categories_for_place_family(place, combined)
        if family:
            return 0.65, [category_label(f, singular=True) for f in family[:2]]
        # Weak text-level match: category label appears in name/cuisine/tags
        tags = place.get("tags", {})
        text = " ".join(str(tags.get(k, "")) for k in
                        ("name", "cuisine", "amenity", "leisure", "shop",
                         "tourism")).lower()
        for ck in combined:
            words = category_label(ck, singular=True).lower().split()
            for w in words:
                if w in text and len(w) > 3:
                    return 0.55, [category_label(ck, singular=True)]
        return 0.1, []
    return 0.7, []  # genuinely vague query -> broad neutral


def compute_quality_score(place: dict, cost_est: dict, rating: Optional[float],
                          rating_scale: float = 5.0, has_image: bool = False,
                          open_status: Optional[OpenStatus] = None) -> float:
    tags = place.get("tags", {})

    rating_norm = 0.5
    if rating is not None and rating_scale > 0:
        rating_norm = max(0.0, min(1.0, rating / rating_scale))

    fields = [
        bool(tags.get("addr:street") or tags.get("addr:city") or tags.get("addr:full")),
        bool(tags.get("phone") or tags.get("contact:phone")),
        bool(tags.get("website") or tags.get("contact:website")),
        bool("opening_hours" in tags),
        bool(cost_est.get("available")),
        has_image,
    ]
    data_fill = sum(1 for f in fields if f) / len(fields)

    return round(0.55 * rating_norm + 0.45 * data_fill, 3)


def compute_companion_score(place: dict, alone: bool) -> float:
    """Small social-fit signal (kept for the UI note)."""
    tags = place.get("tags", {})
    category = ((tags.get("amenity", "") or "") + " " +
                (tags.get("leisure", "") or "")).lower()
    solo = ["cafe", "library", "museum", "park", "cinema", "theatre", "book",
            "gallery", "church", "viewpoint", "beach"]
    group = ["nightclub", "bar", "pub", "restaurant", "sports_centre", "stadium",
             "casino", "attraction", "amusement"]
    for s in solo:
        if s in category:
            return 1.0 if alone else 0.55
    for g in group:
        if g in category:
            return 0.55 if alone else 1.0
    return 0.8


# ── Explanation builder ───────────────────────────────────────────────────────

def _fmt_distance(distance_km: float) -> str:
    if distance_km < 0.05:
        return f"{'just ' if distance_km > 0 else ''}{max(distance_km*1000,1):.0f} m away"
    if distance_km < 1.0:
        return f"{distance_km*1000:.0f} m away"
    if distance_km < 3.0:
        return f"{distance_km:.1f} km away"
    return f"{distance_km:.1f} km away"


def build_why(relevance_labels: list[str], relevance: float,
              distance_km: float, cost_est: dict, budget_amount: float,
              currency: str, budget_hint: Optional[str],
              open_status: Optional[OpenStatus], time_aware: bool,
              available_hours: float, rating: Optional[float],
              rating_count: Optional[int], alone: bool,
              est_hours: float) -> str:
    reasons = []
    currency = (cost_est.get("currency") or currency)

    if relevance_labels:
        reasons.append("matches your search for " + ", ".join(relevance_labels[:2]))
    elif relevance <= 0.2:
        reasons.append("loosely related to your search")

    reasons.append(_fmt_distance(distance_km))

    amount = cost_est.get("amount")
    tier = cost_est.get("tier", "unknown")
    if amount is not None and amount > 0 and budget_amount > 0:
        if amount <= budget_amount:
            reasons.append(f"fits your {budget_amount:g} {currency} budget (∼{amount:g} {currency})")
        else:
            reasons.append(f"over your {budget_amount:g} {currency} budget (∼{amount:g} {currency})")
    elif amount is not None and amount > 0:
        reasons.append(f"≈{amount:g} {currency}")
    elif amount is not None and amount == 0:
        reasons.append("no cost listed")
    elif budget_amount > 0:
        typical = cost_est.get("typical_tier")
        if typical:
            reasons.append(f"no listed price - typically {typical} for this category")
        else:
            reasons.append("no price listed")

    if time_aware and open_status:
        if open_status.has_hours_data and open_status.is_open:
            reasons.append("open now")
        elif open_status.has_hours_data and open_status.next_open_text:
            reasons.append(f"currently closed ({open_status.next_open_text.lower()})")
        elif open_status.has_hours_data:
            reasons.append("currently closed")
        else:
            reasons.append("hours not listed")
    elif open_status and open_status.has_hours_data and open_status.is_open:
        reasons.append("open now")

    if rating is not None:
        if rating_count:
            reasons.append(f"rated {rating}/5 ({rating_count} review(s))")
        else:
            reasons.append(f"rated {rating}")

    if available_hours > 0:
        reasons.append(f"fits about a {est_hours:.0f}h visit in your {available_hours:g}h")

    if alone and relevance_labels and any(l in ("Bar", "Pub", "Nightclub") for l in relevance_labels):
        reasons.append("ok for solo visits")
    return "; ".join(reasons[:5])


# ── Rating extraction (real data only) ────────────────────────────────────────

def extract_rating(place: dict) -> tuple[Optional[float], Optional[int]]:
    """
    Pull a real rating/review count from tags if a contributor provided them
    (e.g. 'rating' / 'reviews'). Returns (None, None) when absent - we never
    compute a fake community rating.
    """
    tags = place.get("tags", {})
    rating = tags.get("rating") or tags.get("stars")
    count = tags.get("reviews") or tags.get("review_count")
    if rating is None:
        return None, None
    try:
        rval = float(str(rating).replace(",", "."))
    except (ValueError, TypeError):
        return None, None
    if rval <= 0 or rval > 10:
        return None, None
    try:
        cval = int(float(count)) if count is not None else None
    except (ValueError, TypeError):
        cval = None
    return rval, cval


# ── Exposed data holders ──────────────────────────────────────────────────────

@dataclass
class ScoredResult:
    place: dict
    score: float = 0.0
    breakdown: dict = field(default_factory=dict)
    why: str = ""
    confidence: str = "estimated"
    confidence_details: dict = field(default_factory=dict)
    cost_estimate: dict = field(default_factory=dict)
    distance_km: float = 0.0
    est_time: float = 0.0
    open_status: Optional[OpenStatus] = None
    rating: Optional[float] = None
    rating_count: Optional[int] = None


# ── Main scoring entry point ──────────────────────────────────────────────────

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
    intent_categories: Optional[list[str]] = None,
    budget_hint: Optional[str] = None,
    time_aware: bool = False,
    has_image: bool = False,
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
            cost_estimate=estimate_cost(place, currency),
            distance_km=0.0, est_time=0.0,
        )

    user_lat = user_constraints.get("lat", 0)
    user_lon = user_constraints.get("lon", 0)
    distance_m = haversine_m(lat, lon, user_lat, user_lon)

    cost_est = estimate_cost(place, currency)
    intent_categories = list(intent_categories or [])

    open_status = get_open_status(
        tags.get("opening_hours"), check_time or datetime.now(timezone.utc))

    cat = ((tags.get("amenity", "") or "") + " " + (tags.get("leisure", "") or "") +
           " " + (tags.get("shop", "") or "") + " " + (tags.get("tourism", "") or "") +
           " " + (tags.get("craft", "") or "")).lower()
    est_time = estimate_duration_hours(cat)

    relevance, matched_labels = compute_relevance_score(place, intent_categories, interests)
    budget_pct = compute_budget_score(cost_est, budget_amount, budget_hint)
    distance_pct = compute_distance_score(distance_m, max_distance_m)
    availability_pct = compute_availability_score(
        open_status, est_time, available_hours, time_aware)
    rating, rating_count = extract_rating(place)
    quality_pct = compute_quality_score(
        place, cost_est, rating, has_image=has_image, open_status=open_status)

    breakdown = {
        "relevance_score": round(relevance, 3),
        "budget_score": round(budget_pct, 3),
        "distance_score": round(distance_pct, 3),
        "availability_score": round(availability_pct, 3),
        "quality_score": round(quality_pct, 3),
        "factors": {
            "distance_km": round(distance_m / 1000, 2),
            "matched_categories": matched_labels,
            "estimated_duration_hours": round(est_time, 1),
            "rating": rating,
            "rating_count": rating_count,
            "data_completeness": round(quality_pct, 3),
        },
        "weights": dict(FACTOR_WEIGHT),
    }

    total = (
        relevance * FACTOR_WEIGHT["relevance"] +
        budget_pct * FACTOR_WEIGHT["budget"] +
        distance_pct * FACTOR_WEIGHT["distance"] +
        availability_pct * FACTOR_WEIGHT["availability"] +
        quality_pct * FACTOR_WEIGHT["quality"]
    ) * 10.0

    open_status = get_open_status(tags.get("opening_hours"), check_time or datetime.now(timezone.utc))

    # Honest confidence tiers
    has_verified_detail = (cost_est.get("available") or
                           "opening_hours" in tags or
                           bool(tags.get("website") or tags.get("phone")))
    if not tags.get("name") or tags.get("name", "").lower().startswith("unnamed"):
        confidence = "unknown"
    elif has_verified_detail:
        confidence = "verified"
    else:
        confidence = "estimated"

    confidence_details = {
        "name_source": "osm",
        "location_source": "osm" if lat is not None else "unknown",
        "cost_source": cost_est.get("source", "none"),
        "cost_verified": bool(cost_est.get("available")),
        "hours_source": "osm" if "opening_hours" in tags else "unknown",
        "hours_available": "opening_hours" in tags,
        "address_present": bool(tags.get("addr:street") or tags.get("addr:full")),
        "phone_present": bool(tags.get("phone") or tags.get("contact:phone")),
        "website_present": bool(tags.get("website") or tags.get("contact:website")),
        "has_image": has_image,
        "rating_source": "osm_tags" if rating is not None else "unknown",
    }

    currency_display = cost_est.get("currency") or currency
    why = build_why(matched_labels, relevance, distance_m / 1000, cost_est,
                    budget_amount, currency_display, budget_hint, open_status,
                    time_aware, available_hours, rating, rating_count,
                    alone, est_time)

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
        rating=rating,
        rating_count=rating_count,
    )


# ── Distance ──────────────────────────────────────────────────────────────────

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Duration estimates (labeled as estimates) ─────────────────────────────────

def estimate_duration_hours(category: str) -> float:
    cat = category.lower()
    quick = ["fast_food", "bar", "pub", "toilet", "atm", "bank", "pharmacy",
             "post_office", "shop", "supermarket", "marketplace", "ice_cream",
             "food_court", "biergarten", "kiosk", "barber", "hairdresser",
             "laundry", "car_wash", "fuel"]
    for q in quick:
        if q in cat:
            return 0.5

    medium = ["restaurant", "museum", "cinema", "library", "gallery", "zoo",
              "aquarium", "swimming_pool", "gym", "casino", "nightclub",
              "amusement", "tourism", "attraction", "workshop", "arts_centre",
              "salon", "spa", "massage", "event_venue"]
    for m in medium:
        if m in cat:
            return 1.5

    long = ["theatre", "college", "university", "stadium", "sports_centre",
            "golf_course", "water_park", "concert", "opera", "community_centre",
            "dance_floor", "school", "hospital"]
    for l_item in long:
        if l_item in cat:
            return 2.5

    outdoors = ["park", "leisure", "playground", "garden", "nature_reserve",
                "beach", "viewpoint", "waterfall", "forest", "recreation_ground"]
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
    for tag_key in ["amenity", "leisure", "shop", "tourism", "craft", "office", "sport"]:
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