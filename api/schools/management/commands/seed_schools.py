"""
Seed schools and their buildings.

Which campuses exist is declared in `api/seeding/catalog.py`, alongside the
institutes that belong to each one, so a school and its institutes cannot
drift apart. This command only writes the rows.

A school is matched on its acronym (`short_name`), which is also what
`seed_institutes` looks it up by - so re-running after somebody has corrected
a display name updates that name rather than creating a second campus.
"""

from __future__ import annotations

from api.schools.models import School, SchoolBuilding
from api.seeding import catalog
from api.seeding.support import SeedCommand


class Command(SeedCommand):
    seed_name = "schools"
    help = "Create the demo schools and their buildings."

    def reset(self, **options):
        deleted, _ = School.objects.all().delete()
        self.warn(f"deleted {deleted} school row(s) and everything under them")

    def seed(self, **options):
        created_schools = 0
        updated_schools = 0
        created_buildings = 0

        for spec in catalog.campuses(self.scale):
            school = School.objects.filter(short_name=spec.acronym).first()

            if school is None:
                # No acronym match: either the campus is new, or it was
                # seeded before acronyms were filled in. The name is the
                # other natural key, so try it before creating a duplicate.
                school, created = School.objects.get_or_create(
                    school_name=spec.name,
                    defaults={
                        "short_name": spec.acronym,
                        "location": spec.location,
                    },
                )
                created_schools += int(created)
            else:
                created = False

            if not created:
                changes = {}
                if school.school_name != spec.name:
                    changes["school_name"] = spec.name
                if school.short_name != spec.acronym:
                    changes["short_name"] = spec.acronym
                if changes:
                    for field, value in changes.items():
                        setattr(school, field, value)
                    school.save(update_fields=list(changes))
                    updated_schools += 1
                    self.info(
                        f"{school.school_name}: corrected "
                        f"{', '.join(sorted(changes))}"
                    )

            for building_name, max_floor in catalog.BUILDINGS:
                _, made = SchoolBuilding.objects.get_or_create(
                    school=school,
                    name=building_name,
                    defaults={"max_floor": max_floor},
                )
                created_buildings += int(made)

            self.info(f"{spec.number}. {spec.acronym} - {school.school_name}")

        self.created("school", created_schools)
        self.created("building", created_buildings)
        if updated_schools:
            self.ok(f"{updated_schools} school row(s) brought back in line.")
