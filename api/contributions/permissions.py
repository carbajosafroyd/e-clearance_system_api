"""
Object-level guards for contributions and discounts.

*Which* actions a caller may reach is decided by `required_permissions` on
the viewsets (see `api/rbac/permissions.py`). These answer the questions a
permission cannot: is this row yours, is it in your institute, and is it
still yours to edit?
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission


def _student_of(user):
    # Reverse OneToOne: Django raises a RelatedObjectDoesNotExist that
    # subclasses AttributeError, so getattr's default covers staff accounts.
    return getattr(user, "student", None)


class IsOwnStudentRecordOrInstituteStaff(BasePermission):
    """A student sees their own rows; staff see their institute's.

    Writes are narrower still: a student may only edit a record that has not
    been reviewed yet - once a reviewer has ruled on it, the text behind the
    decision must stop moving.
    """

    message = "This record belongs to another student."

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True

        student = _student_of(user)
        if student is not None and obj.student_id == student.pk:
            if request.method in SAFE_METHODS:
                return True
            return self._student_may_write(obj)

        institute_id = getattr(user, "institute_id", None)
        if not institute_id:
            return False
        return obj.student.program.institute_id == institute_id

    @staticmethod
    def _student_may_write(obj) -> bool:
        terminal = getattr(obj, "TERMINAL_STATUSES", None)
        if terminal is not None:
            return obj.status not in terminal
        # FeeDiscount: only a request nobody has ruled on yet.
        return obj.status == "pending"


class IsWithinInstituteScope(BasePermission):
    """Plain tenancy check for rows that carry an institute directly."""

    message = "This record is outside your institute."

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        return obj.institute_id == getattr(user, "institute_id", None)
