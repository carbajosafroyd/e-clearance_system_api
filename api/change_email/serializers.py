from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import EmailChangeRequest

User = get_user_model()


class EmailChangeRequestSerializer(serializers.Serializer):
    """Step 1 takes nothing: the code always goes to the account on the token."""


class EmailChangeVerifySerializer(serializers.Serializer):
    code = serializers.CharField(min_length=4, max_length=8, trim_whitespace=True)


class EmailChangeSetNewSerializer(serializers.Serializer):
    change_token = serializers.CharField()
    new_email = serializers.EmailField()

    def validate_new_email(self, value):
        email = value.lower().strip()
        user = self.context["request"].user

        if user.email and email == user.email.lower().strip():
            raise serializers.ValidationError(
                "That's already the email on this account."
            )

        # Same rule the confirmation step re-checks: one address, one account.
        if User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
            raise serializers.ValidationError(
                "That email address is already used by another account."
            )

        return email


class EmailChangeStatusSerializer(serializers.ModelSerializer):
    """
    What the UI needs to pick the flow back up after a refresh — which step
    is live, where the mail went, and when it stops being valid.
    """

    expires_at = serializers.SerializerMethodField()

    class Meta:
        model = EmailChangeRequest
        fields = [
            "id",
            "stage",
            "current_email",
            "new_email",
            "expires_at",
            "confirm_sent_at",
            "created_at",
        ]
        read_only_fields = fields

    def get_expires_at(self, obj) -> str | None:
        """The deadline that matters at the stage the request is actually in."""
        deadline = {
            EmailChangeRequest.Stage.CODE_SENT: obj.code_expires_at,
            EmailChangeRequest.Stage.CODE_VERIFIED: obj.change_token_expires_at,
            EmailChangeRequest.Stage.AWAITING_CONFIRMATION: obj.confirm_expires_at,
        }.get(obj.stage)

        return deadline.isoformat() if deadline else None
