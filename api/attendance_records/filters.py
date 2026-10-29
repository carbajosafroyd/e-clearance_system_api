from django_filters import FilterSet, CharFilter
from .models import AttendanceRecord
class AttendanceRecordFilterSet(FilterSet):
    
    program_name = CharFilter(field_name="student__program__name", lookup_expr="icontains")
    institute_attendance_event_id = CharFilter(field_name="institute_attendance_event", lookup_expr="exact")
    
    class Meta: 
        model = AttendanceRecord
        fields = []
    