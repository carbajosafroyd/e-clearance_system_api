from django.db import models
from core.BaseModel import ActorStampedModel, TimeStampedModel
from django.conf import settings


class PaymentSubmission(ActorStampedModel):
    class SubmissionStatus(models.TextChoices):
        PENDING = "pending", "Pending Review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        # Approved-then-undone. Cancelling reverses the submission's payments
        # and restores each fee's balance; rejected and cancelled are terminal.
        CANCELLED = "cancelled", "Cancelled"
        # Pending-then-recalled by the student before anyone reviewed it. No
        # money ever moved, so there is nothing to reverse; terminal like
        # rejected/cancelled. The cancellation fields double as the
        # withdrawal record - cancelled_by is the student's own user.
        WITHDRAWN = "withdrawn", "Withdrawn"

    student = models.ForeignKey(
        "students.Student", on_delete=models.CASCADE, related_name="payment_submissions"
    )
    # fee = models.ForeignKey(
    #     "fees.Fee", on_delete=models.CASCADE, related_name="payment_submissions"
    # )
    # screenshot = models.ImageField(
    #     upload_to="payment_screenshots/", null=True, blank=True
    # )  # screenshot_path
    screenshot = models.JSONField(default=list, null=False, blank=False)
    #: The Cloudinary account version the screenshots (all uploaded together) was uploaded under - see
    #: CLOUDINARY_ACCOUNTS in settings. Null means "not known yet": the
    #: helpers look it up across accounts on first use.
    cloudinary_version = models.PositiveSmallIntegerField(null=True, blank=True)

    #: The collecting account this money was sent *to*, recorded as typed at
    #: the time. Deliberately a plain string and not a FK to
    #: InstituteGcashAccount: a deployment that has not enabled the account
    #: picker sends whatever number its frontend was built with, and an
    #: account that is later retired or renumbered must not rewrite the
    #: history of what a student was told to pay. Null on rows created before
    #: the field existed - that is "not captured", not "sent to nowhere".
    gcash_number = models.CharField(max_length=11, null=True, blank=True)

    # max_digits matches Fee/Payment (12): a submission covering several fees
    # can easily total more than the 9,999.99 that max_digits=6 capped at.
    total_amount_paid = models.DecimalField(
        max_digits=12, decimal_places=2, null=False, blank=False
    )
    reference_number = models.CharField(max_length=100, null=False, blank=False)
    status = models.CharField(
        max_length=20,
        choices=SubmissionStatus.choices,
        default=SubmissionStatus.PENDING,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_submissions",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    remarks = models.TextField(null=True, blank=True)

    # Cancellation is recorded separately from the review: reviewed_by /
    # reviewed_at / remarks keep the approval as history, these say who undid
    # it, when, and why.
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_submissions",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "payment_submissions"
        permissions = [
            ("review_paymentsubmission", "Can approve or reject a submission"),
            ("cancel_paymentsubmission", "Can cancel an approved submission"),
            ("withdraw_paymentsubmission", "Can withdraw one's own pending submission"),
        ]
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["student", "-updated_at"]),
            # models.Index(fields=["fee", "status"]),
            models.Index(fields=["reference_number"]),
        ]

    def __str__(self):
        return f"Submission {self.id} - {self.student.s_studentID} ({self.status})"


class PaymentSubmissionItems(TimeStampedModel):
    """One fee's slice of a submission.

    Deliberately has no status of its own: an item is cancelled exactly when
    its submission is cancelled, so the state lives on the parent and cannot
    drift. Serializers that need a per-item status echo the parent's.
    """

    payment_submission = models.ForeignKey(
        PaymentSubmission,
        on_delete=models.CASCADE,
        related_name="payment_submission_list",
    )
    fee = models.ForeignKey(
        "fees.Fee", on_delete=models.CASCADE, related_name="payment_submissions"
    )
    # max_digits matches Fee/Payment (12) - see PaymentSubmission.total_amount_paid.
    previous_balance = models.DecimalField(
        max_digits=12, decimal_places=2, null=False, blank=False
    )
    amount_paid = models.DecimalField(
        max_digits=12, decimal_places=2, null=False, blank=False
    )

    class Meta:
        db_table = "payment_submission_items"
        ordering = ["-updated_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount_paid__gt=0),
                name="submission_item_amount_paid_positive",
            ),
        ]

    def __str__(self):
        return f"List {self.id} - {self.payment_submission} ({self.fee})"
