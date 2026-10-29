from django.contrib.auth.models import Group
from django.db.models import Count, Prefetch, Q
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.serializers import ValidationError
from drf_spectacular.utils import extend_schema


from api.utils.custom_paginations import CustomPagination
from api.clearance.models import (
    ClearanceType,
    ClearanceRequirement,
    StudentClearance,
    StudentClearanceItem,
)
from api.students.models import Student
from api.clearance.serializers import (
    ClearanceTypeReadSerializer,
    ClearanceTypeSlimSerializer,
    ClearanceTypeWriteSerializer,
    ClearanceRequirementReadSerializer,
    ClearanceRequirementWriteSerializer,
    StudentClearanceReadSerializer,
    StudentClearanceSlimSerializer,
    StudentClearanceWriteSerializer,
    StudentClearanceItemReadSerializer,
    StudentClearanceItemWriteSerializer,
    GroupSlimSerializer,
)
from api.clearance.services.issuance_service import issue_clearance_to_target_students
from .permissions import (
    IsClearanceAdminOrReadOnly,
    CanAccessInstituteClearanceData,
    CanManageStudentClearanceItem,
    is_clearance_admin,
    is_clearance_signatory,
    can_access_clearance_scope,
    user_group_names,
)


def get_user_institute_id(user):
    return getattr(user, "institute_id", None)


def user_can_admin_clearance(user):
    return bool(
        user
        and user.is_authenticated
        and (
            getattr(user, "is_superuser", False)
            or is_clearance_admin(user)
        )
    )


def is_program_chair_user(user):
    if not user or not user.is_authenticated:
        return False

    names = user_group_names(user)
    return "program chair" in names or "program chairs" in names


def user_can_enroll_student(user):
    return user_can_admin_clearance(user) or is_program_chair_user(user)


def get_student_enrolled_filter():
    status_enum = getattr(Student, "StudentStatus", None)
    enrolled_value = getattr(status_enum, "ENROLLED", None) if status_enum else None
    return {"s_status": enrolled_value} if enrolled_value is not None else {}


def format_student_name(student):
    parts = [
        getattr(student, "s_fname", ""),
        getattr(student, "s_mname", ""),
        getattr(student, "s_lname", ""),
        getattr(student, "s_suffix", ""),
    ]
    return " ".join(str(part).strip() for part in parts if str(part).strip())


def format_program(program):
    if not program:
        return ""

    for attr in ["acronym", "prog_abbr", "program_name", "name"]:
        value = getattr(program, attr, None)
        if value:
            return str(value)

    return str(program)


def requirement_authorizing_roles(requirement):
    roles = []
    prefetched = getattr(requirement, "_prefetched_objects_cache", {})

    if "authorizing_roles" in prefetched:
        roles = list(prefetched["authorizing_roles"])
    elif getattr(requirement, "pk", None):
        roles = list(requirement.authorizing_roles.all())

    if not roles and getattr(requirement, "authorizing_role", None):
        roles = [requirement.authorizing_role]

    unique_roles = []
    seen_ids = set()
    for role in roles:
        if role and role.id not in seen_ids:
            unique_roles.append(role)
            seen_ids.add(role.id)

    return unique_roles


def serialize_item_authorizing_roles(requirement):
    roles = requirement_authorizing_roles(requirement)
    return {
        "authorizing_role_id": roles[0].id if roles else getattr(requirement, "authorizing_role_id", None),
        "authorizing_role_name": roles[0].name if roles else getattr(getattr(requirement, "authorizing_role", None), "name", ""),
        "authorizing_role_ids": [role.id for role in roles],
        "authorizing_role_names": [role.name for role in roles],
        "authorizing_roles": [{"id": role.id, "name": role.name} for role in roles],
    }


def clearance_status_counts(queryset):
    return queryset.aggregate(
        total=Count("id", distinct=True),
        pending=Count("id", filter=Q(status=StudentClearance.ClearanceStatus.PENDING), distinct=True),
        in_progress=Count("id", filter=Q(status=StudentClearance.ClearanceStatus.IN_PROGRESS), distinct=True),
        completed=Count("id", filter=Q(status=StudentClearance.ClearanceStatus.COMPLETED), distinct=True),
        rejected=Count("id", filter=Q(status=StudentClearance.ClearanceStatus.REJECTED), distinct=True),
    )


def item_status_counts(queryset):
    return queryset.aggregate(
        total=Count("id", distinct=True),
        pending=Count("id", filter=Q(status=StudentClearanceItem.ItemStatus.PENDING), distinct=True),
        cleared=Count("id", filter=Q(status=StudentClearanceItem.ItemStatus.CLEARED), distinct=True),
        has_issue=Count("id", filter=Q(status=StudentClearanceItem.ItemStatus.HAS_ISSUE), distinct=True),
    )


def serialize_clearance_with_items(clearance):
    items = list(getattr(clearance, "items", []).all() if hasattr(getattr(clearance, "items", None), "all") else [])
    total_items = len(items)
    cleared_items = sum(1 for item in items if item.status == StudentClearanceItem.ItemStatus.CLEARED)
    percent = round((cleared_items / total_items) * 100, 2) if total_items else 0

    return {
        "id": clearance.id,
        "student_id": clearance.student_id,
        "student_name": format_student_name(clearance.student),
        "student_number": getattr(clearance.student, "s_studentID", ""),
        "program": format_program(getattr(clearance.student, "program", None)),
        "year_level": getattr(clearance.student, "s_lvl", None),
        "s_set": getattr(clearance.student, "s_set", ""),
        "clearance_type_id": clearance.clearance_type_id,
        "clearance_type_name": clearance.clearance_type.name,
        "academic_year": clearance.clearance_type.academic_year,
        "semester": clearance.clearance_type.semester,
        "status": clearance.status,
        "date_initiated": clearance.date_initiated,
        "date_completed": clearance.date_completed,
        "is_enrolled": clearance.is_enrolled,
        "enrolled_at": clearance.enrolled_at,
        "enrolled_by_user_id": clearance.enrolled_by_user_id,
        "progress": {
            "cleared": cleared_items,
            "total": total_items,
            "percent": percent,
        },
        "items": [
            {
                "id": item.id,
                "student_clearance_id": item.student_clearance_id,
                "requirement_id": item.requirement_id,
                "requirement_name": item.requirement.name,
                **serialize_item_authorizing_roles(item.requirement),
                "order_sequence": item.requirement.order_sequence,
                "status": item.status,
                "amount_due": item.amount_due,
                "remarks": item.remarks,
                "cleared_by_user_id": item.cleared_by_user_id,
                "cleared_at": item.cleared_at,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
            for item in items
        ],
    }


@extend_schema(tags=["Clearance Type"])
class ClearanceTypeViewSet(viewsets.ModelViewSet):
    queryset = (
        ClearanceType.objects.all()
        .annotate(requirement_count=Count("requirements", distinct=True))
        .prefetch_related(
            Prefetch(
                "requirements",
                queryset=ClearanceRequirement.objects.select_related("authorizing_role")
                .prefetch_related("authorizing_roles")
                .order_by("order_sequence", "id"),
            )
        )
        .order_by("-created_at")
    )
    pagination_class = CustomPagination
    permission_classes = [IsAuthenticated, IsClearanceAdminOrReadOnly]

    filterset_fields = [
        "name",
        "academic_year",
        "semester",
        "is_active",
        "is_issued",
        "institute",
    ]
    search_fields = ["name", "description", "academic_year", "semester"]
    ordering_fields = ["created_at", "issued_at", "name"]

    def get_serializer_class(self):
        if self.action == "create":
            return ClearanceTypeWriteSerializer
        if self.action == "retrieve":
            return ClearanceTypeReadSerializer
        if self.action in ["update", "partial_update"]:
            return ClearanceTypeWriteSerializer
        return ClearanceTypeSlimSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset.none()

        user = self.request.user

        if getattr(user, "is_superuser", False):
            return self.queryset

        if hasattr(user, "student"):
            student = user.student
            return self.queryset.filter(
                is_active=True,
                is_issued=True,
                institute_id=student.program.institute_id,
                target_year_levels__contains=[student.s_lvl],
        )

        if can_access_clearance_scope(user):
            institute_id = get_user_institute_id(user)
            if institute_id is None:
                return self.queryset.none()
            return self.queryset.filter(institute_id=institute_id)

        return self.queryset.none()

    def perform_create(self, serializer):
        user = self.request.user

        if not (user.is_superuser or is_clearance_admin(user)):
            raise ValidationError({"detail": "Only clearance admins can create clearance types."})

        user_institute = getattr(user, "institute", None)
        user_institute_id = getattr(user, "institute_id", None)

        if not user.is_superuser and not user_institute_id:
            raise ValidationError({"detail": "Your account has no institute assigned."})

        if user.is_superuser:
            serializer.save(updated_by=user)
            return

        serializer.save(institute=user_institute, updated_by=user)

    def perform_update(self, serializer):
        user = self.request.user

        if not (user.is_superuser or is_clearance_admin(user)):
            raise ValidationError({"detail": "Only clearance admins can update clearance types."})

        serializer.save(updated_by=user)

    @action(detail=False, methods=["get"], url_path="admin-summary")
    def admin_summary(self, request):
        user = request.user
        if not user_can_admin_clearance(user):
            raise ValidationError({"detail": "Only clearance admins can access this summary."})

        clearance_types_qs = self.get_queryset()
        clearance_type_counts = clearance_types_qs.aggregate(
            total=Count("id", distinct=True),
            active=Count("id", filter=Q(is_active=True), distinct=True),
            issued=Count("id", filter=Q(is_issued=True), distinct=True),
        )

        institute_id = None if getattr(user, "is_superuser", False) else get_user_institute_id(user)
        students_qs = Student.objects.all()
        if institute_id is not None:
            students_qs = students_qs.filter(program__institute_id=institute_id)

        students_qs = students_qs.filter(**get_student_enrolled_filter())
        students_by_year_level = list(
            students_qs.values("s_lvl")
            .annotate(count=Count("id"))
            .order_by("s_lvl")
        )

        clearances_qs = StudentClearance.objects.all()
        if institute_id is not None:
            clearances_qs = clearances_qs.filter(student__program__institute_id=institute_id)

        clearance_counts = clearance_status_counts(clearances_qs)

        return Response(
            {
                "clearance_types": clearance_type_counts,
                "student_clearances": clearance_counts,
                "total_students": students_qs.count(),
                "students_by_year_level": [
                    {"year_level": row["s_lvl"], "count": row["count"]}
                    for row in students_by_year_level
                ],
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get"], url_path="dashboard")
    def dashboard(self, request, pk=None):
        clearance_type = self.get_object()

        requirements_qs = clearance_type.requirements.select_related("authorizing_role").prefetch_related("authorizing_roles").order_by(
            "order_sequence",
            "id",
        )
        requirements = ClearanceRequirementReadSerializer(requirements_qs, many=True).data

        target_students_qs = Student.objects.filter(
            program__institute_id=clearance_type.institute_id,
            s_lvl__in=clearance_type.target_year_levels or [],
            **get_student_enrolled_filter(),
        )

        clearances_qs = StudentClearance.objects.filter(clearance_type=clearance_type)
        clearance_counts = clearance_status_counts(clearances_qs)
        item_counts = item_status_counts(
            StudentClearanceItem.objects.filter(student_clearance__clearance_type=clearance_type)
        )

        unique_roles = [
            {"id": role.id, "name": role.name}
            for role in Group.objects.exclude(name__iexact="Students")
            .exclude(name__iexact="Student")
            .order_by("name")
        ]

        return Response(
            {
                "clearance_type": ClearanceTypeReadSerializer(clearance_type).data,
                "requirements": requirements,
                "roles": unique_roles,
                "stats": {
                    "target_students_count": target_students_qs.count(),
                    "issued_students_count": clearance_counts["total"],
                    "pending_count": clearance_counts["pending"],
                    "in_progress_count": clearance_counts["in_progress"],
                    "completed_count": clearance_counts["completed"],
                    "rejected_count": clearance_counts["rejected"],
                    "items": item_counts,
                },
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="issue")
    def issue(self, request, pk=None):
        clearance_type = self.get_object()
        user = request.user

        if not (user.is_superuser or is_clearance_admin(user)):
            raise ValidationError({"detail": "Only clearance admins can issue clearance templates."})

        if clearance_type.is_issued:
            return Response(
                {"detail": "This clearance type has already been issued."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not clearance_type.requirements.exists():
            raise ValidationError({"detail": "Add at least one requirement before issuing."})

        if not clearance_type.target_year_levels:
            raise ValidationError({"detail": "Select target year levels before issuing."})

        result = issue_clearance_to_target_students(clearance_type, actor=user)

        return Response(
            {
                "detail": "Clearance issued successfully.",
                **result,
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(tags=["Clearance Requirement"])
class ClearanceRequirementViewSet(viewsets.ModelViewSet):
    queryset = (
        ClearanceRequirement.objects.select_related(
            "clearance_type",
            "authorizing_role",
        )
        .prefetch_related("authorizing_roles")
        .all()
        .order_by("clearance_type_id", "order_sequence")
    )
    pagination_class = CustomPagination
    permission_classes = [IsAuthenticated, IsClearanceAdminOrReadOnly]

    filterset_fields = [
        "clearance_type",
        "authorizing_role",
        "authorizing_roles",
    ]
    search_fields = ["name", "clearance_type__name", "authorizing_role__name", "authorizing_roles__name"]
    ordering_fields = ["order_sequence", "id"]

    def get_serializer_class(self):
        if self.action in ["create", "update", "partial_update"]:
            return ClearanceRequirementWriteSerializer
        return ClearanceRequirementReadSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset.none()

        user = self.request.user

        if getattr(user, "is_superuser", False):
            return self.queryset

        if hasattr(user, "student"):
            student = user.student
            return self.queryset.filter(
                clearance_type__is_active=True,
                clearance_type__is_issued=True,
                clearance_type__institute_id=student.program.institute_id,
                clearance_type__target_year_levels__contains=[student.s_lvl],
        )

        if can_access_clearance_scope(user):
            institute_id = get_user_institute_id(user)
            if institute_id is None:
                return self.queryset.none()
            return self.queryset.filter(clearance_type__institute_id=institute_id)

        return self.queryset.none()

    def perform_create(self, serializer):
        user = self.request.user
        clearance_type = serializer.validated_data["clearance_type"]

        if clearance_type.is_issued:
            raise ValidationError({"detail": "Issued clearance requirements can no longer be modified."})

        if not (user.is_superuser or is_clearance_admin(user)):
            raise ValidationError({"detail": "Only clearance admins can create requirements."})

        if not user.is_superuser:
            institute_id = get_user_institute_id(user)
            if institute_id is None or clearance_type.institute_id != institute_id:
                raise ValidationError({"detail": "You cannot add requirements to this clearance type."})

        serializer.save(updated_by=user)

    def perform_update(self, serializer):
        user = self.request.user
        clearance_type = serializer.instance.clearance_type

        if clearance_type.is_issued:
            raise ValidationError({"detail": "Issued clearance requirements can no longer be modified."})

        if not (user.is_superuser or is_clearance_admin(user)):
            raise ValidationError({"detail": "Only clearance admins can update requirements."})

        if not user.is_superuser:
            institute_id = get_user_institute_id(user)
            if institute_id is None or clearance_type.institute_id != institute_id:
                raise ValidationError({"detail": "You cannot modify this requirement."})

        serializer.save(updated_by=user)


@extend_schema(tags=["Student Clearance"])
class StudentClearanceViewSet(viewsets.ModelViewSet):
    queryset = (
        StudentClearance.objects.select_related(
            "student",
            "student__program",
            "clearance_type",
        )
        .prefetch_related(
            Prefetch(
                "items",
                queryset=StudentClearanceItem.objects.select_related(
                    "requirement",
                    "requirement__authorizing_role",
                    "cleared_by_user",
                ).prefetch_related("requirement__authorizing_roles")
            )
        )
        .order_by("-date_initiated")
    )

    pagination_class = CustomPagination
    permission_classes = [IsAuthenticated, CanAccessInstituteClearanceData]

    filterset_fields = [
        "student",
        "clearance_type",
        "status",
        "clearance_type__academic_year",
        "clearance_type__semester",
    ]
    search_fields = [
        "student__s_fname",
        "student__s_mname",
        "student__s_lname",
        "student__s_studentID",
        "clearance_type__name",
    ]
    ordering_fields = ["date_initiated", "date_completed", "status"]

    def get_serializer_class(self):
        if self.action == "create":
            return StudentClearanceWriteSerializer
        if self.action == "retrieve":
            return StudentClearanceReadSerializer
        if self.action in ["update", "partial_update"]:
            return StudentClearanceWriteSerializer
        return StudentClearanceSlimSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset.none()

        user = self.request.user

        if user.is_superuser:
            return self.queryset

        if hasattr(user, "student"):
            return self.queryset.filter(student=user.student)

        if is_clearance_admin(user) or is_clearance_signatory(user) or is_program_chair_user(user):
            institute_id = get_user_institute_id(user)
            if institute_id is None:
                return self.queryset.none()

            qs = self.queryset.filter(
            student__program__institute_id=institute_id,
        )

            if is_clearance_admin(user) or is_program_chair_user(user):
                return qs.distinct()

            user_group_ids = list(user.groups.values_list("id", flat=True))
            if user_group_ids:
                return qs.filter(
                    Q(items__requirement__authorizing_role_id__in=user_group_ids)
                    | Q(items__requirement__authorizing_roles__id__in=user_group_ids)
            ).distinct()

            return qs.none()

        return self.queryset.none()

    @action(detail=False, methods=["get"], url_path="my-clearances")
    def my_clearances(self, request):
        user = request.user
        if not hasattr(user, "student"):
            return Response([], status=status.HTTP_200_OK)

        queryset = self.get_queryset().filter(student=user.student)
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = StudentClearanceReadSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = StudentClearanceReadSerializer(queryset, many=True)
        return Response(serializer.data)


    @action(detail=False, methods=["get"], url_path="bulk-by-ids")
    def bulk_by_ids(self, request):
        raw_ids = request.query_params.get("ids", "")
        ids = []
        for value in raw_ids.split(","):
            value = value.strip()
            if not value:
                continue
            if value.isdigit():
                ids.append(int(value))

        if not ids:
            return Response([], status=status.HTTP_200_OK)

        unique_ids = list(dict.fromkeys(ids))[:200]
        queryset = self.get_queryset().filter(id__in=unique_ids)

        ordering = {pk: index for index, pk in enumerate(unique_ids)}
        ordered_clearances = sorted(queryset, key=lambda item: ordering.get(item.id, 999999))

        serializer = StudentClearanceReadSerializer(ordered_clearances, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="enroll")
    def enroll(self, request, pk=None):
        user = request.user
        if not user_can_enroll_student(user):
            raise ValidationError({"detail": "Only program chairs or clearance admins can enroll students."})

        clearance = self.get_object()

        if clearance.status != StudentClearance.ClearanceStatus.COMPLETED:
            raise ValidationError(
                {"detail": "Only completed clearances can be marked as enrolled."}
            )

        if clearance.is_enrolled:
            return Response(
                {
                    "detail": "Student is already marked as enrolled for this clearance.",
                    "clearance": StudentClearanceReadSerializer(clearance).data,
                },
                status=status.HTTP_200_OK,
            )

        clearance.is_enrolled = True
        clearance.enrolled_at = timezone.now()
        clearance.enrolled_by_user = user
        clearance.updated_by = user
        clearance.save(
            update_fields=[
                "is_enrolled",
                "enrolled_at",
                "enrolled_by_user",
                "updated_by",
                "updated_at",
            ]
        )

        return Response(
            {
                "detail": "Student marked as enrolled successfully.",
                "clearance": StudentClearanceReadSerializer(clearance).data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="program-chair-dashboard")
    def program_chair_dashboard(self, request):
        user = request.user
        if not user_can_enroll_student(user):
            raise ValidationError({"detail": "Only program chairs or clearance admins can access this dashboard."})

        queryset = self.get_queryset().annotate(
            total_items=Count("items", distinct=True),
            cleared_items=Count(
                "items",
                filter=Q(items__status=StudentClearanceItem.ItemStatus.CLEARED),
                distinct=True,
            ),
        )

        search = request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(student__s_fname__icontains=search)
                | Q(student__s_mname__icontains=search)
                | Q(student__s_lname__icontains=search)
                | Q(student__s_studentID__icontains=search)
                | Q(clearance_type__name__icontains=search)
            )

        counts = clearance_status_counts(queryset)
        completed_count = queryset.filter(status=StudentClearance.ClearanceStatus.COMPLETED).count()
        enrolled_count = queryset.filter(is_enrolled=True).count()
        ready_count = queryset.filter(
            status=StudentClearance.ClearanceStatus.COMPLETED,
            is_enrolled=False,
        ).count()
        stats_payload = {
            "total": counts["total"],
            "not_cleared": counts["total"] - completed_count,
            "ready": ready_count,
            "enrolled": enrolled_count,
            "by_status": counts,
        }

        page = self.paginate_queryset(queryset)
        if page is not None:
            records = [serialize_clearance_with_items(clearance) for clearance in page]
            return self.get_paginated_response(
                {
                    "stats": stats_payload,
                    "results": records,
                }
            )

        records = [serialize_clearance_with_items(clearance) for clearance in queryset[:100]]
        return Response(
            {
                "stats": stats_payload,
                "results": records,
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(tags=["Student Clearance Item"])
class StudentClearanceItemViewSet(viewsets.ModelViewSet):
    queryset = (
        StudentClearanceItem.objects.select_related(
            "student_clearance",
            "student_clearance__student",
            "student_clearance__student__program",
            "student_clearance__clearance_type",
            "requirement",
            "requirement__authorizing_role",
            "cleared_by_user",
        )
        .prefetch_related("requirement__authorizing_roles")
        .order_by("requirement__order_sequence", "id")
    )

    pagination_class = CustomPagination
    permission_classes = [IsAuthenticated, CanManageStudentClearanceItem]

    filterset_fields = [
        "student_clearance",
        "requirement",
        "status",
        "requirement__authorizing_role",
        "requirement__authorizing_roles",
    ]
    search_fields = [
        "student_clearance__student__s_fname",
        "student_clearance__student__s_lname",
        "student_clearance__student__s_studentID",
        "requirement__name",
        "requirement__authorizing_role__name",
        "requirement__authorizing_roles__name",
        "remarks",
    ]
    ordering_fields = ["cleared_at", "status", "id"]

    def get_serializer_class(self):
        if self.action in ["create", "update", "partial_update"]:
            return StudentClearanceItemWriteSerializer
        return StudentClearanceItemReadSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset.none()

        user = self.request.user

        if user.is_superuser:
            return self.queryset

        if hasattr(user, "student"):
            return self.queryset.filter(student_clearance__student=user.student)

        if is_clearance_admin(user) or is_clearance_signatory(user):
            institute_id = get_user_institute_id(user)
            if institute_id is None:
                return self.queryset.none()

            qs = self.queryset.filter(
            student_clearance__student__program__institute_id=institute_id,
        )

            if is_clearance_admin(user):
                return qs.distinct()

            user_group_ids = list(user.groups.values_list("id", flat=True))
            if user_group_ids:
                return qs.filter(
                Q(requirement__authorizing_role_id__in=user_group_ids)
                | Q(requirement__authorizing_roles__id__in=user_group_ids)
            ).distinct()

            return qs.none()

        return self.queryset.none()


    @action(detail=False, methods=["get"], url_path="signatory-dashboard")
    def signatory_dashboard(self, request):
        user = request.user
        if not can_access_clearance_scope(user):
            raise ValidationError({"detail": "You do not have access to this dashboard."})

        queryset = self.get_queryset().filter(status=StudentClearanceItem.ItemStatus.PENDING)

        # Never let Django `is_staff` bypass signatory role scoping.
        # A Dean sees Dean-assigned items only, a Librarian sees Librarian-assigned
        # items only, and multi-role users see the union of their assigned roles.
        if not (getattr(user, "is_superuser", False) or is_clearance_admin(user)):
            user_group_ids = list(user.groups.values_list("id", flat=True))
            if not user_group_ids:
                queryset = queryset.none()
            else:
                queryset = queryset.filter(
                    Q(requirement__authorizing_role_id__in=user_group_ids)
                    | Q(requirement__authorizing_roles__id__in=user_group_ids)
                ).distinct()

        search = request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(student_clearance__student__s_fname__icontains=search)
                | Q(student_clearance__student__s_mname__icontains=search)
                | Q(student_clearance__student__s_lname__icontains=search)
                | Q(student_clearance__student__s_studentID__icontains=search)
                | Q(student_clearance__clearance_type__name__icontains=search)
                | Q(requirement__name__icontains=search)
            )

        year_level = request.query_params.get("year_level")
        if year_level:
            queryset = queryset.filter(student_clearance__student__s_lvl=year_level)

        clearance_type_id = request.query_params.get("clearance_type_id") or request.query_params.get("clearance_type")
        if clearance_type_id:
            queryset = queryset.filter(student_clearance__clearance_type_id=clearance_type_id)

        role_id = request.query_params.get("role_id") or request.query_params.get("authorizing_role")
        if role_id:
            queryset = queryset.filter(
                Q(requirement__authorizing_role_id=role_id)
                | Q(requirement__authorizing_roles__id=role_id)
            )

        pending_items = list(queryset.distinct()[:500])
        grouped = {}

        for item in pending_items:
            clearance = item.student_clearance
            entry = grouped.setdefault(
                clearance.id,
                {
                    "id": clearance.id,
                    "student_id": clearance.student_id,
                    "student_name": format_student_name(clearance.student),
                    "student_number": getattr(clearance.student, "s_studentID", ""),
                    "program": format_program(getattr(clearance.student, "program", None)),
                    "year_level": getattr(clearance.student, "s_lvl", None),
                    "s_set": getattr(clearance.student, "s_set", ""),
                    "clearance_type_id": clearance.clearance_type_id,
                    "clearance_type_name": clearance.clearance_type.name,
                    "academic_year": clearance.clearance_type.academic_year,
                    "semester": clearance.clearance_type.semester,
                    "status": clearance.status,
                    "date_initiated": clearance.date_initiated,
                    "date_completed": clearance.date_completed,
                    "is_enrolled": clearance.is_enrolled,
                    "enrolled_at": clearance.enrolled_at,
                    "enrolled_by_user_id": clearance.enrolled_by_user_id,
                    "items": [],
                },
            )
            entry["items"].append(
                {
                    "id": item.id,
                    "student_clearance_id": item.student_clearance_id,
                    "requirement_id": item.requirement_id,
                    "requirement_name": item.requirement.name,
                    **serialize_item_authorizing_roles(item.requirement),
                    "order_sequence": item.requirement.order_sequence,
                    "status": item.status,
                    "amount_due": item.amount_due,
                    "remarks": item.remarks,
                    "cleared_by_user_id": item.cleared_by_user_id,
                    "cleared_by_name": None,
                    "cleared_at": item.cleared_at,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                }
            )

        assigned_clearances = list(grouped.values())

        return Response(
            {
                "pending_items_count": len(pending_items),
                "students_count": len(assigned_clearances),
                "assigned_clearances": assigned_clearances,
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(tags=["Clearance Role"])
class ClearanceRoleViewSet(viewsets.ModelViewSet):
    queryset = Group.objects.all().order_by("name")
    serializer_class = GroupSlimSerializer
    pagination_class = CustomPagination
    permission_classes = [IsAuthenticated]
    search_fields = ["name"]
    ordering_fields = ["name"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset.none()

        user = self.request.user

        if not (getattr(user, "is_superuser", False) or is_clearance_admin(user)):
            return self.queryset.none()

        return self.queryset.exclude(name__iexact="Students").exclude(name__iexact="Student")

    def perform_create(self, serializer):
        user = self.request.user
        if not (getattr(user, "is_superuser", False) or is_clearance_admin(user)):
            raise ValidationError({"detail": "Only clearance admins can create clearance roles."})
        serializer.save()

    def perform_update(self, serializer):
        user = self.request.user
        if not (getattr(user, "is_superuser", False) or is_clearance_admin(user)):
            raise ValidationError({"detail": "Only clearance admins can update clearance roles."})
        serializer.save()
