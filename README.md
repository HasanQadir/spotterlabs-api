# Fuel Route Optimizer API

A Django REST API that takes a start and finish location within the USA and returns the **cheapest possible fuel stops** along the driving route, along with a map and total fuel cost.

## How it works

1. **Geocodes** start + finish via OpenRouteService (ORS) - 2 API calls
2. **Fetches the driving route** from ORS - 1 API call
3. **Finds fuel stations near the route** using a spatial buffer - pure local DB query, zero extra API calls
4. **Runs a greedy cost-optimisation algorithm** to select the cheapest sequence of stops given a 500-mile tank and 10 MPG efficiency
5. **Returns** an interactive HTML map with the optimised stop list, clickable fuel stop markers, and total fuel cost

**Total ORS API calls per request: 3 (geocode × 2 + routing × 1)**
**Repeat requests for the same route: 0 API calls (served from cache)**

---

## Setup

### 1. Prerequisites
- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (`brew install uv`)
- A free [OpenRouteService API key](https://openrouteservice.org/dev/#/signup)

### 2. Install dependencies
```bash
cd fuel_route
uv sync
```

### 3. Configure environment
```bash
cp .env.example .env
# Edit .env and add your ORS_API_KEY
```

### 4. Apply migrations
```bash
uv run python manage.py migrate
```

### 5. Load fuel station data
```bash
uv run python manage.py load_stations --csv /path/to/fuel-prices-for-be-assessment.csv
```
Parses the CSV and inserts all stations into the local SQLite database. No API calls - completes in seconds.

### 6. Geocode stations (fills in lat/lon coordinates)
```bash
uv run python manage.py geocode_stations
```
Calls ORS to geocode up to 900 stations per run (safely under the 1,000/day free quota). Re-run with a fresh `ORS_API_KEY` in `.env` to continue. The daily scheduler also runs this automatically at 2:00 AM.

### 7. Start the server
```bash
uv run python manage.py runserver
```

---

## API Endpoints

### `GET /api/route/view/`

| Parameter | Required | Description |
|-----------|----------|-------------|
| `start`   | Yes | Starting location (e.g. `Dallas, TX`) |
| `finish`  | Yes | Destination (e.g. `Indianapolis, IN`) |

Returns a browser-friendly HTML page with:
- Route summary (distance, total cost, number of stops)
- Interactive Leaflet.js map (CartoDB/OSM tiles) with the route line and clickable fuel stop markers
- Table of fuel stops with station name, location, price, gallons, cost, mile marker, and coordinates

#### Example
```
GET http://localhost:8000/api/route/view/?start=Dallas, TX&finish=Indianapolis, IN
```

---

## Caching

Route results are cached indefinitely after the first request. The cache is automatically cleared when:
- A station is added, updated, or deleted via Django admin
- A geocoding run completes (command or daily scheduler)

This means repeat requests for the same route make **zero ORS API calls**.

---

## Django Admin

```
http://localhost:8000/admin/
```

View and manage all fuel stations, filter by state, search by name or city, and inspect APScheduler job history.

---

## Vehicle assumptions

| Parameter | Value |
|-----------|-------|
| Tank range | 500 miles |
| Fuel efficiency | 10 MPG |
| Route buffer | 15 miles off-route |
| Start fuel | Full tank |
