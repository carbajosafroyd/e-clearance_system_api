"""
Which permission governs which system setting, and which system it belongs to.

`bulk_change_systemsetting` used to gate the whole key-value store, so anyone
who could set the academic year could also close the institute. Those are not
the same decision and they do not belong to the same people: running a term is
the institute administrator's job, closing the institute is their school's.

So the settings are split into buckets, each with its own permission, and the
mapping lives here in code rather than as a column on the row. A grant that is
a database row cannot be reviewed in a diff and cannot be seeded - two
environments would disagree about who may close the app with nothing to
compare. `roles.py` plus `seed_rbac` stays the one file to edit and the one
command to propagate, the same way `FEATURE_PERMISSIONS` already works.

Buckets rather than one permission per key, because a Django permission needs
``Meta.permissions`` and a migration. Categories are stable; keys are data, and
adding a row next month should not need either.

Reading is deliberately *not* bucketed
--------------------------------------
Every signed-in account fetches these on boot - the frontend filters every
list and report by the current term, and it renders a module only if the row
says the institute runs it. A student who could not read the settings could
not use the app at all.

Writing is the part that needs dividing, so this file is consulted on write
and on nothing else. The read side reports `can_change` per row instead, so a
settings page can hide the controls somebody may not use without having to
re-derive the rule.

Reading *is* narrowed by system, though - see `system_for`. A row carries the
system it configures, so the collection app can ask for the collection
settings instead of for every setting on the platform.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Union

from django.conf import settings as django_settings

from core import features
from core.choices import Semester, SystemScope, get_default_academic_year

#: Coarse, and still the fallback. A key nobody has bucketed yet stays behind
#: the permission that used to guard everything, so adding a setting can never
#: accidentally open it to more people than the day before.
BULK = "system_settings.bulk_change_systemsetting"

#: The academic year and semester every list filter and report defaults to.
TERM = "system_settings.change_term_settings"

#: The optional modules - see `core.features`.
FEATURES = "system_settings.change_feature_settings"

#: Closing a system to its staff, and the message shown while it is closed.
MAINTENANCE = "system_settings.change_maintenance"

#: Closing the *student* side while staff keep working.
#:
#: A narrower thing than `MAINTENANCE`, and a different person's call. Holding
#: students out for an hour while fee data is corrected is the work the
#: institute's own administrators do; closing a system to the staff who run it
#: stops that work, so it belongs to their school. Giving them the wide switch
#: so they could do the narrow job would hand every institute the power to
#: shut itself.
STUDENT_MAINTENANCE = "system_settings.change_student_maintenance"

#: Whether students may pay online at all.
#:
#: The narrowest switch here, and its own bucket rather than a feature flag:
#: a feature is a module the deployment ships or does not, decided rarely;
#: this is the collection desk closing the GCash lane for an afternoon while
#: the receiving account is sorted out, and reopening it. Same hands as the
#: student-side shutter, and for the same reason - holding students out of
#: one lane IS the desk's work - but its own permission, so a deployment can
#: hand out one without the other.
PAYMENTS = "system_settings.change_payment_settings"

#: Keep working while your system is closed.
#:
#: Not the same idea as `MAINTENANCE`, which is the authority to *close* it.
#: Whoever closed a system has to be able to reopen it, so somebody must be
#: able to reach the settings page through a shutter they set - but "can close
#: it" and "is exempt from it" are different sentences, and a deployment
#: should be able to grant one without the other.
BYPASS_MAINTENANCE = "system_settings.bypass_maintenance"

#: Reach every institute in your own school, rather than only your own.
#:
#: The tenant rule for settings is "your institute" for everybody else. A
#: school administrator answers for the institutes under them, so closing one
#: of them is their job - but only theirs, never another school's.
SCHOOL_WIDE = "system_settings.manage_school_settings"

#: The systems a maintenance switch can close.
#:
#: Derived from `core.choices.SystemScope` rather than retyped, because that
#: class already says what the systems are and its own docstring already says
#: adding one means editing every place that lists them. A third hand-written
#: list here was exactly the drift this module exists to prevent - it grew a
#: `student_portal` that neither of the other two had ever heard of.
#:
#: PLATFORM is excluded: a shutter closes something people log into, and
#: "platform" is the absence of one. It is still a valid `system` for a row -
#: the academic year is configured for the institute, not for one system.
SYSTEMS: tuple[tuple[str, str], ...] = tuple(
    (value, label)
    for value, label in SystemScope.choices
    if value != SystemScope.PLATFORM
)

SYSTEM_KEYS: tuple[str, ...] = tuple(system for system, _ in SYSTEMS)
SYSTEM_LABELS: dict[str, str] = dict(SystemScope.choices)

#: Systems with a frontend that actually reads these rows today.
#:
#: The seeder writes shutters only for these. `core.features` already refuses
#: to seed a module an institute cannot run, on the grounds that "writing a
#: row for a feature it is not entitled to would put a dead switch on its
#: settings page" - and a shutter for a system nobody has built yet is the
#: same dead switch. Flipping `voting_student_maintenance_mode` does nothing,
#: because nothing reads it.
#:
#: Override per deployment with `LIVE_SYSTEMS` in Django settings; the day the
#: locker app ships, that list gains a word and the next seed writes its rows.
DEFAULT_LIVE_SYSTEMS: tuple[str, ...] = (SystemScope.COLLECTION,)


def live_systems() -> tuple[str, ...]:
    """The systems this deployment actually serves, in `SYSTEMS` order."""
    configured = getattr(django_settings, "LIVE_SYSTEMS", None)
    if configured is None:
        configured = DEFAULT_LIVE_SYSTEMS
    wanted = {str(system).strip().lower() for system in configured}
    return tuple(system for system in SYSTEM_KEYS if system in wanted)


#: Who a shutter closes over.
#:
#: The split is the whole point of having two. Closing a system to students
#: while staff keep working is the ordinary case - somebody is correcting data
#: and would rather nobody transacted against it mid-fix - and it is a
#: different decision, made by different people, from closing the same system
#: to the staff themselves.
OFFICER = "officer"
STUDENT = "student"
AUDIENCES: tuple[str, ...] = (OFFICER, STUDENT)


def maintenance_key(system: str, audience: str) -> str:
    """e.g. `collection_student_maintenance_mode`."""
    return f"{system}_{audience}_maintenance_mode"


def maintenance_message_key(system: str, audience: str) -> str:
    """e.g. `collection_student_maintenance_message`."""
    return f"{system}_{audience}_maintenance_message"


#: The bucket that owns each audience's switch.
#:
#: Officer-side closures are the console's - shutting the staff out of a system
#: stops the work, so it belongs a tier up. The student side is the operations
#: desk's own, because holding students out for an hour IS the work.
AUDIENCE_BUCKET = {
    OFFICER: MAINTENANCE,
    STUDENT: STUDENT_MAINTENANCE,
}


@dataclass(frozen=True)
class SettingSpec:
    """One setting: its key, who may write it, and what it starts as.

    The bucket and the seed default live on the same object deliberately.
    They used to be two lists in two files - the permission map here and the
    seeder's own - and nothing made them agree, so a setting could be seeded
    into every institute while being governed by the coarse fallback, or
    bucketed here and never created anywhere. Both failures are silent.

    One entry now means the row is seeded *and* governed. `test_catalog.py`
    fails the build if the two ever come apart again.
    """

    key: str
    #: The permission needed to write it.
    bucket: str
    #: What the seeder writes when the row does not exist. A callable where
    #: the value depends on when it is seeded - the academic year does.
    default: Union[str, Callable[[], str]] = ""
    description: str = ""
    #: Which system this row configures, so a frontend can ask for its own.
    #: PLATFORM means "every system needs this one" - the academic year is
    #: not the collection app's setting, it is the institute's.
    system: str = SystemScope.PLATFORM

    def initial_value(self) -> str:
        return str(self.default() if callable(self.default) else self.default)


#: The settings that describe the institute rather than any one system.
#:
#: Feature rows are NOT here: there is one per optional module and the module
#: list is `core.features`, so listing them again would be the same drift in a
#: different place. `permission_for` recognises them by prefix.
_TERM: tuple[SettingSpec, ...] = (
    SettingSpec(
        "current_school_year",
        TERM,
        get_default_academic_year,
        "Academic year the UI defaults its filters and reports to.",
        system=SystemScope.PLATFORM,
    ),
    SettingSpec(
        "current_semester",
        TERM,
        lambda: Semester.FIRST.value,
        "Semester the UI defaults its filters and reports to.",
        system=SystemScope.PLATFORM,
    ),
)


def _system_specs() -> tuple[SettingSpec, ...]:
    """A shutter and a message per system, per audience.

    Generated rather than written out. Near-identical entries invite a
    copy-paste that names one system and buckets another, and that mistake is
    invisible until somebody cannot close the thing they meant to.

    Every system in `SYSTEMS` is declared - so the permission map has an
    answer the moment a row appears - but only `live_systems()` is seeded.
    Declaring is free; seeding puts a switch on somebody's page.
    """
    specs: list[SettingSpec] = []
    for system, label in SYSTEMS:
        for audience in AUDIENCES:
            who = "staff" if audience == OFFICER else "students"
            specs.append(
                SettingSpec(
                    maintenance_key(system, audience),
                    AUDIENCE_BUCKET[audience],
                    "false",
                    f"When true, {who} are held out of the {label}.",
                    system=system,
                )
            )
            specs.append(
                SettingSpec(
                    maintenance_message_key(system, audience),
                    AUDIENCE_BUCKET[audience],
                    "",
                    f"Shown to {who} while the {label} is closed to them.",
                    system=system,
                )
            )
    return tuple(specs)


#: The one switch that closes the online lane.
ONLINE_PAYMENTS_KEY = "allow_online_payments"

#: The collection system's own knobs - the ones read by code rather than by
#: a shutter page. Only this one for now: "days before a fee is overdue" is
#: handled at fee generation and never was a setting.
_PAYMENTS: tuple[SettingSpec, ...] = (
    SettingSpec(
        ONLINE_PAYMENTS_KEY,
        PAYMENTS,
        "true",
        "When false, students are not offered GCash and a payment submission "
        "is refused. Payments recorded at the desk are unaffected.",
        system=SystemScope.COLLECTION,
    ),
)

CATALOG: tuple[SettingSpec, ...] = _TERM + _PAYMENTS + _system_specs()

_BY_KEY: dict[str, SettingSpec] = {spec.key: spec for spec in CATALOG}


def spec_for(setting_key: str) -> Optional[SettingSpec]:
    return _BY_KEY.get(setting_key)


def catalog_keys() -> tuple[str, ...]:
    return tuple(spec.key for spec in CATALOG)


def seeded_specs() -> tuple[SettingSpec, ...]:
    """The catalog entries a fresh institute actually gets rows for."""
    running = set(live_systems())
    return tuple(
        spec
        for spec in CATALOG
        if spec.system == SystemScope.PLATFORM or spec.system in running
    )


def keys_in(bucket: str) -> tuple[str, ...]:
    """Every catalog key governed by `bucket`."""
    return tuple(spec.key for spec in CATALOG if spec.bucket == bucket)


#: The system an optional module belongs to.
#:
#: All four modules - contributions, the points scheme, complaints and coupons
#: - are parts of the collection system: they are filed against fees and paid
#: out against fees. If a later module belongs somewhere else this stops being
#: a constant and starts being a lookup in `core.features`.
FEATURE_SYSTEM = SystemScope.COLLECTION


def system_for(setting_key: str) -> str:
    """Which system a key configures. PLATFORM means "all of them"."""
    spec = _BY_KEY.get(setting_key)
    if spec is not None:
        return spec.system
    if setting_key.startswith(features.SETTING_PREFIX):
        return FEATURE_SYSTEM
    return SystemScope.PLATFORM


#: Every permission this module hands out, for the settings page and for
#: `seed_rbac`'s "does this string exist" test.
ALL_BUCKETS: tuple[str, ...] = (
    TERM,
    FEATURES,
    PAYMENTS,
    MAINTENANCE,
    STUDENT_MAINTENANCE,
)


def permission_for(setting_key: str) -> str:
    """The permission needed to write `setting_key`."""
    spec = _BY_KEY.get(setting_key)
    if spec is not None:
        return spec.bucket
    if setting_key.startswith(features.SETTING_PREFIX):
        return FEATURES
    return BULK


def may_change(user, setting_key: str) -> bool:
    """May this user write `setting_key`?

    Superusers pass, mirroring Django's `ModelBackend` and the rest of the
    codebase. Everyone else needs the bucket's permission; holding the coarse
    `bulk_change_systemsetting` is not enough on its own, or splitting the
    buckets would have changed nothing.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return user.has_perm(permission_for(setting_key))


def refused_keys(user, setting_keys: Iterable[str]) -> list[str]:
    """The keys in a payload this user may not write, in a stable order."""
    return sorted({key for key in setting_keys if not may_change(user, key)})


def changeable_buckets(user) -> tuple[str, ...]:
    """Which buckets this user may edit - what a settings page renders."""
    if user is None or not getattr(user, "is_authenticated", False):
        return ()
    if getattr(user, "is_superuser", False):
        return ALL_BUCKETS
    return tuple(bucket for bucket in ALL_BUCKETS if user.has_perm(bucket))


def bucket_of(setting_key: str) -> Optional[str]:
    """The bucket name a settings page groups this key under, or None."""
    permission = permission_for(setting_key)
    return permission if permission in ALL_BUCKETS else None


#: What a stored flag reads as. Mirrors the frontends' `isOn`, so "TRUE",
#: "yes" and "1" mean the same thing on every side.
_ON_VALUES = frozenset({"true", "1", "yes", "on", "enabled"})


def online_payments_enabled(institute) -> bool:
    """
    May students of `institute` pay online right now?

    Read fresh, not cached: the whole point of the switch is that the desk
    flips it and the next submission is refused, and a cache would let a
    payment through for as long as the cache lives. One indexed lookup per
    submission is nothing next to the image upload the submission carries.

    An absent row reads as the seed default - ON. The row is only ever
    absent on an institute seeded before the switch existed, and those
    institutes were accepting online payments the day before; a missing
    setting must not silently close the lane on them.
    """
    institute_id = getattr(institute, "pk", institute)
    if institute_id is None:
        return True

    # Imported here for the same reason `core.features` does it: this module
    # is loaded before the app registry is ready.
    from api.system_settings.models import SystemSetting

    value = (
        SystemSetting.objects.filter(
            institute_id=institute_id, setting_key=ONLINE_PAYMENTS_KEY
        )
        .values_list("setting_value", flat=True)
        .first()
    )
    if value is None:
        return True
    return str(value).strip().lower() in _ON_VALUES
