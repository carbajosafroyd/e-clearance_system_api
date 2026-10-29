from django.contrib import admin

from .models import Contribution, FeeDiscount, PointAccount, PointPolicy, PointTransaction


@admin.register(PointPolicy)
class PointPolicyAdmin(admin.ModelAdmin):
    list_display = ("institute", "system", "peso_per_point", "is_active")
    list_filter = ("system", "is_active")


@admin.register(Contribution)
class ContributionAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "student", "system", "kind", "status", "points_awarded")
    list_filter = ("system", "kind", "status", "impact")
    search_fields = ("title", "student__s_studentID", "student__s_lname")
    readonly_fields = ("points_awarded", "reviewed_at")


@admin.register(PointAccount)
class PointAccountAdmin(admin.ModelAdmin):
    list_display = ("student", "system", "balance", "total_earned", "total_spent")
    list_filter = ("system",)
    search_fields = ("student__s_studentID", "student__s_lname")
    # The ledger is the source of truth; editing a balance by hand would
    # silently break `balance == earned - spent`.
    readonly_fields = ("balance", "total_earned", "total_spent")


@admin.register(PointTransaction)
class PointTransactionAdmin(admin.ModelAdmin):
    list_display = ("id", "account", "delta", "balance_after", "reason", "created_at")
    list_filter = ("reason",)
    # Append-only: corrections are new rows, never edits.
    readonly_fields = tuple(
        field.name for field in PointTransaction._meta.fields
    )

    def has_add_permission(self, request):
        return False


@admin.register(FeeDiscount)
class FeeDiscountAdmin(admin.ModelAdmin):
    list_display = ("id", "student", "fee", "system", "points_spent", "amount", "status")
    list_filter = ("system", "status")
    search_fields = ("student__s_studentID", "student__s_lname")
    readonly_fields = ("previous_total_amount", "previous_balance", "reviewed_at")
