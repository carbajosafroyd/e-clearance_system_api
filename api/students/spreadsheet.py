"""
Student records as a spreadsheet: export, blank template, and import.

One file format serves all three, and that is the point. An administrator
exports the institute's students, edits the workbook - fixes a surname,
moves a row from the *Enrolled* sheet to *Graduated* - and uploads the same
file back. Nothing has to be reshaped in between, because export writes
exactly the columns import reads.

## The columns

Named for the person editing them (`first_name`, `year_level`), not for
the database (`s_fname`, `s_lvl`). The database names are still accepted
on import, so a file made against the old CSV layout keeps working, and so
does a program given as a numeric id rather than an acronym.

    student_id   the 10-character school ID. The key: an existing record
                 with this ID is UPDATED, anything else is CREATED.
    rfid         the RFID card number, optional
    last_name / first_name / middle_name / suffix
    program      the program's ACRONYM (BSIT, BSCS) - one of the caller's
                 own institute's programs, listed on the Programs sheet
    year_level   1 to 5
    section      free text, "A" or "1-B"
    status       enrolled / graduated / dropped / to_be_updated; on a
                 workbook, blank means "the status of the sheet it is on"

Three more columns appear on export and are ignored on import, because
they belong to the account rather than the record: `has_account`,
`account_email`, `last_updated`.

## The workbook

    Summary      who exported it and when, counts by status / program /
                 year, and the rules below in plain words
    Programs     the acronyms this institute has, so the program column
                 can be a drop-down and a typo is caught in Excel
    Enrolled, Graduated, Dropped, Needs review
                 one sheet per status, same columns each

Splitting by status is what makes "graduate this year's batch" a cut and
paste rather than 300 edits to a status cell.

## Institute scoping

Everything is the CALLER'S institute: the export is the scoped list, the
programs offered are the institute's, and a `student_id` that exists in
another institute is refused as a row - it is neither updated nor
duplicated, and the message does not say which institute owns it.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from api.programs.models import Program
from api.students.models import Student

#: Sheet title per status, in the order they appear in the workbook.
STATUS_SHEETS: list[tuple[str, str]] = [
    ("Enrolled", Student.StudentStatus.ENROLLED),
    ("Graduated", Student.StudentStatus.GRADUATED),
    ("Dropped", Student.StudentStatus.DROPPED),
    ("Needs review", Student.StudentStatus.TO_BE_UPDATED),
]

#: What a person may type in the status column, normalised.
STATUS_ALIASES = {
    "enrolled": Student.StudentStatus.ENROLLED,
    "graduated": Student.StudentStatus.GRADUATED,
    "dropped": Student.StudentStatus.DROPPED,
    "to_be_updated": Student.StudentStatus.TO_BE_UPDATED,
    "tobeupdated": Student.StudentStatus.TO_BE_UPDATED,
    "to be updated": Student.StudentStatus.TO_BE_UPDATED,
    "needs review": Student.StudentStatus.TO_BE_UPDATED,
    "needsreview": Student.StudentStatus.TO_BE_UPDATED,
    "review": Student.StudentStatus.TO_BE_UPDATED,
}

STATUS_LABELS = {
    Student.StudentStatus.ENROLLED: "Enrolled",
    Student.StudentStatus.GRADUATED: "Graduated",
    Student.StudentStatus.DROPPED: "Dropped",
    Student.StudentStatus.TO_BE_UPDATED: "Needs review",
}

#: Sheets that are never read on import.
INFORMATION_SHEETS = {"summary", "programs", "how to use this file", "instructions"}

MAX_ROWS = 5000


@dataclass(frozen=True)
class Column:
    key: str
    """The spreadsheet header, and the key on an import row."""
    model_field: str | None
    """The Student field it maps to. None for `program`, which is resolved."""
    label: str
    required: bool = False
    help: str = ""
    width: int = 16


COLUMNS: list[Column] = [
    Column("student_id", "s_studentID", "Student ID", True, "10 characters, as on the ID card. The key for updates.", 14),
    Column("rfid", "s_rfid", "RFID", False, "The RFID card number. Leave blank if none.", 14),
    Column("last_name", "s_lname", "Last name", True, "", 20),
    Column("first_name", "s_fname", "First name", True, "", 20),
    Column("middle_name", "s_mname", "Middle name", False, "", 18),
    Column("suffix", "s_suffix", "Suffix", False, "Jr., III", 8),
    Column("program", None, "Program", True, "The acronym - see the Programs sheet.", 12),
    Column("year_level", "s_lvl", "Year level", True, "1 to 5", 10),
    Column("section", "s_set", "Section", False, "A, 1-B", 10),
    Column("status", "s_status", "Status", False, "enrolled, graduated, dropped or to_be_updated. Blank = the sheet's status.", 14),
]

EXPORT_ONLY_COLUMNS: list[tuple[str, str, int]] = [
    ("has_account", "Yes when the student has signed in to the platform", 12),
    ("account_email", "The address on that account", 30),
    ("last_updated", "", 20),
]

#: Header spellings accepted on import, normalised -> column key. Covers the
#: human labels, the database names, and the more likely misspellings.
HEADER_ALIASES: dict[str, str] = {
    "student_id": "student_id", "studentid": "student_id", "s_studentid": "student_id",
    "school_id": "student_id", "schoolid": "student_id", "id_number": "student_id",
    "rfid": "rfid", "s_rfid": "rfid",
    "last_name": "last_name", "lastname": "last_name", "s_lname": "last_name", "surname": "last_name",
    "first_name": "first_name", "firstname": "first_name", "s_fname": "first_name", "given_name": "first_name",
    "middle_name": "middle_name", "middlename": "middle_name", "s_mname": "middle_name",
    "suffix": "suffix", "s_suffix": "suffix",
    "program": "program", "program_acronym": "program", "acronym": "program", "course": "program",
    "year_level": "year_level", "yearlevel": "year_level", "s_lvl": "year_level", "year": "year_level", "level": "year_level",
    "section": "section", "s_set": "section", "set": "section",
    "status": "status", "s_status": "status",
}

COLUMN_BY_KEY = {column.key: column for column in COLUMNS}


def _normalise_header(value) -> str:
    return re.sub(r"[^a-z0-9_]", "", str(value or "").strip().lower().replace(" ", "_"))


def _cell(value) -> str:
    """A cell as text. A ten-digit ID Excel stored as a number comes back
    as `2024000001.0`; the `.0` is not part of anybody's ID."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value).strip()


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def build_student_workbook(students, institute, exported_by) -> bytes:
    """
    The institute's students as the workbook described above.

    `students` is the already-scoped, already-filtered queryset - the export
    matches what the screen shows, filters and search included, so
    "export what I am looking at" is literally that.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    rows = list(
        students.select_related("program", "user").order_by("s_lname", "s_fname")
    )
    programs = list(
        Program.objects.filter(institute=institute).order_by("acronym", "name")
    )

    workbook = Workbook()
    header_font = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="DDEBF7")
    title_font = Font(bold=True, size=14)

    # -- Summary ------------------------------------------------------------
    summary = workbook.active
    summary.title = "Summary"
    summary.column_dimensions["A"].width = 34
    summary.column_dimensions["B"].width = 18
    summary.column_dimensions["C"].width = 60

    exporter = (exported_by.get_full_name() or exported_by.username) if exported_by else ""
    summary.append([f"Students - {institute.institute_name}"])
    summary["A1"].font = title_font
    summary.append([institute.school.school_name if institute.school_id else ""])
    summary.append([f"Exported {timezone.localtime().strftime('%d %B %Y, %H:%M')} by {exporter}"])
    summary.append([f"{len(rows)} student(s) in this file"])
    summary.append([])

    def section(title, pairs):
        summary.append([title, "Students"])
        for cell in summary[summary.max_row]:
            cell.font = header_font
            cell.fill = header_fill
        for label, count in pairs:
            summary.append([label, count])
        summary.append([])

    by_status = {value: 0 for _, value in STATUS_SHEETS}
    by_program: dict[str, int] = {}
    by_year: dict[int, int] = {}
    for student in rows:
        by_status[student.s_status] = by_status.get(student.s_status, 0) + 1
        key = student.program.acronym or student.program.name
        by_program[key] = by_program.get(key, 0) + 1
        by_year[student.s_lvl] = by_year.get(student.s_lvl, 0) + 1

    section("By status", [(STATUS_LABELS.get(v, v), by_status.get(v, 0)) for _, v in STATUS_SHEETS])
    section("By program", sorted(by_program.items()))
    section("By year level", [(f"Year {year}", count) for year, count in sorted(by_year.items())])

    summary.append(["How to use this file"])
    summary[summary.max_row][0].font = header_font
    for line in HOW_TO_USE:
        summary.append([line])
    summary.append([])
    summary.append(["Column", "Required", "Meaning"])
    for cell in summary[summary.max_row]:
        cell.font = header_font
        cell.fill = header_fill
    for column in COLUMNS:
        summary.append([column.key, "yes" if column.required else "", column.help or column.label])
    for key, help_text, _ in EXPORT_ONLY_COLUMNS:
        summary.append([key, "", f"{help_text} (export only - ignored on import)".strip()])

    # -- Programs -----------------------------------------------------------
    programs_sheet = workbook.create_sheet("Programs")
    programs_sheet.append(["acronym", "program", "status"])
    for cell in programs_sheet[1]:
        cell.font = header_font
        cell.fill = header_fill
    for program in programs:
        programs_sheet.append([program.acronym, program.name, program.status])
    programs_sheet.column_dimensions["A"].width = 12
    programs_sheet.column_dimensions["B"].width = 48
    programs_sheet.column_dimensions["C"].width = 10

    # -- One sheet per status ----------------------------------------------
    headers = [column.key for column in COLUMNS] + [key for key, _, _ in EXPORT_ONLY_COLUMNS]
    widths = [column.width for column in COLUMNS] + [width for _, _, width in EXPORT_ONLY_COLUMNS]
    program_range = f"=Programs!$A$2:$A${max(2, len(programs) + 1)}"

    for title, status in STATUS_SHEETS:
        sheet = workbook.create_sheet(title)
        sheet.append(headers)
        for cell in sheet[1]:
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(vertical="center")
        for index, width in enumerate(widths, start=1):
            sheet.column_dimensions[get_column_letter(index)].width = width
        sheet.freeze_panes = "A2"

        for student in (s for s in rows if s.s_status == status):
            sheet.append([
                student.s_studentID,
                student.s_rfid or "",
                student.s_lname,
                student.s_fname,
                student.s_mname or "",
                student.s_suffix or "",
                student.program.acronym or "",
                student.s_lvl,
                student.s_set or "",
                student.s_status,
                "Yes" if student.user_id else "No",
                student.user.email if student.user_id else "",
                timezone.localtime(student.updated_at).strftime("%Y-%m-%d %H:%M") if student.updated_at else "",
            ])

        # Drop-downs, so a typo is caught in Excel rather than on upload.
        # Text format on the ID columns, so Excel does not turn a ten-digit
        # ID into 2.02E+09.
        last = max(sheet.max_row, 2)
        limit = max(last + 500, 1000)
        for row_index in range(2, limit + 1):
            sheet.cell(row=row_index, column=1).number_format = "@"
            sheet.cell(row=row_index, column=2).number_format = "@"

        program_validation = DataValidation(type="list", formula1=program_range, allow_blank=True)
        program_validation.error = "Choose one of the institute's program acronyms (see the Programs sheet)."
        program_validation.errorTitle = "Unknown program"
        sheet.add_data_validation(program_validation)
        program_validation.add(f"G2:G{limit}")

        status_validation = DataValidation(
            type="list", formula1='"' + ",".join(v for _, v in STATUS_SHEETS) + '"', allow_blank=True
        )
        status_validation.error = "enrolled, graduated, dropped or to_be_updated - or leave blank for this sheet's status."
        status_validation.errorTitle = "Unknown status"
        sheet.add_data_validation(status_validation)
        status_validation.add(f"J2:J{limit}")

        year_validation = DataValidation(type="whole", operator="between", formula1="1", formula2="5", allow_blank=True)
        year_validation.error = "Year level is a number from 1 to 5."
        year_validation.errorTitle = "Year level"
        sheet.add_data_validation(year_validation)
        year_validation.add(f"H2:H{limit}")

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


HOW_TO_USE = [
    "Each sheet after Programs holds the students with that status. Edit the cells, add rows at the bottom, delete rows you do not want.",
    "To change a student's status, move the row to the other sheet (cut and paste) or type the new status in the status column.",
    "Keep the student_id: it is how the upload knows which record to update. A new student_id creates a new record.",
    "program is the acronym from the Programs sheet. Only this institute's programs are accepted.",
    "has_account, account_email and last_updated are for reading; changes to them are ignored.",
    "Upload the finished file on the Students page (Import). Every row is checked first; the result says who was added, who was updated and what changed, and which rows were refused and why.",
    "A student whose record changes and who already has an account is told what changed, in their notifications.",
]


def build_import_template(institute) -> bytes:
    """The workbook with no students in it - the four status sheets, the
    Programs sheet and the Summary, ready to be filled in."""
    return build_student_workbook(Student.objects.none(), institute, None)


# ---------------------------------------------------------------------------
# Import: parsing
# ---------------------------------------------------------------------------


@dataclass
class ImportRow:
    """One line of an upload, its origin, and what happened to it."""

    sheet: str
    row: int
    values: dict[str, str]
    #: The status of the sheet the row came from, for a blank status cell.
    sheet_status: str | None = None
    errors: list[str] = field(default_factory=list)
    #: Filled in by `import_students`.
    student: Student | None = None
    outcome: str = ""  # created | updated | unchanged | failed
    changes: dict[str, dict[str, str]] = field(default_factory=dict)
    notified: bool = False

    @property
    def student_id(self) -> str:
        return self.values.get("student_id", "")

    @property
    def display_name(self) -> str:
        if self.student:
            return f"{self.student.s_lname}, {self.student.s_fname}".strip(", ")
        return f"{self.values.get('last_name', '')}, {self.values.get('first_name', '')}".strip(", ")

    def as_dict(self) -> dict:
        return {
            "sheet": self.sheet,
            "row": self.row,
            "student_id": self.student_id,
            "name": self.display_name,
            "outcome": self.outcome,
            "changes": self.changes,
            "notified": self.notified,
            "errors": list(self.errors),
        }


def _read_sheets(uploaded) -> list[tuple[str, list[list[str]]]]:
    """Every readable sheet as (title, rows). A CSV is one sheet called CSV."""
    name = (getattr(uploaded, "name", "") or "").lower()

    if name.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        try:
            workbook = load_workbook(uploaded, read_only=True, data_only=True)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("That file could not be opened as an Excel workbook.") from exc
        sheets = []
        for sheet in workbook.worksheets:
            if sheet.title.strip().lower() in INFORMATION_SHEETS:
                continue
            sheets.append((sheet.title, [[_cell(v) for v in row] for row in sheet.iter_rows(values_only=True)]))
        return sheets

    if name.endswith((".csv", ".txt")) or not name:
        raw = uploaded.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        return [("CSV", [[_cell(v) for v in row] for row in csv.reader(io.StringIO(text))])]

    raise ValueError("Upload an Excel workbook (.xlsx) or a CSV file.")


def parse_student_file(uploaded) -> list[ImportRow]:
    """
    Rows from every status sheet (or the CSV), keyed by column, or a
    ValueError with a sentence for the user.

    A sheet whose title is a status (`Enrolled`, `Needs review`) lends that
    status to any row with a blank status cell. A sheet with any other
    title is read too - somebody who renamed a sheet should not lose its
    rows - but its blank statuses have no default and are reported.
    """
    rows: list[ImportRow] = []
    total = 0

    for title, table in _read_sheets(uploaded):
        table = [row for row in table if any(cell for cell in row)]
        if not table:
            continue

        header = [HEADER_ALIASES.get(_normalise_header(cell)) for cell in table[0]]
        if "student_id" not in header:
            # A sheet without the key column is not a student sheet.
            # Skipped rather than fatal: the Programs sheet is one of these,
            # and a renamed information sheet would be too.
            continue

        sheet_status = STATUS_ALIASES.get(title.strip().lower())

        for offset, cells in enumerate(table[1:], start=2):
            values: dict[str, str] = {}
            for index, key in enumerate(header):
                if key is None or index >= len(cells):
                    continue
                values[key] = cells[index]
            if not any(values.values()):
                continue
            rows.append(ImportRow(sheet=title, row=offset, values=values, sheet_status=sheet_status))
            total += 1
            if total > MAX_ROWS:
                raise ValueError(f"That file has more than {MAX_ROWS} student rows; split it and upload in parts.")

    if not rows:
        raise ValueError(
            "No student rows were found. The file needs a header row with at least "
            "student_id, last_name, first_name, program and year_level - download the "
            "template or an export to see the layout."
        )
    return rows


# ---------------------------------------------------------------------------
# Import: validation and writing
# ---------------------------------------------------------------------------

_UPDATABLE = ["s_rfid", "s_lname", "s_fname", "s_mname", "s_suffix", "program_id", "s_lvl", "s_set", "s_status"]

FIELD_LABELS = {
    "s_rfid": "RFID",
    "s_lname": "Last name",
    "s_fname": "First name",
    "s_mname": "Middle name",
    "s_suffix": "Suffix",
    "program_id": "Program",
    "s_lvl": "Year level",
    "s_set": "Section",
    "s_status": "Status",
}


def _clean(value: str | None) -> str | None:
    """The CSV convention this API has always used: NULL / NONE / N/A / blank mean 'no value'."""
    if value is None:
        return None
    value = value.strip()
    if value.upper() in ("", "NULL", "NONE", "NA", "N/A"):
        return None
    return value


def import_students(rows: list[ImportRow], institute, actor) -> dict:
    """
    Validate every row, then create or update in one transaction, then tell
    the students whose records changed. Returns the result payload.

    Nothing is written if a row is invalid? No - a valid row is not held
    hostage by an unrelated bad one. Invalid rows are reported and skipped;
    the rest go through. That matches how the old CSV import behaved and
    how people use it: fix the two rejected rows, upload again, and the
    rest are simply updated in place a second time (to no effect).
    """
    programs = list(Program.objects.filter(institute=institute))
    by_name = {p.name.strip().upper(): p for p in programs}
    by_id = {p.pk: p for p in programs}

    # An acronym two programs share cannot pick one. It is excluded from the
    # lookup and reported, so the row is refused with a reason rather than
    # landing on whichever program sorted first.
    by_acronym: dict[str, Program] = {}
    ambiguous: set[str] = set()
    for program in programs:
        key = program.acronym.strip().upper()
        if not key:
            continue
        if key in by_acronym:
            ambiguous.add(key)
        by_acronym[key] = program
    for key in ambiguous:
        by_acronym.pop(key, None)

    # -- normalise and validate each row on its own -----------------------
    for row in rows:
        v = row.values
        student_id = (_clean(v.get("student_id")) or "").replace(" ", "")
        v["student_id"] = student_id
        if not student_id:
            row.errors.append("Missing student_id.")
        elif len(student_id) != 10:
            row.errors.append(f"student_id must be exactly 10 characters ({len(student_id)} given).")

        for key in ("last_name", "first_name"):
            if not _clean(v.get(key)):
                row.errors.append(f"Missing {key}.")

        program_value = _clean(v.get("program"))
        program = None
        if not program_value:
            row.errors.append("Missing program - use the acronym from the Programs sheet.")
        else:
            program = by_acronym.get(program_value.upper()) or by_name.get(program_value.upper())
            if program is None and program_value.isdigit():
                # The old CSV layout sent the program's numeric id.
                program = by_id.get(int(program_value))
            if program is None and program_value.upper() in ambiguous:
                row.errors.append(
                    f"Two of your institute's programs share the acronym '{program_value}' - "
                    "write the full program name instead."
                )
            elif program is None:
                row.errors.append(f"Unknown program '{program_value}' - it is not one of your institute's programs.")
        row.values["_program"] = program  # type: ignore[assignment]

        level_value = _clean(v.get("year_level"))
        level = None
        if level_value is None:
            row.errors.append("Missing year_level.")
        else:
            try:
                level = int(float(level_value))
            except ValueError:
                row.errors.append(f"year_level '{level_value}' is not a number.")
            else:
                if not 1 <= level <= 5:
                    row.errors.append("year_level must be between 1 and 5.")
        row.values["_level"] = level  # type: ignore[assignment]

        status_value = _clean(v.get("status"))
        status = None
        if status_value:
            status = STATUS_ALIASES.get(status_value.strip().lower().replace("-", "_"))
            if status is None:
                row.errors.append(f"Unknown status '{status_value}' - enrolled, graduated, dropped or to_be_updated.")
        elif row.sheet_status:
            status = row.sheet_status
        else:
            status = Student.StudentStatus.ENROLLED
        row.values["_status"] = status  # type: ignore[assignment]

        rfid = _clean(v.get("rfid"))
        if rfid:
            rfid = rfid.replace(" ", "")
            if len(rfid) != 10:
                row.errors.append(f"rfid must be exactly 10 characters ({len(rfid)} given).")
        row.values["_rfid"] = rfid  # type: ignore[assignment]

    # -- duplicates within the file --------------------------------------
    seen_ids: dict[str, ImportRow] = {}
    seen_rfids: dict[str, ImportRow] = {}
    for row in rows:
        if row.student_id:
            first = seen_ids.get(row.student_id)
            if first:
                row.errors.append(f"Same student_id as {first.sheet} row {first.row}.")
            else:
                seen_ids[row.student_id] = row
        rfid = row.values.get("_rfid")
        if rfid:
            first = seen_rfids.get(rfid)
            if first:
                row.errors.append(f"Same rfid as {first.sheet} row {first.row}.")
            else:
                seen_rfids[rfid] = row

    # -- what exists already ----------------------------------------------
    existing = {
        s.s_studentID: s
        for s in Student.objects.filter(s_studentID__in=list(seen_ids)).select_related("program", "user")
    }
    taken_rfids = {
        s.s_rfid: s
        for s in Student.objects.filter(s_rfid__in=list(seen_rfids)).select_related("program")
    }

    for row in rows:
        current = existing.get(row.student_id)
        if current is not None and current.program.institute_id != institute.pk:
            # Neither updated nor duplicated. The wording does not name the
            # other institute.
            row.errors.append("This student_id belongs to a student outside your institute.")
            continue
        rfid = row.values.get("_rfid")
        if rfid:
            owner = taken_rfids.get(rfid)
            if owner is not None and (current is None or owner.pk != current.pk):
                row.errors.append("This rfid is already assigned to another student.")
        row.student = current

    # -- build the writes --------------------------------------------------
    now = timezone.now()
    to_create: list[tuple[ImportRow, Student]] = []
    to_update: list[tuple[ImportRow, Student]] = []

    for row in rows:
        if row.errors:
            row.outcome = "failed"
            continue

        v = row.values
        desired = {
            "s_rfid": v.get("_rfid"),
            "s_lname": _clean(v.get("last_name")),
            "s_fname": _clean(v.get("first_name")),
            "s_mname": _clean(v.get("middle_name")),
            "s_suffix": _clean(v.get("suffix")),
            "program_id": v["_program"].pk,  # type: ignore[union-attr]
            "s_lvl": v["_level"],
            "s_set": _clean(v.get("section")),
            "s_status": v["_status"],
        }

        if row.student is None:
            student = Student(s_studentID=row.student_id, **desired)
            student.created_by = actor
            student.updated_by = actor
            to_create.append((row, student))
            row.student = student
            row.outcome = "created"
            continue

        student = row.student
        for fieldname, new_value in desired.items():
            old_value = getattr(student, fieldname)
            if _same(old_value, new_value):
                continue
            row.changes[FIELD_LABELS[fieldname]] = {
                "from": _describe(fieldname, old_value, by_id),
                "to": _describe(fieldname, new_value, by_id),
            }
            setattr(student, fieldname, new_value)

        if not row.changes:
            row.outcome = "unchanged"
            continue

        student.updated_by = actor
        student.updated_at = now
        to_update.append((row, student))
        row.outcome = "updated"

    with transaction.atomic():
        if to_create:
            Student.objects.bulk_create([s for _, s in to_create], batch_size=500)
        if to_update:
            Student.objects.bulk_update(
                [s for _, s in to_update], _UPDATABLE + ["updated_by", "updated_at"], batch_size=500
            )

    # -- tell the students whose records changed --------------------------
    for row, student in to_update:
        if student.user_id and student.user is not None:
            _notify_student(student, row.changes, actor)
            row.notified = True

    created = [r for r in rows if r.outcome == "created"]
    updated = [r for r in rows if r.outcome == "updated"]
    unchanged = [r for r in rows if r.outcome == "unchanged"]
    failed = [r for r in rows if r.outcome == "failed"]

    return {
        "total_rows": len(rows),
        "created_count": len(created),
        "updated_count": len(updated),
        "unchanged_count": len(unchanged),
        "errors_count": len(failed),
        "notified_count": sum(1 for r in updated if r.notified),
        "created": [r.as_dict() for r in created],
        "updated": [r.as_dict() for r in updated],
        "unchanged": [r.as_dict() for r in unchanged],
        "failed": [r.as_dict() for r in failed],
        # The shape the first version of this endpoint answered with, kept
        # for anything else that still reads it.
        "created_students": [r.display_name for r in created],
        "updated_students": [r.display_name for r in updated],
        "errors": [{"row": r.row, "sheet": r.sheet, "errors": " ".join(r.errors)} for r in failed],
    }


def _same(old, new) -> bool:
    return (old if old not in ("", None) else None) == (new if new not in ("", None) else None)


def _describe(fieldname: str, value, programs_by_id: dict) -> str:
    if value in (None, ""):
        return ""
    if fieldname == "program_id":
        program = programs_by_id.get(value)
        if not program:
            return str(value)
        # Acronym AND name: two programs can share an acronym, and "OTOT ->
        # OTOT" tells the reader nothing about which is which.
        return f"{program.acronym} ({program.name})" if program.acronym else program.name
    if fieldname == "s_status":
        return STATUS_LABELS.get(value, str(value))
    return str(value)


def _notify_student(student: Student, changes: dict[str, dict[str, str]], actor) -> None:
    """
    An in-app notification saying what changed on their record.

    Sent because the record is THEIRS: a changed year level moves which
    fees they are charged, a changed status can stop them signing in to
    things, and finding that out from a bill is worse than a line in their
    notifications. The notification service is what the other systems use,
    so it arrives wherever they already look.
    """
    from api.notifications.models import Notification
    from api.notifications.service import notify_user

    lines = [
        f"{label}: {change['from'] or '(blank)'} -> {change['to'] or '(blank)'}"
        for label, change in changes.items()
    ]
    who = (actor.get_full_name() or actor.username) if actor else "an administrator"
    try:
        notify_user(
            user=student.user,
            title="Your student record was updated",
            message=(
                f"{who} updated your student record ({student.s_studentID}):\n"
                + "\n".join(lines)
                + "\n\nIf any of this is wrong, tell your institute's office."
            ),
            type=Notification.NotificationType.SYSTEM_ANNOUNCEMENT,
            source=Notification.NotificationSource.SYSTEM,
            sender=actor,
            data={"student_id": student.s_studentID, "changes": changes},
        )
    except Exception:  # noqa: BLE001 - a failed notice must not fail the import
        pass
