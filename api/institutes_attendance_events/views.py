from django.shortcuts import render
from rest_framework import viewsets
from drf_spectacular.utils import extend_schema

from api.serializers.ApiResponse import PaginatedDataSerializer
from api.utils.custom_paginations import CustomPagination
from rest_framework.permissions import IsAuthenticated

from api.rbac import ActionPermission, crud

from .models import InstituteAttendanceEvent

from .serializers import (
    InstituteAttendanceEventReadSerializer,
    InstituteAttendanceEventReadSlimSerializer,
    InstituteAttendanceEventCreateSerializer,
    InstituteAttendanceEventUpdateSerializer,
    InstituteAttendanceEventDeleteSerializer,
)


@extend_schema(tags=["Institute Attendnace Event"])
class InstituteAttendanceEventViewSet(viewsets.ModelViewSet):
    # attendance_event feeds every read serializer; institute__school feeds
    # the detailed one (InstituteReadSlim renders school columns).
    queryset = InstituteAttendanceEvent.objects.select_related(
        "institute__school", "attendance_event"
    ).order_by("id")
    pagination_class = CustomPagination
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = crud(
        "institutes_attendance_events", "instituteattendanceevent"
    )

    @extend_schema(responses=PaginatedDataSerializer)
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def get_serializer_class(self):
        if self.action == "create":
            return InstituteAttendanceEventCreateSerializer
        elif self.action == "retrieve":
            return InstituteAttendanceEventReadSerializer
        elif self.action in ["update", "partial_update"]:
            return InstituteAttendanceEventUpdateSerializer
        elif self.action == "destroy":
            return InstituteAttendanceEventDeleteSerializer
        return InstituteAttendanceEventReadSlimSerializer
    
    def perform_create(self, serializer):
        serializer.save(institute=self.request.user.institute)

    def get_queryset(self):
        user = self.request.user
        
        if user.is_staff or hasattr(user, 'student'):
            return self.queryset.filter(institute=user.institute)
        return self.queryset.none()
    
