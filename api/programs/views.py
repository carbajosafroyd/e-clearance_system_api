from django.shortcuts import render

# Create your views here.

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, DjangoModelPermissions

from .models import Program
from api.programs.serializers import (
    ProgramCreateSerializer,
    ProgramReadSerializer,
    ProgramUpdateSerializer,
    ProgramDeleteSerializer,
)
from api.rbac import ActionPermission, crud
from api.rbac.scoping import is_student_only, scope_queryset
from api.utils.custom_paginations import CustomPagination

from drf_spectacular.utils import extend_schema
from .filters import ProgramFilterSet


@extend_schema(tags=["Program"])
class ProgramViewSet(viewsets.ModelViewSet):
    # institute__school: both read serializers nest the institute, which in
    # turn renders its school.
    queryset = Program.objects.select_related("institute__school").order_by("name")
    pagination_class = CustomPagination  # list actions use this
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = crud("programs", "program")

    filterset_class = ProgramFilterSet
    search_fields = ["name", "acronym", "institute__institute_name"]
    ordering_fields = ["id", "name", "acronym", "status", "institute__institute_name", "created_at"]

    def get_serializer_class(self):
        if self.action == "create":
            return ProgramCreateSerializer
        elif self.action == "retrieve":
            return ProgramReadSerializer
        elif self.action in ["update", "partial_update"]:
            return ProgramUpdateSerializer
        elif self.action == "destroy":
            return ProgramDeleteSerializer
        return ProgramReadSerializer

    def get_queryset(self):
        user = self.request.user
        # Students (and any other non-staff reader) see the whole catalogue:
        # ACADEMIC_READ is deliberately unscoped reference data. Staff see
        # their institute, a school administrator their school, a superuser
        # everything - which this branch used to get wrong twice: a
        # superuser with `is_staff` set was narrowed to their (often empty)
        # institute, and a school administrator to one institute of six.
        if user.is_superuser or is_student_only(user):
            return self.queryset
        return scope_queryset(
            self.queryset,
            user,
            institute_path="institute",
            school_paths=("institute__school",),
        )
