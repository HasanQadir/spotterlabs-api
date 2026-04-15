"""
Spatial utilities - all pure Python / Shapely, zero external calls.

Key operations:
  - Build a Shapely LineString from the ORS route geometry
  - Compute total route length in miles (Haversine)
  - Buffer the route and find all fuel stations inside the buffer
  - Project each station onto the route to obtain its mile-marker
"""

import math
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points


# ── Haversine helpers ────────────────────────────────────────────────────────

EARTH_RADIUS_MILES = 3_958.8


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles between two (lat, lon) points."""
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lon / 2) ** 2
    )
    return EARTH_RADIUS_MILES * 2 * math.asin(math.sqrt(a))


def route_length_miles(coords: list[list[float]]) -> float:
    """Total route distance in miles from ORS coordinate list [[lon, lat], ...]."""
    total = 0.0
    for i in range(len(coords) - 1):
        lon1, lat1 = coords[i]
        lon2, lat2 = coords[i + 1]
        total += _haversine(lat1, lon1, lat2, lon2)
    return total


# ── Route geometry ───────────────────────────────────────────────────────────

def build_linestring(coords: list[list[float]]) -> LineString:
    """Build a Shapely LineString from ORS [[lon, lat], ...] coords."""
    return LineString([(c[0], c[1]) for c in coords])


# ── Station proximity ────────────────────────────────────────────────────────

# 1 degree of latitude ≈ 69 miles everywhere.
# 1 degree of longitude ≈ 69 * cos(lat) miles - we use the lat midpoint of
# the continental US (~39°) as a conservative approximation.
_DEG_PER_MILE_LAT = 1.0 / 69.0
_DEG_PER_MILE_LON = 1.0 / (69.0 * math.cos(math.radians(39.0)))


def find_stations_near_route(
    route_line: LineString,
    stations,  # QuerySet[FuelStation]
    buffer_miles: float,
) -> list[tuple]:
    """
    Return a list of (FuelStation, mile_marker) pairs for every station
    whose coordinates fall within *buffer_miles* of the route, sorted
    ascending by mile_marker.

    We use an asymmetric buffer (different x/y scale) to avoid distortion
    from the lat/lon coordinate system.
    """
    # Build a bounding-box pre-filter to avoid loading all stations from the DB
    min_lon, min_lat, max_lon, max_lat = route_line.bounds
    lat_buf = buffer_miles * _DEG_PER_MILE_LAT
    lon_buf = buffer_miles * _DEG_PER_MILE_LON

    candidates = stations.filter(
        latitude__gte=min_lat - lat_buf,
        latitude__lte=max_lat + lat_buf,
        longitude__gte=min_lon - lon_buf,
        longitude__lte=max_lon + lon_buf,
        latitude__isnull=False,
        longitude__isnull=False,
    )

    # Compute the total line length in degrees once (for fractional projection)
    line_len_deg = route_line.length
    total_miles = route_length_miles(list(route_line.coords))

    nearby: list[tuple] = []
    for station in candidates:
        pt = Point(station.longitude, station.latitude)
        nearest_pt = nearest_points(route_line, pt)[0]

        # Distance from station to nearest point on route (degrees → miles)
        dist_deg = pt.distance(nearest_pt)
        dist_lat_miles = dist_deg / _DEG_PER_MILE_LAT  # rough but consistent
        if dist_lat_miles > buffer_miles:
            continue

        # Mile marker: how far along the route is the nearest point?
        frac = route_line.project(nearest_pt) / line_len_deg
        mile_marker = frac * total_miles
        nearby.append((station, mile_marker))

    nearby.sort(key=lambda x: x[1])
    return nearby
