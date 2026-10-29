"""
The wire contract for the three detailed reports.

Same split as the collection report's module: the params serializer is real -
it validates the query string and is where "a detailed report needs a period"
is enforced - and everything below it is documentation, so the OpenAPI schema
describes the payload instead of guessing at it.
"""

from rest_framework import serializers

from .collection_report_serializers import (
    CollectionReportParamsSerializer,
    CollectionReportPaymentSubmissionsSerializer,
    CollectionReportStatusBreakdownSerializer,
)


class DetailedReportParamsSerializer(CollectionReportParamsSerializer):
    """
    Same filters as the collection report, but a period is mandatory.

    A detailed report lists rows, not aggregates - unbounded, it is every fee
    an institute has ever issued and a PDF with no last page. Either an
    academic year *and* semester, or both ends of a date range.
    """

    def validate(self, attrs):  # type: ignore[override]
        attrs = super().validate(attrs)

        has_term = attrs.get("academic_year") and attrs.get("semester")
        has_range = attrs.get("date_from") and attrs.get("date_to")
        if not (has_term or has_range):
            raise serializers.ValidationError(
                "A detailed report needs a period: send academic_year and "
                "semester together, or date_from and date_to together."
            )

        return attrs


class ReportTotalsSerializer(serializers.Serializer):
    """
    The one totals shape used at every level of every detailed report.
    Waived fees are excluded from all four numbers but still appear in
    status counts and fee lists.
    """

    billed = serializers.FloatField()
    paid = serializers.FloatField()
    balance = serializers.FloatField()
    collection_rate = serializers.FloatField(
        help_text="paid / billed * 100, one decimal; 0.0 when nothing billed."
    )


class ReportGroupTotalsSerializer(ReportTotalsSerializer):
    """Group levels also say how many students they cover."""

    student_count = serializers.IntegerField()


# --------------------------------------------------------------------------
# Detailed fees
# --------------------------------------------------------------------------


class DetailedFeePaymentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    amount_paid = serializers.FloatField()
    previous_balance = serializers.FloatField(
        help_text="The fee's balance before this payment drew it down."
    )
    payment_method = serializers.CharField()
    received_by = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()


class DetailedFeeSubmissionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    reference_number = serializers.CharField()
    status = serializers.CharField()
    amount_for_this_fee = serializers.FloatField(
        help_text="This fee's slice of the submission."
    )
    previous_balance_for_this_fee = serializers.FloatField(
        help_text="The fee's balance when the student submitted."
    )
    total_amount_paid = serializers.FloatField(
        help_text="The whole submission, which may cover other fees too."
    )
    submitted_at = serializers.DateTimeField()
    screenshot_urls = serializers.ListField(
        child=serializers.CharField(),
        help_text="Cloudinary links to the proof-of-payment screenshots.",
    )
    remarks = serializers.CharField(allow_null=True)
    reviewed_by = serializers.CharField(allow_null=True)
    reviewed_at = serializers.DateTimeField(allow_null=True)
    payments = DetailedFeePaymentSerializer(
        many=True, help_text="Payments born from this submission, for this fee."
    )


class DetailedFeeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    student_number = serializers.CharField()
    student_name = serializers.CharField()
    program = serializers.CharField(help_text="Full program name.")
    year_level = serializers.IntegerField()
    set = serializers.CharField()
    total_amount = serializers.FloatField()
    paid = serializers.FloatField()
    balance = serializers.FloatField()
    status = serializers.CharField()
    due_date = serializers.DateTimeField(allow_null=True)
    issued_at = serializers.DateTimeField()
    issued_by = serializers.CharField(allow_null=True)
    academic_year = serializers.CharField()
    semester = serializers.CharField()
    remarks = serializers.CharField(
        allow_null=True, help_text="For a waived fee, the exemption reason."
    )
    submissions = DetailedFeeSubmissionSerializer(many=True)
    payments = DetailedFeePaymentSerializer(
        many=True,
        help_text=(
            "Over-the-counter payments only - anything born from a "
            "submission is listed under that submission instead, so no "
            "payment ever appears twice."
        ),
    )


class DetailedFeesStatusGroupSerializer(serializers.Serializer):
    """
    One status's fees inside a category. Groups appear overdue, pending,
    paid, waived - money still owed first.
    """

    status = serializers.CharField()
    count = serializers.IntegerField()
    totals = ReportTotalsSerializer(
        help_text=(
            "Same rule as everywhere: the waived group counts its fees but "
            "contributes zero money, so status groups still sum exactly to "
            "the category total."
        )
    )
    fees = DetailedFeeSerializer(many=True)


class DetailedFeesCategorySerializer(serializers.Serializer):
    category_name = serializers.CharField()
    totals = ReportTotalsSerializer()
    statuses = DetailedFeesStatusGroupSerializer(many=True)


class DetailedFeesPaymentsSummarySerializer(serializers.Serializer):
    """
    Sums of the *active* payments listed in the payments section - a records
    summary, so it includes money received on later-waived fees (the losses
    section shows which). The collection report's KPI excludes that money.
    """

    total_collections = serializers.FloatField()
    cash = serializers.FloatField()
    gcash = serializers.FloatField()
    tx_count = serializers.IntegerField()


class DetailedFeesSummarySerializer(serializers.Serializer):
    """Computed from the very rows listed below it, so the two always agree."""

    status_breakdown = CollectionReportStatusBreakdownSerializer()
    payments = DetailedFeesPaymentsSummarySerializer()
    payment_submissions = CollectionReportPaymentSubmissionsSerializer()


class DetailedPaymentRecordSerializer(serializers.Serializer):
    """One payment, every column the payments table has."""

    id = serializers.IntegerField()
    fee_id = serializers.IntegerField()
    category = serializers.CharField()
    student_number = serializers.CharField()
    student_name = serializers.CharField()
    previous_balance = serializers.FloatField(
        help_text="The fee's balance before this payment drew it down."
    )
    amount_paid = serializers.FloatField()
    payment_method = serializers.CharField()
    source = serializers.CharField(
        help_text='"submission" when born from a student submission, '
        '"direct" for over the counter.'
    )
    payment_submission_id = serializers.IntegerField(allow_null=True)
    submission_reference = serializers.CharField(allow_null=True)
    received_by = serializers.CharField(allow_null=True)
    status = serializers.CharField(help_text='"active" or "cancelled".')
    cancelled_by = serializers.CharField(allow_null=True)
    cancelled_at = serializers.DateTimeField(allow_null=True)
    cancellation_reason = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class DetailedPaymentsActiveTotalsSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    amount_paid = serializers.FloatField()
    cash = serializers.FloatField()
    gcash = serializers.FloatField()


class DetailedPaymentsCancelledTotalsSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    amount_paid = serializers.FloatField()


class DetailedPaymentsTotalsSerializer(serializers.Serializer):
    """
    Straight sums of the records listed above them, in three layers: the
    grand count/amount over every listed row, `active` for the money
    actually held (with the cash/GCash split), and `cancelled` for money
    that was recorded and reversed.
    """

    count = serializers.IntegerField()
    amount_paid = serializers.FloatField()
    active = DetailedPaymentsActiveTotalsSerializer()
    cancelled = DetailedPaymentsCancelledTotalsSerializer()


class DetailedPaymentsSectionSerializer(serializers.Serializer):
    records = DetailedPaymentRecordSerializer(
        many=True,
        help_text="Oldest first; cancelled payments listed and flagged.",
    )
    totals = DetailedPaymentsTotalsSerializer()


class DetailedSubmissionRecordSerializer(serializers.Serializer):
    """One submission, listed once however many fees it covers."""

    id = serializers.IntegerField()
    submitted_at = serializers.DateTimeField()
    student_number = serializers.CharField()
    student_name = serializers.CharField()
    reference_number = serializers.CharField()
    status = serializers.CharField()
    total_amount_paid = serializers.FloatField()
    fee_count = serializers.IntegerField(
        help_text="How many fees it pays off - its rows in "
        "payment_submission_items, joined by payment_submission_id."
    )
    screenshot_urls = serializers.ListField(
        child=serializers.CharField(),
        help_text="Cloudinary links to the proof-of-payment screenshots.",
    )
    reviewed_by = serializers.CharField(allow_null=True)
    reviewed_at = serializers.DateTimeField(allow_null=True)
    remarks = serializers.CharField(allow_null=True)
    updated_at = serializers.DateTimeField()


class DetailedSubmissionsTotalsSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    total_amount_paid = serializers.FloatField()


class DetailedSubmissionsSectionSerializer(serializers.Serializer):
    records = DetailedSubmissionRecordSerializer(
        many=True, help_text="Oldest first."
    )
    totals = DetailedSubmissionsTotalsSerializer()


class DetailedSubmissionItemRecordSerializer(serializers.Serializer):
    """One line item - a single fee's slice of a submission."""

    id = serializers.IntegerField()
    payment_submission_id = serializers.IntegerField()
    submission_reference = serializers.CharField()
    submission_status = serializers.CharField()
    fee_id = serializers.IntegerField()
    category = serializers.CharField()
    student_number = serializers.CharField()
    student_name = serializers.CharField()
    previous_balance = serializers.FloatField(
        help_text="The fee's balance when the student submitted."
    )
    amount_paid = serializers.FloatField()
    created_at = serializers.DateTimeField()


class DetailedSubmissionItemsTotalsSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    amount_paid = serializers.FloatField()


class DetailedSubmissionItemsSectionSerializer(serializers.Serializer):
    records = DetailedSubmissionItemRecordSerializer(
        many=True, help_text="Grouped by submission, oldest first."
    )
    totals = DetailedSubmissionItemsTotalsSerializer()


class DetailedLossPaymentSerializer(DetailedFeePaymentSerializer):
    status = serializers.CharField(help_text='"active" or "cancelled".')
    submission_reference = serializers.CharField(allow_null=True)


class DetailedLossSubmissionItemSerializer(serializers.Serializer):
    item_id = serializers.IntegerField()
    payment_submission_id = serializers.IntegerField()
    submission_reference = serializers.CharField()
    submission_status = serializers.CharField()
    previous_balance = serializers.FloatField()
    amount_paid = serializers.FloatField()
    submitted_at = serializers.DateTimeField()


class DetailedLossRecordSerializer(serializers.Serializer):
    """One waived fee, with the payment history behind it."""

    fee_id = serializers.IntegerField()
    category = serializers.CharField()
    student_number = serializers.CharField()
    student_name = serializers.CharField()
    program = serializers.CharField()
    year_level = serializers.IntegerField()
    set = serializers.CharField()
    total_amount = serializers.FloatField()
    paid_before_waive = serializers.FloatField()
    waived_amount = serializers.FloatField(
        help_text="The forgiven remainder - this fee's loss."
    )
    remarks = serializers.CharField(
        allow_null=True, help_text="The waive reason; remarks are mandatory."
    )
    payments = DetailedLossPaymentSerializer(many=True)
    submission_items = DetailedLossSubmissionItemSerializer(many=True)


class DetailedLossesSectionSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    billed = serializers.FloatField()
    collected_before_waive = serializers.FloatField()
    waived_amount = serializers.FloatField(
        help_text="The forgiven balances - the actual loss."
    )
    records = DetailedLossRecordSerializer(many=True)


class DetailedFeesReportSerializer(serializers.Serializer):
    generated_at = serializers.DateTimeField()
    summary = DetailedFeesSummarySerializer()
    categories = DetailedFeesCategorySerializer(
        many=True, help_text="Sorted by category name."
    )
    totals = ReportTotalsSerializer()
    payments = DetailedPaymentsSectionSerializer(
        help_text="Every payment behind the listed fees, flat."
    )
    payment_submissions = DetailedSubmissionsSectionSerializer(
        help_text="Every submission touching a listed fee, one record each."
    )
    payment_submission_items = DetailedSubmissionItemsSectionSerializer(
        help_text="The line items of those submissions, one per fee covered."
    )
    losses = DetailedLossesSectionSerializer(
        help_text="The waived fees, each with its payments and items."
    )


# --------------------------------------------------------------------------
# By staff
# --------------------------------------------------------------------------


class StaffReportPaymentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    created_at = serializers.DateTimeField()
    amount_paid = serializers.FloatField()
    previous_balance = serializers.FloatField(
        help_text="The fee's balance before this payment drew it down."
    )
    payment_method = serializers.CharField()
    student_number = serializers.CharField()
    student_name = serializers.CharField()
    category = serializers.CharField()
    fee_id = serializers.IntegerField()
    submission_reference = serializers.CharField(
        allow_null=True,
        help_text="Set when the payment came out of a student submission.",
    )


class StaffReportMemberTotalsSerializer(serializers.Serializer):
    collected = serializers.FloatField()
    tx_count = serializers.IntegerField()
    cash = serializers.FloatField()
    gcash = serializers.FloatField()
    share_of_total = serializers.FloatField(
        help_text="This member's collected as a percentage of everyone's."
    )


class StaffReportMemberSerializer(serializers.Serializer):
    staff_username = serializers.CharField()
    full_name = serializers.CharField(allow_null=True)
    totals = StaffReportMemberTotalsSerializer()
    payments = StaffReportPaymentSerializer(
        many=True, help_text="Newest first."
    )


class StaffReportGrandTotalsSerializer(serializers.Serializer):
    collected = serializers.FloatField()
    tx_count = serializers.IntegerField()
    cash = serializers.FloatField()
    gcash = serializers.FloatField()


class StaffReportSerializer(serializers.Serializer):
    generated_at = serializers.DateTimeField()
    staff = StaffReportMemberSerializer(
        many=True, help_text="Sorted by collected, descending."
    )
    totals = StaffReportGrandTotalsSerializer()


# --------------------------------------------------------------------------
# By program, year level and set
# --------------------------------------------------------------------------


class ProgramSetReportFeeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    category = serializers.CharField()
    total_amount = serializers.FloatField()
    paid = serializers.FloatField()
    balance = serializers.FloatField()
    status = serializers.CharField()
    due_date = serializers.DateTimeField(allow_null=True)
    remarks = serializers.CharField(allow_null=True)


class ProgramSetReportStudentSerializer(serializers.Serializer):
    student_number = serializers.CharField()
    student_name = serializers.CharField()
    totals = ReportTotalsSerializer()
    fees = ProgramSetReportFeeSerializer(
        many=True, help_text="Sorted by category name."
    )


class ProgramSetReportSetSerializer(serializers.Serializer):
    set = serializers.CharField(help_text='"—" when the students have no set.')
    totals = ReportGroupTotalsSerializer()
    students = ProgramSetReportStudentSerializer(
        many=True, help_text="Sorted by surname."
    )


class ProgramSetReportYearLevelSerializer(serializers.Serializer):
    year_level = serializers.IntegerField()
    totals = ReportGroupTotalsSerializer()
    sets = ProgramSetReportSetSerializer(
        many=True, help_text="A-Z, blank sets last."
    )


class ProgramSetReportProgramSerializer(serializers.Serializer):
    program = serializers.CharField(help_text="Full program name.")
    totals = ReportGroupTotalsSerializer()
    year_levels = ProgramSetReportYearLevelSerializer(
        many=True, help_text="Ascending."
    )


class ProgramSetReportSerializer(serializers.Serializer):
    generated_at = serializers.DateTimeField()
    programs = ProgramSetReportProgramSerializer(
        many=True, help_text="Sorted by program name."
    )
    totals = ReportGroupTotalsSerializer()
