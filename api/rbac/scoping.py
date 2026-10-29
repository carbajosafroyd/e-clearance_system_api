"""
Row-level tenancy, written once.

Every viewset that narrows its rows to "what this caller may see" used to
carry its own `if user.is_staff: filter(institute=user.institute)`. Nine
copies drifted: `ProgramViewSet` lost its superuser branch, and every one of
them sent a school administrator - whose account hangs off `user.school`
and may have no institute at all - into `institute=None`, which matches
nothing (or, for accounts, matches the superusers). The rule is here now,
and a viewset asks for it by naming the paths to the institute and the
school on its model.

Three kinds of caller, in order:

    superuser            everything
    School Admins        their whole school - every institute in it, plus
                         (for accounts) the school-level accounts themselves
    anyone else          their own institute; with no institute, nothing,
                         which is the right fail-closed answer

`is_staff` is deliberately NOT consulted here. It is bookkeeping several
older code paths still read, and it has been wrong on real accounts - a
School Admin created through the Django admin or an old invitation had it
off, and every list came back empty for them while their roles said they
should see a whole school. What decides scope is the role (`is_school_wide`)
and where the account is attached; `is_student_only` is how a viewset that
also serves students tells the two apart.
"""

from __future__ import annotations

from django.db.models import Q

from .roles import Roles


def is_school_wide(user) -> bool:
    """Does this account run a whole school rather than one institute?"""
    if not user or not user.is_authenticated:
        return False
    return user.groups.filter(name=Roles.SCHOOL_ADMIN).exists()


def is_student_only(user) -> bool:
    """
    A student account and nothing more: sees its own records, never a scope.

    A staff member can also be a student (an officer finishing a degree);
    their roles win, so the check is "has a student row and no staff
    standing" rather than "has a student row".
    """
    if not user or not user.is_authenticated:
        return False
    if not hasattr(user, "student"):
        return False
    return not (user.is_superuser or user.is_staff or is_school_wide(user))


def school_id_of(user) -> int | None:
    """
    The school an account belongs to, however it is attached.

    `User.school` is set on accounts attached directly to a school (the
    school administrators an invitation places there); everybody else
    reaches it through their institute.
    """
    direct = getattr(user, "school_id", None)
    if direct is not None:
        return direct
    institute = getattr(user, "institute", None)
    return getattr(institute, "school_id", None)


def scope_queryset(queryset, user, *, institute_path: str, school_paths: tuple[str, ...]):
    """
    Narrow `queryset` to what `user` may see.

        return scope_queryset(
            self.queryset, self.request.user,
            institute_path="program__institute",
            school_paths=("program__institute__school",),
        )

    `school_paths` is a tuple because an account can belong to a school two
    ways - through its institute or directly - and the users list has to
    match both (`("institute__school", "school")`).
    """
    if user.is_superuser:
        return queryset

    if is_school_wide(user):
        school_id = school_id_of(user)
        if school_id is None:
            return queryset.none()
        condition = Q()
        for path in school_paths:
            condition |= Q(**{path: school_id})
        return queryset.filter(condition)

    institute_id = getattr(user, "institute_id", None)
    if institute_id is None:
        return queryset.none()

    return queryset.filter(**{institute_path: institute_id})


def institute_in_scope(user, institute) -> bool:
    """May `user` place or edit a record in `institute`?"""
    if institute is None:
        return False
    if user.is_superuser:
        return True
    if is_school_wide(user):
        return institute.school_id == school_id_of(user)
    return institute.pk == getattr(user, "institute_id", None)


def account_in_scope(actor, target) -> bool:
    """
    May `actor` administer `target`'s account - reset its password, change
    its roles, move its email?

    The same answer `scope_queryset` gives for the users list, for the
    routes that fetch the account themselves. An institute administrator
    reaches accounts of their own institute and nothing else; a school
    administrator every account in their school, including its other
    school-level accounts.
    """
    if actor.is_superuser:
        return True
    if is_school_wide(actor):
        own = school_id_of(actor)
        return own is not None and school_id_of(target) == own
    institute_id = getattr(actor, "institute_id", None)
    return institute_id is not None and getattr(target, "institute_id", None) == institute_id
