from rest_framework import serializers
from .models import School, Location

class SchoolCreateSerializer(serializers.ModelSerializer):
    """Schema for creating a school"""

    class Meta:
        model = School
        fields = ["school_name", "logo", "location"]


class LocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Location
        fields = ["id", "city", "country"]


class SchoolReadSerializer(serializers.ModelSerializer):
    """Detailed schema for reading/retrieving"""
    location = LocationSerializer(read_only=True)

    class Meta:
        model = School
        fields = ["id", "school_name", "logo", "location"]


class SchoolUpdateSerializer(serializers.ModelSerializer):
    """Schema for updates (maybe restrict some fields)"""

    class Meta:
        model = School
        fields = ["logo", "location"]  # if cannot change school_name


class SchoolDeleteSerializer(serializers.ModelSerializer):
    """Schema for confirming deletion"""

    class Meta:
        model = School
        fields = ["id", "school_name"]


class SchoolSummarySerializer(serializers.ModelSerializer):
    """Lightweight schema for summary views"""

    class Meta:
        model = School
        fields = ["id", "school_name"]
