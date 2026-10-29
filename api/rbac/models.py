"""
Permission holders for endpoints that are not backed by a model.

Django creates permissions per *model*, so a viewset that reads across
several tables (the dashboards, the collection report, the audit-log
exports) has no natural place to hang a codename. These two models exist
only to own those codenames.

`managed = False` means no table is ever created; `default_permissions = ()`
suppresses the add/change/delete/view set that would be meaningless here.
The permission rows themselves are still created by Django's `post_migrate`
hook, exactly like any other model's, so they are grantable from the admin
and from `seed_rbac`.

Adding a permission
-------------------
Append to the `permissions` list below, run

    python manage.py makemigrations rbac && python manage.py migrate

and reference it from `api/rbac/roles.py` so a role actually receives it.
"""

from django.db import models


class ReportAccess(models.Model):
    """Reporting, dashboards and summary endpoints."""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = [
            ("view_admin_dashboard", "Can view the staff/admin dashboard"),
            ("view_student_dashboard", "Can view the student dashboard"),
            ("view_collection_summary", "Can view collection summary widgets"),
            ("view_collection_report", "Can view the collection report"),
            ("view_detailed_report", "Can view detailed fee/payment reports"),
            ("view_audit_report", "Can view the audit-log report"),
            ("export_report", "Can export reports to PDF/Excel"),
            ("view_contribution_report", "Can view contribution/points reports"),
        ]
        verbose_name = "Report access"
        verbose_name_plural = "Report access"


class PlatformAccess(models.Model):
    """Cross-cutting platform administration."""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = [
            ("view_role_catalog", "Can read the role/permission catalog"),
            ("manage_roles", "Can assign roles and permissions to users"),
            ("use_transfer_token", "Can issue single-sign-on transfer tokens"),
            ("upload_media", "Can upload files to the media store"),
        ]
        verbose_name = "Platform access"
        verbose_name_plural = "Platform access"
