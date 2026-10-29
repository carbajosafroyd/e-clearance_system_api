"""
Sign in with Google.

The interesting part here is the failure paths, not the happy one. "Google
login failed" is useless to whoever is staring at it: the fix for an
unregistered address (ask for an invitation) has nothing in common with the
fix for a stale token (press the button again), and telling someone to
"try again" when retrying can never work is worse than saying nothing.

So every refusal carries a machine-readable ``code`` next to the sentence a
person reads, and the UI branches on the code. The shape is:

    {"detail": "...", "code": "no_account", "error": "..."}

``error`` repeats ``detail`` verbatim - older clients (the Android app) read
that key, and renaming it would break them silently.
"""

import logging
import os

from django.contrib.auth import get_user_model

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2 import id_token

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema

from api.serializers.ApiResponse import TokenResponseSerializer
from api.serializers.AuthSerializer import CustomTokenObtainPairSerializer
from api.serializers.GoogleAuthSerializer import GoogleAuthSerializer
from api.users.utils import generate_username

logger = logging.getLogger(__name__)
User = get_user_model()


class GoogleSignInError(Exception):
    """A refusal we can explain. Anything else is a 500 and gets logged."""

    def __init__(self, detail: str, code: str, http_status: int = 400, **extra):
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.http_status = http_status
        self.extra = extra


def _error_response(error: GoogleSignInError) -> Response:
    return Response(
        {
            "detail": error.detail,
            "code": error.code,
            # Back-compat: the old payload was {"error": "..."} and at least
            # one client still reads it. Same text, so nothing has to change.
            "error": error.detail,
            **error.extra,
        },
        status=error.http_status,
    )


def _allowed_client_ids() -> list[str]:
    """
    The OAuth clients whose tokens this API accepts. A token minted for any
    other Google client is somebody else's, however valid its signature.
    """
    ids = [
        os.getenv("GOOGLE_WEB_CLIENT_ID"),
        os.getenv("GOOGLE_ANDROID_CLIENT_ID"),
    ]
    return [client_id for client_id in ids if client_id]


def verify_google_token(raw_token: str) -> dict:
    """
    Check the token really is Google's, really is ours, and hasn't expired.

    Returns Google's claims. Raises GoogleSignInError with a code the caller
    can turn into something a person can act on.
    """
    client_ids = _allowed_client_ids()
    if not client_ids:
        # Misconfiguration, not the caller's fault - never report it as one.
        logger.error(
            "Google sign-in is unavailable: neither GOOGLE_WEB_CLIENT_ID nor "
            "GOOGLE_ANDROID_CLIENT_ID is set."
        )
        raise GoogleSignInError(
            "Google sign-in isn't available right now. Please sign in with "
            "your username and password, or contact your administrator.",
            code="google_not_configured",
            http_status=503,
        )

    try:
        claims = id_token.verify_oauth2_token(raw_token, Request())
    except ValueError as exc:
        # The library reports expiry, bad signature and malformed input all
        # as ValueError. Expiry is worth separating: it is the one a retry
        # actually fixes, and redirect flows produce it often.
        message = str(exc).lower()
        if "expired" in message or "too early" in message:
            raise GoogleSignInError(
                "Your Google sign-in expired before we could use it. Please "
                "try again.",
                code="token_expired",
            ) from exc
        logger.info("Rejected a Google token: %s", exc)
        raise GoogleSignInError(
            "We couldn't verify that Google sign-in. Please try again.",
            code="invalid_token",
        ) from exc
    except GoogleAuthError as exc:
        # Couldn't reach Google to fetch its signing keys - our problem.
        logger.error("Could not verify a Google token: %s", exc, exc_info=True)
        raise GoogleSignInError(
            "We couldn't reach Google to check your sign-in. Please try again "
            "in a moment.",
            code="google_unreachable",
            http_status=503,
        ) from exc

    if claims.get("aud") not in client_ids:
        logger.warning(
            "Google token for a foreign client rejected (aud=%r)", claims.get("aud")
        )
        raise GoogleSignInError(
            "That Google sign-in wasn't issued for this app. Please use the "
            "sign-in button here.",
            code="invalid_token",
        )

    if claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise GoogleSignInError(
            "We couldn't verify that Google sign-in. Please try again.",
            code="invalid_token",
        )

    if not claims.get("email"):
        raise GoogleSignInError(
            "That Google account didn't share an email address, so we can't "
            "match it to an account.",
            code="no_email_claim",
        )

    return claims


@extend_schema(tags=["Authentication"])
class GoogleAuthView(APIView):
    @extend_schema(
        summary="Google login",
        description=(
            "Exchanges a Google ID token for this API's access/refresh pair.\n\n"
            "Sign-in only — it never creates an account. Refusals carry a "
            "`code` so the client can respond usefully:\n\n"
            "- `no_account` — verified, but no account holds that address. The "
            "response echoes the `email` so the UI can name it.\n"
            "- `account_inactive` — the account was deactivated.\n"
            "- `email_not_verified` — invited but never activated.\n"
            "- `token_expired` / `invalid_token` — retry is worth it for the "
            "first, not the second.\n"
            "- `google_not_configured` / `google_unreachable` — our fault, 503."
        ),
        request=GoogleAuthSerializer,
        responses={
            200: TokenResponseSerializer,
            400: OpenApiResponse(
                description="Unusable token, or no account for that address",
                examples=[
                    OpenApiExample(
                        "No account",
                        value={
                            "detail": (
                                "No account is registered for juan@gmail.com. "
                                "Ask your administrator for an invitation."
                            ),
                            "code": "no_account",
                            "email": "juan@gmail.com",
                        },
                    )
                ],
            ),
            403: OpenApiResponse(description="Account deactivated or not activated"),
            503: OpenApiResponse(description="Google sign-in unavailable"),
        },
    )
    @action(detail=False, methods=["post"])
    def post(self, request):
        serializer = GoogleAuthSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            claims = verify_google_token(serializer.validated_data["token"])
            user = self._resolve_user(claims["email"])
        except GoogleSignInError as error:
            return _error_response(error)

        tokens = CustomTokenObtainPairSerializer.get_token(user)
        payload = TokenResponseSerializer(
            data={"refresh": str(tokens), "access": str(tokens.access_token)}
        )
        payload.is_valid(raise_exception=True)

        return Response(payload.data, status=status.HTTP_200_OK)

    @staticmethod
    def _resolve_user(email: str):
        """
        The account behind a verified Google address, or a refusal that says
        which of the three ways it went wrong.
        """
        # Case-insensitive: Google hands back whatever case the address was
        # typed in, and 'Juan@gmail.com' is the same mailbox as 'juan@…'.
        user = User.objects.filter(email__iexact=email).first()

        if user is None:
            raise GoogleSignInError(
                f"No account is registered for {email}. Ask your administrator "
                f"for an invitation, or sign in with a different Google account.",
                code="no_account",
                # Echoed so the UI can name the address that was tried - the
                # caller just proved they own it, so this reveals nothing.
                email=email,
            )

        if not user.is_active:
            # Django's own ModelBackend refuses inactive users; this path
            # bypasses it entirely, so the check has to be made here.
            raise GoogleSignInError(
                "This account has been deactivated. Contact your administrator "
                "if you think that's a mistake.",
                code="account_inactive",
                http_status=403,
            )

        if not user.email_verified_at:
            raise GoogleSignInError(
                "This account hasn't been activated yet. Open the activation "
                "link in your invitation email first.",
                code="email_not_verified",
                http_status=403,
            )

        return user


@extend_schema(tags=["Authentication"])
class GoogleAuthRegisterView(APIView):
    @extend_schema(
        summary="Google account for registration",
        description="Creates a user using a Google ID token.",
        request=GoogleAuthSerializer,
        responses={200: TokenResponseSerializer},
    )
    def post(self, request):
        serializer = GoogleAuthSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            # Was verified with audience=None against a GOOGLE_CLIENT_ID that
            # is not in the environment, which turns the audience check off:
            # any Google ID token, from any app in the world, could open an
            # account here. Shares the login path's verification now.
            claims = verify_google_token(serializer.validated_data["token"])
        except GoogleSignInError as error:
            return _error_response(error)

        email = claims["email"]

        if User.objects.filter(email__iexact=email).exists():
            return _error_response(
                GoogleSignInError(
                    "An account already uses that email address. Try signing in "
                    "instead.",
                    code="account_exists",
                )
            )

        # Not email.split("@")[0]: that collides the moment two people share
        # a local part across providers, and the save dies on the unique index.
        user = User(email=email, username=generate_username(email))
        user.set_unusable_password()
        user.save()

        tokens = CustomTokenObtainPairSerializer.get_token(user)
        payload = TokenResponseSerializer(
            data={"refresh": str(tokens), "access": str(tokens.access_token)}
        )
        payload.is_valid(raise_exception=True)

        return Response(payload.data, status=status.HTTP_200_OK)
