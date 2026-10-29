"""
Who exists in the seeded dataset: the schools, the institutes under each of
them, and the programs under each institute.

One file rather than three lists in three seeders, because the three are a
wiring diagram and not three independent lists. An institute only means
anything under a school and a program only under an institute, so the moment
the lists live apart they drift: `seed_institutes` hangs every institute off
whichever school happened to be created first, and every institute in the
database ends up under campus one.

The wiring is a number::

    SCHOOLS      numbered 1, 2, 3 ...
    INSTITUTES   name the school they belong to by that number
    PROGRAMS     name the institute they belong to by its acronym

**The numbers are not primary keys.** They are stable names for a row in
these tables, nothing more. Each seeder resolves a spec to a real row by its
natural key - a school by `short_name` (its acronym), an institute by its
acronym - so the numbering holds whatever ids the database handed out, in
whatever order, and re-running a seeder against a half-filled database
attaches everything to the same rows it did the first time.

Acronyms carry weight beyond display. `core.features.RESTRICTED_TO_ACRONYMS`
decides from `Institute.acronym` which institute may run the contribution
scheme, and `seed_users` builds its usernames from it (`IC` -> `icadmin`), so
an acronym here is a name other code matches on. Changing one is a rename
with consequences; adding one is free.

Adding a campus, institute or program is a line in the right tuple. Adding a
school means giving it the next number and listing its institutes against
that number - `seed_institutes` reports a school with no institutes rather
than quietly leaving it empty.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SchoolSpec:
    #: Stable name for this campus within this file. Institutes point at it.
    number: int
    name: str
    #: Stored as `School.short_name`, and what institutes are looked up by.
    acronym: str
    location: str


@dataclass(frozen=True)
class InstituteSpec:
    #: `SchoolSpec.number` of the campus this institute belongs to.
    school: int
    name: str
    #: Stored as `Institute.acronym`. Programs and usernames key on it.
    acronym: str


@dataclass(frozen=True)
class ProgramSpec:
    #: `InstituteSpec.acronym` of the institute that offers this program.
    institute: str
    name: str
    acronym: str


# -- schools ---------------------------------------------------------------

SCHOOLS: tuple[SchoolSpec, ...] = (
    SchoolSpec(
        number=1,
        name="Davao del Norte State College",
        acronym="DNSC",
        location="New Visayas, Panabo City, Davao del Norte",
    ),
    SchoolSpec(
        number=2,
        name="University of Mindanao",
        acronym="UM",
        location="Panabo City, Davao del Norte",
    ),
)

#: Every campus gets the same buildings; the locker and attendance seeders
#: only need somewhere to put a row.
BUILDINGS: tuple[tuple[str, int], ...] = (
    ("IC Building", 4),
    ("Academic Building", 4),
    ("Science Complex", 3),
    ("Student Center", 2),
)


# -- institutes ------------------------------------------------------------
INSTITUTES: tuple[InstituteSpec, ...] = (
    # 1 - Davao del Norte State College (Expanded & Authentic)
    InstituteSpec(school=1, name="Institute of Computing", acronym="IC"),
    InstituteSpec(school=1, name="Institute of Aquatic and Applied Sciences", acronym="IAAS"),
    InstituteSpec(school=1, name="Institute of Leadership, Entrepreneurship, and Good Governance", acronym="ILEGG"),
    InstituteSpec(school=1, name="Institute of Teacher Education", acronym="ITED"),
    
    # 2 - University of Mindanao (Minimized)
    InstituteSpec(school=2, name="College of Computing Education", acronym="CCE"),
)

# -- programs --------------------------------------------------------------

PROGRAMS: tuple[ProgramSpec, ...] = (
    # DNSC - Institute of Computing (IC)
    ProgramSpec("IC", "Bachelor of Science in Information Technology", "BSIT"),
    ProgramSpec("IC", "Bachelor of Science in Information Systems", "BSIS"),
    ProgramSpec("IC", "Bachelor of Science in Computer Science", "BSCS"),

    # DNSC - Institute of Aquatic and Applied Sciences (IAAS)
    ProgramSpec("IAAS", "Bachelor of Science in Agro-Forestry", "BSAF"),
    ProgramSpec("IAAS", "Bachelor of Science in Fisheries and Aquatic Sciences", "BSFAS"),
    ProgramSpec("IAAS", "Bachelor of Science in Food Technology", "BSFT"),
    ProgramSpec("IAAS", "Bachelor of Science in Marine Biology", "BSMB"),

    # DNSC - Institute of Leadership, Entrepreneurship, and Good Governance (ILEGG)
    ProgramSpec("ILEGG", "Bachelor of Public Administration", "BPA"),
    ProgramSpec("ILEGG", "Bachelor of Science in Disaster and Resilience Management", "BSDRM"),
    ProgramSpec("ILEGG", "Bachelor of Science in Entrepreneurship", "BSENTREP"),
    ProgramSpec("ILEGG", "Bachelor of Science in Social Work", "BSSW"),
    ProgramSpec("ILEGG", "Bachelor of Science in Tourism Management", "BSTM"),
    ProgramSpec("ILEGG", "Bachelor of Science in Criminology", "BSCRIM"),

    # DNSC - Institute of Teacher Education (ITED)
    ProgramSpec("ITED", "Bachelor of Elementary Education", "BEED"),
    ProgramSpec("ITED", "Bachelor of Secondary Education", "BSED"),
    ProgramSpec("ITED", "Bachelor of Arts in Communication", "BACOMM"),
    
    # UM - College of Computing Education (Minimized)
    ProgramSpec("CCE", "Bachelor of Science in Computer Science", "BSCS"),
    ProgramSpec("CCE", "Bachelor of Science in Information Technology", "BSIT"),
)

#: For an institute this file does not name - one that predates the catalogue,
#: or a generated satellite campus. A program list of nothing at all would
#: leave the student seeder with no programs to enrol anybody in.
FALLBACK_PROGRAMS: tuple[tuple[str, str], ...] = (
    ("Bachelor of Arts", "BA"),
)

#: Second chance for an institute whose acronym this file does not name: one
#: that predates `Institute.acronym` and still has a blank one, or a generated
#: satellite campus. Matched on a word in the display name, and only after the
#: acronym has been tried - the acronym is the key, this is what keeps those
#: two cases from getting nothing but Bachelor of Arts.
LEGACY_NAME_KEYWORDS: dict[str, str] = {
    "Computing": "IC",
    "Teacher": "ITE",
    "Accountancy": "IBA",
    "Business": "IBA",
}


# -- scale -----------------------------------------------------------------
#
# `--scale 1` is the dataset above: two real campuses, three institutes each.
# A higher scale adds satellite campuses on top, because one campus is enough
# to click through and several are what surfaces a cross-tenant leak. Each
# generated campus gets one institute of its own, named from its acronym so
# nothing collides with the declared ones.

#: Name and acronym suffix of the single institute a satellite campus gets.
SATELLITE_INSTITUTE = ("Institute of Computing", "IC")


def campuses(scale: int = 1) -> tuple[SchoolSpec, ...]:
    """Every school to seed at this scale, declared ones first."""
    generated = tuple(
        SchoolSpec(
            number=len(SCHOOLS) + offset,
            name=f"Satellite Campus {offset}",
            acronym=f"SAT{offset}",
            location=f"Barangay {offset}, Davao del Sur",
        )
        for offset in range(1, max(1, scale))
    )
    return SCHOOLS + generated


def institutes_for(school: SchoolSpec) -> tuple[InstituteSpec, ...]:
    """The institutes that belong to one campus.

    Declared campuses get their declared institutes. A generated satellite
    gets one, named after the campus, so that the schools a higher `--scale`
    creates are not empty shells.
    """
    declared = tuple(spec for spec in INSTITUTES if spec.school == school.number)
    if declared:
        return declared

    name, acronym = SATELLITE_INSTITUTE
    return (
        InstituteSpec(
            school=school.number,
            name=f"{name} ({school.acronym})",
            acronym=f"{school.acronym}{acronym}"[:12],
        ),
    )


def programs_for(acronym: str, *, institute_name: str = "") -> tuple[ProgramSpec, ...]:
    """The programs one institute offers, found by its acronym.

    Falls back to a keyword in the display name for an institute this file
    does not name - a blank acronym, or a generated satellite - and to
    `FALLBACK_PROGRAMS` when even that says nothing.
    """
    key = (acronym or "").strip().upper()
    matched = tuple(spec for spec in PROGRAMS if key and spec.institute == key)
    if matched:
        return matched

    name = institute_name.lower()
    for keyword, mapped in LEGACY_NAME_KEYWORDS.items():
        if keyword.lower() in name:
            return tuple(spec for spec in PROGRAMS if spec.institute == mapped)

    return tuple(
        ProgramSpec(key or acronym, program_name, program_acronym)
        for program_name, program_acronym in FALLBACK_PROGRAMS
    )
