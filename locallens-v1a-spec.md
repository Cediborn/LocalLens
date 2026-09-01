# LocalLens V1-A — Foundation & Core UX Specification

## Overview

LocalLens is a local discovery engine. Users tell it what they want to find, and it discovers relevant places, activities, businesses, and experiences around them. V1-A focuses on building the core foundation: landing page, natural language search, location awareness, responsive UI, and a clean component architecture that supports V1-B through V1-E.

## Current State Analysis

**What exists today:**
- FastAPI backend with geocoding (Nominatim) and POI search (Overpass API)
- Scoring engine (distance, budget, time, interests, companion) in `scoring.py`
- Single-page vanilla HTML/CSS/JS frontend in `static/index.html`
- Dark theme with "brutalist" design (offset shadows, glow buttons)
- Search form with structured fields: location, budget, currency, time, distance, interests checkboxes, solo/group toggle
- Leaflet map integration with markers and popups
- Results display with score rings, breakdown bars, and refinement input
- Backend `/api/recommend` and `/api/refine` endpoints

**What V1-A changes:**
- Replace structured form with natural language search bar as primary input
- Add standalone landing page
- Implement browser geolocation with permission handling and manual fallback
- Add GSAP animations for page transitions and result reveals
- Keep all existing backend APIs and scoring logic untouched
- Maintain single-file frontend architecture (organized for future splitting)

---

## V1-A Objectives

1. ✅ Landing page (standalone)
2. ✅ Main search/discovery interface (NLP bar + collapsible advanced)
3. ✅ Location awareness (browser geolocation + manual fallback)
4. ✅ Search input (natural language as primary interface)
5. ✅ Search intent/category handling (client-side keyword extraction)
6. ✅ Loading state (existing + GSAP enhancements)
7. ✅ Results page structure (existing, refined)
8. ✅ Responsive mobile/desktop layouts
9. ✅ Navigation between major states (landing → search → results)
10. ✅ Clean component architecture (single-file, logically organized)

---

## Core User Flow

```
Landing Page (standalone)
  → User clicks "Get Started" / CTA
    → Geolocation prompt (auto-prompt on load)
      → Location confirmed or manual fallback
        → Search page with NLP bar
          → User types natural language query
            → Loading state
              → Results state with map
```

The interface must make this flow extremely obvious. No dead ends, no confusion about what to do next.

---

## Routing & View Management

**Approach:** Single-page JavaScript views (no server-side routing changes)

The app remains a single `static/index.html` file with JavaScript-based view switching. Views are managed by showing/hiding DOM sections and using GSAP for transitions.

**Views:**
1. `#view-landing` — Standalone landing page
2. `#view-search` — Search interface (NLP bar + advanced options + results)
3. `#view-loading` — Loading state (shared overlay)

**View transitions:**
- Landing → Search: GSAP fade/slide transition (300-400ms)
- Search → Loading: GSAP overlay fade-in
- Loading → Results: GSAP staggered card entrance
- Results → New search: In-place update with fade transition

**URL:** No hash-based routing in V1-A. The app always starts on the landing page. Future phases may add URL-based state.

---

## Landing Page

**Type:** Standalone full-screen landing page

**Layout:**
- Full viewport height (100vh)
- Centered content with strong typography
- Tagline: "What can I do right now?"
- Brief description of LocalLens
- Single prominent CTA button: "Get Started" or "Discover Places"
- Optional: subtle background animation or gradient

**Content:**
- Logo: "LocalLens" with accent color on "Lens"
- Tagline: "Real local discovery powered by OpenStreetMap"
- Description: 2-3 sentences explaining the product
- CTA: "Find Things Near Me" → transitions to search view

**Design:**
- Dark theme consistent with current design direction
- Strong typography (large heading, readable body)
- Minimal visual clutter
- GSAP entrance animation on load (fade-in + subtle scale)

---

## Location Awareness

### Geolocation Flow

1. **On page load:** Immediately request browser geolocation permission
2. **While waiting:** Show a subtle loading indicator (not blocking the UI)
3. **Permission granted:** Reverse-geocode coordinates to get a display name (e.g., "Brooklyn, New York")
4. **Show confirmation:** "We detected you're in [Location]. Use this location?" with:
   - "Use this location" button (primary)
   - "Change location" link (opens manual input)
5. **Permission denied or unavailable:** Show inline manual input for location

### Location States

| State | UI Behavior |
|-------|-------------|
| Geolocation loading | Small spinner near header, non-blocking |
| Location confirmed | Location chip shown near search bar: "📍 Brooklyn, NY" (clickable to change) |
| Location denied/unavailable | Inline text input: "Enter your location" |
| Location error | Inline error + manual input fallback |
| Manual override | User types location → geocoded via backend `/api/recommend` |

### Location Display

- Shown as a clickable chip/badge **near the search bar** (not in header)
- Format: "📍 [City/District], [Country]" (short, readable)
- Click to change → opens inline input or dropdown
- Always visible when on search page

### Backend Integration

- Browser geolocation coordinates are sent to backend as `lat`/`lon` (if available)
- Manual location text is sent as `location` string (geocoded by backend via Nominatim)
- The backend already supports both modes; no backend changes needed

---

## Search Input

### Primary: Natural Language Bar

**Design:**
- Large, prominent input field (similar to Google's search bar)
- Placeholder text: "What are you looking for?" or "Things to do tonight..."
- Full-width within its container
- Brutalist offset shadow styling (consistent with existing design)
- Submit button: "Find Things" with glow effect

**Supported Queries:**
- "Things to do tonight"
- "Best restaurants near me"
- "Cheap date ideas"
- "Places to study"
- "Fun things to do this weekend"
- "Coffee shops near me"
- "Free outdoor activities"
- "Where can I get food after 10pm?"

### Client-Side NLP Parsing

**Approach:** Keyword extraction and intent classification (no external API calls)

**Parsing logic:**

1. **Category extraction:** Map keywords to OSM categories
   - "restaurant" / "food" / "eat" / "dinner" / "lunch" → food categories
   - "bar" / "drinks" / "pub" / "nightlife" → drink/nightlife categories
   - "museum" / "culture" / "art" / "gallery" → culture categories
   - "park" / "outdoor" / "nature" / "hike" → outdoor categories
   - "shop" / "buy" / "market" → shopping categories
   - etc. (leverage existing `INTEREST_KEYWORDS` and `INTEREST_CATEGORY_MAP` from `scoring.py`)

2. **Time-of-day inference:**
   - "tonight" / "evening" / "after 8pm" → set time hint to evening
   - "morning" / "early" / "breakfast" → set time hint to morning
   - "weekend" / "saturday" / "sunday" → flag for weekend-appropriate results
   - "now" / "right now" → use current time

3. **Budget hints:**
   - "cheap" / "free" / "budget" / "affordable" → low budget hint
   - "fancy" / "upscale" / "expensive" → high budget hint
   - "$20" / "under 50" → extract numeric budget

4. **Distance hints:**
   - "near me" / "nearby" / "close" → short radius (1-2 km)
   - "far" / "worth the trip" → longer radius (5-10 km)

5. **Companion hints:**
   - "date" / "romantic" / "couple" → solo/romantic context
   - "kids" / "family" / "children" → family context
   - "friends" / "group" / "party" → group context

6. **Vague queries:** If no clear intent detected, default to broad search (all categories) and show suggestion chips at the top of results

### Advanced Options (Collapsible)

**Trigger:** "More options" link/button below the search bar

**Fields (all current fields preserved):**
- Budget (number input)
- Currency (select dropdown)
- Time available (number input, hours)
- Max distance (number input, km)
- Interests (checkbox group — 15 options)
- Going alone? (radio: solo / with others)
- Available from (time input, optional)

**Behavior:**
- Collapsed by default (only the NLP bar is visible)
- Smooth GSAP expand/collapse animation
- Values are remembered during the session
- Advanced options are sent to the backend alongside the NLP query

### Query → Backend Mapping

The NLP parser produces a structured object that maps to the existing `/api/recommend` body:

```javascript
{
  location: "Brooklyn, NY",           // from geolocation or manual input
  budget: 20,                          // from NLP hint or advanced input
  budget_currency: "USD",              // from advanced input
  time_available_hours: 3,             // from advanced input
  interests: ["food", "nightlife"],    // from NLP extraction or advanced checkboxes
  alone: true,                         // from NLP hint or advanced radio
  max_distance_km: 2,                  // from NLP hint or advanced input
  start_time: null                     // from NLP time-of-day or advanced input
}
```

---

## Loading State

**Existing:** Animated dots loader with "Searching OpenStreetMap..." text

**Enhancements for V1-A:**
- GSAP entrance animation (fade-in + scale)
- Show estimated search time: "Usually takes 2-3 seconds..."
- Progress text updates: "Finding places nearby..." → "Scoring results..." → "Almost there..."
- If search takes >5 seconds, show a helpful tip: "OpenStreetMap coverage can be sparse in some areas"

---

## Results Page Structure

**Keep existing structure, refine:**

1. **Results count** — "X places found near [Location]"
2. **Suggestion chips** — Show 2-3 refinement suggestions at top (for vague queries)
3. **Result cards** — Existing card design with score ring, details, breakdown bars
4. **Map** — Existing Leaflet map with markers (toggle show/hide)
5. **Refinement bar** — Existing refinement input + quick pills
6. **Coverage note** — Existing coverage info

**GSAP enhancements:**
- Staggered card entrance animation (cards appear one by one, 50ms delay)
- Score ring animation (count up from 0 to final score)
- Smooth scroll to first result on load

---

## Responsive Layout

### Mobile (< 640px)
- Full-width layout, single column
- Search bar: full-width, larger touch target
- Advanced options: full-width stacked fields
- Result cards: full-width, stacked layout
- Map: collapsible, 300px height
- Landing page: vertically centered, padding for small screens

### Tablet (640px - 1024px)
- Centered container (max-width: 900px)
- Search bar: full width within container
- Result cards: full width within container
- Map: 400px height

### Desktop (> 1024px)
- Centered container (max-width: 900px)
- Same as tablet but with more breathing room
- Optional: side-by-side results + map on very wide screens (not required for V1-A)

---

## GSAP Animations

**Library:** GSAP (loaded via CDN: `https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js`)

**Animations to implement:**

1. **Landing page entrance:** Fade-in + subtle translateY (10px → 0) on page load
2. **View transition (landing → search):** Landing fades out, search fades in (300ms)
3. **Search bar focus:** Subtle scale pulse (1.0 → 1.02 → 1.0) on focus
4. **Loading overlay:** Fade-in with backdrop blur
5. **Results entrance:** Staggered card reveal (opacity 0 → 1, translateY 20px → 0, 50ms stagger)
6. **Score ring animation:** Count-up from 0 to final score (600ms ease-out)
7. **Refinement results:** Fade transition for result list update

**Performance:**
- Use `gsap.to()` and `gsap.fromTo()` for hardware-accelerated transforms
- Avoid animating layout properties (width, height, margin)
- Use `will-change: transform` on animated elements
- Graceful degradation: if GSAP fails to load, app still works without animations

---

## Component Organization (Single-File)

The single `index.html` file should be organized with clear sections:

```
<!-- HTML Structure -->
<!-- 1. Landing View (#view-landing) -->
<!-- 2. Search View (#view-search) -->
<!--    2a. Location chip -->
<!--    2b. NLP search bar -->
<!--    2c. Advanced options (collapsible) -->
<!--    2d. Results area -->
<!--    2e. Map container -->
<!--    2f. Refinement section -->
<!-- 3. Loading overlay (#view-loading) -->
<!-- 4. Error display -->

<!-- CSS -->
<!-- Organized by section: landing, search, results, map, loading, responsive -->

<!-- JavaScript -->
<!-- Organized by module (logical sections with clear comments): -->
<!-- // ── Config & State ── -->
<!-- // ── Geolocation Module ── -->
<!-- // ── NLP Parser Module ── -->
<!-- // ── View Manager Module ── -->
<!-- // ── Search Module ── -->
<!-- // ── Results Renderer Module ── -->
<!-- // ── Map Module ── -->
<!-- // ── Refinement Module ── -->
<!-- // ── Animation Module ── -->
<!-- // ── Init & Event Listeners ── -->
```

Each module section should have a clear comment header and be self-contained enough to extract into a separate file later.

---

## Edge Cases & Error Handling

| Scenario | Behavior |
|----------|----------|
| Browser geolocation denied | Show manual location input inline |
| Browser geolocation timeout | Show manual location input with message |
| Geocoding fails (backend) | Show error + suggest checking spelling |
| No results found | Show empty state with suggestions (broaden search, try nearby city) |
| Overpass API down | Show error with retry button |
| Vague/ambiguous NLP query | Default to broad search + show suggestion chips |
| Very long NLP query | Truncate display, parse full text |
| Special characters in query | Sanitize and escape, no errors |
| Network failure | Show offline message, suggest checking connection |
| GSAP fails to load | App works without animations (graceful degradation) |

---

## Backend Changes Required

**None.** V1-A is frontend-only. The existing backend APIs (`/api/recommend`, `/api/refine`) support all the data the new frontend needs. The NLP parsing happens entirely on the client side and maps to the existing structured API body.

---

## Technical Constraints

- **No new backend dependencies**
- **No new Python packages**
- **Single-file frontend** (organized for future splitting)
- **GSAP via CDN** (no build step)
- **Leaflet via CDN** (already used)
- **Must not break existing `/api/recommend` or `/api/refine` endpoints**
- **Must work in modern browsers** (Chrome, Firefox, Safari, Edge — last 2 versions)
- **Must be responsive** (mobile-first)
- **Must be accessible** (keyboard navigation, ARIA labels, focus management)

---

## Acceptance Criteria

- [ ] Landing page loads and displays with GSAP entrance animation
- [ ] Clicking CTA transitions to search view with animation
- [ ] Browser geolocation is requested on first load
- [ ] Geolocation result is shown with confirm/edit options
- [ ] Manual location input works as fallback
- [ ] NLP search bar accepts natural language queries
- [ ] Client-side parser extracts categories, time, budget, distance from queries
- [ ] Vague queries default to broad search with suggestion chips
- [ ] Advanced options expand/collapse with animation
- [ ] All advanced fields (budget, currency, time, distance, interests, solo/group, start time) work
- [ ] Search triggers loading state with progress text
- [ ] Results display with staggered GSAP animation
- [ ] Score rings animate count-up
- [ ] Map shows markers for all results
- [ ] Refinement bar and quick pills work
- [ ] Layout is responsive on mobile, tablet, and desktop
- [ ] Error states are handled gracefully
- [ ] App degrades gracefully if GSAP fails to load
- [ ] No existing functionality is broken
- [ ] Application runs correctly end-to-end

---

## Future Phases (Not V1-A)

- V1-B: Enhanced results (photos, reviews, richer data)
- V1-C: Social features (sharing, collections)
- V1-D: Personalization (saved preferences, history)
- V1-E: Advanced discovery (AI-powered recommendations, conversational refinement)

The V1-A foundation must support all of these without major rewrites.
