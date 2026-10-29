from django.conf import settings
from django.db import models
from django.utils import timezone


class EmailChangeRequest(models.Model):
    """
    One row per attempt to move an account to a different email address.

    The whole flow lives on this row because it is strictly linear and every
    step has to be able to see the ones before it:

        code_sent -> code_verified -> awaiting_confirmation -> completed

    Two secrets guard it, and neither is ever stored in the clear. The OTP
    proves the person asking still reads the *current* address; the
    confirmation link proves they read the *new* one. Only opening that link
    writes to ``User.email`` — an address nobody could prove they own never
    becomes an account's address, which is what stops an email change from
    being a way to steal an account.

    Rows are kept after they finish: "when did this account's email change,
    and from what" is exactly the question an audit asks later.

    ## The administrator's shortcut

    A row with `initiated_by` set was opened by an administrator on somebody
    else's account and starts at ``awaiting_confirmation``: there is no
    code, because the reason the person came to an administrator is that
    the old mailbox is gone and a code sent there would never be read. The
    link to the new address is kept - see `services.start_admin_request`.
    """

    class Stage(models.TextChoices):
        CODE_SENT = "code_sent", "Code sent"
        CODE_VERIFIED = "code_verified", "Code verified"
        AWAITING_CONFIRMATION = "awaiting_confirmation", "Awaiting confirmation"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="email_change_requests",
    )
    # The administrator who opened the request on the owner's behalf; null
    # for the owner's own self-service change. Named in the confirmation
    # email so the reader can judge whether the request is legitimate.
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    stage = models.CharField(
        max_length=24, choices=Stage.choices, default=Stage.CODE_SENT
    )

    # Snapshotted, not read through the FK: after the change lands, the
    # user's email is the new one and this row still has to say what it
    # replaced.
    current_email = models.EmailField()
    new_email = models.EmailField(blank=True, default="")

    # Step 1/2 — OTP to the current address. Blank on an administrator-
    # started request, which has no code step.
    code_hash = models.CharField(max_length=128, blank=True, default="")
    code_expires_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    verified_at = models.DateTimeField(null=True, blank=True)

    # Handed to the client once the OTP checks out; spent submitting the new
    # address, so a verified session can't be replayed for a second change.
    change_token_hash = models.CharField(
        max_length=128, blank=True, default="", db_index=True
    )
    change_token_expires_at = models.DateTimeField(null=True, blank=True)
    change_token_used_at = models.DateTimeField(null=True, blank=True)

    # Step 2/2 — the link mailed to the new address.
    confirm_token_hash = models.CharField(
        max_length=128, blank=True, default="", db_index=True
    )
    confirm_expires_at = models.DateTimeField(null=True, blank=True)
    confirm_sent_at = models.DateTimeField(null=True, blank=True)
    confirm_resends = models.PositiveSmallIntegerField(default=0)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    cancelled_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "email_change_requests"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "stage"]),
            models.Index(fields=["new_email"]),
            models.Index(fields=["created_at"]),
        ]

    # -- stage predicates ---------------------------------------------------

    OPEN_STAGES = (
        Stage.CODE_SENT,
        Stage.CODE_VERIFIED,
        Stage.AWAITING_CONFIRMATION,
    )

    @property
    def is_open(self) -> bool:
        """Still somewhere in the flow — not completed and not cancelled."""
        return self.stage in self.OPEN_STAGES

    def code_is_expired(self) -> bool:
        # No code was ever issued (an administrator-started request) reads
        # as expired: nothing can be verified against it.
        return self.code_expires_at is None or timezone.now() > self.code_expires_at

    def code_is_locked(self) -> bool:
        return self.attempts >= self.max_attempts

    def change_token_is_expired(self) -> bool:
        return (
            self.change_token_expires_at is None
            or timezone.now() > self.change_token_expires_at
        )

    def change_token_is_used(self) -> bool:
        return self.change_token_used_at is not None

    def confirmation_is_expired(self) -> bool:
        return (
            self.confirm_expires_at is None or timezone.now() > self.confirm_expires_at
        )

    def cancel(self, *, save: bool = True):
        self.stage = self.Stage.CANCELLED
        self.cancelled_at = timezone.now()
        if save:
            self.save(update_fields=["stage", "cancelled_at", "updated_at"])
        return self

    def __str__(self):
        target = self.new_email or "(no address yet)"
        return f"EmailChangeRequest({self.current_email} -> {target}, {self.stage})"
