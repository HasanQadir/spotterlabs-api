"""
Geocoding task — shared between the APScheduler job and the
`geocode_stations` management command.

Called by:
  - scheduler.py         → automatically, every day at 2 AM
  - manage.py geocode_stations → manually, e.g. to seed data before demo
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from django.conf import settings
from django.db import transaction

from .models import FuelStation

logger = logging.getLogger(__name__)

ORS_GEOCODE_URL = f"{settings.ORS_BASE_URL}/geocode/search"

DAILY_LIMIT = 900    # safely under ORS 1,000/day quota
WORKERS = 10         # parallel threads per batch
BATCH_SIZE = 10      # stations per parallel batch
BATCH_SLEEP = 7.0    # seconds between batches → ~85 req/min (under 100/min limit)


def geocode_stations(limit: int = DAILY_LIMIT, force: bool = False) -> dict:
    """
    Geocode up to `limit` ungeocoded fuel stations using ORS.

    Strategy per station:
      1. Try full highway address  (e.g. "I-75, EXIT 144-B, Bridgeport, MI, USA")
      2. Fall back to city + state (e.g. "Bridgeport, MI, USA")

    Returns a summary dict with counts for logging/display.
    """
    if not settings.ORS_API_KEY:
        logger.error("ORS_API_KEY is not set — geocoding skipped.")
        return {"error": "ORS_API_KEY not set"}

    qs = FuelStation.objects.all() if force else \
         FuelStation.objects.filter(latitude__isnull=True)

    total_remaining = qs.count()
    stations = list(qs[:limit])

    if not stations:
        logger.info("All stations are already geocoded.")
        return {"geocoded": 0, "failed": 0, "remaining": 0}

    logger.info(f"Geocoding {len(stations)} stations ({total_remaining} total remaining) …")

    results: dict[int, tuple | None] = {}
    total_batches = (len(stations) + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_idx in range(total_batches):
        batch = stations[batch_idx * BATCH_SIZE: (batch_idx + 1) * BATCH_SIZE]

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            future_to_pk = {
                pool.submit(_geocode_station, s): s.pk for s in batch
            }
            for future in as_completed(future_to_pk):
                pk = future_to_pk[future]
                results[pk] = future.result()

        done = min((batch_idx + 1) * BATCH_SIZE, len(stations))
        logger.info(f"  … {done}/{len(stations)}")

        if batch_idx < total_batches - 1:
            time.sleep(BATCH_SLEEP)

    # Bulk update coordinates
    to_update = []
    success, failed = 0, 0

    for station in stations:
        coords = results.get(station.pk)
        if coords:
            station.longitude, station.latitude = coords
            to_update.append(station)
            success += 1
        else:
            failed += 1

    with transaction.atomic():
        FuelStation.objects.bulk_update(to_update, ["latitude", "longitude"], batch_size=500)

    geocoded_total = FuelStation.objects.filter(latitude__isnull=False).count()
    grand_total = FuelStation.objects.count()

    summary = {
        "geocoded": success,
        "failed": failed,
        "total_with_coords": geocoded_total,
        "grand_total": grand_total,
        "remaining": grand_total - geocoded_total,
    }
    logger.info(
        f"Done. {success} geocoded, {failed} failed. "
        f"Progress: {geocoded_total}/{grand_total} stations have coordinates."
    )
    return summary


# ── ORS helpers ───────────────────────────────────────────────────────────────

def _geocode_station(station: FuelStation) -> tuple[float, float] | None:
    """Try full address, fall back to city+state."""
    queries = [
        f"{station.address}, {station.city}, {station.state}, USA",
        f"{station.city}, {station.state}, USA",
    ]
    for query in queries:
        result = _ors_call(query)
        if result:
            return result
    return None


def _ors_call(query: str) -> tuple[float, float] | None:
    try:
        resp = requests.get(
            ORS_GEOCODE_URL,
            params={
                "api_key": settings.ORS_API_KEY,
                "text": query,
                "boundary.country": "US",
                "size": 1,
            },
            timeout=10,
        )
        resp.raise_for_status()
        features = resp.json().get("features", [])
        if not features:
            return None
        lon, lat = features[0]["geometry"]["coordinates"]
        return float(lon), float(lat)
    except Exception:
        return None
