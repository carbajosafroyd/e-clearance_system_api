"""
Seed the institutes that belong to each school.

Which institute belongs to which campus is declared once, in
`api/seeding/catalog.py`: a school carries a number, an institute names that
number. This command turns the number into a real school by looking the
campus up on its acronym (`School.short_name`), so an institute lands under
the school it was declared for rather than under whichever school the
database happened to return first.

Re-running repairs as well as creates: an institute found under the wrong
school, or with a blank acronym, is moved and filled in. The acronym is not
decoration - `core.features` decides from it which institute may run the
contribution scheme, and `seed_users` builds its usernames out of it.
"""

from __future__ import annotations

from api.institutes.models import Institute
from api.schools.models import School
from api.seeding import catalog
from api.seeding.support import SeedCommand


class Command(SeedCommand):
    seed_name = "institutes"
    help = "Create the institutes declared for each school."
    depends_on = ("seed_schools",)

    def reset(self, **options):
        deleted, _ = Institute.objects.all().delete()
        self.warn(f"deleted {deleted} institute row(s) and everything under them")

    def seed(self, **options):
        self.require(School, "seed_schools")

        created = 0
        repaired = 0
        missing: list[str] = []

        for school_spec in catalog.campuses(self.scale):
            school = self._school_for(school_spec)
            if school is None:
                missing.append(f"{school_spec.acronym} ({school_spec.name})")
                continue

            for spec in catalog.institutes_for(school_spec):
                institute, made = Institute.objects.get_or_create(
                    institute_name=spec.name,
                    defaults={"school": school, "acronym": spec.acronym},
                )
                created += int(made)

                if not made:
                    repaired += int(self._realign(institute, school, spec))

                if made:
                    self.info(
                        f"{school.short_name or school.school_name} "
                        f"-> {spec.acronym} ({spec.name})"
                    )

        self.created("institute", created)
        if repaired:
            self.ok(f"{repaired} institute(s) re-attached or re-labelled.")
        if missing:
            # Not fatal: a campus can be missing because somebody deleted it,
            # and the rest of the dataset is still worth having. Silence would
            # leave a school with no institutes and no explanation.
            self.warn(
                "no school row for "
                + ", ".join(missing)
                + " - run `python manage.py seed_schools` first."
            )

    def _school_for(self, spec: catalog.SchoolSpec) -> School | None:
        """The campus a spec points at, by acronym and then by name."""
        return (
            School.objects.filter(short_name=spec.acronym).first()
            or School.objects.filter(school_name=spec.name).first()
        )

    def _realign(
        self, institute: Institute, school: School, spec: catalog.InstituteSpec
    ) -> bool:
        """Put an existing institute back under the school it was declared for."""
        changes: dict[str, object] = {}
        if institute.school_id != school.pk:
            changes["school"] = school
        if institute.acronym != spec.acronym:
            changes["acronym"] = spec.acronym

        if not changes:
            return False

        for field, value in changes.items():
            setattr(institute, field, value)
        institute.save(update_fields=list(changes))
        self.info(f"{institute.institute_name}: corrected {', '.join(sorted(changes))}")
        return True
