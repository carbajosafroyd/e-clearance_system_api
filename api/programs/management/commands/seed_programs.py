"""
Seed the degree programs each institute offers.

Which program belongs to which institute is declared in
`api/seeding/catalog.py` and keyed on the institute's **acronym** - `IC` gets
BSCS and BSIT, `CBAE` gets BSBA and BSA. Matching on the acronym rather than
on a word in the display name is what stops the second campus's "College of
Computing Education" from being handed the teacher-education programs, and it
survives somebody fixing a typo in a name.

An institute whose acronym is blank (one that predates the field) still gets
sensible programs: the catalogue falls back to a keyword in its name, and
then to a single generic program, so no institute is left with none.
"""

from __future__ import annotations

from api.institutes.models import Institute
from api.programs.models import Program
from api.seeding import catalog
from api.seeding.support import SeedCommand


class Command(SeedCommand):
    seed_name = "programs"
    help = "Create the degree programs declared for each institute."
    depends_on = ("seed_institutes",)

    def reset(self, **options):
        deleted, _ = Program.objects.all().delete()
        self.warn(f"deleted {deleted} program row(s) and everything under them")

    def seed(self, **options):
        institutes = self.require(Institute, "seed_institutes")

        created = 0
        for institute in institutes:
            specs = catalog.programs_for(
                institute.acronym, institute_name=institute.institute_name
            )

            for spec in specs:
                _, made = Program.objects.get_or_create(
                    name=spec.name,
                    institute=institute,
                    defaults={
                        "acronym": spec.acronym,
                        "status": Program.ProgramStatus.ACTIVE,
                    },
                )
                created += int(made)

            label = institute.acronym or institute.institute_name
            self.info(f"{label}: {', '.join(spec.acronym for spec in specs)}")

        self.created("program", created)
