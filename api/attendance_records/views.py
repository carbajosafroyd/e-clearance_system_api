import csv
from datetime import datetime
from django.shortcuts import render
from rest_framework.response import Response

from api.attendance_records.serializers import (
    AttendanceRecordCreateSerializer,
    AttendanceRecordDeleteSerializer,
    AttendanceRecordReadSerializer,
    AttendanceRecordReadSlimSerializer,
    AttendanceRecordUpdateSerializer,
    AttendanceRecordsImportFileSerializer,
    AttendanceRecordsBulkDeleteeSerializer,
)
from api.attendance_records.services.attendance_import_service import (
    AttendanceImportService,
)
from api.institutes_attendance_events.models import InstituteAttendanceEvent
from api.students.models import Student
from api.utils.clean import clean_row
from .models import AttendanceRecord
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from api.rbac import ActionPermission, crud
from api.utils import custom_paginations

from drf_spectacular.utils import extend_schema
from rest_framework.filters import SearchFilter, OrderingFilter

from .filters import AttendanceRecordFilterSet
from django.db import transaction
from django.core.exceptions import ValidationError


@extend_schema(tags=["Attendance Record"])
class AttendanceRecordViewSet(viewsets.ModelViewSet):
    # The read serializers nest the student (program, and user on retrieve),
    # the event (whose serializer renders attendance_event), and the fee
    # (whose FeeReadMini renders its own student.program and category).
    queryset = AttendanceRecord.objects.select_related(
        "student__program",
        "student__user",
        "institute_attendance_event__attendance_event",
        "fee__student__program",
        "fee__category",
    ).order_by("id")
    pagination_class = custom_paginations.CustomPagination
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = crud(
        "attendance_records",
        "attendancerecord",
        import_attendance_records="attendance_records.import_attendancerecord",
        bulk_delete_records="attendance_records.bulk_delete_attendancerecord",
    )

    filterset_class = AttendanceRecordFilterSet
    search_fields = [
        "student__s_fname",
        "student__s_lname",
        "student__s_studentID",
        "student__s_rfid",
    ]
    # ordering_fields = ['id']

    def get_serializer_class(self):
        if self.action == "create":
            return AttendanceRecordCreateSerializer
        elif self.action == "retrieve":
            return AttendanceRecordReadSerializer
        elif self.action in ["update", "partial_update"]:
            return AttendanceRecordUpdateSerializer
        elif self.action == "destroy":
            return AttendanceRecordDeleteSerializer
        elif self.action == "import_attendance_records":
            return AttendanceRecordsImportFileSerializer
        elif self.action == "bulk_delete_records":
            return AttendanceRecordsBulkDeleteeSerializer
        return AttendanceRecordReadSlimSerializer

    def get_queryset(self):
        user = self.request.user

        if user.is_superuser:
            return self.queryset

        if user.is_staff:
            return self.queryset.filter(
                institute_attendance_event__institute=user.institute
            )

        if hasattr(user, "student"):
            return self.queryset.filter(student=user.student)

        return self.queryset.none()

    @action(detail=False, methods=["post"])
    def import_attendance_records(self, request):

        try:
            service = AttendanceImportService(
                user=request.user,
                file=request.FILES.get("file"),
                event_id=request.data.get("institute_attendance_event"),
            )

            result = service.execute()
            return Response(result)

        except ValidationError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    # PLESA E CHECK NAKU IF SURE BA NGA SCHOOL ID AND RFID SYNC IF CREATING ATTENDANCE RECORDS ON THAT STUDENT

    @action(detail=False, methods=["post"])
    def bulk_delete_records(self, request):
        event_id = request.data.get("institute_attendance_event")

        if not event_id:
            return Response(
                {"detail": "Event ID required."}, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            event = InstituteAttendanceEvent.objects.get(
                id=event_id, institute=request.user.institute
            )
        except InstituteAttendanceEvent.DoesNotExist:
            return Response(
                {"detail": "Invalid event or unauthorized."},
                status=status.HTTP_403_FORBIDDEN,
            )

        deleted_count, _ = AttendanceRecord.objects.filter(
            institute_attendance_event=event
        ).delete()

        return Response(
            {
                "detail": "Successfully deleted attendance records.",
                "deleted_count": deleted_count,
            }
        )
