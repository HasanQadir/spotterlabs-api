"""
Management command to load fuel stations from the CSV and geocode
each unique station address using the ORS Geocoding API.

Usage:
    uv run python manage.py load_stations --csv /path/to/fuel-prices-for-be-assessment.csv

Deduplication strategy
-----------------------
The CSV contains two kinds of repeated rows:

  1. True duplicates — same station (same OPIS ID + address + city + state)
     recorded multiple times with different prices. We keep only the row
     with the LOWEST retail price, then store it once.

  2. Different stations, same city — different OPIS IDs at different highway
     exits in the same city. These are kept as separate rows and geocoded
     individually by full address so each gets accurate coordinates.

Speed optimisations
--------------------
1. Deduplicate true duplicates first  → fewer rows to geocode and store.
2. Geocode by full address            → accurate per-station coordinates.
3. Parallel geocoding (20 workers)    → concurrent ORS calls instead of serial.
4. Address-level fallback             → if full address geocoding fails, fall
                                        back to city + state so no station is
                                        silently dropped.
5. Persistent cache (geocode_cache.json) → re-runs skip already-geocoded
                                        addresses, making subsequent loads
                                        near-instant.
6. Single bulk DB insert              → one transaction, no per-row commits.
"""

import csv
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from route_optimizer.models import FuelStation

ORS_GEOCODE_URL = f"{settings.ORS_BASE_URL}/geocode/search"
CACHE_FILE = settings.BASE_DIR / "geocode_cache.json"
MAX_WORKERS = 20


class Command(BaseCommand):
    help = "Load fuel stations from CSV and geocode them by address via ORS."

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv",
            required=True,
            type=Path,
            help="Path to the fuel-prices CSV file.",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            default=True,
            help="Delete existing FuelStation rows before loading (default: True).",
        )
        parser.add_argument(
            "--workers",
            type=int,
            default=MAX_WORKERS,
            help=f"Number of parallel geocoding threads (default: {MAX_WORKERS}).",
        )

    def handle(self, *args, **options):
        csv_path: Path = options["csv"]
        if not csv_path.exists():
            raise CommandError(f"File not found: {csv_path}")

        if not settings.ORS_API_KEY:
            raise CommandError("ORS_API_KEY is not set. Add it to your .env file.")

        # ── 1. Parse CSV ──────────────────────────────────────────────────
        self.stdout.write(f"Reading {csv_path} …")
        rows = self._parse_csv(csv_path)
        self.stdout.write(f"  {len(rows)} rows parsed.")

        # ── 2. Deduplicate true duplicates ────────────────────────────────
        # Group by (opis_id, address, city, state) — same physical station.
        # Keep only the cheapest price among duplicates.
        unique_stations = self._deduplicate(rows)
        self.stdout.write(
            f"  {len(unique_stations)} unique stations after deduplication "
            f"({len(rows) - len(unique_stations)} true duplicates removed)."
        )

        # ── 3. Load geocode cache ─────────────────────────────────────────
        cache: dict[str, tuple | None] = self._load_cache()

        to_geocode = [
            row for row in unique_stations
            if self._cache_key(row) not in cache
        ]

        if to_geocode:
            self.stdout.write(
                f"Geocoding {len(to_geocode)} stations by address "
                f"({len(unique_stations) - len(to_geocode)} already cached) "
                f"with {options['workers']} parallel workers …"
            )
            t0 = time.perf_counter()
            newly_geocoded = self._geocode_parallel(to_geocode, options["workers"])
            elapsed = time.perf_counter() - t0

            for key, coords in newly_geocoded.items():
                cache[key] = coords
            self._save_cache(cache)

            hits = sum(1 for v in newly_geocoded.values() if v is not None)
            self.stdout.write(
                self.style.SUCCESS(
                    f"  Geocoded {hits}/{len(to_geocode)} stations in {elapsed:.1f}s."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("  All stations already in cache — skipping geocoding.")
            )

        # ── 4. Build FuelStation objects ──────────────────────────────────
        stations = []
        skipped = 0
        for row in unique_stations:
            coords = cache.get(self._cache_key(row))
            if coords is None:
                skipped += 1
                continue
            lon, lat = coords
            stations.append(
                FuelStation(
                    opis_id=int(row["opis_id"]),
                    name=row["name"].strip(),
                    address=row["address"].strip(),
                    city=row["city"].strip(),
                    state=row["state"].strip(),
                    rack_id=int(row["rack_id"]) if row["rack_id"].strip() else None,
                    retail_price=float(row["retail_price"]),
                    latitude=lat,
                    longitude=lon,
                )
            )

        self.stdout.write(
            f"Inserting {len(stations)} stations "
            f"({skipped} skipped — geocoding failed) …"
        )

        # ── 5. Bulk insert ────────────────────────────────────────────────
        with transaction.atomic():
            if options["clear"]:
                FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(stations, batch_size=500)

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {FuelStation.objects.count()} fuel stations in database."
            )
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _deduplicate(rows: list[dict]) -> list[dict]:
        """
        Group rows by (opis_id, address, city, state).
        Keep only the row with the lowest retail_price per group.
        """
        groups: dict[tuple, dict] = {}
        for row in rows:
            key = (
                row["opis_id"].strip(),
                row["address"].strip().lower(),
                row["city"].strip().lower(),
                row["state"].strip().upper(),
            )
            if key not in groups or float(row["retail_price"]) < float(groups[key]["retail_price"]):
                groups[key] = row
        return list(groups.values())

    @staticmethod
    def _cache_key(row: dict) -> str:
        """Stable string key for the geocode cache."""
        return f"{row['address'].strip()}|{row['city'].strip()}|{row['state'].strip()}"

    def _geocode_parallel(
        self,
        rows: list[dict],
        workers: int,
    ) -> dict[str, tuple | None]:
        results: dict[str, tuple | None] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_key = {
                pool.submit(
                    self._geocode,
                    row["address"],
                    row["city"],
                    row["state"],
                ): self._cache_key(row)
                for row in rows
            }
            done = 0
            for future in as_completed(future_to_key):
                key = future_to_key[future]
                results[key] = future.result()
                done += 1
                if done % 100 == 0:
                    self.stdout.write(f"  … {done}/{len(rows)}")
        return results

    @staticmethod
    def _geocode(address: str, city: str, state: str) -> tuple[float, float] | None:
        """
        Geocode a station by full address, falling back to city+state if needed.
        Full address gives accurate per-station coordinates (different highway
        exits in the same city resolve to different points).
        """
        def _call(query: str) -> tuple[float, float] | None:
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

        # Try full address first, fall back to city+state
        full_query = f"{address.strip()}, {city.strip()}, {state.strip()}, USA"
        result = _call(full_query)
        if result is None:
            result = _call(f"{city.strip()}, {state.strip()}, USA")
        return result

    @staticmethod
    def _parse_csv(path: Path) -> list[dict]:
        rows = []
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                normalised = {k.strip(): v.strip() for k, v in row.items()}
                rows.append(
                    {
                        "opis_id": normalised.get("OPIS Truckstop ID", "0"),
                        "name": normalised.get("Truckstop Name", ""),
                        "address": normalised.get("Address", ""),
                        "city": normalised.get("City", ""),
                        "state": normalised.get("State", ""),
                        "rack_id": normalised.get("Rack ID", ""),
                        "retail_price": normalised.get("Retail Price", "0"),
                    }
                )
        return rows

    @staticmethod
    def _load_cache() -> dict:
        if CACHE_FILE.exists():
            try:
                data = json.loads(CACHE_FILE.read_text())
                return {k: tuple(v) if v else None for k, v in data.items()}
            except Exception:
                pass
        return {}

    @staticmethod
    def _save_cache(cache: dict) -> None:
        serialisable = {k: list(v) if v else None for k, v in cache.items()}
        CACHE_FILE.write_text(json.dumps(serialisable, indent=2))
