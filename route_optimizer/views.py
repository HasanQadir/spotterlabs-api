import json

from django.http import HttpResponse
from django.shortcuts import render
from rest_framework.request import Request
from rest_framework.views import APIView

from .services.pipeline import compute_route


class RouteViewerView(APIView):
    """
    GET /api/route/view/?start=<location>&finish=<location>

    Returns a browser-friendly HTML page with an interactive Leaflet.js map
    (CartoDB/OSM tiles), clickable fuel stop markers, and a fuel stop table.

    Example:
        GET /api/route/view/?start=Dallas, TX&finish=Indianapolis, IN

    Response: HTML page with:
        - Route summary (distance, total cost, number of stops)
        - Interactive Leaflet.js map with route line and clickable fuel stop markers
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

        stops_data = [
            {
                "name": s.station.name,
                "city": s.station.city,
                "state": s.station.state,
                "price": f"{s.station.retail_price:.3f}",
                "gallons": f"{s.gallons_purchased:.1f}",
                "cost": f"{s.stop_cost:.2f}",
                "mile": round(s.mile_marker),
                "lat": f"{s.station.latitude:.5f}",
                "lon": f"{s.station.longitude:.5f}",
            }
            for s in result.stops
        ]

        context = {
            "start": start,
            "finish": finish,
            "total_distance_miles": round(result.total_distance_miles, 1),
            "total_fuel_cost": f"{result.total_fuel_cost:.2f}",
            "stops": stops_data,
            "route_latlngs": json.dumps([[lat, lon] for lon, lat in result.coords]),
            "stops_json": json.dumps([
                {
                    "lat": s.station.latitude,
                    "lon": s.station.longitude,
                    "name": s.station.name,
                    "city": s.station.city,
                    "state": s.station.state,
                    "price": float(s.station.retail_price),
                    "gallons": s.gallons_purchased,
                    "cost": s.stop_cost,
                    "mile": s.mile_marker,
                }
                for s in result.stops
            ]),
        }

        return render(request, "route_optimizer/route_view.html", context)
