"""
Geocoding task — shared between the APScheduler job and the
`geocode_stations` management command.

Called by:
  - scheduler.py         → automatically, every day at 2 AM
  - manage.py geocode_stations → manually, e.g. to seed data before demo
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from django.conf import settings
from django.db import transaction

from .constants import DAILY_LIMIT, WORKERS, RATE_LIMIT_CALLS, RATE_WINDOW
from .models import FuelStation

logger = logging.getLogger(__name__)

ORS_GEOCODE_URL = f"{settings.ORS_BASE_URL}/geocode/search"

_call_times: list[float] = []
_rate_lock = threading.Lock()


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

    station_map = {s.pk: s for s in stations}
    pending: list = []   # (station, coords) pairs waiting to be flushed
    success, failed = 0, 0
    done = 0

    FLUSH_EVERY = 50  # write to DB every N completed geocodes

    def _flush(batch):
        to_update = []
        for station, coords in batch:
            station.longitude, station.latitude = coords
            to_update.append(station)
        with transaction.atomic():
            FuelStation.objects.bulk_update(to_update, ["latitude", "longitude"], batch_size=500)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        future_to_pk = {
            pool.submit(_geocode_station, s): s.pk for s in stations
        }
        for future in as_completed(future_to_pk):
            pk = future_to_pk[future]
            coords = future.result()
            done += 1
            if coords:
                pending.append((station_map[pk], coords))
                success += 1
            else:
                failed += 1

            if len(pending) >= FLUSH_EVERY:
                _flush(pending)
                pending.clear()

            if done % 10 == 0:
                logger.info(f"  … {done}/{len(stations)}")

    # Flush any remaining results
    if pending:
        _flush(pending)


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
    """
    Try full highway address first, fall back to city+state.
    Exact per-station coordinates matter for truck routing.
    """
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
    """
    Make one ORS geocoding call, gated by a sliding-window rate limiter.
    Allows at most 80 API calls per 60-second window across all threads.
    """
    with _rate_lock:
        now = time.monotonic()
        while _call_times and _call_times[0] < now - RATE_WINDOW:
            _call_times.pop(0)
        if len(_call_times) >= RATE_LIMIT_CALLS:
            sleep_for = RATE_WINDOW - (now - _call_times[0]) + 0.05
            time.sleep(max(sleep_for, 0))
            now = time.monotonic()
            while _call_times and _call_times[0] < now - RATE_WINDOW:
                _call_times.pop(0)
        _call_times.append(time.monotonic())

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
