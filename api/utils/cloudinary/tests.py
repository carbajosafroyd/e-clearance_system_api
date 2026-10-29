"""
Versioned Cloudinary accounts: new uploads go to the current account, every
existing asset is read and deleted under the version its row stores, and a
row that does not know its version is looked up across accounts once.

The SDK is mocked at the call boundary; what these tests pin down is WHICH
account each call names.
"""
from unittest.mock import patch

import cloudinary.api
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from api.utils.cloudinary import cloudinary_utils as cu

ACCOUNTS = {
    1: {"cloud_name": "old-cloud", "api_key": "k1", "api_secret": "s1"},
    2: {"cloud_name": "new-cloud", "api_key": "k2", "api_secret": "s2"},
}


def _png(name="a.png"):
    return SimpleUploadedFile(name, b"\x89PNG\r\n\x1a\n", content_type="image/png")


@override_settings(CLOUDINARY_ACCOUNTS=ACCOUNTS, CLOUDINARY_CURRENT_VERSION=2)
class AccountSelectionTests(SimpleTestCase):
    def test_current_version_and_options(self):
        self.assertEqual(cu.current_version(), 2)
        self.assertEqual(cu.account_options()["cloud_name"], "new-cloud")
        self.assertEqual(cu.account_options(1)["cloud_name"], "old-cloud")

    def test_unknown_version_is_a_configuration_error(self):
        with self.assertRaises(ValueError):
            cu.account_options(9)

    @patch("api.utils.cloudinary.cloudinary_utils.cloudinary.uploader.upload")
    def test_upload_goes_to_the_current_account_and_reports_it(self, upload):
        upload.return_value = {"public_id": "uploads/x", "secure_url": "https://res/x"}
        result = cu.upload_image_versioned(_png(), folder="uploads/")
        self.assertEqual(result.version, 2)
        self.assertEqual(result.public_id, "uploads/x")
        self.assertEqual(upload.call_args.kwargs["cloud_name"], "new-cloud")
        self.assertEqual(cu.upload_image(_png()), "uploads/x")

    @patch("api.utils.cloudinary.cloudinary_utils.cloudinary.uploader.destroy")
    def test_delete_uses_the_stored_version_not_the_current_one(self, destroy):
        cu.delete_image("uploads/x", version=1)
        self.assertEqual(destroy.call_args.kwargs["cloud_name"], "old-cloud")

    def test_url_is_built_for_the_stored_account(self):
        self.assertIn("old-cloud", cu.get_file_url("uploads/x", version=1))
        self.assertIn("new-cloud", cu.get_file_url("uploads/x", version=2))
        self.assertIn("old-cloud", cu.resolve_image_url("uploads/x", version=1))
        # a legacy absolute URL is passed through whatever the version
        self.assertEqual(cu.resolve_image_url("https://elsewhere/a.png", version=2), "https://elsewhere/a.png")

    @patch("api.utils.cloudinary.cloudinary_utils.cloudinary.api.delete_resources")
    def test_bulk_delete_groups_by_account(self, delete_resources):
        cu.delete_files(
            [
                {"public_id": "a", "resource_type": "image", "version": 1},
                {"public_id": "b", "resource_type": "image", "version": 2},
                {"public_id": "c", "resource_type": "image", "version": 1},
            ]
        )
        calls = {call.kwargs["cloud_name"]: call.args[0] for call in delete_resources.call_args_list}
        self.assertEqual(calls, {"old-cloud": ["a", "c"], "new-cloud": ["b"]})


@override_settings(CLOUDINARY_ACCOUNTS=ACCOUNTS, CLOUDINARY_CURRENT_VERSION=2)
class DiscoveryTests(SimpleTestCase):
    """A row with no version: found by cloud name in a URL, or by asking each account."""

    def test_a_url_names_its_account(self):
        self.assertEqual(cu.discover_version("https://res.cloudinary.com/old-cloud/image/upload/v1/a"), 1)
        self.assertEqual(cu.discover_version("https://res.cloudinary.com/new-cloud/image/upload/v1/a"), 2)
        self.assertIsNone(cu.discover_version("https://res.cloudinary.com/somebody-else/image/upload/a"))

    @patch("api.utils.cloudinary.cloudinary_utils.cloudinary.api.resource")
    def test_a_public_id_is_asked_of_each_account_current_first(self, resource):
        def answer(public_id, **kwargs):
            if kwargs["cloud_name"] == "old-cloud":
                return {"public_id": public_id}
            raise cloudinary.api.NotFound()

        resource.side_effect = answer
        self.assertEqual(cu.discover_version("uploads/legacy"), 1)
        asked = [call.kwargs["cloud_name"] for call in resource.call_args_list]
        self.assertEqual(asked, ["new-cloud", "old-cloud"])

    @patch("api.utils.cloudinary.cloudinary_utils.cloudinary.api.resource", side_effect=cloudinary.api.NotFound())
    def test_nobody_has_it(self, _resource):
        self.assertIsNone(cu.discover_version("uploads/gone"))

    @patch("api.utils.cloudinary.cloudinary_utils.cloudinary.uploader.destroy")
    @patch("api.utils.cloudinary.cloudinary_utils.cloudinary.api.resource")
    def test_delete_without_a_version_looks_it_up_first(self, resource, destroy):
        resource.side_effect = lambda public_id, **kw: (
            {"public_id": public_id} if kw["cloud_name"] == "old-cloud" else (_ for _ in ()).throw(cloudinary.api.NotFound())
        )
        cu.delete_file("uploads/legacy")
        self.assertEqual(destroy.call_args.kwargs["cloud_name"], "old-cloud")


class NoAccountsConfiguredTests(SimpleTestCase):
    """A bare dev box with nothing configured behaves as it always did."""

    @override_settings(CLOUDINARY_ACCOUNTS={}, CLOUDINARY_CURRENT_VERSION=1)
    def test_options_are_empty_and_nothing_raises(self):
        self.assertEqual(cu.account_options(), {})
        self.assertEqual(cu.account_options(5), {})
        self.assertIsNone(cu.discover_version("uploads/x"))
