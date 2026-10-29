from rest_framework import serializers

from .models import TransferToken


class TransferTokenRedeemResponseSerializer(serializers.Serializer):
    """Standard JWT pair response — same shape your existing
    CustomTokenObtainPairSerializer already returns.

    Named for the endpoint rather than the payload: `TokenResponse` is
    already taken by api.serializers.ApiResponse, and two components with
    one name silently overwrite each other in the schema."""

    refresh = serializers.CharField(
        help_text="JWT refresh token for the authenticated user."
    )
    access = serializers.CharField(
        help_text="JWT access token for the authenticated user."
    )


class TransferTokenIssueRequestSerializer(serializers.Serializer):
    """Optional metadata the calling system can pass when issuing a token."""

    intended_for = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True, 
        default="",
        help_text="Optional identifier of the system this token is meant for (e.g. 'collection-management-system').",
    )


class TransferTokenIssueResponseSerializer(serializers.Serializer):
    transfer_token = serializers.CharField(
        help_text="Opaque, single-use token to be redeemed by the destination system within the TTL window."
    )
    expires_at = serializers.DateTimeField(
        help_text="UTC timestamp after which this transfer token can no longer be redeemed."
    )


class TransferTokenRedeemRequestSerializer(serializers.Serializer):
    transfer_token = serializers.CharField(
        help_text="The opaque transfer token previously issued by /transfer_token/issue/."
    )

    def validate_transfer_token(self, value):
        try:
            token_obj = TransferToken.objects.select_related("user").get(token=value)
        except TransferToken.DoesNotExist:
            raise serializers.ValidationError("Invalid transfer token.")

        if token_obj.is_used:
            raise serializers.ValidationError(
                "This transfer token has already been used."
            )

        if token_obj.is_expired:
            raise serializers.ValidationError("This transfer token has expired.")

        # Stash the resolved object so the view doesn't need to query again.
        self.context["token_obj"] = token_obj
        return value


class TransferTokenErrorResponseSerializer(serializers.Serializer):
    """Generic shape for validation-style error responses, e.g.
    {"transfer_token": ["This transfer token has already been used."]}"""

    transfer_token = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text="List of validation error messages related to the transfer_token field.",
    )
