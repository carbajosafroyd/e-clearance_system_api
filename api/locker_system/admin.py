from django.contrib import admin
from .models import Locker, LockerSubscriptionRequest, LockerSubscription, LockerReport


@admin.register(Locker)
class LockerAdmin(admin.ModelAdmin):
    list_display = ("locker_number", "building", "institute", "floor", "is_active")
    list_filter = ("is_active", "building", "institute", "floor")
    search_fields = ("locker_number",)


@admin.register(LockerSubscriptionRequest)
class LockerSubscriptionRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "student", "locker", "academic_year", "semester", "status")
    list_filter = ("status", "academic_year", "semester")
    search_fields = ("student__id", "locker__locker_number")


@admin.register(LockerSubscription)
class LockerSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("id", "student", "locker", "academic_year", "semester", "status", "payment_status", "subscribed_at")
    list_filter = ("status", "payment_status", "academic_year", "semester")
    search_fields = ("student__id", "locker__locker_number")


@admin.register(LockerReport)
class LockerReportAdmin(admin.ModelAdmin):
    list_display = ("id", "locker", "student", "message")
    search_fields = ("locker__locker_number", "student__id")
