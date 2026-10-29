from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework import views, status
from rest_framework.response import Response
from api.utils.cloudinary.cloudinary_utils import (
    upload_image,
    upload_image_versioned,
    get_file_url,
    delete_file,
)


from api.utils.cloudinary.serializers import (
    CloudinaryUploadSerializer,
    CloudinaryResponseSerializer,
    CloudinaryRetrieveSerializer,
)
from api.rbac import ActionPermission
from rest_framework.permissions import IsAuthenticated


@extend_schema(tags=["Cloudinary"])
class CloudinaryView(views.APIView):
    permission_classes = [IsAuthenticated, ActionPermission]
    # Students need this too - a GCash receipt is uploaded here before the
    # submission that references it is created.
    required_permissions = {
        "get": "rbac.upload_media",
        "post": "rbac.upload_media",
        "delete": "rbac.upload_media",
    }
    serializer_class = CloudinaryUploadSerializer

    @extend_schema(
        request=CloudinaryUploadSerializer,
        responses=CloudinaryResponseSerializer,
        summary="Upload a new image to Cloudinary",
    )
    def post(self, request):
        image = request.FILES.get("image")
        folder = request.data.get("folder")
        if not image or not folder:
            return Response(
                {"error": "image or folder is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        uploaded = upload_image_versioned(image, folder)
        # The caller must store `version` beside `public_id`: reads and
        # deletes of this asset need it once a second account exists.
        return Response(uploaded._asdict(), status=status.HTTP_201_CREATED)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="public_id",
                description="The public_id of the Cloudinary image",
                required=True,
                type=str,
                location=OpenApiParameter.QUERY,
            )
        ],
        responses=CloudinaryRetrieveSerializer,
        summary="Get a single image URL by public_id",
    )
    def get(self, request):
        pid = request.query_params.get("public_id")
        if not pid:
            return Response({"error": "public_id is required"})
        image_url = get_file_url(public_id=pid)
        if not image_url:
            return Response(
                {"error": "Image not found"}, status=status.HTTP_404_NOT_FOUND
            )
        return Response({"image_url": image_url}, status=status.HTTP_200_OK)

    @extend_schema(
        summary="Delete the image by public_id",
        parameters=[
            OpenApiParameter(
                name="public_id",
                description="The upublic_id of the image to be deleted",
                required=True,
                type=str,
                location=OpenApiParameter.QUERY,
            )
        ],
    )
    def delete(self, request):
        pid = request.query_params.get("public_id")
        if not pid:
            return Response({"error": "public_id is required"})
        success = delete_file(public_id=pid)
        if not success:
            return Response(
                {"success": False, "error": "Image not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response({"success": True}, status=status.HTTP_200_OK)
