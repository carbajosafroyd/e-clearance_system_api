"""
Keep the feature-flag cache honest.

`core.features` caches an institute's `feature_*` rows so a permission check
is not a database round trip. A signal rather than clearing it in the view:
settings are also edited through the Django admin, a management command and
the shell, and a toggle that takes effect from one of those and not the
others is worse than no cache at all.
"""

from __future__ import annotations

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from core import features

from .models import SystemSetting


@receiver(post_save, sender=SystemSetting)
@receiver(post_delete, sender=SystemSetting)
def drop_cached_features(sender, instance: SystemSetting, **kwargs) -> None:
    if instance.setting_key.startswith(features.SETTING_PREFIX):
        features.invalidate(instance.institute_id)
