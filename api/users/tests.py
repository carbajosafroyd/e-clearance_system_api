"""
Invitations: the one-time password, the reserved username, and the school
name the email is branded with.

The password is the part worth pinning down. It is generated once, mailed
once, and stored only as a hash — so the tests check that the plaintext
never reaches the database and that the account it eventually creates can
actually be signed in to with it.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from rest_framework.test import APIClient

from api.institutes.models import Institute
from api.schools.models import School
from api.users.models import UserRegistrationToken
from api.users.serializers import build_registration_token
from api.users.utils import generate_temporary_password, generate_username

User = get_user_model()


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_SEND_ASYNC=False,
)
class InvitationTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(
            school_name="Northern Polytechnic", short_name="NPC", location="Tagum"
        )
        self.institute = Institute.objects.create(
            institute_name="Institute of Computing", school=self.school
        )
        # `get_or_create`: the rbac data migrations sync every role at
        # migrate time, so the group exists before any test runs.
        self.group, _ = Group.objects.get_or_create(name="Students")

        self.inviter = User.objects.create_user(
            username="registrar",
            email="registrar@npc.edu",
            password="s3cret-passphrase",
            first_name="Ana",
            last_name="Reyes",
        )
        self.inviter.institute = self.institute
        self.inviter.save(update_fields=["institute"])

    # -- the token ----------------------------------------------------------

    def test_invitation_reserves_a_username_and_hashes_the_password(self):
        token = build_registration_token(
            email="new.student@gmail.com", group=self.group, institute=self.institute
        )
        token.save()

        self.assertEqual(token.username, "new.student")
        self.assertTrue(token.raw_password)
        # Stored hashed, never in the clear.
        self.assertNotIn(token.raw_password, token.initial_password)
        self.assertTrue(token.initial_password.startswith("pbkdf2_"))

    def test_reserved_username_is_unique_against_users_and_other_invitations(self):
        User.objects.create_user(username="taken", email="taken@x.com")
        first = build_registration_token(
            email="taken@gmail.com", group=self.group, institute=self.institute
        )
        first.save()

        self.assertEqual(first.username, "taken1")

        second = build_registration_token(
            email="taken@yahoo.com", group=self.group, institute=self.institute
        )
        self.assertEqual(second.username, "taken2")

    def test_within_one_batch_usernames_do_not_collide(self):
        reserved: set[str] = set()
        names = []
        for domain in ("gmail.com", "yahoo.com", "outlook.com"):
            token = build_registration_token(
                email=f"jdoe@{domain}",
                group=self.group,
                institute=self.institute,
                reserved_usernames=reserved,
            )
            reserved.add(token.username)
            names.append(token.username)

        self.assertEqual(len(set(names)), 3)

    @override_settings(INVITE_SEND_TEMPORARY_PASSWORD=False)
    def test_password_can_be_switched_off(self):
        token = build_registration_token(
            email="nopass@gmail.com", group=self.group, institute=self.institute
        )
        self.assertEqual(token.raw_password, "")
        self.assertEqual(token.initial_password, "")

    # -- activation ---------------------------------------------------------

    def _activate(self, token):
        return self.client.get(reverse("register"), {"token": token.token})

    def test_activation_sets_the_password_from_the_invitation(self):
        token = build_registration_token(
            email="student@gmail.com", group=self.group, institute=self.institute
        )
        token.save()
        raw_password = token.raw_password

        response = self._activate(token)
        self.assertEqual(response.status_code, 201)

        user = User.objects.get(email="student@gmail.com")
        self.assertTrue(user.check_password(raw_password))
        self.assertEqual(user.username, "student")
        self.assertEqual(user.institute, self.institute)

        # The hash has moved to the account; the invitation no longer holds it.
        token.refresh_from_db()
        self.assertEqual(token.initial_password, "")
        self.assertIsNotNone(token.used_at)

    def test_activation_without_a_password_leaves_the_account_unusable(self):
        with override_settings(INVITE_SEND_TEMPORARY_PASSWORD=False):
            token = build_registration_token(
                email="nopass@gmail.com", group=self.group, institute=self.institute
            )
            token.save()

        self.assertEqual(self._activate(token).status_code, 201)

        user = User.objects.get(email="nopass@gmail.com")
        self.assertFalse(user.has_usable_password())

    def test_an_invitation_activates_once(self):
        token = build_registration_token(
            email="once@gmail.com", group=self.group, institute=self.institute
        )
        token.save()

        self.assertEqual(self._activate(token).status_code, 201)
        self.assertEqual(self._activate(token).status_code, 400)

    def test_activation_falls_back_when_the_reserved_username_was_taken(self):
        token = build_registration_token(
            email="dupe@gmail.com", group=self.group, institute=self.institute
        )
        token.save()

        # Someone claimed it in the days between invite and activation.
        User.objects.create_user(username="dupe", email="someone.else@gmail.com")

        self.assertEqual(self._activate(token).status_code, 201)
        user = User.objects.get(email="dupe@gmail.com")
        self.assertNotEqual(user.username, "dupe")

    # -- the email ----------------------------------------------------------

    def test_invite_email_is_branded_with_the_inviters_school(self):
        # A role, not a bare permission: the invitation endpoint also checks
        # the delegation table (`rbac/roles.py::DELEGATION`), and only a
        # role that appoints Students may invite one.
        from api.rbac.roles import Roles
        from api.rbac.services import assign_role

        assign_role(self.inviter, Roles.INSTITUTE_ADMIN)
        self.inviter = get_user_model().objects.get(pk=self.inviter.pk)

        client = APIClient()
        client.force_authenticate(self.inviter)

        response = client.post(
            reverse("user-invite"),
            {"email": "invited@gmail.com", "group_id": self.group.id},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)

        message = mail.outbox[0]
        body = message.body
        html = message.alternatives[0][0]

        self.assertIn("Northern Polytechnic", body)
        self.assertIn("Northern Polytechnic", html)
        self.assertNotIn("Davao del Norte Systems", html)

        # The three things the recipient needs, all in the message.
        token = UserRegistrationToken.objects.get(email="invited@gmail.com")
        self.assertIn(token.username, body)
        self.assertIn(f"token={token.token}", body)
        self.assertIn("Temporary password", html)

    @staticmethod
    def _permissions(app_label, codename):
        from django.contrib.auth.models import Permission

        return Permission.objects.filter(
            content_type__app_label=app_label, codename=codename
        )


class TemporaryPasswordTests(TestCase):
    def test_password_mixes_letters_and_digits_and_avoids_lookalikes(self):
        for _ in range(50):
            password = generate_temporary_password()
            self.assertGreaterEqual(len(password), 10)
            self.assertTrue(any(c.isdigit() for c in password))
            self.assertTrue(any(c.isalpha() for c in password))
            self.assertFalse(set(password) & set("0O1lI"))

    def test_passwords_pass_djangos_own_validators(self):
        from django.contrib.auth.password_validation import validate_password

        for _ in range(10):
            validate_password(generate_temporary_password())


class UsernameGenerationTests(TestCase):
    def test_strips_characters_a_username_cannot_hold(self):
        self.assertEqual(generate_username("Juan+Tags@gmail.com"), "juantags")

    def test_falls_back_when_the_local_part_is_unusable(self):
        self.assertEqual(generate_username("+++@gmail.com"), "user")


class OwnAvatarTests(TestCase):
    """
    `/me/update/` with a `profile` file: uploaded into the account's own
    folder in the tree (paths.py), tagged with its owner, the row stamped
    with the account version it went to; `remove_profile` deletes it on
    that same version. Cloudinary itself is mocked at the serializer's
    import - this pins the call, not the network.

    Regression: the serializer once used `current_version()` without
    importing it, so every avatar upload 500-ed after the file had already
    gone up.
    """

    ACCOUNTS = {
        1: {"cloud_name": "old-cloud", "api_key": "k1", "api_secret": "s1"},
        2: {"cloud_name": "new-cloud", "api_key": "k2", "api_secret": "s2"},
    }

    def setUp(self):
        self.school = School.objects.create(school_name="Test School", short_name="TS", location="x")
        self.institute = Institute.objects.create(institute_name="Test Institute", acronym="TI", school=self.school)
        self.user = get_user_model().objects.create_user(
            username="staffer", email="staffer@example.com", password="x", institute=self.institute
        )
        self.client = APIClient(HTTP_HOST="127.0.0.1")
        self.client.force_authenticate(user=self.user)

    @staticmethod
    def _png():
        from io import BytesIO

        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile

        buffer = BytesIO()
        Image.new("RGB", (1, 1), (1, 2, 3)).save(buffer, format="PNG")
        return SimpleUploadedFile("me.png", buffer.getvalue(), content_type="image/png")

    @override_settings(CLOUDINARY_ACCOUNTS=ACCOUNTS, CLOUDINARY_CURRENT_VERSION=2)
    def test_upload_files_the_avatar_under_the_account_and_stamps_the_version(self):
        from unittest.mock import patch

        folder = f"codex_systems/schools/{self.school.pk}/institutes/{self.institute.pk}/profiles"
        with patch("api.users.serializers.upload_image", return_value=f"{folder}/abc") as upload:
            response = self.client.patch("/api/v1/me/update/", {"profile": self._png()}, format="multipart")
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.user.refresh_from_db()
        self.assertEqual(self.user.profile, f"{folder}/abc")
        self.assertEqual(self.user.cloudinary_version, 2)
        self.assertEqual(upload.call_args.kwargs["folder"], folder)
        self.assertEqual(
            upload.call_args.kwargs["tags"],
            [f"school:{self.school.pk}", f"institute:{self.institute.pk}", f"user:{self.user.pk}"],
        )

    @override_settings(CLOUDINARY_ACCOUNTS=ACCOUNTS, CLOUDINARY_CURRENT_VERSION=2)
    def test_remove_deletes_on_the_stored_version(self):
        from unittest.mock import patch

        self.user.profile = "users/old"
        self.user.cloudinary_version = 1
        self.user.save(update_fields=["profile", "cloudinary_version"])
        with patch("api.users.serializers.delete_file") as delete:
            response = self.client.patch("/api/v1/me/update/", {"remove_profile": "true"}, format="multipart")
        self.assertEqual(response.status_code, 200, response.content[:300])
        delete.assert_called_once_with("users/old", version=1)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.profile)
        self.assertIsNone(self.user.cloudinary_version)


# ---------------------------------------------------------------------------
# Password pepper
# ---------------------------------------------------------------------------


@override_settings(
    PASSWORD_PEPPERS={1: "pepper-one", 3: "pepper-three"},
    PASSWORD_PEPPER_CURRENT_VERSION=3,
)
class PasswordPepperTests(TestCase):
    """
    Rotation as it happens in production: the environment carries versions
    1 and 3 with 3 current, and users hashed under 1 (or under no pepper at
    all) must keep signing in and be silently moved to 3 when they do.
    """

    def _user_on_version(self, version, raw="Str0ng-pass!"):
        # Build the hash the way that version would have, bypassing the
        # override so the row genuinely looks like it was made back then.
        from django.contrib.auth.hashers import make_password
        from core import pepper

        user = User(username=f"v{version}", email=f"v{version}@gmail.com")
        user.password = make_password(pepper.pepper_password(raw, version))
        user.pepper_version = version
        user.save()
        return user, raw

    def test_new_passwords_use_the_current_pepper(self):
        user = User.objects.create_user("fresh", "fresh@gmail.com", "Str0ng-pass!")
        self.assertEqual(user.pepper_version, 3)
        self.assertTrue(user.check_password("Str0ng-pass!"))
        self.assertFalse(user.check_password("wrong"))

    def test_old_version_verifies_and_is_rehashed_to_current(self):
        user, raw = self._user_on_version(1)
        old_hash = user.password

        self.assertTrue(user.check_password(raw))

        user.refresh_from_db()
        self.assertEqual(user.pepper_version, 3)
        self.assertNotEqual(user.password, old_hash)
        # And the re-hash is the real thing, not just a bumped number.
        self.assertTrue(user.check_password(raw))

    def test_wrong_password_on_old_version_does_not_rehash(self):
        user, raw = self._user_on_version(1)
        old_hash = user.password

        self.assertFalse(user.check_password("not-it"))

        user.refresh_from_db()
        self.assertEqual(user.pepper_version, 1)
        self.assertEqual(user.password, old_hash)

    def test_legacy_unpeppered_hash_is_upgraded_on_sign_in(self):
        user, raw = self._user_on_version(0)

        self.assertTrue(user.check_password(raw))

        user.refresh_from_db()
        self.assertEqual(user.pepper_version, 3)

    def test_retired_pepper_cannot_verify(self):
        # Version 2 was never configured in this environment.
        from django.contrib.auth.hashers import make_password

        user = User(username="v2", email="v2@gmail.com", pepper_version=2)
        user.password = make_password("whatever-it-was")
        user.save()

        self.assertFalse(user.check_password("whatever-it-was"))
        user.refresh_from_db()
        self.assertEqual(user.pepper_version, 2)

    def test_update_fields_password_carries_the_version_along(self):
        # The user seeder and both admin reset flows save with
        # update_fields=["password"] after set_password. The version must
        # ride along, or the row records a hash it does not have and the
        # account cannot sign in - which is exactly how every seeded
        # account shipped once.
        user, _ = self._user_on_version(1)

        user.set_password("N3w-pass!")
        user.save(update_fields=["password"])

        user.refresh_from_db()
        self.assertEqual(user.pepper_version, 3)
        self.assertTrue(user.check_password("N3w-pass!"))

    def test_login_endpoint_rotates_the_pepper(self):
        user, raw = self._user_on_version(1)
        client = APIClient()

        # `reverse` cannot be trusted here: logout/ registers under the same
        # name as login/ in api/urls.py.
        response = client.post(
            "/api/v1/login/", {"username": user.username, "password": raw}
        )

        self.assertEqual(response.status_code, 200, response.content)
        user.refresh_from_db()
        self.assertEqual(user.pepper_version, 3)

    def test_invitation_hash_carries_its_pepper_version(self):
        school = School.objects.create(school_name="S", short_name="S", location="x")
        institute = Institute.objects.create(institute_name="I", school=school)
        group, _ = Group.objects.get_or_create(name="Students")

        token = build_registration_token(
            email="inv@gmail.com", group=group, institute=institute
        )
        self.assertEqual(token.initial_password_pepper_version, 3)
        token.save()

        # Rotate between the invitation and its activation.
        with override_settings(
            PASSWORD_PEPPERS={1: "pepper-one", 3: "pepper-three", 4: "pepper-four"},
            PASSWORD_PEPPER_CURRENT_VERSION=4,
        ):
            response = APIClient().get(reverse("register"), {"token": token.token})
            self.assertEqual(response.status_code, 201, response.content)
            user = User.objects.get(email="inv@gmail.com")
            self.assertEqual(user.pepper_version, 3)
            # Still signs in - and moves to 4 as it does.
            self.assertTrue(user.check_password(token.raw_password))
            user.refresh_from_db()
            self.assertEqual(user.pepper_version, 4)
