"""
The contributions API.

Four endpoints, one per noun:

    /contributions/        file a report, review one
    /contribution-points/  wallets, ledger, ranking
    /point-policies/       what a report is worth, per institute and system
    /fee-discounts/        spend points against a fee

Every action is named in `required_permissions`; anything not listed is
refused. Row-level scoping is `get_queryset()` plus the object permissions in
`permissions.py` - a permission says what kind of thing you may do, never
whose row you may do it to.

The whole module is optional. `api/urls.py` registers these routes only where
the deployment runs the feature, and every table below is `gate`d as well, so
a viewset that somehow stays routed still refuses rather than quietly
serving a module this institute does not have.
"""

from __future__ import annotations

import logging

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from api.audits.models import AuditLog
from api.audits.services import AuditLogService
from api.audits.utils import resolve_actor, snapshot_instance
from api.fees.models import Fee
from api.rbac import DEFAULT, DENY, ActionPermission, crud, gate
from core import features
from api.utils.custom_paginations import CustomPagination

from . import selectors, services
from .filters import ContributionFilterSet, FeeDiscountFilterSet
from .models import Contribution, FeeDiscount, PointAccount, PointPolicy, PointTransaction
from .permissions import IsOwnStudentRecordOrInstituteStaff, IsWithinInstituteScope
from .serializers import (
    AdjustPointsSerializer,
    ContributionRejectSerializer,
    ContributionReadSerializer,
    ContributionReviewSerializer,
    ContributionRevokeSerializer,
    ContributionSlimSerializer,
    ContributionSummarySerializer,
    ContributionWriteSerializer,
    DiscountQuoteRequestSerializer,
    DiscountQuoteSerializer,
    FeeDiscountReadSerializer,
    FeeDiscountRequestSerializer,
    LeaderboardEntrySerializer,
    OptionalRemarksSerializer,
    PointAccountSerializer,
    PointPolicySerializer,
    PointTransactionSerializer,
    ReasonSerializer,
    RemarksSerializer,
    WalletSerializer,
)

logger = logging.getLogger(__name__)

SYSTEM_PARAM = OpenApiParameter(
    name="system",
    description="Restrict to one system (collection, locker, attendance, …).",
    required=False,
    type=str,
)


def _student_of(user):
    """The Student behind a user account, or None for staff.

    Reverse OneToOne: Django raises a RelatedObjectDoesNotExist that
    subclasses AttributeError, so getattr's default covers staff accounts.
    """
    return getattr(user, "student", None)


def _institute_scope(request):
    """The institute a caller's figures should cover, or None for everything."""
    if request.user.is_superuser:
        return None
    return getattr(request.user, "institute_id", None)


@extend_schema(tags=["Contributions"])
class ContributionViewSet(viewsets.ModelViewSet):
    """Student reports and recommendations, one per system."""

    queryset = Contribution.objects.select_related(
        "student__program", "reviewed_by"
    ).order_by("-created_at")

    pagination_class = CustomPagination
    permission_classes = [
        IsAuthenticated,
        ActionPermission,
        IsOwnStudentRecordOrInstituteStaff,
    ]
    required_permissions = gate(
        features.CONTRIBUTIONS,
        crud(
            "contributions",
            "contribution",
            approve="contributions.review_contribution",
            reject="contributions.review_contribution",
            revoke="contributions.review_contribution",
            start_review="contributions.review_contribution",
            mine="contributions.view_contribution",
            summary="rbac.view_contribution_report",
        ),
    )

    filterset_class = ContributionFilterSet
    search_fields = [
        "title",
        "description",
        "student__s_fname",
        "student__s_lname",
        "student__s_studentID",
    ]
    ordering_fields = ["created_at", "updated_at", "points_awarded", "status"]

    def get_serializer_class(self):  # type: ignore
        if self.action in ("create", "update", "partial_update"):
            return ContributionWriteSerializer
        if self.action == "retrieve":
            return ContributionReadSerializer
        return ContributionSlimSerializer

    def get_queryset(self):  # type: ignore
        # Schema generation runs this with an AnonymousUser.
        if getattr(self, "swagger_fake_view", False):
            return Contribution.objects.none()

        user = self.request.user
        queryset = super().get_queryset()

        if user.is_superuser:
            return queryset

        student = _student_of(user)
        if student is not None:
            return queryset.filter(student=student)

        institute_id = getattr(user, "institute_id", None)
        if not institute_id:
            return queryset.none()
        return queryset.filter(student__program__institute_id=institute_id)

    def perform_create(self, serializer):
        student = _student_of(self.request.user)
        if student is None:
            from rest_framework.exceptions import ValidationError

            raise ValidationError(
                {
                    "detail": (
                        "Only students file contributions. Staff review them; "
                        "they do not submit them on a student's behalf."
                    )
                }
            )
        serializer.save(student=student, updated_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    def _audit(self, contribution, old_snapshot, request):
        AuditLogService.log_model_update(
            instance=contribution,
            old_snapshot=old_snapshot,
            user=request.user,
            system=AuditLog.System.PLATFORM,
        )

    @extend_schema(
        request=ContributionReviewSerializer,
        responses=ContributionReadSerializer,
        description=(
            "Accept the report and pay for it. Points default to the policy's "
            "suggestion for the kind and impact; send `points` to override, "
            "and `impact` to correct the student's rating before scoring."
        ),
    )
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        contribution = self.get_object()
        serializer = ContributionReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        old_snapshot = snapshot_instance(contribution)
        contribution = services.approve_contribution(
            contribution=contribution,
            actor=request.user,
            points=serializer.validated_data.get("points"),
            impact=serializer.validated_data.get("impact"),
            remarks=serializer.validated_data.get("remarks", ""),
        )
        self._audit(contribution, old_snapshot, request)

        return Response(
            ContributionReadSerializer(contribution, context={"request": request}).data
        )

    @extend_schema(
        request=ContributionRejectSerializer,
        responses=ContributionReadSerializer,
        description=(
            "Turn the report down. Remarks are required - the student needs "
            "to know why. Set `duplicate` when it is real but already "
            "reported: that is recorded separately from a rejection."
        ),
    )
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        contribution = self.get_object()
        serializer = ContributionRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        old_snapshot = snapshot_instance(contribution)
        contribution = services.reject_contribution(
            contribution=contribution,
            actor=request.user,
            remarks=serializer.validated_data["remarks"],
            duplicate=serializer.validated_data["duplicate"],
        )
        self._audit(contribution, old_snapshot, request)

        return Response(
            ContributionReadSerializer(contribution, context={"request": request}).data
        )

    @extend_schema(
        request=ContributionRevokeSerializer,
        responses=ContributionReadSerializer,
        description=(
            "Undo an approval and take the points back. Refused if the "
            "student has already spent them - cancel the discount first."
        ),
    )
    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        contribution = self.get_object()
        serializer = ContributionRevokeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        old_snapshot = snapshot_instance(contribution)
        contribution = services.revoke_contribution(
            contribution=contribution,
            actor=request.user,
            remarks=serializer.validated_data["remarks"],
        )
        self._audit(contribution, old_snapshot, request)

        return Response(
            ContributionReadSerializer(contribution, context={"request": request}).data
        )

    @extend_schema(
        request=OptionalRemarksSerializer,
        responses=ContributionReadSerializer,
        description="Claim the report for review, so two reviewers do not both work it.",
    )
    @action(detail=True, methods=["post"], url_path="start-review")
    def start_review(self, request, pk=None):
        contribution = self.get_object()

        if contribution.status != Contribution.Status.PENDING:
            return Response(
                {"detail": f"This contribution is already {contribution.status}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        contribution.status = Contribution.Status.UNDER_REVIEW
        contribution.reviewed_by = request.user
        contribution.updated_by = request.user
        contribution.save(
            update_fields=["status", "reviewed_by", "updated_by", "updated_at"]
        )
        return Response(
            ContributionReadSerializer(contribution, context={"request": request}).data
        )

    @extend_schema(responses=ContributionSlimSerializer(many=True))
    @action(detail=False, methods=["get"])
    def mine(self, request):
        """Everything the signed-in student has filed."""
        student = _student_of(request.user)
        if student is None:
            return Response([])

        queryset = self.filter_queryset(self.get_queryset().filter(student=student))
        page = self.paginate_queryset(queryset)
        if page is not None:
            return self.get_paginated_response(
                ContributionSlimSerializer(page, many=True).data
            )
        return Response(ContributionSlimSerializer(queryset, many=True).data)

    @extend_schema(
        parameters=[SYSTEM_PARAM],
        responses=ContributionSummarySerializer,
        description=(
            "Dashboard figures: contribution counts by status, points earned "
            "and spent, money given away as discounts, and a per-system "
            "breakdown that is zero-filled so a quiet system still appears."
        ),
    )
    @action(detail=False, methods=["get"])
    def summary(self, request):
        return Response(
            ContributionSummarySerializer(
                selectors.contribution_summary(
                    institute_id=_institute_scope(request),
                    system=request.query_params.get("system") or None,
                )
            ).data
        )


@extend_schema(tags=["Contribution Points"])
class PointAccountViewSet(viewsets.ReadOnlyModelViewSet):
    """Wallets, the ledger behind them, and the ranking."""

    queryset = PointAccount.objects.select_related("student__program").order_by(
        "-balance"
    )
    pagination_class = CustomPagination
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = gate(
        features.CONTRIBUTION_POINTS,
        {
            "list": "contributions.view_pointaccount",
            "retrieve": "contributions.view_pointaccount",
            "me": "contributions.view_pointaccount",
            "transactions": "contributions.view_pointtransaction",
            "leaderboard": "contributions.view_contribution_leaderboard",
            "adjust": "contributions.add_pointtransaction",
            DEFAULT: DENY,
        },
    )

    filterset_fields = ["system", "student"]
    ordering_fields = ["balance", "total_earned", "total_spent"]

    def get_serializer_class(self):  # type: ignore
        return PointAccountSerializer

    def get_queryset(self):  # type: ignore
        if getattr(self, "swagger_fake_view", False):
            return PointAccount.objects.none()

        user = self.request.user
        queryset = super().get_queryset()

        if user.is_superuser:
            return queryset

        student = _student_of(user)
        if student is not None:
            return queryset.filter(student=student)

        institute_id = getattr(user, "institute_id", None)
        if not institute_id:
            return queryset.none()
        return queryset.filter(student__program__institute_id=institute_id)

    @extend_schema(
        responses=WalletSerializer,
        description=(
            "The signed-in student's wallets - one per system, zero-filled - "
            "plus their totals and where they sit on the ranking."
        ),
    )
    @action(detail=False, methods=["get"])
    def me(self, request):
        student = _student_of(request.user)
        if student is None:
            return Response(
                {"detail": "Only students hold point wallets."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payload = selectors.student_wallet(student=student)
        payload["rank"] = selectors.student_rank(
            student=student,
            institute_id=student.program.institute_id,
        )
        return Response(WalletSerializer(payload).data)

    @extend_schema(
        parameters=[SYSTEM_PARAM],
        responses=PointTransactionSerializer(many=True),
        description="The ledger behind a balance, newest first.",
    )
    @action(detail=False, methods=["get"])
    def transactions(self, request):
        queryset = PointTransaction.objects.select_related(
            "account__student", "contribution"
        ).order_by("-created_at", "-id")

        user = request.user
        if not user.is_superuser:
            student = _student_of(user)
            if student is not None:
                queryset = queryset.filter(account__student=student)
            else:
                institute_id = getattr(user, "institute_id", None)
                queryset = (
                    queryset.filter(
                        account__student__program__institute_id=institute_id
                    )
                    if institute_id
                    else queryset.none()
                )

        system = request.query_params.get("system")
        if system:
            queryset = queryset.filter(account__system=system)

        student_id = request.query_params.get("student")
        if student_id:
            queryset = queryset.filter(account__student_id=student_id)

        page = self.paginate_queryset(queryset)
        if page is not None:
            return self.get_paginated_response(
                PointTransactionSerializer(page, many=True).data
            )
        return Response(PointTransactionSerializer(queryset, many=True).data)

    @extend_schema(
        parameters=[
            SYSTEM_PARAM,
            OpenApiParameter(
                name="search",
                description=(
                    "Filter the board by name, school id or program. Places "
                    "stay absolute - a match still shows the rank it holds on "
                    "the whole board."
                ),
                required=False,
                type=str,
            ),
        ],
        responses=LeaderboardEntrySerializer(many=True),
        description=(
            "Top contributors, ranked on points *earned* - spending them on a "
            "discount is what they are for, so the board must not punish it. "
            "Omit `system` for the combined board. Paged like every other "
            "list: `current_page` and `per_page`."
        ),
    )
    @action(detail=False, methods=["get"])
    def leaderboard(self, request):
        system = request.query_params.get("system") or None
        search = (request.query_params.get("search") or "").strip()
        institute_id = _institute_scope(request)

        queryset = selectors.leaderboard_queryset(
            institute_id=institute_id, system=system, search=search
        )
        # Only when filtering. Unfiltered, the page's own offset already is
        # the place, and this would be a second pass over the whole board for
        # nothing.
        ranks = (
            selectors.leaderboard_ranks(institute_id=institute_id, system=system)
            if search
            else None
        )

        page = self.paginate_queryset(queryset)
        if page is None:  # pagination disabled - the whole board
            rows = selectors.leaderboard_rows(queryset, system=system, ranks=ranks)
            return Response(LeaderboardEntrySerializer(rows, many=True).data)

        # `start_index` is the board position of this page's first row. Rank
        # is absolute or it is a lie: page two starting again at 1 would tell
        # a second student they came first.
        rows = selectors.leaderboard_rows(
            page,
            system=system,
            start_rank=self.paginator.page.start_index(),
            ranks=ranks,
        )
        return self.get_paginated_response(
            LeaderboardEntrySerializer(rows, many=True).data
        )

    @extend_schema(
        request=AdjustPointsSerializer,
        responses=PointTransactionSerializer,
        description=(
            "Manual correction, for what the workflow does not cover. Remarks "
            "are required and the entry lands in the ledger like any other."
        ),
    )
    @action(detail=False, methods=["post"])
    def adjust(self, request):
        serializer = AdjustPointsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        student = serializer.validated_data["student"]
        if not request.user.is_superuser:
            institute_id = getattr(request.user, "institute_id", None)
            if student.program.institute_id != institute_id:
                return Response(
                    {"detail": "That student is outside your institute."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        entry = services.adjust_points(
            student=student,
            system=serializer.validated_data["system"],
            delta=serializer.validated_data["delta"],
            actor=request.user,
            remarks=serializer.validated_data["remarks"],
        )

        actor_type, actor_id = resolve_actor(request.user)
        AuditLogService.log_action(
            system=AuditLog.System.PLATFORM,
            entity_type="PointTransaction",
            entity_id=entry.pk,
            action=AuditLog.Action.CREATE,
            actor_type=actor_type,
            actor_id=actor_id,
            new_values={
                "student_id": student.pk,
                "system": entry.account.system,
                "delta": entry.delta,
                "balance_after": entry.balance_after,
                "remarks": entry.remarks,
            },
        )
        return Response(
            PointTransactionSerializer(entry).data, status=status.HTTP_201_CREATED
        )


@extend_schema(tags=["Contribution Points"])
class PointPolicyViewSet(viewsets.ModelViewSet):
    """What a contribution is worth, per institute and per system."""

    queryset = PointPolicy.objects.select_related("institute").order_by(
        "institute_id", "system"
    )
    pagination_class = CustomPagination
    permission_classes = [IsAuthenticated, ActionPermission, IsWithinInstituteScope]
    required_permissions = gate(
        features.CONTRIBUTION_POINTS, crud("contributions", "pointpolicy")
    )
    serializer_class = PointPolicySerializer
    filterset_fields = ["institute", "system", "is_active"]

    def get_queryset(self):  # type: ignore
        if getattr(self, "swagger_fake_view", False):
            return PointPolicy.objects.none()

        user = self.request.user
        queryset = super().get_queryset()
        if user.is_superuser:
            return queryset

        institute_id = getattr(user, "institute_id", None)
        if not institute_id:
            return queryset.none()
        return queryset.filter(institute_id=institute_id)

    def perform_create(self, serializer):
        # Staff may only write policy for their own institute; the object
        # permission covers edits, this covers creation.
        user = self.request.user
        institute = serializer.validated_data.get("institute")
        if not user.is_superuser and institute.pk != getattr(user, "institute_id", None):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You can only configure your own institute.")
        serializer.save()


@extend_schema(tags=["Fee Discounts"])
class FeeDiscountViewSet(viewsets.ModelViewSet):
    """Points spent against a fee."""

    queryset = FeeDiscount.objects.select_related(
        "student__program", "fee__category", "requested_by", "reviewed_by", "cancelled_by"
    ).order_by("-created_at")

    pagination_class = CustomPagination
    permission_classes = [
        IsAuthenticated,
        ActionPermission,
        IsOwnStudentRecordOrInstituteStaff,
    ]
    required_permissions = gate(
        features.CONTRIBUTION_POINTS,
        crud(
            "contributions",
            "feediscount",
            # Creating one is `request`; the plain create route is off so
            # there is exactly one way in, and it is the one that reserves
            # the points.
            create=DENY,
            request_discount="contributions.add_feediscount",
            quote="contributions.view_feediscount",
            approve="contributions.review_feediscount",
            reject="contributions.review_feediscount",
            cancel="contributions.cancel_feediscount",
            # Reading a fee's own relief is reading the fee: the same grant the
            # fee detail panel is shown under, held by students and desk staff.
            for_fee="fees.view_fee_discount_history",
        ),
    )

    filterset_class = FeeDiscountFilterSet
    search_fields = [
        "student__s_fname",
        "student__s_lname",
        "student__s_studentID",
        "fee__category__category_name",
    ]
    ordering_fields = ["created_at", "amount", "points_spent", "status"]
    serializer_class = FeeDiscountReadSerializer

    def get_queryset(self):  # type: ignore
        if getattr(self, "swagger_fake_view", False):
            return FeeDiscount.objects.none()

        user = self.request.user
        queryset = super().get_queryset()

        if user.is_superuser:
            return queryset

        student = _student_of(user)
        if student is not None:
            return queryset.filter(student=student)

        institute_id = getattr(user, "institute_id", None)
        if not institute_id:
            return queryset.none()
        return queryset.filter(student__program__institute_id=institute_id)

    def _fee_in_scope(self, request, fee_id: int) -> Fee:
        """Fetch a fee the caller is allowed to touch, 404 otherwise."""
        queryset = Fee.objects.select_related("student__program", "category")
        user = request.user

        if not user.is_superuser:
            student = _student_of(user)
            if student is not None:
                queryset = queryset.filter(student=student)
            else:
                queryset = queryset.filter(
                    student__program__institute_id=getattr(user, "institute_id", None)
                )
        return get_object_or_404(queryset, pk=fee_id)

    @extend_schema(
        request=DiscountQuoteRequestSerializer,
        responses=DiscountQuoteSerializer,
        description=(
            "What N points would be worth against a fee, and the bounds the "
            "policy puts on it. Read-only - nothing is reserved. This is what "
            "the UI polls while the student picks how many points to spend."
        ),
    )
    @action(detail=False, methods=["post"])
    def quote(self, request):
        serializer = DiscountQuoteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        fee = self._fee_in_scope(request, serializer.validated_data["fee"])
        quote = services.discount_quote(
            fee=fee,
            system=serializer.validated_data["system"],
            points=serializer.validated_data["points"],
        )
        return Response(DiscountQuoteSerializer(quote).data)

    @extend_schema(
        request=FeeDiscountRequestSerializer,
        responses=FeeDiscountReadSerializer,
        description=(
            "Spend points against one of your own fees. The points leave your "
            "wallet immediately - otherwise the same balance could back five "
            "requests at once - and come straight back if the request is "
            "rejected or cancelled. An admin still has to approve it before "
            "the fee moves."
        ),
    )
    @action(detail=False, methods=["post"], url_path="request")
    def request_discount(self, request):
        serializer = FeeDiscountRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        student = _student_of(request.user)
        if student is None:
            return Response(
                {"detail": "Only students can spend contribution points."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        fee = self._fee_in_scope(request, serializer.validated_data["fee"])
        discount = services.request_discount(
            fee=fee,
            student=student,
            system=serializer.validated_data["system"],
            points=serializer.validated_data["points"],
            actor=request.user,
            remarks=serializer.validated_data.get("remarks", ""),
        )
        return Response(
            FeeDiscountReadSerializer(discount, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=OptionalRemarksSerializer,
        responses=FeeDiscountReadSerializer,
        description=(
            "Apply the discount. The fee's total and balance both drop by the "
            "amount, so the reports' `collected = total - balance` identity "
            "is untouched and only what was expected goes down. A fee that "
            "reaches zero becomes PAID."
        ),
    )
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        discount = self.get_object()
        serializer = OptionalRemarksSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        old_snapshot = snapshot_instance(discount)
        discount = services.approve_discount(
            discount=discount,
            actor=request.user,
            remarks=serializer.validated_data.get("remarks", ""),
        )
        AuditLogService.log_model_update(
            instance=discount,
            old_snapshot=old_snapshot,
            user=request.user,
            system=AuditLog.System.COLLECTION,
        )
        return Response(
            FeeDiscountReadSerializer(discount, context={"request": request}).data
        )

    @extend_schema(
        request=RemarksSerializer,
        responses=FeeDiscountReadSerializer,
        description="Refuse the request and put the points back. Remarks required.",
    )
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        discount = self.get_object()
        serializer = RemarksSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        old_snapshot = snapshot_instance(discount)
        discount = services.reject_discount(
            discount=discount,
            actor=request.user,
            remarks=serializer.validated_data["remarks"],
        )
        AuditLogService.log_model_update(
            instance=discount,
            old_snapshot=old_snapshot,
            user=request.user,
            system=AuditLog.System.COLLECTION,
        )
        return Response(
            FeeDiscountReadSerializer(discount, context={"request": request}).data
        )

    @extend_schema(
        request=ReasonSerializer,
        responses=FeeDiscountReadSerializer,
        description=(
            "Undo a discount: the fee is restored to exactly what it was and "
            "the points go back to the student. A reason is required."
        ),
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        discount = self.get_object()
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        old_snapshot = snapshot_instance(discount)
        discount = services.cancel_discount(
            discount=discount,
            actor=request.user,
            reason=serializer.validated_data["reason"],
        )
        AuditLogService.log_model_update(
            instance=discount,
            old_snapshot=old_snapshot,
            user=request.user,
            system=AuditLog.System.COLLECTION,
            action=AuditLog.Action.CANCEL,
        )
        return Response(
            FeeDiscountReadSerializer(discount, context={"request": request}).data
        )

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="fee", description="Fee id.", required=True, type=int
            )
        ],
        responses=FeeDiscountReadSerializer(many=True),
        description=(
            "Every discount ever requested against one fee, newest first - "
            "rejected and cancelled ones included, because 'we asked and were "
            "refused' is part of the history. Paged: a fee that has been "
            "argued over a dozen times must not push the fee itself off the "
            "screen."
        ),
    )
    @action(detail=False, methods=["get"], url_path="for-fee")
    def for_fee(self, request):
        fee_id = request.query_params.get("fee")
        if not fee_id:
            return Response(
                {"detail": "Pass ?fee=<id>."}, status=status.HTTP_400_BAD_REQUEST
            )

        fee = self._fee_in_scope(request, fee_id)
        history = selectors.fee_discount_history(fee=fee)

        page = self.paginate_queryset(history)
        if page is None:
            return Response(
                FeeDiscountReadSerializer(
                    history, many=True, context={"request": request}
                ).data
            )
        return self.get_paginated_response(
            FeeDiscountReadSerializer(
                page, many=True, context={"request": request}
            ).data
        )
