from django.utils import timezone

from rest_framework.viewsets import ViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied

from api.rbac import DEFAULT, DENY, ActionPermission

from api.payments.serializers import PaymentReadMiniSerializer
from ..serializers.summaries_serializers import SummaryFilterSerializer
from api.students.models import Student
from rest_framework.response import Response
from api.fees.models import Fee, GeneratedFeeBatch
from django.db.models import (
    Q,
    Case,
    Count,
    DecimalField,
    IntegerField,
    Sum,
    F,
    When,
    Avg,
    Count,
    Max,
    Min,
)
from decimal import Decimal
from django.db.models.functions import Coalesce
from api.payment_submissions.models import PaymentSubmission, PaymentSubmissionItems
from api.payments.models import Payment
from drf_spectacular.utils import extend_schema
from api.collection_categories.models import CollectionCategory


@extend_schema(tags=["Summaries"])
class SummaryViewSets(ViewSet):
    """Dashboard and summary widgets.

    Two audiences share this viewset: the back-office widgets, which read
    institute-wide totals, and the four `student-*` actions, which read only
    the caller's own figures. The table below is what keeps them apart -
    without it a student's token would reach the institute-wide numbers.
    """

    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = {
        "admin_dashboard": "rbac.view_admin_dashboard",
        "students": "rbac.view_collection_summary",
        "fees": "rbac.view_collection_summary",
        "generated_batch": "rbac.view_collection_summary",
        "payments": "rbac.view_collection_summary",
        "payment_submissions": "rbac.view_collection_summary",
        "collection_categories": "rbac.view_collection_summary",
        # Each of these narrows to request.user.student before it reads
        # anything, so the permission only has to say "is a student here".
        "student_dashboard": "rbac.view_student_dashboard",
        "student_fee": "rbac.view_student_dashboard",
        "student_paments": "rbac.view_student_dashboard",
        "student_payment_submissions": "rbac.view_student_dashboard",
        DEFAULT: DENY,
    }
    serializer_class = SummaryFilterSerializer

    @extend_schema(
        parameters=[SummaryFilterSerializer],
    )
    @action(
        methods=["get"],
        detail=False,
        url_path="admin-dashboard",
    )
    def admin_dashboard(self, request):
        serializer = SummaryFilterSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        academic_year = serializer.validated_data["academic_year"]
        semester = serializer.validated_data["semester"]
        user_institute = request.user.institute
        today = timezone.now().date()

        print(user_institute)

        # Base Queryset for Fees (Excluding Waived)
        # We include everything that isn't waived
        base_fee_qs = Fee.objects.filter(
            academic_year=academic_year,
            semester=semester,
            student__program__institute=user_institute,
        ).exclude(status=Fee.FeeStatus.WAIVED)

        # General Totals & Overdue Count
        # A fee is "Overdue" if balance > 0 AND due_date < today
        stats = base_fee_qs.aggregate(
            total_amount=Sum("total_amount"),
            total_discount=Sum("discount_total"),
            total_balance=Sum("balance"),
            overdue_count=Count("id", filter=Q(balance__gt=0, due_date__lt=today)),
        )

        total_amt = stats["total_amount"] or 0
        total_disc = stats["total_discount"] or 0
        total_bal = stats["total_balance"] or 0
        overdue_count = stats["overdue_count"]
        # Net of relief: the rate answers "did we collect what we asked for",
        # and discounts lower what was asked rather than what came in.
        total_expected = total_amt - total_disc

        # Overall Collection Rate
        collection_rate = (
            round(((total_expected - total_bal) / total_expected * 100), 2)
            if total_expected > 0
            else 100.0
        )

        # Collection Rate & Overdue per Category (Optimized)
        # We group by category and calculate everything in one go
        category_data = base_fee_qs.values("category__category_name").annotate(
            cat_total=Sum("total_amount"),
            cat_discount=Sum("discount_total"),
            cat_balance=Sum("balance"),
            cat_overdue_count=Count("id", filter=Q(balance__gt=0, due_date__lt=today)),
        )

        collection_rate_per_categories = []
        for item in category_data:
            c_amt = item["cat_total"] or 0
            c_disc = item["cat_discount"] or 0
            c_bal = item["cat_balance"] or 0
            c_expected = c_amt - c_disc

            rate = (
                round(((c_expected - c_bal) / c_expected * 100), 2)
                if c_expected > 0
                else 100.0
            )

            collection_rate_per_categories.append(
                {
                    "category_name": item["category__category_name"],
                    "collection_rate": rate,
                    "overdue_count": item["cat_overdue_count"],
                }
            )

        # Students & Pending Submissions
        students_count = Student.objects.filter(
            program__institute=user_institute
        ).count()

        recent_transactions = (
            Payment.objects.filter(
                fee__academic_year=academic_year,
                fee__semester=semester,
                fee__student__program__institute=user_institute,
                status=Payment.PaymentStatus.ACTIVE,
            )
            .select_related("fee", "fee__student")
            .order_by("-updated_at")[:7]
        )

        pending_submissions_count = (
            PaymentSubmission.objects.filter(
                status=PaymentSubmission.SubmissionStatus.PENDING,
                student__program__institute=user_institute,
                payment_submission_list__fee__academic_year=academic_year,
                payment_submission_list__fee__semester=semester,
            )
            .distinct()
            .count()
        )

        return Response(
            {
                "students_count": students_count,
                "overdue_count": overdue_count,
                "total_amount": total_amt,
                "total_discount": total_disc,
                "total_expected": total_expected,
                "total_balance": total_bal,
                "collection_rate": collection_rate,
                "recent_transactions": PaymentReadMiniSerializer(
                    recent_transactions, many=True
                ).data,
                "pending_submissions_count": pending_submissions_count,
                "collection_rate_per_categories": collection_rate_per_categories,
            }
        )

    @action(detail=False, methods=["get"])
    def students(self, request):
        students = Student.objects.filter(program__institute=request.user.institute)

        return Response(
            {
                "enrolled_students": students.filter(
                    s_status=Student.StudentStatus.ENROLLED
                ).count(),
                "graduated_students": students.filter(
                    s_status=Student.StudentStatus.GRADUATED
                ).count(),
                "dropped_students": students.filter(
                    s_status=Student.StudentStatus.DROPPED
                ).count(),
                "to_be_updated_students": students.filter(
                    s_status=Student.StudentStatus.TO_BE_UPDATED,
                ).count(),
            }
        )

    @extend_schema(
        parameters=[SummaryFilterSerializer],
    )
    @action(detail=False, methods=["get"])
    def fees(self, request):
        serializer = SummaryFilterSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        academic_year = serializer.validated_data["academic_year"]
        semester = serializer.validated_data["semester"]

        fees = Fee.objects.filter(
            student__program__institute=request.user.institute,
            academic_year=academic_year,
            semester=semester,
        )

        return Response(
            {
                "pending_fees": fees.filter(status=Fee.FeeStatus.PENDING).count(),
                "paid_fees": fees.filter(status=Fee.FeeStatus.PAID).count(),
                "waived_fees": fees.filter(status=Fee.FeeStatus.WAIVED).count(),
                "overdue_fees": fees.filter(status=Fee.FeeStatus.OVERDUE).count(),
            }
        )

    @extend_schema(
        parameters=[SummaryFilterSerializer],
    )
    @action(methods=["get"], detail=False)
    def generated_batch(self, request):
        serializer = SummaryFilterSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        academic_year = serializer.validated_data["academic_year"]
        semester = serializer.validated_data["semester"]

        user = request.user

        queryset = GeneratedFeeBatch.objects.filter(
            institute=user.institute, academic_year=academic_year, semester=semester
        )

        # This translates to: SELECT category_name, COUNT(id) ... GROUP BY category_name
        category_counts = (
            queryset.values(category_name=F("category__category_name"))
            .annotate(generated_total=Count("id"))
            .order_by("category__category_name")
        )
        return Response(category_counts)

    @extend_schema(
        parameters=[SummaryFilterSerializer],
    )
    @action(methods=["get"], detail=False)
    def payments(self, request):
        serializer = SummaryFilterSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        academic_year = serializer.validated_data["academic_year"]
        semester = serializer.validated_data["semester"]

        # Base Filter: Ensuring we cross the Fee relationship to check the year/semester
        # Cancelled payments are reversed money - not revenue.
        base_qs = Payment.objects.filter(
            fee__academic_year=academic_year,
            fee__semester=semester,
            fee__student__program__institute=request.user.institute,
            status=Payment.PaymentStatus.ACTIVE,
        )

        dashboard_data = base_qs.aggregate(
            # Grand Totals
            total_revenue=Sum("amount_paid", default=Decimal(0)),
            total_transactions=Count("id"),
            # Cash Breakdown
            cash_total=Sum(
                "amount_paid", filter=Q(payment_method="cash"), default=Decimal(0)
            ),
            cash_count=Count("id", filter=Q(payment_method="cash")),
            # GCash Breakdown
            gcash_total=Sum(
                "amount_paid", filter=Q(payment_method="gcash"), default=Decimal(0)
            ),
            gcash_count=Count("id", filter=Q(payment_method="gcash")),
            # Collection Efficiency (Sum of previous balances vs what was paid)
            total_balance_covered=Sum("previous_balance", default=Decimal(0)),
        )

        # Staff Performance (Dynamic list - hard to flatten as keys)
        staff_stats = (
            base_qs.values(staff_username=F("received_by__username"))
            .annotate(collected=Sum("amount_paid"), tx_count=Count("id"))
            .order_by("-collected")
        )

        # Inject the staff list into the flattened stats
        dashboard_data["staff_performance"] = list(staff_stats)

        return Response(dashboard_data)

    @extend_schema(
        parameters=[SummaryFilterSerializer],
    )
    @action(
        methods=["get"],
        detail=False,
        url_path="payment-submissions",
    )
    def payment_submissions(self, request):
        """
        Returns PaymentSubmission stats filtered by academic_year & semester.
        If a submission has no payments yet, it will assume the max fee as total.
        """

        serializer = SummaryFilterSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        academic_year = serializer.validated_data["academic_year"]
        semester = serializer.validated_data["semester"]

        # A submission belongs to the term through its items, and only an
        # approved one has payments - counting through `payments` would
        # never see a pending or rejected proof. Institute-scoped like every
        # other summary; a superuser with no institute sees everything.
        base_qs = PaymentSubmission.objects.filter(
            payment_submission_list__fee__academic_year=academic_year,
            payment_submission_list__fee__semester=semester,
        )
        if request.user.institute_id:
            base_qs = base_qs.filter(
                student__program__institute=request.user.institute
            )
        base_qs = base_qs.distinct()

        def amount_of(status_value):
            return Coalesce(
                Sum("total_amount_paid", filter=Q(status=status_value)),
                Decimal(0),
                output_field=DecimalField(),
            )

        Status = PaymentSubmission.SubmissionStatus
        stats = base_qs.aggregate(
            total_count=Count("id"),
            pending_count=Count("id", filter=Q(status=Status.PENDING)),
            approved_count=Count("id", filter=Q(status=Status.APPROVED)),
            rejected_count=Count("id", filter=Q(status=Status.REJECTED)),
            cancelled_count=Count("id", filter=Q(status=Status.CANCELLED)),
            withdrawn_count=Count("id", filter=Q(status=Status.WITHDRAWN)),
            approved_amount=amount_of(Status.APPROVED),
            pending_amount=amount_of(Status.PENDING),
            rejected_amount=amount_of(Status.REJECTED),
            cancelled_amount=amount_of(Status.CANCELLED),
            withdrawn_amount=amount_of(Status.WITHDRAWN),
        )
        # Money that actually arrived: approved submissions only.
        stats["total_amount_paid"] = stats["approved_amount"]

        for key in [
            "total_amount_paid",
            "approved_amount",
            "pending_amount",
            "rejected_amount",
            "cancelled_amount",
            "withdrawn_amount",
        ]:
            stats[key] = float(stats.get(key) or 0)

        return Response(stats)

    @action(
        methods=["get"],
        detail=False,
        url_path="collection-categories",
    )
    def collection_categories(self, request):

        queryset = CollectionCategory.objects.filter(institute=request.user.institute)

        aggr = queryset.aggregate(
            total_count=Count("id"),
            # total_sum=Coalesce(Sum("collection_fee"), Decimal("0.00")),
            avg_fee=Coalesce(Avg("collection_fee"), Decimal("0.00")),
            max_val=Max("collection_fee"),
            min_val=Min("collection_fee"),
        )

        max_cat = (
            queryset.filter(collection_fee=aggr["max_val"])
            .values_list("category_name", flat=True)
            .first()
        )
        min_cat = (
            queryset.filter(collection_fee=aggr["min_val"])
            .values_list("category_name", flat=True)
            .first()
        )

        stats = {
            "total_categories": aggr["total_count"],
            # "total_revenue_potential": aggr["total_sum"],
            "average_fee": round(aggr["avg_fee"], 2),
            "highest_fee_amount": aggr["max_val"] or 0,
            "highest_fee_category": max_cat or "N/A",
            "lowest_fee_amount": aggr["min_val"] or 0,
            "lowest_fee_category": min_cat or "N/A",
        }

        return Response(stats)

    @extend_schema(
        parameters=[SummaryFilterSerializer],
    )
    @action(methods=["get"], detail=False, url_path="student-dashboard")
    def student_dashboard(self, request):
        user = request.user
        if not hasattr(user, "student"):
            raise PermissionDenied("Only students can access this endpoint.")

        student = user.student

        serializer = SummaryFilterSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        academic_year = serializer.validated_data["academic_year"]
        semester = serializer.validated_data["semester"]

        fee_stats = Fee.objects.filter(
            student=student, academic_year=academic_year, semester=semester
        ).aggregate(
            total_billed=Coalesce(Sum("total_amount"), Decimal("0.00")),
            total_discount=Coalesce(Sum("discount_total"), Decimal("0.00")),
            curr_balance=Coalesce(Sum("balance"), Decimal("0.00")),
            total_f=Count("id"),
            # Fee Status Counts
            p_count=Count("id", filter=Q(status="pending")),
            pd_count=Count("id", filter=Q(status="paid")),
            w_count=Count("id", filter=Q(status="waived")),
            o_count=Count("id", filter=Q(status="overdue")),
            # Alerting
            # next_due=Min('due_date', filter=Q(status__in=['pending', 'overdue']))
        )

        # Using distinct() because one submission can have multiple items
        sub_stats = (
            PaymentSubmission.objects.filter(
                student=student,
                payment_submission_list__fee__academic_year=academic_year,
                payment_submission_list__fee__semester=semester,
            )
            .distinct()
            .aggregate(
                total_s=Count("id"),
                s_pending_count=Count("id", filter=Q(status="pending")),
                s_approved_count=Count("id", filter=Q(status="approved")),
                s_rejected_count=Count("id", filter=Q(status="rejected")),
                s_cancelled_count=Count("id", filter=Q(status="cancelled")),
                s_withdrawn_count=Count("id", filter=Q(status="withdrawn")),
                # The actual money currently floating in 'Pending' status
                s_pending_amt=Coalesce(
                    Sum("total_amount_paid", filter=Q(status="pending")),
                    Decimal("0.00"),
                ),
            )
        )

        total_billed = fee_stats["total_billed"]
        total_discount = fee_stats["total_discount"]
        # What the student is actually asked for, after any relief. Progress
        # is measured against that, not against the gross: a student whose
        # fees were half discounted and who paid the rest is at 100%, and
        # showing them 50% would be telling them they still owe something.
        total_expected = total_billed - total_discount
        total_paid = total_expected - fee_stats["curr_balance"]
        progress = (total_paid / total_expected * 100) if total_expected > 0 else 0

        return Response(
            {
                "account_summary": {
                    "overall_balance": fee_stats["curr_balance"],
                    "total_billed": total_billed,
                    "total_discount": total_discount,
                    "total_expected": total_expected,
                    "total_paid_confirmed": total_paid,
                    "payment_progress_percent": round(progress, 2),
                    # "has_overdue": fee_stats['o_count'] > 0,
                    # "next_due_date": fee_stats['next_due']
                },
                "fees": {
                    "total_count": fee_stats["total_f"],
                    "pending_count": fee_stats["p_count"],
                    "paid_count": fee_stats["pd_count"],
                    "waived_count": fee_stats["w_count"],
                    "overdue_count": fee_stats["o_count"],
                },
                "submissions": {
                    "total_count": sub_stats["total_s"],
                    "pending_review_count": sub_stats["s_pending_count"],
                    "approved_count": sub_stats["s_approved_count"],
                    "rejected_count": sub_stats["s_rejected_count"],
                    "cancelled_count": sub_stats["s_cancelled_count"],
                    "withdrawn_count": sub_stats["s_withdrawn_count"],
                    "pending_review_amount": sub_stats["s_pending_amt"],
                },
            }
        )

    @extend_schema(
        parameters=[SummaryFilterSerializer],
    )
    @action(methods=["get"], detail=False, url_path="student-fee")
    def student_fee(self, request):
        serializer = SummaryFilterSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        user = request.user

        if not hasattr(user, "student"):
            raise PermissionDenied("Only students can access this endpoint.")

        academic_year = serializer.validated_data["academic_year"]
        semester = serializer.validated_data["semester"]

    @extend_schema(
        parameters=[SummaryFilterSerializer],
    )
    @action(methods=["get"], detail=False, url_path="student-paments")
    def student_paments(self, request):
        serializer = SummaryFilterSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        user = request.user

        if not hasattr(user, "student"):
            raise PermissionDenied("Only students can access this endpoint.")

        academic_year = serializer.validated_data["academic_year"]
        semester = serializer.validated_data["semester"]

    @extend_schema(
        parameters=[SummaryFilterSerializer],
    )
    @action(methods=["get"], detail=False, url_path="student-payment-submissions")
    def student_payment_submissions(self, request):
        serializer = SummaryFilterSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        user = request.user

        if not hasattr(user, "student"):
            raise PermissionDenied("Only students can access this endpoint.")

        academic_year = serializer.validated_data["academic_year"]
        semester = serializer.validated_data["semester"]
