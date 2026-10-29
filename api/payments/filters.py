import django_filters
from .models import Payment
from core.choices import AcademicYear, Semester

class PaymentFilterSet(django_filters.FilterSet):

    fee_id = django_filters.NumberFilter(field_name="fee__id")
    academic_year = django_filters.ChoiceFilter(field_name="fee__academic_year", choices = AcademicYear.choices())
    semester = django_filters.ChoiceFilter(field_name="fee__semester", choices = Semester.choices)
    
    student_id = django_filters.CharFilter(
        field_name="fee__student__id", lookup_expr="exact"
    )
    student_school_id = django_filters.CharFilter(
        field_name="fee__student__s_studentID", lookup_expr="exact"
    )
    payment_method = django_filters.ChoiceFilter(choices=Payment.PaymentMethod.choices)
    status = django_filters.ChoiceFilter(choices=Payment.PaymentStatus.choices)
    payment_submission_id = django_filters.NumberFilter(
        field_name="payment_submission__id", lookup_expr="exact"
    )
    
    received_by_id = django_filters.NumberFilter(
        field_name="received_by", lookup_expr="exact"
    )

    received_by_username = django_filters.CharFilter(
        field_name="received_by__username", lookup_expr="icontains"
    )
            # fields = ["semester", "status", "academic_year"]

    class Meta:
        model = Payment
        fields = ["id"]
