from django.apps import AppConfig


class ComplaintsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api.complaints"
    label = "complaints"
    verbose_name = "Student Complaints"
