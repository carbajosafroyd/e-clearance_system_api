import hmac

from django.contrib.auth import get_user_model
from django.utils import timezone

from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer

from core.services.email_branding import email_brand
from core.services.send_email_v2 import send_email_async

from .models import PasswordChangeToken, PasswordResetRequest
from .services import start_password_reset
from .serializers import (
    PasswordChangeSerializer,
    PasswordResetRequestSerializer,
    PasswordResetVerifySerializer,
)
from .utils import (
    OTP_EXPIRY_MINUTES,
    change_token_expiry,
    generate_change_token,
    generate_otp_code,
    hash_value,
    otp_expiry,
)

User = get_user_model()


class RequestPasswordResetView(APIView):
    """
    Step 1: POST {email}
    Always returns a generic 200 — never reveals whether the email exists.
    """

    permission_classes = []
    throttle_classes = [AnonRateThrottle]  # tune scope/rate in settings

    @extend_schema(
        tags=["Password Reset"],
        summary="Step 1: Request Password Reset OTP",
        description="Sends a 4-8 character OTP to the user's email. Always returns a generic 200 OK to prevent email enumeration attacks.",
        request=PasswordResetRequestSerializer,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="PasswordResetRequestSuccess",
                    fields={
                        "detail": serializers.CharField(
                            default="If that email is registered, a code has been sent."
                        )
                    },
                ),
                description="Generic success message regardless of email existence",
            )
        },
    )
    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].lower().strip() # type: ignore

        user = User.objects.filter(email__iexact=email).first()
        if user is not None:
            # Shared with the administrator-triggered reset on the users
            # endpoint, so both produce the same code row and the same email.
            start_password_reset(user)

        return Response(
            {"detail": "If that email is registered and validated, a code has been sent."},
            status=status.HTTP_200_OK,
        )


class VerifyPasswordResetView(APIView):
    """
    Step 2: POST {email, code}
    Returns a one-time change_token on success.
    """

    permission_classes = []
    throttle_classes = [AnonRateThrottle]

    @extend_schema(
        tags=["Password Reset"],
        summary="Step 2: Verify OTP Code",
        description="Verifies the OTP sent to the user's email. On success, returns a one-time `change_token` to be used in Step 3.",
        request=PasswordResetVerifySerializer,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="PasswordResetVerifySuccess",
                    fields={"change_token": serializers.CharField()},
                ),
                description="OTP verified successfully, token granted",
            ),
            400: OpenApiResponse(
                response=inline_serializer(
                    name="PasswordResetVerifyError",
                    fields={"detail": serializers.CharField(default="Invalid or expired code.")},
                ),
                description="Invalid or expired OTP code",
            ),
            429: OpenApiResponse(
                response=inline_serializer(
                    name="PasswordResetRateLimitError",
                    fields={
                        "detail": serializers.CharField(
                            default="Too many attempts. Request a new code."
                        )
                    },
                ),
                description="Too many failed verification attempts (Locked out)",
            ),
        },
    )
    def post(self, request):
        serializer = PasswordResetVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].lower().strip() # type: ignore
        code = serializer.validated_data["code"] # type: ignore

        reset_request = (
            PasswordResetRequest.objects.filter(email=email, validated_at__isnull=True)
            .order_by("-created_at")
            .first()
        )

        generic_error = Response(
            {"detail": "Invalid or expired code."}, status=status.HTTP_400_BAD_REQUEST
        )

        if reset_request is None or reset_request.is_expired():
            return generic_error

        if reset_request.is_locked():
            return Response(
                {"detail": "Too many attempts. Request a new code."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        reset_request.attempts += 1
        reset_request.save(update_fields=["attempts"])

        if not hmac.compare_digest(reset_request.code_hash, hash_value(code)):
            return generic_error

        reset_request.validated_at = timezone.now()
        reset_request.save(update_fields=["validated_at"])

        raw_token = generate_change_token()
        PasswordChangeToken.objects.create(
            reset_request=reset_request,
            email=email,
            token_hash=hash_value(raw_token),
            expires_at=change_token_expiry(),
        )

        return Response({"change_token": raw_token}, status=status.HTTP_200_OK)


class ChangePasswordView(APIView):
    """
    Step 3: POST {change_token, new_password}
    """

    permission_classes = []
    throttle_classes = [AnonRateThrottle]

    @extend_schema(
        tags=["Password Reset"],
        summary="Step 3: Complete Password Change",
        description="Updates the user's password using the temporary `change_token` acquired from Step 2.",
        request=PasswordChangeSerializer,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="PasswordChangeSuccess",
                    fields={"detail": serializers.CharField(default="Password updated successfully.")},
                ),
                description="Password successfully updated",
            ),
            400: OpenApiResponse(
                response=inline_serializer(
                    name="PasswordChangeError",
                    fields={"detail": serializers.CharField(default="Invalid or expired token.")},
                ),
                description="Token is invalid, expired, or already used",
            ),
        },
    )
    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw_token = serializer.validated_data["change_token"] # type: ignore
        new_password = serializer.validated_data["new_password"] # type: ignore

        token_obj = PasswordChangeToken.objects.filter(
            token_hash=hash_value(raw_token)
        ).first()

        invalid = Response(
            {"detail": "Invalid or expired token."}, status=status.HTTP_400_BAD_REQUEST
        )

        if token_obj is None or token_obj.is_expired() or token_obj.is_used():
            return invalid

        user = User.objects.filter(email__iexact=token_obj.email).first()
        if user is None:
            return invalid

        user.set_password(new_password)  # hashes, peppers, stamps pepper_version
        user.save(update_fields=["password", "pepper_version"])

        token_obj.used_at = timezone.now()
        token_obj.save(update_fields=["used_at"])

        return Response(
            {"detail": "Password updated successfully."}, status=status.HTTP_200_OK
        )