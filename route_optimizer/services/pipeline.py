"""
Route pipeline — orchestrates all services for a single route request.

Ties together: geocoding → routing → spatial filtering → optimisation → map rendering.
Views call compute_route() and get back a RouteResult; they never touch the
individual services directly.
"""

from dataclasses import dataclass

from django.conf import settings

from ..models import FuelStation
from . import routing as routing_svc
from . import spatial as spatial_svc
from .map_image import render_map
from .optimizer import StopResult, optimise


@dataclass
class RouteResult:
    coords: list
    total_distance_miles: float
    stops: list[StopResult]
    total_fuel_cost: float
    map_b64: str


def compute_route(start: str, finish: str) -> RouteResult:
    """
    Full pipeline: geocode → driving route → nearby stations → optimise → map.
    Raises ValueError / Exception on failure (callers convert to HTTP errors).
    """
    start_coords = routing_svc.geocode(start)
    finish_coords = routing_svc.geocode(finish)

    route_data = routing_svc.get_route(start_coords, finish_coords)
    coords = route_data["features"][0]["geometry"]["coordinates"]
    total_distance_miles = spatial_svc.route_length_miles(coords)

    route_line = spatial_svc.build_linestring(coords)
    geocoded_stations = FuelStation.objects.filter(latitude__isnull=False)
    stations_with_markers = spatial_svc.find_stations_near_route(
        route_line, geocoded_stations, buffer_miles=settings.ROUTE_BUFFER_MILES
    )

    stops, total_fuel_cost = optimise(
        stations_with_markers,
        total_distance_miles,
        tank_range=settings.TANK_RANGE_MILES,
        mpg=settings.MPG,
    )

    map_b64 = render_map(coords, stops)

    return RouteResult(
        coords=coords,
        total_distance_miles=total_distance_miles,
        stops=stops,
        total_fuel_cost=total_fuel_cost,
        map_b64=map_b64,
    )
