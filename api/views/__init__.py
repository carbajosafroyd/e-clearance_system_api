from .auth_view_set import AuthViewSet, LogoutView
from api.institutes.views import InstituteViewSet
from api.programs.views import ProgramViewSet
from api.schools.views import SchoolViewSet, SchoolBuildingViewSet
from api.users.views import UserViewSet

# from api.systems.views import SystemViewSet
# from api.user_systems.views import UserSystemViewSet
from api.students.views import StudentViewSet
from api.collection_categories.views import CollectionCategoryViewSet, FeeExcemptedStudentViewSet, IntituteGcashAccountViewSet
from api.fees.views import FeeViewSet, GeneratedFeeBatchViewSet
from api.attendance_events.views import AttendanceEventViewSet
from api.attendance_records.views import AttendanceRecordViewSet
from api.institutes_attendance_events.views import InstituteAttendanceEventViewSet
from api.institutes_attendance_event_settings.views import InsituteEventSettingViewSet
from api.payments.views import PaymentViewSet
from api.payment_submissions.views import PaymentSubmissionViewSet
from api.views.cloudinary_view import CloudinaryView
from api.locker_system.views.lockers_view_sets import LockerViewSet
from api.locker_system.views.locker_reports_viewsets import LockerReportsViewSet
from api.locker_system.views.locekrs_subscription_request_viewsets import LockerSubscriptionRequestViewSet
from api.locker_system.views.locker_subscription_viewsets import LockerSubscriptionViewSet
from api.locker_system.views.locker_summary_viewset import LockerSummaryViewSet
from api.users.views import BulkUserInviteView
from api.summaries.views.summaries_view import SummaryViewSets
from api.posts.views.posts_view import PostViewSet
from api.posts.views.comments_view import PostCommentViewSet

from api.transfer_token.views import IssueTransferTokenView, RedeemTransferTokenView
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
    "InstituteViewSet",
    "ProgramViewSet",
    "SchoolViewSet",
    "UserViewSet",
    # "SystemViewSet",
    # "UserSystemViewSet",
    "StudentViewSet",
    "CollectionCategoryViewSet",
    "FeeViewSet",
    "AttendanceEventViewSet",
    "AttendanceRecordViewSet",
    "InstituteAttendanceEventViewSet",
    "InsituteEventSettingViewSet",
    "PaymentViewSet",
    "PaymentSubmissionViewSet",
    "CloudinaryView",
    "LockerViewSet",
    "LockerReportsViewSet",
    "LockerSubscriptionRequestViewSet",
    "LockerSubscriptionViewSet",
    "LockerSummaryViewSet",
    "SchoolBuildingViewSet",
    "BulkUserInviteView",
    "GeneratedFeeBatchViewSet",
    "FeeExcemptedStudentViewSet",
    "IntituteGcashAccountViewSet",
    "LogoutView",
    "SummaryViewSets",
    "PostViewSet",
    "PostCommentViewSet",
    
    # Clearance
    "ClearanceTypeViewSet",
    "ClearanceRequirementViewSet",
    "StudentClearanceViewSet",
    "StudentClearanceItemViewSet",
    "ClearanceRoleViewSet",
    
    "IssueTransferTokenView",
    "RedeemTransferTokenView",

    "NotificationViewSet",
    "NotificationBroadcastViewSet",
    "SystemSettingViewSet",

    "AuditLogViewSet",

    "LivenessView",
    "ReadinessView",
    "HealthDetailView",
]
