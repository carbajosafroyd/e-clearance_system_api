from django.test import TestCase

# Create your tests here.

from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APITestCase

from api.fees.tests.factories import make_institute


def _png(name="bg.png"):
    """A real one-pixel PNG, so ImageField's Pillow check accepts it."""
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (1, 1), (10, 20, 30)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class BackgroundImageTests(APITestCase):
    """
    The background picture is a second Cloudinary slot beside the logo:
    uploaded through `background_file`, cleared through `remove_background`,
    read back as a URL, and refused when both are sent at once.
    """

    def setUp(self):
        self.institute = make_institute()
        self.admin = get_user_model().objects.create_superuser(
            username="platform", email="platform@example.com", password="x"
        )
        self.client.force_authenticate(user=self.admin)
        self.url = reverse("institutes-detail", args=[self.institute.pk])

    @patch("api.utils.cloudinary.logo_serializer.replace_image", return_value="institutes/backgrounds/abc")
    def test_upload_sets_the_slot_and_leaves_the_logo_alone(self, replace):
        self.institute.logo = "institutes/logos/keep"
        self.institute.save(update_fields=["logo"])
        response = self.client.patch(self.url, {"background_file": _png()}, format="multipart")
        self.assertEqual(response.status_code, 200, response.data)
        self.institute.refresh_from_db()
        self.assertEqual(self.institute.background_image, "institutes/backgrounds/abc")
        self.assertEqual(self.institute.logo, "institutes/logos/keep")
        # filed under the institute's own folder in the tree (paths.py), tagged with its owner
        sid, iid = self.institute.school_id, self.institute.pk
        self.assertEqual(replace.call_args.kwargs["folder"], f"codex_systems/schools/{sid}/institutes/{iid}/backgrounds")
        self.assertEqual(replace.call_args.kwargs["tags"], [f"school:{sid}", f"institute:{iid}"])

    @patch("api.utils.cloudinary.logo_serializer.remove_image")
    def test_remove_clears_the_slot(self, remove):
        self.institute.background_image = "institutes/backgrounds/old"
        self.institute.background_cloudinary_version = 1
        self.institute.save(update_fields=["background_image", "background_cloudinary_version"])
        response = self.client.patch(self.url, {"remove_background": "true"}, format="multipart")
        self.assertEqual(response.status_code, 200, response.data)
        self.institute.refresh_from_db()
        self.assertIsNone(self.institute.background_image)
        self.assertIsNone(self.institute.background_cloudinary_version)
        # deleted on the account the row said it lived on, not the current one
        remove.assert_called_once_with("institutes/backgrounds/old", version=1)

    def test_upload_and_remove_together_is_refused(self):
        response = self.client.patch(
            self.url, {"background_file": _png(), "remove_background": "true"}, format="multipart"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("background_file", response.data)

    def test_read_side_resolves_a_url(self):
        self.institute.background_image = "https://cdn.example.edu/campus.jpg"
        self.institute.save(update_fields=["background_image"])
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["background_image"], "https://cdn.example.edu/campus.jpg")
