"""
LocalLens - Category catalog.

Defines the broad taxonomy of local places LocalLens can search for.
Each category maps to one or more OSM tag selections and describes a
pricing model so that prices (whenever real data exists) are presented
in a category-appropriate way (e.g. hotels = per night, barbers = per service).

No fabricated amounts exist here. A category may carry a *typical* relative
tier (free / budget / moderate / expensive) which is only ever used as a
transparent, category-level affordability signal - never displayed as a
real price for a specific place.
"""

from __future__ import annotations

from typing import Optional

# ── OSM tag keys LocalLens knows how to query ─────────────────────────────────
SEARCHABLE_KEYS = (
    "amenity", "leisure", "tourism", "shop", "craft", "natural",
    "office", "historic", "sport",
)

# ── Pricing models ─────────────────────────────────────────────────────────────
# Used to describe what a price means for a place of this category.
PRICE_MODEL_LABELS = {
    "per_meal": "per meal",
    "per_drink": "per drink",
    "per_night": "per night",
    "entry_fee": "entry fee",
    "per_service": "per service",
    "membership": "membership",
    "per_item": "per item",
    "per_visit": "per visit",
    "variable": "varies",
}


# ── The catalog ────────────────────────────────────────────────────────────────
# Each entry:
#   label      - friendly plural label shown in the UI
#   tags       - list of (tag_key, tag_value) selections used for Overpass
#   price_model- how prices are interpreted for this category
#   typical_tier - category-level affordability reference (never a price)
NIGHTS = [
    ("tourism", "hotel"), ("tourism", "motel"), ("tourism", "guest_house"),
    ("tourism", "bed_and_breakfast"), ("tourism", "chalet"),
]

CATALOG = {
    # ── Food & drink ──
    "restaurant": {
        "label": "Restaurants", "singular": "Restaurant",
        "tags": [("amenity", "restaurant")],
        "price_model": "per_meal", "typical_tier": "moderate",
    },
    "cafe": {
        "label": "Cafés", "singular": "Café",
        "tags": [("amenity", "cafe")],
        "price_model": "per_meal", "typical_tier": "budget",
    },
    "fast_food": {
        "label": "Fast food", "singular": "Fast food",
        "tags": [("amenity", "fast_food")],
        "price_model": "per_meal", "typical_tier": "budget",
    },
    "food_court": {
        "label": "Food courts", "singular": "Food court",
        "tags": [("amenity", "food_court")],
        "price_model": "per_meal", "typical_tier": "budget",
    },
    "ice_cream": {
        "label": "Ice cream", "singular": "Ice cream shop",
        "tags": [("amenity", "ice_cream")],
        "price_model": "per_item", "typical_tier": "budget",
    },
    "bakery": {
        "label": "Bakeries", "singular": "Bakery",
        "tags": [("shop", "bakery")],
        "price_model": "per_item", "typical_tier": "budget",
    },
    "bar": {
        "label": "Bars", "singular": "Bar",
        "tags": [("amenity", "bar")],
        "price_model": "per_drink", "typical_tier": "moderate",
    },
    "pub": {
        "label": "Pubs", "singular": "Pub",
        "tags": [("amenity", "pub")],
        "price_model": "per_drink", "typical_tier": "moderate",
    },
    "biergarten": {
        "label": "Beer gardens", "singular": "Beer garden",
        "tags": [("amenity", "biergarten")],
        "price_model": "per_drink", "typical_tier": "budget",
    },
    "nightclub": {
        "label": "Nightclubs", "singular": "Nightclub",
        "tags": [("amenity", "nightclub")],
        "price_model": "entry_fee", "typical_tier": "moderate",
    },
    "liquor_store": {
        "label": "Liquor stores", "singular": "Liquor store",
        "tags": [("shop", "alcohol")],
        "price_model": "per_item", "typical_tier": "moderate",
    },
    "supermarket": {
        "label": "Supermarkets", "singular": "Supermarket",
        "tags": [("shop", "supermarket")],
        "price_model": "per_item", "typical_tier": "budget",
    },
    "butcher": {
        "label": "Butchers", "singular": "Butcher",
        "tags": [("shop", "butcher")],
        "price_model": "per_item", "typical_tier": "budget",
    },
    "greengrocer": {
        "label": "Greengrocers", "singular": "Greengrocer",
        "tags": [("shop", "greengrocer")],
        "price_model": "per_item", "typical_tier": "budget",
    },
    "fishmonger": {
        "label": "Fish shops", "singular": "Fish shop",
        "tags": [("shop", "fishmonger")],
        "price_model": "per_item", "typical_tier": "budget",
    },

    # ── Lodging ──
    "hotel": {
        "label": "Hotels & guest houses", "singular": "Hotel",
        "tags": NIGHTS,
        "price_model": "per_night", "typical_tier": "moderate",
    },
    "hostel": {
        "label": "Hostels", "singular": "Hostel",
        "tags": [("tourism", "hostel")],
        "price_model": "per_night", "typical_tier": "budget",
    },
    "camp_site": {
        "label": "Camp sites", "singular": "Camp site",
        "tags": [("tourism", "camp_site")],
        "price_model": "per_night", "typical_tier": "budget",
    },

    # ── Nightlife ──
    "nightlife": {
        "label": "Nightlife", "singular": "Nightlife spot",
        "tags": [("amenity", "nightclub"), ("amenity", "bar"),
                 ("amenity", "pub"), ("leisure", "dance_floor"),
                 ("amenity", "casino")],
        "price_model": "entry_fee", "typical_tier": "moderate",
    },
    "casino": {
        "label": "Casinos", "singular": "Casino",
        "tags": [("amenity", "casino")],
        "price_model": "entry_fee", "typical_tier": "expensive",
    },

    # ── Shopping ──
    "shopping_mall": {
        "label": "Shopping malls", "singular": "Shopping mall",
        "tags": [("shop", "mall"), ("shop", "department_store"),
                 ("shop", "shopping_centre")],
        "price_model": "variable", "typical_tier": "moderate",
    },
    "clothing_store": {
        "label": "Clothing stores", "singular": "Clothing store",
        "tags": [("shop", "clothes"), ("shop", "fashion")],
        "price_model": "per_item", "typical_tier": "moderate",
    },
    "electronics_store": {
        "label": "Electronics stores", "singular": "Electronics store",
        "tags": [("shop", "electronics"), ("shop", "computer")],
        "price_model": "per_item", "typical_tier": "moderate",
    },
    "phone_shop": {
        "label": "Phone shops", "singular": "Phone shop",
        "tags": [("shop", "mobile_phone"), ("shop", "phones")],
        "price_model": "per_item", "typical_tier": "budget",
    },
    "phone_repair": {
        "label": "Phone & electronics repair", "singular": "Repair shop",
        "tags": [("craft", "electronics_repair"), ("shop", "mobile_phone"),
                 ("shop", "computer")],
        "price_model": "per_service", "typical_tier": "budget",
    },
    "furniture_store": {
        "label": "Furniture stores", "singular": "Furniture store",
        "tags": [("shop", "furniture")],
        "price_model": "per_item", "typical_tier": "moderate",
    },
    "book_store": {
        "label": "Bookstores", "singular": "Bookstore",
        "tags": [("shop", "books")],
        "price_model": "per_item", "typical_tier": "budget",
    },
    "market": {
        "label": "Markets", "singular": "Market",
        "tags": [("amenity", "marketplace"), ("shop", "marketplace"), ("shop", "mall")],
        "price_model": "variable", "typical_tier": "budget",
    },
    "hardware": {
        "label": "Hardware stores", "singular": "Hardware store",
        "tags": [("shop", "hardware"), ("shop", "doityourself")],
        "price_model": "per_item", "typical_tier": "budget",
    },
    "jewelry": {
        "label": "Jewellers", "singular": "Jeweller",
        "tags": [("shop", "jewelry"), ("shop", "jewellery")],
        "price_model": "per_item", "typical_tier": "expensive",
    },
    "optician": {
        "label": "Opticians", "singular": "Optician",
        "tags": [("shop", "optician")],
        "price_model": "per_item", "typical_tier": "moderate",
    },
    "chemist_drug": {
        "label": "Chemists & drug stores", "singular": "Chemist",
        "tags": [("shop", "chemist")],
        "price_model": "per_item", "typical_tier": "budget",
    },

    # ── Health ──
    "pharmacy": {
        "label": "Pharmacies", "singular": "Pharmacy",
        "tags": [("amenity", "pharmacy")],
        "price_model": "per_item", "typical_tier": "budget",
    },
    "hospital": {
        "label": "Hospitals", "singular": "Hospital",
        "tags": [("amenity", "hospital")],
        "price_model": "variable", "typical_tier": "moderate",
    },
    "clinic": {
        "label": "Clinics & doctors", "singular": "Clinic",
        "tags": [("amenity", "clinic"), ("amenity", "doctors"),
                 ("amenity", "doctor")],
        "price_model": "per_service", "typical_tier": "moderate",
    },
    "dentist": {
        "label": "Dentists", "singular": "Dentist",
        "tags": [("amenity", "dentist")],
        "price_model": "per_service", "typical_tier": "moderate",
    },
    "veterinary": {
        "label": "Veterinarians", "singular": "Veterinarian",
        "tags": [("amenity", "veterinary")],
        "price_model": "per_service", "typical_tier": "moderate",
    },

    # ── Education ──
    "school": {
        "label": "Schools", "singular": "School",
        "tags": [("amenity", "school"), ("amenity", "kindergarten"),
                 ("amenity", "preschool"), ("amenity", "childcare")],
        "price_model": "variable", "typical_tier": "budget",
    },
    "college": {
        "label": "Colleges", "singular": "College",
        "tags": [("amenity", "college")],
        "price_model": "variable", "typical_tier": "moderate",
    },
    "university": {
        "label": "Universities", "singular": "University",
        "tags": [("amenity", "university")],
        "price_model": "variable", "typical_tier": "moderate",
    },
    "library": {
        "label": "Libraries", "singular": "Library",
        "tags": [("amenity", "library")],
        "price_model": "free", "typical_tier": "free",
    },
    "music_school": {
        "label": "Music schools", "singular": "Music school",
        "tags": [("amenity", "music_school")],
        "price_model": "per_service", "typical_tier": "moderate",
    },
    "driving_school": {
        "label": "Driving schools", "singular": "Driving school",
        "tags": [("amenity", "driving_school")],
        "price_model": "per_service", "typical_tier": "moderate",
    },
    "language_school": {
        "label": "Language schools", "singular": "Language school",
        "tags": [("amenity", "language_school")],
        "price_model": "per_service", "typical_tier": "moderate",
    },

    # ── Culture & entertainment ──
    "museum": {
        "label": "Museums", "singular": "Museum",
        "tags": [("tourism", "museum"), ("amenity", "museum")],
        "price_model": "entry_fee", "typical_tier": "budget",
    },
    "gallery": {
        "label": "Art galleries", "singular": "Art gallery",
        "tags": [("tourism", "gallery"), ("amenity", "arts_centre")],
        "price_model": "entry_fee", "typical_tier": "budget",
    },
    "theatre": {
        "label": "Theatres", "singular": "Theatre",
        "tags": [("amenity", "theatre")],
        "price_model": "entry_fee", "typical_tier": "moderate",
    },
    "cinema": {
        "label": "Cinemas", "singular": "Cinema",
        "tags": [("amenity", "cinema")],
        "price_model": "entry_fee", "typical_tier": "moderate",
    },
    "arts_centre": {
        "label": "Arts centres", "singular": "Arts centre",
        "tags": [("amenity", "arts_centre")],
        "price_model": "entry_fee", "typical_tier": "moderate",
    },
    "attraction": {
        "label": "Tourist attractions", "singular": "Attraction",
        "tags": [("tourism", "attraction"), ("tourism", "theme_park")],
        "price_model": "entry_fee", "typical_tier": "budget",
    },
    "viewpoint": {
        "label": "Viewpoints", "singular": "Viewpoint",
        "tags": [("tourism", "viewpoint")],
        "price_model": "free", "typical_tier": "free",
    },
    "zoo": {
        "label": "Zoos", "singular": "Zoo",
        "tags": [("tourism", "zoo")],
        "price_model": "entry_fee", "typical_tier": "moderate",
    },
    "aquarium": {
        "label": "Aquariums", "singular": "Aquarium",
        "tags": [("tourism", "aquarium")],
        "price_model": "entry_fee", "typical_tier": "moderate",
    },
    "monument": {
        "label": "Historic landmarks", "singular": "Landmark",
        "tags": [("historic", "monument"), ("historic", "memorial"),
                 ("historic", "castle"), ("historic", "ruins"),
                 ("historic", "archaeological_site")],
        "price_model": "entry_fee", "typical_tier": "budget",
    },
    "event_venue": {
        "label": "Event venues", "singular": "Event venue",
        "tags": [("amenity", "event_venue"), ("amenity", "conference_centre"),
                 ("amenity", "community_centre")],
        "price_model": "entry_fee", "typical_tier": "moderate",
    },
    "church": {
        "label": "Churches", "singular": "Church",
        "tags": [("amenity", "place_of_worship")],
        "extra": {"religion": "christian"},
        "price_model": "free", "typical_tier": "free",
    },
    "mosque": {
        "label": "Mosques", "singular": "Mosque",
        "tags": [("amenity", "place_of_worship")],
        "extra": {"religion": "muslim"},
        "price_model": "free", "typical_tier": "free",
    },
    "place_of_worship": {
        "label": "Places of worship", "singular": "Place of worship",
        "tags": [("amenity", "place_of_worship")],
        "price_model": "free", "typical_tier": "free",
    },

    # ── Outdoors ──
    "park": {
        "label": "Parks", "singular": "Park",
        "tags": [("leisure", "park"), ("leisure", "garden"),
                 ("leisure", "recreation_ground")],
        "price_model": "free", "typical_tier": "free",
    },
    "nature_reserve": {
        "label": "Nature reserves", "singular": "Nature reserve",
        "tags": [("leisure", "nature_reserve")],
        "price_model": "entry_fee", "typical_tier": "budget",
    },
    "beach": {
        "label": "Beaches", "singular": "Beach",
        "tags": [("natural", "beach")],
        "price_model": "free", "typical_tier": "free",
    },
    "playground": {
        "label": "Playgrounds", "singular": "Playground",
        "tags": [("leisure", "playground")],
        "price_model": "free", "typical_tier": "free",
    },
    "garden": {
        "label": "Gardens", "singular": "Garden",
        "tags": [("leisure", "garden")],
        "price_model": "entry_fee", "typical_tier": "budget",
    },

    # ── Sports & fitness ──
    "gym": {
        "label": "Gyms & fitness centres", "singular": "Gym",
        "tags": [("leisure", "fitness_centre"), ("amenity", "gym")],
        "price_model": "membership", "typical_tier": "moderate",
    },
    "sports_centre": {
        "label": "Sports centres", "singular": "Sports centre",
        "tags": [("leisure", "sports_centre")],
        "price_model": "per_visit", "typical_tier": "moderate",
    },
    "stadium": {
        "label": "Stadiums", "singular": "Stadium",
        "tags": [("leisure", "stadium")],
        "price_model": "entry_fee", "typical_tier": "moderate",
    },
    "swimming_pool": {
        "label": "Swimming pools", "singular": "Swimming pool",
        "tags": [("leisure", "swimming_pool"), ("leisure", "water_park")],
        "price_model": "per_visit", "typical_tier": "budget",
    },
    "pitch": {
        "label": "Sports pitches", "singular": "Pitch",
        "tags": [("leisure", "pitch")],
        "price_model": "free", "typical_tier": "free",
    },

    # ── Personal care ──
    "barber": {
        "label": "Barbers", "singular": "Barber",
        "tags": [("amenity", "barber"), ("shop", "barber"),
                 ("shop", "hairdresser")],
        "price_model": "per_service", "typical_tier": "budget",
    },
    "salon": {
        "label": "Hair & beauty salons", "singular": "Salon",
        "tags": [("shop", "hairdresser"), ("shop", "beauty")],
        "price_model": "per_service", "typical_tier": "moderate",
    },
    "spa": {
        "label": "Spas", "singular": "Spa",
        "tags": [("amenity", "spa"), ("leisure", "spa")],
        "price_model": "per_service", "typical_tier": "expensive",
    },
    "massage": {
        "label": "Massage", "singular": "Massage studio",
        "tags": [("shop", "massage"), ("amenity", "massage")],
        "price_model": "per_service", "typical_tier": "moderate",
    },
    "nails": {
        "label": "Nail salons", "singular": "Nail salon",
        "tags": [("shop", "beauty")],
        "price_model": "per_service", "typical_tier": "moderate",
    },

    # ── Services ──
    "tailor": {
        "label": "Tailors", "singular": "Tailor",
        "tags": [("craft", "tailor")],
        "price_model": "per_service", "typical_tier": "budget",
    },
    "laundry": {
        "label": "Laundry & dry cleaning", "singular": "Laundry service",
        "tags": [("shop", "laundry"), ("shop", "dry_cleaning"),
                 ("amenity", "laundry")],
        "price_model": "per_service", "typical_tier": "budget",
    },
    "printing": {
        "label": "Print & copy shops", "singular": "Print shop",
        "tags": [("craft", "printer"), ("shop", "copy")],
        "price_model": "per_service", "typical_tier": "budget",
    },
    "internet_cafe": {
        "label": "Internet cafés", "singular": "Internet café",
        "tags": [("amenity", "internet_cafe")],
        "price_model": "per_visit", "typical_tier": "budget",
    },
    "coworking": {
        "label": "Coworking spaces", "singular": "Coworking space",
        "tags": [("office", "coworking")],
        "price_model": "membership", "typical_tier": "moderate",
    },
    "car_repair": {
        "label": "Car repair & mechanics", "singular": "Mechanic",
        "tags": [("shop", "car_repair"), ("craft", "mechanic"),
                 ("amenity", "motorcycle_repair")],
        "price_model": "per_service", "typical_tier": "moderate",
    },
    "car_wash": {
        "label": "Car washes", "singular": "Car wash",
        "tags": [("amenity", "car_wash")],
        "price_model": "per_service", "typical_tier": "budget",
    },
    "car_rental": {
        "label": "Car rentals", "singular": "Car rental",
        "tags": [("amenity", "car_rental")],
        "price_model": "variable", "typical_tier": "moderate",
    },
    "fuel": {
        "label": "Fuel stations", "singular": "Fuel station",
        "tags": [("amenity", "fuel")],
        "price_model": "variable", "typical_tier": "moderate",
    },
    "charging": {
        "label": "EV charging", "singular": "Charging station",
        "tags": [("amenity", "charging_station")],
        "price_model": "variable", "typical_tier": "budget",
    },
    "post_office": {
        "label": "Post offices", "singular": "Post office",
        "tags": [("amenity", "post_office")],
        "price_model": "per_service", "typical_tier": "budget",
    },
    "bank": {
        "label": "Banks", "singular": "Bank",
        "tags": [("amenity", "bank")],
        "price_model": "free", "typical_tier": "free",
    },
    "atm": {
        "label": "ATMs", "singular": "ATM",
        "tags": [("amenity", "atm")],
        "price_model": "free", "typical_tier": "free",
    },
    "money_transfer": {
        "label": "Money transfer & exchange", "singular": "Money transfer",
        "tags": [("amenity", "bureau_de_change"), ("amenity", "money_transfer")],
        "price_model": "per_service", "typical_tier": "budget",
    },
    "police": {
        "label": "Police stations", "singular": "Police station",
        "tags": [("amenity", "police")],
        "price_model": "free", "typical_tier": "free",
    },
    "fire_station": {
        "label": "Fire stations", "singular": "Fire station",
        "tags": [("amenity", "fire_station")],
        "price_model": "free", "typical_tier": "free",
    },
    "pet_grooming": {
        "label": "Pet services", "singular": "Pet groomer",
        "tags": [("shop", "pet_grooming"), ("amenity", "animal_grooming")],
        "price_model": "per_service", "typical_tier": "moderate",
    },
    "lawyer": {
        "label": "Lawyers", "singular": "Lawyer",
        "tags": [("office", "lawyer"), ("office", "notary")],
        "price_model": "per_service", "typical_tier": "expensive",
    },
    "accountant": {
        "label": "Accountants", "singular": "Accountant",
        "tags": [("office", "accountant"), ("office", "tax_advisor")],
        "price_model": "per_service", "typical_tier": "moderate",
    },
    "travel_agent": {
        "label": "Travel agencies", "singular": "Travel agency",
        "tags": [("shop", "travel_agency"), ("office", "travel_agency")],
        "price_model": "variable", "typical_tier": "moderate",
    },
    "real_estate": {
        "label": "Real estate agencies", "singular": "Real estate agency",
        "tags": [("office", "estate_agent")],
        "price_model": "variable", "typical_tier": "moderate",
    },
}

# Broad default set used when the query is vague or no interests are given.
BROAD_SEARCH = [
    "restaurant", "cafe", "fast_food", "bar", "pub", "bakery", "hotel",
    "hostel", "nightclub", "museum", "gallery", "theatre", "cinema",
    "library", "attraction", "park", "garden", "gym", "beach", "viewpoint",
    "supermarket", "market", "book_store", "pharmacy", "place_of_worship",
    "arts_centre", "playground", "swimming_pool", "sports_centre",
]

# Interest checkbox values → category keys.
INTEREST_CATEGORY_MAP = {
    "food": ["restaurant", "cafe", "fast_food", "food_court", "ice_cream",
             "bakery", "bar", "pub"],
    "drinks": ["bar", "pub", "nightclub", "biergarten", "liquor_store"],
    "culture": ["museum", "theatre", "cinema", "arts_centre", "library",
                "gallery", "attraction", "monument"],
    "art": ["arts_centre", "gallery", "museum"],
    "music": ["bar", "pub", "nightclub", "theatre", "arts_centre"],
    "history": ["museum", "attraction", "monument", "place_of_worship"],
    "outdoors": ["park", "playground", "sports_centre", "nature_reserve",
                 "beach", "viewpoint", "garden"],
    "nature": ["park", "nature_reserve", "beach", "viewpoint", "zoo",
               "aquarium"],
    "sports": ["sports_centre", "gym", "swimming_pool", "stadium", "pitch"],
    "shopping": ["supermarket", "market", "shopping_mall", "clothing_store",
                 "electronics_store", "phone_shop", "book_store", "bakery",
                 "hardware"],
    "party": ["nightclub", "bar", "pub", "casino"],
    "relax": ["cafe", "park", "bar", "library", "beach", "viewpoint", "spa",
              "massage"],
    "learning": ["library", "museum", "college", "university", "school",
                 "language_school"],
    "family": ["playground", "park", "zoo", "aquarium", "swimming_pool",
               "food_court", "attraction", "pitch"],
    "nightlife": ["nightclub", "bar", "pub", "casino"],
    "solo": ["cafe", "library", "museum", "park", "cinema", "theatre", "beach"],
    "group": ["restaurant", "sports_centre", "nightclub", "food_court",
              "event_venue"],
}

VALID_INTERESTS = set(INTEREST_CATEGORY_MAP.keys())


def get_category(key: str) -> Optional[dict]:
    """Return a catalog entry by key, or None if unknown."""
    return CATALOG.get(key)


def category_label(key: str, singular: bool = False) -> str:
    """Friendly label for a category key (falls back to raw key)."""
    entry = CATALOG.get(key)
    if not entry:
        return key.replace("_", " ").title()
    return entry.get("singular" if singular else "label", key)


def price_model_label(price_model: Optional[str]) -> str:
    return PRICE_MODEL_LABELS.get(price_model or "", "varies")


def tags_for_keys(keys: list[str]) -> list[dict]:
    """
    Convert category keys into Overpass tag specs for search_pois.
    Each spec: {"key": k, "values": [...], "extra": {...}|None}
    """
    merged: dict = {}
    for key in keys:
        entry = CATALOG.get(key)
        if not entry:
            continue
        for tag_key, tag_val in entry.get("tags", []):
            if tag_key not in merged:
                merged[tag_key] = {"values": set(), "extra": entry.get("extra")}
            merged[tag_key]["values"].add(tag_val)
    specs = []
    for tag_key, spec in merged.items():
        specs.append({
            "key": tag_key,
            "values": sorted(v for v in spec["values"] if v),
            "extra": spec["extra"],
        })
    return specs


def matched_categories_for_place(place: dict, keys: list[str]) -> list[str]:
    """
    Return which of the given category keys a place matches, based on its
    OSM tag values (and required extra constraints, e.g. religion).
    """
    tags = place.get("tags", {})
    tag_values = set()
    for tag_key in SEARCHABLE_KEYS:
        val = tags.get(tag_key)
        if val and isinstance(val, str):
            tag_values.add(val)

    matched = []
    for key in keys:
        entry = CATALOG.get(key)
        if not entry:
            continue
        extra_ok = True
        extra = entry.get("extra")
        if extra:
            for ek, ev in extra.items():
                if tags.get(ek) != ev:
                    extra_ok = False
                    break
        if not extra_ok:
            continue
        for tag_key, tag_val in entry.get("tags", []):
            if tag_val in tag_values:
                matched.append(key)
                break
    return matched


def matched_categories_for_place_family(place: dict, keys: list[str]) -> list[str]:
    """Like matched_categories_for_place but ignores 'extra' constraints."""
    tags = place.get("tags", {})
    tag_values = set()
    for tag_key in SEARCHABLE_KEYS:
        val = tags.get(tag_key)
        if val and isinstance(val, str):
            tag_values.add(val)

    matched = []
    for key in keys:
        entry = CATALOG.get(key)
        if not entry:
            continue
        for tag_key, tag_val in entry.get("tags", []):
            if tag_val in tag_values:
                matched.append(key)
                break
    return matched