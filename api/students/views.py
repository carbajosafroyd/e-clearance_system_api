import csv
import io
from django.utils import timezone
from django.db.models import Case, IntegerField, Value, When

from django.shortcuts import render
from django.db import transaction

# Create your views here.

from rest_framework import viewsets

from rest_framework.decorators import action

from rest_framework.permissions import IsAuthenticated, DjangoModelPermissions

from api.utils.clean import clean_row

from .models import Student
from api.students.serializers import (
    StudentReadSlimSerializer,
    StudentCreateSerializer,
    StudentImportSerializer,
    StudentImportFileSerializer,
    StudentDeleteSerializer,
    StudentReadSerializer,
)
from api.rbac import ActionPermission, crud
from api.institutes.models import Institute
from api.rbac.scoping import institute_in_scope, is_student_only, scope_queryset
from api.utils.custom_paginations import CustomPagination

from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes

from .filters import StudentFilterSet
from .spreadsheet import build_import_template, build_student_workbook, import_students, parse_student_file
from api.audits.models import AuditLog
from api.audits.services import AuditLogService
from api.audits.utils import resolve_actor
from django.http import HttpResponse
import re
from rest_framework.response import Response

from django.db.models import Sum

from rest_framework.serializers import ValidationError
from rest_framework import status

from api.programs.models import Program


def _acting_institute(request):
    """
    The institute a spreadsheet action (export, import, template) works on.

    A workbook belongs to ONE institute - its Programs sheet, its status
    sheets - so the action needs to know which. Institute staff have exactly
    one and it is used. An account that sees more than one (a school
    administrator, a superuser) names it with `institute=<id>`; the console
    asks them with a picker. Naming one outside the caller's scope is
    refused, not silently replaced.
    """
    user = request.user
    requested = request.query_params.get("institute") or request.data.get("institute")

    if requested not in (None, ""):
        institute = Institute.objects.filter(pk=requested).first()
        if institute is None or not institute_in_scope(user, institute):
            raise ValidationError({"institute": "That institute is not in your scope."})
        return institute

    if user.institute_id is not None:
        return user.institute

    raise ValidationError(
        {
            "institute": (
                "Name the institute this file is for (`institute=<id>`): your "
                "account is not attached to one."
            )
        }
    )


@extend_schema(tags=["Student"])
class StudentViewSet(viewsets.ModelViewSet):
    # program + user: the slim list serializer reads program.name and nests the
    # user block on every row.
    queryset = Student.objects.select_related("program", "user").order_by("s_lname")
    pagination_class = CustomPagination  # list actions use this
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = crud(
        "students",
        "student",
        update_students="students.import_students",
        import_template="students.import_students",
        # Reading is the whole requirement for an export: the file contains
        # nothing the list on screen does not already show.
        export="students.view_student",
    )

    filterset_class = StudentFilterSet
    search_fields = [
        "id",
        "s_rfid",
        "s_studentID",
        "s_suffix",
        "s_lname",
        "s_mname",
        "s_fname",
        # "s_email",  to be rmove
    ]
    ordering_fields = [
        "id",
        "s_studentID",
        "s_lvl",
        "s_set",
        "s_status",
        "s_lname",
        "s_fname",
        "program__name",
        "created_at",
        "updated_at",
    ]

    def get_serializer_class(self):
        if self.action == "create":
            return StudentCreateSerializer
        elif self.action == "retrieve":
            return StudentReadSerializer
        elif self.action in ["update", "partial_update"]:
            return StudentCreateSerializer
        elif self.action == "destroy":
            return StudentDeleteSerializer
        return StudentReadSlimSerializer

    def get_queryset(self):
        user = self.request.user
        queryset = self.queryset

        if self.action == "retrieve":
            # The detailed serializer digs further: user.institute.school,
            # user.school and program.institute.school. Joined only here so
            # the list query stays lean.
            queryset = queryset.select_related(
                "program__institute__school",
                "user__institute__school",
                "user__school",
            )

        if is_student_only(user):
            return queryset.filter(pk=user.student.pk)

        # Own institute for institute staff, the whole school for a school
        # administrator, everything for a superuser - see
        # `api/rbac/scoping.py`.
        return scope_queryset(
            queryset,
            user,
            institute_path="program__institute",
            school_paths=("program__institute__school",),
        )

    def perform_create(self, serializer):
        user = self.request.user
        program = serializer.validated_data["program"]

        if not institute_in_scope(user, program.institute):
            raise ValidationError(
                {"program": "You cannot assign a program outside your institute."}
            )

        serializer.save()

    @extend_schema(
        request=StudentImportFileSerializer,
        summary="Import or update students from a spreadsheet",
        description=(
            "Upload an Excel workbook (.xlsx - the layout the export and the "
            "template produce: one sheet per status, `student_id`, `last_name`, "
            "`first_name`, `program` as the acronym, `year_level`...) or a CSV "
            "with the same columns. Rows are matched on `student_id` within the "
            "caller's institute: an existing student is UPDATED, a new one is "
            "CREATED, and an id that belongs to another institute is refused. "
            "Answers 200 whatever happened; read the per-row `created`, "
            "`updated` (with what changed), `unchanged` and `failed` lists. A "
            "student whose record changed and who has an account is notified "
            "of the change."
        ),
    )
    @action(detail=False, methods=["post"], url_path="import-update-students")
    def update_students(self, request):
        try:
            institute = _acting_institute(request)
        except ValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)

        uploaded = request.FILES.get("file")
        if not uploaded:
            return Response({"detail": "No file provided."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            rows = parse_student_file(uploaded)
        except ValueError as exc:
            return Response({"file": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)

        result = import_students(rows, institute, request.user)

        actor_type, actor_id = resolve_actor(request.user)
        AuditLogService.log_action(
            system=AuditLog.System.PLATFORM,
            entity_type="StudentImport",
            entity_id=institute.pk,
            action=AuditLog.Action.UPDATE,
            actor_type=actor_type,
            actor_id=actor_id,
            old_values=None,
            new_values={
                "file": getattr(uploaded, "name", ""),
                "created": result["created_count"],
                "updated": result["updated_count"],
                "unchanged": result["unchanged_count"],
                "failed": result["errors_count"],
            },
        )

        return Response(result, status=status.HTTP_200_OK)

    @extend_schema(
        summary="Export students as an Excel workbook",
        description=(
            "The same filters, search and ordering as the list. One sheet per "
            "status plus a Summary and the institute's Programs; the file can be "
            "edited and uploaded back to `import-update-students/`."
        ),
        responses={(200, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"): OpenApiTypes.BINARY},
    )
    @action(detail=False, methods=["get"], url_path="export")
    def export(self, request):
        try:
            institute = _acting_institute(request)
        except ValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)

        # The workbook is one institute's (its Programs sheet, its status
        # sheets), so the rows are narrowed to it whatever else the filters
        # say - a school administrator exporting "everything" gets one file
        # per institute, never a mixed one the import could not take back.
        students = self.filter_queryset(self.get_queryset()).filter(
            program__institute=institute
        )
        workbook = build_student_workbook(students, institute, request.user)

        stamp = timezone.localtime().strftime("%Y-%m-%d_%H%M")
        slug = re.sub(r"[^a-z0-9]+", "-", institute.institute_name.lower()).strip("-") or "institute"
        response = HttpResponse(
            workbook,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="students_{slug}_{stamp}.xlsx"'
        return response

    @extend_schema(
        summary="Download the blank student import workbook",
        responses={(200, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"): OpenApiTypes.BINARY},
    )
    @action(detail=False, methods=["get"], url_path="import-template")
    def import_template(self, request):
        try:
            institute = _acting_institute(request)
        except ValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)
        response = HttpResponse(
            build_import_template(institute),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = 'attachment; filename="student-import-template.xlsx"'
        return response
