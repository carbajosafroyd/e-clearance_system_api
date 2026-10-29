import logging

from django.shortcuts import render

from api.audits.models import AuditLog
from api.audits.services import AuditLogService
from api.audits.utils import resolve_actor
from api.collection_categories.excempted_students_serializers import (
    FeeExemptedStudentBulkDeleteResultSerializer,
    FeeExemptedStudentBulkDeleteSerializer,
    FeeExemptedStudentBulkResultSerializer,
    FeeExemptedStudentBulkWriteSerializer,
    FeeExemptedStudentReadSerializer,
    FeeExemptedStudentWriteSerializer,
)
from api.collection_categories.filters import FeeExemptedStudentFilterSet
from api.utils.cloudinary.cloudinary_utils import remove_image
from api.collection_categories.institute_gcash_accounts_serailizers import CreateIntituteGcashAccountSerializer, ReadInsituteGcashSerializer, UpdateInsituteGcashSerializer, DeleteInsituteGcashSerializer
# Create your views here.

from .models import CollectionCategory, FeeExemptedStudent, InstituteGcashAccount
from django.db.models import Count
from api.payment_submissions.models import PaymentSubmission
from api.collection_categories.serializers import (
    CollectionCategoryReadSerializer,
    CollectionCategoryCreateSerializer,
    CollectionCategoryDeleteSerializer,
    CollectionCategoryMiniSerializer
)
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from api.rbac import ActionPermission, crud
from api.rbac.scoping import is_school_wide, scope_queryset
from api.utils.custom_paginations import CustomPagination
from rest_framework.permissions import IsAuthenticated, DjangoModelPermissions


from drf_spectacular.utils import extend_schema
from rest_framework.filters import SearchFilter, OrderingFilter

logger = logging.getLogger(__name__)


@extend_schema(tags=["Collection Category"])
class CollectionCategoryViewSet(viewsets.ModelViewSet):
    # institute__school: the retrieve serializer nests the full institute,
    # whose serializer in turn nests the school.
    queryset = CollectionCategory.objects.select_related("institute__school").order_by("id")
    pagination_class = CustomPagination
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = crud(
        "collection_categories",
        "collectioncategory",
        bulk_create="collection_categories.bulk_add_collectioncategory",
    )

    filterset_fields = [
        "category_name",
    ]
    search_fields = ["category_name"]
    ordering_fields = ["id", "category_name"]

    def get_serializer_class(self):
        if self.action == "create":
            return CollectionCategoryCreateSerializer
        elif self.action == "retrieve":
            return CollectionCategoryReadSerializer
        elif self.action in ["update", "partial_update"]:
            return CollectionCategoryCreateSerializer
        elif self.action == "destroy":
            return CollectionCategoryDeleteSerializer
        return CollectionCategoryMiniSerializer

    def get_queryset(self):
        user = self.request.user

        if user.is_superuser:
            return self.queryset

        if user.is_staff:
            return self.queryset.filter(institute=user.institute)

        return self.queryset.none()

    def perform_create(self, serializer):
        user = self.request.user
        serializer.save(institute=user.institute)

    def get_queryset(self):
        user = self.request.user
        if not user.is_superuser:
            return self.queryset.filter(institute=user.institute)

        return self.queryset

@extend_schema(tags=["Fee Exempted Student"])
class FeeExcemptedStudentViewSet(viewsets.ModelViewSet):
    """
    Which students are excused from which collection category for a term.
    Staff manage their own institute's exemptions; a student account only
    ever sees its own. Bulk endpoints exist for both directions: `POST
    bulk/` to exempt a whole list of students, `POST bulk-delete/` to remove
    a whole selection again.
    """

    # student__program: StudentReadMini reads program.name per row.
    queryset = FeeExemptedStudent.objects.select_related(
        "student__program", "category"
    ).order_by("student__s_lname")
    pagination_class = CustomPagination
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = crud(
        "collection_categories",
        "feeexemptedstudent",
        bulk_create="collection_categories.bulk_add_feeexemptedstudent",
        # The bulk delete is a POST, so without this entry it would be
        # checked against the *add* permission.
        bulk_delete="collection_categories.bulk_delete_feeexemptedstudent",
    )

    filterset_class = FeeExemptedStudentFilterSet
    # The list screen renders full_name = fname + mname + lname + suffix, and
    # SearchFilter requires EVERY typed word to match some field - without
    # mname/suffix here, searching a student by their displayed name returned
    # nothing whenever they had a middle name.
    search_fields = [
        "student__s_fname",
        "student__s_mname",
        "student__s_lname",
        "student__s_suffix",
        "student__s_studentID",
        "category__category_name",
        "reason",
    ]
    ordering_fields = [
        "id",
        "academic_year",
        "semester",
        "status",
        "student__s_lname",
        "category__category_name",
        "created_at",
        "updated_at",
    ]

    def get_serializer_class(self):
        if self.action in ["list", "retrieve"]:
            return FeeExemptedStudentReadSerializer
        if self.action == "bulk_create":
            return FeeExemptedStudentBulkWriteSerializer
        if self.action == "bulk_delete":
            return FeeExemptedStudentBulkDeleteSerializer
        return FeeExemptedStudentWriteSerializer

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return self.queryset
        if user.is_staff:
            return self.queryset.filter(student__program__institute=user.institute)
        if hasattr(user, "student"):
            return self.queryset.filter(student=user.student)
        return self.queryset.none()

    @extend_schema(
        request=FeeExemptedStudentBulkWriteSerializer,
        responses={status.HTTP_201_CREATED: FeeExemptedStudentBulkResultSerializer},
        description=(
            "Exempt many students at once from one category for a term. "
            "Send `student_school_ids` plus the shared `category`, `reason`, "
            "`academic_year` and `semester`. Unknown or out-of-institute "
            "school IDs reject the whole request; students already exempted "
            "for that category/term are skipped and reported, so re-posting "
            "the same list is safe."
        ),
    )
    @action(detail=False, methods=["post"], url_path="bulk", url_name="bulk")
    def bulk_create(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = serializer.save()

        # One audit entry for the whole batch, like GeneratedFeeBatch does -
        # the count and the list are the trail, not 500 identical rows.
        self._log_bulk_action(
            action=AuditLog.Action.CREATE,
            new_values={
                "category_id": serializer.validated_data["category"].pk,
                "academic_year": serializer.validated_data["academic_year"],
                "semester": serializer.validated_data["semester"],
                "reason": serializer.validated_data["reason"],
                "created_count": len(result["created"]),
                "created_student_school_ids": result["created"],
                "skipped_existing": result["skipped_existing"],
            },
        )
        return Response(
            {
                "created_count": len(result["created"]),
                "skipped_count": len(result["skipped_existing"]),
                "created": result["created"],
                "skipped_existing": result["skipped_existing"],
            },
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=FeeExemptedStudentBulkDeleteSerializer,
        responses={status.HTTP_200_OK: FeeExemptedStudentBulkDeleteResultSerializer},
        description=(
            "Remove many exemptions at once by their record ids (the ids the "
            "list endpoint returns). Ids that match nothing in your institute "
            "scope - already deleted, or never yours - are skipped and "
            "reported under `skipped_missing`, so re-posting the same "
            "selection is safe. Requires the *delete* permission on fee "
            "exemptions even though the request is a POST. The whole batch "
            "is written to the audit log as one entry."
        ),
    )
    @action(
        detail=False,
        methods=["post"],
        url_path="bulk-delete",
        url_name="bulk-delete",
    )
    def bulk_delete(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = serializer.validated_data["ids"]

        # get_queryset() carries the institute scoping, so an id belonging to
        # another institute is simply "not found" here - never deletable.
        found = list(self.get_queryset().filter(pk__in=ids))
        found_ids = {row.pk for row in found}

        snapshot = [
            {
                "id": row.pk,
                "student_school_id": row.student.s_studentID,
                "category_id": row.category_id,
                "category_name": row.category.category_name,
                "academic_year": row.academic_year,
                "semester": row.semester,
                "reason": row.reason,
            }
            for row in found
        ]

        FeeExemptedStudent.objects.filter(pk__in=found_ids).delete()

        self._log_bulk_action(
            action=AuditLog.Action.DELETE,
            old_values={
                "deleted_count": len(found_ids),
                "deleted_exemptions": snapshot,
            },
        )
        return Response(
            {
                "deleted_count": len(found_ids),
                "skipped_count": len(ids) - len(found_ids),
                "deleted": [pk for pk in ids if pk in found_ids],
                "skipped_missing": [pk for pk in ids if pk not in found_ids],
            },
            status=status.HTTP_200_OK,
        )

    def _log_bulk_action(self, *, action, old_values=None, new_values=None):
        """
        Best-effort audit write: a failed log line must never turn a
        successful bulk operation into a 500 (same stance as
        AuditLogService.log_model_delete).
        """
        try:
            actor_type, actor_id = resolve_actor(self.request.user)
            AuditLogService.log_action(
                system=AuditLog.System.COLLECTION,
                entity_type="FeeExemptedStudent",
                entity_id="bulk",
                action=action,
                actor_type=actor_type,
                actor_id=actor_id,
                old_values=old_values,
                new_values=new_values,
            )
        except Exception:
            logger.exception("Audit log failed for FeeExemptedStudent bulk %s", action)


@extend_schema(tags=["Intitute Gcash    Account"])
class IntituteGcashAccountViewSet(viewsets.ModelViewSet):
    # institute__school: InstituteReadSlim reads school.id / school.school_name.
    # Ordered: an unordered queryset makes page 2 of a paginated list
    # arbitrary, and this one is paginated.
    queryset = InstituteGcashAccount.objects.select_related(
        "institute__school"
    ).order_by("id")
    pagination_class = CustomPagination
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = crud("collection_categories", "institutegcashaccount")

    # `institute` is only meaningful for a superuser - everybody else is
    # already narrowed to one by `get_queryset`. `status` is the filter the
    # console's page actually needs: "which accounts are switched off?".
    filterset_fields = ["status", "institute"]
    search_fields = ["hint", "number"]
    ordering_fields = ["id", "hint", "status", "created_at", "updated_at"]

    def perform_destroy(self, instance):
        # The QR image was uploaded for this row alone. The update
        # serializer already deletes the old file on replace; this is the
        # matching half for delete, which used to leave the image behind.
        remove_image(instance.gcash_qr, version=instance.cloudinary_version)
        super().perform_destroy(instance)

    def get_serializer_class(self):
        if self.action == "list" or self.action == "retrieve":
            return ReadInsituteGcashSerializer
        elif self.action == "create":
            return CreateIntituteGcashAccountSerializer
        elif self.action in ["update", "partial_update"]:
            return UpdateInsituteGcashSerializer
        elif self.action == "destroy":
            return DeleteInsituteGcashSerializer
        return ReadInsituteGcashSerializer


    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return self.queryset
        # The receiving accounts are the institute's own business: which
        # wallet students are told to send money to is decided at the desk,
        # and a school administrator has no part in it. They see nothing
        # here rather than every institute's wallets - the console hides the
        # page from them for the same reason.
        if is_school_wide(user):
            return self.queryset.none()
        if user.is_staff:
            return scope_queryset(
                self.queryset,
                user,
                institute_path="institute",
                school_paths=("institute__school",),
            )
        # Anyone else who belongs to an institute - students, and accounts in
        # the Students group that have no Student row yet. Resolve through the
        # student's program first, since that is where a student's institute
        # really lives, then fall back to the account's own institute.
        #
        # Never `student=`: this model has no such field, and filtering on it
        # raised FieldError - a 500 on every student request.
        institute_id = getattr(
            getattr(getattr(getattr(user, "student", None), "program", None), "institute", None),
            "id",
            None,
        ) or getattr(user, "institute_id", None)

        if institute_id is None:
            return self.queryset.none()

        # Only AVAILABLE accounts. Offering somebody an account that is full or
        # disabled is asking for money to be sent where nobody is watching.
        return self.queryset.filter(
            institute_id=institute_id,
            status=InstituteGcashAccount.GcashAccountStatus.AVAILABLE,
        )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["times_used"] = self._times_used()
        return context

    def _times_used(self) -> dict[str, int]:
        """
        Approved submissions per wallet number for the requesting student.

        The wallet a student has paid into before is usually the one they
        want again, so the picker says how often. Approved only: a pending
        or rejected one is not money that arrived. Keyed by number rather
        than account row, since a submission records the number it was
        told, not the account id.

        Counted here in Python rather than as a correlated subquery: the
        two tables' string columns carry different collations on the
        production MySQL, and comparing them inside one query raised
        "Illegal mix of collations". One grouped query and a dict lookup
        is the same answer on every database.
        """
        student = getattr(self.request.user, "student", None)
        if student is None:
            return {}
        counts = (
            PaymentSubmission.objects.filter(
                student=student,
                status=PaymentSubmission.SubmissionStatus.APPROVED,
                gcash_number__isnull=False,
            )
            .order_by()
            .values("gcash_number")
            .annotate(n=Count("id"))
        )
        return {row["gcash_number"]: row["n"] for row in counts}
    