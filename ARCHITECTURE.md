# Architectural Decisions - Fuel Route Optimizer

A record of the key choices made while building this system, what alternatives were considered, and why we chose what we did.

---

## 1. Routing & Geocoding API - OpenRouteService (ORS)

**Decision:** Use OpenRouteService for both geocoding (city names to coordinates) and driving directions.

**Alternatives considered:**
- Google Maps API - requires credit card, charges per request, expensive at scale
- Mapbox - also paid beyond a small free tier
- OSRM public demo server - free but no geocoding, no SLA, not suitable for production
- HERE Maps - paid

**Why ORS:**
- Fully free tier: 2,000 directions/day, 1,000 geocode searches/day - more than enough
- No credit card required to sign up
- Returns full GeoJSON route geometry with all coordinates - exactly what we need
- Single API handles both geocoding and routing - fewer integrations to maintain
- Well-documented, reliable uptime

---

## 2. Station Geocoding Service - Why ORS Over Census and Nominatim

This was the most debated decision in the project. Three options were seriously evaluated:

**Option A - US Census Batch Geocoder**
- The US government's free batch geocoding API. No API key needed, no quota limits.
- Sends up to 9,999 addresses in a single HTTP POST - so all 6,738 stations in just 2 requests.
- Completes in ~30 seconds total.
- **Why we rejected it:** The Census geocoder is designed for postal street addresses like `123 Main Street, Springfield, IL`. Our addresses are highway exit descriptions like `I-44, EXIT 283 & US-69` and `I-75, EXIT 144-B`. These are not postal addresses. The Census struggled to match them - ~50% match rate at best, with many stations falling back to city centroids. We implemented it, tested it, and saw too many inaccurate results.

**Option B - Nominatim (OpenStreetMap's public geocoder)**
- Uses the exact same underlying OpenStreetMap data as ORS.
- No daily quota, no API key needed.
- Much better accuracy for highway exit descriptions than Census.
- **Why we rejected it:** Nominatim's Terms of Service explicitly prohibit "bulk geocoding of large amounts of data" and "systematic queries - searching for complete lists of facilities." Geocoding 6,738 truck stops is exactly that. Using it would get us banned. Off the table entirely.

**Option C - ORS Geocoding (chosen)**
- ORS uses Pelias - a free-text search engine that understands location concepts, not just postal formats.
- Understands `I-75 EXIT 144 Bridgeport MI` as a location, not just a street address. Much better suited for highway exit data.
- Trade-off: 1,000 calls/day free quota.
- **How we handled the quota:** Split into a separate `geocode_stations` command that runs up to 900 stations per API key per day. User rotates API keys manually until all stations are geocoded. The daily scheduler then keeps it current going forward.

| | ORS | Census | Nominatim |
|--|-----|--------|-----------|
| Accuracy for highway exits | Good | Poor | Good |
| Daily quota | 1,000 | Unlimited | Unlimited |
| API key needed | Yes | No | No |
| TOS compliant for bulk use | Yes | Yes | No - banned |
| First run time | ~900/day per key | ~30s | Banned |

**Verdict:** ORS is the only option that is both accurate for our data type and TOS-compliant for bulk geocoding.

---

## 3. Station Data Pipeline - Two-Phase Approach

**Decision:** Split data loading into two separate steps: `load_stations` (CSV import, no API) and `geocode_stations` (ORS calls, incremental).

**Why not geocode during CSV import:**
- The CSV has 8,151 rows. Geocoding all of them in one shot would need ~6,738 ORS API calls (after deduplication), far exceeding the 1,000/day free quota.
- If the process crashed mid-way, all progress would be lost.
- Mixing import logic with API calls makes the command slow, fragile, and hard to retry.

**Why two phases:**
- `load_stations` completes in seconds - pure CSV parsing and bulk DB insert, zero API calls.
- `geocode_stations` runs incrementally - up to 900 stations per run, picking up where it left off. Safe to interrupt and resume.
- Progress is flushed to the DB every 50 geocoded stations, so a crash never loses more than 50 records.

---

## 4. Geocoding Strategy - Full Address First, City Fallback

**Decision:** For each station, first try the full highway address (e.g. `I-75, EXIT 144-B, Bridgeport, MI, USA`). If that fails, fall back to `city, state, USA`.

**Why this matters:**
- Many stations in the CSV share the same city but are at different highway exits miles apart.
- City-level geocoding would place multiple stations at the same coordinates (city center), making the optimizer think they are at the same location.
- Full address geocoding gives each station a precise location - critical for accurate mile-marker calculation and correct fuel stop placement on the map.

**Cost:** Up to 2 ORS calls per station instead of 1. Accepted because accuracy outweighs cost for a one-time seeding operation.

---

## 5. Rate Limiting - Sliding Window

**Decision:** Implement a sliding window rate limiter (80 calls per 60-second window, shared across all threads) for the geocoding pipeline.

**Why not a fixed interval (e.g. sleep 0.75s between calls):**
- Fixed intervals are wasteful - they sleep even when the API is not under load.
- A sliding window allows bursting up to 80 calls immediately, then throttling only when the window fills up. Faster in practice.

**Why 80 calls per 60 seconds (not 100):**
- ORS hard limit is 100 calls/minute. We use 80 to stay safely below, with headroom for network jitter and retries.

**Implementation:**
- A shared list `_call_times` tracks timestamps of recent calls.
- Protected by a `threading.Lock` so all 10 parallel geocoding threads share one rate limiter.

---

## 6. Spatial Filtering - Shapely + Bounding Box Pre-filter

**Decision:** Use Shapely for route buffering and station proximity, with a DB bounding box pre-filter to avoid loading all stations into memory.

**Alternatives considered:**
- PostGIS - powerful but requires PostgreSQL, overkill for this project
- GeoDjango - adds complexity, requires GDAL/GEOS system libraries
- Pure Haversine on all stations - would iterate all 6,738 stations on every request

**Why Shapely:**
- Pure Python, no system dependencies beyond pip
- Accurate nearest-point projection onto the route LineString
- `route.project(point)` gives exact mile-marker position along the route

**Bounding box pre-filter:**
- Before Shapely calculations, the DB query filters stations within the lat/lon bounding box of the route (with buffer margin).
- This reduces the candidate set from ~6,738 to typically 50-200 stations before any Shapely geometry is computed.
- Avoids loading the full table into memory on every request.

---

## 7. Optimization Algorithm - Greedy

**Decision:** Use a greedy algorithm for fuel stop selection.

**The problem:** Given stations sorted by mile-marker along a route, decide where to stop and how many gallons to buy to minimize total cost, subject to a 500-mile tank limit.

**Why greedy (not dynamic programming or brute force):**
- The greedy approach is provably optimal for this specific problem structure.
- At each position, ask: is the current station the cheapest within one full tank from here?
  - YES - fill up completely (maximize cheap fuel).
  - NO - buy just enough to reach the next cheaper station ahead.
- Runs in O(n) time - sub-millisecond even for hundreds of stations.
- Brute force would be exponential. DP would be O(n^2). Neither is necessary.

---

## 8. Map Rendering - Leaflet.js + CartoDB Tiles

**Decision:** Use Leaflet.js with CartoDB tiles for the interactive map in `/api/route/view/`, and `staticmap` for the base64 PNG embedded in the JSON API response.

**Why two map formats:**
- The JSON API response (`/api/route/`) includes a `map_image_base64` PNG - useful for Postman demos and programmatic consumers.
- The HTML viewer (`/api/route/view/`) uses an interactive Leaflet map - users can zoom, pan, and click fuel stop markers to see popups with price, gallons, and cost details.

**Why Leaflet.js:**
- The most popular open-source JavaScript mapping library
- Lightweight, zero dependencies beyond the library itself
- Renders the route GeoJSON we already have with no extra API calls
- Interactive markers with popups - far more useful for a demo than a static image

**Why CartoDB tiles (not OSM tiles directly):**
- Initially used OSM tile servers (`tile.openstreetmap.org`) but they returned 403 errors on localhost
- OSM's volunteer-run tile servers require a proper `Referer` header from a real domain - they block localhost
- CartoDB provides free map tiles (backed by the same OSM data) via their own CDN with no Referer restriction and no API key needed
- No quota limits, works on localhost and production equally

**Why not Mapbox or Google Maps:**
- Both require a paid API key beyond their free tier
- CartoDB is completely free with no account needed

**Zero extra API calls:** Leaflet renders everything client-side. The route coordinates and stop locations come from the existing pipeline result - no additional requests to ORS or any other service.

---

## 9. Caching - File-based with Signal Invalidation

**Decision:** Cache route computation results to disk indefinitely, with automatic invalidation on any FuelStation DB change.

**Why file-based (not in-memory):**
- In-memory cache (`LocMemCache`) is cleared on every server restart - useless for a development server.
- File-based cache (`FileBasedCache`) persists across restarts.
- For production, Redis or Memcached would be used instead.

**Why no TTL (timeout=None):**
- Route results only change when station data changes. A time-based expiry would either expire too soon (wasting computation) or too late (serving stale prices).
- Signal-based invalidation is precise: the cache clears exactly when data changes, not on a timer.

**What triggers invalidation:**
- `post_save` or `post_delete` signal on any FuelStation (covers Django admin edits)
- Manual `cache.clear()` call at the end of every geocoding run (covers bulk updates that bypass signals)

**Cache key:** `route:{start}:{finish}` (normalized to lowercase). Same route requested twice - zero ORS API calls on the second request.

---

## 10. Package Manager - uv

**Decision:** Use `uv` instead of `pip` or `poetry`.

**Why:**
- Significantly faster dependency resolution and installation than pip
- Single tool for both venv creation and package management
- `pyproject.toml` is the modern standard (PEP 517/518)
- Lockfile (`uv.lock`) ensures reproducible environments

---

## 11. Database - SQLite

**Decision:** Use SQLite for this project.

**Why not PostgreSQL:**
- No infrastructure to set up - SQLite is a single file
- Sufficient for a demo/assessment - all queries are simple indexed lookups
- The spatial filtering is done in Python (Shapely), not in the DB, so PostGIS is not needed
- Easy to share: the DB file can be committed or sent directly

**When to switch to PostgreSQL:**
- Multi-process or multi-server deployment (SQLite has write concurrency limits)
- If PostGIS spatial queries become necessary for performance at larger scale

---

## 12. Background Scheduling - django-apscheduler

**Decision:** Use `django-apscheduler` to run the daily geocoding job at 2:00 AM automatically.

**Alternatives considered:**
- Celery + Redis/RabbitMQ - powerful but heavy, requires separate broker infrastructure
- Cron + management command - works but requires OS-level setup outside the Django project
- Manual management command only - requires human intervention every day

**Why django-apscheduler:**
- Runs inside the Django process - no separate worker or broker needed
- Job execution history is stored in the Django DB and visible in admin
- Simple to configure - a few lines in `apps.py`
- Sufficient for a single daily task that doesn't need distributed execution
