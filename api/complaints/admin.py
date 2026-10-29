from django.contrib import admin

from .models import Complaint


@admin.register(Complaint)
class ComplaintAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "student",
        "system",
        "category",
        "status",
        "handled_by",
        "handled_at",
    )
    list_filter = ("system", "category", "status")
    search_fields = ("title", "student__s_studentID", "student__s_lname")
    # Set by the resolve/reject actions, which are also what notify the
    # student - editing them here would close a complaint in silence.
    readonly_fields = ("handled_at",)
