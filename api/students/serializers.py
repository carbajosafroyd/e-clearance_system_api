from rest_framework import serializers
from api.students.models import Student
from api.programs import serializers as ProgramSerializers
from api.users.models import User
from api.users.serializers import UserReadSlimSerializer
from api.utils.cloudinary.cloudinary_utils import get_file_url


class StudentCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Student
        fields = "__all__"
        read_only_fields = ["id"]


class StudentReadSerializer(serializers.ModelSerializer):
    user = UserReadSlimSerializer()
    program = ProgramSerializers.ProgramReadSerializer()

    class Meta:
        model = Student
        fields = "__all__"
        # exclude = ["user"]


class StudentUserSlimReadSerializer(serializers.ModelSerializer):
    """Shortest schema for retrieving student users data"""
    profile = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "profile",
        ]
        read_only_fields = fields

    def get_profile(self, obj) -> str:
        return get_file_url(obj.profile, version=obj.cloudinary_version)


class StudentReadSlimSerializer(serializers.ModelSerializer):
    # program = ProgramSerializers.ProgramSlimReadSerializer()
    program_name = serializers.CharField(source="program.name", read_only=True)
    user = StudentUserSlimReadSerializer()

    class Meta:
        model = Student
        exclude = ["created_at", "updated_at", "updated_by"]


class StudentReadMiniSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    program_name = serializers.CharField(source="program.name")

    class Meta:
        model = Student
        fields = [
            "id",
            # "user",
            # "s_fname",
            # "s_mname",
            # "s_lname",
            # "s_suffix",
            # "s_email",
            "full_name",
            "program_name",
            "s_set",
            "s_lvl",
        ]
        read_only_fields = fields

    def get_full_name(self, obj) -> str:
        return " ".join(
            filter(
                None,
                [
                    obj.s_fname,
                    obj.s_mname,
                    obj.s_lname,
                    obj.s_suffix,
                ],
            )
        )


class StudentImportFileSerializer(serializers.Serializer):
    file = serializers.FileField()


class StudentImportSerializer(serializers.ModelSerializer):

    class Meta:
        model = Student
        fields = [
            "s_studentID",
            "s_rfid",
            "s_fname",
            "s_mname",
            "s_lname",
            "s_suffix",
            "s_set",
            "s_lvl",
            "s_status",
            "program",
            # "user_id",
        ]


class StudentDeleteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Student
        fields = ["id"]


# class StudentLiteReadSerializer(serializers.ModelSerializer):
#     program_id = serializers.CharField(source="program.name", read_only=True)
#     program_name = serializers.CharField(source="program.name", read_only=True)
#     institute_id = serializers.CharField(source="program.institute.id", read_only=True)
#     institute_name = serializers.CharField(source="program.institute.institute_name", read_only=True)
#     school_id = serializers.CharField(source="program.institute.school.id", read_only=True)
#     school_name = serializers.CharField(source="program.institute.school", read_only=True)

#     class Meta:
#         model = Student
#         exclude = ["created_at", "updated_at"]
