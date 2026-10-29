from django.shortcuts import render

from rest_framework.permissions import IsAuthenticated
from api.rbac import ActionPermission, crud
from api.utils import custom_paginations
from .models import AttendanceEvent
from rest_framework import viewsets

from api.attendance_events.serializers import (
    AttendanceEventCreateSerializer,
    AttendanceEventReadSerializer,
    AttendanceEventReadSlimSerializer,
    AttendanceEventDeleteSerializer,
    AttendanceEventUpdateSerializer,
)

from drf_spectacular.utils import extend_schema

# from api.attendance_events.filters import AttendanceEventFilter


@extend_schema(tags=["Attendance Event"])
class AttendanceEventViewSet(viewsets.ModelViewSet):
    queryset = AttendanceEvent.objects.all().order_by("id")
    pagination_class = custom_paginations.CustomPagination
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = crud("attendance_events", "attendanceevent")

    # AVOID MUNA HANTUD SA WA PA NA MIGRATE ANG BAG O :<
    # filterset_class = AttendanceEventFilter
    # search_fields = ['school__school_name']
    # ordering_fields = ['id', 'start_date', 'end_date']

    def get_serializer_class(self):
        if self.action == "create":
            return AttendanceEventCreateSerializer
        elif self.action == "retrieve":
            return AttendanceEventReadSerializer
        elif self.action in ["update", "partial_update"]:
            return AttendanceEventUpdateSerializer
        elif self.action == "destroy":
            return AttendanceEventDeleteSerializer
        return AttendanceEventReadSlimSerializer
