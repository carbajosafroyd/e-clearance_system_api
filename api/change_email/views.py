"""
Changing the email address on your own account.

Four steps, and the split between them is the point:

  1. ``POST request/``   - code to the address currently on the account
  2. ``POST verify/``    - code in, one-time ``change_token`` out
  3. ``POST set-new/``   - token + the new address; a link is mailed *there*
  4. ``GET  confirm/``   - opening that link is what actually changes it

Steps 1-3 need the caller's access token; step 4 cannot, because it is
opened from an inbox. Nothing is written to ``User.email`` before step 4:
until someone proves they read the new address, the change does not exist.

``status/`` lets the UI pick the flow back up after a refresh, and
``cancel/`` abandons it.
"""

from django.shortcuts import render
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
    extend_schema,
    inline_serializer,
)

from django.conf import settings
from core.services.email_branding import email_brand

from . import services
from .serializers import (
    EmailChangeSetNewSerializer,
    EmailChangeStatusSerializer,
    EmailChangeVerifySerializer,
)
from .utils import (
    CONFIRM_EXPIRY_MINUTES,
    CHANGE_TOKEN_EXPIRY_MINUTES,
    OTP_EXPIRY_MINUTES,
    mask_email,
)


def _error_response(error: services.EmailChangeError, status_code: int):
    return Response(
        {"detail": error.message, "code": error.code}, status=status_code
    )


@extend_schema(tags=["Email Change"])
class RequestEmailChangeView(APIView):
    """Step 1: send a verification code to the account's current email."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "email_change_request"

    @extend_schema(
        summary="Step 1: Send a code to the current email",
        description=(
            "Sends a one-time code to the address currently on the account and "
            "opens an email-change request. Any earlier request still in flight "
            "is cancelled."
        ),
        request=None,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="EmailChangeRequestSuccess",
                    fields={
                        "detail": serializers.CharField(),
                        "sent_to": serializers.CharField(
                            help_text="Masked current email, e.g. j•••n@gmail.com"
                        ),
                        "expires_in_minutes": serializers.IntegerField(),
                    },
                ),
                description="Code sent",
            ),
            400: OpenApiResponse(description="The account has no email on file"),
        },
    )
    def post(self, request):
        try:
            request_row = services.start_request(request.user)
        except services.EmailChangeError as error:
            return _error_response(error, status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "detail": "We sent a verification code to your current email address.",
                "sent_to": mask_email(request_row.current_email),
                "expires_in_minutes": OTP_EXPIRY_MINUTES,
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(tags=["Email Change"])
class VerifyEmailChangeCodeView(APIView):
    """Step 2: exchange the code for a one-time change token."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "email_change_verify"

    @extend_schema(
        summary="Step 2: Verify the code",
        request=EmailChangeVerifySerializer,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="EmailChangeVerifySuccess",
                    fields={
                        "change_token": serializers.CharField(),
                        "expires_in_minutes": serializers.IntegerField(),
                    },
                ),
                description="Code accepted",
            ),
            400: OpenApiResponse(description="Invalid or expired code"),
            429: OpenApiResponse(description="Too many attempts"),
        },
    )
    def post(self, request):
        serializer = EmailChangeVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            _, change_token = services.verify_code(
                request.user, serializer.validated_data["code"]  # type: ignore[index]
            )
        except services.EmailChangeError as error:
            code_status = (
                status.HTTP_429_TOO_MANY_REQUESTS
                if error.code == "locked"
                else status.HTTP_400_BAD_REQUEST
            )
            return _error_response(error, code_status)

        return Response(
            {
                "change_token": change_token,
                "expires_in_minutes": CHANGE_TOKEN_EXPIRY_MINUTES,
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(tags=["Email Change"])
class SetNewEmailView(APIView):
    """Step 3: name the new address; the confirmation link goes there."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "email_change_request"

    @extend_schema(
        summary="Step 3: Submit the new email address",
        description=(
            "Records the new address and mails a one-time confirmation link to "
            "it. The account's email is **not** changed until that link is "
            "opened."
        ),
        request=EmailChangeSetNewSerializer,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="EmailChangeSetNewSuccess",
                    fields={
                        "detail": serializers.CharField(),
                        "new_email": serializers.EmailField(),
                        "expires_in_minutes": serializers.IntegerField(),
                    },
                ),
                description="Confirmation link sent to the new address",
            ),
            400: OpenApiResponse(
                description="Expired step, or the address is already in use"
            ),
        },
    )
    def post(self, request):
        serializer = EmailChangeSetNewSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)

        try:
            request_row = services.set_new_email(
                request.user,
                serializer.validated_data["change_token"],  # type: ignore[index]
                serializer.validated_data["new_email"],  # type: ignore[index]
            )
        except services.EmailChangeError as error:
            return _error_response(error, status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "detail": (
                    "We sent a confirmation link to the new address. Open it to "
                    "finish the change."
                ),
                "new_email": request_row.new_email,
                "expires_in_minutes": CONFIRM_EXPIRY_MINUTES,
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(tags=["Email Change"])
class ResendEmailChangeConfirmationView(APIView):
    """Mail the confirmation link again, with a fresh token."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "email_change_request"

    @extend_schema(
        summary="Resend the confirmation link",
        request=None,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="EmailChangeResendSuccess",
                    fields={
                        "detail": serializers.CharField(),
                        "new_email": serializers.EmailField(),
                        "expires_in_minutes": serializers.IntegerField(),
                    },
                ),
                description="A new link was sent; the previous one stops working",
            ),
            400: OpenApiResponse(description="Nothing is waiting for confirmation"),
        },
    )
    def post(self, request):
        try:
            request_row = services.resend_confirmation(request.user)
        except services.EmailChangeError as error:
            return _error_response(error, status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "detail": "We sent a new confirmation link to the new address.",
                "new_email": request_row.new_email,
                "expires_in_minutes": CONFIRM_EXPIRY_MINUTES,
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(tags=["Email Change"])
class EmailChangeStatusView(APIView):
    """Where the caller's own email change currently stands."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Current email-change request",
        description=(
            "Returns the request still in flight for the caller, or `null` when "
            "there is none. The UI uses this to resume the flow after a reload."
        ),
        responses={200: EmailChangeStatusSerializer(allow_null=True)},
    )
    def get(self, request):
        request_row = services.active_request(request.user)
        if request_row is None:
            return Response(None, status=status.HTTP_200_OK)

        return Response(
            EmailChangeStatusSerializer(request_row).data, status=status.HTTP_200_OK
        )

    @extend_schema(
        summary="Cancel the current email-change request",
        request=None,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="EmailChangeCancelSuccess",
                    fields={"detail": serializers.CharField()},
                ),
                description="Cancelled (also returned when there was nothing to cancel)",
            )
        },
    )
    def delete(self, request):
        cancelled = services.cancel_open_requests(request.user)
        return Response(
            {
                "detail": (
                    "Email change cancelled."
                    if cancelled
                    else "There was no email change in progress."
                )
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(tags=["Email Change"])
class ConfirmEmailChangeView(APIView):
    """
    Step 4: the link from the new inbox.

    Public by necessity and rendered as HTML, not JSON — whoever opens this
    is a person in a mail client, not the SPA.
    """

    authentication_classes = []
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "email_change_confirm"

    @extend_schema(
        summary="Step 4: Confirm the new address (from the emailed link)",
        description=(
            "Applies the change and renders an HTML result page. Opened by a "
            "person from their inbox, so it takes no authentication and returns "
            "no JSON."
        ),
        parameters=[
            OpenApiParameter(
                name="token",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="The one-time token from the confirmation email.",
                required=True,
            )
        ],
        responses={200: OpenApiTypes.STR, 400: OpenApiTypes.STR},
    )
    def get(self, request):
        token = request.query_params.get("token", "")

        try:
            request_row = services.confirm(token)
        except services.EmailChangeError as error:
            return render(
                request,
                "email_templates/errors/email_change_error.html",
                {**email_brand(), "message": error.message, "reason": error.code},
                status=400,
            )

        user = request_row.user
        return render(
            request,
            "email_templates/email_change_success.html",
            {
                **email_brand(user),
                "account_username": user.username,
                "previous_email": request_row.current_email,
                "new_email": request_row.new_email,
                "login_url": getattr(settings, "FRONTEND_URL", None),
            },
            status=200,
        )
