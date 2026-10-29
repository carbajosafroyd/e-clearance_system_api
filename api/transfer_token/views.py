from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated

from api.rbac import ActionPermission
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from api.serializers.AuthSerializer import CustomTokenObtainPairSerializer

from .models import TransferToken
from .serializers import (
    TransferTokenRedeemResponseSerializer,
    TransferTokenErrorResponseSerializer,
    TransferTokenIssueRequestSerializer,
    TransferTokenIssueResponseSerializer,
    TransferTokenRedeemRequestSerializer,
)


class IssueTransferTokenView(APIView):
    """
    POST /transfer_token/issue/

    Called by System A while the user is authenticated there (normal
    access token in the Authorization header). Issues a short-lived,
    single-use transfer token that can be redeemed once by another
    system to obtain a fresh access/refresh pair for the same user —
    no re-login required.
    """

    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = {"post": "rbac.use_transfer_token"}
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "transfer_token_issue"

    @extend_schema(
        tags=["Transfer Token"],
        summary="Issue a transfer token",
        description=(
            "Issues a short-lived, single-use transfer token for the "
            "currently authenticated user. Hand this token to another "
            "system (sharing the same user database) so it can be "
            "exchanged there for a fresh access/refresh pair, without "
            "the user logging in again. The token expires after "
            "`TRANSFER_TOKEN_TTL_SECONDS` (default 60s) and can only "
            "be redeemed once."
        ),
        request=TransferTokenIssueRequestSerializer,
        responses={
            201: OpenApiResponse(
                response=TransferTokenIssueResponseSerializer,
                description="Transfer token issued successfully.",
            ),
            401: OpenApiResponse(
                description="Authentication credentials were not provided or are invalid."
            ),
        },
        examples=[
            OpenApiExample(
                "Request example",
                value={"intended_for": "system-b"},
                request_only=True,
            ),
            OpenApiExample(
                "Response example",
                value={
                    "transfer_token": "iHEQSnYwUs5pb1x65nYLb2ccGgP1Ll4KXuN-ZrG0p6aFwuMhfNQn3o4f0uNIZwLA",
                    "expires_at": "2026-06-18T10:15:11.970038Z",
                },
                response_only=True,
            ),
        ],
    )
    def post(self, request):
        serializer = TransferTokenIssueRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        token_obj = TransferToken.objects.create(
            user=request.user,
            issued_from=request.META.get("HTTP_HOST", ""),
            intended_for=serializer.validated_data.get("intended_for", ""),
        )

        response_data = {
            "transfer_token": token_obj.token,
            "expires_at": token_obj.expires_at,
        }
        response_serializer = TransferTokenIssueResponseSerializer(response_data)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


class RedeemTransferTokenView(APIView):
    """
    POST /transfer_token/redeem/

    Called by System B with the transfer token it received from System A
    (e.g. via redirect query param). No prior authentication is required
    here — the transfer token itself is the credential. On success, the
    token is immediately invalidated (single use) and a normal JWT
    access/refresh pair is issued for the underlying user.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "transfer_token_redeem"

    @extend_schema(
        tags=["Transfer Token"],
        summary="Redeem a transfer token",
        description=(
            "Exchanges a previously issued transfer token for a fresh "
            "JWT access/refresh pair belonging to the same user. No "
            "Authorization header is required — the transfer token "
            "itself is the credential. The token is invalidated "
            "immediately on redemption, so it can never be used twice, "
            "and will be rejected if it has already expired."
        ),
        request=TransferTokenRedeemRequestSerializer,
        responses={
            200: OpenApiResponse(
                response=TransferTokenRedeemResponseSerializer,
                description="Transfer token redeemed successfully; new JWT pair issued.",
            ),
            400: OpenApiResponse(
                response=TransferTokenErrorResponseSerializer,
                description=(
                    "Transfer token is invalid, expired, or has already been used."
                ),
            ),
        },
        examples=[
            OpenApiExample(
                "Request example",
                value={
                    "transfer_token": "iHEQSnYwUs5pb1x65nYLb2ccGgP1Ll4KXuN-ZrG0p6aFwuMhfNQn3o4f0uNIZwLA"
                },
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "refresh": "eyJhbGciOiJIUzI1NiIs...",
                    "access": "eyJhbGciOiJIUzI1NiIs...",
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Already used",
                value={"transfer_token": ["This transfer token has already been used."]},
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Expired",
                value={"transfer_token": ["This transfer token has expired."]},
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Invalid",
                value={"transfer_token": ["Invalid transfer token."]},
                response_only=True,
                status_codes=["400"],
            ),
        ],
    )
    def post(self, request):
        serializer = TransferTokenRedeemRequestSerializer(
            data=request.data, context={}
        )
        serializer.is_valid(raise_exception=True)

        # validate_transfer_token() stashed the resolved object here.
        token_obj: TransferToken = serializer.context["token_obj"]
        user = token_obj.user

        # Invalidate immediately so it can never be redeemed twice,
        # even under concurrent requests racing to use the same token.
        updated = TransferToken.objects.filter(
            pk=token_obj.pk, used_at__isnull=True
        ).update(used_at=timezone.now())

        if not updated:
            # Someone else redeemed it a moment ago — lost the race.
            return Response(
                {"transfer_token": ["This transfer token has already been used."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        token = CustomTokenObtainPairSerializer.get_token(user)
        data = {
            "refresh": str(token),
            "access": str(token.access_token),
        }
        response_serializer = TransferTokenRedeemResponseSerializer(data)
        return Response(response_serializer.data, status=status.HTTP_200_OK)