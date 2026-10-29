"""
The email-change flow, kept out of the views.

The views own HTTP — status codes, throttles, what a caller is allowed to
see. Everything about *what the change does* lives here, including the two
emails and the audit entry, so the confirmation link (an unauthenticated GET
that renders HTML) and the API steps can't drift apart.
"""

import hmac
import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from core.services.email_branding import email_brand
from core.services.email_sender import email_sender
from core.services.send_email_v2 import send_email_async

from .models import EmailChangeRequest
from .utils import (
    CONFIRM_EXPIRY_MINUTES,
    MAX_OTP_ATTEMPTS,
    OTP_EXPIRY_MINUTES,
    change_token_expiry,
    confirm_expiry,
    confirmation_link,
    format_moment,
    generate_otp_code,
    generate_token,
    hash_value,
    otp_expiry,
)

logger = logging.getLogger(__name__)
User = get_user_model()


class EmailChangeError(Exception):
    """Something the caller can act on; the message is safe to show them."""

    def __init__(self, message: str, *, code: str = "invalid"):
        super().__init__(message)
        self.message = message
        self.code = code


def active_request(user) -> EmailChangeRequest | None:
    """The one request still in flight for this user, if any."""
    return (
        EmailChangeRequest.objects.filter(
            user=user, stage__in=EmailChangeRequest.OPEN_STAGES
        )
        .order_by("-created_at")
        .first()
    )


def cancel_open_requests(user, *, exclude_pk=None) -> int:
    """
    Only one change may be in flight at a time. Starting a new one retires
    the old, so an abandoned attempt can't be finished later with a link
    sitting in an inbox the user has since moved on from.
    """
    queryset = EmailChangeRequest.objects.filter(
        user=user, stage__in=EmailChangeRequest.OPEN_STAGES
    )
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)

    return queryset.update(
        stage=EmailChangeRequest.Stage.CANCELLED,
        cancelled_at=timezone.now(),
        updated_at=timezone.now(),
    )


def _account_context(user) -> dict:
    full_name = (user.get_full_name() or "").strip()
    return {
        **email_brand(user),
        "account_username": user.username,
        "account_name": full_name or None,
    }


def _initiator_name(request_row) -> str | None:
    """The administrator who opened the request, for the email; None for self-service."""
    actor = request_row.initiated_by
    if actor is None:
        return None
    return (actor.get_full_name() or actor.username).strip() or None


def pending_request(user) -> EmailChangeRequest | None:
    """The change waiting on the new inbox, if any - whoever started it."""
    return (
        EmailChangeRequest.objects.select_related("initiated_by")
        .filter(user=user, stage=EmailChangeRequest.Stage.AWAITING_CONFIRMATION)
        .order_by("-created_at")
        .first()
    )


# --------------------------------------------------------------------------
# Step 1 - code to the current address
# --------------------------------------------------------------------------


def start_request(user) -> EmailChangeRequest:
    current_email = (user.email or "").lower().strip()
    if not current_email:
        raise EmailChangeError(
            "This account has no email address on file, so there is nothing to "
            "verify against. Ask an administrator to set one first.",
            code="no_current_email",
        )

    cancel_open_requests(user)

    code = generate_otp_code()
    request_row = EmailChangeRequest.objects.create(
        user=user,
        current_email=current_email,
        code_hash=hash_value(code),
        code_expires_at=otp_expiry(),
        max_attempts=MAX_OTP_ATTEMPTS,
    )

    send_email_async(
        subject="Confirm your email change",
        to_email=current_email,
        template_name="email_change_otp",
        context={
            **_account_context(user),
            "code": code,
            "expiry_minutes": OTP_EXPIRY_MINUTES,
            "current_email": current_email,
            "requested_at": format_moment(request_row.created_at),
        },
        sender=email_sender(user),
    )

    return request_row


# --------------------------------------------------------------------------
# Step 2 - verify the code
# --------------------------------------------------------------------------


def verify_code(user, code: str) -> tuple[EmailChangeRequest, str]:
    """Returns (request, raw change token), or raises EmailChangeError."""
    request_row = (
        EmailChangeRequest.objects.filter(
            user=user, stage=EmailChangeRequest.Stage.CODE_SENT
        )
        .order_by("-created_at")
        .first()
    )

    if request_row is None or request_row.code_is_expired():
        raise EmailChangeError("Invalid or expired code.", code="invalid_code")

    if request_row.code_is_locked():
        raise EmailChangeError(
            "Too many attempts. Start the email change again.", code="locked"
        )

    request_row.attempts += 1
    request_row.save(update_fields=["attempts", "updated_at"])

    if not hmac.compare_digest(request_row.code_hash, hash_value(code)):
        raise EmailChangeError("Invalid or expired code.", code="invalid_code")

    raw_token = generate_token()
    request_row.stage = EmailChangeRequest.Stage.CODE_VERIFIED
    request_row.verified_at = timezone.now()
    request_row.change_token_hash = hash_value(raw_token)
    request_row.change_token_expires_at = change_token_expiry()
    request_row.save(
        update_fields=[
            "stage",
            "verified_at",
            "change_token_hash",
            "change_token_expires_at",
            "updated_at",
        ]
    )

    return request_row, raw_token


# --------------------------------------------------------------------------
# Step 3 - name the new address, mail it a link
# --------------------------------------------------------------------------


def set_new_email(user, change_token: str, new_email: str):
    request_row = EmailChangeRequest.objects.filter(
        user=user,
        change_token_hash=hash_value(change_token),
        stage=EmailChangeRequest.Stage.CODE_VERIFIED,
    ).first()

    if (
        request_row is None
        or request_row.change_token_is_used()
        or request_row.change_token_is_expired()
    ):
        raise EmailChangeError(
            "This step has expired. Start the email change again.",
            code="invalid_change_token",
        )

    # Re-checked here as well as in the serializer: the address could have
    # been claimed between the two calls.
    if User.objects.filter(email__iexact=new_email).exclude(pk=user.pk).exists():
        raise EmailChangeError(
            "That email address is already used by another account.",
            code="email_taken",
        )

    raw_confirm_token = generate_token()
    now = timezone.now()

    request_row.new_email = new_email
    request_row.stage = EmailChangeRequest.Stage.AWAITING_CONFIRMATION
    request_row.change_token_used_at = now
    request_row.confirm_token_hash = hash_value(raw_confirm_token)
    request_row.confirm_expires_at = confirm_expiry()
    request_row.confirm_sent_at = now
    request_row.save(
        update_fields=[
            "new_email",
            "stage",
            "change_token_used_at",
            "confirm_token_hash",
            "confirm_expires_at",
            "confirm_sent_at",
            "updated_at",
        ]
    )

    _send_confirmation(user, request_row, raw_confirm_token)
    return request_row


def resend_confirmation(user) -> EmailChangeRequest:
    """
    Re-issue the link for a change already waiting on the new address, with
    a fresh token so the previous mail stops working.
    """
    request_row = EmailChangeRequest.objects.filter(
        user=user, stage=EmailChangeRequest.Stage.AWAITING_CONFIRMATION
    ).first()

    if request_row is None:
        raise EmailChangeError(
            "There's no email change waiting for confirmation.", code="no_pending"
        )

    already_taken = (
        User.objects.filter(email__iexact=request_row.new_email)
        .exclude(pk=user.pk)
        .exists()
    )
    if already_taken:
        request_row.cancel()
        raise EmailChangeError(
            "That email address is now used by another account. Start again "
            "with a different address.",
            code="email_taken",
        )

    raw_confirm_token = generate_token()
    request_row.confirm_token_hash = hash_value(raw_confirm_token)
    request_row.confirm_expires_at = confirm_expiry()
    request_row.confirm_sent_at = timezone.now()
    request_row.confirm_resends += 1
    request_row.save(
        update_fields=[
            "confirm_token_hash",
            "confirm_expires_at",
            "confirm_sent_at",
            "confirm_resends",
            "updated_at",
        ]
    )

    _send_confirmation(user, request_row, raw_confirm_token)
    return request_row


def _send_confirmation(user, request_row, raw_token: str):
    send_email_async(
        subject="Confirm your new email address",
        to_email=request_row.new_email,
        template_name="email_change_confirm",
        context={
            **_account_context(user),
            "link": confirmation_link(raw_token),
            "expiry_minutes": CONFIRM_EXPIRY_MINUTES,
            "current_email": request_row.current_email,
            "new_email": request_row.new_email,
            "requested_at": format_moment(request_row.created_at),
            # Who asked. Named so the reader of the new inbox can tell a
            # request their administrator made for them from one they
            # never asked for.
            "initiated_by": _initiator_name(request_row),
        },
        sender=email_sender(user),
    )


# --------------------------------------------------------------------------
# Started by an administrator - straight to the link in the new inbox
# --------------------------------------------------------------------------


def start_admin_request(target, new_email: str, *, actor) -> EmailChangeRequest:
    """
    An administrator moves somebody else's account to a new address.

    The self-service flow proves two things: that the person asking still
    reads the CURRENT address (the code), and that they read the NEW one
    (the link). This exists for the case where the first proof is
    impossible - the old mailbox is lost, which is exactly why the person
    walked up to an administrator. So the code step is skipped and the
    administrator's authority stands in for it. The link is kept: an
    address nobody has proven they read must never become the account's,
    or an administrator's typo would lock the person out for good.

    Any change already in flight for the account is cancelled first, so a
    link sitting in an inbox the person has since abandoned cannot finish
    later. Calling this again with the same address is how "resend" works -
    a fresh token, and the previous mail stops working.
    """
    new_email = (new_email or "").lower().strip()
    if User.objects.filter(email__iexact=new_email).exclude(pk=target.pk).exists():
        raise EmailChangeError(
            "That email address is already used by another account.",
            code="email_taken",
        )

    cancel_open_requests(target)

    raw_confirm_token = generate_token()
    now = timezone.now()
    request_row = EmailChangeRequest.objects.create(
        user=target,
        initiated_by=actor,
        stage=EmailChangeRequest.Stage.AWAITING_CONFIRMATION,
        current_email=(target.email or "").lower().strip(),
        new_email=new_email,
        max_attempts=MAX_OTP_ATTEMPTS,
        confirm_token_hash=hash_value(raw_confirm_token),
        confirm_expires_at=confirm_expiry(),
        confirm_sent_at=now,
    )

    _send_confirmation(target, request_row, raw_confirm_token)
    _audit_started(target, actor, request_row)
    return request_row


def _audit_started(target, actor, request_row):
    """
    The trail records WHO pointed the account at WHICH address, when the
    link went out - before anything on the account has changed. The change
    itself is logged by `confirm`, with the owner's click as the actor.
    """
    try:
        from api.audits.models import AuditLog
        from api.audits.services import AuditLogService
        from api.audits.utils import resolve_actor

        actor_type, actor_id = resolve_actor(actor)
        AuditLogService.log_action(
            system=AuditLog.System.PLATFORM,
            entity_type="UserEmail",
            entity_id=target.id,
            action=AuditLog.Action.UPDATE,
            actor_type=actor_type,
            actor_id=actor_id,
            old_values={"email": request_row.current_email},
            new_values={
                "pending_email": request_row.new_email,
                "confirmation_sent": True,
            },
        )
    except Exception:
        logger.exception(
            "Could not write audit log for administrator email change on user %s",
            target.id,
        )


# --------------------------------------------------------------------------
# Step 4 - the link in the new inbox
# --------------------------------------------------------------------------


def confirm(raw_token: str) -> EmailChangeRequest:
    """
    Apply the change. Everything is re-checked here because this runs
    unauthenticated, minutes or hours after the steps that led to it.
    """
    if not raw_token:
        raise EmailChangeError(
            "This confirmation link is missing its token.", code="missing_token"
        )

    request_row = (
        EmailChangeRequest.objects.select_related("user")
        .exclude(confirm_token_hash="")
        .filter(confirm_token_hash=hash_value(raw_token))
        .first()
    )

    if request_row is None:
        # A completed change clears its token, so a second click on the same
        # link lands here rather than on a stale row.
        raise EmailChangeError(
            "This confirmation link is invalid, or has already been used.",
            code="invalid_token",
        )

    if request_row.stage != EmailChangeRequest.Stage.AWAITING_CONFIRMATION:
        raise EmailChangeError(
            "This email change was cancelled or replaced by a newer one.",
            code="not_pending",
        )

    if request_row.confirmation_is_expired():
        request_row.cancel()
        raise EmailChangeError("This confirmation link has expired.", code="expired")

    user = request_row.user
    if not user.is_active:
        raise EmailChangeError(
            "This account is no longer active.", code="inactive_account"
        )

    new_email = request_row.new_email
    previous_email = request_row.current_email or user.email

    with transaction.atomic():
        taken = (
            User.objects.select_for_update()
            .filter(email__iexact=new_email)
            .exclude(pk=user.pk)
            .exists()
        )
        if taken:
            request_row.cancel()
            raise EmailChangeError(
                "That email address is already used by another account.",
                code="email_taken",
            )

        user.email = new_email
        # The address is proven by this very click, so it counts as verified.
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email", "email_verified_at"])

        request_row.stage = EmailChangeRequest.Stage.COMPLETED
        request_row.confirmed_at = timezone.now()
        request_row.confirm_token_hash = ""
        request_row.save(
            update_fields=[
                "stage",
                "confirmed_at",
                "confirm_token_hash",
                "updated_at",
            ]
        )

        cancel_open_requests(user, exclude_pk=request_row.pk)

    _notify_previous_address(user, request_row, previous_email)
    _audit(user, previous_email, new_email)

    return request_row


def _notify_previous_address(user, request_row, previous_email: str):
    """
    The old inbox gets the last word. If the change wasn't the owner's doing,
    this is the only place they can find out.
    """
    if not previous_email or previous_email.lower() == (user.email or "").lower():
        return

    send_email_async(
        subject="The email on your account was changed",
        to_email=previous_email,
        template_name="email_changed_notice",
        context={
            **_account_context(user),
            "current_email": previous_email,
            "new_email": user.email,
            "changed_at": format_moment(request_row.confirmed_at or timezone.now()),
        },
        sender=email_sender(user),
    )


def _audit(user, previous_email: str, new_email: str):
    """Bookkeeping must never undo a change that already landed."""
    try:
        from api.audits.models import AuditLog
        from api.audits.services import AuditLogService
        from api.audits.utils import resolve_actor

        actor_type, actor_id = resolve_actor(user)
        AuditLogService.log_action(
            system=AuditLog.System.PLATFORM,
            entity_type="User",
            entity_id=user.id,
            action=AuditLog.Action.UPDATE,
            actor_type=actor_type,
            actor_id=actor_id,
            old_values={"email": previous_email},
            new_values={"email": new_email},
        )
    except Exception:
        logger.exception(
            "Could not write audit log for email change on user %s", user.id
        )
