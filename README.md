# Fuel Route Optimizer API

A Django REST API that takes a start and finish location within the USA and returns the **cheapest possible fuel stops** along the driving route, along with a map of the route and total fuel cost.

## How it works

1. **Geocodes** start + finish via OpenRouteService (ORS)
2. **Fetches the driving route** from ORS (single call, full GeoJSON geometry)
3. **Finds fuel stations near the route** using a spatial buffer — all from a pre-seeded local SQLite DB, zero extra API calls
4. **Runs a greedy cost-optimisation algorithm** to select the cheapest sequence of stops given a 500-mile tank and 10 MPG efficiency
5. **Returns** a JSON response with the GeoJSON route, each stop's details, and the total fuel cost

**Total external API calls at runtime: 2–3 (geocode × 2 + routing × 1)**

---

## Setup

### 1. Prerequisites
- Python 3.11+
- [uv](https://docs.astral.sh/uv/) installed (`pip install uv` or `brew install uv`)
- A free [OpenRouteService API key](https://openrouteservice.org/dev/#/signup)

### 2. Install dependencies
```bash
cd fuel_route
uv sync
```

### 3. Configure environment
```bash
cp .env.example .env
# Edit .env and set your ORS_API_KEY
```

### 4. Apply migrations
```bash
uv run python manage.py migrate
```

### 5. Load fuel station data (one-time)
```bash
uv run python manage.py load_stations --csv /path/to/fuel-prices-for-be-assessment.csv
```
This geocodes every unique city/state in the CSV (one ORS call each) and stores results in the local SQLite database. Takes a few minutes to run once.

### 6. Start the server
```bash
uv run python manage.py runserver
```

---

## API Usage

### `GET /api/route/`

| Parameter | Required | Description |
|-----------|----------|-------------|
| `start`   | Yes | Starting location (e.g. `New York, NY`) |
| `finish`  | Yes | Destination (e.g. `Los Angeles, CA`) |

#### Example request (Postman / curl)
```
GET http://localhost:8000/api/route/?start=Chicago, IL&finish=Dallas, TX
```

#### Response
```json
{
  "start": "Chicago, IL",
  "finish": "Dallas, TX",
  "total_distance_miles": 924.3,
  "total_fuel_cost_usd": 278.54,
  "fuel_stops": [
    {
      "name": "PILOT TRAVEL CENTER #412",
      "address": "I-44, EXIT 80",
      "city": "Joplin",
      "state": "MO",
      "price_per_gallon": 2.919,
      "gallons_purchased": 38.5,
      "stop_cost_usd": 112.38,
      "mile_marker": 512.4,
      "coordinates": { "latitude": 37.08, "longitude": -94.47 }
    }
  ],
  "route": {
    "geojson": { "type": "FeatureCollection", "features": [...] },
    "map_image_base64": "iVBORw0KGgo..."
  }
}
```

To view the map image, paste the `map_image_base64` value into your browser:
```
data:image/png;base64,<paste_value_here>
```

To view the route on a map, paste the `geojson` object into [geojson.io](https://geojson.io).

---

## Vehicle assumptions
| Parameter | Value |
|-----------|-------|
| Tank range | 500 miles |
| Fuel efficiency | 10 MPG |
| Start fuel | Full tank |
