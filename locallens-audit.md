# LocalLens — Product Audit & Market-Readiness Report

**App:** https://locallens-tan.vercel.app/ · **Date of audit:** 2026-09-09
**Method:** live API probing (30+ requests across 8 cities), end-to-end run in a real headless Chromium (desktop + mobile), and static review of the shipped HTML/JS. Every issue below was reproduced against the live deployment, not guessed.

---

## Part 1 — Genuine problems

### 🔴 P0 · The core promise — "What can I do *right now*?" — is not implemented

The tagline and the entire product pitch is time-awareness, but the app never checks the clock against opening hours.

| Evidence | Result |
|---|---|
| UI "Available from" time picker | **Dead control.** `#startTime` appears only in the HTML; the JS reads it 0 times and always sends `start_time: null`. |
| Backend with `"start_time": "03:00"` vs `null` (nightlife query, Accra) | **Identical 30 results.** The parameter is accepted and echoed back, never used for filtering. |
| "Something I can do after 8pm" quick-pill | Changed scores on only 2 of 30 results; places with unknown hours pass through untouched. |
| Hours data | 71% of 189 sampled results show "Hours not listed" — so even an honest open-now filter would need a missing-data strategy. |

A user asking "things to do tonight" can be shown a place that closes at 20:00, and nothing about the score reflects whether it's open.

### 🔴 P0 · Refinement is theatre

| Refinement sent | What happened |
|---|---|
| `"asdf zzz nonsense refinement"` | Returned the same results with note *"Adjusted 30 results based on: 'asdf zzz...'"* — nonsense input is indistinguishable from real input. |
| `"Give me your top 3."` (the "Top 3" pill) | Returned **30** results; note says *"Shows 30 results with score > 0."* |
| `"Nothing involving food."` on a food search | 25 results, still all `cafe / fast_food / internet_cafe` — scores lowered, nothing removed. |
| `"Make it cheaper."` | **Zero** score changes. Silent no-op. |

Worse, the `/api/refine` response drops the `breakdown`, `osm_id` and `osm_tags` fields, so after *any* refinement every card's score-breakdown bars render as 0.00 and the "why it matches" text contradicts the new score (e.g. score **10.0** next to text *"9.0/10 — 90/100 match"*).

### 🔴 P0 · The scoring model punishes users for not using optional fields, and prices are fiction

- **Budget left at default (0)** — the normal case — gives **every** place `budget_score 0.0` and absurd copy like *"~25 GHS — fits your 0.0 budget"* / *"~52 GHS — may stretch your 0.0 budget"*. A full 25% of the score weight silently evaporates; that's why nightlife searches cap around 6.6/10 while the same places score 9+ when a budget is typed.
- **Cost "estimates" are a hardcoded global table, identical in every city:** `cafe 12`, `fast_food 9`, `bar 25`, `museum 12`, `restaurant 38`, `cinema 14`. Verified byte-identical for New York, Paris, Tokyo, London, Lagos, Tamale and Accra. A $9 fast-food meal in Manhattan and a 9 GHS one in Accra are both fiction; the "Budget 25%" factor is comparing made-up numbers to each other.
- **The "✓ Verified" badge contradicts its own definition** ("Data directly from OpenStreetMap") on rows whose cost is `estimated_typical`, hours are `Unknown` and address is `"Location only"`.
- No relevance floor: 4.6/10 matches are presented as results, including literal **"Unnamed bar"** and **"Unnamed fast_food"** entries — recommending places that have no name a user can ask for or search.

### 🔴 P0 · Data & coverage: the app looks empty exactly where early users will try it

- Of 189 sampled results: **68%** missing address, **71%** hours, **76%** website, **75%** phone. Cards are mostly a name + fake price + "Location only".
- **Overpass is capped at 100 places regardless of radius:** a 500 km search around Accra returned the *same nearest 100* as a 5 km search — "I'm willing to travel farther" can never surface farther places.
- **Lagos, Nigeria — a 10M-person metro — returned 9 places in 3 km** (and took 106 s).
- **Geocoding fails at the granularity humans type:** `"Medina Estates, Accra"` and `"Medina, Accra, Ghana"` → HTTP 400, while `"Medina Estates"` alone geocodes to **"Medina Drive, Village Estates II, Highland Village" — Texas, USA.** A Ghanaian user typing their suburb gets either an error or American results.
- No photos, no ratings, no reviews — text-only cards in a category (local discovery) where users expect imagery and social proof.

### 🔴 P0 · Performance & infrastructure are not production-grade

- **Latency: 5–106 seconds per search** (observed: Lagos 106 s, New York 55 s, USD-budget Accra 43 s; typical 7–25 s). Mobile users will abandon long before results.
- **No caching whatsoever** — identical queries re-hit Overpass every time (`x-vercel-cache: MISS`, variable latency).
- **Cold-region backend for a warm-region audience:** functions execute in `iad1` (US East) while the product's own currency list (GHS, NGN, KES, ZAR) targets Africa — every request pays a trans-Atlantic hop before the slow Overpass call even starts.
- **No input validation / no fast-fail:** one unknown interest value (`"hacking"`) hung the request >120 s (client timed out).
- **No auth, no rate limiting** on `/api/*`; a single abusive user exhausts the *public* Overpass/Nominatim quota for everyone. (Production traffic on public Nominatim also violates OSM's usage policy without an identifying User-Agent and heavy caching.)
- **Wasted request in the location flow:** on geolocate, the frontend fires a throwaway `POST /api/recommend` (radius 0.1 km) and *awaits it* before showing the confirmation dialog — a full Overpass round trip whose response is discarded.
- **The confirmation dialog shows raw coordinates** ("5.6227, -0.1711") instead of a place name — the reverse-geocode that would produce "Osu, Accra" is never actually used.

### 🟠 P1 · Frontend bugs (reproduced in headless Chromium)

1. **Search results never scroll into view.** The code calls `gsap.to(window, { scrollTo: ... })` but the GSAP ScrollToPlugin is never loaded → **6 uncaught `TypeError`s per search**, and after a search the results start **719 px below the fold** with the window still at the top. The user sees… nothing, for up to a minute, then has to discover results by scrolling.
2. Map is rendered **after all 30 result cards** (bottom of a very long page); markers overlap into an unreadable pile (no clustering).
3. The search / manual-location / refine inputs have placeholder-only labels (placeholders vanish on typing); app markup has 0 `<h1>` and no landmarks (`main`/`nav`/`footer`), no ARIA on its own elements.
4. OSM-sourced `website` URLs are inserted into `href` with HTML-escaping but **no protocol allow-list** — OSM is community-editable, so a poisoned `javascript:…` tag becomes stored XSS for every user who clicks "Visit Website" (code-level finding; should be fixed alongside other hardening).
5. View-switch GSAP transition updates `currentView` before the fade completes (rapid clicks can double-transition) — code-level finding.
6. Mobile: no horizontal overflow (good), but the whole experience is a single extremely long column; no sticky map or bottom-sheet pattern.

### 🟠 P1 · Market-readiness basics are missing entirely

- **No favicon** (404), no meta description, no Open Graph/Twitter tags, no `robots.txt`, no `sitemap.xml` — unsharable and unindexable.
- **No privacy policy or terms** despite a "🔒 No tracking" badge while using browser geolocation and sending coordinates to third-party APIs (GDPR/CCPA posture).
- No favorites, no search history, no shareable result URLs, no PWA/install, no offline, no i18n, no account.
- One 78 KB HTML file with ~32 KB of inline JS, three unpinned CDN dependencies without SRI hashes, no build/test/CI pipeline.

---

## Part 2 — Additions to make it market-ready

### Phase 0 — Keep the promises you already make (≈1–2 weeks)

1. **Real "open now"**: parse `opening_hours` (e.g. the `opening_hours.js` library); show **Open now / Opens 19:00 / Closed** badges; when the user sets "Available from", exclude closed places and down-rank unknown-hours ones instead of ignoring the field. Wire the existing `#startTime` control and honor `start_time` server-side.
2. **Fix default-budget semantics**: budget 0 = "no constraint" → neutral budget factor (weight redistributed), not 0.0; rewrite the "fits your 0.0 budget" copy.
3. **Make refine honest**: actually remove categories for "no food", actually cap for "Top 3", no-op on unrecognized input with a clear message, and return full result objects so breakdown bars and "why" text stay consistent.
4. **Region-aware cost model**: per-country PPP multipliers or city-tier tables (e.g. cafe ≈ 12 USD in US, ≈ 4 USD in Accra) so the Budget factor compares plausible numbers; mark costs as "typical for this area", not "verified".
5. **Cache everything**: cache geocode results (24 h) and search results (30–60 min) keyed by rounded coords + params; serve stale-while-revalidate; hard 8 s budget with partial results instead of a 100 s hang.
6. **Replace the throwaway geocode call** with a real reverse-geocode (Photon or self-hosted Nominatim with a proper app User-Agent) and show *"Osu, Accra, Ghana"* in the confirm dialog.
7. **Validation & relevance floor**: whitelist interests (400 on anything else), reject nonsense refinements, hide `Unnamed …` places and sub-6.0 scores unless the result set would be empty.

### Phase 1 — Trust & polish (≈2–4 weeks)

8. **Enrichment layer**: photos via Wikidata/Wikimedia/Mapillary where available; phone/website backfill from Wikidata; optional paid data tier (Google Places/Foursquare) as a "Pro" upgrade while OSM stays the free tier.
9. **Map UX**: sticky side-by-side map on desktop, `leaflet.markercluster`, hover-sync between list and map, "Open now" colored markers.
10. **Product loops**: favorites & recents (localStorage), shareable URLs (`?q=…&lat=…&lon=…`), PWA install + offline-cached last results, push-free.
11. **Hygiene pack**: h1/landmarks/labels, meta description + OG tags, favicon, robots/sitemap, English + 2–3 launch languages, privacy policy & terms (keep "no tracking" as a real, documented promise).
12. **Infrastructure**: private or managed Overpass instance, rate limiting + API keys, multi-region or edge deployment closer to target markets (Accra/Lagos/Nairobi users), structured logging and a `/health` endpoint.

### Phase 2 — Moats & monetization (≈1–2 months)

13. **Itinerary builder**: "plan my evening" — chain 2–3 scored stops with travel time and total cost; this is the feature people screenshot and share.
14. **Group mode**: merge 2–3 people's interests/budgets into one ranked list (perfect for the "with others" toggle you already collect).
15. **Local-picks layer**: curated tips from residents per city ("locals say…") — UGC with moderation, the defensibility that raw OSM can't give a copycat.
16. **Monetization**: free = OSM tier; **Pro** = enriched data, itineraries, offline; **B2B** = white-label API for hotels, tourism boards, event sites; optional business-claiming ("this is my venue — update hours/photos").
17. **Growth surfaces**: "tonight in Accra" auto-generated daily pages (great for SEO), OG-rich share cards with map snapshot.

---

## Bottom line

LocalLens has a genuinely good idea — transparent, explainable, privacy-first local discovery — and a polished visual shell. But as shipped, its three headline promises (**time-awareness**, **budget fit**, **refinable results**) are not functionally present, its data layer returns mostly empty cards with fictional prices, and its infrastructure (uncached public OSM calls, 5–106 s latency, US-East execution for an African audience) would collapse under real traffic. The Phase 0 list above is the minimum bar before any marketing spend; Phases 1–2 are what turn it from a demo into a product people keep on their phone.

*Evidence captured during audit: raw API responses (`rec_accra.json`, `city_*.json`, `ref_*.json`), browser screenshots (`shot-*.png`), and test harnesses (`t.py`–`t4.py`, `e2e.js`, `e2e2.js`) are available alongside this report.*
