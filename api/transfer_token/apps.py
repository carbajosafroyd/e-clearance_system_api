from django.apps import AppConfig


class TransferTokenConfig(AppConfig):
    name = 'api.transfer_token'
    default_auto_field = "django.db.models.BigAutoField"
    verbose_name = "Transfer Token (Cross-System SSO Handoff)"
