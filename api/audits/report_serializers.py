"""
The wire contract for the audit-log report.

Documentation only, in the same spirit as the reports app: the response is
assembled as a plain dict by `report.build_audit_log_report`, and these
classes exist so the OpenAPI schema describes the payload instead of
shrugging at it.
"""

from rest_framework import serializers


class AuditReportAppliedFiltersSerializer(serializers.Serializer):
    """Every filter echoed back as a display string, never a code."""

    period = serializers.CharField(help_text='"All time" or a date range.')
    systems = serializers.CharField()
    actions = serializers.CharField()
    actor_types = serializers.CharField()
    actor = serializers.CharField(help_text="The name search / actor id, or All actors.")
    entity = serializers.CharField()


class AuditReportSummarySerializer(serializers.Serializer):
    total_entries = serializers.IntegerField(
        help_text="Everything the filters matched - never affected by the record cap."
    )
    first_entry_at = serializers.DateTimeField(allow_null=True)
    last_entry_at = serializers.DateTimeField(allow_null=True)
    records_returned = serializers.IntegerField(
        help_text="How many rows `records` actually carries."
    )
    records_truncated = serializers.BooleanField(
        help_text=(
            "True when the filtered set is larger than the report's record "
            "cap. The aggregates still cover everything; narrow the date "
            "range to list the rest."
        )
    )


class AuditReportChoiceCountSerializer(serializers.Serializer):
    """One choice of an enum with its entry count; zero-filled, so every choice appears."""

    value = serializers.CharField()
    label = serializers.CharField()
    count = serializers.IntegerField()


class AuditReportEntityTypeCountSerializer(serializers.Serializer):
    entity_type = serializers.CharField()
    count = serializers.IntegerField()


class AuditReportDailyCountSerializer(serializers.Serializer):
    date = serializers.DateField()
    count = serializers.IntegerField()


class AuditReportTopActorSerializer(serializers.Serializer):
    actor_id = serializers.IntegerField()
    actor_type = serializers.CharField()
    actor_username = serializers.CharField(
        allow_null=True, help_text="Null when the user has since been deleted."
    )
    actor_full_name = serializers.CharField(allow_null=True)
    entry_count = serializers.IntegerField()


class AuditReportRecordSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    created_at = serializers.DateTimeField()
    created_at_display = serializers.CharField(
        help_text='Local time preformatted for print, e.g. "2026-08-22 14:03".'
    )
    system = serializers.CharField()
    entity_type = serializers.CharField()
    entity_id = serializers.CharField()
    action = serializers.CharField()
    actor_type = serializers.CharField()
    actor_id = serializers.IntegerField(allow_null=True)
    actor_username = serializers.CharField(allow_null=True)
    actor_full_name = serializers.CharField(allow_null=True)
    old_values = serializers.JSONField(allow_null=True)
    new_values = serializers.JSONField(allow_null=True)


class AuditLogReportSerializer(serializers.Serializer):
    """The whole snapshot. Documentation only - see the module docstring."""

    generated_at = serializers.DateTimeField()
    applied_filters = AuditReportAppliedFiltersSerializer()
    summary = AuditReportSummarySerializer()
    by_action = AuditReportChoiceCountSerializer(many=True)
    by_system = AuditReportChoiceCountSerializer(many=True)
    by_actor_type = AuditReportChoiceCountSerializer(many=True)
    by_entity_type = AuditReportEntityTypeCountSerializer(many=True)
    daily = AuditReportDailyCountSerializer(many=True)
    top_actors = AuditReportTopActorSerializer(many=True)
    records = AuditReportRecordSerializer(
        many=True, help_text="Newest first, capped - see summary.records_truncated."
    )
