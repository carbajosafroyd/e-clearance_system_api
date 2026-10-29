"""
Object-level guards for payment submissions.

*Which* actions a caller may reach is decided by `required_permissions` on
`PaymentSubmissionViewSet` (see `api/rbac/permissions.py`). What is left here
is the tenancy question those permissions cannot answer: reviewing a
submission is not enough, it has to be a submission from your own institute.
"""

from rest_framework.permissions import BasePermission


class CanEditPaymentSubmissionStatus(BasePermission):
    """Staff may only act on submissions from students in their institute."""

    message = "This submission belongs to another institute."

    def has_object_permission(self, request, view, obj):
        user = request.user

        if getattr(user, "is_superuser", False):
            return True

        if not getattr(user, "is_staff", False):
            return False

        return obj.student.program.institute_id == getattr(user, "institute_id", None)
