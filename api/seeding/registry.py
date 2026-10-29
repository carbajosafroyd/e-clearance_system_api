"""
The order the seeders run in.

`seed_all` walks this list top to bottom. The order is the dependency order:
a school before an institute, an institute before a program, a program before
a student, a student before a fee. Adding a seeder means adding one line
*after* whatever it needs.

`core` steps run on every `seed_all`; `demo` steps only when demo data is
wanted (`seed_all` includes them by default and `--core-only` skips them).
Roles are core because the API is unusable without them - every endpoint is
permission-gated.

A step may also be `essential`: part of the smallest dataset somebody can
actually sign in to and use - roles, the schools, their institutes and
programs, an account per role, and the settings the frontend reads on boot.
`seed_all --essential` runs those and stops, which is the way to start a new
database without a pile of sample fees and payments in it. Essential is a
wider set than core: `seed_users` is essential but not core, because it
creates accounts with a shared password and has no business running in
production.

A step may name a `feature`; it is skipped where that feature is switched
off, so seeding a deployment that does not run a module does not create rows
nobody can reach.
"""

from __future__ import annotations

from dataclasses import dataclass

from core import features


@dataclass(frozen=True)
class SeedStep:
    command: str
    description: str
    #: "core" runs everywhere (including production); "demo" is sample data.
    kind: str = "demo"
    #: Part of the minimum a usable, signed-into deployment needs.
    essential: bool = False
    #: The command takes `--essential` itself, and seeds a smaller set with
    #: it. `seed_users` does: every role account is useful demo data, but a
    #: new deployment only needs the admins.
    supports_essential: bool = False
    #: Passed through only when the command accepts them.
    supports_scale: bool = True
    #: Feature flag this step belongs to, or None for always.
    feature: str | None = None

    @property
    def enabled(self) -> bool:
        return self.feature is None or features.is_enabled(self.feature)


SEED_STEPS: tuple[SeedStep, ...] = (
    SeedStep(
        "seed_rbac",
        "Roles and their permissions",
        kind="core",
        essential=True,
        supports_scale=False,
    ),
    SeedStep("seed_schools", "Schools and buildings", essential=True),
    SeedStep("seed_institutes", "Institutes", essential=True),
    SeedStep("seed_programs", "Degree programs", essential=True),
    SeedStep(
        "seed_users",
        "One account per role, plus staff",
        essential=True,
        supports_essential=True,
    ),
    SeedStep("seed_students", "Students and their linked accounts"),
    SeedStep(
        "seed_system_settings",
        "Per-institute settings",
        essential=True,
        supports_scale=False,
    ),
    SeedStep("seed_clearance", "Clearance templates and student clearances", essential=True),
    SeedStep("seed_notifications", "Notifications and one broadcast"),
    SeedStep("seed_audits", "A short audit trail"),
)


def steps(
    kind: str | None = None,
    *,
    essential_only: bool = False,
    include_disabled: bool = False,
) -> tuple[SeedStep, ...]:
    selected = tuple(
        step
        for step in SEED_STEPS
        if (kind is None or step.kind == kind)
        and (not essential_only or step.essential)
    )
    if include_disabled:
        return selected
    return tuple(step for step in selected if step.enabled)
