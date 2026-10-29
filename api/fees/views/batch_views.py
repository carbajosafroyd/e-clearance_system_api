import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from api.audits.models import AuditLog
from api.audits.services import AuditLogService
from api.audits.utils import snapshot_instance
from api.rbac import ActionPermission, crud
from api.utils import custom_paginations

from .. import cache as fee_cache
from ..constants import ATTENDANCE_CATEGORY_NAME
from ..filters import GeneratedFeeBatchFilterSet
from ..models import Fee, GeneratedFeeBatch
from ..serializers import (
    GenerateAttendanceFees,
    GenerateFees,
    GenerationAcceptedSerializer,
    ReadGeneratedFeeBatch,
)
from ..services import (
    GenerationRequest,
    start_attendance_generation,
    start_general_generation,
)
from ..services.generation import notifications

logger = logging.getLogger(__name__)

ATTENDANCE_GENERATION_DOC = """
**Attendance fee generation**

Calculates and issues fines from attendance records, relative to what each day
of the event actually required.

### Behaviour
* **Audience:** enrolled students in your institute only.
* **Skips:** students who already have a fee for this event.
* **Exemptions:** with `exempt_students=true`, students holding an active
  exemption for the Attendance category get a WAIVED fee instead of a billed
  one, carrying the exemption reason as the remark.
* **Dynamic fining:** a period flag set to `true` on the day's settings is
  mandatory - a missing record or empty timestamp is charged. A flag set to
  `false` is ignored entirely, whatever the student did.
* **Nothing owed, nothing raised:** a student who missed no required period
  gets no fee row at all, rather than a 0.00 fee that would later fall overdue.
* **Batching:** every fee created is tagged with the generated batch id.

### Notes
* A student with no record at all on a mandatory day is charged for every
  required period that day.
* Make sure each date in the event range has an `InstituteAttendanceEventSetting`
  before running - days without one are treated as requiring nothing.
* The work runs in the background; this endpoint returns 202 immediately and
  the result arrives as a notification.
"""

GENERAL_GENERATION_DOC = """
**Collection fee generation**

Issues one flat `collection_fee` per student for the given category, academic
year and semester.

* **Audience:** enrolled students in your institute, optionally narrowed by
  `year_levels`.
* **Skips:** students who already have a fee for this category and term.
* **Exemptions:** with `exempt_students=true`, exempted students get a WAIVED
  fee carrying the exemption reason instead of a billed one.
* `Attendance` and `Locker` are rejected here - they have their own flows.
* The work runs in the background; this endpoint returns 202 immediately and
  the result arrives as a notification.
"""


@extend_schema(tags=["Generated Fee Batch"])
class GeneratedFeeBatchViewSet(viewsets.ModelViewSet):
    queryset = GeneratedFeeBatch.objects.select_related(
        "category", "institute", "generated_by", "updated_by"
    ).order_by("-updated_at")

    pagination_class = custom_paginations.CustomPagination
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = crud(
        "fees",
        "generatedfeebatch",
        generate_fees="fees.generate_fees",
        generate_attendance_fees="fees.generate_attendance_fees",
    )

    # The list cache keys on the full query string (see fees/cache.py), so
    # every filter/search/ordering combination gets its own entry - filters
    # and caching compose without further work here.
    filterset_class = GeneratedFeeBatchFilterSet
    search_fields = [
        "category__category_name",
        "generated_by__username",
        "academic_year",
    ]
    ordering_fields = [
        "id",
        "created_at",
        "updated_at",
        "total_created_fees",
        "academic_year",
        "semester",
        "category__category_name",
    ]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        if user.is_superuser:
            return queryset

        institute_id = getattr(user, "institute_id", None)
        if not institute_id:
            return queryset.none()
        return queryset.filter(institute_id=institute_id)

    def list(self, request, *args, **kwargs):
        """
        Cached per caller and per query string; see `fees/cache.py`.

        Batches are historical records that only change when a generation run
        finishes or a batch is deleted, so this is the cache with the longest
        useful life in the app.
        """
        key = fee_cache.build_key(
            fee_cache.BATCH_NS, fee_cache.request_parts(request, "batch-list")
        )

        data = fee_cache.read(key)
        if data is None:
            data = super().list(request, *args, **kwargs).data
            fee_cache.write(key, data)
        return Response(data)

    def get_serializer_class(self):
        if self.action == "generate_attendance_fees":
            return GenerateAttendanceFees
        if self.action == "generate_fees":
            return GenerateFees
        return ReadGeneratedFeeBatch

    # Batches are created by the generation actions below, and are a historical
    # record once written - so the default write routes are closed off.
    def create(self, request, *args, **kwargs):
        return Response(
            {"detail": "Method 'POST' not allowed. Use the generation actions."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def update(self, request, *args, **kwargs):
        return Response(
            {"detail": "Method 'PUT' not allowed."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def partial_update(self, request, *args, **kwargs):
        return Response(
            {"detail": "Method 'PATCH' not allowed."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    @extend_schema(
        description=(
            "Deletes the batch **and every fee it generated**. Refused if any "
            "of those fees has already been paid."
        )
    )
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    def perform_destroy(self, instance):
        fees = Fee.objects.filter(gen_fee_batch=instance)

        # Fee.gen_fee_batch cascades, so deleting the batch would take paid
        # fees - and the payment history hanging off them - with it.
        paid_count = fees.filter(status=Fee.FeeStatus.PAID).count()
        if paid_count:
            raise ValidationError(
                {
                    "detail": (
                        f"This batch has {paid_count} paid fee(s) and cannot be "
                        f"deleted. Void or reassign those fees first."
                    )
                }
            )

        entity_id = instance.pk
        snapshot = snapshot_instance(instance)
        fee_count = fees.count()

        logger.info(
            "fee_batch.delete batch=%s fees=%s actor=%s",
            entity_id,
            fee_count,
            self.request.user.username,
        )
        instance.delete()  # cascades to the batch's fees

        # One entry for the batch with the cascade size, rather than one row
        # per deleted fee - a batch can hold hundreds of fees and the count
        # is what matters for the trail.
        AuditLogService.log_model_delete(
            entity_type="GeneratedFeeBatch",
            entity_id=entity_id,
            snapshot=snapshot,
            user=self.request.user,
            system=AuditLog.System.COLLECTION,
            extra_old={"deleted_fee_count": fee_count},
        )

    @extend_schema(
        description=ATTENDANCE_GENERATION_DOC,
        responses={status.HTTP_202_ACCEPTED: GenerationAcceptedSerializer},
    )
    @action(methods=["post"], detail=False, url_path="generate-attendance-fees")
    def generate_attendance_fees(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # The serializer resolved this to the event instance, already checked
        # against the caller's institute.
        event = data["institute_attendance_event_id"]

        notifications.notify_queued(
            actor=request.user,
            label=ATTENDANCE_CATEGORY_NAME,
            academic_year=event.academic_year,
            semester=event.semester,
        )
        start_attendance_generation(
            user_id=request.user.id,
            institute_attendance_event_id=event.pk,
            request=_generation_request(data),
        )

        return Response(
            {"detail": "Attendance fee generation has started in the background."},
            status=status.HTTP_202_ACCEPTED,
        )

    @extend_schema(
        description=GENERAL_GENERATION_DOC,
        responses={status.HTTP_202_ACCEPTED: GenerationAcceptedSerializer},
    )
    @action(detail=False, methods=["post"], url_path="generate_fees")
    def generate_fees(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # The serializer resolved the name to a CollectionCategory in the
        # caller's institute.
        category = data["category_name"]

        notifications.notify_queued(
            actor=request.user,
            label=category.category_name,
            academic_year=data["academic_year"],
            semester=data["semester"],
        )
        start_general_generation(
            user_id=request.user.id,
            category_id=category.pk,
            academic_year=data["academic_year"],
            semester=data["semester"],
            request=_generation_request(data),
        )

        return Response(
            {"detail": "Fee generation has started in the background."},
            status=status.HTTP_202_ACCEPTED,
        )


def _generation_request(data: dict) -> GenerationRequest:
    """Flatten validated payload to the plain-value object the worker takes."""
    return GenerationRequest(
        due_date=data["due_date"],
        all_students=data.get("all_students", True),
        year_levels=tuple(data.get("year_levels") or ()),
        # `exempt_students` on the wire means "honour the exemption records".
        apply_exemptions=data.get("exempt_students", False),
    )
