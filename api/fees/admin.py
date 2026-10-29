from django.contrib import admin

from .models import Fee, GeneratedFeeBatch


@admin.register(Fee)
class FeeAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "student",
        "category",
        "total_amount",
        "balance",
        "status",
        "due_date",
        "academic_year",
        "semester",
    )
    list_filter = ("status", "academic_year", "semester", "category")
    search_fields = (
        "student__s_studentID",
        "student__s_lname",
        "student__s_fname",
        "category__category_name",
    )
    list_select_related = ("student", "category", "issued_by")
    date_hierarchy = "created_at"
    ordering = ("-updated_at",)
    readonly_fields = ("total_amount", "balance", "gen_fee_batch", "issued_by")


@admin.register(GeneratedFeeBatch)
class GeneratedFeeBatchAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "category",
        "institute",
        "academic_year",
        "semester",
        "total_created_fees",
        "generated_by",
        "created_at",
    )
    list_filter = ("institute", "academic_year", "semester", "category")
    list_select_related = ("category", "institute", "generated_by")
    ordering = ("-created_at",)
