from django.apps import AppConfig


class FeesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api.fees"

    def ready(self):
        # Importing the module is what connects the receivers - without this
        # the fee caches would never be invalidated. Nothing else uses the
        # name, hence the noqa.
        from . import signals  # noqa: F401
