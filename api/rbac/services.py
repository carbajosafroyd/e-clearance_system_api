"""
Turning `roles.py` into rows: groups, their permissions, and the `is_staff`
flag that older code paths still read.

Everything here is idempotent - running it twice changes nothing the second
time - so `seed_rbac` is safe to run on every deploy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import transaction

from .roles import (
    ROLE_DEFINITIONS,
    permission_enabled,
    resolve_permissions,
    staff_roles,
)

logger = logging.getLogger(__name__)


@dataclass
class SyncReport:
    created_groups: list[str] = field(default_factory=list)
    updated_groups: list[str] = field(default_factory=list)
    unchanged_groups: list[str] = field(default_factory=list)
    removed_groups: list[str] = field(default_factory=list)
    #: Permission strings named in roles.py that no model registers.
    missing_permissions: set[str] = field(default_factory=set)
    staff_promoted: int = 0
    staff_demoted: int = 0


def _permission_objects() -> tuple[dict[str, Permission], dict[str, list[Permission]]]:
    by_label: dict[str, Permission] = {}
    by_app: dict[str, list[Permission]] = {}

    for permission in Permission.objects.select_related("content_type"):
        app_label = permission.content_type.app_label
        by_label[f"{app_label}.{permission.codename}"] = permission
        by_app.setdefault(app_label, []).append(permission)

    return by_label, by_app


def expand(specs) -> tuple[list[Permission], set[str]]:
    """Resolve permission strings (incl. wildcards) to `Permission` rows.

    Returns the resolved permissions and the set of strings that matched
    nothing - typically a typo, or a custom permission whose migration has
    not been applied yet.
    """
    by_label, by_app = _permission_objects()

    resolved: dict[int, Permission] = {}
    missing: set[str] = set()

    for spec in specs:
        if spec == "*":
            resolved.update({p.pk: p for p in by_label.values()})
            continue

        if spec.endswith(".*"):
            app_label = spec[:-2]
            app_permissions = by_app.get(app_label)
            if not app_permissions:
                missing.add(spec)
                continue
            resolved.update({p.pk: p for p in app_permissions})
            continue

        permission = by_label.get(spec)
        if permission is None:
            missing.add(spec)
            continue
        resolved[permission.pk] = permission

    # Wildcards are expanded above, so this is where `"*"` and `"app.*"` meet
    # the feature flags: a disabled module's permissions are never granted,
    # however broadly a role asked for them.
    return [
        permission
        for permission in resolved.values()
        if permission_enabled(
            f"{permission.content_type.app_label}.{permission.codename}"
        )
    ], missing


def sync_role(role_name: str) -> Group:
    """Create one role and set its permissions. Idempotent.

    Useful on its own in tests and fixtures, where building the whole
    catalog to get one group is wasteful.
    """
    permissions, _missing = expand(resolve_permissions(role_name))
    group, _ = Group.objects.get_or_create(name=role_name)
    group.permissions.set(permissions)
    return group


@transaction.atomic
def sync_roles(*, prune_unknown_groups: bool = False) -> SyncReport:
    """Create every role in `ROLE_DEFINITIONS` and set its permissions.

    A group's permission set is replaced, not merged: `roles.py` is the
    source of truth, so a permission removed there is removed from the
    database too. Groups that are *not* in `roles.py` are left alone unless
    `prune_unknown_groups` is set - ad-hoc groups created by hand for a
    single institute are a legitimate thing to keep.
    """
    report = SyncReport()

    for role_name in ROLE_DEFINITIONS:
        specs = resolve_permissions(role_name)
        permissions, missing = expand(specs)
        report.missing_permissions |= missing

        group, created = Group.objects.get_or_create(name=role_name)
        current = set(group.permissions.values_list("pk", flat=True))
        wanted = {p.pk for p in permissions}

        if created:
            group.permissions.set(permissions)
            report.created_groups.append(role_name)
        elif current != wanted:
            group.permissions.set(permissions)
            report.updated_groups.append(role_name)
        else:
            report.unchanged_groups.append(role_name)

    if prune_unknown_groups:
        stale = Group.objects.exclude(name__in=list(ROLE_DEFINITIONS))
        report.removed_groups = list(stale.values_list("name", flat=True))
        stale.delete()

    logger.info(
        "rbac.sync created=%s updated=%s unchanged=%s missing=%s",
        len(report.created_groups),
        len(report.updated_groups),
        len(report.unchanged_groups),
        len(report.missing_permissions),
    )
    return report


@transaction.atomic
def sync_staff_flags(*, demote: bool = False) -> tuple[int, int]:
    """Align ``User.is_staff`` with the roles that declare ``staff_flag``.

    Returns ``(promoted, demoted)``. Demotion is opt-in and never touches
    superusers: an account can legitimately be staff for a reason that has
    nothing to do with these groups (the Django admin, for one).
    """
    User = get_user_model()
    names = list(staff_roles())

    promoted = (
        User.objects.filter(groups__name__in=names, is_staff=False)
        .distinct()
        .update(is_staff=True)
    )

    demoted = 0
    if demote:
        demoted = (
            User.objects.filter(is_staff=True, is_superuser=False)
            .exclude(groups__name__in=names)
            .update(is_staff=False)
        )

    return promoted, demoted


def assign_role(user, role_name: str) -> None:
    """Put a user in a role, creating and filling the group if need be.

    The group is (re)synced from `roles.py` on the way through, so this works
    on a database where `seed_rbac` has never run - which is exactly the
    situation in tests and in a freshly migrated environment.
    """
    user.groups.add(sync_role(role_name))

    spec = ROLE_DEFINITIONS.get(role_name)
    if spec and spec.staff_flag and not user.is_staff:
        user.is_staff = True
        user.save(update_fields=["is_staff"])

    # `has_perm` caches the whole permission set on the user instance the
    # first time it is asked; without dropping it, code that assigns a role
    # and then checks a permission on the same object sees the old answer.
    for attribute in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
        user.__dict__.pop(attribute, None)
