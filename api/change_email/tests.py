"""
The email-change flow.

The tests worth having here are the ones about what must *not* happen: the
address must not move before the new inbox is proven, a code from one step
must not be usable at another, and two accounts must never end up sharing an
address.
"""

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from rest_framework.test import APIClient

from api.change_email.models import EmailChangeRequest
from api.change_email.utils import hash_value

User = get_user_model()


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_SEND_ASYNC=False,
)
class EmailChangeFlowTests(TestCase):
    def setUp(self):
        # Throttle history lives in the cache and survives the transaction
        # rollback between tests; user ids do not, so it would otherwise
        # carry over onto the next test's user.
        cache.clear()

        self.user = User.objects.create_user(
            username="jdelacruz",
            email="old@example.com",
            password="s3cret-passphrase",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    # -- helpers ------------------------------------------------------------

    def _request_code(self):
        response = self.client.post(reverse("email-change-request"))
        self.assertEqual(response.status_code, 200)
        return EmailChangeRequest.objects.get(user=self.user)

    def _verify(self, code):
        return self.client.post(reverse("email-change-verify"), {"code": code})

    def _set_new(self, change_token, new_email):
        return self.client.post(
            reverse("email-change-set-new"),
            {"change_token": change_token, "new_email": new_email},
        )

    def _seed_verified(self, code="123456"):
        """A request sitting at CODE_VERIFIED with a known change token."""
        row = EmailChangeRequest.objects.create(
            user=self.user,
            current_email=self.user.email,
            code_hash=hash_value(code),
            code_expires_at=timezone.now() + timezone.timedelta(minutes=10),
            stage=EmailChangeRequest.Stage.CODE_VERIFIED,
            verified_at=timezone.now(),
            change_token_hash=hash_value("raw-change-token"),
            change_token_expires_at=timezone.now() + timezone.timedelta(minutes=15),
        )
        return row, "raw-change-token"

    # -- the happy path -----------------------------------------------------

    def test_full_flow_changes_the_email_only_at_the_confirmation_link(self):
        row, change_token = self._seed_verified()

        response = self._set_new(change_token, "new@example.com")
        self.assertEqual(response.status_code, 200)

        # Sent, but not applied: the address only moves at step 4.
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "old@example.com")

        row.refresh_from_db()
        self.assertEqual(row.stage, EmailChangeRequest.Stage.AWAITING_CONFIRMATION)
        self.assertEqual(row.new_email, "new@example.com")

        # Pull the real token out of the link that was mailed.
        confirmation = next(m for m in mail.outbox if "new@example.com" in m.to)
        token = confirmation.body.split("token=")[1].split()[0]

        self.client.force_authenticate(None)
        page = self.client.get(reverse("email-change-confirm"), {"token": token})
        self.assertEqual(page.status_code, 200)

        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "new@example.com")
        self.assertIsNotNone(self.user.email_verified_at)

        row.refresh_from_db()
        self.assertEqual(row.stage, EmailChangeRequest.Stage.COMPLETED)

        # The old address is told, so a change the owner did not make is visible.
        self.assertTrue(any("old@example.com" in m.to for m in mail.outbox))

    def test_request_step_mails_the_current_address(self):
        self._request_code()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["old@example.com"])

    # -- the code -----------------------------------------------------------

    def test_wrong_code_is_rejected_and_counted(self):
        row = self._request_code()

        response = self._verify("000000")
        self.assertEqual(response.status_code, 400)

        row.refresh_from_db()
        self.assertEqual(row.attempts, 1)
        self.assertEqual(row.stage, EmailChangeRequest.Stage.CODE_SENT)

    def test_code_locks_after_max_attempts(self):
        row = self._request_code()
        row.attempts = row.max_attempts
        row.save(update_fields=["attempts"])

        response = self._verify("000000")
        self.assertEqual(response.status_code, 429)

    def test_expired_code_is_rejected(self):
        row = self._request_code()
        row.code_hash = hash_value("123456")
        row.code_expires_at = timezone.now() - timezone.timedelta(minutes=1)
        row.save(update_fields=["code_hash", "code_expires_at"])

        self.assertEqual(self._verify("123456").status_code, 400)

    # -- the new address ----------------------------------------------------

    def test_cannot_take_an_address_another_account_uses(self):
        User.objects.create_user(username="other", email="taken@example.com")
        _, change_token = self._seed_verified()

        response = self._set_new(change_token, "taken@example.com")
        self.assertEqual(response.status_code, 400)

    def test_cannot_set_the_address_the_account_already_has(self):
        _, change_token = self._seed_verified()
        response = self._set_new(change_token, "old@example.com")
        self.assertEqual(response.status_code, 400)

    def test_change_token_cannot_be_replayed(self):
        _, change_token = self._seed_verified()

        self.assertEqual(self._set_new(change_token, "new@example.com").status_code, 200)
        # Same token, second address: the step is spent.
        self.assertEqual(
            self._set_new(change_token, "another@example.com").status_code, 400
        )

    # -- the confirmation link ----------------------------------------------

    def test_confirmation_link_works_once(self):
        _, change_token = self._seed_verified()
        self._set_new(change_token, "new@example.com")

        confirmation = next(m for m in mail.outbox if "new@example.com" in m.to)
        token = confirmation.body.split("token=")[1].split()[0]

        self.client.force_authenticate(None)
        self.assertEqual(
            self.client.get(reverse("email-change-confirm"), {"token": token}).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse("email-change-confirm"), {"token": token}).status_code,
            400,
        )

    def test_expired_confirmation_link_changes_nothing(self):
        row, change_token = self._seed_verified()
        self._set_new(change_token, "new@example.com")

        confirmation = next(m for m in mail.outbox if "new@example.com" in m.to)
        token = confirmation.body.split("token=")[1].split()[0]

        row.refresh_from_db()
        row.confirm_expires_at = timezone.now() - timezone.timedelta(minutes=1)
        row.save(update_fields=["confirm_expires_at"])

        self.client.force_authenticate(None)
        page = self.client.get(reverse("email-change-confirm"), {"token": token})
        self.assertEqual(page.status_code, 400)

        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "old@example.com")

    def test_garbage_token_is_rejected(self):
        self.client.force_authenticate(None)
        response = self.client.get(reverse("email-change-confirm"), {"token": "nope"})
        self.assertEqual(response.status_code, 400)

    # -- status and cancel --------------------------------------------------

    def test_status_reports_the_open_request_and_cancel_clears_it(self):
        self._request_code()

        status_response = self.client.get(reverse("email-change-status"))
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.json()["data"]["stage"], "code_sent")

        self.assertEqual(self.client.delete(reverse("email-change-status")).status_code, 200)
        self.assertIsNone(self.client.get(reverse("email-change-status")).json()["data"])

    def test_starting_again_retires_the_previous_request(self):
        first = self._request_code()
        self.client.post(reverse("email-change-request"))

        first.refresh_from_db()
        self.assertEqual(first.stage, EmailChangeRequest.Stage.CANCELLED)

    def test_requires_authentication(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post(reverse("email-change-request")).status_code, 401)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_SEND_ASYNC=False,
)
class EmailChangeWithoutCurrentAddressTests(TestCase):
    def test_account_with_no_email_cannot_start(self):
        cache.clear()
        user = User.objects.create_user(username="noemail", email="")
        client = APIClient()
        client.force_authenticate(user)

        response = client.post(reverse("email-change-request"))
        self.assertEqual(response.status_code, 400)
