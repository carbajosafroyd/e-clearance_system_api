"""
Google sign-in, mostly its refusals.

Each one has to say something different, because the fix for each one is
different: ask for an invitation, open your activation email, talk to an
admin, or just press the button again. A test per branch keeps them from
collapsing back into one "Google login failed".
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from rest_framework.test import APIClient

User = get_user_model()

WEB_CLIENT_ID = "web-client.apps.googleusercontent.com"


def claims(email="juan@gmail.com", aud=WEB_CLIENT_ID, iss="accounts.google.com"):
    return {"email": email, "aud": aud, "iss": iss, "email_verified": True}


class GoogleLoginTests(TestCase):
    def setUp(self):
        # DRF keeps throttle history in the cache, which outlives the
        # per-test transaction rollback - without this the later tests all
        # come back 429 instead of the code they are checking.
        cache.clear()

        self.client = APIClient()
        self.url = reverse("google-auth")

        self.user = User.objects.create_user(
            username="juan", email="juan@gmail.com", password="s3cret-passphrase"
        )
        self.user.email_verified_at = timezone.now()
        self.user.save(update_fields=["email_verified_at"])

    def post(self, verified_claims=None, side_effect=None):
        target = "api.views.google_auth_view.id_token.verify_oauth2_token"
        with patch.dict(
            "os.environ", {"GOOGLE_WEB_CLIENT_ID": WEB_CLIENT_ID}, clear=False
        ):
            with patch(target) as verify:
                if side_effect is not None:
                    verify.side_effect = side_effect
                else:
                    verify.return_value = verified_claims or claims()
                return self.client.post(self.url, {"token": "any"}, format="json")

    def body(self, response):
        """Unwrap CustomResponseRenderer's envelope."""
        payload = response.json()
        return payload["data"] if response.status_code < 400 else payload["errors"]

    # -- the happy path -----------------------------------------------------

    def test_known_active_verified_user_gets_tokens(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", self.body(response))
        self.assertIn("refresh", self.body(response))

    def test_address_matches_regardless_of_case(self):
        response = self.post(claims(email="Juan@Gmail.com"))
        self.assertEqual(response.status_code, 200, response.content)

    # -- the refusals -------------------------------------------------------

    def test_unregistered_address_says_so_and_names_it(self):
        response = self.post(claims(email="stranger@gmail.com"))
        body = self.body(response)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(body["code"], "no_account")
        # The UI names the address that was tried, so "no account found" is
        # not a guessing game when someone has three Google logins.
        self.assertEqual(body["email"], "stranger@gmail.com")
        self.assertIn("stranger@gmail.com", body["detail"])
        # Old key kept for clients that still read it.
        self.assertEqual(body["error"], body["detail"])

    def test_deactivated_account_is_refused(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        response = self.post()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.body(response)["code"], "account_inactive")

    def test_never_activated_account_is_refused(self):
        self.user.email_verified_at = None
        self.user.save(update_fields=["email_verified_at"])

        response = self.post()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.body(response)["code"], "email_not_verified")

    def test_expired_token_is_distinguished_from_a_bad_one(self):
        response = self.post(side_effect=ValueError("Token expired, iat 1234"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.body(response)["code"], "token_expired")

    def test_unverifiable_token_is_rejected_without_leaking_the_exception(self):
        response = self.post(side_effect=ValueError("Wrong number of segments in x"))
        body = self.body(response)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(body["code"], "invalid_token")
        self.assertNotIn("segments", body["detail"])

    def test_token_for_another_google_client_is_rejected(self):
        response = self.post(claims(aud="someone-elses.apps.googleusercontent.com"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.body(response)["code"], "invalid_token")

    def test_token_from_a_foreign_issuer_is_rejected(self):
        response = self.post(claims(iss="evil.example.com"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.body(response)["code"], "invalid_token")

    def test_missing_client_id_is_our_fault_not_the_callers(self):
        with patch.dict(
            "os.environ",
            {"GOOGLE_WEB_CLIENT_ID": "", "GOOGLE_ANDROID_CLIENT_ID": ""},
            clear=False,
        ):
            with patch("api.views.google_auth_view.id_token.verify_oauth2_token"):
                response = self.client.post(self.url, {"token": "any"}, format="json")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.body(response)["code"], "google_not_configured")


class GoogleRegisterTests(TestCase):
    """
    The register endpoint used to verify against an unset GOOGLE_CLIENT_ID,
    which turns the audience check off entirely — any Google token from any
    app could open an account. It shares the login path's verification now.
    """

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.url = reverse("google-auth-register")

    def post(self, verified_claims):
        with patch.dict(
            "os.environ", {"GOOGLE_WEB_CLIENT_ID": WEB_CLIENT_ID}, clear=False
        ):
            with patch(
                "api.views.google_auth_view.id_token.verify_oauth2_token"
            ) as verify:
                verify.return_value = verified_claims
                return self.client.post(self.url, {"token": "any"}, format="json")

    def test_a_token_from_a_foreign_client_cannot_open_an_account(self):
        response = self.post(claims(aud="someone-elses.apps.googleusercontent.com"))

        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(email="juan@gmail.com").exists())

    def test_registering_twice_is_refused(self):
        User.objects.create_user(username="juan", email="juan@gmail.com")
        response = self.post(claims())

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["errors"]["code"], "account_exists")

    def test_username_does_not_collide_with_an_existing_one(self):
        User.objects.create_user(username="juan", email="juan@yahoo.com")
        response = self.post(claims(email="juan@gmail.com"))

        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotEqual(
            User.objects.get(email="juan@gmail.com").username, "juan"
        )
