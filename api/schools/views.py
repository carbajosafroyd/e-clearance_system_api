from django.shortcuts import render

# Create your views here.

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, DjangoModelPermissions
from rest_framework.views import APIView
from rest_framework.exceptions import PermissionDenied

from .models import School, SchoolBuilding
from api.schools.serializers import (
    SchoolCreateSerializer,
    SchoolReadSerializer,
    SchoolUpdateSerializer,
    SchoolDeleteSerializer,
    ShoolBuildingDeleteSerializer,
    SchoolBuildinCreateSerializer,
    SchoolBuildingReadSlimSerializer,
    SchoolBuildingReadSerializer,
)
from api.rbac import ActionPermission, crud
from api.rbac.scoping import school_id_of
from api.utils.custom_paginations import CustomPagination

from drf_spectacular.utils import extend_schema
from drf_spectacular.types import OpenApiTypes

from core.services.email_sender import platform_configured, platform_sender
from .permissions import IsSchoolStaff
from .filters import SchoolBuildingFilterSet, SchoolFilterSet
from api.utils.cloudinary.cloudinary_utils import delete_by_prefix, remove_image
from api.utils.cloudinary.paths import owner_root

@extend_schema(tags=["School"])
class SchoolViewSet(viewsets.ModelViewSet):
    queryset = School.objects.all().order_by("school_name")
    pagination_class = CustomPagination  # list actions use this
    permission_classes = [IsAuthenticated, ActionPermission, IsSchoolStaff]
    required_permissions = crud("schools", "school")

    filterset_class = SchoolFilterSet
    search_fields = ["school_name", "short_name", "location"]
    ordering_fields = ["id", "school_name", "short_name", "location", "created_at"]

    def get_queryset(self):
        # One school for everyone but a superuser - the one the account is
        # attached to, directly or through its institute. The list used to
        # be every school on the platform for any staff account, with only
        # the detail route checking ownership.
        user = self.request.user
        if user.is_superuser:
            return self.queryset
        school_id = school_id_of(user)
        if school_id is None:
            return self.queryset.none()
        return self.queryset.filter(pk=school_id)

    def perform_destroy(self, instance):
        # Its own images may predate the folder tree, so they go by id. Then,
        # once the row is gone, the whole subtree - every institute under the
        # school and everything under those - goes by prefix, on every
        # account (see paths.py). The prefix is read first: it needs the pk.
        prefix = owner_root(instance)
        remove_image(instance.logo, version=instance.logo_cloudinary_version)
        remove_image(instance.background_image, version=instance.background_cloudinary_version)
        super().perform_destroy(instance)
        delete_by_prefix(prefix)

    def get_serializer_class(self):
        if self.action == "create":
            return SchoolCreateSerializer
        elif self.action == "retrieve":
            return SchoolReadSerializer
        elif self.action in ["update", "partial_update"]:
            return SchoolUpdateSerializer
        elif self.action == "destroy":
            return SchoolDeleteSerializer
        return SchoolReadSerializer


@extend_schema(tags=["School Buildings"])
class SchoolBuildingViewSet(viewsets.ModelViewSet):
    # school: the slim serializer reads school.school_name per row.
    queryset = SchoolBuilding.objects.select_related("school").order_by("id")
    pagination_class = CustomPagination
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = crud("schools", "schoolbuilding")

    filterset_class = SchoolBuildingFilterSet
    search_fields = ["name", "school__school_name"]
    ordering_fields = ["id", "name", "max_floor", "school__school_name", "created_at"]

    def get_serializer_class(self):
        if self.action in ["create", "update", "partial_update"]:
            return SchoolBuildinCreateSerializer
        elif self.action == "retrieve":
            return SchoolBuildingReadSerializer
        elif self.action == "destroy":
            return ShoolBuildingDeleteSerializer
        return SchoolBuildingReadSlimSerializer

    def perform_create(self, serializer):
        # A building belongs to the caller's school, however the account is
        # attached to it. A superuser names the school in the payload.
        user = self.request.user
        requested = self.request.data.get("school") if user.is_superuser else None
        school_id = requested or school_id_of(user)
        if not school_id:
            raise PermissionDenied("User must belong to a school or institute to create this object.")
        serializer.save(school_id=school_id)

    def get_queryset(self):
        # The caller's school - through `user.school` or their institute -
        # for everybody but a superuser. This used to demand `is_staff` AND
        # a direct `user.school`, so an administrator attached only through
        # their institute saw no buildings at all.
        user = self.request.user
        if user.is_superuser:
            return self.queryset
        school_id = school_id_of(user)
        if school_id is None:
            return self.queryset.none()
        return self.queryset.filter(school_id=school_id)


@extend_schema(
    tags=["School"],
    summary="The platform's email sender",
    description=(
        "What a school (or an institute whose school has no sender) falls back "
        "to: the mailbox from the server's environment. From-line and brand "
        "name only - never the login. `configured` is false on a deployment "
        "that has no platform mailbox at all, in which case mail from an "
        "unconfigured school cannot go out."
    ),
    responses={200: OpenApiTypes.OBJECT},
)
class EmailSenderDefaultsView(APIView):
    """`GET email-sender/defaults/` - for the console's school form."""

    permission_classes = [IsAuthenticated, IsSchoolStaff]

    def get(self, request):
        return Response(
            {
                "platform": {
                    **platform_sender().public(),
                    "configured": platform_configured(),
                }
            }
        )
