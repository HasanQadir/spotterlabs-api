from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import FuelStation


@receiver(post_save, sender=FuelStation)
@receiver(post_delete, sender=FuelStation)
def clear_route_cache(sender, **kwargs):
    cache.clear()
