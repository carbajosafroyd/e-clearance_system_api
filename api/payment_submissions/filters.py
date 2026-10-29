import django_filters
from .models import PaymentSubmission
from core.choices import AcademicYear, Semester


class PaymentSubmissionFilterSet(django_filters.FilterSet):

    academic_year = django_filters.ChoiceFilter(
        field_name="payment_submission_list__fee__academic_year",
        choices=AcademicYear.choices(),
        label="Academic Year",
    )

    semester = django_filters.ChoiceFilter(
        field_name="payment_submission_list__fee__semester",
        choices=Semester.choices,
        label="Semester",
    )
    student_id = django_filters.NumberFilter(
        field_name="student_id", lookup_expr="exact"
    )
    student_school_id = django_filters.CharFilter(
        field_name="student__s_studentID", lookup_expr="icontains"
    )
    reviewed_by = django_filters.CharFilter(
        field_name="reviewed_by__username", lookup_expr="icontains"
    )

    class Meta:
        model = PaymentSubmission
        fields = ["status", "id"]
