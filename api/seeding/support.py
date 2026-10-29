"""
Shared machinery for the `seed_*` commands.

Every seeder in this project subclasses `SeedCommand` and gets the same
behaviour for free:

* **idempotent** - running it twice does not double the data. Seeders use
  ``get_or_create`` on a natural key, so re-running fills in what is missing
  and leaves the rest alone.
* **`--reset`** - delete this feature's rows first, then seed from scratch.
* **`--scale N`** - multiply the volume. `--scale 1` is a demo dataset you
  can read on one screen; `--scale 10` is enough to notice a missing index.
* **deterministic** - the random stream is seeded from a fixed value, so the
  same command produces the same data on every machine. Pass
  ``--random-seed`` for a different-but-still-reproducible dataset.
* **dependency-aware** - `self.require(...)` fails with the command you
  should have run first, instead of an IntegrityError six frames deep.
* **feature-aware** - a seeder that names a `feature` does nothing where that
  feature is switched off, so running it by hand on a deployment without the
  module is a no-op rather than a pile of unreachable rows.

Write a new one like this::

    class Command(SeedCommand):
        seed_name = "widgets"
        help = "Create demo widgets."
        depends_on = ("seed_institutes",)

        def seed(self, **options):
            institutes = self.require(Institute, "seed_institutes")
            for institute in institutes:
                Widget.objects.get_or_create(institute=institute, name="Demo")
            self.created("widget", len(institutes))

Then add it to `SEED_STEPS` in `api/seeding/registry.py` so `seed_all` picks
it up in the right order.
"""

from __future__ import annotations

import random
from typing import Iterable, Sequence

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core import features

#: Fixed so two developers seeding the same feature get the same rows.
DEFAULT_RANDOM_SEED = 20260824


class SeedCommand(BaseCommand):
    """Base class for every `seed_*` command."""

    #: Short name used in output, e.g. "students".
    seed_name = ""

    #: Commands that must have run first. Reported, not executed - `seed_all`
    #: is what puts them in order.
    depends_on: Sequence[str] = ()

    #: Feature flag this seeder belongs to (`core.features`), or None for
    #: data every deployment has.
    feature: str | None = None

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete this feature's seeded rows before creating them.",
        )
        parser.add_argument(
            "--scale",
            type=int,
            default=1,
            help="Volume multiplier (default 1 - a small readable dataset).",
        )
        parser.add_argument(
            "--random-seed",
            type=int,
            default=DEFAULT_RANDOM_SEED,
            help="Seed for the random stream, so runs stay reproducible.",
        )
        self.add_seed_arguments(parser)

    def add_seed_arguments(self, parser):
        """Override to add options of your own."""

    # -- hooks ------------------------------------------------------------

    def seed(self, **options) -> None:
        raise NotImplementedError

    def reset(self, **options) -> None:
        """Override when `--reset` should do something."""
        self.warn("--reset is not implemented for this seeder; nothing deleted.")

    # -- helpers ----------------------------------------------------------

    def require(self, model, command: str, *, filters: dict | None = None) -> list:
        """Fetch the rows a seeder depends on, or explain what to run first."""
        queryset = model.objects.all()
        if filters:
            queryset = queryset.filter(**filters)

        rows = list(queryset)
        if not rows:
            raise CommandError(
                f"No {model._meta.verbose_name_plural} found. "
                f"Run `python manage.py {command}` first "
                f"(or `python manage.py seed_all`)."
            )
        return rows

    def require_one(self, model, command: str, **filters):
        instance = model.objects.filter(**filters).first()
        if instance is None:
            raise CommandError(
                f"No {model._meta.verbose_name} matching {filters}. "
                f"Run `python manage.py {command}` first."
            )
        return instance

    def pick(self, population: Sequence):
        return self.rng.choice(population)

    def sample(self, population: Sequence, count: int) -> list:
        count = min(count, len(population))
        return self.rng.sample(list(population), count)

    def chance(self, probability: float) -> bool:
        return self.rng.random() < probability

    # -- output -----------------------------------------------------------

    def info(self, message: str) -> None:
        self.stdout.write(f"    {message}")

    def ok(self, message: str) -> None:
        self.stdout.write(self.style.SUCCESS(f"    {message}"))

    def warn(self, message: str) -> None:
        self.stdout.write(self.style.WARNING(f"    {message}"))

    def created(self, noun: str, count: int, *, skipped: int = 0) -> None:
        suffix = f", {skipped} already present" if skipped else ""
        self.ok(f"{count} {noun if count == 1 else plural(noun)} created{suffix}.")

    # -- entry point ------------------------------------------------------

    def handle(self, *args, **options):
        self.rng = random.Random(options["random_seed"])
        self.scale = max(1, options["scale"])

        label = self.seed_name or self.__class__.__module__.rsplit(".", 1)[-1]

        if self.feature is not None and not features.is_enabled(self.feature):
            self.warn(
                f"the '{self.feature}' feature is switched off for this "
                f"deployment; skipping {label}."
            )
            return

        self.stdout.write(self.style.MIGRATE_HEADING(f"Seeding {label}"))

        with transaction.atomic():
            if options["reset"]:
                self.reset(**options)
            self.seed(**options)


def plural(noun: str) -> str:
    """Just enough English to keep the seed output readable."""
    if noun.endswith("y") and not noun.endswith(("ay", "ey", "iy", "oy", "uy")):
        return noun[:-1] + "ies"
    if noun.endswith(("s", "x", "z", "ch", "sh")):
        return noun + "es"
    return noun + "s"


def cycle(values: Iterable):
    """Endless round-robin over `values` - handy for spreading demo data."""
    items = list(values)
    if not items:
        raise CommandError("cycle() needs at least one value")

    index = 0
    while True:
        yield items[index % len(items)]
        index += 1
