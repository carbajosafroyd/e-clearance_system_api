from django.shortcuts import render

# Create your views here.

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, DjangoModelPermissions

from .models import Institute
from api.institutes.serializers import (
    InstituteCreateSerializer,
    InstituteReadSerializer,
    InstituteUpdateSerializer,
    InstituteDeleteSerializer,
    InstituteReadSlimSerializer
)
from api.rbac import ActionPermission, crud
from api.utils.custom_paginations import CustomPagination

from drf_spectacular.utils import extend_schema
from .filters import InstituteFilterSet
from api.utils.cloudinary.cloudinary_utils import delete_by_prefix, remove_image
from api.utils.cloudinary.paths import owner_root


@extend_schema(tags=["Institute"])

class InstituteViewSet(viewsets.ModelViewSet):
    # school: every read serializer renders the school block or its columns.
    queryset = Institute.objects.select_related("school").order_by("institute_name")
    pagination_class = CustomPagination  # list actions use this

    filterset_class = InstituteFilterSet
    search_fields = ["institute_name", "acronym", "school__short_name", "school__school_name"]
    ordering_fields = ["id", "institute_name", "acronym", "school__school_name", "created_at"]

    def get_serializer_class(self):
        if self.action == "create":
            return InstituteCreateSerializer
        elif self.action == "retrieve":
            return InstituteReadSerializer
        elif self.action in ["update", "partial_update"]:
            return InstituteUpdateSerializer
        elif self.action == "destroy":
            return InstituteDeleteSerializer
        return InstituteReadSlimSerializer
    
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = crud("institutes", "institute")

    def perform_destroy(self, instance):
        # Its own images may predate the folder tree, so they go by id. Then,
        # once the row is gone, everything filed under the institute - QR
        # codes, posts, every student's uploads - goes by prefix, on every
        # account (see paths.py). The prefix is read first: it needs the pk.
        prefix = owner_root(instance)
        remove_image(instance.logo, version=instance.logo_cloudinary_version)
        remove_image(instance.background_image, version=instance.background_cloudinary_version)
        super().perform_destroy(instance)
        delete_by_prefix(prefix)

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return self.queryset

        # A school administrator's account hangs off `user.school` and has
        # no institute; an account attached to neither sees nothing rather
        # than raising - `user.institute.school` on a None institute was a
        # guaranteed 500 for every such account.
        school_id = getattr(user, "school_id", None) or getattr(
            getattr(user, "institute", None), "school_id", None
        )
        if school_id is None:
            return self.queryset.none()
        return self.queryset.filter(school_id=school_id)