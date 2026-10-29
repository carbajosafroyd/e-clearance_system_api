import django_filters

from .models import Fee, GeneratedFeeBatch


class FeesFilterSet(django_filters.FilterSet):
    """
    Query-param filters for /fees/.

    `due_date` is a DateTimeField, so every date-shaped filter here goes
    through the `date` lookup. Comparing a bare date against a datetime column
    would make `?due_date_lte=2026-08-13` mean "before midnight on the 13th"
    and silently drop that entire day.
    """

    due_date = django_filters.DateFilter(field_name="due_date", lookup_expr="date")
    due_date_gte = django_filters.DateFilter(
        field_name="due_date", lookup_expr="date__gte"
    )
    due_date_lte = django_filters.DateFilter(
        field_name="due_date", lookup_expr="date__lte"
    )

    category_id = django_filters.NumberFilter(field_name="category_id")
    category_name = django_filters.CharFilter(
        field_name="category__category_name", lookup_expr="icontains"
    )

    total_amount = django_filters.NumberFilter(field_name="total_amount")
    total_amount_gte = django_filters.NumberFilter(
        field_name="total_amount", lookup_expr="gte"
    )
    total_amount_lte = django_filters.NumberFilter(
        field_name="total_amount", lookup_expr="lte"
    )

    # "which fees got relief, and how much" - the question an audit of a
    # discount scheme opens with.
    discount_total_gte = django_filters.NumberFilter(
        field_name="discount_total", lookup_expr="gte"
    )
    discount_total_lte = django_filters.NumberFilter(
        field_name="discount_total", lookup_expr="lte"
    )
    balance_gte = django_filters.NumberFilter(field_name="balance", lookup_expr="gte")
    balance_lte = django_filters.NumberFilter(field_name="balance", lookup_expr="lte")

    program_id = django_filters.NumberFilter(field_name="student__program_id")
    program_name = django_filters.CharFilter(
        field_name="student__program__name", lookup_expr="icontains"
    )

    institute_id = django_filters.NumberFilter(field_name="student__program__institute")
    institute_name = django_filters.CharFilter(
        field_name="student__program__institute__institute_name",
        lookup_expr="icontains",
    )
    school_id = django_filters.NumberFilter(
        field_name="student__program__institute__school"
    )

    student_id = django_filters.NumberFilter(field_name="student_id")
    student_school_id = django_filters.CharFilter(
        field_name="student__s_studentID", lookup_expr="icontains"
    )
    year_level = django_filters.NumberFilter(field_name="student__s_lvl")

    batch_id = django_filters.NumberFilter(field_name="gen_fee_batch_id")
    attendance_event_id = django_filters.NumberFilter(
        field_name="institute_attendance_event_id"
    )
    issued_by = django_filters.CharFilter(
        field_name="issued_by__username", lookup_expr="icontains"
    )

    status = django_filters.MultipleChoiceFilter(choices=Fee.FeeStatus.choices)

    class Meta:
        model = Fee
        fields = ["semester", "status", "academic_year", "id"]


class GeneratedFeeBatchFilterSet(django_filters.FilterSet):
    """
    Query-param filters for /generate-fees/ (the batch list).

    The frontend's filter modal has been sending `category`, `academic_year`,
    `semester` and `generated_by` all along - until now the backend silently
    ignored every one of them. `category` and `generated_by` are free-text
    inputs on that modal, so both match by name, not pk; `category_id` covers
    the pk case.
    """

    category = django_filters.CharFilter(
        field_name="category__category_name", lookup_expr="icontains"
    )
    category_id = django_filters.NumberFilter(field_name="category_id")
    generated_by = django_filters.CharFilter(
        field_name="generated_by__username", lookup_expr="icontains"
    )

    # created_at is a datetime, so the date-shaped bounds go through the
    # `date` lookup - see the FeesFilterSet docstring for why.
    date_gte = django_filters.DateFilter(
        field_name="created_at", lookup_expr="date__gte"
    )
    date_lte = django_filters.DateFilter(
        field_name="created_at", lookup_expr="date__lte"
    )

    total_created_fees_gte = django_filters.NumberFilter(
        field_name="total_created_fees", lookup_expr="gte"
    )
    total_created_fees_lte = django_filters.NumberFilter(
        field_name="total_created_fees", lookup_expr="lte"
    )

    class Meta:
        model = GeneratedFeeBatch
        # Comma-separated multi-choice via `in`, e.g. `?semester__in=1st,2nd`.
        fields = {
            "id": ["exact"],
            "academic_year": ["exact", "in"],
            "semester": ["exact", "in"],
        }
