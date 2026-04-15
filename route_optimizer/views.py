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
import json

from django.conf import settings
from django.http import HttpResponse
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


class RouteMapView(APIView):
    """
    GET /api/route/map/?start=...&finish=...
    Returns the route map as a PNG image directly (viewable in a browser).
    """

    def get(self, request: Request) -> HttpResponse:
        start = request.query_params.get("start", "").strip()
        finish = request.query_params.get("finish", "").strip()

        if not start or not finish:
            return HttpResponse("Both 'start' and 'finish' query parameters are required.", status=400)

        try:
            start_coords = routing_svc.geocode(start)
            finish_coords = routing_svc.geocode(finish)
        except Exception as exc:
            return HttpResponse(f"Geocoding failed: {exc}", status=400)

        try:
            route_data = routing_svc.get_route(start_coords, finish_coords)
        except Exception as exc:
            return HttpResponse(f"Routing failed: {exc}", status=502)

        feature = route_data["features"][0]
        coords = feature["geometry"]["coordinates"]
        total_distance_miles = spatial_svc.route_length_miles(coords)

        route_line = spatial_svc.build_linestring(coords)
        all_stations = FuelStation.objects.all()
        stations_with_markers = spatial_svc.find_stations_near_route(
            route_line, all_stations, buffer_miles=settings.ROUTE_BUFFER_MILES
        )

        try:
            stops, total_fuel_cost = optimise(
                stations_with_markers, total_distance_miles,
                tank_range=settings.TANK_RANGE_MILES, mpg=settings.MPG,
            )
        except ValueError as exc:
            return HttpResponse(str(exc), status=422)

        map_b64 = render_map(coords, stops)
        img_bytes = base64.b64decode(map_b64)
        return HttpResponse(img_bytes, content_type="image/png")


class RouteViewerView(APIView):
    """
    GET /api/route/view/?start=...&finish=...
    Returns a simple HTML page showing the map image and fuel stop summary.
    """

    def get(self, request: Request) -> HttpResponse:
        start = request.query_params.get("start", "").strip()
        finish = request.query_params.get("finish", "").strip()

        if not start or not finish:
            return HttpResponse("Both 'start' and 'finish' parameters are required.", status=400)

        try:
            start_coords = routing_svc.geocode(start)
            finish_coords = routing_svc.geocode(finish)
        except Exception as exc:
            return HttpResponse(f"Geocoding failed: {exc}", status=400)

        try:
            route_data = routing_svc.get_route(start_coords, finish_coords)
        except Exception as exc:
            return HttpResponse(f"Routing failed: {exc}", status=502)

        feature = route_data["features"][0]
        coords = feature["geometry"]["coordinates"]
        total_distance_miles = spatial_svc.route_length_miles(coords)

        route_line = spatial_svc.build_linestring(coords)
        all_stations = FuelStation.objects.all()
        stations_with_markers = spatial_svc.find_stations_near_route(
            route_line, all_stations, buffer_miles=settings.ROUTE_BUFFER_MILES
        )

        try:
            stops, total_fuel_cost = optimise(
                stations_with_markers, total_distance_miles,
                tank_range=settings.TANK_RANGE_MILES, mpg=settings.MPG,
            )
        except ValueError as exc:
            return HttpResponse(str(exc), status=422)

        map_b64 = render_map(coords, stops)

        stops_rows = "".join(
            f"<tr><td>{i+1}</td><td>{s.station.name}</td><td>{s.station.city}, {s.station.state}</td>"
            f"<td>${s.station.retail_price:.3f}</td><td>{s.gallons_purchased:.1f}</td>"
            f"<td>${s.stop_cost:.2f}</td><td>{s.mile_marker:.0f}</td></tr>"
            for i, s in enumerate(stops)
        )

        html = f"""<!DOCTYPE html>
<html>
<head>
  <title>Fuel Route: {start} → {finish}</title>
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 1100px; margin: 40px auto; padding: 0 20px; background: #f5f5f5; }}
    h1 {{ color: #333; }}
    .summary {{ background: #fff; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 1px 4px rgba(0,0,0,.1); }}
    .summary span {{ font-size: 1.4em; font-weight: bold; color: #e74c3c; }}
    img {{ width: 100%; border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,.2); margin-bottom: 20px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,.1); }}
    th {{ background: #2c3e50; color: #fff; padding: 10px 12px; text-align: left; }}
    td {{ padding: 9px 12px; border-bottom: 1px solid #eee; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover td {{ background: #f9f9f9; }}
  </style>
</head>
<body>
  <h1>Fuel Route: {start} → {finish}</h1>
  <div class="summary">
    <p>Total distance: <strong>{total_distance_miles:.1f} miles</strong> &nbsp;|&nbsp;
       Total fuel cost: <span>${total_fuel_cost:.2f}</span> &nbsp;|&nbsp;
       Fuel stops: <strong>{len(stops)}</strong></p>
  </div>
  <img src="data:image/png;base64,{map_b64}" alt="Route map">
  <table>
    <thead>
      <tr><th>#</th><th>Station</th><th>Location</th><th>Price/gal</th><th>Gallons</th><th>Cost</th><th>Mile</th></tr>
    </thead>
    <tbody>{stops_rows}</tbody>
  </table>
</body>
</html>"""
        return HttpResponse(html, content_type="text/html")


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
