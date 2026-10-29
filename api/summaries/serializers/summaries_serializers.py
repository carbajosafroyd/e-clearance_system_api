from rest_framework import serializers
from core.choices import AcademicYear, get_default_academic_year, Semester


class SummaryFilterSerializer(serializers.Serializer):
    academic_year = serializers.ChoiceField(
        choices=AcademicYear.choices(), required=True
    )
    semester = serializers.ChoiceField(choices=Semester.choices, required=True)