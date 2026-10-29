from .auth_view_set import AuthViewSet, LogoutView
from api.institutes.views import InstituteViewSet
from api.programs.views import ProgramViewSet
from api.schools.views import SchoolViewSet, SchoolBuildingViewSet
from api.users.views import UserViewSet, BulkUserInviteView
from api.students.views import StudentViewSet
from api.views.cloudinary_view import CloudinaryView

# Clearance
from api.clearance.views import (
    ClearanceTypeViewSet,
    ClearanceRequirementViewSet,
    StudentClearanceViewSet,
    StudentClearanceItemViewSet,
    ClearanceRoleViewSet,
)

from api.notifications.views import NotificationViewSet
from api.notifications.notification_broadcast_viewset import NotificationBroadcastViewSet

from api.system_settings.views import SystemSettingViewSet
from api.audits.views import AuditLogViewSet
from api.health.views import LivenessView, ReadinessView, HealthDetailView

__all__ = [
    "AuthViewSet",
    "LogoutView",
    "InstituteViewSet",
    "ProgramViewSet",
    "SchoolViewSet",
    "SchoolBuildingViewSet",
    "UserViewSet",
    "BulkUserInviteView",
    "StudentViewSet",
    "CloudinaryView",
    # Clearance
    "ClearanceTypeViewSet",
    "ClearanceRequirementViewSet",
    "StudentClearanceViewSet",
    "StudentClearanceItemViewSet",
    "ClearanceRoleViewSet",
    "NotificationViewSet",
    "NotificationBroadcastViewSet",
    "SystemSettingViewSet",
    "AuditLogViewSet",
    "LivenessView",
    "ReadinessView",
    "HealthDetailView",
]
