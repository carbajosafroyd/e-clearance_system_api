from rest_framework.permissions import BasePermission, SAFE_METHODS

CLEARANCE_ADMIN_GROUPS = {
    "school admins",
    "collection management system admins",
    "clearance management system admins",
    "admin",
}

CLEARANCE_NON_SIGNATORY_GROUPS = {
    "student",
    "students",
}

CLEARANCE_SIGNATORY_GROUPS = {
    "librarian",
    "librarians",
    "dean",
    "deans",
    "cashier",
    "cashiers",
    "adviser",
    "advisers",
    "governor",
    "governors",
    "program chair",
    "program chairs",
    "treasurer",
    "treasurers",
    "assistant treasurer",
    "assistant treasurers",
}


def user_group_names(user) -> set[str]:
    if not user or not user.is_authenticated:
        return set()

    return {
        str(name).strip().lower()
        for name in user.groups.values_list("name", flat=True)
        if str(name).strip()
    }


def user_group_ids(user) -> set[int]:
    if not user or not user.is_authenticated:
        return set()

    return set(user.groups.values_list("id", flat=True))


def get_user_institute_id(user):
    return getattr(user, "institute_id", None)


def get_user_student(user):
    if not user or not user.is_authenticated:
        return None

    try:
        return getattr(user, "student", None)
    except Exception:
        return None


def is_clearance_admin(user) -> bool:
    if not user or not user.is_authenticated:
        return False

    return bool(user_group_names(user) & CLEARANCE_ADMIN_GROUPS)


def is_clearance_signatory(user) -> bool:
    if not user or not user.is_authenticated:
        return False

    names = user_group_names(user)

    if names & CLEARANCE_SIGNATORY_GROUPS:
        return True

    # Dynamic clearance roles created by admins are Django groups too.
    # Treat any non-student, non-admin group as a possible signatory group.
    dynamic_role_names = names - CLEARANCE_ADMIN_GROUPS - CLEARANCE_NON_SIGNATORY_GROUPS
    return bool(dynamic_role_names)


def can_access_clearance_scope(user) -> bool:
    if not user or not user.is_authenticated:
        return False

    if getattr(user, "is_superuser", False):
        return True

    if is_clearance_admin(user):
        return True

    if is_clearance_signatory(user):
        return True

    return False


def object_institute_id(obj):
    if obj is None:
        return None

    if hasattr(obj, "institute_id"):
        return obj.institute_id

    if hasattr(obj, "clearance_type") and getattr(obj, "clearance_type", None):
        return getattr(obj.clearance_type, "institute_id", None)

    if hasattr(obj, "student_clearance") and getattr(obj, "student_clearance", None):
        student_clearance = obj.student_clearance

        if getattr(student_clearance, "clearance_type", None):
            return getattr(student_clearance.clearance_type, "institute_id", None)

        student = getattr(student_clearance, "student", None)
        if student and getattr(student, "program", None):
            return getattr(student.program, "institute_id", None)

    if hasattr(obj, "student") and getattr(obj, "student", None):
        student = obj.student
        if getattr(student, "program", None):
            return getattr(student.program, "institute_id", None)

    return None


def object_student_id(obj):
    if obj is None:
        return None

    if hasattr(obj, "student_id"):
        return getattr(obj, "student_id", None)

    if hasattr(obj, "student") and getattr(obj, "student", None):
        return getattr(obj.student, "id", None)

    if hasattr(obj, "student_clearance") and getattr(obj, "student_clearance", None):
        student_clearance = obj.student_clearance

        if hasattr(student_clearance, "student_id"):
            return getattr(student_clearance, "student_id", None)

        student = getattr(student_clearance, "student", None)
        if student:
            return getattr(student, "id", None)

    return None


def is_own_student_clearance_object(user, obj) -> bool:
    student = get_user_student(user)

    if not student:
        return False

    return object_student_id(obj) == getattr(student, "id", None)


def requirement_authorizing_role_ids(requirement) -> set[int]:
    if requirement is None:
        return set()

    role_ids = set()

    prefetched = getattr(requirement, "_prefetched_objects_cache", {})
    if "authorizing_roles" in prefetched:
        role_ids.update(role.id for role in prefetched["authorizing_roles"] if role and role.id)
    elif getattr(requirement, "pk", None):
        role_ids.update(requirement.authorizing_roles.values_list("id", flat=True))

    legacy_role_id = getattr(requirement, "authorizing_role_id", None)
    if legacy_role_id:
        role_ids.add(legacy_role_id)

    return role_ids


def requirement_allows_user(requirement, user) -> bool:
    return bool(requirement_authorizing_role_ids(requirement) & user_group_ids(user))


class IsClearanceAdminOrReadOnly(BasePermission):
    message = "Only clearance admin groups can manage clearance data."

    def has_permission(self, request, view):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        if request.method in SAFE_METHODS:
            return True

        return (
            is_clearance_admin(user)
            or getattr(user, "is_superuser", False)
        )

    def has_object_permission(self, request, view, obj):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        if getattr(user, "is_superuser", False):
            return True

        obj_institute = object_institute_id(obj)
        user_institute = get_user_institute_id(user)

        if request.method in SAFE_METHODS:
            if get_user_student(user):
                return is_own_student_clearance_object(user, obj)

            if can_access_clearance_scope(user):
                if obj_institute is None:
                    return True
                return obj_institute == user_institute

            return False

        if not is_clearance_admin(user):
            return False

        if obj_institute is None:
            return True

        return obj_institute == user_institute


class CanAccessInstituteClearanceData(BasePermission):
    message = "You do not have permission to access this institute's clearance data."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        if getattr(user, "is_superuser", False):
            return True

        if get_user_student(user):
            if request.method in SAFE_METHODS:
                return is_own_student_clearance_object(user, obj)

            return False

        obj_institute_id = object_institute_id(obj)

        if can_access_clearance_scope(user):
            user_institute_id = get_user_institute_id(user)

            if obj_institute_id is None:
                return False

            return obj_institute_id == user_institute_id

        return False


class CanManageStudentClearanceItem(BasePermission):
    message = "You do not have permission to manage this clearance item."

    def has_permission(self, request, view):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        if request.method in SAFE_METHODS:
            return True

        return (
            getattr(user, "is_superuser", False)
            or is_clearance_admin(user)
            or is_clearance_signatory(user)
        )

    def has_object_permission(self, request, view, obj):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        if getattr(user, "is_superuser", False):
            return True

        if get_user_student(user):
            if request.method in SAFE_METHODS:
                return is_own_student_clearance_object(user, obj)

            return False

        obj_institute = object_institute_id(obj)
        user_institute = get_user_institute_id(user)

        if obj_institute is not None and obj_institute != user_institute:
            return False

        if request.method in SAFE_METHODS:
            if is_clearance_admin(user):
                return True
            return requirement_allows_user(getattr(obj, "requirement", None), user)

        if is_clearance_admin(user):
            return True

        if not is_clearance_signatory(user):
            return False

        return requirement_allows_user(getattr(obj, "requirement", None), user)
