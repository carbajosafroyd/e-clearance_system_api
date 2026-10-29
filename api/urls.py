from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from api.schools.views import EmailSenderDefaultsView
from api.change_password.views import ChangePasswordView, RequestPasswordResetView, VerifyPasswordResetView
from api.change_email.views import (
    ConfirmEmailChangeView,
    EmailChangeStatusView,
    RequestEmailChangeView,
    ResendEmailChangeConfirmationView,
    SetNewEmailView,
    VerifyEmailChangeCodeView,
)
from api.users.views import RegisterView, StudentInviteFileView, StudentInviteTemplateView, UserInviteView
from api.views import (    # SystemViewSet,# UserSystemViewSet,
    auth_view_set, SchoolViewSet, ProgramViewSet, InstituteViewSet, StudentViewSet, UserViewSet,
    StudentViewSet, CollectionCategoryViewSet, FeeViewSet, AttendanceEventViewSet, AttendanceRecordViewSet, InstituteAttendanceEventViewSet, InsituteEventSettingViewSet, google_auth_view, PaymentViewSet, PaymentSubmissionViewSet, CloudinaryView, LockerViewSet, LockerReportsViewSet, LockerSubscriptionRequestViewSet, LockerSubscriptionViewSet, LockerSummaryViewSet, SchoolBuildingViewSet, BulkUserInviteView, GeneratedFeeBatchViewSet, FeeExcemptedStudentViewSet, IntituteGcashAccountViewSet, LogoutView, SummaryViewSets, PostViewSet, PostCommentViewSet, RedeemTransferTokenView, IssueTransferTokenView, NotificationViewSet, NotificationBroadcastViewSet,SystemSettingViewSet, AuditLogViewSet
)

from api.views.auth_view_set import CustomTokenObtainPairView

from api.audits.report_views import (
    AuditLogReportExcelView,
    AuditLogReportPdfView,
    AuditLogReportView,
)

from api.complaints.views import ComplaintViewSet
from api.contributions.views import (
    ContributionViewSet,
    FeeDiscountViewSet,
    PointAccountViewSet,
    PointPolicyViewSet,
)
from api.rbac.views import AccessViewSet
from core import features

# Clearance
from api.clearance.views import (
    ClearanceTypeViewSet,
    ClearanceRequirementViewSet,
    StudentClearanceViewSet,
    StudentClearanceItemViewSet,
    ClearanceRoleViewSet,
)

router = DefaultRouter()
router.register(f"me", auth_view_set.AuthViewSet, basename="me")
router.register(f"schools", SchoolViewSet, basename="schools")
router.register(f"school-buildings", SchoolBuildingViewSet, basename="school-buildings")
router.register(f"institutes", InstituteViewSet, basename="institutes")
router.register(f"programs", ProgramViewSet, basename="programs")
router.register(f"users", UserViewSet, basename="users")
router.register(f"students", StudentViewSet, basename="students")

# Sibling systems deactivated for dedicated E-Clearance backend
# router.register(f"collection-categories", CollectionCategoryViewSet, basename="collcetion-categories")
# router.register(f"fees", FeeViewSet, basename="fees")
# router.register(f"attendance-events", AttendanceEventViewSet, basename="attendance-events")
# router.register(f"institute-attendance-event", InstituteAttendanceEventViewSet, basename="institute-attendance-event")
# router.register(f"institute-attendance-event-settings", InsituteEventSettingViewSet, basename="institute-attendance-event-settings")
# router.register(f"attendance-records", AttendanceRecordViewSet, basename="attendance-records")
# router.register(f"payments", PaymentViewSet, basename="payments")
# router.register(f"payment-submissions", PaymentSubmissionViewSet, basename="payment-submissins")
# router.register(f"lockers", LockerViewSet, basename="lockers")
# router.register(f"locker-summary", LockerSummaryViewSet, basename="locker-summary")
# router.register(f"locker-reports", LockerReportsViewSet, basename="locker-reports")
# router.register(f"locker-subscription-requests", LockerSubscriptionRequestViewSet, basename="locker-subcription-requests")
# router.register(f"locker-subscriptions", LockerSubscriptionViewSet, basename="locker-subcriptions")
# router.register(f"generate-fees", GeneratedFeeBatchViewSet, basename="generate-fees")
# router.register(f"exempted-fee-students", FeeExcemptedStudentViewSet, basename="exempted-fee-students")
# router.register(f"institute-gcash-accounts", IntituteGcashAccountViewSet, basename="exeinstitute-gcash-accounts")
# router.register(f"summaries", SummaryViewSets, basename="summaries")
# router.register(f"posts", PostViewSet, basename="posts")
# router.register(f"posts_comments", PostCommentViewSet, basename="posts_comments")

# Clearance
router.register("clearance-types", ClearanceTypeViewSet, basename="clearance-types")
router.register("clearance-requirements", ClearanceRequirementViewSet, basename="clearance-requirements")
router.register("student-clearances", StudentClearanceViewSet, basename="student-clearances")
router.register("student-clearance-items", StudentClearanceItemViewSet, basename="student-clearance-items")
router.register("clearance-roles", ClearanceRoleViewSet, basename="clearance-roles")

router.register("notifications", NotificationViewSet, basename="notifications")
router.register("notifications-broadcast", NotificationBroadcastViewSet, basename="notifications-broadcast")

router.register("system-settings", SystemSettingViewSet, basename="system-settings")

# Roles and permissions. `access/me/` is what the frontend reads to decide
# which routes, nav entries and buttons to render.
router.register("access", AccessViewSet, basename="access")

router.register("audit-logs", AuditLogViewSet, basename="audit-logs")
urlpatterns = [
    # Compat bridge retired in favor of native REST endpoints
    # path("", include("api.clearance.compat_urls")),
    path("email-sender/defaults/", EmailSenderDefaultsView.as_view(), name="email-sender-defaults"),
    path("admin/invitations/", UserInviteView.as_view(), name="user-invite"),
    path("admin/student-bulk-invitations/", BulkUserInviteView.as_view(), name="user-bulk-invite"),
    path("admin/student-bulk-invitations/upload/", StudentInviteFileView.as_view(), name="user-bulk-invite-upload"),
    path("admin/student-bulk-invitations/template/", StudentInviteTemplateView.as_view(), name="user-bulk-invite-template"),
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", CustomTokenObtainPairView.as_view(), name="token_obtain_pair"),      
    path("logout/", LogoutView.as_view(), name="token_obtain_pair"),      
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/google/", google_auth_view.GoogleAuthView.as_view(), name="google-auth"),
    path("auth/google/register", google_auth_view.GoogleAuthRegisterView.as_view(), name="google-auth-register"),
    path("transfer_token/issue/", IssueTransferTokenView.as_view(), name="issue"),
    path("transfer_token/redeem/", RedeemTransferTokenView.as_view(), name="redeem"),
    path("cloudinary/", CloudinaryView.as_view(), name="cloudinary"),
    path("password-reset/request/", RequestPasswordResetView.as_view(), name="password-reset-request"),
    path("password-reset/verify/", VerifyPasswordResetView.as_view(), name="password-reset-verify"),
    path("password-reset/change/", ChangePasswordView.as_view(), name="password-reset-change"),
    # Changing the email on your own account. Steps 1-3 are authenticated;
    # `confirm/` is opened from the new inbox, so it cannot be.
    path("email-change/request/", RequestEmailChangeView.as_view(), name="email-change-request"),
    path("email-change/verify/", VerifyEmailChangeCodeView.as_view(), name="email-change-verify"),
    path("email-change/set-new/", SetNewEmailView.as_view(), name="email-change-set-new"),
    path("email-change/resend/", ResendEmailChangeConfirmationView.as_view(), name="email-change-resend"),
    path("email-change/status/", EmailChangeStatusView.as_view(), name="email-change-status"),
    path("email-change/confirm/", ConfirmEmailChangeView.as_view(), name="email-change-confirm"),
    # Before the router: these sit under prefixes the router owns
    # (`summaries/`, `audit-logs/`), and first match wins - otherwise
    # `audit-logs/report/` would be swallowed by the router's detail route
    # as pk="report".
    path("", include("api.reports.urls")),
    path(
        "audit-logs/report/",
        AuditLogReportView.as_view(),
        name="audit-logs-report",
    ),
    path(
        "audit-logs/report/pdf/",
        AuditLogReportPdfView.as_view(),
        name="audit-logs-report-pdf",
    ),
    path(
        "audit-logs/report/excel/",
        AuditLogReportExcelView.as_view(),
        name="audit-logs-report-excel",
    ),
    path("", include(router.urls)),
]
 