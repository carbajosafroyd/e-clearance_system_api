"""
Coupons over HTTP.

Two audiences, and they see different things. Staff issue, list, export and
recall; students claim, preview and apply. `get_queryset` does the row
scoping — a permission says what kind of thing you may do, never whose row
you may do it to.
"""

from __future__ import annotations

import logging

from django.db.models import Q
from django.http import HttpResponse
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
    extend_schema,
)
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from api.rbac import ALLOW_ANY, ActionPermission, crud, gate
from api.reports.renderers import PdfRenderer, XlsxRenderer
from api.reports.services.common import export_filename
from api.reports.views.base import XLSX_CONTENT_TYPE
from api.utils.renderer import CustomResponseRenderer
from api.utils.custom_paginations import CustomPagination
from core import features

from . import exports, services
from .models import Coupon, CouponBatch, CouponRedemption
from .serializers import (
    ApplySerializer,
    ClaimSerializer,
    CouponBatchCreateSerializer,
    CouponBatchReadSerializer,
    CouponDetailSerializer,
    CouponReadSerializer,
    CouponRedemptionReadSerializer,
    PreviewSerializer,
    CouponReasonSerializer,
    CouponReverseSerializer,
)


logger = logging.getLogger(__name__)


def _error(err: services.CouponError, http_status=status.HTTP_400_BAD_REQUEST):
    """Every refusal names the rule it broke, so the UI can act on it."""
    return Response({"detail": err.message, "code": err.code}, status=http_status)


def _student_of(request):
    student = getattr(request.user, "student", None)
    if student is None:
        return None
    return student


def _usable_on(coupon: Coupon, fee, student) -> bool:
    """Would the preview accept this coupon on this fee, right now?"""
    try:
        services.check_usable(coupon, student)
        services.check_fee(coupon, fee, student)
    except services.CouponError:
        return False
    return True


@extend_schema(tags=["Coupons"])
class CouponBatchViewSet(viewsets.ModelViewSet):
    """Issuing runs: create, inspect, recall, export."""

    queryset = (
        CouponBatch.objects.select_related("institute", "issued_by", "target_program")
        .prefetch_related("categories", "coupons")
        .order_by("-created_at")
    )
    permission_classes = [IsAuthenticated, ActionPermission]
    pagination_class = CustomPagination
    required_permissions = gate(
        features.COUPONS,
        crud(
            "coupons",
            "couponbatch",
            create="coupons.issue_couponbatch",
            revoke="coupons.revoke_couponbatch",
            export_excel="coupons.export_couponbatch",
            export_pdf="coupons.export_couponbatch",
        ),
    )
    filterset_fields = ["status", "kind", "institute"]
    search_fields = ["name", "reason"]

    def get_serializer_class(self):
        if self.action == "create":
            return CouponBatchCreateSerializer
        return CouponBatchReadSerializer

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return self.queryset
        # Everyone else sees their own institute's runs and nothing else.
        return self.queryset.filter(institute=user.institute)

    @extend_schema(
        summary="Issue a batch of coupons",
        description=(
            "Creates the batch and mints its coupons in one call. Give "
            "`student_school_ids` for coupons bound to named students — the "
            "safer default, since a bound code is worthless to anyone else — "
            "or `quantity` for bearer coupons."
        ),
        request=CouponBatchCreateSerializer,
        responses={201: CouponBatchReadSerializer},
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = dict(serializer.validated_data)
        students = data.pop("student_school_ids", [])
        quantity = data.pop("quantity", None)
        max_redemptions = data.pop("max_redemptions", 1)
        code_prefix = data.pop("code_prefix", "")
        categories = data.pop("categories", [])

        batch = CouponBatch.objects.create(
            institute=request.user.institute,
            issued_by=request.user,
            updated_by=request.user,
            **data,
        )
        if categories:
            batch.categories.set(categories)

        try:
            services.issue_batch(
                batch=batch,
                students=students or None,
                quantity=quantity or 1,
                max_redemptions=max_redemptions,
                code_prefix=code_prefix,
            )
        except services.CouponError as err:
            return _error(err)

        batch.refresh_from_db()
        return Response(
            CouponBatchReadSerializer(batch).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(
        summary="Recall a batch",
        description=(
            "Voids every unspent coupon in the run. Coupons that were already "
            "redeemed are **left alone** and reported back — reversing them "
            "would re-bill students who did nothing wrong, and each one is a "
            "separate decision."
        ),
        request=CouponReasonSerializer,
        responses={200: OpenApiResponse(description="Counts of what happened")},
    )
    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        batch = self.get_object()
        serializer = CouponReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = services.revoke_batch(
                batch=batch,
                actor=request.user,
                reason=serializer.validated_data["reason"],
            )
        except services.CouponError as err:
            return _error(err)

        detail = f"{result['voided']} coupon(s) voided."
        if result["already_redeemed"]:
            detail += (
                f" {result['already_redeemed']} had already been redeemed and "
                f"were not reversed — review them individually."
            )
        return Response({**result, "detail": detail}, status=status.HTTP_200_OK)

    @extend_schema(
        summary="Export the batch as a spreadsheet",
        description=(
            "One row per coupon, with the run's terms in a header block. "
            "Returned as an attachment, so fetch it through the authenticated "
            "client - a plain browser navigation carries no token."
        ),
        responses={(200, XLSX_CONTENT_TYPE): OpenApiTypes.BINARY},
    )
    @action(
        detail=True,
        methods=["get"],
        url_path="export/excel",
        # JSON first so a validation error still comes back in the project
        # envelope; the binary renderer is here so `Accept:` for the xlsx type
        # is negotiable at all, rather than being refused with a 406 before
        # the handler runs. Same reasoning as api/reports/renderers.py.
        renderer_classes=[CustomResponseRenderer, XlsxRenderer],
    )
    def export_excel(self, request, pk=None):
        batch = self.get_object()
        return self._attachment(
            exports.to_xlsx(batch), batch, "xlsx", XLSX_CONTENT_TYPE
        )

    @extend_schema(
        summary="Export the batch as a printable sheet",
        description=(
            "A grid of tickets to cut up, each with its code and a QR. "
            "Returned as an attachment; fetch it through the authenticated "
            "client."
        ),
        responses={(200, "application/pdf"): OpenApiTypes.BINARY},
    )
    @action(
        detail=True,
        methods=["get"],
        url_path="export/pdf",
        renderer_classes=[CustomResponseRenderer, PdfRenderer],
    )
    def export_pdf(self, request, pk=None):
        batch = self.get_object()
        try:
            content = exports.to_pdf(batch)
        except Exception:
            logger.exception("Could not render the coupon sheet for %s", batch.pk)
            return Response(
                {
                    "detail": "Could not render the coupon sheet.",
                    "code": "render_failed",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return self._attachment(content, batch, "pdf", "application/pdf")

    @staticmethod
    def _attachment(content: bytes, batch, extension: str, content_type: str):
        """
        Timestamped, like every other export in the project - two runs of the
        same batch should not overwrite each other in a downloads folder.
        """
        name = export_filename(
            prefix=f"coupons-{batch.pk}", extension=extension
        )
        response = HttpResponse(content, content_type=content_type)
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        return response

    @extend_schema(
        summary="The batch's coupons, for printing or checking",
        parameters=[
            OpenApiParameter(
                name="q",
                description=(
                    "Narrow to one code, or to the student it was issued to. "
                    "A run is minted in the hundreds; the usual reason for "
                    "opening it is that somebody is asking about one of them. "
                    "Deliberately not `search`: `get_object()` runs this "
                    "viewset's own filter backends, so a `search` here would "
                    "be applied to the *batch* lookup first - matched against "
                    "the run's name and reason - and a code would 404 the "
                    "batch before the action ever ran."
                ),
                required=False,
                type=str,
            )
        ],
        responses={200: CouponReadSerializer(many=True)},
    )
    @action(detail=True, methods=["get"])
    def coupons(self, request, pk=None):
        batch = self.get_object()
        rows = (
            batch.coupons.select_related("student", "batch")
            .prefetch_related("batch__categories", "redemptions")
            .order_by("id")
        )

        term = (request.query_params.get("q") or "").strip()
        if term:
            rows = rows.filter(
                Q(code__icontains=term)
                | Q(student__s_studentID__icontains=term)
                | Q(student__s_fname__icontains=term)
                | Q(student__s_lname__icontains=term)
            )

        page = self.paginate_queryset(rows)
        serializer = CouponReadSerializer(page if page is not None else rows, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)


@extend_schema(tags=["Coupons"])
class CouponViewSet(viewsets.ReadOnlyModelViewSet):
    """Individual codes: staff search and lookup, plus voiding one."""

    queryset = (
        Coupon.objects.select_related("batch", "student")
        # `redemptions` is not decoration: `is_spent` and `status_label` both
        # ask whether one reached a fee, and every serialized coupon carries
        # both.
        .prefetch_related("batch__categories", "redemptions")
        .order_by("-created_at")
    )
    permission_classes = [IsAuthenticated, ActionPermission]
    pagination_class = CustomPagination
    serializer_class = CouponReadSerializer
    required_permissions = gate(
        features.COUPONS,
        {
            "list": "coupons.view_coupon",
            "retrieve": "coupons.view_coupon",
            # The desk lookup is a staff tool, gated on the issuing-side
            # permission that students do not hold. A student never needs to
            # resolve an arbitrary code - they claim it, and the claim
            # endpoint tells them what it is.
            "lookup": "coupons.view_couponbatch",
            "revoke": "coupons.revoke_coupon",
        },
    )
    filterset_fields = ["status", "batch", "student"]
    search_fields = [
        "code",
        "student__s_studentID",
        "student__s_fname",
        "student__s_lname",
    ]

    def get_serializer_class(self):  # type: ignore
        """The list is a table; one code is a case history.

        Pulling the redemptions into the list would be a join per row for
        something no list column shows.
        """
        if self.action in {"retrieve", "lookup", "revoke"}:
            return CouponDetailSerializer
        return CouponReadSerializer

    def get_queryset(self):
        """
        Scoped by *student*, not merely by institute.

        Students hold `coupons.view_coupon` so they can see their own, and an
        institute-only filter would have let any of them list every code in
        the institute - other people's names against other people's coupons,
        and for a bearer coupon the code itself, which is the whole coupon.
        """
        user = self.request.user
        if user.is_superuser:
            return self.queryset

        student = getattr(user, "student", None)
        if student is not None and not user.is_staff:
            # Bound *or* held. A bearer coupon has no `student`, so filtering
            # on that alone left a student unable to open a code they had
            # claimed and were holding in their own list.
            return self.queryset.filter(
                Q(student=student) | Q(redemptions__student=student)
            ).distinct()

        return self.queryset.filter(batch__institute=user.institute)

    def get_object(self):
        """Detail reads carry their history, so fetch it in one go."""
        if self.action in {"retrieve", "revoke"}:
            self.queryset = self.queryset.prefetch_related(
                "redemptions__student__program", "redemptions__fee__category"
            )
        return super().get_object()

    @extend_schema(
        summary="Look a printed code up",
        description=(
            "For when a student is standing at the desk holding one. Comes "
            "back with the coupon's history attached - who claimed it, who "
            "spent it and against what - because that is the question being "
            "asked, and it is not answerable from the code alone."
        ),
        responses={200: CouponDetailSerializer},
    )
    @action(detail=False, methods=["get"])
    def lookup(self, request):
        try:
            coupon = services.find_coupon(request.query_params.get("code", ""))
        except services.CouponError as err:
            return _error(err, status.HTTP_404_NOT_FOUND)

        if not request.user.is_superuser and (
            coupon.batch.institute_id != request.user.institute_id
        ):
            return Response(
                {"detail": "No coupon with that code.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(CouponDetailSerializer(coupon).data)

    @extend_schema(
        summary="Void one unspent coupon",
        request=CouponReasonSerializer,
        responses={200: CouponDetailSerializer},
    )
    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        coupon = self.get_object()
        serializer = CouponReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            voided = services.void_coupon(
                coupon=coupon,
                actor=request.user,
                reason=serializer.validated_data["reason"],
            )
        except services.CouponError as err:
            return _error(err)
        return Response(CouponDetailSerializer(voided).data)


@extend_schema(tags=["Coupons"])
class CouponRedemptionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Redemptions. Students see their own; staff see their institute's.

    The student-facing verbs — claim, preview, apply — live here because
    they are all things that happen *to* a redemption.
    """

    queryset = (
        CouponRedemption.objects.select_related(
            "coupon__batch", "student__program", "fee__category", "updated_by",
            "reversed_by",
        )
        # `applies_to` reads the batch's categories on every row; without this
        # a page of ten costs ten extra queries.
        .prefetch_related("coupon__batch__categories")
        .order_by("-created_at")
    )
    permission_classes = [IsAuthenticated, ActionPermission]
    pagination_class = CustomPagination
    serializer_class = CouponRedemptionReadSerializer
    required_permissions = gate(
        features.COUPONS,
        {
            "list": "coupons.view_couponredemption",
            "retrieve": "coupons.view_couponredemption",
            # A student redeeming their own coupon is not an administrative
            # act, and gating it on a staff permission would lock every
            # student out of the feature. `get_queryset` still scopes the
            # rows, and the services re-check ownership on every call.
            "mine": ALLOW_ANY,
            "claim": ALLOW_ANY,
            "preview": ALLOW_ANY,
            "apply": ALLOW_ANY,
            "reverse": "coupons.reverse_couponredemption",
        },
    )
    filterset_fields = ["status", "student", "fee"]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return self.queryset
        student = _student_of(self.request)
        if student is not None and not user.is_staff:
            return self.queryset.filter(student=student)
        return self.queryset.filter(coupon__batch__institute=user.institute)

    # -- the student's own -------------------------------------------------

    @extend_schema(
        summary="Coupons you hold but have not spent",
        parameters=[
            OpenApiParameter(
                name="search",
                description=(
                    "Narrow to a code, the run it came from, or what it was "
                    "given for. Safe to call `search` here, unlike the "
                    "batch's nested action: this is a list route, so there is "
                    "no `get_object()` for a filter backend to interfere with."
                ),
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="fee",
                description=(
                    "A fee id. Keeps only the coupons that could be spent on "
                    "that fee right now - the same checks the preview runs, "
                    "so an expired, voided, wrong-category or wrong-term "
                    "coupon is left out rather than offered and then refused."
                ),
                required=False,
                type=int,
            ),
        ],
        responses={200: CouponRedemptionReadSerializer(many=True)},
        description=(
            "Paged like every other list. A student who collects a code at "
            "every event of the term can hold more of these than one screen "
            "should ask for at once."
        ),
    )
    @action(detail=False, methods=["get"])
    def mine(self, request):
        student = _student_of(request)
        rows = (
            self.queryset.filter(
                student=student, status=CouponRedemption.Status.CLAIMED
            )
            if student is not None
            # Not a student, so nothing is held - but the shape has to be the
            # paged envelope either way, or the caller has to branch on it.
            else self.queryset.none()
        )

        term = (request.query_params.get("search") or "").strip()
        if term:
            # The three things a student would type: the code off the paper,
            # the event it came from, or why they were given it.
            rows = rows.filter(
                Q(coupon__code__icontains=term)
                | Q(coupon__batch__name__icontains=term)
                | Q(coupon__batch__reason__icontains=term)
            )

        fee_id = (request.query_params.get("fee") or "").strip()
        if fee_id:
            from api.fees.models import Fee

            fee = Fee.objects.filter(pk=fee_id).first() if fee_id.isdigit() else None
            if fee is None:
                return Response(
                    {"detail": "That fee no longer exists.", "code": "fee_not_found"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # A held coupon is a promise, not a guarantee: weeks pass between
            # claiming and spending, and the run may have expired or been
            # recalled in the gap. Offering one that will only be refused is
            # worse than leaving it out, so each goes through the preview's
            # own checks. A student holds tens of these at most, so doing it
            # row by row is cheap.
            rows = [
                row
                for row in rows.select_related("coupon__batch")
                if _usable_on(row.coupon, fee, student)
            ]

        page = self.paginate_queryset(rows)
        if page is None:
            return Response(CouponRedemptionReadSerializer(rows, many=True).data)
        return self.get_paginated_response(
            CouponRedemptionReadSerializer(page, many=True).data
        )

    @extend_schema(
        summary="Claim a coupon (the QR landing, or a typed code)",
        request=ClaimSerializer,
        responses={201: CouponRedemptionReadSerializer},
    )
    @action(detail=False, methods=["post"])
    def claim(self, request):
        student = _student_of(request)
        if student is None:
            return Response(
                {
                    "detail": "Only students can claim coupons.",
                    "code": "not_a_student",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = ClaimSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            redemption = services.claim(
                raw_code=serializer.validated_data["code"],
                student=student,
                actor=request.user,
            )
        except services.CouponError as err:
            return _error(err)

        return Response(
            CouponRedemptionReadSerializer(redemption).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        summary="What would come off, without committing",
        request=PreviewSerializer,
        responses={200: OpenApiResponse(description="amount, and the resulting balance")},
    )
    @action(detail=False, methods=["post"])
    def preview(self, request):
        student = _student_of(request)
        if student is None:
            return Response(
                {"detail": "Only students can use coupons.", "code": "not_a_student"},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = PreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from api.fees.models import Fee

        fee = Fee.objects.filter(pk=serializer.validated_data["fee"]).first()
        if fee is None:
            return Response(
                {"detail": "That fee no longer exists.", "code": "fee_not_found"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            coupon = services.find_coupon(serializer.validated_data["code"])
            services.check_usable(coupon, student)
            services.check_fee(coupon, fee, student)
            amount = services.quote(coupon, fee)
        except services.CouponError as err:
            return _error(err)

        return Response(
            {
                "amount": amount,
                "balance_before": fee.balance,
                "balance_after": fee.balance - amount,
                "value_label": coupon.batch.describe_value(),
            }
        )

    @extend_schema(
        summary="Spend a coupon against a fee",
        request=ApplySerializer,
        responses={200: CouponRedemptionReadSerializer},
    )
    @action(detail=False, methods=["post"])
    def apply(self, request):
        student = _student_of(request)
        if student is None:
            return Response(
                {"detail": "Only students can use coupons.", "code": "not_a_student"},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = ApplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            redemption = services.apply(
                raw_code=serializer.validated_data["code"],
                fee_id=serializer.validated_data["fee"],
                student=student,
                actor=request.user,
            )
        except services.CouponError as err:
            return _error(err)

        return Response(CouponRedemptionReadSerializer(redemption).data)

    # -- taking it back ----------------------------------------------------

    @extend_schema(
        summary="Reverse a spent coupon",
        description=(
            "Puts the relief back onto the fee. Safe while the fee is unpaid; "
            "on a settled fee it creates a debt the student was told they did "
            "not owe, so that case needs `coupons.reverse_paid_couponredemption` "
            "**and** `confirm_paid_fee`. The student is notified either way."
        ),
        request=CouponReverseSerializer,
        responses={200: CouponRedemptionReadSerializer},
    )
    @action(detail=True, methods=["post"])
    def reverse(self, request, pk=None):
        redemption = self.get_object()
        serializer = CouponReverseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        wants_paid = serializer.validated_data.get("confirm_paid_fee", False)
        may_reverse_paid = request.user.has_perm(
            "coupons.reverse_paid_couponredemption"
        ) or request.user.is_superuser

        try:
            reversed_row = services.reverse_redemption(
                redemption=redemption,
                actor=request.user,
                reason=serializer.validated_data["reason"],
                allow_paid=wants_paid and may_reverse_paid,
            )
        except services.CouponError as err:
            code = (
                status.HTTP_403_FORBIDDEN
                if err.code == "fee_already_paid" and not may_reverse_paid
                else status.HTTP_400_BAD_REQUEST
            )
            return _error(err, code)

        return Response(CouponRedemptionReadSerializer(reversed_row).data)
