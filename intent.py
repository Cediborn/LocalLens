"""
LocalLens - Query intent resolution.

Turns a free-text local search into structured criteria:
  - which OSM categories to search
  - budget amount + currency (Ghanaian Cedi default)
  - time hints (open now / after 8pm / tonight)
  - distance hints
  - sort preference
  - explicit "open now" flag

This is intentionally keyword based (like a local search engine), not a
generative chat model. The query is the strongest driver of the results.
"""

from __future__ import annotations

import re
from typing import Optional

from categories import BROAD_SEARCH

# ── Currency detection ─────────────────────────────────────────────────────────
# Ghana is the default locale. Recognize common symbols/codes without trying
# to convert any rates between currencies.

CURRENCY_PATTERNS = [
    (r'gh[\u00b5\u20b5]?c?\b', "GHS"),     # GHC / GH¢ (legacy code)
    (r'gh\u20b5', "GHS"),
    (r'\u00a2', "GHS"),                    # ₵
    (r'\u20b5', "GHS"),                    # cedis
    (r'\bcedis?\b', "GHS"),
    (r'\bcedi\b', "GHS"),
    (r'\bghs\b', "GHS"),
    (r'\bghp\b', "GHS"),
    (r'\busd\b', "USD"),
    (r'\$', "USD"),
    (r'\beur\b', "EUR"),
    (r'\u20ac', "EUR"),
    (r'\bgbp\b', "GBP"),
    (r'\u00a3', "GBP"),
    (r'\bngn\b', "NGN"),
    (r'\u20a6', "NGN"),
    (r'\bnairas?\b', "NGN"),
    (r'\bkes\b', "KES"),
    (r'\bksh\b', "KES"),
    (r'\bzar\b', "ZAR"),
    (r'\brands?\b', "ZAR"),
    (r'\baud\b', "AUD"),
    (r'\bcad\b', "CAD"),
    (r'\binr\b', "INR"),
    (r'\u20b9', "INR"),
    (r'\bjpy\b', "JPY"),
    (r'\u00a5', "JPY"),
]

CURRENCY_SYMBOL = {
    "GHS": "GH\u20b5", "USD": "$", "EUR": "\u20ac", "GBP": "\u00a3",
    "NGN": "\u20a6", "KES": "KSh", "ZAR": "R", "AUD": "A$", "CAD": "C$",
    "INR": "\u20b9", "JPY": "\u00a5",
}

# Order matters: more specific multi-word phrases are tested first.
QUERY_PATTERNS: list[tuple[str, list[str]]] = [
    # ── Food & drink ──
    ("restaurant", ["restaurant", "fine dining", "place to eat", "places to eat"]),
    ("fast_food", ["fast food", "fastfood", "takeaway", "take away", "street food", "junk food"]),
    ("cafe", ["cafe", "caf\u00e9", "coffee shop", "coffeehouse", "coffee house", "coffee", "tea room", "breakfast spot"]),
    ("food_court", ["food court"]),
    ("ice_cream", ["ice cream", "frozen yogurt", "gelato"]),
    ("bakery", ["bakery", "bakeries", "bread", "cake shop"]),
    ("bar", ["cocktail bar", "sports bar", "wine bar", "bar near", "bars in"]),
    ("pub", ["pub", "tavern", "inn"]),
    ("nightclub", ["nightclub", "night club", "club"]),
    ("liquor_store", ["liquor", "off licence", "off-licence"]),
    ("supermarket", ["supermarket", "grocery store", "grocery", "grocery shop"]),
    ("butcher", ["butcher"]),
    ("greengrocer", ["greengrocer", "fruit and veg", "vegetable shop", "produce"]),
    ("fishmonger", ["fishmonger", "fish shop", "fish market"]),

    # ── Lodging ──
    ("hotel", ["hotel", "guest house", "bed and breakfast", "motel", "places to stay", "accommodation", "place to sleep"]),
    ("hostel", ["hostel", "youth hostel"]),
    ("camp_site", ["camping", "campsite", "camp site"]),

    # ── Nightlife ──
    ("nightlife", ["nightlife", "night life", "things to do tonight", "tonight", "after dark", "late night", "going out", "clubbing", "late"]),
    ("casino", ["casino", "gambling"]),

    # ── Shopping ──
    ("shopping_mall", ["shopping mall", "shopping centre", "shopping center", "mall"]),
    ("clothing_store", ["clothing", "clothes shop", "clothes store", "fashion", "dress shop", "boutique", "shoe shop", "shoes"]),
    ("electronics_store", ["electronics", "electronics store", "tv shop", "appliance"]),
    ("phone_shop", ["phone shop", "phone store", "mobile phones", "buy a phone", "phones"]),
    ("phone_repair", ["phone repair", "phone fix", "repair phone", "screen repair", "electronic repair", "computer repair", "laptop fix"]),
    ("furniture_store", ["furniture", "sofa shop", "mattress"]),
    ("book_store", ["bookstore", "book store", "bookshop", "book shop", "books"]),
    ("market", ["market", "bazaar", "farmers market"]),
    ("hardware", ["hardware store", "hardware", "diy store", "paint shop", "tools"]),
    ("jewelry", ["jewellery", "jewelry", "goldsmith"]),
    ("optician", ["optician", "eyeglass", "glasses", "spectacles"]),
    ("chemist_drug", ["chemist", "drug store", "drugstore"]),

    # ── Health ──
    ("pharmacy", ["pharmacy", "pharmacies", "chemists"]),
    ("hospital", ["hospital", "emergency"]),
    ("clinic", ["clinic", "doctor", "medical centre", "medical center", "gp", "health centre"]),
    ("dentist", ["dentist", "dental"]),
    ("veterinary", ["vet", "veterinarian", "animal clinic"]),

    # ── Education ──
    ("school", ["school", "schools", "kindergarten", "nursery school", "preschool"]),
    ("college", ["college", "polytechnic"]),
    ("university", ["university", "uni"]),
    ("library", ["library", "libraries"]),
    ("music_school", ["music school", "music lessons"]),
    ("driving_school", ["driving school", "driving lessons"]),
    ("language_school", ["language school", "english classes", "learn english"]),

    # ── Culture & entertainment ──
    ("museum", ["museum"]),
    ("gallery", ["art gallery", "gallery", "art exhibit"]),
    ("theatre", ["theatre", "theater", "stage play", "live show"]),
    ("cinema", ["cinema", "movie theater", "movies", "movie theatre", "watch a film", "film"]),
    ("arts_centre", ["arts centre", "art center", "cultural centre", "culture"]),
    ("attraction", ["tourist attraction", "attraction", "amusement park", "theme park", "water park", "fun park"]),
    ("viewpoint", ["viewpoint", "scenic view", "overlook", "panoramic"]),
    ("zoo", ["zoo"]),
    ("aquarium", ["aquarium"]),
    ("monument", ["monument", "landmark", "castle", "historic site", "historical", "ruins", "statue"]),
    ("event_venue", ["event venue", "conference", "wedding venue", "event space"]),
    ("church", ["church"]),
    ("mosque", ["mosque"]),
    ("place_of_worship", ["place of worship", "worship", "temple", "chapel", "cathedral", "synagogue", "temple"]),

    # ── Outdoors ──
    ("park", ["park", "garden"]),
    ("nature_reserve", ["nature reserve", "wildlife reserve", "reserve"]),
    ("beach", ["beach", "seaside", "beachfront"]),
    ("playground", ["playground", "play area", "kids play"]),
    ("garden", ["botanical garden", "community garden"]),

    # ── Sports & fitness ──
    ("gym", ["gym", "fitness centre", "fitness center", "workout", "fitness", "exercise"]),
    ("sports_centre", ["sports centre", "sports center", "sports complex", "leisure centre"]),
    ("stadium", ["stadium", "sports ground", "arena"]),
    ("swimming_pool", ["swimming pool", "swim", "pool"]),
    ("pitch", ["sport pitch", "sports field", "play football", "watch football", "soccer", "football", "basketball court", "tennis court"]),

    # ── Personal care ──
    ("barber", ["barber", "barbershop", "haircut", "get a haircut", "trim"]),
    ("salon", ["salon", "hairstylist", "hair salon", "hairdresser"]),
    ("spa", ["spa", "wellness"]),
    ("massage", ["massage", "massage therapist"]),
    ("nails", ["nail salon", "manicure", "pedicure", "nails"]),

    # ── Services ──
    ("tailor", ["tailor", "seamstress", "alterations"]),
    ("laundry", ["laundry", "dry cleaning", "laundromat", "wash clothes"]),
    ("printing", ["print shop", "printing", "photocopy", "copy shop", "printing shop", "print shop"]),
    ("internet_cafe", ["internet cafe", "internet caf\u00e9", "cyber cafe"]),
    ("coworking", ["coworking", "co-working", "co working", "work space", "workspace"]),
    ("car_repair", ["car repair", "mechanic", "auto repair", "car service", "garage"]),
    ("car_wash", ["car wash"]),
    ("car_rental", ["car rental", "rent a car", "hire car"]),
    ("fuel", ["fuel", "petrol station", "gas station", "filling station", "petrol"]),
    ("charging", ["charging station", "ev charging", "ev charger"]),
    ("post_office", ["post office", "postal"]),
    ("bank", ["bank"]),
    ("atm", ["atm", "cash machine", "cashpoint", "withdraw cash"]),
    ("money_transfer", ["money transfer", "money exchange", "currency exchange", "bureau de change", "send money"]),
    ("police", ["police", "police station"]),
    ("fire_station", ["fire station"]),
    ("pet_grooming", ["pet grooming", "dog grooming", "pet groomer", "pet shop", "dog groomer"]),
    ("lawyer", ["lawyer", "attorney", "legal advice", "solicitor", "notary"]),
    ("accountant", ["accountant", "tax advisor", "bookkeeping", "accounting"]),
    ("travel_agent", ["travel agent", "travel agency", "booking office"]),
    ("real_estate", ["real estate", "estate agent", "property agent", "estate agency"]),
]

# Short words that are too ambiguous to pin a category by themselves.
AMBIGUOUS_TOKENS = {"place", "places", "near", "me", "the", "a", "an", "of",
                    "in", "for", "with", "and", "or", "to", "my", "around"}

BUDGET_WORDS = {
    "cheap": ["cheap", "affordable", "budget", "inexpensive", "low cost",
              "value for money", "bargain", "economical", "reasonably priced"],
    "free": ["free", "no cost", "free of charge"],
    "expensive": ["expensive", "fancy", "upscale", "premium", "luxury",
                  "splurge", "high end", "high-end", "fine dining", "posh"],
}


def detect_currency(q: str) -> Optional[str]:
    for pattern, code in CURRENCY_PATTERNS:
        if re.search(pattern, q, re.IGNORECASE):
            return code
    return None


def extract_amount(q: str) -> Optional[float]:
    """Extract a numeric budget figure (e.g. 'under GH\u20b5300', '$25')."""
    patterns = [
        r'(?:under|below|up\s*to|less\s*than|around|about|max(?:imum)?)\s*(?:\w{2,4}|\W)*?\s*(\d+(?:\.\d+)?)',
        r'(\d+(?:\.\d+)?)\s*(?:cedis?|ghs|gh\u20b5|usd|\$|\u20ac|eur|gbp|\u00a3|ngn|\u20a6|\u20b9|\\|kes|ksh|zar|rands?)',
        r'(?:\$|\u20ac|\u00a3|\u20a6|\u20b9|\u00a5|gh\u20b5)\s*(\d+(?:\.\d+)?)',
        r'\b(\d{2,4})\s*(?:cedis|ghs|gh\u20b5|usd)?\b',
    ]
    for pat in patterns:
        m = re.search(pat, q)
        if m:
            val = float(m.group(1))
            if 0 < val <= 100000:
                return val
    return None


def detect_open_now(q: str) -> bool:
    return bool(re.search(r'\bopen now\b|\bnow\b|\bright now\b|currently open', q, re.IGNORECASE))


def detect_time_hint(q: str) -> Optional[str]:
    """Return an 'evening'/'morning'/'night' hint, or None."""
    if re.search(r'\bafter\s*(?:8|9|10)\b|\btonight\b|after dark|late night|open late', q, re.IGNORECASE):
        return "evening"
    if re.search(r'\bmorning\b|\bbreakfast\b|early\b', q, re.IGNORECASE):
        return "morning"
    if re.search(r'\bnight\b|after midnight', q, re.IGNORECASE):
        return "night"
    return None


def detect_distance_hint(q: str) -> Optional[str]:
    if re.search(r'\bwalking distance\b|\bnear me\b|\bnearby\b|\bclose by\b|\baround the corner\b|\bwalk\b', q, re.IGNORECASE):
        return "close"
    if re.search(r'\bwilling to travel\b|\bfarther\b|\bfurther\b|\bfar\b|\blong drive\b|\bworth the trip\b', q, re.IGNORECASE):
        return "far"
    return None


def detect_sort(q: str) -> Optional[str]:
    if re.search(r'\bclosest\b|\bnearest\b|\bnear me\b', q, re.IGNORECASE):
        return "distance"
    if re.search(r'\bcheapest\b|\bcheap\b|\blanked by price\b|\blowest priced\b', q, re.IGNORECASE):
        return "price"
    return None


def detect_vibe(q: str) -> Optional[str]:
    """
    Detect a 'vibe' modifier such as study-friendly, quiet, romantic.
    Returns a matching broad category key or None if not clearly present.
    """
    if re.search(r'\bquiet\b|\bstudy\b|\bstudying\b|\bstudied\b|concentrate|read\b', q, re.IGNORECASE):
        return "learning"
    if re.search(r'\bdate\b|\bromantic\b', q, re.IGNORECASE):
        return "restaurant"
    if re.search(r'\bchildren\b|\bkids\b|\bfamily\b', q, re.IGNORECASE):
        return "family_play"
    return None


def resolve_categories(q: str) -> list[str]:
    """
    Map a query to a list of category keys. The first matching pattern wins,
    so 'hotel near me' yields hotels, not restaurants. Falls back to food
    categories for eating words, then to a broad search when nothing is
    specific enough.
    """
    ql = q.lower()
    for key, phrases in QUERY_PATTERNS:
        for phrase in phrases:
            if phrase.lower() in ql:
                return [key]
    if any(w in ql for w in ("food", "foods", "eat", "eating", "eats",
                             "hungry", "bite to eat", "dinner", "lunch",
                             "lunch spot")):
        return ["restaurant", "fast_food", "cafe", "food_court", "bakery",
                "ice_cream"]
    return [k for k in BROAD_SEARCH]


class Intent:
    __slots__ = (
        "query", "categories", "budget", "currency", "budget_hint",
        "open_now", "time_hint", "distance_hint", "sort", "start_time",
        "alone", "time_available_hours", "max_distance_km",
    )

    def __init__(self, query: str):
        q = (query or "").strip().lower()
        self.query = query or ""
        self.categories = resolve_categories(q)
        self.currency = detect_currency(q) or "GHS"
        self.budget = extract_amount(q)
        self.budget_hint = None
        for hint, words in BUDGET_WORDS.items():
            for w in words:
                if w in q:
                    self.budget_hint = hint
                    break
            if self.budget_hint:
                break
        self.open_now = detect_open_now(q)
        self.time_hint = detect_time_hint(q)
        self.distance_hint = detect_distance_hint(q)
        self.sort = detect_sort(q)
        self.start_time = None
        self.alone = True
        self.time_available_hours = 0
        self.max_distance_km = 5.0

    def to_overrides(self) -> dict:
        """Return an overrides dict merged over explicit frontend controls."""
        overrides = {
            "categories": list(self.categories),
            "currency": self.currency,
            "budget": self.budget,
            "budget_hint": self.budget_hint,
            "open_now": self.open_now,
            "time_hint": self.time_hint,
            "sort": self.sort,
            "distance_hint": self.distance_hint,
        }
        if self.budget_hint == "free":
            overrides["budget"] = 0
        return overrides


def hour_from_hint(time_hint: Optional[str], now_hour: int = 20) -> Optional[str]:
    """Translate a time hint into an approximate 'HH:MM' check time."""
    if time_hint == "morning":
        return "09:00"
    if time_hint == "evening":
        return f"{max(int(now_hour), 20):02d}:00"
    if time_hint == "night":
        return "23:00"
    return None


def all_american_keywords() -> list[str]:
    """Not used at runtime; kept for documentation/tests."""
    return list(set(w for _, words in QUERY_PATTERNS for w in words))