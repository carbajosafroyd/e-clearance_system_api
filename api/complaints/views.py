"""
The complaints API: `/api/v1/complaints/`.

One resource, because a complaint is one thing with a short life: a student
files it, an officer answers it, the student is told. Everything else is a
filter on the same list.

Every action is named in `required_permissions`; anything not listed is
refused. Row scoping is `get_queryset()` plus `IsOwnComplaintOrInstituteStaff`
- a permission says what kind of thing you may do, never whose row you may do
it to.

The whole module is optional (`FEATURE_COMPLAINTS`) and independent of the
contributions scheme: an institute that pays nobody for anything still owes
its students an answer about a fee they should not have been charged.
"""

from __future__ import annotations

import logging

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from api.audits.models import AuditLog
from api.audits.services import AuditLogService
from api.audits.utils import snapshot_instance
from api.rbac import DEFAULT, ActionPermission, crud, gate
from api.utils.custom_paginations import CustomPagination
from core import features

from . import selectors, services
from .filters import ComplaintFilterSet
from .models import Complaint
from .permissions import IsOwnComplaintOrInstituteStaff
from .serializers import (
    ComplaintReadSerializer,
    ComplaintResponseSerializer,
    ComplaintSlimSerializer,
    ComplaintSummarySerializer,
    ComplaintWithdrawSerializer,
    ComplaintWriteSerializer,
)

logger = logging.getLogger(__name__)

SYSTEM_PARAM = OpenApiParameter(
    name="system",
    description="Restrict to one system (collection, locker, attendance, …).",
    required=False,
    type=str,
)


def _student_of(user):
    """The Student behind a user account, or None for staff."""
    return getattr(user, "student", None)


def _institute_scope(request):
    """The institute a caller's figures should cover, or None for everything."""
    if request.user.is_superuser:
        return None
    return getattr(request.user, "institute_id", None)


@extend_schema(tags=["Complaints"])
class ComplaintViewSet(viewsets.ModelViewSet):
    """Student complaints and the staff workflow that answers them."""

    queryset = Complaint.objects.select_related(
        "student__program", "related_fee__category", "handled_by"
    ).order_by("-created_at")

    pagination_class = CustomPagination
    permission_classes = [
        IsAuthenticated,
        ActionPermission,
        IsOwnComplaintOrInstituteStaff,
    ]
    required_permissions = gate(
        features.COMPLAINTS,
        crud(
            "complaints",
            "complaint",
            acknowledge="complaints.resolve_complaint",
            resolve="complaints.resolve_complaint",
            reject="complaints.resolve_complaint",
            withdraw="complaints.withdraw_complaint",
            mine="complaints.view_complaint",
            summary="complaints.view_all_complaints",
        ),
    )

    filterset_class = ComplaintFilterSet
    search_fields = [
        "title",
        "description",
        "student__s_fname",
        "student__s_lname",
        "student__s_studentID",
    ]
    ordering_fields = ["created_at", "updated_at", "handled_at", "status"]

    def get_serializer_class(self):  # type: ignore
        if self.action in ("create", "update", "partial_update"):
            return ComplaintWriteSerializer
        if self.action == "retrieve":
            return ComplaintReadSerializer
        return ComplaintSlimSerializer

    def get_queryset(self):  # type: ignore
        # Schema generation runs this with an AnonymousUser.
        if getattr(self, "swagger_fake_view", False):
            return Complaint.objects.none()

        user = self.request.user
        queryset = super().get_queryset()

        if user.is_superuser:
            return queryset

        student = _student_of(user)
        if student is not None:
            return queryset.filter(student=student)

        institute_id = getattr(user, "institute_id", None)
        if not institute_id:
            return queryset.none()
        return queryset.filter(student__program__institute_id=institute_id)

    def perform_create(self, serializer):
        student = _student_of(self.request.user)
        if student is None:
            raise ValidationError(
                {
                    "detail": (
                        "Only students file complaints. Staff answer them; "
                        "they do not file them on a student's behalf."
                    )
                }
            )
        serializer.save(student=student, updated_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    def _audit(self, complaint, old_snapshot, request, action=None):
        AuditLogService.log_model_update(
            instance=complaint,
            old_snapshot=old_snapshot,
            user=request.user,
            system=AuditLog.System.PLATFORM,
            **({"action": action} if action else {}),
        )

    def _respond(self, request, pk, handler, *, audit_action=None):
        """Shared body for resolve/reject: same shape, different verb."""
        complaint = self.get_object()
        serializer = ComplaintResponseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        old_snapshot = snapshot_instance(complaint)
        complaint = handler(
            complaint=complaint,
            actor=request.user,
            response=serializer.validated_data["response"],
        )
        self._audit(complaint, old_snapshot, request, action=audit_action)

        return Response(
            ComplaintReadSerializer(complaint, context={"request": request}).data
        )

    @extend_schema(
        request=None,
        responses=ComplaintReadSerializer,
        description=(
            "Tell the student somebody has picked it up. Not an answer - it "
            "exists so a complaint does not sit there looking ignored while "
            "it is being checked."
        ),
    )
    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        complaint = self.get_object()
        old_snapshot = snapshot_instance(complaint)
        complaint = services.acknowledge_complaint(
            complaint=complaint, actor=request.user
        )
        self._audit(complaint, old_snapshot, request)
        return Response(
            ComplaintReadSerializer(complaint, context={"request": request}).data
        )

    @extend_schema(
        request=ComplaintResponseSerializer,
        responses=ComplaintReadSerializer,
        description=(
            "Close it as handled. The response is required and is sent to "
            "the student in full - say what was actually done, not 'noted'."
        ),
    )
    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        return self._respond(request, pk, services.resolve_complaint)

    @extend_schema(
        request=ComplaintResponseSerializer,
        responses=ComplaintReadSerializer,
        description=(
            "Close it without action. Still requires a reason, and still "
            "notifies the student: a complaint that vanishes is how somebody "
            "learns not to file the next one."
        ),
    )
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return self._respond(
            request, pk, services.reject_complaint, audit_action=AuditLog.Action.REJECT
        )

    @extend_schema(
        request=ComplaintWithdrawSerializer,
        responses=ComplaintReadSerializer,
        description="Take back your own complaint, while nobody has answered it yet.",
    )
    @action(detail=True, methods=["post"])
    def withdraw(self, request, pk=None):
        complaint = self.get_object()

        student = _student_of(request.user)
        if student is None or complaint.student_id != student.pk:
            return Response(
                {"detail": "Only the student who filed it can withdraw it."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = ComplaintWithdrawSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        complaint = services.withdraw_complaint(
            complaint=complaint,
            actor=request.user,
            reason=serializer.validated_data.get("reason", ""),
        )
        return Response(
            ComplaintReadSerializer(complaint, context={"request": request}).data
        )

    @extend_schema(responses=ComplaintSlimSerializer(many=True))
    @action(detail=False, methods=["get"])
    def mine(self, request):
        """Everything the signed-in student has filed."""
        student = _student_of(request.user)
        if student is None:
            return Response([])

        queryset = self.filter_queryset(self.get_queryset().filter(student=student))
        page = self.paginate_queryset(queryset)
        if page is not None:
            return self.get_paginated_response(
                ComplaintSlimSerializer(page, many=True).data
            )
        return Response(ComplaintSlimSerializer(queryset, many=True).data)

    @extend_schema(
        parameters=[SYSTEM_PARAM],
        responses=ComplaintSummarySerializer,
        description=(
            "Queue figures: how many are open, how they were settled, and "
            "the per-system and per-category breakdowns. Systems are "
            "zero-filled so a quiet one still appears."
        ),
    )
    @action(detail=False, methods=["get"])
    def summary(self, request):
        return Response(
            ComplaintSummarySerializer(
                selectors.complaint_summary(
                    institute_id=_institute_scope(request),
                    system=request.query_params.get("system") or None,
                )
            ).data
        )
