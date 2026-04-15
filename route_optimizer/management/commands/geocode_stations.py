"""
Management command to manually trigger geocoding of fuel stations.

Usage:
    uv run python manage.py geocode_stations
    uv run python manage.py geocode_stations --limit 500
    uv run python manage.py geocode_stations --force   # re-geocode everything

The same logic runs automatically every day at 2:00 AM via APScheduler.
Use this command to seed data manually (e.g. before a demo).
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from route_optimizer.constants import DAILY_LIMIT
from route_optimizer.tasks import geocode_stations


class Command(BaseCommand):
    help = "Manually geocode ungeocoded fuel stations via ORS (same job as the daily scheduler)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=DAILY_LIMIT,
            help=f"Max stations to geocode in this run (default: {DAILY_LIMIT}).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            default=False,
            help="Re-geocode stations that already have coordinates.",
        )

    def handle(self, *args, **options):
        if not settings.ORS_API_KEY:
            self.stdout.write(self.style.ERROR(
                "ORS_API_KEY is not set in your .env file. Add it and try again."
            ))
            return

        self.stdout.write("Starting geocoding - progress shown every 10 stations …")
        summary = geocode_stations(
            limit=options["limit"],
            force=options["force"],
        )

        if "error" in summary:
            self.stdout.write(self.style.ERROR(summary["error"]))
            return

        if summary["geocoded"] == 0 and summary["remaining"] == 0:
            self.stdout.write(self.style.SUCCESS("All stations already geocoded."))
            return

        self.stdout.write(self.style.SUCCESS(
            f"Done. {summary['geocoded']} geocoded, {summary['failed']} failed. "
            f"Progress: {summary['total_with_coords']}/{summary['grand_total']} stations "
            f"({summary['remaining']} remaining)."
        ))
