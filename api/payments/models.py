from django.db import models
from core.BaseModel import ActorStampedModel
from django.conf import settings
from api.fees.models import Fee


class Payment(ActorStampedModel):
    """Money received against a fee.

    Payment rows are immutable ledger entries: an encoding mistake is
    corrected by cancelling the payment (which restores the fee's balance),
    never by editing the amount or deleting the row. `previous_balance`
    records the fee balance at the moment the money was applied.
    """

    class PaymentMethod(models.TextChoices):
        CASH = "cash", "Cash"
        GCASH = "gcash", "GCash"

    class PaymentStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        CANCELLED = "cancelled", "Cancelled"

    fee = models.ForeignKey(Fee, on_delete=models.CASCADE, related_name="payments")
    previous_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount_paid = models.DecimalField(
        max_digits=12, decimal_places=2, null=False, blank=False
    )
    payment_method = models.CharField(
        max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.CASH
    )
    # payment_submission = models.OneToOneField(
    #     "payment_submissions.PaymentSubmission",
    #     on_delete=models.SET_NULL,
    #     null=True,
    #     blank=True,
    #     related_name="payment",
    # )
    payment_submission = models.ForeignKey(
        "payment_submissions.PaymentSubmission",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments",
    )
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="received_payments",
    )

    status = models.CharField(
        max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.ACTIVE
    )
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_payments",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "payments"
        permissions = [
            ("cancel_payment", "Can cancel a payment and restore the balance"),
            ("bulk_add_payment", "Can record payments in bulk"),
        ]
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["fee", "-created_at"]),
            models.Index(fields=["received_by", "-created_at"]),
            models.Index(fields=["payment_method", "created_at"]),
        ]
        constraints = [
            # Zero and negative payments are nonsense rows; the serializers
            # already refuse them, this makes the database refuse them too.
            models.CheckConstraint(
                condition=models.Q(amount_paid__gt=0),
                name="payment_amount_paid_positive",
            ),
        ]

    def __str__(self):
        return f"Payment {self.id} - {self.amount_paid} ({self.payment_method})"
