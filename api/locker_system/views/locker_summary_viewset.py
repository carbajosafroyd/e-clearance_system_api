from rest_framework.viewsets import ViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response

from drf_spectacular.utils import extend_schema

from api.locker_system.models import Locker, LockerSubscription
from api.rbac import DEFAULT, DENY, ActionPermission
from api.locker_system.serializers import (
    LockerMetricsSerializer,
    LockerQueryParamsSerializer,
)
from api.students.models import Student


@extend_schema(tags=["Lockers"])
class LockerSummaryViewSet(ViewSet):
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = {
        "metrics": "locker_system.view_locker_summary",
        DEFAULT: DENY,
    }

    # A plain ViewSet has no get_serializer() for the schema to fall back on,
    # so the response has to be spelled out.
    @extend_schema(
        parameters=[LockerQueryParamsSerializer],
        responses=LockerMetricsSerializer,
    )
    @action(methods=["get"], detail=False, url_path="metrics")
    def metrics(self, request):
        serializer = LockerQueryParamsSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        academic_year = serializer.validated_data["academic_year"]
        semester = serializer.validated_data["semester"]
        all_lockers = Locker.objects.all()

        active_subscription_locker_ids = LockerSubscription.objects.filter(
            academic_year=academic_year,
            semester=semester,
            status=LockerSubscription.SubscriptionStatus.ACTIVE,
        ).values_list("locker_id", flat=True)

        total_lockers = all_lockers.count()
        occupied = all_lockers.filter(is_active=True, id__in=active_subscription_locker_ids).count()
        vacant = all_lockers.filter(is_active=True).exclude(id__in=active_subscription_locker_ids).count()
        under_maintenance = all_lockers.filter(is_active=False).exclude(id__in=active_subscription_locker_ids).count()

        total_student_renters = LockerSubscription.objects.filter(
            academic_year=academic_year,
            semester=semester,
            status=LockerSubscription.SubscriptionStatus.ACTIVE,
        ).values("student").distinct().count()

        total_students = Student.objects.count()

        active_subscriptions_qs = LockerSubscription.objects.filter(
            academic_year=academic_year,
            semester=semester,
            status=LockerSubscription.SubscriptionStatus.ACTIVE,
        )
        total_paid_renters = active_subscriptions_qs.filter(
            payment_status=LockerSubscription.SubscriptionPaymentStatus.PAID
        ).values("student").distinct().count()

        student_adoption_rate = round((total_student_renters / total_students) * 100, 2) if total_students > 0 else 0
        locker_utilization_rate = round((occupied / total_lockers) * 100, 2) if total_lockers > 0 else 0
        remaining_capacity_locker = round((vacant / total_lockers) * 100, 2) if total_lockers > 0 else 0
        percentage_under_maintenance = round((under_maintenance / total_lockers) * 100, 2) if total_lockers > 0 else 0
        percentage_paid_renters = round((total_paid_renters / total_student_renters) * 100, 2) if total_student_renters > 0 else 0

        return Response({
            "total_lockers": total_lockers,
            "occupied_lockers": occupied,
            "vacant_lockers": vacant,
            "under_maintenance_lockers": under_maintenance,
            "total_student_renters": total_student_renters,
            "total_paid_renters": total_paid_renters,
            "student_adoption_rate": student_adoption_rate,
            "locker_utilization_rate": locker_utilization_rate,
            "remaining_capacity_locker": remaining_capacity_locker,
            "percentage_under_maintenance": percentage_under_maintenance,
            "percentage_paid_renters": percentage_paid_renters,
        })
