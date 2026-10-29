from django.contrib import admin

from .models import Institute


@admin.register(Institute)
class InstituteAdmin(admin.ModelAdmin):
    list_display = ("institute_name", "acronym", "school", "email_host_user")
    search_fields = ("institute_name", "acronym", "school__school_name")
    # The app password is set through the API (encrypted on the way in) and
    # never shown anywhere; the admin must not offer the raw column.
    exclude = ("email_host_password_encrypted",)
