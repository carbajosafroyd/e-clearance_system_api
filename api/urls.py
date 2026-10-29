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
from api.views import (
    auth_view_set, SchoolViewSet, ProgramViewSet, InstituteViewSet, StudentViewSet, UserViewSet,
    SchoolBuildingViewSet, BulkUserInviteView, LogoutView, CloudinaryView,
    ClearanceTypeViewSet, ClearanceRequirementViewSet, StudentClearanceViewSet,
    StudentClearanceItemViewSet, ClearanceRoleViewSet,
    NotificationViewSet, NotificationBroadcastViewSet, SystemSettingViewSet, AuditLogViewSet
)
from api.views import google_auth_view
from api.views.auth_view_set import CustomTokenObtainPairView

from api.audits.report_views import (
    AuditLogReportExcelView,
    AuditLogReportPdfView,
    AuditLogReportView,
)
from api.rbac.views import AccessViewSet

router = DefaultRouter()
router.register("me", auth_view_set.AuthViewSet, basename="me")
router.register("schools", SchoolViewSet, basename="schools")
router.register("school-buildings", SchoolBuildingViewSet, basename="school-buildings")
router.register("institutes", InstituteViewSet, basename="institutes")
router.register("programs", ProgramViewSet, basename="programs")
router.register("users", UserViewSet, basename="users")
router.register("students", StudentViewSet, basename="students")

# E-Clearance core endpoints
router.register("clearance-types", ClearanceTypeViewSet, basename="clearance-types")
router.register("clearance-requirements", ClearanceRequirementViewSet, basename="clearance-requirements")
router.register("student-clearances", StudentClearanceViewSet, basename="student-clearances")
router.register("student-clearance-items", StudentClearanceItemViewSet, basename="student-clearance-items")
router.register("clearance-roles", ClearanceRoleViewSet, basename="clearance-roles")

# Notifications
router.register("notifications", NotificationViewSet, basename="notifications")
router.register("notifications-broadcast", NotificationBroadcastViewSet, basename="notifications-broadcast")

# System Settings
router.register("system-settings", SystemSettingViewSet, basename="system-settings")

# Roles & Permissions scoping
router.register("access", AccessViewSet, basename="access")

# Audit logs
router.register("audit-logs", AuditLogViewSet, basename="audit-logs")

urlpatterns = [
    path("email-sender/defaults/", EmailSenderDefaultsView.as_view(), name="email-sender-defaults"),
    path("admin/invitations/", UserInviteView.as_view(), name="user-invite"),
    path("admin/student-bulk-invitations/", BulkUserInviteView.as_view(), name="user-bulk-invite"),
    path("admin/student-bulk-invitations/upload/", StudentInviteFileView.as_view(), name="user-bulk-invite-upload"),
    path("admin/student-bulk-invitations/template/", StudentInviteTemplateView.as_view(), name="user-bulk-invite-template"),
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", CustomTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/google/", google_auth_view.GoogleAuthView.as_view(), name="google-auth"),
    path("auth/google/register", google_auth_view.GoogleAuthRegisterView.as_view(), name="google-auth-register"),
    path("cloudinary/", CloudinaryView.as_view(), name="cloudinary"),
    path("password-reset/request/", RequestPasswordResetView.as_view(), name="password-reset-request"),
    path("password-reset/verify/", VerifyPasswordResetView.as_view(), name="password-reset-verify"),
    path("password-reset/change/", ChangePasswordView.as_view(), name="password-reset-change"),
    # Changing the email on your own account
    path("email-change/request/", RequestEmailChangeView.as_view(), name="email-change-request"),
    path("email-change/verify/", VerifyEmailChangeCodeView.as_view(), name="email-change-verify"),
    path("email-change/set-new/", SetNewEmailView.as_view(), name="email-change-set-new"),
    path("email-change/resend/", ResendEmailChangeConfirmationView.as_view(), name="email-change-resend"),
    path("email-change/status/", EmailChangeStatusView.as_view(), name="email-change-status"),
    path("email-change/confirm/", ConfirmEmailChangeView.as_view(), name="email-change-confirm"),
    # Audit log report exports
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
