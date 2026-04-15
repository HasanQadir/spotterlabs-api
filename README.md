# Fuel Route Optimizer API

A Django REST API that takes a start and finish location within the USA and returns the **cheapest possible fuel stops** along the driving route, along with a map and total fuel cost.

## How it works

1. **Geocodes** start + finish via OpenRouteService (ORS) — 2 API calls
2. **Fetches the driving route** from ORS — 1 API call
3. **Finds fuel stations near the route** using a spatial buffer — pure local DB query, zero extra API calls
4. **Runs a greedy cost-optimisation algorithm** to select the cheapest sequence of stops given a 500-mile tank and 10 MPG efficiency
5. **Returns** JSON with the optimised stop list, total cost, GeoJSON route, and a base64 map image

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
Parses the CSV and inserts all stations into the local SQLite database. No API calls — completes in seconds.

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

### `GET /api/route/` — JSON response

| Parameter | Required | Description |
|-----------|----------|-------------|
| `start`   | Yes | Starting location (e.g. `Dallas, TX`) |
| `finish`  | Yes | Destination (e.g. `Indianapolis, IN`) |

#### Example
```
GET http://localhost:8000/api/route/?start=Dallas, TX&finish=Indianapolis, IN
```

#### Response
```json
{
  "start": "Dallas, TX",
  "finish": "Indianapolis, IN",
  "total_distance_miles": 902.5,
  "total_fuel_cost_usd": 122.14,
  "fuel_stops": [
    {
      "name": "EXXON - Pilot #1293",
      "address": "I-30, EXIT 61",
      "city": "Garland",
      "state": "TX",
      "price_per_gallon": 2.842,
      "gallons_purchased": 1.44,
      "stop_cost_usd": 4.10,
      "mile_marker": 14.4,
      "coordinates": { "latitude": 32.907, "longitude": -96.640 }
    }
  ],
  "route": {
    "geojson": { "type": "FeatureCollection", "features": [...] },
    "map_image_base64": "iVBORw0KGgo..."
  }
}
```

---

### `GET /api/route/view/` — Browser-friendly HTML page

Opens a visual page with the route map and a fuel stop table. Each stop has a clickable Google Maps link for its coordinates.

```
GET http://localhost:8000/api/route/view/?start=Dallas, TX&finish=Indianapolis, IN
```

---

### `GET /api/route/map/` — Raw PNG map image

Returns the route map as a PNG image directly viewable in a browser.

```
GET http://localhost:8000/api/route/map/?start=Dallas, TX&finish=Indianapolis, IN
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
