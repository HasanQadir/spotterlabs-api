"""
Single API endpoint:

    GET /api/route/?start=<location>&finish=<location>

Example:
    GET /api/route/?start=Chicago, IL&finish=Dallas, TX

Response (JSON):
    {
        "total_distance_miles": 920.5,
        "total_fuel_cost": 287.45,
        "fuel_stops": [ ... ],
        "route": {
            "geojson": { ... },   // GeoJSON FeatureCollection — paste into geojson.io
            "map_image_base64": "..." // PNG rendered with OSM tiles
        }
    }
"""

import base64
import io

from django.conf import settings
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import FuelStation
from .services import routing as routing_svc
from .services import spatial as spatial_svc
from .services.optimizer import optimise
from .services.map_image import render_map


class RouteView(APIView):
    """
    GET  /api/route/?start=...&finish=...
    """

    def get(self, request: Request) -> Response:
        start = request.query_params.get("start", "").strip()
        finish = request.query_params.get("finish", "").strip()

        if not start or not finish:
            return Response(
                {"error": "Both 'start' and 'finish' query parameters are required."},
                status=400,
            )

        # ── 1. Geocode start & finish (2 ORS calls) ──────────────────────
        try:
            start_coords = routing_svc.geocode(start)
            finish_coords = routing_svc.geocode(finish)
        except Exception as exc:
            return Response({"error": f"Geocoding failed: {exc}"}, status=400)

        # ── 2. Fetch driving route (1 ORS call) ──────────────────────────
        try:
            route_data = routing_svc.get_route(start_coords, finish_coords)
        except Exception as exc:
            return Response({"error": f"Routing failed: {exc}"}, status=502)

        feature = route_data["features"][0]
        coords = feature["geometry"]["coordinates"]  # [[lon, lat], ...]
        total_distance_miles = spatial_svc.route_length_miles(coords)

        # ── 3. Find stations near the route (local, no API call) ─────────
        route_line = spatial_svc.build_linestring(coords)
        all_stations = FuelStation.objects.all()
        stations_with_markers = spatial_svc.find_stations_near_route(
            route_line,
            all_stations,
            buffer_miles=settings.ROUTE_BUFFER_MILES,
        )

        # ── 4. Optimise fuel stops (local, no API call) ───────────────────
        try:
            stops, total_fuel_cost = optimise(
                stations_with_markers,
                total_distance_miles,
                tank_range=settings.TANK_RANGE_MILES,
                mpg=settings.MPG,
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=422)

        # ── 5. Build map image (local, OSM tiles) ────────────────────────
        map_b64 = render_map(coords, stops)

        # ── 6. Serialise response ─────────────────────────────────────────
        stops_payload = [
            {
                "name": s.station.name,
                "address": s.station.address,
                "city": s.station.city,
                "state": s.station.state,
                "price_per_gallon": float(s.station.retail_price),
                "gallons_purchased": s.gallons_purchased,
                "stop_cost_usd": s.stop_cost,
                "mile_marker": s.mile_marker,
                "coordinates": {
                    "latitude": s.station.latitude,
                    "longitude": s.station.longitude,
                },
            }
            for s in stops
        ]

        return Response(
            {
                "start": start,
                "finish": finish,
                "total_distance_miles": round(total_distance_miles, 1),
                "total_fuel_cost_usd": total_fuel_cost,
                "fuel_stops": stops_payload,
                "route": {
                    "geojson": _build_geojson(coords, stops),
                    "map_image_base64": map_b64,
                },
            }
        )


# ── GeoJSON builder ──────────────────────────────────────────────────────────

def _build_geojson(coords: list, stops) -> dict:
    """
    Return a GeoJSON FeatureCollection with:
      - one LineString for the route
      - one Point per fuel stop
    Paste into https://geojson.io to visualise instantly.
    """
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"label": "route"},
        }
    ]
    for stop in stops:
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [stop.station.longitude, stop.station.latitude],
                },
                "properties": {
                    "name": stop.station.name,
                    "city": stop.station.city,
                    "state": stop.station.state,
                    "price_per_gallon": float(stop.station.retail_price),
                    "gallons": stop.gallons_purchased,
                    "cost_usd": stop.stop_cost,
                    "mile_marker": stop.mile_marker,
                    "marker-color": "#e74c3c",
                    "marker-symbol": "fuel",
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}
