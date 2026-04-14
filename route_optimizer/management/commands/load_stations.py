"""
Management command to load fuel stations from the CSV and geocode
each unique (city, state) pair using the ORS Geocoding API.

Usage:
    uv run python manage.py load_stations --csv /path/to/fuel-prices-for-be-assessment.csv

Speed optimisations vs a naive serial approach
-----------------------------------------------
1. Deduplicate first  — only geocode unique (city, state) pairs, not every row.
2. Parallel geocoding — use a ThreadPoolExecutor (default 20 workers) to fire
                        all ORS requests concurrently.  500 unique locations
                        that would take ~3 min serially finish in ~8-12 s.
3. Persistent cache   — results are saved to geocode_cache.json next to the DB.
                        Re-running the command (e.g. after a data refresh) skips
                        any location that was already geocoded → near-instant.
4. Single bulk insert — all rows written in one DB transaction with bulk_create.
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
MAX_WORKERS = 20  # concurrent ORS requests


class Command(BaseCommand):
    help = "Load fuel stations from CSV and geocode them via ORS (parallelised)."

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
        self.stdout.write(f"  {len(rows)} rows loaded.")

        # ── 2. Deduplicate (city, state) pairs ────────────────────────────
        unique_keys: set[tuple] = {
            (r["city"].strip(), r["state"].strip()) for r in rows
        }
        self.stdout.write(f"  {len(unique_keys)} unique city/state locations.")

        # ── 3. Load persistent geocode cache ──────────────────────────────
        cache: dict[str, tuple | None] = self._load_cache()
        cache_key = lambda city, state: f"{city.strip()}|{state.strip()}"

        to_geocode = [
            (city, state)
            for city, state in unique_keys
            if cache_key(city, state) not in cache
        ]

        if to_geocode:
            self.stdout.write(
                f"Geocoding {len(to_geocode)} new locations "
                f"({len(unique_keys) - len(to_geocode)} already cached) "
                f"with {options['workers']} parallel workers …"
            )
            t0 = time.perf_counter()
            newly_geocoded = self._geocode_parallel(to_geocode, options["workers"])
            elapsed = time.perf_counter() - t0

            # Merge into cache
            for (city, state), coords in newly_geocoded.items():
                cache[cache_key(city, state)] = coords
            self._save_cache(cache)

            hits = sum(1 for v in newly_geocoded.values() if v is not None)
            self.stdout.write(
                self.style.SUCCESS(
                    f"  Geocoded {hits}/{len(to_geocode)} locations in {elapsed:.1f}s."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("  All locations already in cache — skipping geocoding.")
            )

        # ── 4. Build FuelStation objects ──────────────────────────────────
        stations = []
        skipped = 0
        for row in rows:
            key = cache_key(row["city"], row["state"])
            coords = cache.get(key)
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

        # ── 5. Bulk insert in a single transaction ────────────────────────
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

    def _geocode_parallel(
        self,
        locations: list[tuple[str, str]],
        workers: int,
    ) -> dict[tuple, tuple | None]:
        """
        Geocode all (city, state) pairs concurrently.
        Returns a dict mapping (city, state) → (lon, lat) | None.
        """
        results: dict[tuple, tuple | None] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_key = {
                pool.submit(self._geocode, city, state): (city, state)
                for city, state in locations
            }
            done = 0
            for future in as_completed(future_to_key):
                key = future_to_key[future]
                results[key] = future.result()
                done += 1
                if done % 50 == 0:
                    self.stdout.write(f"  … {done}/{len(locations)}")
        return results

    @staticmethod
    def _geocode(city: str, state: str) -> tuple[float, float] | None:
        query = f"{city.strip()}, {state.strip()}, USA"
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
                # JSON keys are strings; values are [lon, lat] lists or null
                return {k: tuple(v) if v else None for k, v in data.items()}
            except Exception:
                pass
        return {}

    @staticmethod
    def _save_cache(cache: dict) -> None:
        serialisable = {k: list(v) if v else None for k, v in cache.items()}
        CACHE_FILE.write_text(json.dumps(serialisable, indent=2))
