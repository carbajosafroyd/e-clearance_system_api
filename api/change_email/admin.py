from django.contrib import admin

from .models import EmailChangeRequest


@admin.register(EmailChangeRequest)
class EmailChangeRequestAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "current_email",
        "new_email",
        "stage",
        "created_at",
        "confirmed_at",
    )
    list_filter = ("stage", "created_at")
    search_fields = ("current_email", "new_email", "user__username")
    readonly_fields = ("code_hash", "change_token_hash", "confirm_token_hash")
