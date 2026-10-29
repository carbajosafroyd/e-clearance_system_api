from api.locker_system.serializers import (
    LockerReportCreateSerializer,
    LockerReportsReadSerializer,
    LockerReportsReadSlimSerializer,
)
from api.rbac import ActionPermission, crud
from api.utils.custom_paginations import CustomPagination
from api.locker_system.models import LockerReport
from drf_spectacular.utils import extend_schema
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

@extend_schema(tags=["Locker Reports"])
class LockerReportsViewSet(viewsets.ModelViewSet):
    # The read serializers nest the student (program, and user on retrieve)
    # and the locker (building/institute, each rendering their school).
    queryset = LockerReport.objects.select_related(
        "student__program",
        "student__user",
        "locker__building__school",
        "locker__institute__school",
    ).order_by("-created_at")
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = crud("locker_system", "lockerreport")
    pagination_class = CustomPagination
    ordering_fields = ["id"]

    def get_serializer_class(self):
        if self.action == 'create':
            return LockerReportCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return LockerReportCreateSerializer
        elif self.action == "retrieve":
            return LockerReportsReadSerializer
        else:
            return LockerReportsReadSlimSerializer
        
    def get_queryset(self):
        user = self.request.user

        if user.is_superuser:
            return self.queryset
        
        if user.is_staff:
            return self.queryset.filter(student__program__institute=user.institute)
        if hasattr(user, "student"):
            return self.queryset.filter(student=user.student)
        
        else:
            return self.queryset.none()