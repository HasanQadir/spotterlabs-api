"""
Management command to load fuel stations from the CSV into the database.

Usage:
    uv run python manage.py load_stations --csv /path/to/fuel-prices-for-be-assessment.csv

This command ONLY handles data loading - no geocoding.
Geocoding (lat/lon) is handled separately by the `geocode_stations` command
which runs as a daily cron job, processing 900 stations per day to stay
within the ORS free-tier quota of 1,000 requests/day.

Deduplication
--------------
  1. True duplicates (same OPIS ID + address + city + state, different prices)
     → keep only the cheapest price row.
  2. Different stations in the same city (different addresses) are kept as
     separate rows - each will get its own geocoded coordinates later.
"""

import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from route_optimizer.models import FuelStation


class Command(BaseCommand):
    help = "Load fuel stations from CSV into the database (geocoding handled separately)."

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

    def handle(self, *args, **options):
        csv_path: Path = options["csv"]
        if not csv_path.exists():
            raise CommandError(f"File not found: {csv_path}")

        # ── 1. Parse CSV ──────────────────────────────────────────────────
        self.stdout.write(f"Reading {csv_path} …")
        rows = self._parse_csv(csv_path)
        self.stdout.write(f"  {len(rows)} rows parsed.")

        # ── 2. Deduplicate true duplicates ────────────────────────────────
        unique_stations = self._deduplicate(rows)
        self.stdout.write(
            f"  {len(unique_stations)} unique stations after deduplication "
            f"({len(rows) - len(unique_stations)} true duplicates removed)."
        )

        # ── 3. Build FuelStation objects (no coordinates yet) ─────────────
        stations = [
            FuelStation(
                opis_id=int(row["opis_id"]),
                name=row["name"].strip(),
                address=row["address"].strip(),
                city=row["city"].strip(),
                state=row["state"].strip(),
                rack_id=int(row["rack_id"]) if row["rack_id"].strip() else None,
                retail_price=float(row["retail_price"]),
                latitude=None,
                longitude=None,
            )
            for row in unique_stations
        ]

        # ── 4. Bulk insert in one transaction ─────────────────────────────
        self.stdout.write(f"Inserting {len(stations)} stations …")
        with transaction.atomic():
            if options["clear"]:
                FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(stations, batch_size=500)

        total = FuelStation.objects.count()
        self.stdout.write(self.style.SUCCESS(
            f"Done. {total} stations in database (coordinates pending - "
            f"run 'geocode_stations' or wait for the daily cron job)."
        ))

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _deduplicate(rows: list[dict]) -> list[dict]:
        """Keep only the cheapest price row per unique (opis_id, address, city, state)."""
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
