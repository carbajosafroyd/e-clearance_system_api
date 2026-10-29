from django.contrib import admin

from .models import PasswordChangeToken, PasswordResetRequest


@admin.register(PasswordResetRequest)
class PasswordResetRequestAdmin(admin.ModelAdmin):
    list_display = ("email", "created_at", "expires_at", "validated_at", "attempts")
    readonly_fields = ("code_hash",)


@admin.register(PasswordChangeToken)
class PasswordChangeTokenAdmin(admin.ModelAdmin):
    list_display = ("email", "created_at", "expires_at", "used_at")
    readonly_fields = ("token_hash",)