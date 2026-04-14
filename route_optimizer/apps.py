from django.apps import AppConfig


class RouteOptimizerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "route_optimizer"

    def ready(self):
        from . import scheduler
        scheduler.start()
