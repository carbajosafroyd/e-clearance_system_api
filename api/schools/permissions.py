from rest_framework.permissions import BasePermission

from api.rbac.scoping import school_id_of


class IsSchoolStaff(BasePermission):
    """
    Object-level ownership for schools: your own, however you are attached
    to it. `get_queryset` already narrows the list; this stops a detail
    route reaching past it. Superusers pass.
    """

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.is_superuser:
            return True
        return obj.id == school_id_of(user)