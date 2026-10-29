from __future__ import annotations

import hashlib
import os
import re
from collections import defaultdict
from decimal import Decimal
from typing import Any, Mapping

from django.core.cache import cache
from django.db.models import Q

from api.clearance.bridge_errors import ClearanceBridgeError
from api.clearance.services.n8n_gateway import N8nGateway
from api.collection_categories.models import CollectionCategory
from api.fees.models import Fee
from api.programs.models import Program
from api.students.models import Student
from api.users.models import User


def text(value: Any) -> str:
    return str(value or "").strip()


def lower(value: Any) -> str:
    return text(value).lower()


def upper(value: Any) -> str:
    return text(value).upper()


def normalized_financial_name(value: Any) -> str:
    raw = upper(value)
    raw = re.sub(r"^FIN[_\- ]*", "", raw)
    return re.sub(r"[^A-Z0-9]", "", raw)


def year_label(level: Any) -> str:
    try:
        number = int(level)
    except (TypeError, ValueError):
        return text(level) or "Unknown Year"
    return {1: "First Year", 2: "Second Year", 3: "Third Year", 4: "Fourth Year"}.get(number, f"Year {number}")


def set_code(program: Any, level: Any, raw_set: Any) -> str:
    program_code = upper(program)
    try:
        year = str(int(level))
    except (TypeError, ValueError):
        year = ""
    raw = upper(raw_set)
    direct = re.sub(r"\s+", "-", raw)
    if program_code and direct.startswith(program_code + "-"):
        return direct
    compact = re.sub(r"[^A-Z0-9]", "", raw)
    if program_code and compact.startswith(program_code):
        compact = compact[len(program_code):]
    if year and compact.startswith(year):
        compact = compact[len(year):]
    if program_code:
        return f"{program_code}-{year}{compact}" if year else f"{program_code}-{compact}"
    return direct or compact


def full_name(student: Student | None) -> str:
    if student is None:
        return ""
    return " ".join(
        part
        for part in [
            text(getattr(student, "s_fname", "")),
            text(getattr(student, "s_mname", "")),
            text(getattr(student, "s_lname", "")),
            text(getattr(student, "s_suffix", "")),
        ]
        if part
    )


def user_full_name(user: User | None) -> str:
    if user is None:
        return ""
    return " ".join(filter(None, [text(getattr(user, "first_name", "")), text(getattr(user, "last_name", ""))]))


def _number(value: Any) -> float:
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _hash_int(value: Any) -> int:
    digest = hashlib.sha256(repr(value).encode()).digest()
    return int.from_bytes(digest[:4], "big") or 1


def _bool_query(value: Any) -> bool:
    return lower(value) in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        return default


def _cache_get_or_load(key: str, ttl: int, loader, force_refresh: bool = False):
    if not force_refresh:
        cached = cache.get(key)
        if cached is not None:
            return cached
    value = loader()
    cache.set(key, value, max(1, ttl))
    return value


class ClearanceAdapterCompatService:
    def __init__(self) -> None:
        self.n8n = N8nGateway()
        self.identity_ttl = _env_int("CLMS_IDENTITY_CACHE_SECONDS", 60)
        self.student_ttl = _env_int("CLMS_STUDENT_CACHE_SECONDS", 300)
        self.category_ttl = _env_int("CLMS_COLLECTION_CATEGORY_CACHE_SECONDS", 600)
        self.fee_ttl = _env_int("CLMS_COLLECTION_FEE_CACHE_SECONDS", 30)

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        try:
            return int(value) if str(value or "").strip() else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _first_int(*values: Any) -> int | None:
        for value in values:
            parsed = ClearanceAdapterCompatService._optional_int(value)
            if parsed is not None:
                return parsed
        return None

    def _institute_id_from_payload(self, payload: Mapping[str, Any] | None) -> int | None:
        payload = payload if isinstance(payload, Mapping) else {}
        active = payload.get("active_assignment") if isinstance(payload.get("active_assignment"), Mapping) else {}
        assignment = payload.get("assignment") if isinstance(payload.get("assignment"), Mapping) else {}
        values = (
            payload.get("institute_id"),
            payload.get("instituteId"),
            active.get("institute_id"),
            active.get("instituteId"),
            assignment.get("institute_id"),
            assignment.get("instituteId"),
        )
        institute_ids = set()
        for value in values:
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value).strip()) or int(value) <= 0:
                raise ClearanceBridgeError(502, "INSTITUTE_CONTEXT_INVALID", "The CLMS context returned an invalid institute identifier.")
            institute_ids.add(int(value))
        if len(institute_ids) > 1:
            raise ClearanceBridgeError(409, "INSTITUTE_CONTEXT_MISMATCH", "The CLMS context contains conflicting institute identifiers.")
        return next(iter(institute_ids), None)

    def _session_email(self, payload: Mapping[str, Any] | None) -> str:
        payload = payload if isinstance(payload, Mapping) else {}

        user = payload.get("user") if isinstance(payload.get("user"), Mapping) else {}
        identity = payload.get("identity") if isinstance(payload.get("identity"), Mapping) else {}
        session = payload.get("session") if isinstance(payload.get("session"), Mapping) else {}
        active = payload.get("active_assignment") if isinstance(payload.get("active_assignment"), Mapping) else {}
        assignment = payload.get("assignment") if isinstance(payload.get("assignment"), Mapping) else {}

        for value in (
            payload.get("email"),
            payload.get("session_email"),
            user.get("email"),
            identity.get("email"),
            session.get("email"),
            session.get("session_email"),
            active.get("email"),
            assignment.get("email"),
        ):
            email = lower(value)
            if email:
                return email

        return ""

    def _institute_id_from_dnsc_identity(self, payload: Mapping[str, Any] | None) -> int | None:
        email = self._session_email(payload)
        if not email:
            return None

        user = (
            User.objects
            .filter(email__iexact=email, is_active=True)
            .only("id", "institute_id")
            .first()
        )
        if user is not None and user.institute_id is not None:
            return int(user.institute_id)

        student = (
            Student.objects
            .select_related("program")
            .filter(user__email__iexact=email, user__is_active=True)
            .first()
        )
        if (
            student is not None
            and getattr(student, "program", None) is not None
            and student.program.institute_id is not None
        ):
            return int(student.program.institute_id)

        return None

    def _required_institute_id(self, payload: Mapping[str, Any] | None) -> int:
        assignment_institute_id = self._institute_id_from_payload(payload)
        dnsc_institute_id = self._institute_id_from_dnsc_identity(payload)

        if (
            assignment_institute_id is not None
            and dnsc_institute_id is not None
            and assignment_institute_id != dnsc_institute_id
        ):
            raise ClearanceBridgeError(
                409,
                "INSTITUTE_CONTEXT_MISMATCH",
                "The CLMS assignment institute does not match the authenticated DNSC account.",
            )

        institute_id = dnsc_institute_id or assignment_institute_id
        if institute_id is None:
            raise ClearanceBridgeError(
                409,
                "INSTITUTE_CONTEXT_REQUIRED",
                "The authenticated DNSC account is not associated with an institute.",
            )

        return institute_id

    def _scope_programs(self, queryset, institute_id: int | None):
        if institute_id is not None:
            queryset = queryset.filter(institute_id=institute_id)
        return queryset

    def _scope_students(self, queryset, institute_id: int | None):
        if institute_id is not None:
            queryset = queryset.filter(program__institute_id=institute_id)
        return queryset

    def _scope_users(self, queryset, institute_id: int | None):
        if institute_id is not None:
            queryset = queryset.filter(Q(institute_id=institute_id) | Q(student__program__institute_id=institute_id))
        return queryset.distinct()

    def _scope_categories(self, queryset, institute_id: int | None):
        if institute_id is not None:
            queryset = queryset.filter(institute_id=institute_id)
        return queryset

    def _scope_fees(self, queryset, institute_id: int | None):
        if institute_id is not None:
            queryset = queryset.filter(student__program__institute_id=institute_id)
        return queryset

    @staticmethod
    def _permissions(active: Mapping[str, Any]) -> set[str]:
        values = active.get("permissions")
        if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
            raise ClearanceBridgeError(502, "SESSION_CONTEXT_INVALID", "The active assignment returned an invalid permission list.")
        return {upper(value) for value in values}

    def admin_session(self, request, required_permissions: set[str]) -> dict[str, Any]:
        session = self.n8n.validate_session(request)
        active = session.get("active_assignment")
        if not isinstance(active, dict):
            raise ClearanceBridgeError(403, "ADMIN_ASSIGNMENT_REQUIRED", "Select an active administrator assignment and try again.")
        if active.get("is_signatory") is not False:
            raise ClearanceBridgeError(403, "ADMIN_ASSIGNMENT_REQUIRED", "This resource requires an administrator assignment.")
        permissions = self._permissions(active)
        if required_permissions and not (permissions & required_permissions):
            raise ClearanceBridgeError(403, "PERMISSION_DENIED", "Your active administrator role cannot access this institutional data.")
        return session

    def signee_session(self, request) -> dict[str, Any]:
        session = self.n8n.validate_session(request)
        active = session.get("active_assignment")
        if not isinstance(active, dict) or active.get("is_signatory") is not True:
            raise ClearanceBridgeError(403, "SIGNEE_ASSIGNMENT_REQUIRED", "Select an active signee assignment and try again.")
        permissions = self._permissions(active)
        if not {"QUEUE_VIEW", "CLEARANCE_VIEW"} <= permissions:
            raise ClearanceBridgeError(403, "PERMISSION_DENIED", "Your active position cannot view signee queues.")
        return session

    def resolve_identity(self, email: str, *, force_refresh: bool = False) -> dict[str, Any]:
        normalized = lower(email)
        if len(normalized) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
            raise ClearanceBridgeError(422, "EMAIL_INVALID", "Enter a valid institutional email address.")

        def load():
            # Identity resolution happens before a CLMS session exists, so it cannot
            # be scoped from an active assignment. Resolve the unique institutional
            # email first, then return the institute attached to that DNSC identity.
            user = (
                User.objects.filter(email__iexact=normalized, is_active=True)
                .prefetch_related("groups")
                .first()
            )
            student = (
                Student.objects.select_related("user", "program", "program__institute")
                .filter(user__email__iexact=normalized, user__is_active=True)
                .first()
            )

            if user is None and student is None:
                return {
                    "exists": False,
                    "email": normalized,
                    "identity_type": None,
                    "institute_id": None,
                    "source_system": "DNSC_SYSTEMS_API",
                    "read_only": True,
                }

            source_user = user or getattr(student, "user", None)
            group = source_user.groups.first() if source_user is not None else None
            identity_name = user_full_name(source_user)
            institute_id = self._first_int(
                getattr(source_user, "institute_id", None) if source_user is not None else None,
                getattr(getattr(student, "program", None), "institute_id", None) if student is not None else None,
            )

            if student is not None:
                program = upper(getattr(student.program, "name", ""))
                return {
                    "exists": True,
                    "email": normalized,
                    "identity_type": "STUDENT",
                    "user_id": f"DNSC-USER-{source_user.id}" if source_user is not None else None,
                    "full_name": full_name(student) or identity_name,
                    "group_name": group.name if group else None,
                    "institute_id": institute_id,
                    "student": {
                        "student_id": f"DNSC-{student.id}",
                        "student_number": text(student.s_studentID),
                        "program": program,
                        "year_level": year_label(student.s_lvl),
                        "set_code": set_code(program, student.s_lvl, student.s_set),
                        "enrollment_status": upper(student.s_status),
                    },
                    "source_system": "DNSC_SYSTEMS_API",
                    "read_only": True,
                }

            return {
                "exists": True,
                "email": normalized,
                "identity_type": "USER",
                "user_id": f"DNSC-USER-{user.id}",
                "full_name": identity_name,
                "group_name": group.name if group else None,
                "institute_id": institute_id,
                "student": None,
                "source_system": "DNSC_SYSTEMS_API",
                "read_only": True,
            }

        return _cache_get_or_load(f"clms:identity:{normalized}", self.identity_ttl, load, force_refresh)

    def list_students(self, request, *, force_refresh: bool = False) -> dict[str, Any]:
        session = self.admin_session(request, {"USER_MANAGE", "STUDENT_IMPORT", "CLEARANCE_VIEW_ALL"})
        institute_id = self._required_institute_id(session)

        def load():
            programs = self._scope_programs(Program.objects.filter(status="active"), institute_id)
            program_ids = list(programs.values_list("id", flat=True))
            rows = (
                Student.objects.select_related("program", "user")
                .filter(program_id__in=program_ids, s_status="enrolled")
                .order_by("program__name", "s_lvl", "s_set", "s_lname", "s_fname")
            )
            students = []
            seen = set()
            for row in rows:
                student_number = text(row.s_studentID)
                if not student_number or upper(student_number) in seen:
                    continue
                seen.add(upper(student_number))
                program = upper(row.program.name)
                students.append({
                    "student_id": f"DNSC-{row.id}",
                    "student_number": student_number,
                    "full_name": full_name(row),
                    "email": lower(getattr(row.user, "email", "")) if row.user_id else "",
                    "academic_period": "",
                    "program": program,
                    "year_level": year_label(row.s_lvl),
                    "set_code": set_code(program, row.s_lvl, row.s_set),
                    "enrollment_status": "ACTIVE",
                })
            return students

        key = f"clms:students:{institute_id or 'all'}"
        students = _cache_get_or_load(key, self.student_ttl, load, force_refresh)
        active = session["active_assignment"]
        if not all(text(active.get(field)) for field in ("program_scope", "year_scope", "set_scope")):
            raise ClearanceBridgeError(502, "SESSION_CONTEXT_INVALID", "The active assignment is missing its student scope.")
        students = [row for row in students if self._assignment_scope_allows(active, row["program"], row["year_level"], row["set_code"])]
        return {"students": students, "source_system": "DNSC_SYSTEMS_API", "read_only": True, "count": len(students)}

    def categories(self, request, *, force_refresh: bool = False) -> dict[str, Any]:
        session = self.admin_session(request, {"TEMPLATE_MANAGE", "PERIOD_MANAGE", "CLEARANCE_VIEW_ALL"})
        institute_id = self._required_institute_id(session)
        return self._categories_for_institute(institute_id, force_refresh=force_refresh)

    def _categories_for_institute(self, institute_id: int | None, *, force_refresh: bool = False) -> dict[str, Any]:
        def load():
            rows = self._scope_categories(CollectionCategory.objects.all(), institute_id).order_by("category_name", "id")
            return [
                {
                    "id": str(row.id),
                    "category_name": text(row.category_name),
                    "collection_fee": text(row.collection_fee),
                    "description": text(row.description) or None,
                }
                for row in rows
                if text(row.category_name)
            ]

        key = f"clms:collection-categories:{institute_id or 'all'}"
        categories = _cache_get_or_load(key, self.category_ttl, load, force_refresh)
        return {"categories": categories, "source_system": "DNSC_COLLECTION", "read_only": True, "count": len(categories)}

    def current_categories(self, request, *, force_refresh: bool = False) -> dict[str, Any]:
        context = self._context(request, force_refresh=force_refresh)
        institute_id = self._required_institute_id(context)
        catalog = self._categories_for_institute(institute_id, force_refresh=force_refresh)
        by_id = {text(row.get("id")): row for row in catalog.get("categories") or []}
        categories = []
        for requirement in self._requirements(context):
            category_id = text(requirement.get("collection_category_id"))
            official = by_id.get(category_id, {})
            categories.append({
                "mapping_id": text(requirement.get("mapping_id")),
                "clearance_period_id": text(requirement.get("clearance_period_id")),
                "category_id": category_id,
                "category_name": text(official.get("category_name")) or text(requirement.get("category_name")),
                "collection_fee": text(official.get("collection_fee")) or None,
                "description": text(official.get("description")) or None,
                "assigned_roles": requirement.get("assigned_roles") or [],
                "step_order": int(requirement.get("step_order") or 0),
                "is_blocking": bool(requirement.get("is_blocking")),
                "status": upper(requirement.get("status")),
                "available_in_dnsc": bool(official),
            })
        return {
            "academic_period": text(context.get("current_academic_period")) or None,
            "academic_year": text(context.get("current_academic_year")) or None,
            "semester": text(context.get("current_semester")) or None,
            "clearance_period_id": text(context.get("current_clearance_period_id")) or None,
            "categories": categories,
            "count": len(categories),
            "source_system": "DNSC_COLLECTION",
            "configuration_source": "CLMS_CLEARANCE_COLLECTION_MAP",
            "read_only": True,
        }

    def list_queues(self, request, filters: Mapping[str, Any], *, force_refresh: bool = False) -> dict[str, Any]:
        workspace_map, context = self._build_live_workspaces(request, force_refresh=force_refresh)
        queues = [entry["group"] for entry in workspace_map.values() if entry["group"]["total"] > 0]
        search = text(filters.get("search")).lower()
        queues = [
            group for group in queues
            if (not text(filters.get("academic_period")) or text(group.get("academic_period")) == text(filters.get("academic_period")))
            and (not text(filters.get("program")) or upper(group.get("program")) == upper(filters.get("program")))
            and (not text(filters.get("year_level")) or text(group.get("year_level")) == text(filters.get("year_level")))
            and (not text(filters.get("set_code")) or upper(group.get("set_code")) == upper(filters.get("set_code")))
            and (not text(filters.get("task_name")) or lower(group.get("task_name")) == lower(filters.get("task_name")))
            and (not search or any(search in lower(value) for value in [group.get("task_name"), group.get("set_name"), group.get("program"), group.get("year_level"), group.get("academic_year")]))
        ]
        queues.sort(key=lambda g: (g.get("program", ""), g.get("year_level", ""), g.get("set_code", "")))
        return {
            "queues": queues,
            "source_system": "DNSC_COLLECTION",
            "read_only": True,
            "current_academic_period": context.get("current_academic_period"),
            "configured_financial_requirements": len(self._requirements(context)),
        }

    def open_queue(self, request, queue_id: str, *, force_refresh: bool = False) -> dict[str, Any]:
        workspace_map, _ = self._build_live_workspaces(request, force_refresh=force_refresh)
        detail = workspace_map.get(queue_id)
        if detail is None:
            raise ClearanceBridgeError(404, "COLLECTION_QUEUE_NOT_FOUND", "This live fee queue is no longer available. Refresh the queues because balances may have changed.")
        return {
            **detail,
            "saved_position": {"student_clearance_id": None, "status_filter": None, "search": "", "page": 0, "page_size": 10},
            "queue_revision": detail["group"]["queue_revision"],
            "source_system": "DNSC_COLLECTION",
            "read_only": True,
        }

    def student_status(self, request, student_number: str, *, force_refresh: bool = False) -> dict[str, Any]:
        normalized = text(student_number)
        if not normalized:
            raise ClearanceBridgeError(422, "STUDENT_NUMBER_REQUIRED", "A student number is required.")
        context = self._context(request, force_refresh=force_refresh)
        requirements = self._requirements(context)
        academic_year = text(context.get("current_academic_year"))
        semester = text(context.get("current_semester")) or "1st"
        if not academic_year:
            raise ClearanceBridgeError(409, "CLEARANCE_PERIOD_UNAVAILABLE", "No active clearance academic period is available.")
        institute_id = self._required_institute_id(context)
        student = self._scope_students(
            Student.objects.select_related("program").filter(s_studentID=normalized),
            institute_id,
        ).first()
        if student is None:
            raise ClearanceBridgeError(404, "STUDENT_NOT_FOUND", "The student is not available in your institute.")
        program = upper(student.program.name)
        if program not in self._allowed_program_names(context, institute_id) or not self._assignment_scope_allows(
            context["assignment"], program, year_label(student.s_lvl), set_code(program, student.s_lvl, student.s_set)
        ):
            raise ClearanceBridgeError(403, "PERMISSION_DENIED", "The student is outside your active assignment scope.")
        fee_rows = self._fee_queryset(institute_id).filter(
            student__s_studentID=normalized,
            academic_year=academic_year,
            semester=semester,
        )
        exact = list(fee_rows)
        matched = [row for row in exact if self._requirement_for_fee(requirements, *self._fee_category(row)) is not None]
        items = [self._financial_item(row) for row in matched]
        outstanding = [item for item in items if item["balance"] > 0 and item["external_status"] in {"PENDING", "OVERDUE"}]
        program = upper(student.program.name) if student is not None else ""
        return {
            "student_number": normalized,
            "student_name": full_name(student),
            "program": program,
            "year_level": year_label(student.s_lvl) if student is not None else "",
            "set_code": set_code(program, student.s_lvl, student.s_set) if student is not None and program else "",
            "academic_period": text(context.get("current_academic_period")) or None,
            "academic_year": academic_year,
            "semester": semester,
            "mapped_requirement_count": len(requirements),
            "matched_fee_count": len(items),
            "outstanding_count": len(outstanding),
            "outstanding_total": round(sum(item["balance"] for item in outstanding), 2),
            "financially_clear": len(outstanding) == 0,
            "items": items,
            "source_system": "DNSC_COLLECTION",
            "read_only": True,
        }

    def _context(self, request, *, force_refresh: bool) -> dict[str, Any]:
        assignment_id = text(request.headers.get("X-Assignment-ID"))
        if not assignment_id:
            raise ClearanceBridgeError(403, "SIGNEE_ASSIGNMENT_REQUIRED", "Select an active signee assignment and try again.")

        # A cached assignment is not proof of this caller's current authorization.
        # Keep institutional datasets cached, but validate the session and routing
        # context on every request so revoked or different callers cannot reuse it.
        session = self.signee_session(request)
        active = session["active_assignment"]
        if text(active.get("assignment_id")) != assignment_id:
            raise ClearanceBridgeError(403, "SIGNEE_ASSIGNMENT_REQUIRED", "The selected assignment does not belong to the active session.")
        context = self.n8n.collection_context(request)
        assignment = context.get("assignment")
        if not isinstance(assignment, dict) or text(assignment.get("assignment_id")) != assignment_id:
            raise ClearanceBridgeError(502, "COLLECTION_CONTEXT_INVALID", "The collection context does not match the selected assignment.")
        for field in ("program_scope", "year_scope", "set_scope"):
            if not text(active.get(field)) or upper(assignment.get(field)) != upper(active.get(field)):
                raise ClearanceBridgeError(502, "COLLECTION_CONTEXT_INVALID", "The collection scope does not match the authenticated assignment.")
        institute_id = self._required_institute_id(session)
        context_institute_id = self._institute_id_from_payload(context)
        if context_institute_id is not None and context_institute_id != institute_id:
            raise ClearanceBridgeError(409, "INSTITUTE_CONTEXT_MISMATCH", "The collection context belongs to a different institute.")
        context = {**context, "institute_id": institute_id}
        self._requirements(context)
        return context

    @staticmethod
    def _requirements(context: Mapping[str, Any]) -> list[dict[str, Any]]:
        rows = context.get("financial_requirements")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ClearanceBridgeError(502, "COLLECTION_CONTEXT_INVALID", "The collection context returned an invalid financial requirement list.")
        return [row for row in rows if upper(row.get("status")) == "ACTIVE"]

    def _active_program_names(self, institute_id: int | None) -> list[str]:
        qs = self._scope_programs(Program.objects.filter(status="active"), institute_id).order_by("name")
        return [upper(value) for value in qs.values_list("name", flat=True) if upper(value)]

    def _allowed_program_names(self, context: Mapping[str, Any], institute_id: int) -> list[str]:
        # DNSC's institute-owned programs are authoritative. Optional workflow
        # allowlists can restrict that set further, including to an empty set.
        programs = self._active_program_names(institute_id)
        assignment = context.get("assignment") if isinstance(context.get("assignment"), dict) else {}
        program_scope = upper(assignment.get("program_scope"))
        if not program_scope:
            raise ClearanceBridgeError(502, "COLLECTION_CONTEXT_INVALID", "The active assignment is missing its program scope.")
        if program_scope != "ALL":
            programs = [program for program in programs if program == program_scope]
        configured_key = "unit_programs" if "unit_programs" in context else "programs" if "programs" in context else None
        if configured_key is not None:
            configured = context[configured_key]
            if not isinstance(configured, list) or any(not isinstance(value, str) or not value.strip() for value in configured):
                raise ClearanceBridgeError(502, "COLLECTION_CONTEXT_INVALID", "The collection context returned an invalid program list.")
            allowed = {upper(value) for value in configured}
            programs = [program for program in programs if program in allowed]
        return programs

    def _fee_queryset(self, institute_id: int | None):
        qs = Fee.objects.select_related(
            "student",
            "student__program",
            "category",
            "gen_fee_batch",
        )
        return self._scope_fees(qs, institute_id)

    def _current_dataset(self, context: Mapping[str, Any], *, force_refresh: bool):
        academic_year = text(context.get("current_academic_year"))
        semester = text(context.get("current_semester")) or "1st"
        institute_id = self._required_institute_id(context)
        programs = self._allowed_program_names(context, institute_id)
        if not academic_year or not programs:
            return [], [], programs

        key = "clms:collection-dataset:{}:{}:{}:{}".format(
            institute_id or "all",
            ",".join(programs),
            academic_year,
            semester,
        )

        def load():
            students = list(
                self._scope_students(
                    Student.objects.select_related("program", "user").filter(
                        program__name__in=programs,
                        program__status="active",
                        s_status="enrolled",
                    ),
                    institute_id,
                )
            )
            fees = list(
                self._fee_queryset(institute_id).filter(
                    student__program__name__in=programs,
                    academic_year=academic_year,
                    semester=semester,
                )
            )
            return students, fees

        students, fees = _cache_get_or_load(key, self.fee_ttl, load, force_refresh)
        return students, fees, programs

    def _build_live_workspaces(self, request, *, force_refresh: bool):
        context = self._context(request, force_refresh=force_refresh)
        requirements = self._requirements(context)
        if not requirements:
            return {}, context

        academic_year = text(context.get("current_academic_year"))
        semester = text(context.get("current_semester")) or "1st"
        academic_period = text(context.get("current_academic_period"))
        if not academic_year:
            return {}, context
        assignment = context.get("assignment") if isinstance(context.get("assignment"), dict) else {}
        role = context.get("role") if isinstance(context.get("role"), dict) else {}
        student_rows, fee_rows, active_programs = self._current_dataset(context, force_refresh=force_refresh)
        students_by_id = {str(row.id): row for row in student_rows}

        by_student = {}
        for fee in fee_rows:
            category_id, category_name = self._fee_category(fee)
            requirement = self._requirement_for_fee(requirements, category_id, category_name)
            if requirement is None:
                continue
            student = students_by_id.get(str(fee.student_id), fee.student)
            program = upper(student.program.name if student and student.program_id else "")
            if program not in active_programs:
                continue
            current_year = year_label(student.s_lvl)
            current_set = set_code(program, student.s_lvl, student.s_set)
            if not self._assignment_scope_allows(assignment, program, current_year, current_set):
                continue
            student_number = text(student.s_studentID)
            if not student_number:
                continue
            entry = by_student.setdefault(student_number, {
                "student_number": student_number,
                "student_name": full_name(student),
                "program": program,
                "year_level": current_year,
                "set_code": current_set,
                "fees": [],
            })
            entry["fees"].append(fee)

        grouped = defaultdict(list)
        for student in by_student.values():
            items = [self._financial_item(fee) for fee in student["fees"]]
            outstanding = [item for item in items if item["balance"] > 0 and item["external_status"] in {"PENDING", "OVERDUE"}]
            if not outstanding:
                continue
            student["items"] = items
            student["outstanding"] = outstanding
            key = "|".join([student["program"], student["year_level"], student["set_code"], academic_period])
            grouped[key].append(student)

        workspaces = {}
        for key, students_raw in grouped.items():
            queue_id = "COLLECTION-" + hashlib.sha256(key.encode()).hexdigest()[:16].upper()
            students = [self._student_financial_record(row, academic_year, semester) for row in students_raw]
            students.sort(key=lambda record: (record["student_name"], record["student_number"]))
            unpaid = sum(1 for record in students if record["status"] == "UNPAID")
            partial = sum(1 for record in students if record["status"] == "PARTIALLY_PAID")
            first = students_raw[0]
            group = {
                "queue_id": queue_id,
                "academic_period": academic_period,
                "academic_year": ("A.Y. " + academic_year.replace("-", "–")) if academic_year else "",
                "semester": "Second Semester" if semester == "2nd" else "First Semester",
                "program": first["program"],
                "year_level": first["year_level"],
                "set_code": first["set_code"],
                "set_name": first["set_code"].replace("-", " "),
                "step_code": "COLLECTION_GATE",
                "task_name": "Financial Requirements",
                "position_name": text(role.get("role_name")) or upper(assignment.get("role_code")),
                "total": len(students),
                "unpaid": unpaid,
                "partially_paid": partial,
                "for_verification": 0,
                "cleared": 0,
                "returned": 0,
                "actionable": 0,
                "queue_revision": "LIVE-" + hashlib.sha256("|".join(f"{r['item_id']}:{r['record_version']}" for r in students).encode()).hexdigest()[:12].upper(),
                "source_system": "DNSC_COLLECTION",
                "read_only": True,
            }
            workspaces[queue_id] = {"group": group, "students": students}
        return workspaces, context

    @staticmethod
    def _fee_category(fee: Fee) -> tuple[str, str]:
        return text(fee.category_id), text(getattr(fee.category, "category_name", ""))

    @staticmethod
    def _requirement_for_fee(requirements: list[dict[str, Any]], category_id: str, category_name: str):
        if category_id:
            for requirement in requirements:
                if text(requirement.get("collection_category_id")) == category_id:
                    return requirement
        fee_key = normalized_financial_name(category_name)
        if fee_key:
            for requirement in requirements:
                if normalized_financial_name(requirement.get("category_name")) == fee_key:
                    return requirement
        return None

    @staticmethod
    def _assignment_scope_allows(assignment: Mapping[str, Any], program: str, year: str, set_value: str) -> bool:
        program_scope = upper(assignment.get("program_scope"))
        year_scope = upper(assignment.get("year_scope"))
        set_scope = upper(assignment.get("set_scope"))
        return (
            bool(program_scope and year_scope and set_scope)
            and (program_scope == "ALL" or program_scope == upper(program))
            and (year_scope == "ALL" or year_scope == upper(year))
            and (set_scope in {"ALL", "ALL_ACTIVE_IC_SETS"} or set_scope == upper(set_value))
        )

    @classmethod
    def _financial_item(cls, fee: Fee) -> dict[str, Any]:
        category_id, category_name = cls._fee_category(fee)
        total = _number(fee.total_amount)
        balance = _number(fee.balance)
        paid = max(0.0, total - balance)
        return {
            "fee_id": text(fee.id),
            "category_id": category_id,
            "category_name": category_name,
            "total_amount": total,
            "amount_paid": paid,
            "balance": balance,
            "external_status": upper(fee.status),
            "due_date": fee.due_date.isoformat() if fee.due_date else None,
            "remarks": text(fee.remarks) or None,
            "batch_id": text(fee.gen_fee_batch_id) or None,
        }

    @staticmethod
    def _student_financial_record(row: Mapping[str, Any], academic_year: str, semester: str) -> dict[str, Any]:
        outstanding = row["outstanding"]
        all_items = row["items"]
        total = sum(item["total_amount"] for item in outstanding)
        balance = sum(item["balance"] for item in outstanding)
        paid = max(0.0, total - balance)
        state = "PARTIALLY_PAID" if paid > 0 else "UNPAID"
        identity = f"{row['student_number']}::{academic_year}::{semester}"
        versions = [(item["fee_id"], item["balance"], item["external_status"], item["due_date"]) for item in all_items]
        due_dates = sorted([item["due_date"] for item in outstanding if item["due_date"]])
        references = [item["batch_id"] for item in all_items if item["batch_id"]]
        categories = [item["category_name"] for item in outstanding if item["category_name"]]
        return {
            "student_clearance_id": f"COLLECTION_STUDENT::{identity}",
            "student_number": row["student_number"],
            "student_name": row["student_name"],
            "program": row["program"],
            "year_level": row["year_level"],
            "set_name": row["set_code"].replace("-", " "),
            "task_name": "Financial Requirements",
            "status": state,
            "record_version": _hash_int(versions),
            "student_note": ", ".join(categories) if categories else None,
            "staff_remarks": None,
            "reference": references[0] if references else None,
            "latest_approval": None,
            "item_id": f"DNSC_FINANCIAL::{identity}",
            "clearance_id": "",
            "amount_due": round(total, 2),
            "amount_paid": round(paid, 2),
            "has_evidence": False,
            "prerequisites_satisfied": True,
            "can_approve": False,
            "approval_blockers": ["Outstanding financial obligations are controlled by DNSC Collection Management."],
            "source_system": "DNSC_COLLECTION",
            "read_only": True,
            "external_status": "OUTSTANDING",
            "due_date": due_dates[0] if due_dates else None,
            "outstanding_count": len(outstanding),
            "outstanding_total": round(balance, 2),
            "financially_clear": False,
            "financial_items": all_items,
        }
