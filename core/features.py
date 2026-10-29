"""
Feature flags: which optional parts of the platform an institute runs.

The collection system serves several institutes from one deployment, and not
every institute wants every optional module. The contribution/points scheme
in particular is one institute's idea - another may want the fee and payment
core with none of it.

Two levels, and they answer different questions.

**The environment says what this build ships.** `FEATURE_COUPONS=False` means
the module is not in this deployment at all: the routes are never registered
(`api/urls.py`), so the endpoints 404, and no institute can turn it on. This
is the kill switch, and changing it needs a restart because Django builds its
URL table once.

**The database says what each institute runs.** A `feature_<name>` row in
`SystemSetting`, per institute, decides whether the endpoints answer *for
that institute* and whether its UI renders the module. That is the toggle
staff actually use, it takes effect on the next request, and it needs no
deploy. An institute with no row inherits the environment default, so an
existing deployment behaves exactly as it did before anybody wrote a row.

The two compose as an AND: the environment can only take a feature away, and
the database can only choose within what the build ships. A flag that is off
in the environment is off everywhere, whatever the rows say - so a module
pulled for a real reason cannot be switched back on from a settings page.

Not every institute may toggle every flag. `available_features()` is the
allow-list, and it is what a settings UI should offer; writing a row for a
feature an institute is not entitled to has no effect.

Layers, each failing closed
---------------------------
1. routes unregistered when the build does not ship the module;
2. `Feature(...)` / `gate(...)` refuse per request, resolved against the
   caller's institute;
3. `FEATURE_PERMISSIONS` in `api/rbac/roles.py` strips the grants a disabled
   feature owns, so `/api/v1/me/` never reports a permission the API would
   refuse;
4. `/api/v1/me/` reports the resolved flags, so a UI built against the same
   list has nothing to render.

Adding a flag
-------------
1. name it here and give it a default in `config/settings/base.py`;
2. if it owns permissions, list their codenames in
   `api/rbac/roles.py::FEATURE_PERMISSIONS`;
3. gate its routes in `api/urls.py` and its actions with
   `api.rbac.Feature(...)`;
4. add it to `FEATURE_SETTINGS` in the system-settings seeder;
5. document it in `.env.example`.

`settings.FEATURES` is read on every call rather than captured at import, so
tests can use `@override_settings(FEATURES={...})` and so a value is never
stale.
"""

from __future__ import annotations

from typing import Iterable, Optional

from django.conf import settings
from django.core.cache import cache

#: Student reports, recommendations and complaints-about-the-software. The
#: master switch for the contributions module.
CONTRIBUTIONS = "contributions"

#: The reward half of it: points, wallets, the ledger, the ranking and
#: points-funded fee discounts. Off means reports are still filed and
#: reviewed, they just do not pay - which is the useful state while the
#: scheme is being trialled.
CONTRIBUTION_POINTS = "contribution_points"

#: Student complaints (a mis-issued fee, a payment that never landed) and the
#: staff workflow that resolves them. Deliberately independent: a complaint
#: is a service obligation, not a contribution, and it never earns points.
COMPLAINTS = "complaints"

#: Coupons: relief the institute hands out for turning up to things, rather
#: than relief a student earns by reporting things. Independent of the
#: contribution scheme in both directions - a deployment can run either, both
#: or neither, and a fee can carry a discount from each at once.
COUPONS = "coupons"

#: Every flag, in the order they are reported.
ALL_FEATURES: tuple[str, ...] = (
    CONTRIBUTIONS,
    CONTRIBUTION_POINTS,
    COMPLAINTS,
    COUPONS,
)

#: A feature that cannot be on while its parent is off. Kept here rather than
#: left to whoever writes the settings, so an inconsistent pair is impossible
#: instead of merely discouraged.
REQUIRES: dict[str, str] = {
    CONTRIBUTION_POINTS: CONTRIBUTIONS,
}

#: Features only some institutes may run, and who may run them.
#:
#: The contribution scheme belongs to the institute that built it. Offering
#: the toggle to everyone else means a settings page that promises something
#: the deployment is not set up to deliver - and the reason it is limited is
#: policy, not a technical one, so it is stated here rather than buried in a
#: view.
#:
#: Matched on `Institute.acronym`, not the display name: a name is edited for
#: presentation, and policy that turns on a substring of one breaks quietly.
#:
#: An institute whose acronym is blank is *not* refused - see `is_available`.
#: The field is new, and reading "unknown" as "no" would switch the scheme off
#: for the institute that built it on the day this ships.
RESTRICTED_TO_ACRONYMS: dict[str, tuple[str, ...]] = {
    CONTRIBUTIONS: ("IC",),
    CONTRIBUTION_POINTS: ("IC",),
}

#: `SystemSetting.setting_key` for a flag: `feature_coupons`, and so on.
SETTING_PREFIX = "feature_"

#: Cheap and short. The rows change when somebody saves a settings form, and
#: `invalidate()` clears this on write - the timeout is only a backstop for a
#: row edited straight in the database.
#:
#: One caveat worth knowing before somebody debugs it: on Redis the
#: invalidation is shared, so a toggle lands on every worker at once. On the
#: LocMemCache fallback each worker has its own copy and only the one that
#: handled the save is cleared, so the rest catch up within this window. If a
#: toggle "sometimes works", that is why.
CACHE_TIMEOUT = 300

_TRUE = {"1", "true", "yes", "on", "enabled"}
_FALSE = {"0", "false", "no", "off", "disabled"}


def setting_key(feature: str) -> str:
    return f"{SETTING_PREFIX}{feature}"


def parse_flag(raw: object, default: bool = False) -> bool:
    """Read a stored value as a boolean.

    Settings are a text column and the values are typed by people, so "TRUE",
    "yes" and "1" all have to mean the same thing. Anything unrecognised
    falls back to `default` rather than to `bool("something")`, which is
    True for every non-empty string including "false".
    """
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return default
    text = str(raw).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    # Unrecognised reads as off, the same way an unknown feature name does.
    # A module quietly staying on because somebody typed "ture" is the worse
    # of the two failures: one shows up as a missing page, the other as an
    # endpoint nobody meant to leave open.
    return default


def _cache_key(institute_id: int) -> str:
    return f"features:institute:{institute_id}"


def _institute_id_of(institute) -> Optional[int]:
    """Accept an Institute, a user, an id, or None."""
    if institute is None:
        return None
    if isinstance(institute, int):
        return institute
    # A user, which is what a permission check actually has in hand.
    for attribute in ("institute_id", "pk"):
        value = getattr(institute, attribute, None)
        if isinstance(value, int):
            return value
    return None


def institute_overrides(institute) -> dict[str, bool]:
    """The `feature_*` rows this institute has, as booleans.

    Only the rows. The environment default is applied by `is_enabled`, so
    `@override_settings(FEATURES=...)` keeps working on top of whatever is
    cached here.
    """
    institute_id = _institute_id_of(institute)
    if institute_id is None:
        return {}

    key = _cache_key(institute_id)
    cached = cache.get(key)
    if cached is not None:
        return cached

    # Imported here: this module is loaded from `config.settings` territory
    # and at URL-build time, both of which run before the app registry is
    # ready.
    from api.system_settings.models import SystemSetting

    rows = SystemSetting.objects.filter(
        institute_id=institute_id,
        setting_key__in=[setting_key(name) for name in ALL_FEATURES],
    ).values_list("setting_key", "setting_value")

    overrides = {
        key_[len(SETTING_PREFIX):]: parse_flag(value) for key_, value in rows
    }
    cache.set(key, overrides, CACHE_TIMEOUT)
    return overrides


def invalidate(institute=None) -> None:
    """Drop the cached rows. Called whenever a `SystemSetting` is written."""
    institute_id = _institute_id_of(institute)
    if institute_id is None:
        return
    cache.delete(_cache_key(institute_id))


def ships_with_build(feature: str) -> bool:
    """Is the module in this deployment at all? The environment's answer."""
    flags = getattr(settings, "FEATURES", None) or {}
    return bool(flags.get(feature, False))


def is_available(feature: str, institute=None) -> bool:
    """May this institute run `feature` at all - never mind whether it does?

    What a settings page should offer. A flag the build does not ship is
    never available; a restricted one is available only to the institutes
    named for it.
    """
    if feature not in ALL_FEATURES or not ships_with_build(feature):
        return False

    allowed = RESTRICTED_TO_ACRONYMS.get(feature)
    if allowed is None:
        return True

    acronym = (getattr(institute, "acronym", "") or "").strip().upper()
    if not acronym:
        # A blank acronym is missing information, not a denial.
        #
        # The field is new, so every institute that existed before it was
        # added has one. Reading that as "not entitled" would switch the
        # contribution scheme off for the institute that built it the moment
        # this deploys - a restriction nobody asked for, applied to the one
        # party it was never aimed at. The environment flag still gates the
        # module; the restriction starts biting once acronyms are filled in.
        return True
    return acronym in allowed


def available_features(institute=None) -> tuple[str, ...]:
    """Every flag this institute may set, in report order."""
    return tuple(
        feature for feature in ALL_FEATURES if is_available(feature, institute)
    )


def is_enabled(feature: str, institute=None) -> bool:
    """Is `feature` switched on for this institute?

    Unknown names are False: a typo turns a feature off rather than silently
    opening one, and the mistake shows up as a missing page instead of an
    unguarded endpoint.

    With no institute the environment decides alone. That is the right answer
    for `seed_rbac` and for anything running outside a request, where "which
    institute" has no meaning.
    """
    if feature not in ALL_FEATURES or not ships_with_build(feature):
        return False

    if institute is not None and not is_available(feature, institute):
        return False

    overrides = institute_overrides(institute)
    if not overrides.get(feature, True):
        return False

    parent = REQUIRES.get(feature)
    if parent is not None and not is_enabled(parent, institute):
        return False
    return True


def enabled_features(institute=None) -> dict[str, bool]:
    """The full flag set, cascade applied - what `/api/v1/me/` reports."""
    return {
        feature: is_enabled(feature, institute) for feature in ALL_FEATURES
    }


def disabled_features(institute=None) -> tuple[str, ...]:
    return tuple(
        feature for feature in ALL_FEATURES if not is_enabled(feature, institute)
    )


def resolve_institute(user) -> object | None:
    """The institute a permission check should resolve flags against.

    Superusers included: one who is attached to an institute is working
    inside it, and giving them a different set of modules from the staff
    beside them makes every bug report unreproducible. Only a user with no
    institute at all falls back to the deployment answer, which is also what
    `seed_rbac` and the schema generator get.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return getattr(user, "institute", None)


def feature_settings(institute) -> list[dict]:
    """What a settings page needs to render the toggles.

    `available` is separate from `enabled` on purpose: a feature this
    institute may not run has to read as unavailable rather than as merely
    switched off, or somebody will keep trying to switch it on.
    """
    overrides = institute_overrides(institute)
    return [
        {
            "feature": feature,
            "key": setting_key(feature),
            "available": is_available(feature, institute),
            "enabled": is_enabled(feature, institute),
            "configured": feature in overrides,
            "requires": REQUIRES.get(feature),
        }
        for feature in ALL_FEATURES
    ]


def apply_cascade(values: dict[str, bool]) -> dict[str, bool]:
    """Force `REQUIRES` on a set of values about to be saved.

    Switching the parent off has to take the child with it. Storing the pair
    inconsistently would leave a row saying the points scheme is on while
    every check reports it off, which is the sort of disagreement somebody
    debugs for an afternoon.
    """
    resolved = dict(values)
    for child, parent in REQUIRES.items():
        if child in resolved and not resolved.get(parent, True):
            resolved[child] = False
    return resolved


def known_feature_keys() -> Iterable[str]:
    return (setting_key(feature) for feature in ALL_FEATURES)
