"""
APScheduler configuration.

Registers the daily geocode_stations job and starts the background scheduler.
Called from RouteOptimizerConfig.ready() so it starts automatically with Django.

The job runs at 2:00 AM every day. All execution history (start time, finish
time, success/failure) is stored in the Django database and visible in the
Django admin under DjangoJobExecution.
"""

import logging
import sys

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from django_apscheduler.jobstores import DjangoJobStore

logger = logging.getLogger(__name__)

_SKIP_COMMANDS = {
    "migrate", "makemigrations", "collectstatic",
    "shell", "dbshell", "test", "check",
    "load_stations", "geocode_stations",
    "createsuperuser", "changepassword",
}


def start():
    """
    Start the background scheduler.
    Skipped automatically during management commands that don't need it.
    """
    if any(cmd in sys.argv for cmd in _SKIP_COMMANDS):
        return

    # Defer DB access until after Django is fully initialised.
    # This suppresses the "Accessing the database during app initialization"
    # warning that APScheduler's DjangoJobStore triggers in ready().
    from django.db import connection
    try:
        connection.ensure_connection()
    except Exception:
        return

    scheduler = BackgroundScheduler()
    scheduler.add_jobstore(DjangoJobStore(), "default")

    scheduler.add_job(
        _geocode_job,
        trigger=CronTrigger(hour=2, minute=0),   # every day at 2:00 AM
        id="geocode_stations_daily",
        name="Geocode fuel stations (900/day via ORS)",
        jobstore="default",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("Scheduler started — geocode_stations_daily will run at 2:00 AM every day.")


def _geocode_job():
    """Entry point called by APScheduler — imports task at call time to avoid circular imports."""
    from .tasks import geocode_stations
    geocode_stations()
