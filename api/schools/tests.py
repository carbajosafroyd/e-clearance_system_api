"""
The per-school / per-institute email sender.

A school or institute can mail through its own Gmail account: an address
and an app password saved from the console. Three things are worth pinning
down about it:

  * the app password is ENCRYPTED, never stored or returned in the clear -
    a database dump yields ciphertext, the API yields a boolean;
  * which mailbox a message goes out through follows the account that
    caused it: superuser -> platform, school admin -> school, institute
    account -> institute, then school, then platform;
  * the logo in the email follows the same chain - institute, school,
    platform default.

The sender tests live here (rather than in core/, which is not an app
Django's test runner walks) because the School row is the first tenant to
carry the fields.
"""

from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient, APITestCase

from api.institutes.models import Institute
from api.rbac.roles import Roles
from api.rbac.services import assign_role
from api.schools.models import School
from core.email_secrets import EmailSecretUnreadable, decrypt_secret, encrypt_secret
from core.services.email_branding import email_brand
from core.services.email_sender import email_sender, platform_sender
from core.services.send_email_v2 import build_email, send_bulk_emails_async

User = get_user_model()

KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()

PLATFORM = dict(
    EMAIL_HOST_USER="platform@example.com",
    EMAIL_HOST_PASSWORD="platform-app-password",
    DEFAULT_FROM_EMAIL="Platform <platform@example.com>",
)


def _school(name="Northern Polytechnic", **sender):
    school = School.objects.create(school_name=name, short_name=name[:3].upper(), location="Tagum")
    _configure(school, **sender)
    return school


def _institute(school, name="Institute of Computing", **sender):
    institute = Institute.objects.create(institute_name=name, school=school)
    _configure(institute, **sender)
    return institute


def _configure(row, user="", password="", from_email=""):
    if not user:
        return
    row.email_host_user = user
    row.default_from_email = from_email
    row.set_email_host_password(password)
    row.save()


# ---------------------------------------------------------------------------
# Encryption
# ---------------------------------------------------------------------------


@override_settings(EMAIL_SECRETS_KEYS=(KEY_A,))
class EmailSecretTests(TestCase):
    def test_roundtrip_and_the_stored_value_is_not_the_password(self):
        stored = encrypt_secret("abcd efgh ijkl mnop")
        self.assertNotEqual(stored, "abcd efgh ijkl mnop")
        self.assertNotIn("abcd", stored)
        self.assertEqual(decrypt_secret(stored), "abcd efgh ijkl mnop")

    def test_empty_stays_empty_both_ways(self):
        self.assertEqual(encrypt_secret(""), "")
        self.assertEqual(encrypt_secret(None), "")
        self.assertEqual(decrypt_secret(""), "")

    def test_two_encryptions_of_the_same_password_differ(self):
        # Fernet salts with a random IV: equal passwords must not be
        # recognisable as equal from the database.
        self.assertNotEqual(encrypt_secret("same"), encrypt_secret("same"))

    def test_rotation_keeps_old_rows_readable(self):
        stored = encrypt_secret("old-password")
        with override_settings(EMAIL_SECRETS_KEYS=(KEY_B, KEY_A)):
            self.assertEqual(decrypt_secret(stored), "old-password")
            # ...and new writes go under the new key only.
            fresh = encrypt_secret("new-password")
        with override_settings(EMAIL_SECRETS_KEYS=(KEY_B,)):
            self.assertEqual(decrypt_secret(fresh), "new-password")

    def test_a_key_that_is_gone_raises_rather_than_returning_garbage(self):
        stored = encrypt_secret("secret")
        with override_settings(EMAIL_SECRETS_KEYS=(KEY_B,)):
            with self.assertRaises(EmailSecretUnreadable):
                decrypt_secret(stored)

    @override_settings(EMAIL_SECRETS_KEYS=(), SECRET_KEY="dev-only-secret")
    def test_locally_a_key_is_derived_from_the_django_secret(self):
        self.assertEqual(decrypt_secret(encrypt_secret("x")), "x")


# ---------------------------------------------------------------------------
# Which mailbox sends
# ---------------------------------------------------------------------------


@override_settings(EMAIL_SECRETS_KEYS=(KEY_A,), **PLATFORM)
class SenderResolutionTests(TestCase):
    def setUp(self):
        self.school = _school(
            user="registrar@npc.edu.ph",
            password="school-app-password",
            from_email="NPC Registrar <registrar@npc.edu.ph>",
        )
        self.institute = _institute(
            self.school,
            user="ic.office@gmail.com",
            password="institute-app-password",
        )
        self.bare_institute = _institute(self.school, name="Institute of Teacher Education")
        self.other_school = _school(name="Coastal College")

    def _user(self, username, *, school=None, institute=None, superuser=False):
        make = User.objects.create_superuser if superuser else User.objects.create_user
        user = make(username=username, email=f"{username}@example.com", password="x")
        user.school = school
        user.institute = institute
        user.save(update_fields=["school", "institute"])
        return user

    def test_superuser_is_always_the_platform(self):
        # Even one attached to a configured school: the rule is the role.
        boss = self._user("boss", school=self.school, institute=self.institute, superuser=True)
        sender = email_sender(boss)
        self.assertTrue(sender.is_platform)
        self.assertEqual(sender.from_email, "Platform <platform@example.com>")
        self.assertIsNone(sender.username)

    def test_school_admin_sends_as_the_school(self):
        admin = self._user("schooladmin", school=self.school)
        assign_role(admin, Roles.SCHOOL_ADMIN)
        sender = email_sender(admin)
        self.assertEqual(sender.source, "school")
        self.assertEqual(sender.owner_id, self.school.pk)
        self.assertEqual(sender.username, "registrar@npc.edu.ph")
        self.assertEqual(sender.password, "school-app-password")
        self.assertEqual(sender.from_email, "NPC Registrar <registrar@npc.edu.ph>")

    def test_institute_account_sends_as_the_institute(self):
        officer = self._user("officer", institute=self.institute)
        sender = email_sender(officer)
        self.assertEqual(sender.source, "institute")
        self.assertEqual(sender.username, "ic.office@gmail.com")
        self.assertEqual(sender.password, "institute-app-password")
        # No From set on the institute: the address itself is the From.
        self.assertEqual(sender.from_email, "ic.office@gmail.com")

    def test_institute_without_a_sender_falls_back_to_its_school(self):
        officer = self._user("ite", institute=self.bare_institute)
        sender = email_sender(officer)
        self.assertEqual(sender.source, "school")
        self.assertEqual(sender.username, "registrar@npc.edu.ph")

    def test_nothing_configured_falls_back_to_the_platform(self):
        lonely = self._user("lonely", institute=_institute(self.other_school, name="Nursing"))
        self.assertTrue(email_sender(lonely).is_platform)
        self.assertTrue(email_sender(None).is_platform)

    def test_a_password_the_key_can_no_longer_read_is_skipped_not_fatal(self):
        officer = self._user("officer", institute=self.institute)
        with override_settings(EMAIL_SECRETS_KEYS=(KEY_B,)):
            with self.assertLogs("core.services.email_sender", level="WARNING"):
                sender = email_sender(officer)
        # Both the institute's and the school's password are unreadable now.
        self.assertTrue(sender.is_platform)

    def test_a_half_configured_row_does_not_count(self):
        self.institute.email_host_password_encrypted = ""
        self.institute.save()
        officer = self._user("officer", institute=self.institute)
        self.assertEqual(email_sender(officer).source, "school")

    def test_explicit_school_and_institute_override_the_users_own(self):
        officer = self._user("officer", institute=self.institute)
        self.assertEqual(email_sender(officer, institute=self.bare_institute).source, "school")
        self.assertTrue(email_sender(officer, institute=self.bare_institute, school=self.other_school).is_platform)


# ---------------------------------------------------------------------------
# The connection a message goes out on
# ---------------------------------------------------------------------------


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_SEND_ASYNC=False,
    EMAIL_SECRETS_KEYS=(KEY_A,),
    **PLATFORM,
)
class SendingTests(TestCase):
    def setUp(self):
        self.school = _school(user="registrar@npc.edu.ph", password="school-app-password")
        self.institute = _institute(self.school)
        self.officer = User.objects.create_user(username="officer", email="o@npc.edu.ph", password="x")
        self.officer.institute = self.institute
        self.officer.save(update_fields=["institute"])

    def _build(self, sender=None):
        return build_email(
            subject="Hello",
            to_email="someone@example.com",
            template_name="password_reset_otp",
            context={**email_brand(self.officer), "code": "123456", "expiry_minutes": 10},
            sender=sender,
        )

    def test_tenant_sender_logs_in_with_its_own_credentials(self):
        with patch("core.services.email_sender.mail.get_connection", wraps=mail.get_connection) as conn:
            message = self._build(sender=email_sender(self.officer))
        conn.assert_called_once_with(username="registrar@npc.edu.ph", password="school-app-password")
        self.assertEqual(message.from_email, "registrar@npc.edu.ph")
        message.send()
        self.assertEqual(mail.outbox[0].from_email, "registrar@npc.edu.ph")

    def test_platform_sender_leaves_the_login_to_the_settings(self):
        with patch("core.services.email_sender.mail.get_connection", wraps=mail.get_connection) as conn:
            message = self._build(sender=platform_sender())
        conn.assert_called_once_with()
        self.assertEqual(message.from_email, "Platform <platform@example.com>")

    def test_no_sender_means_the_platform(self):
        self.assertEqual(self._build().from_email, "Platform <platform@example.com>")

    def test_bulk_send_opens_one_connection_per_mailbox(self):
        school_sender = email_sender(self.officer)
        base = dict(
            subject="Hi", template_name="password_reset_otp",
            context={"code": "1", "expiry_minutes": 1},
        )
        messages = [
            {**base, "to_email": "a@example.com", "sender": school_sender},
            {**base, "to_email": "b@example.com", "sender": school_sender},
            {**base, "to_email": "c@example.com", "sender": platform_sender()},
        ]
        with patch("core.services.email_sender.mail.get_connection", wraps=mail.get_connection) as conn:
            send_bulk_emails_async(messages)
        # 3 while building (one per message) + 2 for the two groups.
        self.assertEqual(conn.call_count, 5)
        self.assertEqual(
            sorted(m.from_email for m in mail.outbox),
            ["Platform <platform@example.com>", "registrar@npc.edu.ph", "registrar@npc.edu.ph"],
        )

    def test_an_invitation_goes_out_through_the_inviters_mailbox(self):
        group, _ = Group.objects.get_or_create(name="Students")
        assign_role(self.officer, Roles.INSTITUTE_ADMIN)
        inviter = User.objects.get(pk=self.officer.pk)

        client = APIClient()
        client.force_authenticate(inviter)
        response = client.post(
            reverse("user-invite"), {"email": "invited@gmail.com", "group_id": group.id}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(mail.outbox[0].from_email, "registrar@npc.edu.ph")

    def test_a_password_reset_goes_out_through_the_accounts_own_mailbox(self):
        from api.change_password.services import start_password_reset

        start_password_reset(self.officer)
        self.assertEqual(mail.outbox[0].from_email, "registrar@npc.edu.ph")


# ---------------------------------------------------------------------------
# The logo in the header
# ---------------------------------------------------------------------------


@override_settings(EMAIL_BRAND_LOGO_URL="https://cdn.example.com/platform.png")
class BrandLogoTests(TestCase):
    def setUp(self):
        self.school = _school()
        self.school.logo = "https://cdn.example.com/school.png"
        self.school.save()
        self.institute = _institute(self.school)

    def _user(self, **attach):
        user = User.objects.create_user(username="u", email="u@example.com", password="x", **attach)
        return user

    def test_institute_logo_wins_for_an_institute_account(self):
        self.institute.logo = "https://cdn.example.com/institute.png"
        self.institute.save()
        brand = email_brand(self._user(institute=self.institute))
        self.assertEqual(brand["brand_logo_url"], "https://cdn.example.com/institute.png")
        self.assertEqual(brand["brand_name"], "Northern Polytechnic")

    def test_school_logo_when_the_institute_has_none(self):
        brand = email_brand(self._user(institute=self.institute))
        self.assertEqual(brand["brand_logo_url"], "https://cdn.example.com/school.png")

    def test_school_logo_for_a_school_admin(self):
        brand = email_brand(self._user(school=self.school))
        self.assertEqual(brand["brand_logo_url"], "https://cdn.example.com/school.png")

    def test_platform_default_when_attached_to_nothing(self):
        superuser = User.objects.create_superuser(username="root", email="r@example.com", password="x")
        self.assertEqual(email_brand(superuser)["brand_logo_url"], "https://cdn.example.com/platform.png")
        self.assertEqual(email_brand()["brand_logo_url"], "https://cdn.example.com/platform.png")

    @override_settings(EMAIL_BRAND_LOGO_URL="")
    def test_no_default_means_no_logo_not_a_broken_image(self):
        self.assertIsNone(email_brand()["brand_logo_url"])


# ---------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------


@override_settings(EMAIL_SECRETS_KEYS=(KEY_A,))
class EmailSenderApiTests(APITestCase):
    def setUp(self):
        self.school = _school()
        self.institute = _institute(self.school)
        self.admin = User.objects.create_superuser(username="platform", email="p@example.com", password="x")
        self.client.force_authenticate(user=self.admin)
        self.url = reverse("schools-detail", args=[self.school.pk])

    def test_setting_a_sender_stores_the_password_encrypted_and_never_returns_it(self):
        response = self.client.patch(
            self.url,
            {"email_host_user": "Registrar@NPC.edu.ph", "email_host_password": "abcd efgh ijkl mnop"},
            format="multipart",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.school.refresh_from_db()

        self.assertEqual(self.school.email_host_user, "registrar@npc.edu.ph")
        self.assertNotIn("abcd", self.school.email_host_password_encrypted)
        # Google's cosmetic spaces are dropped; SMTP wants the 16 characters.
        self.assertEqual(self.school.get_email_host_password(), "abcdefghijklmnop")

        self.assertTrue(response.data["has_email_password"])
        self.assertTrue(response.data["email_sender_configured"])
        self.assertNotIn("email_host_password", response.data)
        self.assertNotIn("email_host_password_encrypted", response.data)

    def test_omitting_the_password_keeps_the_stored_one(self):
        _configure(self.school, user="registrar@npc.edu.ph", password="keep-me")
        response = self.client.patch(
            self.url, {"default_from_email": "NPC <registrar@npc.edu.ph>"}, format="multipart"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.school.refresh_from_db()
        self.assertEqual(self.school.get_email_host_password(), "keep-me")
        self.assertEqual(self.school.default_from_email, "NPC <registrar@npc.edu.ph>")

    def test_blanking_the_address_switches_the_sender_off_and_drops_the_password(self):
        _configure(self.school, user="registrar@npc.edu.ph", password="gone", from_email="X <x@y.z>")
        response = self.client.patch(self.url, {"email_host_user": ""}, format="multipart")
        self.assertEqual(response.status_code, 200, response.data)
        self.school.refresh_from_db()
        self.assertEqual(self.school.email_host_user, "")
        self.assertEqual(self.school.email_host_password_encrypted, "")
        self.assertEqual(self.school.default_from_email, "")
        self.assertFalse(response.data["email_sender_configured"])

    def test_a_password_needs_an_address(self):
        response = self.client.patch(self.url, {"email_host_password": "abc"}, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertIn("email_host_user", response.data)

    def test_an_address_needs_a_password(self):
        response = self.client.patch(self.url, {"email_host_user": "r@npc.edu.ph"}, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertIn("email_host_password", response.data)

    def test_changing_the_address_needs_a_fresh_password(self):
        _configure(self.school, user="old@npc.edu.ph", password="old-password")
        response = self.client.patch(self.url, {"email_host_user": "new@npc.edu.ph"}, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertIn("email_host_password", response.data)
        # ...and with one, the switch goes through.
        response = self.client.patch(
            self.url,
            {"email_host_user": "new@npc.edu.ph", "email_host_password": "new-password"},
            format="multipart",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.school.refresh_from_db()
        self.assertEqual(self.school.get_email_host_password(), "new-password")

    def test_from_address_accepts_a_display_name_and_rejects_nonsense(self):
        _configure(self.school, user="r@npc.edu.ph", password="p")
        ok = self.client.patch(
            self.url, {"default_from_email": "NPC Registrar <r@npc.edu.ph>"}, format="multipart"
        )
        self.assertEqual(ok.status_code, 200, ok.data)
        bad = self.client.patch(self.url, {"default_from_email": "not an address"}, format="multipart")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("default_from_email", bad.data)

    def test_create_with_a_sender(self):
        response = self.client.post(
            reverse("schools-list"),
            {
                "school_name": "Coastal College", "location": "Davao",
                "email_host_user": "cc@gmail.com", "email_host_password": "cc-app-password",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.data)
        created = School.objects.get(pk=response.data["id"])
        self.assertEqual(created.get_email_host_password(), "cc-app-password")

    def test_institute_carries_the_same_fields_and_never_the_ciphertext(self):
        url = reverse("institutes-detail", args=[self.institute.pk])
        response = self.client.patch(
            url, {"email_host_user": "ic@gmail.com", "email_host_password": "ic-password"}, format="multipart"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.institute.refresh_from_db()
        self.assertEqual(self.institute.get_email_host_password(), "ic-password")

        detail = self.client.get(url).data
        self.assertTrue(detail["has_email_password"])
        self.assertEqual(detail["email_host_user"], "ic@gmail.com")
        self.assertNotIn("email_host_password_encrypted", detail)
        self.assertNotIn("email_host_password", detail)

        row = self.client.get(reverse("institutes-list")).data["data"][0]
        self.assertTrue(row["email_sender_configured"])
        self.assertNotIn("email_host_password_encrypted", row)

    # -- what a row actually sends as, and what it would fall back to ---------

    @override_settings(**PLATFORM, EMAIL_BRAND_NAME="DNSC Systems")
    def test_effective_sender_names_the_fallback_when_nothing_is_set(self):
        # Nothing on the school: it sends as the platform, and says so.
        school = self.client.get(self.url).data
        self.assertEqual(
            school["effective_email_sender"],
            {"source": "platform", "from_email": "Platform <platform@example.com>", "label": "DNSC Systems"},
        )
        # Nothing on the institute either: the platform, through the school.
        institute = self.client.get(reverse("institutes-detail", args=[self.institute.pk])).data
        self.assertEqual(institute["effective_email_sender"]["source"], "platform")

        # Set the school up: the institute now falls back to the SCHOOL.
        _configure(self.school, user="registrar@npc.edu.ph", password="p", from_email="NPC <registrar@npc.edu.ph>")
        institute = self.client.get(reverse("institutes-detail", args=[self.institute.pk])).data
        self.assertEqual(
            institute["effective_email_sender"],
            {"source": "school", "from_email": "NPC <registrar@npc.edu.ph>", "label": "Northern Polytechnic"},
        )
        # And with its own, itself.
        _configure(self.institute, user="ic@gmail.com", password="p")
        institute = self.client.get(reverse("institutes-detail", args=[self.institute.pk])).data
        self.assertEqual(
            institute["effective_email_sender"],
            {"source": "institute", "from_email": "ic@gmail.com", "label": "Institute of Computing"},
        )
        # Never the login.
        for key in ("username", "password"):
            self.assertNotIn(key, institute["effective_email_sender"])

    @override_settings(**PLATFORM, EMAIL_BRAND_NAME="DNSC Systems")
    def test_defaults_endpoint_shows_the_platform_mailbox_without_the_login(self):
        response = self.client.get(reverse("email-sender-defaults"))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data["platform"],
            {
                "source": "platform",
                "from_email": "Platform <platform@example.com>",
                "label": "DNSC Systems",
                "configured": True,
            },
        )
        with override_settings(EMAIL_HOST_USER="", EMAIL_HOST_PASSWORD="", DEFAULT_FROM_EMAIL=""):
            response = self.client.get(reverse("email-sender-defaults"))
        self.assertFalse(response.data["platform"]["configured"])
        self.assertEqual(response.data["platform"]["from_email"], "")

    def test_defaults_endpoint_is_for_staff(self):
        student = User.objects.create_user(username="stu", email="s@example.com", password="x")
        self.client.force_authenticate(user=student)
        self.assertEqual(self.client.get(reverse("email-sender-defaults")).status_code, 403)
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(reverse("email-sender-defaults")).status_code, 401)
