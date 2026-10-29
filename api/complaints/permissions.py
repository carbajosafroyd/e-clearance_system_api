"""
Object-level guards for complaints.

`required_permissions` on the viewset decides *which* actions a caller may
reach; this answers the question a permission cannot - is this row yours, or
in your institute, and is it still yours to edit?
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission


def _student_of(user):
    # Reverse OneToOne: Django raises a RelatedObjectDoesNotExist that
    # subclasses AttributeError, so getattr's default covers staff accounts.
    return getattr(user, "student", None)


class IsOwnComplaintOrInstituteStaff(BasePermission):
    """A student sees their own complaints; staff see their institute's.

    A student may keep editing while the complaint is open, and not after:
    once an officer has answered, the text the answer responds to has to stop
    moving.
    """

    message = "This complaint belongs to another student."

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True

        student = _student_of(user)
        if student is not None and obj.student_id == student.pk:
            return request.method in SAFE_METHODS or obj.is_open

        institute_id = getattr(user, "institute_id", None)
        if not institute_id:
            return False
        return obj.student.program.institute_id == institute_id
