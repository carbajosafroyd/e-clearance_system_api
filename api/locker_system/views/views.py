from django.db import transaction
from django.shortcuts import get_object_or_404

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied

from drf_spectacular.utils import extend_schema

from django.utils import timezone
from datetime import timedelta

from api.locker_system.models import LockerSubscription

from api.locker_system.models import LockerSubscriptionRequest
from api.locker_system.serializers import (
    LockerSubscriptionCancelSerializer,
    LockerSubscriptionCreateSerializer,
    LockerSubscriptionReadSerializer,
    LockerSubscriptionRequestCreateSerializer,
    LockerSubscriptionRequestReadSerializer,
    LockerSubscriptionRequestStatusSerializer,
)
from api.utils.MixIns import UserContextMixin
from api.utils.custom_paginations import CustomPagination

# from api.utils.helpers.app_settings import get_setting
# from .filters import filter_available_lockers_this_year_sem

# from .models import (
#     LockerFeeRecord,
#     LockerFeeBatch,
#     LockerSemesterFeeConfig,
#     LockerSubscription,
# )
# from .serializers import (
#     LockerFeeRecordSerializer,
#     LockerFeeBatchSerializer,
#     LockerFeeGenerateSerializer,
# )




    # def get_queryset(self):
    #     user = self.request.user

    #     if hasattr(user, "student"):
    #         return self.queryset.filter(student=user.student)

    #     return self.queryset


# @extend_schema(tags=["Locker Fees"])
# class LockerFeeRecordViewSet(viewsets.ModelViewSet):
#     """
#     CRUD for LockerFeeRecord. Generation is in a separate endpoint (below).
#     """

#     queryset = LockerFeeRecord.objects.all().select_related(
#         "student", "subscription", "batch", "institute"
#     )
#     serializer_class = LockerFeeRecordSerializer
#     permission_classes = [IsAuthenticated]
#     ordering_fields = ["generated_at", "amount"]
#     search_fields = ["student__full_name", "student__student_number"]


# @extend_schema(tags=["Locker Fees"])
# class LockerFeeBatchViewSet(viewsets.ModelViewSet):
#     """
#     Manage batches. Use `generate` action on this viewset to create a batch & fee records.
#     Use `rollback` (DELETE) to remove a batch and its fee records.
#     """

#     queryset = LockerFeeBatch.objects.all().select_related("institute", "created_by")
#     serializer_class = LockerFeeBatchSerializer
#     permission_classes = [IsAuthenticated]
#     ordering_fields = ["created_at"]
#     search_fields = ["institute__institute_name"]

#     def get_serializer_class(self):
#         if self.action == "generate":
#             return LockerFeeGenerateSerializer
#         return LockerFeeBatchSerializer

#     @extend_schema(
#         request=LockerFeeGenerateSerializer, responses={201: LockerFeeBatchSerializer}
#     )
#     @action(detail=False, methods=["post"], url_path="generate")
#     def generate(self, request):
#         """
#         POST /api/locker-fee-batches/generate/
#         payload: { "institute_id": 1, "semester": "1st", "year": 2025, "force": False }
#         Only staff users can generate/force-rollover.
#         """
#         if not request.user.is_staff:
#             raise PermissionDenied("Only staff users may generate locker fee batches.")

#         serializer = LockerFeeGenerateSerializer(data=request.data)
#         serializer.is_valid(raise_exception=True)

#         institute_id = serializer.validated_data["institute_id"]
#         semester = serializer.validated_data["semester"]
#         year = serializer.validated_data["year"]
#         force = serializer.validated_data["force"]

#         # get config (we validated existence in serializer)
#         config = LockerSemesterFeeConfig.objects.get(
#             institute_id=institute_id, semester=semester, year=year
#         )

#         existing_batch = LockerFeeBatch.objects.filter(
#             institute_id=institute_id, semester=semester, year=year
#         ).first()

#         if existing_batch and not force:
#             return Response(
#                 {"detail": "Batch already exists. Use force=True to regenerate."},
#                 status=status.HTTP_400_BAD_REQUEST,
#             )

#         # Use atomic transaction for safe generation
#         with transaction.atomic():
#             if existing_batch and force:
#                 # delete existing batch and all linked fee records
#                 existing_batch.delete()

#             # create new batch
#             new_batch = LockerFeeBatch.objects.create(
#                 institute_id=institute_id,
#                 semester=semester,
#                 year=year,
#                 created_by=request.user,
#             )

#             # select subscriptions to bill: active subscriptions for students in the institute
#             subscriptions_qs = LockerSubscription.objects.filter(
#                 is_active=True, student__institute_id=institute_id
#             ).select_related("student")

#             created_records = []
#             for sub in subscriptions_qs:
#                 # note: unique_together in model prevents duplicates per student/sem/year
#                 rec = LockerFeeRecord.objects.create(
#                     batch=new_batch,
#                     student=sub.student,
#                     subscription=sub,
#                     institute_id=institute_id,
#                     semester=semester,
#                     year=year,
#                     amount=config.amount,
#                 )
#                 created_records.append(rec)

#         batch_ser = LockerFeeBatchSerializer(new_batch, context={"request": request})
#         return Response(batch_ser.data, status=status.HTTP_201_CREATED)

#     @extend_schema(responses={204: None})
#     @action(detail=True, methods=["delete"], url_path="rollback")
#     def rollback(self, request, pk=None):
#         """
#         DELETE /api/locker-fee-batches/{pk}/rollback/
#         Deletes the batch and all its fee records.
#         Only staff users may rollback.
#         """
#         if not request.user.is_staff:
#             raise PermissionDenied("Only staff users may rollback locker fee batches.")

#         batch = get_object_or_404(LockerFeeBatch, pk=pk)

#         with transaction.atomic():
#             batch.delete()

#         return Response(status=status.HTTP_204_NO_CONTENT)
