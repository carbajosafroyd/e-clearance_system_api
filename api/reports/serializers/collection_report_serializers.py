"""
The wire contract for the collection report.

`CollectionReportParamsSerializer` is real - it validates and coerces the
query string. Everything below it is documentation: the response is assembled
as a plain dict by the service, and these classes exist so the OpenAPI schema
describes the payload instead of shrugging at it.
"""

from rest_framework import serializers

from core.choices import AcademicYear, Semester


class CollectionReportParamsSerializer(serializers.Serializer):
    """
    Query parameters. Every one of them is optional - no filters at all means
    the whole institute, all time.
    """

    academic_year = serializers.ChoiceField(
        choices=AcademicYear.choices(),
        required=False,
        help_text="Ignored when a date range is given.",
    )
    semester = serializers.ChoiceField(
        choices=Semester.choices,
        required=False,
        help_text="Ignored when a date range is given.",
    )
    date_from = serializers.DateField(
        required=False,
        help_text="Overrides academic_year / semester when present.",
    )
    date_to = serializers.DateField(
        required=False,
        help_text="Overrides academic_year / semester when present.",
    )
    # Dunder name on purpose: it matches what the frontend already sends and
    # mirrors the ORM lookup used by the fee list filters.
    program__name = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Program name, exactly as it appears in filter_options.programs.",
    )
    s_set = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Student set, matched exactly.",
    )
    year_level = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Student year level (Student.s_lvl), matched exactly.",
    )
    compare_previous = serializers.BooleanField(
        required=False,
        default=False,
        help_text=(
            "Fill previous_total_collections with the same total over the "
            "window immediately before this one."
        ),
    )

    def validate(self, attrs):  # type: ignore[override]
        date_from = attrs.get("date_from")
        date_to = attrs.get("date_to")

        if date_from and date_to and date_from > date_to:
            raise serializers.ValidationError(
                {"date_from": "date_from cannot be later than date_to."}
            )

        return attrs


class ModuleReportParamsSerializer(CollectionReportParamsSerializer):
    """
    Query parameters for the optional-module reports (complaints, reports &
    ideas, leaderboard, point discounts, coupons).

    Same period and student-scope vocabulary as the money reports, plus the
    two things those modules are cut by: the system a record was filed
    against, and its status. Every parameter is optional - nothing at all
    means the whole institute, all time - because these lists are small
    enough to be read whole, and "everything we ever received" is a real
    question here in a way it is not for a fee ledger.
    """

    system = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=(
            "One of collection, locker, attendance, clearance, voting, "
            "platform. Complaints and contributions are filed against one "
            "system; the leaderboard and point wallets are kept per system."
        ),
    )
    status = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=(
            "Comma-separated statuses to keep, in the module's own vocabulary "
            "(e.g. `submitted,acknowledged`). Omit for every status."
        ),
    )


class CollectionReportKpiSerializer(serializers.Serializer):
    total_collections = serializers.FloatField(
        help_text=(
            "Active payments only, and only on fees that are not waived - "
            "money received on a later-waived fee is reported under "
            "losses.waived.collected_before_waive instead."
        )
    )
    previous_total_collections = serializers.FloatField(
        allow_null=True,
        help_text=(
            "Null unless compare_previous was sent, and also null when there "
            "is no comparable window - never a stand-in zero."
        ),
    )
    total_outstanding = serializers.FloatField(
        help_text="Unpaid balance on fees that are actually owed; waived fees excluded."
    )
    cash = serializers.FloatField()
    gcash = serializers.FloatField()
    top_category = serializers.CharField(
        allow_null=True, help_text="Best collection rate, not the largest amount."
    )
    top_category_rate = serializers.FloatField(help_text="Percentage, e.g. 85.5.")


class CollectionReportStatusBreakdownSerializer(serializers.Serializer):
    paid = serializers.IntegerField()
    pending = serializers.IntegerField(
        help_text="Includes part-paid fees; this system has no separate partial status."
    )
    overdue = serializers.IntegerField()
    waived = serializers.IntegerField()


class CollectionReportCategorySerializer(serializers.Serializer):
    category_name = serializers.CharField()
    collection_rate = serializers.FloatField(
        help_text="Collected over `expected`, i.e. net of relief. Percentage."
    )
    billed = serializers.FloatField(
        help_text="Gross, before any relief. Waived fees excluded."
    )
    discounts = serializers.FloatField(
        help_text="Relief granted - points, coupons. `billed - discounts = expected`."
    )
    expected = serializers.FloatField(
        help_text="What is actually asked for, after relief. Waived fees excluded."
    )
    collected = serializers.FloatField()
    outstanding = serializers.FloatField()
    fee_count = serializers.IntegerField(help_text="Every fee, waived included.")
    paid_count = serializers.IntegerField()
    pending_count = serializers.IntegerField()
    overdue_count = serializers.IntegerField()
    waived_count = serializers.IntegerField()


class CollectionReportSubmissionStatSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    amount = serializers.FloatField()


class CollectionReportPaymentSubmissionsSerializer(serializers.Serializer):
    """Student-submitted payment proofs, by review status."""

    total = CollectionReportSubmissionStatSerializer(
        help_text="Every submission, whatever its status; the three below add up to it."
    )
    approved = CollectionReportSubmissionStatSerializer()
    pending = CollectionReportSubmissionStatSerializer(help_text="Awaiting review.")
    rejected = CollectionReportSubmissionStatSerializer()
    approval_rate = serializers.FloatField(
        allow_null=True,
        help_text=(
            "Share of reviewed submissions that were approved, as a "
            "percentage - pending ones are not counted against it. Null when "
            "nothing has been reviewed yet."
        ),
    )


class CollectionReportWaivedLossRecordSerializer(serializers.Serializer):
    fee_id = serializers.IntegerField()
    category = serializers.CharField()
    student_number = serializers.CharField()
    student_name = serializers.CharField()
    program = serializers.CharField()
    year_level = serializers.IntegerField()
    set = serializers.CharField(help_text='"—" when the student has no set.')
    total_amount = serializers.FloatField()
    paid_before_waive = serializers.FloatField(
        help_text="Money that came in before the rest was forgiven."
    )
    discounted_before_waive = serializers.FloatField(
        help_text="Relief granted before it was written off. Not money."
    )
    waived_amount = serializers.FloatField(
        help_text="The forgiven remainder - this fee's loss."
    )
    remarks = serializers.CharField(
        allow_null=True, help_text="The waive reason; remarks are mandatory."
    )


class CollectionReportWaivedLossesSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    billed = serializers.FloatField(help_text="What the waived fees asked in full.")
    collected_before_waive = serializers.FloatField(
        help_text=(
            "Money received on these fees before they were waived. Kept, but "
            "excluded from every collection figure so payment-derived and "
            "fee-derived totals agree."
        )
    )
    discounted_before_waive = serializers.FloatField(
        help_text=(
            "Relief granted on these fees before they were written off "
            "anyway. Not money received, and not part of the loss."
        )
    )
    waived_amount = serializers.FloatField(
        help_text="The forgiven balances - the actual loss."
    )
    records = CollectionReportWaivedLossRecordSerializer(many=True)


class CollectionReportDiscrepancyRecordSerializer(serializers.Serializer):
    fee_id = serializers.IntegerField()
    category = serializers.CharField()
    student_number = serializers.CharField()
    student_name = serializers.CharField()
    status = serializers.CharField()
    total_amount = serializers.FloatField()
    discount_total = serializers.FloatField(help_text="Relief recorded on the fee.")
    balance = serializers.FloatField()
    fee_paid = serializers.FloatField(
        help_text=(
            "total_amount - discount_total - balance: what the fee says was "
            "paid off. Relief is subtracted because it is a drawdown with no "
            "payment behind it, and this figure is compared against payments."
        )
    )
    payments_total = serializers.FloatField(
        help_text="Sum of the fee's active payments."
    )
    approved_submission_total = serializers.FloatField(
        help_text=(
            "Sum of the fee's approved submission items, for cross-checking "
            "a submission that was approved but never became a payment."
        )
    )
    difference = serializers.FloatField(
        help_text="fee_paid - payments_total; positive means missing payments."
    )


class CollectionReportDiscrepanciesSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    difference = serializers.FloatField(help_text="Sum of the signed differences.")
    shortfall = serializers.FloatField(
        help_text=(
            "Sum of the positive differences only - fees drawn down with no "
            "payment behind the drawdown. This is the part that counts into "
            "total_losses."
        )
    )
    records = CollectionReportDiscrepancyRecordSerializer(many=True)


class CollectionReportDiscountRecordSerializer(serializers.Serializer):
    fee_id = serializers.IntegerField()
    category = serializers.CharField()
    student_number = serializers.CharField()
    student_name = serializers.CharField()
    program = serializers.CharField()
    year_level = serializers.IntegerField()
    set = serializers.CharField(help_text='"—" when the student has no set.')
    status = serializers.CharField()
    total_amount = serializers.FloatField(help_text="The gross bill.")
    discount_total = serializers.FloatField(
        help_text="All relief on the fee - points + coupons + other."
    )
    points = serializers.FloatField(
        help_text="Funded by approved contribution-point discounts."
    )
    coupons = serializers.FloatField(help_text="Funded by applied coupons.")
    other = serializers.FloatField(
        help_text=(
            "Relief neither ledger explains - written onto the fee by hand "
            "or by a backfill. Normally 0."
        )
    )
    balance = serializers.FloatField()


class CollectionReportDiscountsSerializer(serializers.Serializer):
    """
    Relief granted on purpose - points, coupons - and what it cost.

    Counted into `total_losses`: a discount is revenue the institute chose
    not to collect, the same kind of figure as a waived balance, and a
    report that only showed it as a narrower `expected` would leave the gap
    between billed and collected unexplained.
    """

    count = serializers.IntegerField(help_text="Fees carrying any relief.")
    amount = serializers.FloatField(help_text="points + coupons + other.")
    points = serializers.FloatField()
    coupons = serializers.FloatField()
    other = serializers.FloatField()
    records = CollectionReportDiscountRecordSerializer(many=True)


class CollectionReportLossesSerializer(serializers.Serializer):
    """
    Money that will never arrive, and money that does not add up.

    `waived` lists every waived fee: the loss is the forgiven remainder, not
    the whole bill. `discounts` lists every fee that got relief, split by
    what funded it. `discrepancies` is a per-fee reconciliation of the fee's
    own arithmetic (total_amount - discount_total - balance) against its
    active payments - a mismatch means money moved outside both the payment
    flow and the discount ledgers.
    """

    waived = CollectionReportWaivedLossesSerializer()
    discounts = CollectionReportDiscountsSerializer()
    discrepancies = CollectionReportDiscrepanciesSerializer()
    total_losses = serializers.FloatField(
        help_text=(
            "waived.waived_amount + discounts.amount + discrepancies.shortfall "
            "- forgiven money, relief granted, and money given away unrecorded."
        )
    )


class CollectionReportStaffSerializer(serializers.Serializer):
    staff_username = serializers.CharField()
    tx_count = serializers.IntegerField()
    collected = serializers.FloatField()


class CollectionReportMonthSerializer(serializers.Serializer):
    """One column of the monthly axis."""

    label = serializers.CharField(help_text='Year and month, e.g. "2026-02".')
    start = serializers.DateField(help_text="First day of the month.")
    end = serializers.DateField(help_text="Last day of the month.")


class CollectionReportTrendSeriesItemSerializer(serializers.Serializer):
    name = serializers.CharField(help_text="A real collection category name.")
    points = serializers.ListField(
        child=serializers.FloatField(),
        help_text="One value per label, index for index.",
    )


class CollectionReportTrendSerializer(serializers.Serializer):
    """
    Collections per month, one series per real collection category - nothing
    is bucketed into fixed keys.
    """

    labels = serializers.ListField(child=serializers.CharField())
    months = CollectionReportMonthSerializer(
        many=True, help_text="The same axis with the dates behind each label."
    )
    series = CollectionReportTrendSeriesItemSerializer(many=True)


class CollectionReportPaymentMethodSerializer(serializers.Serializer):
    period = serializers.CharField(help_text='Year and month, e.g. "2026-02".')
    start = serializers.DateField()
    end = serializers.DateField()
    cash = serializers.FloatField()
    gcash = serializers.FloatField()
    total = serializers.FloatField(help_text="cash + gcash, pre-rounded.")


class CollectionReportProgramSetSerializer(serializers.Serializer):
    """
    One row per program / year level / set. Sorted program A-Z, then year
    level ascending, then set A-Z with blank sets last.
    """

    program = serializers.CharField(help_text="Full program name.")
    year_level = serializers.IntegerField()
    set = serializers.CharField(help_text='"—" when the student has no set.')
    billed = serializers.FloatField(help_text="Waived fees excluded.")
    total = serializers.FloatField(help_text="Collected, not billed.")
    balance = serializers.FloatField()
    rate = serializers.FloatField(help_text="Percentage, e.g. 91.2.")


class CollectionReportFilterOptionsSerializer(serializers.Serializer):
    programs = serializers.ListField(child=serializers.CharField())
    staff = serializers.ListField(child=serializers.CharField())


class CollectionReportSerializer(serializers.Serializer):
    """The whole snapshot. Documentation only - see the module docstring."""

    generated_at = serializers.DateTimeField()
    kpi = CollectionReportKpiSerializer()
    status_breakdown = CollectionReportStatusBreakdownSerializer()
    categories = CollectionReportCategorySerializer(many=True)
    losses = CollectionReportLossesSerializer()
    payment_submissions = CollectionReportPaymentSubmissionsSerializer()
    staff = CollectionReportStaffSerializer(many=True)
    trend = CollectionReportTrendSerializer()
    payment_methods = CollectionReportPaymentMethodSerializer(many=True)
    program_sets = CollectionReportProgramSetSerializer(many=True)
    filter_options = CollectionReportFilterOptionsSerializer()
