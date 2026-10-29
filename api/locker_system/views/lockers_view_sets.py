from django.db import transaction

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from drf_spectacular.utils import extend_schema


from api.locker_system.models import Locker, LockerSubscription
from api.locker_system.serializers import (
    LockerReadSerializer,
    LockerReadSlimSerializer,
    LockerUpdateSeralizer,
    LockerDeleteSeralizer,
    LockerCreateSerializer,
    LockerBulkCreateSerializer,
    LockerBulkDeleteSerializer,
    LockerQueryParamsSerializer,
)
from api.rbac import ActionPermission, crud
from api.utils.custom_paginations import CustomPagination

# from api.utils.helpers.app_settings import get_setting
# from .filters import filter_available_lockers_this_year_sem


@extend_schema(tags=["Lockers"])
class LockerViewSet(viewsets.ModelViewSet):
    # The read serializers nest the building (renders school.school_name) and
    # the institute (renders school columns) on every row.
    queryset = Locker.objects.select_related(
        "building__school", "institute__school"
    ).order_by("id")
    pagination_class = CustomPagination
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = crud(
        "locker_system",
        "locker",
        bulk_create="locker_system.bulk_add_locker",
        bulk_delete="locker_system.bulk_delete_locker",
        get_available_lockers="locker_system.view_locker",
    )
    ordering_fields = ["id"]

    def get_serializer_class(self):
        if self.action == "create":
            return LockerCreateSerializer
        elif self.action == "retrieve":
            return LockerReadSerializer
        elif self.action in ["update", "partial_update"]:
            return LockerUpdateSeralizer
        elif self.action == "destroy":
            return LockerDeleteSeralizer
        elif self.action == "bulk_delete":
            return LockerBulkDeleteSerializer
        elif self.action == "bulk_create":
            return LockerBulkCreateSerializer
        return LockerReadSlimSerializer

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return self.queryset
        if user.is_staff:
            return self.queryset.filter(institute=user.institute)
        # if hasattr(user, "student"):
        #     return filter_available_lockers_this_year_sem(
        #         self = self,
        #         institute=user.institute,
        #     )
        return self.queryset.none()

    def perform_create(self, serializer):
        return serializer.save(institute=self.request.user.institute)

    @action(detail=False, methods=["post"], url_path="bulk-create")
    def bulk_create(self, request):
        serializer = LockerBulkCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        building = serializer.validated_data["building"]
        floor = serializer.validated_data["floor"]
        start = serializer.validated_data["start_locker_number"]
        end = serializer.validated_data["end_locker_number"]

        locker_numbers = list(range(start, end + 1))

        conflicts = Locker.objects.filter(
            building=building, floor=floor, locker_number__in=locker_numbers
        ).values_list("locker_number", flat=True)

        if conflicts.exists():
            return Response(
                {
                    "detail": "Some lockers already exist.",
                    "conflicting_lockers": list(conflicts),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            lockers = [
                Locker(
                    building=building,
                    floor=floor,
                    locker_number=num,
                    institute=self.request.user.institute,
                )
                for num in locker_numbers
            ]
            Locker.objects.bulk_create(lockers)

        return Response(
            {"detail": f"{len(lockers)} lockers created successfully."},
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["delete"], url_path="bulk-delete")
    def bulk_delete(self, request):
        serializer = LockerBulkDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        building = serializer.validated_data["building"]
        floor = serializer.validated_data["floor"]
        start = serializer.validated_data["start_locker_number"]
        end = serializer.validated_data["end_locker_number"]

        qs = Locker.objects.filter(
            building=building, floor=floor, locker_number__range=(start, end)
        )

        count = qs.count()
        qs.delete()

        return Response(
            {"detail": f"{count} lockers deleted."}, status=status.HTTP_200_OK
        )

    @extend_schema(
        parameters=[LockerQueryParamsSerializer],
        responses=LockerReadSlimSerializer(many=True),
        description="Get All available lockers filtered by which Intitute you enrolled to and allowed lockers to have."
    )
    @action(detail=False, methods=["get"], url_path="available")
    def get_available_lockers(self, request):
        serializer = LockerQueryParamsSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        institute = self.request.user.institute

        # lockers = Locker.objects.filter(institute=user.institute)
        lockers_rented_ids = LockerSubscription.objects.filter(
            locker__institute=institute,
            academic_year=serializer.validated_data["academic_year"],
            semester=serializer.validated_data["semester"],
            status = LockerSubscription.SubscriptionStatus.ACTIVE
        ).values_list("locker_id", flat=True)
        queryset = Locker.objects.filter(institute=institute).exclude(
            id__in=lockers_rented_ids
        ).order_by("id")

        # Added Pagination
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = LockerReadSlimSerializer(page, many=True, context={"request": request})
            return self.get_paginated_response(serializer.data)

        serializer = LockerReadSlimSerializer(
            queryset, many=True, context={"request": request}
        )
        return Response(serializer.data)
