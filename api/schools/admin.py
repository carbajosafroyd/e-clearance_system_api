from django.contrib import admin

from .models import School  # AppSetting


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ("school_name", "short_name", "location", "email_host_user")
    search_fields = ("school_name", "short_name", "location")
    # The app password is set through the API (encrypted on the way in) and
    # never shown anywhere; the admin must not offer the raw column.
    exclude = ("email_host_password_encrypted",)


# @admin.register(AppSetting)
# class AppSettingAdmin(admin.ModelAdmin):
#     list_display = ("key",)
#     search_fields = ("key",)
