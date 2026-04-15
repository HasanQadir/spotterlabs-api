import base64

from django.http import HttpResponse
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .services.optimizer import StopResult
from .services.pipeline import compute_route


# ── Views ────────────────────────────────────────────────────────────────────

class RouteView(APIView):
    """
    GET /api/route/?start=<location>&finish=<location>

    Geocodes start and finish, fetches the driving route from ORS, finds the
    cheapest fuel stops along the way, and returns a JSON response with the
    optimised stop list, total cost, GeoJSON route, and a base64 map image.

    Example:
        GET /api/route/?start=Dallas, TX&finish=Indianapolis, IN

    Response (JSON):
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
                    "coordinates": {"latitude": 32.907, "longitude": -96.640}
                },
                ...
            ],
            "route": {
                "geojson": { ... },           // GeoJSON FeatureCollection
                "map_image_base64": "iVBOR..." // PNG rendered with OSM tiles
            }
        }
    """

    def get(self, request: Request) -> Response:
        start = request.query_params.get("start", "").strip()
        finish = request.query_params.get("finish", "").strip()

        if not start or not finish:
            return Response(
                {"error": "Both 'start' and 'finish' query parameters are required."},
                status=400,
            )

        try:
            result = compute_route(start, finish)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=422)
        except Exception as exc:
            return Response({"error": str(exc)}, status=502)

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
            for s in result.stops
        ]

        return Response(
            {
                "start": start,
                "finish": finish,
                "total_distance_miles": round(result.total_distance_miles, 1),
                "total_fuel_cost_usd": result.total_fuel_cost,
                "fuel_stops": stops_payload,
                "route": {
                    "geojson": _build_geojson(result.coords, result.stops),
                    "map_image_base64": result.map_b64,
                },
            }
        )


class RouteMapView(APIView):
    """
    GET /api/route/map/?start=<location>&finish=<location>

    Returns the route map as a raw PNG image, viewable directly in a browser
    or embeddable in an <img> tag without base64 decoding.

    Example:
        GET /api/route/map/?start=Dallas, TX&finish=Indianapolis, IN

    Response: PNG image (Content-Type: image/png)
    """

    def get(self, request: Request) -> HttpResponse:
        start = request.query_params.get("start", "").strip()
        finish = request.query_params.get("finish", "").strip()

        if not start or not finish:
            return HttpResponse(
                "Both 'start' and 'finish' query parameters are required.", status=400
            )

        try:
            result = compute_route(start, finish)
        except ValueError as exc:
            return HttpResponse(str(exc), status=422)
        except Exception as exc:
            return HttpResponse(str(exc), status=502)

        img_bytes = base64.b64decode(result.map_b64)
        return HttpResponse(img_bytes, content_type="image/png")


class RouteViewerView(APIView):
    """
    GET /api/route/view/?start=<location>&finish=<location>

    Returns a browser-friendly HTML page showing the route map image and a
    table of fuel stops with prices, gallons purchased, and mile markers.
    Useful for quick visual demos without a separate frontend.

    Example:
        GET /api/route/view/?start=Dallas, TX&finish=Indianapolis, IN

    Response: HTML page with:
        - Route summary (distance, total cost, number of stops)
        - Full-width map image rendered with OSM tiles
        - Table of fuel stops with station name, location, price, gallons, cost, mile marker
    """

    def get(self, request: Request) -> HttpResponse:
        start = request.query_params.get("start", "").strip()
        finish = request.query_params.get("finish", "").strip()

        if not start or not finish:
            return HttpResponse(
                "Both 'start' and 'finish' query parameters are required.", status=400
            )

        try:
            result = compute_route(start, finish)
        except ValueError as exc:
            return HttpResponse(str(exc), status=422)
        except Exception as exc:
            return HttpResponse(str(exc), status=502)

        stops_rows = "".join(
            f"<tr><td>{i+1}</td><td>{s.station.name}</td><td>{s.station.city}, {s.station.state}</td>"
            f"<td>${s.station.retail_price:.3f}</td><td>{s.gallons_purchased:.1f}</td>"
            f"<td>${s.stop_cost:.2f}</td><td>{s.mile_marker:.0f}</td>"
            f"<td><a href=\"https://www.google.com/maps?q={s.station.latitude},{s.station.longitude}\" "
            f"target=\"_blank\">{s.station.latitude:.5f}, {s.station.longitude:.5f}</a></td></tr>"
            for i, s in enumerate(result.stops)
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
    <p>Total distance: <strong>{result.total_distance_miles:.1f} miles</strong> &nbsp;|&nbsp;
       Total fuel cost: <span>${result.total_fuel_cost:.2f}</span> &nbsp;|&nbsp;
       Fuel stops: <strong>{len(result.stops)}</strong></p>
  </div>
  <img src="data:image/png;base64,{result.map_b64}" alt="Route map">
  <table>
    <thead>
      <tr><th>#</th><th>Station</th><th>Location</th><th>Price/gal</th><th>Gallons</th><th>Cost</th><th>Mile</th><th>Coordinates</th></tr>
    </thead>
    <tbody>{stops_rows}</tbody>
  </table>
</body>
</html>"""
        return HttpResponse(html, content_type="text/html")


# ── GeoJSON builder ──────────────────────────────────────────────────────────

def _build_geojson(coords: list, stops: list[StopResult]) -> dict:
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
