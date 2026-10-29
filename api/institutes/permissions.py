"""
Institute-level guards.

Route authorisation lives in `required_permissions` on the viewsets; this
module is for tenancy checks only. `api.rbac.InstituteScoped` covers the
common case - use it before adding anything here.
"""

from rest_framework.permissions import BasePermission


class BelongsToInstitute(BasePermission):
    """The object must belong to the caller's institute."""

    message = "You do not belong to this institute."

    def has_object_permission(self, request, view, obj):
        user = request.user

        if getattr(user, "is_superuser", False):
            return True

        institute_id = getattr(user, "institute_id", None)
        return bool(institute_id) and getattr(obj, "id", None) == institute_id
