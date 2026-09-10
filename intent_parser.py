"""
LocalLens - Natural-language search intent parser.

Turns free-text local searches into a structured SearchIntent so that
search can be driven by what the user meant instead of by exact category
buttons.

Architecture (data-driven, NOT a giant if/else tree):

  natural language
    -> intent extraction   (central taxonomy + synonym layer + subcategories)
    -> structured intent   (category, subcategory, keywords, modifiers,
                            budget, distance, rating, location, confidence)
    -> category expansion  (which OSM categories to actually search)
    -> relevance filter    (see scoring.relevance_*)
    -> ranking             (see scoring.score_place)

Everything is deterministic and runs locally - no AI calls are made here.
Repeated interpretations are cached so the same query never re-parses.

Honesty rules:
  - The parser interprets the QUERY. It never invents business facts.
  - Ghana (GHS) is the default currency; no exchange rates are invented.
  - Ambiguous queries are reported as ambiguous instead of over-confident.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from categories import BROAD_SEARCH

# ── Currency detection (Ghana default) ────────────────────────────────────────
# Recognizes common symbols/codes without converting any rates.

CURRENCY_PATTERNS = [
    (r'gh[\u00b5\u20b5]?c?\b', "GHS"),     # GHC / GH¢ (legacy code)
    (r'\bghc\s*\d', "GHS"),                # ghc300 (no space) amounts
    (r'gh\u20b5', "GHS"),
    (r'\u00a2', "GHS"),                    # ¢
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

# ── Category groups (pseudo categories that expand to real catalog keys) ──────

FOOD_FAMILY = [
    "restaurant", "fast_food", "food_court", "cafe", "bakery", "ice_cream",
]

GROUP_CATEGORIES = {
    "food": FOOD_FAMILY,
    "nightlife": ["nightclub", "bar", "pub", "casino", "biergarten"],
    "accommodation": ["hotel", "hostel", "camp_site"],
}


# ── Headwords: tiny queries that resolve instantly and with high confidence ───

CATEGORY_HEADWORDS: dict[str, list[str]] = {
    "restaurant": ["restaurant", "eatery", "chopbar", "chop bar"],
    "fast_food": ["fastfood", "fast food", "takeaway"],
    "cafe": ["cafe", "caf\u00e9", "coffeehouse"],
    "bar": ["bar", "pub", "tavern", "lounge bar"],
    "hotel": ["hotel", "motel", "inn", "lodging", "guesthouse", "guest house"],
    "hostel": ["hostel"],
    "nightclub": ["nightclub", "club"],
    "casino": ["casino"],
    "shopping_mall": ["mall", "shoppingmall", "shopping mall"],
    "supermarket": ["supermarket", "grocery", "minimart", "mini mart"],
    "book_store": ["bookstore", "bookshop", "book store", "book shop"],
    "market": ["market", "marketplace"],
    "hardware": ["hardware"],
    "pharmacy": ["pharmacy", "chemist", "drugstore", "drug store"],
    "hospital": ["hospital"],
    "clinic": ["clinic", "doctor", "medical centre", "medical center"],
    "dentist": ["dentist"],
    "optician": ["optician", "optometrist"],
    "school": ["school", "kindergarten", "preschool", "nursery"],
    "college": ["college", "polytechnic"],
    "university": ["university"],
    "library": ["library"],
    "museum": ["museum"],
    "gallery": ["gallery"],
    "theatre": ["theatre", "theater"],
    "cinema": ["cinema"],
    "attraction": ["attraction"],
    "zoo": ["zoo"],
    "aquarium": ["aquarium"],
    "church": ["church"],
    "mosque": ["mosque"],
    "gym": ["gym", "fitness center", "fitness centre"],
    "swimming_pool": ["swimming pool", "pool"],
    "barber": ["barber", "barbershop", "barber shop"],
    "salon": ["salon", "hairdresser"],
    "spa": ["spa"],
    "massage": ["massage"],
    "nails": ["manicure", "pedicure", "nail salon"],
    "tailor": ["tailor", "seamstress"],
    "laundry": ["laundry", "laundromat", "dry cleaner"],
    "printing": ["print shop", "printing", "photocopy"],
    "internet_cafe": ["internet cafe", "internet caf\u00e9", "cyber cafe"],
    "coworking": ["coworking", "co-working", "workspace"],
    "car_repair": ["mechanic", "garage", "car repair"],
    "car_wash": ["car wash"],
    "car_rental": ["car rental", "car hire"],
    "fuel": ["petrol", "gas station", "fuel"],
    "charging": ["charging station", "ev charger"],
    "post_office": ["post office"],
    "bank": ["bank"],
    "atm": ["atm", "cashpoint"],
    "money_transfer": ["money transfer", "currency exchange",
                       "bureau de change"],
    "insurance": ["insurance"],
    "medical_lab": ["medical lab", "medical laboratory"],
    "police": ["police station", "police"],
    "fire_station": ["fire station"],
    "pet_grooming": ["pet grooming", "dog grooming"],
    "pet_shop": ["pet shop", "pet store"],
    "lawyer": ["lawyer", "attorney", "solicitor", "notary"],
    "accountant": ["accountant"],
    "construction_company": ["construction", "builder", "builders",
                             "contractor", "building company"],
    "building_materials": ["building materials", "building material"],
    "electrician": ["electrician"],
    "plumber": ["plumber"],
    "carpenter": ["carpenter"],
    "painter": ["painter"],
    "roofer": ["roofer"],
    "cleaning": ["cleaning service", "cleaning"],
    "security_services": ["locksmith", "security"],
    "it_services": ["web design", "web developer", "software company",
                    "app developer", "it company"],
    "photography": ["photographer", "photography"],
    "auto_parts": ["auto parts", "car parts", "tyre shop", "tire shop"],
    "bus_station": ["bus station", "bus stop"],
    "taxi": ["taxi"],
    "gaming": ["arcade", "bowling", "gaming"],
    "phone_repair": ["phone repair", "repair shop", "screen repair"],
    "phone_shop": ["phone shop", "phone store", "mobile phone shop"],
}


# ── Synonyms: the centralized natural-language layer ─────────────────────────
# Keyed by category (or pseudo-group). Phrases are matched by ordered
# subsequence with prefix-stem tolerance, so everyday phrasing ("where can I
# repair my samsung") resolves without exact keyword matching.

CATEGORY_SYNONYMS: dict[str, list[str]] = {
    # ── Food & drink ──
    "food": [
        "somewhere to eat", "something to eat", "place to eat",
        "places to eat", "place to get food", "where to eat",
        "places to get food", "food", "eat out", "get something to eat",
        "food joint", "food spot", "where can i eat", "hungry",
        "food places", "cheap food", "food near me",
    ],
    "restaurant": [
        "restaurant", "fine dining", "family restaurant",
        "romantic restaurant", "cheap restaurant", "restaurants", "eatery",
        "chop bar", "dining", "dinner", "lunch", "brunch", "breakfast",
        "diner", "steakhouse", "seafood restaurant", "vegetarian restaurant",
        "vegan restaurant", "grill", "barbecue", "bbq", "buffet",
        "ghanaian food", "african food", "local food", "chinese restaurant",
        "indian restaurant", "lebanese restaurant", "italian restaurant",
        "pizza", "pizza place", "burger", "burgers", "burger place",
        "fried chicken", "shawarma", "jollof", "waakye", "banku", "fufu",
        "kenkey", "street food",
    ],
    "fast_food": [
        "fast food", "takeout", "take away", "takeaway", "junk food",
        "fastfood", "falafel", "kebab", "shawarma",
    ],
    "cafe": [
        "cafe", "caf\u00e9", "coffee shop", "coffee house", "coffeehouse",
        "coffee", "tea room", "quiet cafe", "cafe for studying",
        "study cafe", "breakfast spot", "brunch spot", "tea shop",
        "cakes and coffee",
    ],
    "food_court": ["food court", "food hall"],
    "bakery": ["bakery", "bakeries", "bread", "cake shop", "pastry",
               "pastries", "cake delivery"],
    "ice_cream": ["ice cream", "frozen yogurt", "gelato", "frozen yoghurt"],
    "bar": ["bar", "sports bar", "wine bar", "cocktail bar", "beer", "drink",
            "drinks", "taproom", "microbrewery", "brewery"],
    "pub": ["pub", "tavern", "irish pub", "inn"],
    "nightclub": ["nightclub", "night club", "club", "dance club"],
    "liquor_store": ["liquor store", "off licence", "off-licence",
                     "alcohol shop"],
    "supermarket": ["supermarket", "grocery store", "grocery shop", "grocery",
                    "groceries", "mini mart", "convenience store",
                    "provisions"],
    "butcher": ["butcher", "butcher shop", "meat shop"],
    "greengrocer": ["greengrocer", "fruit and veg", "fruit and vegetable",
                    "vegetable shop", "vegetable market", "produce"],
    "fishmonger": ["fishmonger", "fish shop", "fish market", "fresh fish"],
    "market": ["market", "bazaar", "farmers market", "farmer's market"],

    # ── Lodging ──
    "hotel": [
        "hotel", "hotels", "guest house", "bed and breakfast", "b and b",
        "places to stay", "place to stay", "accommodation", "place to sleep",
        "where to sleep", "short stay apartment", "vacation rental",
        "holiday apartment", "airbnb", "boutique hotel", "budget hotel",
        "luxury hotel", "cheap hotel", "resort", "beach resort", "lodge",
        "somewhere to sleep", "sleep",
    ],
    "hostel": ["hostel", "youth hostel", "budget hostel", "backpackers"],
    "camp_site": ["campsite", "camp site", "camping", "caravan park"],

    # ── Shopping ──
    "shopping_mall": ["shopping mall", "shopping centre", "shopping center",
                      "mall", "shopping complex"],
    "clothing_store": ["clothing store", "clothes shop", "clothes store",
                       "clothing", "fashion", "dress shop", "shoe shop",
                       "shoe store", "shoes", "sneakers", "sportswear",
                       "boutique", "men's clothing", "women's clothing",
                       "kids clothing", "children's clothing", "african wear",
                       "kente"],
    "electronics_store": ["electronics store", "electronics", "tv shop",
                          "led tv", "home appliance", "appliance store",
                          "computer store", "laptop shop"],
    "phone_shop": ["phone shop", "phone store", "mobile phone store",
                   "mobile phones", "buy a phone", "buy a new phone",
                   "phones", "phone dealer", "iphone store", "accessories shop",
                   "phone accessories"],
    "phone_repair": [
        "phone repair", "phone fix", "fix my phone", "fix my iphone",
        "fix my samsung", "fix my android", "fix phones", "fixes phones",
        "someone who fixes phones", "repair my phone", "repair phone",
        "mobile repair", "phone technician", "cracked phone", "broken phone",
        "cracked screen", "screen repair", "screen replacement",
        "iphone repair", "android repair", "samsung repair", "repair samsung",
        "repair android", "repair my samsung", "repair my iphone",
        "laptop repair", "laptop fix", "computer repair", "tablet repair",
        "repair my laptop", "fix my laptop", "fix my computer",
        "electronics repair", "phone repairs", "fix cracked screen",
    ],
    "furniture_store": ["furniture", "furniture store", "sofa shop",
                        "mattress store", "mattress", "bedding"],
    "book_store": [
        "bookstore", "book store", "bookshop", "book shop", "books",
        "where to buy books", "somewhere to buy books", "place to buy books",
        "book seller", "bookseller", "buy books",
    ],
    "hardware": ["hardware", "hardware store", "diy shop", "diy store",
                 "diy", "paint shop", "paint store", "tools", "tool shop"],
    "jewelry": ["jewellery", "jewelry", "jeweller", "goldsmith", "watch shop",
                "watch store", "jewelry store"],
    "optician": ["optician", "eyeglasses", "eye glasses", "glasses",
                 "spectacles", "lens"],
    "chemist_drug": ["chemist", "drug store", "drugstore"],
    "pet_shop": ["pet shop", "pet store", "pet food", "pet supplies"],

    # ── Health & medical ──
    "pharmacy": [
        "pharmacy", "pharmacies", "chemist", "place to get medicine",
        "medicine", "medication", "drugs", "dispensary", "buy medicine",
        "medicines", "prescription",
    ],
    "hospital": ["hospital", "emergency", "emergency room", "a and e",
                 "health facility", "healthcare centre"],
    "clinic": ["clinic", "doctor", "doctors", "medical centre",
               "medical center", "general practitioner", "gp", "physician",
               "health centre", "health center", "specialist doctor",
               "dermatologist", "cardiologist", "pediatrician", "checkup",
               "medical check up"],
    "dentist": ["dentist", "dental clinic", "dental", "teeth", "toothache"],
    "medical_lab": ["medical lab", "laboratory", "blood test", "x ray",
                    "ultrasound", "mri", "diagnostic centre",
                    "diagnostic center", "medical imaging", "covid test",
                    "vaccination center"],
    "veterinary": ["vet", "veterinarian", "animal clinic",
                   "veterinary clinic", "pet doctor"],
    "optician": ["eye test", "eye clinic", "eye doctor"],

    # ── Education ──
    "school": [
        "school", "schools", "primary school", "junior high school",
        "senior high school", "international school", "private school",
        "public school", "nursery school", "kindergarten", "preschool",
        "daycare", "school for children", "montessori",
    ],
    "college": ["college", "polytechnic", "technical school",
                "vocational school"],
    "university": ["university", "uni", "higher education"],
    "library": ["library", "libraries", "public library", "read books"],
    "music_school": ["music school", "music lessons", "music classes",
                     "dance school", "dance classes", "singing lessons"],
    "driving_school": ["driving school", "driving lessons",
                       "driving instructor"],
    "language_school": ["language school", "english classes", "learn english",
                        "french classes", "ielts classes", "toefl classes",
                        "wassce classes"],
    "tutoring": ["tutor", "tutors", "tutoring", "private tutor", "math tutor",
                 "mathematics tutor", "english tutor", "science tutor"],
    "study": ["study center", "study centre", "place to study", "study area",
              "study hall"],

    # ── Culture & entertainment ──
    "museum": ["museum", "museums", "history museum", "art museum"],
    "gallery": ["art gallery", "gallery", "art exhibit", "art exhibition",
                "exhibition hall"],
    "theatre": ["theatre", "theater", "stage play", "live show", "play house"],
    "cinema": ["cinema", "movie theater", "movie theatre", "movies",
               "watch a film", "watch movie", "watch films", "film screening",
               "movie night"],
    "arts_centre": ["arts centre", "art center", "cultural centre",
                    "cultural center", "community arts"],
    "attraction": ["tourist attraction", "attraction", "amusement park",
                   "theme park", "water park", "fun park", "sightseeing",
                   "places to visit"],
    "viewpoint": ["viewpoint", "scenic view", "overlook", "panoramic view"],
    "zoo": ["zoo", "zoo park", "wildlife park"],
    "aquarium": ["aquarium", "sea life centre"],
    "monument": ["monument", "landmark", "castle", "historic site",
                 "historical site", "ruins", "statue", "memorial"],
    "event_venue": ["event venue", "event centre", "event center",
                    "conference", "conference center", "wedding venue",
                    "event space", "party venue", "convention centre"],
    "church": ["church", "chapel", "christian church"],
    "mosque": ["mosque", "islamic centre"],
    "place_of_worship": ["place of worship", "worship", "temple", "cathedral",
                         "synagogue", "prayer", "pray"],

    # ── Outdoors ──
    "park": ["park", "garden", "city park", "fun park"],
    "nature_reserve": ["nature reserve", "wildlife reserve", "nature park",
                       "national park"],
    "beach": ["beach", "seaside", "beachfront", "beach resort"],
    "playground": ["playground", "play area", "kids play", "adventure park"],
    "tourism": ["tourist guide", "tour company", "tour operator", "city tour",
                "tour guide", "boat tour", "safari", "hiking", "trekking",
                "scenic place", "picnic spot"],

    # ── Sports & fitness ──
    "gym": ["gym", "gymnasium", "fitness centre", "fitness center", "workout",
            "fitness", "exercise", "personal trainer", "crossfit",
            "boxing gym", "gym membership", "affordable gym"],
    "sports_centre": ["sports centre", "sports center", "sports complex",
                      "leisure centre", "recreation centre"],
    "stadium": ["stadium", "sports ground", "arena"],
    "swimming_pool": ["swimming pool", "swim", "pool", "swimming lessons",
                      "learn to swim"],
    "pitch": ["sports pitch", "sports field", "play football", "watch football",
              "soccer", "football", "basketball court", "tennis court",
              "football viewing center", "football viewing centre",
              "watch a match", "watch the match", "play soccer", "play ball",
              "football field", "volleyball court"],
    "sports": ["martial arts", "karate", "taekwondo", "boxing", "yoga studio",
               "pilates", "dance studio", "running club", "sports club"],

    # ── Personal care ──
    "barber": ["barber", "barbershop", "barber shop", "haircut",
               "get a haircut", "cheap barber", "hair trim", "fade",
               "beard trim", "men's haircut"],
    "salon": ["salon", "hairstylist", "hair salon", "hairdresser",
              "beauty salon", "women's salon", "braiding", "dreadlocks",
              "hair braiding", "wig shop", "wig maker", "makeup artist",
              "makeup studio"],
    "spa": ["spa", "wellness center", "wellness centre", "day spa",
            "wellness spa"],
    "massage": ["massage", "massage therapist", "massage studio",
                "body massage", "foot massage"],
    "nails": ["nail salon", "manicure", "pedicure", "nails",
              "nail technician", "acrylic nails", "eyelash", "facial"],
    "tailor": ["tailor", "seamstress", "alterations", "dressmaking"],
    "laundry": ["laundry", "dry cleaning", "laundromat", "wash clothes",
                "clean clothes", "ironing service"],
    "cleaning": ["cleaning service", "house cleaning", "office cleaning",
                 "home cleaning", "maid service", "janitorial"],
    "security_services": ["locksmith", "security company", "cctv",
                          "cctv installation", "install cctv",
                          "installs cctv", "cctv camera", "security cameras",
                          "alarm", "alarm installation"],
    "it_services": ["web design", "web developer", "website design",
                    "app developer", "app development", "software company",
                    "software developer", "it company", "it support",
                    "computer networking", "digital agency",
                    "digital marketing", "seo", "graphic design",
                    "graphic designer", "website", "build a website",
                    "video editor", "videographer"],

    # ── Services ──
    "printing": ["print shop", "printing", "photocopy", "copy shop",
                 "printer", "print documents", "where can i print",
                 "photo print", "digital printing", "photocopy shop"],
    "internet_cafe": ["internet cafe", "internet caf\u00e9", "cyber cafe",
                      "cybercafe", "internet shop"],
    "coworking": ["coworking", "co-working", "co working", "work space",
                  "workspace", "office space", "meeting room",
                  "office rental", "business center", "business centre"],
    "car_repair": ["car repair", "mechanic", "auto repair", "car service",
                   "garage", "service my car", "get my car serviced",
                   "car servicing", "accident repair", "car body repair",
                   "spray painting", "car maintenance"],
    "car_wash": ["car wash", "wash my car", "car detailing", "valet"],
    "car_rental": ["car rental", "rent a car", "hire car", "car hire"],
    "auto_parts": ["auto parts", "car parts", "tyre shop", "tire shop",
                   "vehicle parts", "spare parts", "battery shop",
                   "car battery"],
    "fuel": ["fuel", "petrol station", "gas station", "filling station",
             "petrol", "diesel", "refuel"],
    "charging": ["charging station", "ev charging", "ev charger",
                 "charge my car"],
    "bus_station": ["bus station", "bus terminal", "intercity bus"],
    "taxi": ["taxi", "taxi service", "cab", "ride service", "ride hire"],
    "post_office": ["post office", "postal service", "send mail",
                    "send parcel"],
    "bank": ["bank", "banking", "microfinance", "mobile banking"],
    "atm": ["atm", "cash machine", "cashpoint", "withdraw cash"],
    "money_transfer": ["money transfer", "money exchange", "currency exchange",
                       "bureau de change", "send money", "western union",
                       "mobile money"],
    "insurance": ["insurance", "insurance company", "car insurance",
                  "health insurance", "vehicle insurance", "insurance broker"],
    "lawyer": ["lawyer", "attorney", "legal advice", "solicitor", "notary",
               "law firm", "legal services", "litigation"],
    "accountant": ["accountant", "tax advisor", "bookkeeping", "accounting",
                   "tax consultant", "auditor"],
    "real_estate": ["real estate", "estate agent", "property agent",
                    "estate agency", "property management",
                    "property developer", "rent apartment", "buy a house",
                    "land agent"],
    "travel_agent": ["travel agent", "travel agency", "booking office",
                     "flight booking"],
    "photography": ["photographer", "photography", "photo studio",
                    "wedding photos", "photography for events",
                    "camera shop"],

    # ── Construction & trades ──
    "construction_company": [
        "construction company", "construction firm", "building contractor",
        "builders", "building company", "building construction",
        "civil engineer", "quantity surveyor", "structural engineer",
        "company that builds houses", "company that builds", "build houses",
        "build my house", "build a house", "building works", "house builder",
        "home builder", "residential construction", "contractor",
        "construction works", "someone to build my house",
        "firm that builds", "construction engineering", "house construction",
        "build my home",
    ],
    "building_materials": ["building materials", "construction materials",
                           "cement", "buy building materials",
                           "building material", "cement store",
                           "roofing materials", "lumber", "timber",
                           "sand and stones", "quarry products"],
    "electrician": ["electrician", "electrical contractor", "wiring",
                    "electrical work", "electrical installation",
                    "fix my wiring"],
    "plumber": ["plumber", "plumbing", "pipe repair", "fix my pipes",
                "plumbing work", "drainage", "toilet repair"],
    "carpenter": ["carpenter", "carpentry", "woodwork", "wood working",
                  "furniture maker"],
    "painter": ["painter", "painting service", "house painting",
                "paint my house", "painting contractor", "interior painting"],
    "roofer": ["roofer", "roofing", "roof repair", "roof installation",
               "leaking roof", "fix my roof"],
    "interior_design": ["interior design", "interior designer",
                        "interior decorator", "interior decoration",
                        "decorator"],
}


# ── Subcategories: services/variants inside a canonical category ─────────────
# label -> list of phrases. The label becomes a readable subcategory; the
# phrases flow into keywords so the relevance scorer can reward places whose
# name/description mention them.

SUBCATEGORY_SYNONYMS: dict[str, list[tuple[str, list[str]]]] = {
    "phone_repair": [
        ("iPhone repair", ["iphone repair", "repair iphone", "fix iphone",
                           "iphone"]),
        ("Android repair", ["android repair", "repair android",
                            "samsung repair", "repair samsung", "samsung",
                            "android"]),
        ("Screen repair", ["screen repair", "cracked screen",
                           "screen replacement", "replace screen",
                           "repair screen", "screen"]),
        ("Laptop repair", ["laptop repair", "repair laptop", "fix laptop",
                           "computer repair", "repair computer", "laptop",
                           "computer"]),
        ("Tablet repair", ["tablet repair", "ipad repair", "repair tablet",
                           "tablet"]),
        ("Cracked phone", ["cracked phone", "broken phone", "cracked"]),
    ],
    "construction_company": [
        ("Residential construction", ["residential construction",
                                      "residential buildings",
                                      "house construction", "build houses",
                                      "build a house", "build my house",
                                      "build my home", "house builder",
                                      "home builder", "residential building",
                                      "housing"]),
        ("Commercial construction", ["commercial construction",
                                     "commercial building",
                                     "office building",
                                     "industrial construction"]),
        ("Civil engineering", ["civil engineer", "structural engineer",
                               "engineering works", "roads", "bridge"]),
        ("Renovation", ["renovation", "renovate", "remodel", "extension"]),
    ],
    "restaurant": [
        ("Jollof", ["jollof"]),
        ("Waakye", ["waakye"]),
        ("Banku", ["banku"]),
        ("Fufu", ["fufu"]),
        ("Kenkey", ["kenkey"]),
        ("Pizza", ["pizza"]),
        ("Burgers", ["burger", "burgers"]),
        ("Fried chicken", ["fried chicken", "broasted chicken"]),
        ("Shawarma", ["shawarma", "kebab"]),
        ("Local Ghanaian food", ["ghanaian food", "local food",
                                 "african food", "local cuisine",
                                 "chop bar", "traditional food"]),
        ("Seafood", ["seafood", "fish", "grilled fish"]),
        ("Outdoor dining", ["outdoor seating", "outdoor dining", "rooftop",
                            "on the beach"]),
    ],
    "fast_food": [
        ("Burgers", ["burger", "burgers"]),
        ("Fried chicken", ["fried chicken", "broasted chicken"]),
        ("Shawarma", ["shawarma", "kebab"]),
        ("Pizza", ["pizza"]),
    ],
    "cafe": [
        ("Quiet / study-friendly", ["quiet", "study", "studying", "work",
                                    "concentrate", "read", "laptop",
                                    "study-friendly"]),
        ("Coffee", ["coffee", "espresso", "latte", "cappuccino"]),
    ],
    "hotel": [
        ("Swimming pool", ["swimming pool", "pool"]),
        ("Breakfast included", ["with breakfast", "breakfast included",
                                "breakfast"]),
        ("Parking", ["with parking", "parking"]),
        ("Gym", ["with gym", "gym", "fitness"]),
        ("Near airport", ["airport"]),
        ("Honeymoon", ["honeymoon", "honeymoon suite"]),
        ("Conference", ["conference", "business hotel", "meeting"]),
    ],
    "barber": [
        ("Haircut", ["haircut", "hair cut", "trim", "fade"]),
        ("Beard trim", ["beard", "beard trim"]),
    ],
    "salon": [
        ("Women's salon", ["women's salon", "woman salon", "women salon",
                           "ladies"]),
        ("Braiding", ["braiding", "braids", "braid"]),
        ("Dreadlocks", ["dreadlocks", "dreads", "locs"]),
        ("Makeup", ["makeup", "make-up", "bridal makeup"]),
        ("Nails", ["nails", "manicure", "pedicure"]),
    ],
    "gym": [
        ("Membership", ["membership", "affordable membership", "monthly",
                        "subscription"]),
        ("Personal training", ["personal trainer", "training"]),
    ],
    "clinic": [
        ("General checkup", ["checkup", "check-up", "general"]),
    ],
    "school": [
        ("Primary", ["primary school", "primary"]),
        ("Secondary", ["secondary school", "senior high school",
                       "junior high school", "high school"]),
        ("International", ["international school"]),
        ("Private", ["private school"]),
        ("Nursery", ["nursery", "kindergarten", "preschool", "daycare"]),
    ],
    "pitch": [
        ("Football", ["football", "soccer", "watch football", "play football"]),
        ("Basketball", ["basketball"]),
        ("Tennis", ["tennis"]),
    ],
    "car_repair": [
        ("Service", ["service", "servicing", "maintenance"]),
        ("Repair", ["repair", "fix", "accident", "body"]),
    ],
}


# ── Modifiers (centralized) ───────────────────────────────────────────────────

BUDGET_PREFERENCE_WORDS = {
    "low": ["cheap", "affordable", "budget", "inexpensive", "low cost",
            "low-cost", "value for money", "bargain", "economical",
            "reasonably priced", "cost effective", "cost-effective"],
    "high": ["expensive", "fancy", "upscale", "luxury", "premium", "splurge",
             "high end", "high-end", "posh", "exclusive", "fine dining",
             "top end"],
    "free": ["free", "no cost", "free of charge", "no fee"],
}

DISTANCE_PREFERENCE_WORDS = {
    "nearby": ["near me", "nearby", "close by", "close to me",
               "walking distance", "around the corner", "around here",
               "here near", "in the area", "around me", "near my area",
               "near", "nearish"],
    "moderate": ["not too far", "a few blocks"],
    "far": ["far", "farther", "further", "willing to travel",
            "worth the trip", "long drive", "anywhere"],
}

RATING_PREFERENCE_WORDS = {
    "high": ["highly rated", "top rated", "top-rated", "best rated",
             "best", "trusted", "popular", "good reviews", "well reviewed",
             "well rated", "excellent reviews", "highest rated", "top",
             "quality", "5 star", "4 star"],
}

OPEN_NOW_PHRASES = [
    "open now", "currently open", "open right now", "is open now",
    "open at this hour", "open today", "open as we speak",
]

TIME_HINT_PHRASES = {
    "morning": ["morning", "early", "breakfast", "brunch", "dawn", "sunrise",
                "before noon"],
    "afternoon": ["afternoon", "noon", "midday", "lunchtime", "lunch time"],
    "evening": ["evening", "tonight", "after 8", "after 8pm", "after 6",
                "after 7", "after 9", "sunset", "after work", "after dark",
                "late night", "open late", "after dusk",
                "open in the evening"],
    "night": ["night", "after midnight", "midnight", "late"],
}

USECASES = {
    "studying": ["study", "studying", "quiet", "concentrate", "read a book",
                 "do homework", "cram"],
    "date": ["date", "romantic", "anniversary", "valentine", "couple",
             "for a date"],
    "family": ["family", "children", "kids", "family friendly",
               "family-friendly", "child friendly", "for kids",
               "for children", "family outing"],
    "wedding": ["wedding", "bridal", "bridal shower", "honeymoon"],
    "business": ["business meeting", "meeting", "work call", "business",
                 "conference call", "client"],
    "tourists": ["tourist", "tourists", "visitors", "travellers",
                 "travelers", "vacation", "holiday"],
    "solo": ["solo", "alone", "by myself"],
    "group": ["group", "friends", "with friends", "big group", "party with"],
}

# Brand terms that are genuinely ambiguous without more context.
BRAND_TERMS = {
    "apple": ["phone_shop", "phone_repair", "restaurant"],
    "iphone": ["phone_shop", "phone_repair"],
    "samsung": ["phone_shop", "phone_repair"],
    "nokia": ["phone_shop", "phone_repair"],
}

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "at",
    "by", "with", "from", "near", "me", "my", "you", "your", "i", "we",
    "it", "is", "are", "was", "were", "do", "does", "did", "have", "has",
    "had", "can", "could", "will", "would", "should", "where", "what",
    "when", "which", "who", "how", "there", "here", "this", "that",
    "these", "those", "some", "any", "someone", "somebody", "somewhere",
    "something", "place", "places", "spot", "spots", "one", "looking",
    "look", "find", "need", "want", "like", "please", "get", "getting",
    "about", "over", "around", "under", "best", "good", "great", "nice",
    "cheap", "affordable", "budget", "nearby", "close", "closer",
    "closest", "nearest", "new", "old", "top", "better", "way", "ways",
    "just", "else", "things", "thing", "doing", "go", "going", "buy",
    "between", "than", "then", "very", "really", "quite", "also", "too",
    "our", "their", "his", "her", "its", "us", "now", "today", "tonight",
    "tonite", "free", "cheaply", "must", "maybe", "perhaps", "pretty",
    "much", "many", "lot", "lots", "kinds", "kind", "type", "types",
    "open", "closed", "rated", "rating", "reviews", "review", "fix", "fixing",
}

# ── Parsing helpers ───────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"[a-z0-9\u20b5\u00a2]+(?:'[a-z]+)?|[a-z][a-z0-9-]*")


def _tokens(text: str) -> list[str]:
    """Lowercase tokenization that keeps currency symbols and hyphenated words."""
    return _TOKEN_RE.findall(text.lower())


def _norm_currency_tokens(text: str) -> str:
    """Normalize common Ghanaian cedi spellings to a canonical code."""
    t = text.lower()
    t = re.sub(r"\bgh\s*c\b", "ghc", t)
    t = re.sub(r"\bgh\s*[\u20b5\u00a2]", "gh\u20b5", t)
    return t


def detect_currency(q: str) -> Optional[str]:
    for pattern, code in CURRENCY_PATTERNS:
        if re.search(pattern, q, re.IGNORECASE):
            return code
    return None


def _extract_amount(q: str) -> Optional[float]:
    """Extract a numeric budget figure; GHS unless the query says otherwise."""
    t = _norm_currency_tokens(q)
    patterns = [
        r"(?:under|below|less\s+than|upto|up\s+to|max(?:imum)?|beneath|"
        r"no\s+more\s+than|not\s+more\s+than)\s+gh\s*[\u20b5\u00a2]?\s*"
        r"(\d+(?:\.\d+)?)",
        r"(?:under|below|less\s+than|upto|up\s+to|max(?:imum)?|"
        r"no\s+more\s+than|not\s+more\s+than)\s+gh\s*c\s*(\d+(?:\.\d+)?)",
        r"(?:under|below|less\s+than|upto|up\s+to|max(?:imum)?|"
        r"no\s+more\s+than|not\s+more\s+than)\s*(\d+(?:\.\d+)?)\s*"
        r"(?:ghs|gh\s*[\u20b5\u00a2]|gh\s*c|cedis?|cedi|gh\u20b5|\$|\u20ac|"
        r"\u00a3|\u20a6|\u20b9|\u00a5)?\b",
        r"gh\s*[\u20b5\u00a2]\s*(\d+(?:\.\d+)?)",
        r"gh\s*c\s*(\d+(?:\.\d+)?)",
        r"(?:ghs|ghp|gh\s*c|cedis?|cedi|gh\u20b5)\s*(\d+(?:\.\d+)?)",
        r"(\d+(?:\.\d+)?)\s*(ghs|gh\s*[\u20b5\u00a2]|cedis?|cedi|gh\u20b5)\b",
        r"(?:budget|spend|spending|cost|price|pay)\s+(?:of\s+)?(\d+(?:\.\d+)?)",
        r"(\d+(?:\.\d+)?)\s*(usd|\$|\u20ac|eur|gbp|\u00a3|ngn|\u20a6|kes|"
        r"ksh|zar|a\$|c\$|inr|\u20b9|jpy|\u00a5)\b",
        r"(?:around|about|roughly)\s+(\d+(?:\.\d+)?)\b",
    ]
    for pat in patterns:
        m = re.search(pat, t)
        if m:
            val = float(m.group(1))
            if 0 < val <= 100000:
                return val
    return None


_STEM_SUFFIXES = ("s", "es", "ed", "ing", "ies", "d", "ly")

CURRENCY_TOKENS = {
    "ghc", "ghs", "ghp", "cedis", "cedi", "usd", "eur", "gbp",
    "ngn", "kes", "ksh", "zar", "aud", "cad", "inr", "jpy", "naira",
    "nairas", "rand", "rands",
}


def _stem_eq(qt: str, pt: str) -> bool:
    """Morphological token match: 'restaurants' <-> 'restaurant'.

    Only plain inflections (s/es/ed/ing/ies/d/ly) are allowed, so 'roof'
    does NOT match 'roofer' or 'roofing'-equivalents compounded wrongly.
    """
    if qt == pt:
        return True
    a, b = (qt, pt) if len(qt) <= len(pt) else (pt, qt)
    if len(a) < 4:
        return False
    return b.startswith(a) and b[len(a):] in _STEM_SUFFIXES


def _phrase_score(query_tokens: list[str], phrase: str) -> float:
    """Ordered-subsequence phrase match with adjacency + stem tolerance.

    Returns 0.0 when the phrase does not match, else a score ~ phrase length
    with a bonus for adjacency and exact (non-stemmed) matches.
    """
    p = _tokens(phrase)
    if not p:
        return 0.0
    if len(p) == 1:
        for qt in query_tokens:
            if _stem_eq(qt, p[0]):
                return 1.0
        return 0.0

    qi = 0
    positions: list[int] = []
    exact = 0
    for i, qt in enumerate(query_tokens):
        if qi < len(p) and _stem_eq(qt, p[qi]):
            positions.append(i)
            if qt == p[qi]:
                exact += 1
            qi += 1
        if qi == len(p):
            break
    if qi != len(p):
        return 0.0

    total_pairs = max(len(p) - 1, 1)
    adj = sum(1 for a, b in zip(positions, positions[1:]) if b == a + 1)
    base = len(p) * (0.6 + 0.4 * (adj / total_pairs))
    return round(base + 0.15 * exact, 3)


def _headword_boosts(query_tokens: list[str],
                     matched_phrases: set[str]) -> dict[str, float]:
    """Bare single-word category mentions outrank longer symptom phrases.

    Ignored when the token is already explained by a matched multi-word
    phrase (e.g. 'school' inside 'driving school' must not outrank it).
    """
    owned_by_long = set()
    for phrase in matched_phrases:
        p = _tokens(phrase)
        if len(p) >= 2:
            owned_by_long.update(p)
    boosts: dict[str, float] = {}
    for cat, heads in CATEGORY_HEADWORDS.items():
        for h in heads:
            ht = _tokens(h)
            if len(ht) == 1 and ht[0] in query_tokens \
                    and ht[0] not in owned_by_long:
                boosts[cat] = max(boosts.get(cat, 0.0), 1.5)
    return boosts


def _extract_location_place(q: str) -> Optional[str]:
    """Extract 'near the airport' / 'near the university' style targets.
    Returns None for 'near me' and other non-targets."""
    t = _norm_currency_tokens(q)
    t = re.sub(r"[.,;:?!]", " ", t)
    m = re.search(r"\bnear\s+(?P<rest>[\w\s-]+?)\s*$", t)
    if not m:
        return None
    rest = m.group("rest").strip()
    if re.match(r"^(me|my|here)\b", rest):
        return None
    words = rest.split()
    for stop in ("under", "with", "open", "have", "has", "and", "to", "for",
                 "that", "please", "in", "on", "at", "nearby", "then", "now",
                 "who", "can", "i", "we", "it", "is", "are", "or", "if"):
        if stop in words:
            words = words[:words.index(stop)]
    place = " ".join(w for w in words
                     if w not in ("the", "a", "an", "my", "me", "of"))
    place = place.strip().strip("-").strip()
    if len(place) < 2 or re.match(r"^(me|my|here)\b", place):
        return None
    return place


def _extract_distance_max(q: str) -> Optional[float]:
    """'within 2km' / 'within 2000m' -> max distance in kilometres."""
    t = q.lower()
    m = re.search(r"(?:within|inside|in)\s+(?:a\s+)?(\d+(?:\.\d+)?)\s*"
                  r"(km|kms?|kilometers?|kilometres?|m|metres?|meters?)\b", t)
    if m:
        val = float(m.group(1))
        unit = m.group(2).lower()
        if unit.startswith("km") or unit.startswith("kilom"):
            return max(0.2, val)
        return max(0.2, val / 1000.0)
    return None


def _extract_rating_minimum(q: str) -> Optional[float]:
    m = re.search(r"\b(\d(?:\.\d)?)\s*\+?\s*stars?\b", q.lower())
    if not m:
        m = re.search(r"\brated\s+(\d(?:\.\d)?)\s*(\+|and\s+above)?\b",
                      q.lower())
    if m:
        val = float(m.group(1))
        if 1 <= val <= 5:
            return val
    return None


@dataclass
class SearchIntent:
    """Structured interpretation of a natural-language local search."""

    original_query: str = ""
    category: Optional[str] = None          # primary canonical category
    categories: list = field(default_factory=list)   # categories to search
    subcategory: Optional[str] = None
    subcategory_keywords: list = field(default_factory=list)
    keywords: list = field(default_factory=list)
    location: dict = field(default_factory=dict)
    budget: dict = field(default_factory=lambda: {
        "amount": None, "currency": "GHS", "preference": "none"})
    distance: dict = field(default_factory=lambda: {
        "preference": "none", "max_distance": None})
    rating: dict = field(default_factory=dict)
    open_now: bool = False
    time_hint: Optional[str] = None
    distance_hint: Optional[str] = None
    sort: Optional[str] = None
    alone: bool = True
    time_available_hours: float = 0
    max_distance_km: float = 5.0
    intent: str = ""
    modifiers: list = field(default_factory=list)
    confidence: float = 0.0
    ambiguous: bool = False
    candidates: list = field(default_factory=list)

    @property
    def specific(self) -> bool:
        """A query is 'specific' when we are confident about its category."""
        return bool(self.category) and not self.ambiguous

    @property
    def budget_hint(self) -> Optional[str]:
        pref = self.budget.get("preference")
        return pref if pref in ("low", "high", "free") else None

    @property
    def budget_amount(self) -> Optional[float]:
        return self.budget.get("amount")

    @property
    def currency(self) -> str:
        return self.budget.get("currency", "GHS")

    def to_structured(self) -> dict:
        """The structured-intent JSON shape used across the API."""
        return {
            "original_query": self.original_query,
            "category": self.category,
            "subcategory": self.subcategory,
            "subcategory_keywords": list(self.subcategory_keywords),
            "keywords": list(self.keywords),
            "location": dict(self.location),
            "budget": dict(self.budget),
            "distance": dict(self.distance),
            "rating": dict(self.rating),
            "open_now": self.open_now,
            "intent": self.intent,
            "modifiers": list(self.modifiers),
            "confidence": round(self.confidence, 2),
            "ambiguous": self.ambiguous,
            "candidates": list(self.candidates),
            "search_categories": list(self.categories),
        }


def _clean_keywords(query_tokens: list[str], matched_phrases: set[str],
                    extras: set[str]) -> list[str]:
    """Residual meaningful words not explained by category/modifier phrases."""
    owned = set()
    for phrase in matched_phrases:
        owned.update(_tokens(phrase))
    kept = []
    for qt in query_tokens:
        if qt in owned or qt in STOPWORDS or re.search(r"\d", qt):
            continue
        if qt in extras or qt in CURRENCY_TOKENS:
            continue
        if qt not in kept:
            kept.append(qt)
    return kept[:10]


def _match_subcategories(category: Optional[str],
                         query_tokens: list[str]) -> list[str]:
    """Return matched subcategory labels for the resolved category."""
    if not category:
        return []
    hits = []
    for label, phrases in SUBCATEGORY_SYNONYMS.get(category, []):
        for phrase in phrases:
            if _phrase_score(query_tokens, phrase) > 0:
                hits.append(label)
                break
    return hits


def _wordy(cat: str) -> str:
    return cat.replace("_", " ")


def _real_category(key: str) -> str:
    """Group pseudo-keys map to their family anchor."""
    if key == "food":
        return "restaurant"
    if key == "nightlife":
        return "nightclub"
    if key == "accommodation":
        return "hotel"
    return key


def _describe(cat: str, phrase: str) -> str:
    if cat == "food":
        return "find somewhere to eat near you"
    if phrase:
        return f"looking for a {phrase}"
    return f"find a {_wordy(cat)} near you"


def _expand_one(cat: str) -> list[str]:
    if cat in GROUP_CATEGORIES:
        return list(GROUP_CATEGORIES[cat])
    if cat == "restaurant":
        return ["restaurant", "fast_food", "cafe"]
    if cat == "hotel":
        return ["hotel", "hostel", "camp_site"]
    if cat == "car_repair":
        return ["car_repair", "auto_parts", "car_wash"]
    if cat == "construction_company":
        return ["construction_company", "building_materials", "electrician",
                "plumber", "carpenter", "painter", "roofer"]
    if cat == "study":
        return ["cafe", "library", "coworking"]
    if cat == "tutoring":
        return ["school", "college", "university", "library"]
    if cat == "tourism":
        return ["attraction", "museum", "viewpoint", "travel_agent"]
    if cat == "sports":
        return ["sports_centre", "gym", "swimming_pool", "stadium", "pitch"]
    if cat == "clothing_store":
        return ["clothing_store", "shopping_mall"]
    if cat == "security_services":
        return ["security_services", "phone_repair"]
    if cat == "it_services":
        return ["it_services", "photography", "printing"]
    return [cat] if cat else list(BROAD_SEARCH)


def expand_categories(category: Optional[str], candidates: list,
                      ambiguous: bool = False) -> list[str]:
    """Turn the resolved category into the OSM category list to query."""
    if not category and not candidates:
        return list(BROAD_SEARCH)
    if category and not ambiguous:
        return _expand_one(category)
    out = []
    for cand in (candidates or [category]):
        out.extend(_expand_one(cand))
    out = list(dict.fromkeys(out))
    return out or list(BROAD_SEARCH)


def _extract_modifiers(intent: SearchIntent, ql: str) -> None:
    q = ql

    # ── Budget preference ──
    pref = "none"
    for level, words in BUDGET_PREFERENCE_WORDS.items():
        for w in words:
            if w in q:
                pref = level
                break
        if pref != "none":
            break
    intent.budget["preference"] = pref

    # ── Budget amount + currency (GHS default, never USD) ──
    currency = detect_currency(intent.original_query) or "GHS"
    amount = _extract_amount(intent.original_query)
    if amount is not None:
        intent.budget["amount"] = amount
        intent.budget["currency"] = currency

    # ── Distance ──
    dpref = "none"
    for level, words in DISTANCE_PREFERENCE_WORDS.items():
        for w in words:
            if w in q:
                dpref = level
                break
        if dpref != "none":
            break
    intent.distance["preference"] = dpref
    intent.distance_hint = dpref if dpref in ("nearby", "far") else None
    dmax = _extract_distance_max(q)
    if dmax is not None:
        intent.distance["max_distance"] = round(dmax, 2)
        if dpref == "none":
            intent.distance["preference"] = "nearby"

    # ── Rating ──
    rmin = _extract_rating_minimum(q)
    rpref = "high" if any(w in q for w in RATING_PREFERENCE_WORDS["high"]) \
        else "none"
    if rmin is not None:
        intent.rating["minimum"] = rmin
    if rpref != "none":
        intent.rating["preference"] = rpref
        intent.rating.setdefault("minimum", 4.0)

    # ── Open now ──
    intent.open_now = any(ph in q for ph in OPEN_NOW_PHRASES)

    # ── Time hint ──
    hint = None
    for t, words in TIME_HINT_PHRASES.items():
        for w in words:
            if w in q:
                hint = t
                break
        if hint:
            break
    intent.time_hint = hint

    # ── Sort ──
    if re.search(r"\b(closest|nearest)\b", q):
        intent.sort = "distance"
    elif re.search(r"\b(cheapest|lowest\s*price)\b", q):
        intent.sort = "price"

    # ── Use cases / atmosphere ──
    for label, words in USECASES.items():
        for w in words:
            if w in q:
                if label not in intent.modifiers:
                    intent.modifiers.append(label)
                break

    # ── Location target ("near the airport", "near the university") ──
    place = _extract_location_place(q)
    if place:
        intent.location = {"type": "named_place", "query": place}
    elif intent.distance.get("preference") in ("nearby", "far"):
        intent.location = {"type": "nearby"}
    else:
        intent.location = {"type": "none"}


# ── Parse cache ──────────────────────────────────────────────────────────────

_parse_cache: dict = {}
_PARSE_CACHE_MAX_SIZE = 600


def _cache(key: str, intent: SearchIntent) -> None:
    if len(_parse_cache) >= _PARSE_CACHE_MAX_SIZE:
        try:
            oldest = next(iter(_parse_cache))
            del _parse_cache[oldest]
        except StopIteration:
            pass
    _parse_cache[key] = intent


def parse_query(query: str) -> SearchIntent:
    """Parse a natural-language query into a structured SearchIntent."""
    raw = (query or "").strip()
    if not raw:
        return SearchIntent(original_query="")

    key = raw.lower().strip()
    cached = _parse_cache.get(key)
    if cached is not None:
        return cached

    q = _norm_currency_tokens(raw)
    ql = q.lower().strip()
    toks = _tokens(ql)
    intent = SearchIntent(original_query=raw)
    matched_phrases: set[str] = set()

    # ── Fast path: a single known headword resolves instantly ──
    fast_head = None
    for cat, heads in CATEGORY_HEADWORDS.items():
        for h in heads:
            if _tokens(h) == toks:
                intent.category = cat
                intent.candidates = [cat]
                intent.confidence = 0.95
                intent.intent = _describe(cat, "")
                fast_head = h
                break
        if fast_head:
            break
    if fast_head:
        matched_phrases.add(fast_head)

    # ── Full phrase scoring across the centralized synonym layer ──
    scores: dict[str, float] = {}
    best_phrase: dict[str, str] = {}
    if not intent.category:
        for cat, phrases in CATEGORY_SYNONYMS.items():
            total = 0.0
            best = ""
            best_s = 0.0
            for phrase in phrases:
                s = _phrase_score(toks, phrase)
                if s > 0:
                    matched_phrases.add(phrase)
                    total += s
                    if s > best_s:
                        best_s = s
                        best = phrase
            if total > 0:
                scores[cat] = round(total, 3)
                best_phrase[cat] = best

    if scores:
        for cat, bonus in _headword_boosts(toks, matched_phrases).items():
            scores[cat] = round(scores.get(cat, 0.0) + bonus, 3)
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        top_cat, top_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        intent.category = _real_category(top_cat)
        intent.candidates = [c for c, _ in ranked[:4]]
        # Ambiguity: two interpretations within 25% of each other.
        intent.ambiguous = (
            len(ranked) > 1
            and second_score >= 0.75 * top_score
            and top_score - second_score < max(1.0, 0.3 * top_score)
        )
        if top_score >= 4:
            intent.confidence = 0.9
        elif top_score >= 2:
            intent.confidence = 0.85
        elif top_score >= 1:
            intent.confidence = 0.7
        else:
            intent.confidence = 0.55
        if intent.ambiguous:
            intent.confidence = min(intent.confidence, 0.55)
        intent.intent = _describe(top_cat, best_phrase.get(top_cat) or "")
    elif not intent.category:
        # ── Brand / genuinely ambiguous single terms ──
        if len(toks) == 1 and toks[0] in BRAND_TERMS:
            intent.candidates = list(BRAND_TERMS[toks[0]])
            intent.category = intent.candidates[0]
            intent.ambiguous = True
            intent.confidence = 0.5
            intent.intent = (
                f"{toks[0].capitalize()} - could be several kinds of places")
        else:
            intent.confidence = 0.15
            intent.intent = "broad search across all local places"

    # ── Expand into the OSM categories we should actually search ──
    intent.categories = expand_categories(intent.category, intent.candidates,
                                          intent.ambiguous)

    extras: set[str] = set()
    sub = _match_subcategories(intent.category, toks)
    if sub:
        intent.subcategory = sub[0]
        for label, phrases in SUBCATEGORY_SYNONYMS.get(intent.category, []):
            if label == sub[0]:
                for phrase in phrases:
                    if _phrase_score(toks, phrase) > 0:
                        extras.update(_tokens(phrase))
                break

    intent.subcategory_keywords = sorted(
        e for e in extras if e not in STOPWORDS)

    intent.keywords = _clean_keywords(toks, matched_phrases, extras)

    _extract_modifiers(intent, ql)

    if not intent.category:
        intent.confidence = 0.15

    _cache(key, intent)
    return intent


def hour_from_hint(time_hint: Optional[str], now_hour: int = 20) -> Optional[str]:
    """Translate a time hint into an approximate 'HH:MM' check time."""
    if time_hint == "morning":
        return "09:00"
    if time_hint == "evening":
        return f"{max(int(now_hour), 20):02d}:00"
    if time_hint == "night":
        return "23:00"
    return None