"""
Declarative, per-action permission checks for DRF views.

The problem with `DjangoModelPermissions` is that it only understands the
four CRUD verbs. Half of this API's surface is `@action` routes - waive,
unwaive, approve, reject, cancel, generate, import, export - and every one
of them lands on the model's `change_*` permission, so "can edit a fee" and
"can forgive somebody's debt" become the same grant.

`ActionPermission` fixes that by letting a view declare, in one readable
table, which permission each action needs::

    class FeeViewSet(viewsets.ModelViewSet):
        permission_classes = [IsAuthenticated, ActionPermission]
        required_permissions = {
            "list":     "fees.view_fee",
            "retrieve": "fees.view_fee",
            "create":   "fees.add_fee",
            "update":   "fees.change_fee",
            "destroy":  "fees.delete_fee",
            "waive":    "fees.waive_fee",
            "unwaive":  "fees.unwaive_fee",
            DEFAULT:    "fees.view_fee",
        }

Adding an action is one line in that dict; adding the permission it names is
one line in the model's ``Meta.permissions`` plus a migration. Nothing else
in the view changes.

Requirement values
------------------
====================  ======================================================
``"app.codename"``    the caller must hold it
``["a", "b"]``        the caller must hold **all** of them (like
                      ``user.has_perms``)
``AnyOf("a", "b")``   the caller must hold **at least one**
``ALLOW_ANY``         no permission needed beyond the other permission
                      classes on the view (typically `IsAuthenticated`)
``DENY``              always refused - use to switch an inherited route off
``Feature("x", req)`` refused unless the deployment runs feature ``x``; when
                      it does, ``req`` is evaluated as usual (omit ``req``
                      and holding no permission is enough)
====================  ======================================================

Lookup order
------------
1. the exact action name (``self.action`` on a ViewSet, otherwise the
   lower-cased HTTP method, so plain ``APIView``s work too);
2. ``DEFAULT`` (the ``"default"`` key);
3. otherwise **deny**.

The fall-through denies rather than allows: a new action added without a
table entry is closed until somebody decides what it needs, instead of
silently shipping unprotected.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Sequence

from rest_framework.permissions import SAFE_METHODS, BasePermission

logger = logging.getLogger(__name__)

#: Key used for the catch-all entry in a `required_permissions` table.
DEFAULT = "default"


class _Sentinel:
    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        self._name = name

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return self._name


#: No permission required (authentication is still enforced separately).
ALLOW_ANY = _Sentinel("ALLOW_ANY")

#: Always refused.
DENY = _Sentinel("DENY")

#: Actions DRF generates that never carry data and so never need a grant.
_ALWAYS_ALLOWED_ACTIONS = frozenset({"metadata", "options"})


class AnyOf:
    """Holding *one* of these permissions is enough."""

    __slots__ = ("permissions",)

    def __init__(self, *permissions: str) -> None:
        if not permissions:
            raise ValueError("AnyOf() needs at least one permission")
        self.permissions: tuple[str, ...] = permissions

    def __iter__(self):
        return iter(self.permissions)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"AnyOf({', '.join(self.permissions)})"


class Feature:
    """Gate a requirement on a deployment feature flag.

    An institute that does not run the contributions scheme should not merely
    have its buttons hidden - the endpoint should refuse. Wrapping the
    requirement says so at the one place the view already declares access::

        required_permissions = crud(
            "fees", "fee",
            discounts=Feature(features.CONTRIBUTION_POINTS,
                              "fees.view_fee_discount_history"),
        )

    With the flag off the action is refused for everyone, superusers
    included: a feature this deployment does not run is not a permission
    anybody can be granted. With it on, the wrapped requirement decides, and
    a bare ``Feature("x")`` means "the flag is the only requirement".

    Resolved against the **caller's institute**, because the flags are
    per-institute: one institute running the coupon module and another not is
    the normal case, and a single deployment serves both. Whole route groups
    are still switched off in `api/urls.py` when the build does not ship a
    module at all, so those 404 for everybody.
    """

    __slots__ = ("feature", "requirement")

    def __init__(self, feature: str, requirement: Any = ALLOW_ANY) -> None:
        self.feature = feature
        self.requirement = requirement

    def enabled_for(self, user) -> bool:
        from core import features

        return features.is_enabled(
            self.feature, features.resolve_institute(user)
        )

    @property
    def enabled(self) -> bool:
        """The deployment-wide answer, for callers with no user in hand."""
        from core import features

        return features.is_enabled(self.feature)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Feature({self.feature!r}, {self.requirement!r})"


def gate(feature: str, table: dict[str, Any]) -> dict[str, Any]:
    """Wrap every entry of a permission table in the same `Feature`.

    For a viewset that exists only because a feature is enabled::

        required_permissions = gate(
            features.CONTRIBUTION_POINTS, crud("contributions", "feediscount")
        )

    Entries that are already `Feature`-wrapped are left alone, so one action
    can require a narrower flag than the viewset around it.
    """
    return {
        key: value if isinstance(value, Feature) else Feature(feature, value)
        for key, value in table.items()
    }


def user_has(user, requirement: Any) -> bool:
    """Evaluate one requirement value against a user."""
    if isinstance(requirement, Feature):
        # The caller's institute, not the deployment: the same endpoint is on
        # for one institute and off for the next.
        if not requirement.enabled_for(user):
            return False
        return user_has(user, requirement.requirement)
    if requirement is ALLOW_ANY:
        return True
    if requirement is DENY or requirement is None:
        return False
    if isinstance(requirement, AnyOf):
        return any(user.has_perm(perm) for perm in requirement)
    if isinstance(requirement, str):
        return user.has_perm(requirement)
    if isinstance(requirement, (list, tuple, set, frozenset)):
        return all(user.has_perm(perm) for perm in requirement)

    raise TypeError(
        f"Unsupported permission requirement {requirement!r}. Use a string, "
        "a list/tuple, AnyOf(...), ALLOW_ANY or DENY."
    )


def requirement_codenames(requirement: Any) -> tuple[str, ...]:
    """The permission strings inside a requirement, for error messages."""
    if isinstance(requirement, Feature):
        return requirement_codenames(requirement.requirement)
    if isinstance(requirement, str):
        return (requirement,)
    if isinstance(requirement, AnyOf):
        return tuple(requirement)
    if isinstance(requirement, (list, tuple, set, frozenset)):
        return tuple(requirement)
    return ()


def crud(app_label: str, model: str, **extra: Any) -> dict[str, Any]:
    """Build the standard CRUD table for a `ModelViewSet`, plus extras.

    ``crud("fees", "fee", waive="fees.waive_fee")`` expands to the five CRUD
    entries and a ``default`` of ``fees.view_fee``, then adds ``waive``.
    Keyword names are action names, so any `@action` method can be listed;
    pass ``default=...`` to change the catch-all.

    Custom actions are the point of this file - name every one of them here
    rather than letting it fall through to the model's `change_` permission.
    """
    table: dict[str, Any] = {
        "list": f"{app_label}.view_{model}",
        "retrieve": f"{app_label}.view_{model}",
        "create": f"{app_label}.add_{model}",
        "update": f"{app_label}.change_{model}",
        "partial_update": f"{app_label}.change_{model}",
        "destroy": f"{app_label}.delete_{model}",
        DEFAULT: f"{app_label}.view_{model}",
    }
    table.update(extra)
    return table


class ActionPermission(BasePermission):
    """Reads `view.required_permissions`; see the module docstring."""

    message = "You do not have permission to perform this action."

    def _view_key(self, request, view) -> str:
        action = getattr(view, "action", None)
        if action:
            return action
        return request.method.lower()

    def _requirement(self, request, view) -> Any:
        key = self._view_key(request, view)
        if key in _ALWAYS_ALLOWED_ACTIONS:
            return ALLOW_ANY

        table = getattr(view, "required_permissions", None)
        if table is None:
            raise AssertionError(
                f"{view.__class__.__name__} uses ActionPermission but does not "
                "declare `required_permissions`. Add the table (see "
                "api/rbac/permissions.py) or drop the permission class."
            )

        if key in table:
            return table[key]
        return table.get(DEFAULT, DENY)

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not user or not user.is_authenticated:
            return False

        requirement = self._requirement(request, view)
        allowed = user_has(user, requirement)

        if not allowed:
            if isinstance(requirement, Feature) and not requirement.enabled:
                # Not an authorization failure: this deployment does not run
                # the module at all, and saying "you need permission X" would
                # send somebody looking for a grant that will never help.
                self.message = (
                    "This feature is not enabled for this deployment."
                )
                return False

            needed = requirement_codenames(requirement)
            if needed:
                self.message = (
                    "You do not have permission to perform this action "
                    f"(requires: {', '.join(sorted(needed))})."
                )
            logger.info(
                "rbac.denied user=%s view=%s action=%s requires=%s",
                getattr(user, "username", user),
                view.__class__.__name__,
                self._view_key(request, view),
                needed or requirement,
            )
        return allowed


class HasAnyRole(BasePermission):
    """Group membership check, for the few places a permission is a poor fit.

    Prefer `ActionPermission`: roles are a package of permissions, and code
    that checks the package instead of the capability has to be edited every
    time a new role should be allowed in.
    """

    #: Override on the view as `required_roles = (...)`, or subclass.
    roles: Sequence[str] = ()
    message = "Your role does not grant access to this resource."

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True

        wanted = {r.lower() for r in (getattr(view, "required_roles", None) or self.roles)}
        if not wanted:
            return False
        return bool(wanted & user_role_names(user))


class ReadOnly(BasePermission):
    """Companion for `permission_classes = [..., ReadOnly | SomethingElse]`."""

    def has_permission(self, request, view) -> bool:
        return request.method in SAFE_METHODS


class InstituteScoped(BasePermission):
    """Object-level tenancy: the object must belong to the caller's institute.

    Superusers pass. Users with no institute fail closed. Objects that expose
    no institute at all (global reference data) pass - row filtering for
    those belongs in `get_queryset`, not here.

    Point `institute_paths` at wherever the institute lives, most specific
    first::

        class FeeViewSet(ModelViewSet):
            permission_classes = [IsAuthenticated, ActionPermission, InstituteScoped]
            institute_paths = ("student.program.institute_id",)
    """

    message = "This record is outside your institute."

    #: Default probes, tried in order, when the view names none.
    default_paths: tuple[str, ...] = (
        "institute_id",
        "student.program.institute_id",
        "program.institute_id",
        "category.institute_id",
        "user.institute_id",
    )

    def _paths(self, view) -> Iterable[str]:
        return getattr(view, "institute_paths", None) or self.default_paths

    @staticmethod
    def _resolve(obj: Any, path: str) -> Any:
        current = obj
        for part in path.split("."):
            current = getattr(current, part, None)
            if current is None:
                return None
        return current

    def has_object_permission(self, request, view, obj) -> bool:
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True

        for path in self._paths(view):
            institute_id = self._resolve(obj, path)
            if institute_id is not None:
                return institute_id == getattr(user, "institute_id", None)

        # Nothing on this object identifies an institute.
        return True


def user_role_names(user) -> set[str]:
    """Lower-cased group names for a user, or an empty set for anonymous."""
    if not user or not user.is_authenticated:
        return set()
    return {
        str(name).strip().lower()
        for name in user.groups.values_list("name", flat=True)
        if str(name).strip()
    }


def user_permission_codenames(user) -> list[str]:
    """Every ``app_label.codename`` a user holds, sorted.

    This is what `/api/v1/me/` hands the frontend so the UI can hide what the
    API would refuse anyway. Superusers get the full catalog, matching
    `ModelBackend.has_perm`, which short-circuits to True for them.

    Permissions belonging to a switched-off feature are dropped, superusers
    included. A database seeded while the module was on keeps its rows, so
    filtering here is what makes turning the flag off take effect on the next
    request instead of the next `seed_rbac`.
    """
    if not user or not user.is_authenticated:
        return []

    from core import features
    from .roles import enabled_permissions

    institute = features.resolve_institute(user)

    if user.is_superuser:
        from django.contrib.auth.models import Permission

        held: Iterable[str] = (
            f"{app_label}.{codename}"
            for app_label, codename in Permission.objects.values_list(
                "content_type__app_label", "codename"
            )
        )
    else:
        held = user.get_all_permissions()

    return sorted(enabled_permissions(held, institute=institute))
