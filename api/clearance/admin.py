from django.contrib import admin
from .models import (
    ClearanceType,
    ClearanceRequirement,
    StudentClearance,
    StudentClearanceItem,
)

admin.site.register(ClearanceType)
admin.site.register(ClearanceRequirement)
admin.site.register(StudentClearance)
admin.site.register(StudentClearanceItem)