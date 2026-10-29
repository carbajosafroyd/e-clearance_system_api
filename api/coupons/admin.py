from django.contrib import admin

from .models import Coupon, CouponBatch, CouponClaim, CouponRedemption


class CouponInline(admin.TabularInline):
    model = Coupon
    extra = 0
    fields = ("code", "student", "uses_taken", "max_redemptions", "status")
    readonly_fields = ("code",)
    show_change_link = True


@admin.register(CouponBatch)
class CouponBatchAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "institute",
        "value_type",
        "value",
        "total_created",
        "status",
        "expires_at",
    )
    list_filter = ("status", "kind", "value_type", "institute")
    search_fields = ("name", "reason")
    filter_horizontal = ("categories",)
    inlines = [CouponInline]


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ("code", "batch", "student", "uses_taken", "status")
    list_filter = ("status", "batch")
    search_fields = ("code", "student__s_studentID")
    # Editing a code by hand would orphan whatever was printed with it.
    readonly_fields = ("code", "uses_taken")


@admin.register(CouponRedemption)
class CouponRedemptionAdmin(admin.ModelAdmin):
    list_display = ("coupon", "student", "fee", "amount", "status", "applied_at")
    list_filter = ("status",)
    search_fields = ("coupon__code", "student__s_studentID")
    # The undo record. Hand-editing it would leave the fee and the audit
    # trail disagreeing about what happened.
    readonly_fields = ("previous_discount_total", "previous_balance", "amount")


@admin.register(CouponClaim)
class CouponClaimAdmin(admin.ModelAdmin):
    list_display = ("coupon", "student", "created_at")
    search_fields = ("coupon__code", "student__s_studentID")
