from django.db import models
from core.BaseModel import ActorStampedModel


class AttendanceEvent(ActorStampedModel):

    # school = models.ForeignKey("schools.School", on_delete=models.CASCADE, related_name="school")
    event_name = models.CharField(max_length=50, db_index=True)
    description = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "attendance_events"

    def __str__(self):
        return f"{self.event_name} ({self.get_event_status_display()})"
