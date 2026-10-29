"""
Bulk student invitations: the rules, the file parsing, and the send.

Two doors lead here - a JSON list pasted into the console
(`BulkUserInviteSerializer`) and an uploaded spreadsheet the students filled
in themselves (`StudentInviteFileView`) - and both must apply the SAME rules,
or a list that passes one door fails the other and nobody can say why. So
the rules live here, once:

  * the email must look like an address, must not belong to an existing
    account, and must appear once in the batch;
  * the student ID, when given, must belong to a student of the CALLER'S
    institute (an ID from another institute is reported as unknown, so the
    endpoint cannot be used to probe who studies elsewhere), must not already
    be linked to an account, and must appear once in the batch;
  * the caller must have an institute - the tokens carry it, and an
    invitation with no institute creates an account that belongs nowhere.

Re-inviting is allowed. A student who typed the wrong address the first
time is the ordinary case, so a new invitation for the same email or the
same student EXPIRES the earlier unused one rather than being refused
because of it. Only an accepted invitation (an account) blocks a new one.
"""
from __future__ import annotations

import csv
import io
import re
import threading
from dataclasses import dataclass, field

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from api.students.models import Student

from .models import UserRegistrationToken

User = get_user_model()

#: A spreadsheet bigger than this is almost certainly the wrong file. It is
#: also the ceiling on how many emails one click can queue.
MAX_ROWS = 1000

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

#: Header spellings accepted for each column, normalised to lower-case
#: alphanumerics. Students fill these files in, so "Student ID", "school id"
#: and "s_studentID" all have to mean the same thing.
EMAIL_HEADERS = {"email", "emailaddress", "email_address", "e_mail"}
STUDENT_ID_HEADERS = {
    "studentschoolid",
    "student_school_id",
    "studentid",
    "student_id",
    "sstudentid",
    "s_studentid",
    "schoolid",
    "school_id",
    "idnumber",
    "id_number",
}


@dataclass
class InviteRow:
    """One line of the batch, with whatever validation found wrong with it."""

    #: Where it came from, for the message: a spreadsheet row number (the
    #: header is row 1, so data starts at 2) or a 1-based position in a list.
    row: int
    email: str
    student_school_id: str
    errors: list[str] = field(default_factory=list)
    student: Student | None = None

    @property
    def student_name(self) -> str | None:
        if not self.student:
            return None
        return f"{self.student.s_lname}, {self.student.s_fname}".strip(", ")

    def as_dict(self) -> dict:
        return {
            "row": self.row,
            "email": self.email,
            "student_school_id": self.student_school_id,
            "student_name": self.student_name,
            "errors": list(self.errors),
        }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_rows(rows: list[InviteRow], institute) -> None:
    """
    Fill in `errors` (and `student`) on every row. Mutates in place.

    Three queries for the whole batch rather than two per row: the emails in
    one `IN`, the students in another, and nothing per row - a thousand-row
    file is one round trip each.
    """
    if institute is None:
        for row in rows:
            row.errors.append("Your account is not attached to an institute, so it cannot invite students.")
        return

    for row in rows:
        row.email = row.email.strip().lower()
        row.student_school_id = row.student_school_id.strip()

    # Within-batch duplicates first, so a duplicate is reported on the LATER
    # row and the first occurrence stays valid.
    seen_emails: dict[str, int] = {}
    seen_ids: dict[str, int] = {}
    for row in rows:
        if not row.email:
            row.errors.append("Missing email address.")
        elif not EMAIL_RE.match(row.email):
            row.errors.append("That does not look like an email address.")
        elif row.email in seen_emails:
            row.errors.append(f"Same email as row {seen_emails[row.email]}.")
        else:
            seen_emails[row.email] = row.row

        if row.student_school_id:
            if row.student_school_id in seen_ids:
                row.errors.append(f"Same student ID as row {seen_ids[row.student_school_id]}.")
            else:
                seen_ids[row.student_school_id] = row.row

    taken = set(
        email.lower()
        for email in User.objects.filter(email__in=list(seen_emails)).values_list("email", flat=True)
    )
    for row in rows:
        if row.email in taken:
            row.errors.append("An account already uses this email address.")

    students = {
        student.s_studentID: student
        for student in Student.objects.filter(
            s_studentID__in=list(seen_ids), program__institute=institute
        ).select_related("user")
    }
    for row in rows:
        if not row.student_school_id:
            continue
        student = students.get(row.student_school_id)
        if student is None:
            # Deliberately the same sentence for "does not exist" and
            # "exists in another institute".
            row.errors.append(f"No student with ID {row.student_school_id} in your institute.")
            continue
        if student.user_id is not None:
            row.errors.append(
                f"{student.s_lname}, {student.s_fname} already has an account ({student.user.username})."
            )
            continue
        row.student = student


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def _normalise_header(value) -> str:
    return re.sub(r"[^a-z0-9_]", "", str(value or "").strip().lower())


def _cell(value) -> str:
    """A spreadsheet cell as text. Numbers lose their trailing `.0`, since a
    ten-digit student ID typed into Excel comes back as a float."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _read_table(uploaded) -> list[list[str]]:
    """Every row of the first sheet (or the CSV) as a list of strings."""
    name = (getattr(uploaded, "name", "") or "").lower()

    if name.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        try:
            workbook = load_workbook(uploaded, read_only=True, data_only=True)
        except Exception as exc:  # noqa: BLE001 - openpyxl raises a zoo of types
            raise ValueError("That file could not be opened as an Excel workbook.") from exc
        sheet = workbook.worksheets[0]
        return [[_cell(v) for v in row] for row in sheet.iter_rows(values_only=True)]

    if name.endswith((".csv", ".txt")) or not name:
        raw = uploaded.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        return [[_cell(v) for v in row] for row in csv.reader(io.StringIO(text))]

    raise ValueError("Upload an Excel workbook (.xlsx) or a CSV file.")


def parse_invite_file(uploaded) -> list[InviteRow]:
    """
    Turn an upload into rows, or raise ValueError with a sentence for the user.

    The header row is found by name, not position, so a student who reorders
    the columns or adds a "Name" column does no harm. A file with no header
    at all is accepted when its first row contains an "@" - a bare list of
    addresses is a perfectly reasonable thing to upload.
    """
    table = [row for row in _read_table(uploaded) if any(cell for cell in row)]
    if not table:
        raise ValueError("The file has no rows in it.")

    header = [_normalise_header(cell) for cell in table[0]]
    email_col = next((i for i, h in enumerate(header) if h in EMAIL_HEADERS), None)
    id_col = next((i for i, h in enumerate(header) if h in STUDENT_ID_HEADERS), None)

    if email_col is None:
        if any("@" in cell for cell in table[0]):
            # Headerless: first column is the email, second the ID.
            email_col, id_col, data, first_row = 0, 1, table, 1
        else:
            raise ValueError(
                'The file needs an "email" column (and optionally a "student_school_id" column). '
                "Download the template to see the layout."
            )
    else:
        data, first_row = table[1:], 2

    if len(data) > MAX_ROWS:
        raise ValueError(f"That file has {len(data)} rows; the limit is {MAX_ROWS} per upload.")

    rows: list[InviteRow] = []
    for offset, cells in enumerate(data):
        email = cells[email_col] if email_col < len(cells) else ""
        student_id = cells[id_col] if id_col is not None and id_col < len(cells) else ""
        if not email and not student_id:
            continue
        rows.append(InviteRow(row=first_row + offset, email=email, student_school_id=student_id))

    if not rows:
        raise ValueError("The file has a header but no student rows under it.")
    return rows


def render_template_workbook() -> bytes:
    """
    The blank file students fill in. Two sheets: the table, and the rules in
    plain words - because the file travels without the person who knows them.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Students"
    sheet.append(["email", "student_school_id"])
    sheet.append(["juan.delacruz@dnsc.edu.ph", "2024000001"])
    sheet.append(["maria.santos@dnsc.edu.ph", "2024000002"])

    bold = Font(bold=True)
    fill = PatternFill("solid", fgColor="DDEBF7")
    for cell in sheet[1]:
        cell.font = bold
        cell.fill = fill
    sheet.column_dimensions["A"].width = 36
    sheet.column_dimensions["B"].width = 20
    # The ID column as text, so Excel does not turn 2024000001 into 2.024E+09.
    for row in range(2, 1002):
        sheet.cell(row=row, column=2).number_format = "@"

    notes = workbook.create_sheet("How to fill this in")
    for line in (
        "One student per row. Keep the two column headings exactly as they are.",
        "email - the address the invitation goes to. It must not already have an account.",
        "student_school_id - the 10-digit school ID on the student's ID card. Optional, but",
        "    with it the new account is linked to the student's record straight away.",
        "Delete the two example rows before sending the file back.",
        "The administrator uploads this file on the console's Invitations page; every row is",
        "    checked (unknown ID, ID from another institute, address already in use, duplicates)",
        "    before a single email is sent.",
    ):
        notes.append([line])
    notes.column_dimensions["A"].width = 100

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def create_student_invitations(rows: list[InviteRow], institute) -> list[UserRegistrationToken]:
    """
    Write the tokens for VALID rows and return them with `raw_password` set.

    Earlier unused invitations for the same email or the same student are
    expired first - see the module docstring for why re-inviting is allowed.
    The caller is responsible for emailing; the plaintext passwords live only
    on the returned objects.
    """
    # Lazy: `serializers` imports this module for validation.
    from .serializers import build_registration_token

    try:
        group = Group.objects.get(name="Students")
    except Group.DoesNotExist as exc:
        raise ValueError("Default student group is not configured.") from exc

    emails = [row.email for row in rows]
    student_ids = [row.student.pk for row in rows if row.student]

    tokens: list[UserRegistrationToken] = []
    # Usernames are reserved as we go, so two invitations in the same batch
    # built from the same email prefix cannot claim the same one.
    reserved: set[str] = set()
    for row in rows:
        token = build_registration_token(
            email=row.email,
            group=group,
            institute=institute,
            student=row.student,
            reserved_usernames=reserved,
        )
        reserved.add(token.username)
        tokens.append(token)

    with transaction.atomic():
        UserRegistrationToken.objects.filter(
            Q(email__in=emails) | Q(student_id__in=student_ids),
            used_at__isnull=True,
            expires_at__gt=timezone.now(),
        ).update(expires_at=timezone.now())
        created = UserRegistrationToken.objects.bulk_create(tokens)

    for created_token, source in zip(created, tokens):
        created_token.raw_password = getattr(source, "raw_password", "")

    return created


def send_invitations_in_background(tokens, inviter) -> None:
    """
    Render the emails now, while the one-time passwords are still in memory,
    and hand the finished messages to a thread. The request returns at once;
    "accepted for sending" is what a 201 means here.
    """
    from core.services.email_sender import email_sender
    from core.services.send_email_v2 import send_bulk_emails_async

    from .views import _invite_context

    # One mailbox for the whole batch - the inviter's institute's, or the
    # school's, or the platform's. Resolved once: it decrypts a password.
    sender = email_sender(inviter)

    messages = [
        {
            "subject": "Complete your account registration",
            "to_email": token.email,
            "template_name": "user_invite",
            "context": _invite_context(token, inviter),
            "sender": sender,
        }
        for token in tokens
    ]
    threading.Thread(target=send_bulk_emails_async, args=(messages,)).start()
