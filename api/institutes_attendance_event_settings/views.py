from django.shortcuts import render

# Create your views here.

from api.institutes_attendance_event_settings.serializers import (
    InstituteAttendanceEventSettingReadSerializer,
    InstituteAttendanceEventSettingReadSlimSerializer,
    InstituteAttendanceEventSettingCreateSerializer,
    InstituteAttendanceEventSettingUpdateSerializer,
    InstituteAttendanceEventSettingDeleteSerializer,
)

from .models import InstituteAttendanceEventSetting
from rest_framework.permissions import IsAuthenticated
from api.rbac import ActionPermission, crud
from api.utils.custom_paginations import CustomPagination
from rest_framework import viewsets

from drf_spectacular.utils import extend_schema
from .filters import AttendanceEventSettingsFilter

from ..serializers.ApiResponse import PaginatedDataSerializer


@extend_schema(tags=["Institute Attendnace Event Settings"])
class InsituteEventSettingViewSet(viewsets.ModelViewSet):
    # The slim serializer reads institute_attendance_event.attendance_event
    # .event_name on every row.
    queryset = InstituteAttendanceEventSetting.objects.select_related(
        "institute_attendance_event__attendance_event"
    ).order_by("id")
    pagination_class = CustomPagination
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = crud(
        "institutes_attendance_event_settings", "instituteattendanceeventsetting"
    )

    # filterset_class = AttendanceEventSettingsFilter
    # ordering_fields = ["id"]

    @extend_schema(responses=PaginatedDataSerializer)
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def get_serializer_class(self):
        if self.action == "create":
            return InstituteAttendanceEventSettingCreateSerializer
        elif self.action == "retrieve":
            return InstituteAttendanceEventSettingReadSerializer
        elif self.action in ["update", "partial_update"]:
            return InstituteAttendanceEventSettingUpdateSerializer
        elif self.action == "destroy":
            return InstituteAttendanceEventSettingDeleteSerializer
        return InstituteAttendanceEventSettingReadSlimSerializer
    
    @extend_schema(description='Send Something like this "checkin_start": "07:00:00", "afternoon_checkout_end": "18:00:00" then leave others null if no attendance for that period')
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    def get_queryset(self):
        user = self.request.user

        if user.is_staff:
            return self.queryset.filter(
                institute_attendance_event__institute=user.institute
            )
        return self.queryset.none()
