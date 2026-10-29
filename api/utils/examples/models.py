from django.db import models
from core.BaseModel import BaseModel

class Location(models.Model):
    city = models.CharField(max_length=100)
    country = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.city}, {self.country}"


class School(BaseModel):
    school_name = models.CharField(max_length=100, unique=True)
    logo = models.TextField(max_length=1200)
    location = models.TextField(max_length=1200)

    def __str__(self):
        return self.school_name
    
    "if gusto mag add og custom permisions :>"
    # class Meta:
    #     permissions = [
    #         ("approve_school", "Can approve school"),
    #         ("archive_school", "Can archive school"),
    #     ]

