# LocalLens - "What can I do right now?"

A real local discovery application that finds things to do near you based on your constraints: location, budget, time, interests, company, and preferred distance.

## What it does

LocalLens answers: "I'm in [location], have [budget], [time] free, I'm [alone/with others], and I want [interests]. What can I do?"

It returns real recommendations ranked by a transparent scoring system, with clear indicators of what's verified vs estimated vs AI-generated.

## Real data sources

- **Nominatim (OpenStreetMap)** — geocoding: turn addresses into coordinates
- **Overpass API (OpenStreetMap)** — POI discovery: find real places (restaurants, parks, museums, bars, shops, etc.) with actual names, categories, coordinates, cuisine types, and opening hours

Both are free, no API keys required, and work globally.

## Tech stack

- **Backend**: Python FastAPI
- **Frontend**: Vanilla HTML/CSS/JS (single page, no framework)
- **Deployment**: Docker (hostable on Render, Railway, Fly.io, or any Docker host)
- **License**: Data from OpenStreetMap under ODbL; app code MIT

## Quick start

```bash
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
# Open http://localhost:8000
```

Or with Docker:

```bash
docker build -t locallens .
docker run -p 8000:8000 locallens
```

## Project structure

```
locallens/
├── app.py              # FastAPI backend
├── scoring.py          # Transparent recommendation scoring
├── requirements.txt    # Python dependencies
├── Dockerfile          # Container build
├── static/
│   └── index.html      # Frontend (vanilla JS)
└── README.md           # This file
```

## API endpoints

### `POST /api/recommend`
Main endpoint. Send constraints, get ranked recommendations.

**Request:**
```json
{
  "location": "Accra, Ghana",
  "budget": 100,
  "budget_currency": "GHS",
  "time_available_hours": 3,
  "interests": ["culture", "food"],
  "alone": true,
  "max_distance_km": 5,
  "start_time": "14:00"
}
```

**Response:**
```json
{
  "query": { ... },
  "results": [
    {
      "name": "The Lexiton",
      "category": "Restaurant",
      "cost_estimate": {"amount": 40, "currency": "GHS", "tier": "moderate"},
      "distance_km": 1.2,
      "estimated_time": 1.5,
      "opening_hours": "Unknown",
      "why_it_matches": "8.5/10 — 1.2 km away (within 5 km), moderate cost within GH₵100 budget, open now, matches food interest",
      "confidence": "verified",
      "confidence_details": {
        "name_source": "overpass_osm",
        "location_source": "overpass_osm",
        "cost_source": "estimated_typical",
        "hours_source": "unknown"
      },
      "source": "OpenStreetMap via Overpass API",
      "score": 8.5,
      "breakdown": {
        "distance_score": 2.0,
        "budget_score": 2.0,
        "time_score": 1.5,
        "interest_score": 2.0,
        "companion_score": 1.0
      }
    }
  ],
  "data_sources": ["overpass_osm", "nominatim_osm"],
  "coverage_note": "37 restaurants, 78 bars found within 3km. Results are real OSM data."
}
```

### `POST /api/refine`
Refine previous results conversationally.

**Request:**
```json
{
  "previous_results": [...],
  "refinement": "Make it cheaper.",
  "budget": 50
}
```

### `GET /health`
Health check.

## Scoring system

Each result gets a score out of 10, broken down into transparent components:

| Factor | Weight | How it's scored |
|--------|--------|-----------------|
| **Distance** | 25% | Closer = better. Within 0.5km = full marks, degrades linearly to 0 at max_distance |
| **Budget** | 25% | Estimated cost vs available budget. Well under budget = full marks |
| **Time fit** | 20% | Estimated duration vs available time. Fits comfortably = full marks |
| **Interest match** | 20% | Category + tags vs user interests. Direct match = full marks |
| **Companion fit** | 10% | Solo-friendly / group-friendly vs user's situation |

Confidence is tracked per-field:
- **verified** — came directly from OSM/Overpass data
- **estimated** — derived from available data (e.g., typical cost for category)
- **ai_generated** — suggested by the system when no data available

## Known limitations

- OpenStreetMap coverage varies by region — dense cities have rich data, rural areas may have sparse results
- Cost estimates are typical ranges, not actual prices — always verify before going
- Opening hours from OSM may be outdated or missing
- No real-time data (crowding, live events, temporary closures)
- Language support is English-only in this version

## Future improvements

1. Add more POI categories (nightlife, live music, workshops, classes)
2. Integrate timezone-aware "open now" filtering
3. Add user ratings/reviews from OSM or other sources
4. Multi-language support
5. Mobile-friendly PWA with offline caching
