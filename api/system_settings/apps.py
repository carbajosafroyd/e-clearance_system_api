from django.apps import AppConfig


class SystemSettingsConfig(AppConfig):
    name = "api.system_settings"

    def ready(self):
        # Registers the feature-flag cache invalidation.
        from . import signals  # noqa: F401
