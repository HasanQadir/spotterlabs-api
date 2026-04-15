"""
Thin wrapper around the OpenRouteService API.

We make at most 3 external calls per request:
  1. Geocode start location
  2. Geocode finish location
  3. Fetch driving directions (GeoJSON)

The directions response contains the full route geometry and
the total distance - everything we need for the optimizer.
"""

import requests
from django.conf import settings


def _headers() -> dict:
    return {
        "Authorization": settings.ORS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json, application/geo+json",
    }


def geocode(place: str) -> tuple[float, float]:
    """Return (longitude, latitude) for a free-text US place name."""
    resp = requests.get(
        f"{settings.ORS_BASE_URL}/geocode/search",
        params={
            "api_key": settings.ORS_API_KEY,
            "text": place,
            "boundary.country": "US",
            "size": 1,
        },
        timeout=10,
    )
    resp.raise_for_status()
    features = resp.json().get("features", [])
    if not features:
        raise ValueError(f"Could not geocode location: {place!r}")
    lon, lat = features[0]["geometry"]["coordinates"]
    return float(lon), float(lat)


def get_route(
    start: tuple[float, float],
    finish: tuple[float, float],
) -> dict:
    """
    Call ORS directions and return the raw GeoJSON FeatureCollection.

    The first (and only) feature contains:
      - geometry.coordinates  : list of [lon, lat] waypoints
      - properties.summary.distance  : metres
    """
    resp = requests.post(
        f"{settings.ORS_BASE_URL}/v2/directions/driving-car/geojson",
        headers=_headers(),
        json={
            "coordinates": [list(start), list(finish)],
            "geometry_simplify": False,
            "instructions": False,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()
