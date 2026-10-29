import logging

from rest_framework import viewsets, response, status
from api.audits.models import AuditLog
from api.audits.services import AuditLogService
from api.audits.utils import snapshot_instance
from api.payment_submissions.filters import PaymentSubmissionFilterSet
from api.students.permissions import IsStudent
from api.utils.cloudinary.cloudinary_utils import delete_file
from .models import PaymentSubmission, PaymentSubmissionItems
from .serializers import (
    PaymentSubmissionCreateSerializer,
    PaymentSubmissionReadSerializer,
    PaymentSubmissionUpdateSerializer,
    PaymentSubmissionDeleteSerializer,
    PaymentSubmissionReadSlimSerializer,
    RemarksSubmissionSerializer,
)

from rest_framework.permissions import (
    IsAuthenticated,
    DjangoModelPermissions,
    IsAdminUser,
)
from api.rbac import ActionPermission, crud
from api.utils.custom_paginations import CustomPagination
from core import features, setting_buckets

from drf_spectacular.utils import extend_schema
from django.utils import timezone
from . import services as submission_services
from .permissions import CanEditPaymentSubmissionStatus

from rest_framework.decorators import action
from rest_framework.response import Response

from .notifications import (
    notify_submission_approved,
    notify_submission_cancelled,
    notify_submission_rejected,
)

from django.db.models import Case, When, Value, IntegerField, Prefetch

logger = logging.getLogger(__name__)


def _paying_institute(user):
    """
    Whose switch applies to this submission.

    A student's institute is their program's - that is where the fee lives
    and where the receiving GCash account is. Staff submitting on a
    student's behalf (a rare path, but the endpoint allows it) fall back to
    their own institute.
    """
    student = getattr(user, "student", None)
    program = getattr(student, "program", None)
    if program is not None:
        return program.institute
    return features.resolve_institute(user)


@extend_schema(tags=["Payment Submission"])
class PaymentSubmissionViewSet(viewsets.ModelViewSet):
    # student__program: StudentReadMini reads program.name. The items prefetch
    # serves the retrieve payload (items with fee/category) and lets the slim
    # serializer's fee_payment_count reuse the cache instead of one COUNT per row.
    queryset = (
        PaymentSubmission.objects.select_related("student__program", "reviewed_by")
        .prefetch_related(
            Prefetch(
                "payment_submission_list",
                queryset=PaymentSubmissionItems.objects.select_related("fee__category"),
            )
        )
        .order_by(
            Case(
                When(status=PaymentSubmission.SubmissionStatus.PENDING, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            ),
            "-updated_at",
        )
    )
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = crud(
        "payment_submissions",
        "paymentsubmission",
        approve="payment_submissions.review_paymentsubmission",
        reject="payment_submissions.review_paymentsubmission",
        cancel="payment_submissions.cancel_paymentsubmission",
        withdraw="payment_submissions.withdraw_paymentsubmission",
    )
    pagination_class = CustomPagination

    filterset_class = PaymentSubmissionFilterSet
    # A submission hangs off the student, not a fee - the fee is on its items.
    search_fields = [
        "student__s_fname",
        "student__s_lname",
        "student__s_studentID",
        "reference_number",
    ]

    ordering_fields = ["created_at", "updated_at"]
    # ordering = ["-updated_at"]
    ordering_fields = ["created_at", "updated_at"]

    @extend_schema(
        description=(
            "Create a payment submission. Upload images and set amount paid. "
            "screenshot URLs are auto-generated.\n\n"
            "Refused with 403 while the institute's `allow_online_payments` "
            "setting is off - the collection desk has closed the GCash lane, "
            "and the student portal hides the pay button for the same reason. "
            "Payments recorded at the desk are unaffected."
        ),
        request=PaymentSubmissionCreateSerializer,
    )
    def create(self, request, *args, **kwargs):
        # Checked before the serializer runs, so a closed lane refuses the
        # request without first uploading the screenshot to Cloudinary. The
        # frontends hide the button; this is what makes the switch real for
        # anyone posting directly.
        institute = _paying_institute(request.user)
        if not setting_buckets.online_payments_enabled(institute):
            return Response(
                {
                    "detail": (
                        "Online payments are turned off for your institute. "
                        "Pay at the collection desk instead."
                    ),
                    "code": "online_payments_off",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().create(request, *args, **kwargs)

    def get_serializer_class(self):
        if self.action == "create":
            return PaymentSubmissionCreateSerializer
        elif self.action in ["update", "partial_update"]:
            return PaymentSubmissionUpdateSerializer
        elif self.action == "destroy":
            return PaymentSubmissionDeleteSerializer
        elif self.action == "retrieve":
            return PaymentSubmissionReadSerializer
        return PaymentSubmissionReadSlimSerializer

    def perform_update(self, serializer):
        old_snapshot = snapshot_instance(serializer.instance)
        submission = serializer.save()
        AuditLogService.log_model_update(
            instance=submission,
            old_snapshot=old_snapshot,
            user=self.request.user,
            system=AuditLog.System.COLLECTION,
        )

    def destroy(self, request, *args, **kwargs):
        # An approved submission has payments behind it; deleting it would
        # orphan them (the FK is SET_NULL) and erase the trail cancellation
        # depends on. Cancelled ones ARE that trail. Superuser-only cleanup.
        instance = self.get_object()
        if instance.status in (
            PaymentSubmission.SubmissionStatus.APPROVED,
            PaymentSubmission.SubmissionStatus.CANCELLED,
        ) and not request.user.is_superuser:
            return response.Response(
                {
                    "detail": (
                        "Approved or cancelled submissions cannot be deleted. "
                        "Use the cancel action to undo an approval."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().destroy(request, *args, **kwargs)

    def perform_destroy(self, instance):
        # Items cascade away with the submission, so capture both BEFORE the
        # delete - each item gets its own audit entry alongside the parent's.
        entity_id = instance.pk
        submission_snapshot = snapshot_instance(instance)
        item_snapshots = [
            (item.pk, snapshot_instance(item))
            for item in instance.payment_submission_list.all()
        ]

        for pid in instance.screenshot or []:
            delete_file(pid, version=instance.cloudinary_version)

        instance.delete()

        user = self.request.user
        AuditLogService.log_model_delete(
            entity_type="PaymentSubmission",
            entity_id=entity_id,
            snapshot=submission_snapshot,
            user=user,
            system=AuditLog.System.COLLECTION,
        )
        for item_pk, item_snapshot in item_snapshots:
            AuditLogService.log_model_delete(
                entity_type="PaymentSubmissionItems",
                entity_id=item_pk,
                snapshot=item_snapshot,
                user=user,
                system=AuditLog.System.COLLECTION,
            )

    # def perform_update(self, serializer):
    #     instance = self.get_object()
    #     old_status = instance.status

    #     with transaction.atomic():
    #         updated_instance = serializer.save(
    #             reviewed_by=self.request.user, reviewed_at=timezone.now()
    #         )

    #         if (
    #             old_status != updated_instance.status
    #             and updated_instance.status == "approved"
    #             and not hasattr(updated_instance, "payment")
    #         ):
    #             # fee.balance -= updated_instance.amount_paid
    #             # if fee.balance < 1:
    #             #     fee.status = Fee.FeeStatus.PAID
    #             # fee.save()
    #             # deleted, have to make sure that this process only exist in one action, on CREATE sa payment

    #             serializer = PaymentCreateSerializer(
    #                 data={
    #                     "fee": updated_instance.fee.pk,
    #                     "amount_paid": updated_instance.amount_paid,
    #                     "payment_method": Payment.PaymentMethod.GCASH,
    #                     "payment_submission": updated_instance.pk,
    #                 },
    #                 context={"request": self.request},
    #             )
    #             serializer.is_valid(raise_exception=True)
    #             serializer.save()

    # I Think i should create a code here that automatically creates a payment if the status changed to approved :>
    # and subtracts the payment amount paid to fee total_amount and updates the balance and if the balance is now 0 then the status of the fee now is paid.

    def get_queryset(self):
        user = self.request.user
        if user.is_staff:
            return self.queryset.filter(student__program__institute=user.institute)
        if hasattr(user, "student"):
            return self.queryset.filter(student=user.student)
        return self.queryset.none()

    @extend_schema(request=RemarksSubmissionSerializer)
    @action(
        detail=True,
        methods=["post"],
        permission_classes=[IsAuthenticated, ActionPermission, CanEditPaymentSubmissionStatus],
    )
    def approve(self, request, pk=None):
        submission = self.get_object()
        if submission.status != "pending":
            return Response(
                {"detail": "Only pending submissions can be approved."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        old_snapshot = snapshot_instance(submission)
        remarks = request.data.get("remarks", "")

        # The service re-checks the status under a row lock (two staff
        # clicking approve at once serialize there) and creates the payments
        # through the one balance-mutation path.
        submission, items = submission_services.approve_submission(
            submission=submission, actor=request.user, remarks=remarks
        )

        AuditLogService.log_model_update(
            instance=submission,
            old_snapshot=old_snapshot,
            user=request.user,
            system=AuditLog.System.COLLECTION,
            action=AuditLog.Action.APPROVE,
            extra_new={
                "items": [
                    {
                        "id": item.pk,
                        "fee": item.fee_id,
                        "amount_paid": str(item.amount_paid),
                        "previous_balance": str(item.previous_balance),
                    }
                    for item in items
                ]
            },
        )

        notify_submission_approved(submission=submission, actor=request.user)

        return Response({"detail": "Submission approved successfully."})

    @extend_schema(request=RemarksSubmissionSerializer)
    @action(
        detail=True,
        methods=["post"],
        permission_classes=[IsAuthenticated, ActionPermission, CanEditPaymentSubmissionStatus],
    )
    def reject(self, request, pk=None):
        submission = self.get_object()

        if submission.status != "pending":
            return Response(
                {"detail": "Only pending submissions can be rejected."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        remarks = request.data.get("remarks", "")
        if not remarks:
            return Response(
                {"detail": "Remarks are required to reject a submission."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_snapshot = snapshot_instance(submission)
        submission.status = "rejected"
        submission.reviewed_by = request.user
        # submission.updated_by = request.user add rani after
        submission.reviewed_at = timezone.now()
        submission.remarks = remarks
        submission.save()

        AuditLogService.log_model_update(
            instance=submission,
            old_snapshot=old_snapshot,
            user=request.user,
            system=AuditLog.System.COLLECTION,
            action=AuditLog.Action.REJECT,
        )

        notify_submission_rejected(submission=submission, actor=request.user)

        return Response({"detail": "Submission rejected successfully."})

    @extend_schema(
        request=RemarksSubmissionSerializer,
        description=(
            "Student-only: withdraw your own submission while it is still "
            "pending review. No money has moved, so nothing is reversed. "
            "Remarks are optional. Once a submission has been approved, "
            "only staff can undo it (see the cancel action)."
        ),
    )
    @action(
        detail=True,
        methods=["post"],
        permission_classes=[IsAuthenticated, ActionPermission, IsStudent],
    )
    def withdraw(self, request, pk=None):
        # get_queryset scopes students to their own submissions, so another
        # student's pk 404s here rather than needing an ownership check.
        submission = self.get_object()

        if submission.status != PaymentSubmission.SubmissionStatus.PENDING:
            return Response(
                {
                    "detail": (
                        "Only pending submissions can be withdrawn. Once "
                        "reviewed, please contact the collection office."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_snapshot = snapshot_instance(submission)

        # The service re-checks the status under a row lock, so withdrawing
        # while staff approve resolves to exactly one outcome.
        submission = submission_services.withdraw_submission(
            submission=submission,
            actor=request.user,
            reason=request.data.get("remarks", ""),
        )

        AuditLogService.log_model_update(
            instance=submission,
            old_snapshot=old_snapshot,
            user=request.user,
            system=AuditLog.System.COLLECTION,
            action=AuditLog.Action.CANCEL,
        )

        return Response({"detail": "Submission withdrawn successfully."})

    @extend_schema(
        request=RemarksSubmissionSerializer,
        description=(
            "Cancel an approved submission: every payment it created is "
            "cancelled and each fee's balance restored. Remarks are required. "
            "Rejected and cancelled submissions are terminal."
        ),
    )
    @action(
        detail=True,
        methods=["post"],
        permission_classes=[IsAuthenticated, ActionPermission, CanEditPaymentSubmissionStatus],
    )
    def cancel(self, request, pk=None):
        submission = self.get_object()

        if submission.status != PaymentSubmission.SubmissionStatus.APPROVED:
            return Response(
                {"detail": "Only approved submissions can be cancelled."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        remarks = request.data.get("remarks", "")
        if not remarks:
            return Response(
                {"detail": "Remarks are required to cancel a submission."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_snapshot = snapshot_instance(submission)
        submission, cancelled_payments = submission_services.cancel_submission(
            submission=submission, actor=request.user, reason=remarks
        )

        AuditLogService.log_model_update(
            instance=submission,
            old_snapshot=old_snapshot,
            user=request.user,
            system=AuditLog.System.COLLECTION,
            action=AuditLog.Action.CANCEL,
            extra_new={
                "cancelled_payments": [
                    {
                        "id": payment.pk,
                        "fee": payment.fee_id,
                        "amount_paid": str(payment.amount_paid),
                    }
                    for payment in cancelled_payments
                ]
            },
        )

        notify_submission_cancelled(
            submission=submission,
            cancelled_payments=cancelled_payments,
            actor=request.user,
        )

        return Response({"detail": "Submission cancelled successfully."})
