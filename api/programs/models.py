from django.db import models
from core.BaseModel import TimeStampedModel


class Program(TimeStampedModel):
    class ProgramStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    name = models.CharField(max_length=100)
    acronym = models.CharField(max_length=10)
    status = models.CharField(
        max_length=20, choices=ProgramStatus.choices, default=ProgramStatus.ACTIVE
    )
    institute = models.ForeignKey(  
        "institutes.Institute", on_delete=models.CASCADE, related_name="programs"
    )
    
    class Meta:
        db_table = "programs"
        indexes = [
            models.Index(fields=["institute", "status"]),
            models.Index(fields=["name"]),
        ]
    def __str__(self):
        return self.name