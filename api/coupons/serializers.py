from __future__ import annotations

from decimal import Decimal

from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.collection_categories.models import CollectionCategory
from api.students.models import Student

from .models import Coupon, CouponBatch, CouponRedemption


class CouponBatchReadSerializer(serializers.ModelSerializer):
    category_names = serializers.SerializerMethodField()
    value_label = serializers.SerializerMethodField()
    term_label = serializers.SerializerMethodField()
    issued_by_username = serializers.CharField(
        source="issued_by.username", read_only=True, default=None
    )
    counts = serializers.SerializerMethodField()

    class Meta:
        model = CouponBatch
        fields = [
            "id",
            "name",
            "reason",
            "kind",
            "value_type",
            "value",
            "value_label",
            "max_discount",
            "categories",
            "category_names",
            "academic_year",
            "semester",
            "term_label",
            "min_fee_balance",
            "valid_from",
            "expires_at",
            "target_program",
            "target_year_level",
            "one_per_student",
            "total_created",
            "status",
            "issued_by_username",
            "revoked_at",
            "revocation_reason",
            "counts",
            "created_at",
        ]
        read_only_fields = fields

    def get_value_label(self, obj) -> str:
        return obj.describe_value()

    def get_term_label(self, obj) -> str:
        return obj.describe_term()

    def get_category_names(self, obj) -> list[str]:
        # "Any fee" is a real answer and the UI must not render it as a blank.
        return [c.category_name for c in obj.categories.all()]

    def get_counts(self, obj) -> dict:
        """
        Held and spent are different states and the difference matters: a
        recall voids what is merely held and leaves what was spent, so a
        coordinator deciding whether to recall needs both numbers.
        """
        coupons = obj.coupons.all()
        spent_ids = set(
            CouponRedemption.objects.filter(
                coupon__batch=obj, status=CouponRedemption.Status.APPLIED
            ).values_list("coupon_id", flat=True)
        )
        spent = sum(1 for c in coupons if c.pk in spent_ids)
        held = sum(
            1 for c in coupons if c.pk not in spent_ids and c.uses_taken > 0
        )
        return {
            "total": len(coupons),
            "redeemed": spent,
            "claimed": held,
            "unspent": len(coupons) - spent - held,
            "revoked": sum(1 for c in coupons if c.status == Coupon.Status.REVOKED),
        }


class CouponBatchCreateSerializer(serializers.ModelSerializer):
    """
    Issuing a batch and minting its coupons in one call.

    Validation is strict here on purpose: this is the cheapest place to catch
    a mistake, because nothing has been printed yet.
    """

    #: Bound coupons — one per student. The default the UI should push, since
    #: a bound code is worthless to whoever finds it.
    student_school_ids = serializers.ListField(
        child=serializers.CharField(), required=False, allow_empty=True
    )
    #: Bearer coupons, when no student list is given.
    quantity = serializers.IntegerField(required=False, min_value=1, max_value=2000)
    max_redemptions = serializers.IntegerField(required=False, min_value=1, default=1)
    code_prefix = serializers.CharField(
        required=False, allow_blank=True, max_length=8, default=""
    )

    class Meta:
        model = CouponBatch
        fields = [
            "name",
            "reason",
            "kind",
            "value_type",
            "value",
            "max_discount",
            "categories",
            "academic_year",
            "semester",
            "min_fee_balance",
            "valid_from",
            "expires_at",
            "target_program",
            "target_year_level",
            "one_per_student",
            "student_school_ids",
            "quantity",
            "max_redemptions",
            "code_prefix",
        ]

    def validate(self, attrs):
        institute = self.context["request"].user.institute
        if institute is None:
            raise serializers.ValidationError(
                {"detail": "Your account is not attached to an institute."}
            )

        value_type = attrs.get("value_type", CouponBatch.ValueType.FIXED)
        value = attrs.get("value") or Decimal("0")

        if value_type == CouponBatch.ValueType.PERCENT and value > 100:
            raise serializers.ValidationError(
                {"value": "A percentage cannot be more than 100."}
            )

        expires_at = attrs.get("expires_at")
        valid_from = attrs.get("valid_from") or timezone.now()
        if expires_at and expires_at <= valid_from:
            raise serializers.ValidationError(
                {"expires_at": "The window must end after it starts."}
            )
        if expires_at and expires_at <= timezone.now():
            raise serializers.ValidationError(
                {"expires_at": "That is already in the past."}
            )

        # A category from another institute produces a coupon that looks
        # valid and refuses every fee, because no student here can ever hold
        # a fee in it.
        for category in attrs.get("categories", []):
            if category.institute_id != institute.pk:
                raise serializers.ValidationError(
                    {
                        "categories": (
                            f"'{category.category_name}' belongs to another "
                            f"institute."
                        )
                    }
                )

        floor = attrs.get("min_fee_balance")
        if (
            floor
            and value_type == CouponBatch.ValueType.FIXED
            and floor < value
        ):
            raise serializers.ValidationError(
                {
                    "min_fee_balance": (
                        "Set this to at least the coupon's own value, or the "
                        "coupon can be spent where it is worth less than its "
                        "face and the student loses the difference."
                    )
                }
            )

        if not attrs.get("student_school_ids") and not attrs.get("quantity"):
            raise serializers.ValidationError(
                {
                    "detail": (
                        "Give either a list of students or how many bearer "
                        "coupons to make."
                    )
                }
            )
        return attrs

    def validate_student_school_ids(self, value):
        if not value:
            return []

        institute = self.context["request"].user.institute
        students = Student.objects.filter(
            s_studentID__in=value, program__institute=institute
        )
        found = {s.s_studentID: s for s in students}

        missing = [sid for sid in value if sid not in found]
        if missing:
            raise serializers.ValidationError(
                f"Not in this institute: {', '.join(missing[:10])}"
                + (" …" if len(missing) > 10 else "")
            )
        # Preserve the caller's order so the export matches their list.
        return [found[sid] for sid in value]


class CouponReadSerializer(serializers.ModelSerializer):
    student_school_id = serializers.CharField(
        source="student.s_studentID", read_only=True, default=None
    )
    student_name = serializers.SerializerMethodField()
    batch_name = serializers.CharField(source="batch.name", read_only=True)
    value_label = serializers.SerializerMethodField()
    term_label = serializers.SerializerMethodField()
    expires_at = serializers.DateTimeField(source="batch.expires_at", read_only=True)
    applies_to = serializers.SerializerMethodField()
    #: Whether any of it actually reached a fee.
    #:
    #: `uses_taken` cannot answer this: claiming takes a use, so a coupon
    #: somebody is holding but has not spent reads as 1/1 while having moved
    #: no money. Voiding is allowed on that one and refused on a spent one,
    #: and the UI has to gate its button on the same fact the service does or
    #: it will offer a button whose request comes back 400.
    is_spent = serializers.BooleanField(read_only=True)
    status_label = serializers.CharField(read_only=True)

    class Meta:
        model = Coupon
        fields = [
            "id",
            "code",
            "batch",
            "batch_name",
            "student",
            "student_school_id",
            "student_name",
            "value_label",
            "term_label",
            "applies_to",
            "max_redemptions",
            "uses_taken",
            "is_spent",
            "status",
            "status_label",
            "expires_at",
            "revoked_at",
            "revocation_reason",
            "created_at",
        ]
        read_only_fields = fields

    def get_student_name(self, obj) -> str | None:
        if not obj.student:
            return None
        return f"{obj.student.s_fname} {obj.student.s_lname}".strip()

    def get_value_label(self, obj) -> str:
        return obj.batch.describe_value()

    def get_term_label(self, obj) -> str:
        return obj.batch.describe_term()

    def get_applies_to(self, obj) -> str:
        names = [c.category_name for c in obj.batch.categories.all()]
        return ", ".join(names) if names else "Any fee"


class CouponRedemptionReadSerializer(serializers.ModelSerializer):
    code = serializers.CharField(source="coupon.code", read_only=True)
    reason = serializers.CharField(source="coupon.batch.reason", read_only=True)
    value_label = serializers.SerializerMethodField()
    fee_category = serializers.CharField(
        source="fee.category.category_name", read_only=True, default=None
    )
    student_name = serializers.SerializerMethodField()
    student_school_id = serializers.CharField(
        source="student.s_studentID", read_only=True, default=None
    )
    student_program = serializers.CharField(
        source="student.program.acronym", read_only=True, default=None
    )
    status_label = serializers.CharField(
        source="get_status_display", read_only=True
    )
    #: Who pressed the button, for the two acts that need a name against
    #: them. A reversal puts money back onto somebody's fee; "the system did
    #: it" is not an answer anyone can act on.
    reversed_by_username = serializers.CharField(
        source="reversed_by.username", read_only=True, default=None
    )
    applied_by_username = serializers.CharField(
        source="updated_by.username", read_only=True, default=None
    )
    #: The coupon behind this claim, so a student's list can open the code
    #: itself. Without it the row knows what it is worth and nothing about
    #: the thing it belongs to.
    coupon = serializers.PrimaryKeyRelatedField(read_only=True)
    batch_name = serializers.CharField(source="coupon.batch.name", read_only=True)
    expires_at = serializers.DateTimeField(
        source="coupon.batch.expires_at", read_only=True
    )
    applies_to = serializers.SerializerMethodField()
    term_label = serializers.SerializerMethodField()
    #: The coupon's own state. A held claim outlives a void or an expiry, so
    #: a student's list would otherwise call a dead coupon "ready to use".
    coupon_status = serializers.CharField(source="coupon.status", read_only=True)
    coupon_status_label = serializers.CharField(
        source="coupon.get_status_display", read_only=True
    )
    revocation_reason = serializers.CharField(
        source="coupon.revocation_reason", read_only=True, default=None
    )

    class Meta:
        model = CouponRedemption
        fields = [
            "id",
            "coupon",
            "code",
            "coupon_status",
            "coupon_status_label",
            "revocation_reason",
            "batch_name",
            "reason",
            "value_label",
            "applies_to",
            "term_label",
            "expires_at",
            "student",
            "student_name",
            "student_school_id",
            "student_program",
            "fee",
            "fee_category",
            "amount",
            "previous_balance",
            "previous_discount_total",
            "status",
            "status_label",
            "applied_at",
            "applied_by_username",
            "reversed_at",
            "reversed_by_username",
            "reversal_reason",
            "created_at",
        ]
        read_only_fields = fields

    def get_value_label(self, obj) -> str:
        return obj.coupon.batch.describe_value()

    def get_applies_to(self, obj) -> str:
        names = [c.category_name for c in obj.coupon.batch.categories.all()]
        # "Any fee" is a real answer and the UI must not render it as a blank.
        return ", ".join(names) if names else "Any fee"

    def get_term_label(self, obj) -> str:
        return obj.coupon.batch.describe_term()

    def get_student_name(self, obj) -> str:
        return f"{obj.student.s_fname} {obj.student.s_lname}".strip()


class CouponDetailSerializer(CouponReadSerializer):
    """One code, with everything that has happened to it.

    A code on a printed sheet answers "what is this worth". A code somebody
    is asking about at a desk answers a different question - who has it, did
    they spend it, on what, and can it be undone - and none of that is on the
    coupon itself. It is on its redemptions, so they come with it rather than
    behind a second request the UI has to know to make.
    """

    redemptions = serializers.SerializerMethodField()
    batch_reason = serializers.CharField(source="batch.reason", read_only=True)
    valid_from = serializers.DateTimeField(source="batch.valid_from", read_only=True)
    max_discount = serializers.DecimalField(
        source="batch.max_discount",
        max_digits=12,
        decimal_places=2,
        read_only=True,
        default=None,
    )
    min_fee_balance = serializers.DecimalField(
        source="batch.min_fee_balance",
        max_digits=12,
        decimal_places=2,
        read_only=True,
        default=None,
    )
    issued_by_username = serializers.CharField(
        source="batch.issued_by.username", read_only=True, default=None
    )

    class Meta(CouponReadSerializer.Meta):
        fields = CouponReadSerializer.Meta.fields + [
            "batch_reason",
            "valid_from",
            "max_discount",
            "min_fee_balance",
            "issued_by_username",
            "redemptions",
        ]
        read_only_fields = fields

    @extend_schema_field(CouponRedemptionReadSerializer(many=True))
    def get_redemptions(self, obj):
        """The history, scoped to who is asking.

        A shared code can be claimed by a dozen students, and this endpoint is
        reachable by any of them - `view_coupon` is a student permission, and
        the queryset lets a student read a coupon of their own. Handing the
        full list back would tell each of them the names, school ids and
        programs of everybody else who claimed it.

        Staff see all of it, because "who has this and what did they do with
        it" is the entire question the desk is asking.
        """
        rows = list(obj.redemptions.all())

        # Every one of these belongs to `obj` by construction, and `obj` has
        # its batch and categories loaded already. Priming the relation stops
        # each row re-fetching the same coupon and the same category list -
        # two queries per redemption for data sitting in memory.
        for row in rows:
            row.coupon = obj

        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is not None and not (user.is_superuser or user.is_staff):
            student = getattr(user, "student", None)
            rows = [row for row in rows if student and row.student_id == student.pk]

        return CouponRedemptionReadSerializer(
            rows, many=True, context=self.context
        ).data


class ClaimSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=32)


class ApplySerializer(serializers.Serializer):
    code = serializers.CharField(max_length=32)
    fee = serializers.IntegerField()


class PreviewSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=32)
    fee = serializers.IntegerField()


class CouponReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)


class CouponReverseSerializer(CouponReasonSerializer):
    #: The caller has seen the "this fee is already paid" warning and means
    #: it. Never defaulted to true.
    confirm_paid_fee = serializers.BooleanField(required=False, default=False)
