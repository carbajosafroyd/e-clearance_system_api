import os
import uuid
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile

ALLOWED_EXTENSIONS = ["jpg", "jpeg", "png", "gif"]
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB

def validate_image_file(image):
    ext = image.name.split('.')[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"{image.name} has invalid extension. Only {ALLOWED_EXTENSIONS} allowed.")
    if image.size > MAX_FILE_SIZE:
        raise ValueError(f"{image.name} is too large. Max size is {MAX_FILE_SIZE / (1024*1024)} MB.")

def _generate_unique_filename(original_name):
    """Append a UUID before the file extension to avoid conflicts"""
    name, ext = os.path.splitext(original_name)
    unique_name = f"{name}_{uuid.uuid4().hex}{ext}"
    return unique_name

def upload_image(image, folder="uploads/"):
    """
    Save single image to server storage with auto-renaming to avoid conflicts.
    Returns "public_id" (relative path)
    """
    validate_image_file(image)
    folder_path = os.path.join(settings.MEDIA_ROOT, folder)
    os.makedirs(folder_path, exist_ok=True)

    # Generate unique filename
    filename = _generate_unique_filename(image.name)
    file_path = os.path.join(folder_path, filename)

    saved_path = default_storage.save(file_path, ContentFile(image.read()))
    return os.path.relpath(saved_path, settings.MEDIA_ROOT)

def upload_images(images, folder="uploads/"):
    """Save multiple images with unique filenames"""
    public_ids = []
    for image in images:
        public_ids.append(upload_image(image, folder=folder))
    return public_ids

def delete_image(public_id):
    """Delete image from server storage using public_id"""
    full_path = os.path.join(settings.MEDIA_ROOT, public_id)
    if os.path.exists(full_path):
        os.remove(full_path)
        return True
    return False

def get_image_url(public_id, **options):
    """Return URL for accessing saved image"""
    return os.path.join(settings.MEDIA_URL, public_id)


""""
FOR SETUP IF FUTURE DEVS WILL USE SERVER SIDE IMAGE STORING

######################################################################################################
$$ settings.py $$

import os
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Media (uploaded files) settings
MEDIA_ROOT = os.path.join(BASE_DIR, "media")  # physical folder
MEDIA_URL = "/media/"                          # URL to access uploaded files

######################################################################################################
$$ urls.py (serve media in development) $$

from django.conf import settings
from django.conf.urls.static import static
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    # ... your app urls
]

# Serve media files during development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    
######################################################################################################
$$ Example yse in django view $$

from django.shortcuts import render
from django.http import JsonResponse
from .utils.images import upload_image, get_image_url

def upload_receipt(request):
    if request.method == "POST" and request.FILES.get("receipt"):
        try:
            public_id = upload_image(request.FILES["receipt"], folder="receipts/")
            url = get_image_url(public_id)
            return JsonResponse({"success": True, "url": url, "public_id": public_id})
        except ValueError as e:
            return JsonResponse({"success": False, "error": str(e)})
    return JsonResponse({"success": False, "error": "No file uploaded"})

######################################################################################################
$$ Example use in serializers $$

from rest_framework import serializers
from .utils.images import upload_image

class PaymentReceiptSerializer(serializers.Serializer):
    receipt = serializers.ImageField()

    def create(self, validated_data):
        image = validated_data["receipt"]
        public_id = upload_image(image, folder="receipts/")
        return {"public_id": public_id}

"""