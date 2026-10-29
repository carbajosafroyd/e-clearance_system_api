"""
The folder tree and tags in paths.py, pinned string by string: a change to
the layout is a deliberate edit here, never a surprise in the Media Library.

Owners are unsaved model instances - no database, no Cloudinary. The two
lookups that would need a database (`student_of`, `is_school_wide`) are
patched where a test needs them.
"""
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from api.institutes.models import Institute
from api.posts.models import Post
from api.programs.models import Program
from api.schools.models import School
from api.students.models import Student
from api.users.models import User
from api.utils.cloudinary import cloudinary_utils as cu
from api.utils.cloudinary import paths
from api.utils.cloudinary.paths import Kind, describe_path, folder_for, owner_root, tags_for


def _fixtures():
    school = School(pk=1, school_name="Davao del Norte State College", short_name="DNSC")
    institute = Institute(pk=2, institute_name="Institute of Computing", acronym="IC", school=school)
    program = Program(pk=5, name="BS Information Technology", acronym="BSIT", institute=institute)
    student = Student(
        pk=1234, s_studentID="2021000123", program=program, s_lvl=3, s_set="B",
        s_fname="Ana", s_lname="Cruz",
    )
    return school, institute, program, student


def _user(**fields):
    return User(pk=7, username="someone", **fields)


class RootTests(SimpleTestCase):
    def setUp(self):
        self.school, self.institute, self.program, self.student = _fixtures()

    def test_every_root_by_id(self):
        self.assertEqual(paths.superadmin_root(), "codex_systems/superadmin")
        self.assertEqual(paths.school_root(self.school), "codex_systems/schools/1")
        self.assertEqual(paths.school_root(1), "codex_systems/schools/1")
        self.assertEqual(paths.institute_root(self.institute), "codex_systems/schools/1/institutes/2")
        self.assertEqual(paths.student_root(self.student), "codex_systems/schools/1/institutes/2/students/1234")

    def test_an_unsaved_owner_cannot_own_an_upload(self):
        with self.assertRaises(ValueError):
            paths.school_root(School(school_name="not saved"))

    def test_owner_root_is_the_delete_prefix(self):
        self.assertEqual(owner_root(self.school), "codex_systems/schools/1")
        self.assertEqual(owner_root(self.institute), "codex_systems/schools/1/institutes/2")
        self.assertEqual(owner_root(self.student), "codex_systems/schools/1/institutes/2/students/1234")


class FolderForTests(SimpleTestCase):
    def setUp(self):
        self.school, self.institute, self.program, self.student = _fixtures()

    def test_school_and_institute_images(self):
        self.assertEqual(folder_for(self.school, Kind.LOGOS), "codex_systems/schools/1/logos")
        self.assertEqual(folder_for(self.school, "backgrounds"), "codex_systems/schools/1/backgrounds")
        self.assertEqual(folder_for(self.institute, Kind.LOGOS), "codex_systems/schools/1/institutes/2/logos")
        self.assertEqual(
            folder_for(self.institute, Kind.GCASH_ACCOUNTS), "codex_systems/schools/1/institutes/2/gcash_accounts"
        )

    def test_student_uploads_sit_under_the_student(self):
        self.assertEqual(
            folder_for(self.student, Kind.PAYMENTS), "codex_systems/schools/1/institutes/2/students/1234/payments"
        )
        self.assertEqual(
            folder_for(self.student, Kind.PROFILES), "codex_systems/schools/1/institutes/2/students/1234/profiles"
        )

    def test_a_post_is_its_own_folder(self):
        post = Post(pk=9, institute=self.institute, user=_user())
        self.assertEqual(folder_for(post, Kind.POSTS), "codex_systems/schools/1/institutes/2/posts/9")
        with self.assertRaises(ValueError):
            folder_for(post, Kind.PROFILES)

    def test_a_post_without_an_institute_keeps_the_flat_shape(self):
        self.assertEqual(folder_for(Post(pk=9, user=_user()), Kind.POSTS), "posts/9")

    def test_an_unknown_kind_or_owner_is_refused(self):
        with self.assertRaises(ValueError):
            folder_for(self.school, "thumbnails")
        with self.assertRaises(TypeError):
            folder_for(object(), Kind.PROFILES)


class UserFolderTests(SimpleTestCase):
    """Whose folder an account's own uploads go under - the precedence in `_user_scope`."""

    def setUp(self):
        self.school, self.institute, self.program, self.student = _fixtures()

    def test_a_superuser_goes_under_superadmin_whatever_it_is_attached_to(self):
        user = _user(is_superuser=True, school=self.school, institute=self.institute)
        with patch.object(paths, "student_of", return_value=self.student):
            self.assertEqual(folder_for(user, Kind.PROFILES), "codex_systems/superadmin/profiles")

    def test_a_student_account_is_filed_under_the_student(self):
        user = _user(institute=self.institute)
        with patch.object(paths, "student_of", return_value=self.student):
            self.assertEqual(
                folder_for(user, Kind.PROFILES), "codex_systems/schools/1/institutes/2/students/1234/profiles"
            )

    def test_a_school_admin_over_both_goes_under_the_school(self):
        user = _user(school=self.school, institute=self.institute)
        with patch.object(paths, "student_of", return_value=None), patch.object(
            paths, "is_school_wide", return_value=True
        ):
            self.assertEqual(folder_for(user, Kind.PROFILES), "codex_systems/schools/1/profiles")

    def test_a_school_only_account_needs_no_role_lookup(self):
        user = _user(school=self.school)
        with patch.object(paths, "student_of", return_value=None), patch.object(
            paths, "is_school_wide", side_effect=AssertionError("should not be asked")
        ):
            self.assertEqual(folder_for(user, Kind.PROFILES), "codex_systems/schools/1/profiles")

    def test_institute_staff_go_under_the_institute(self):
        user = _user(school=self.school, institute=self.institute)
        with patch.object(paths, "student_of", return_value=None), patch.object(
            paths, "is_school_wide", return_value=False
        ):
            self.assertEqual(folder_for(user, Kind.PROFILES), "codex_systems/schools/1/institutes/2/profiles")

    def test_an_unattached_account_is_unfiled(self):
        with patch.object(paths, "student_of", return_value=None):
            self.assertEqual(folder_for(_user(), Kind.PROFILES), "users/profiles")


class TagTests(SimpleTestCase):
    def setUp(self):
        self.school, self.institute, self.program, self.student = _fixtures()

    def test_owner_tags(self):
        self.assertEqual(tags_for(self.school), ["school:1"])
        self.assertEqual(tags_for(self.institute), ["school:1", "institute:2"])
        self.assertEqual(
            tags_for(self.student),
            ["school:1", "institute:2", "student:1234", "program:BSIT", "year:3", "set:B"],
        )
        post = Post(pk=9, institute=self.institute, user=_user())
        self.assertEqual(tags_for(post), ["school:1", "institute:2", "post:9"])

    def test_user_tags_follow_the_folder(self):
        with patch.object(paths, "student_of", return_value=None):
            self.assertEqual(tags_for(_user(is_superuser=True)), ["codex_systems/superadmin", "user:7"])
            self.assertEqual(tags_for(_user()), ["user:7"])
            with patch.object(paths, "is_school_wide", return_value=False):
                self.assertEqual(
                    tags_for(_user(school=self.school, institute=self.institute)),
                    ["school:1", "institute:2", "user:7"],
                )
        with patch.object(paths, "student_of", return_value=self.student):
            self.assertEqual(
                tags_for(_user(institute=self.institute)),
                ["school:1", "institute:2", "student:1234", "program:BSIT", "year:3", "set:B", "user:7"],
            )

    def test_values_are_sanitised_and_empties_dropped(self):
        self.program.acronym = "BS IT/CS"
        self.student.s_set = None
        self.assertEqual(
            tags_for(self.student), ["school:1", "institute:2", "student:1234", "program:BSITCS", "year:3"]
        )


class DescribePathTests(SimpleTestCase):
    def test_namespaced_paths_require_complete_owner_root_segments(self):
        for public_id in (
            "codex_systems_extra/schools/1/logos/file",
            "codex_systems/schools_extra/1/logos/file",
            "codex_systems/schools/1/institutes/2/../profiles/file",
        ):
            with self.subTest(public_id=public_id):
                self.assertTrue(describe_path(public_id).legacy)

    def setUp(self):
        self.school, self.institute, self.program, self.student = _fixtures()

    def test_a_tree_id_reads_back_to_its_owner(self):
        info = describe_path(folder_for(self.student, Kind.PAYMENTS) + "/abc123")
        self.assertFalse(info.legacy)
        self.assertEqual((info.school, info.institute, info.student), (1, 2, 1234))
        self.assertEqual((info.kind, info.file), (Kind.PAYMENTS, "abc123"))
        self.assertEqual(info.owner, "student 1234 (school 1, institute 2)")

        info = describe_path(folder_for(self.institute, Kind.GCASH_ACCOUNTS) + "/qr1")
        self.assertEqual((info.school, info.institute, info.student, info.kind), (1, 2, None, Kind.GCASH_ACCOUNTS))
        self.assertEqual(info.owner, "institute 2 (school 1)")

        info = describe_path(folder_for(self.school, Kind.LOGOS) + "/l")
        self.assertEqual((info.school, info.institute, info.kind), (1, None, Kind.LOGOS))
        self.assertEqual(info.owner, "school 1")

        post = Post(pk=9, institute=self.institute, user=_user())
        info = describe_path(folder_for(post, Kind.POSTS) + "/v1")
        self.assertEqual((info.post, info.kind, info.file), (9, Kind.POSTS, "v1"))
        self.assertEqual(info.owner, "post 9 (school 1, institute 2)")

        info = describe_path("codex_systems/superadmin/profiles/me")
        self.assertEqual((info.root, info.kind, info.owner), ("codex_systems/superadmin", Kind.PROFILES, "superadmin"))
        self.assertEqual(describe_path("users/profiles/x").owner, "unattached account")

    def test_anything_from_before_the_tree_is_legacy_not_an_error(self):
        for public_id in [
            "users/abc", "institutes/logos/x", "codex_systems/schools/backgrounds/x", "gcash_qr/x",
            "payment_submissions/x", "posts/3/x", "codex_systems/schools/1/x", "codex_systems/schools/1/institutes/2/x",
            "codex_systems/schools/one/logos/x", "https://res.cloudinary.com/c/image/upload/v1/a.png", "", None,
        ]:
            self.assertTrue(describe_path(public_id).legacy, public_id)
            self.assertEqual(describe_path(public_id).owner, "legacy (pre-tree id)")


ACCOUNTS = {
    1: {"cloud_name": "old-cloud", "api_key": "k1", "api_secret": "s1"},
    2: {"cloud_name": "new-cloud", "api_key": "k2", "api_secret": "s2"},
}


@override_settings(
    CLOUDINARY_ACCOUNTS=ACCOUNTS, CLOUDINARY_CURRENT_VERSION=2, CLOUDINARY_PREFIX_DELETE_ENABLED=True
)
class UploadCarriesFolderAndTagsTests(SimpleTestCase):
    @patch("api.utils.cloudinary.cloudinary_utils.cloudinary.uploader.upload")
    def test_folder_and_tags_reach_the_sdk(self, upload):
        upload.return_value = {"public_id": "codex_systems/schools/1/logos/x", "secure_url": "https://res/x"}
        png = SimpleUploadedFile("a.png", b"\x89PNG\r\n\x1a\n", content_type="image/png")
        cu.upload_image(png, folder="codex_systems/schools/1/logos", tags=["school:1"])
        kwargs = upload.call_args.kwargs
        self.assertEqual(kwargs["folder"], "codex_systems/schools/1/logos")
        self.assertEqual(kwargs["tags"], ["school:1"])
        self.assertEqual(kwargs["cloud_name"], "new-cloud")

    @patch("api.utils.cloudinary.cloudinary_utils.cloudinary.uploader.upload")
    def test_no_tags_means_no_tags_argument(self, upload):
        upload.return_value = {"public_id": "uploads/x", "secure_url": "https://res/x"}
        png = SimpleUploadedFile("a.png", b"\x89PNG\r\n\x1a\n", content_type="image/png")
        cu.upload_image(png, folder="uploads/")
        self.assertNotIn("tags", upload.call_args.kwargs)

    @patch("api.utils.cloudinary.cloudinary_utils.cloudinary.api.delete_resources_by_prefix")
    def test_delete_by_prefix_hits_every_account_and_resource_type(self, delete):
        cu.delete_by_prefix("codex_systems/schools/1/institutes/2")
        calls = [
            (c.args[0], c.kwargs["resource_type"], c.kwargs["cloud_name"]) for c in delete.call_args_list
        ]
        self.assertCountEqual(
            calls,
            [
                ("codex_systems/schools/1/institutes/2/", "image", "old-cloud"),
                ("codex_systems/schools/1/institutes/2/", "video", "old-cloud"),
                ("codex_systems/schools/1/institutes/2/", "image", "new-cloud"),
                ("codex_systems/schools/1/institutes/2/", "video", "new-cloud"),
            ],
        )

    @patch("api.utils.cloudinary.cloudinary_utils.cloudinary.api.delete_resources_by_prefix")
    def test_prefix_delete_is_inert_under_the_test_runner_flag(self, delete):
        with override_settings(CLOUDINARY_PREFIX_DELETE_ENABLED=False):
            self.assertEqual(cu.delete_by_prefix("codex_systems/schools/1/"), {})
        delete.assert_not_called()

    @patch("api.utils.cloudinary.cloudinary_utils.cloudinary.api.delete_resources_by_prefix")
    def test_one_refusing_account_does_not_stop_the_others(self, delete):
        delete.side_effect = [Exception("revoked"), Exception("revoked"), {"deleted": {}}, {"deleted": {}}]
        result = cu.delete_by_prefix("codex_systems/schools/1/")
        self.assertEqual(sorted(result), ["image", "video"])
        self.assertEqual(list(result["image"]), [2])
