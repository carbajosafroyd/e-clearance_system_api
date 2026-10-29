"""
Serializers for the fees app.

Split into read/write fee shapes and the generation-batch payloads, but
re-exported flat so `from api.fees.serializers import FeeReadMiniSerializer`
keeps working for the six other apps that import from here.
"""

from .batch_serializers import (
    DeleteGeneratedFeeBtach,
    GenerateAttendanceFees,
    GeneratedFeeBatchSerializer,
    GenerateFees,
    GenerationAcceptedSerializer,
    ReadGeneratedFeeBatch,
    StudentScopeMixin,
)
from .fee_serializers import (
    AttendanceFeeBreakdownSerializer,
    AttendanceFeeSettingsSerializer,
    AttendanceRecordReadMiniSerializer,
    FeeCreateSerializer,
    FeeDeleteSerializer,
    FeeReadMiniSerializer,
    FeeReadMiniV2Serializer,
    FeeReadSerializer,
    FeeReadSlimSerializer,
    FeeUpdateSerializer,
    FeeWaiveSerializer,
)

__all__ = [
    # fees
    "AttendanceFeeBreakdownSerializer",
    "AttendanceFeeSettingsSerializer",
    "AttendanceRecordReadMiniSerializer",
    "FeeCreateSerializer",
    "FeeDeleteSerializer",
    "FeeReadMiniSerializer",
    "FeeReadMiniV2Serializer",
    "FeeReadSerializer",
    "FeeReadSlimSerializer",
    "FeeUpdateSerializer",
    "FeeWaiveSerializer",
    # generation batches
    "DeleteGeneratedFeeBtach",
    "GenerateAttendanceFees",
    "GenerateFees",
    "GeneratedFeeBatchSerializer",
    "GenerationAcceptedSerializer",
    "ReadGeneratedFeeBatch",
    "StudentScopeMixin",
]
