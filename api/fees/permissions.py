from rest_framework.permissions import BasePermission


class IsWithinInstituteScope(BasePermission):
    """
    Object-level tenancy guard for fees.

    `FeeViewSet.get_queryset()` already scopes list/retrieve to the caller's
    institute, but a custom `@action(detail=True)` route that builds its own
    lookup can slip past it. This runs on every object access, so the guard
    holds regardless of how the object was fetched.

    Rules:
      * superusers see everything;
      * institute staff see fees belonging to students in their institute;
      * a student sees only their own fees.

    TODO (carried over from the original note on this module): staff access
    should eventually also narrow by collection category, driven by the
    systems assigned to the user (user -> user_systems -> systems), so that
    e.g. locker staff can only touch Locker fees. That needs the user_systems
    app wired back up first - until then, institute scoping is the only
    boundary enforced here.
    """

    message = "This fee is outside your institute scope."

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True

        student = getattr(obj, "student", None)
        if student is None:
            return False

        # Reverse OneToOne: Django raises a RelatedObjectDoesNotExist that
        # subclasses AttributeError, so getattr's default kicks in for users
        # with no linked student record.
        own_student = getattr(user, "student", None)
        if own_student is not None and own_student.pk == student.pk:
            return True

        institute_id = getattr(user, "institute_id", None)
        return bool(institute_id) and student.program.institute_id == institute_id
