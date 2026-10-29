from __future__ import annotations

import logging

from rest_framework.exceptions import APIException
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from api.clearance.bridge_errors import ClearanceBridgeError
from api.clearance.services.adapter_compat_service import ClearanceAdapterCompatService, _bool_query

logger = logging.getLogger(__name__)
service = ClearanceAdapterCompatService()


class AdapterCompatAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer]

    def handle_exception(self, exc):
        if isinstance(exc, ClearanceBridgeError):
            return Response(
                {"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
                status=exc.status_code,
            )
        if isinstance(exc, APIException):
            return super().handle_exception(exc)
        logger.exception("Unhandled CLMS compatibility API error", exc_info=exc)
        return Response(
            {
                "error": {
                    "code": "CLEARANCE_API_INTERNAL_ERROR",
                    "message": "The clearance API encountered a temporary error.",
                    "details": {},
                }
            },
            status=500,
        )

    @staticmethod
    def force_refresh(request) -> bool:
        return _bool_query(request.query_params.get("force_refresh"))


class IdentityResolveView(AdapterCompatAPIView):
    def post(self, request):
        if not isinstance(request.data, dict):
            raise ClearanceBridgeError(422, "EMAIL_INVALID", "Provide an object containing an institutional email address.")
        email = str(request.data.get("email") or "").strip()
        data = service.resolve_identity(email, force_refresh=self.force_refresh(request))
        return Response({"data": data})


class StudentListView(AdapterCompatAPIView):
    def get(self, request):
        data = service.list_students(request, force_refresh=self.force_refresh(request))
        return Response({"data": data})


class StudentCollectionStatusView(AdapterCompatAPIView):
    def get(self, request, student_number: str):
        data = service.student_status(request, student_number, force_refresh=self.force_refresh(request))
        return Response({"data": data})


class CollectionCategoriesView(AdapterCompatAPIView):
    def get(self, request):
        data = service.categories(request, force_refresh=self.force_refresh(request))
        return Response({"data": data})


class CurrentCollectionCategoriesView(AdapterCompatAPIView):
    def get(self, request):
        data = service.current_categories(request, force_refresh=self.force_refresh(request))
        return Response({"data": data})


class CollectionQueuesView(AdapterCompatAPIView):
    def get(self, request):
        filters = {
            "academic_period": request.query_params.get("academic_period"),
            "program": request.query_params.get("program"),
            "year_level": request.query_params.get("year_level"),
            "set_code": request.query_params.get("set_code"),
            "task_name": request.query_params.get("task_name"),
            "search": request.query_params.get("search", ""),
        }
        data = service.list_queues(request, filters, force_refresh=self.force_refresh(request))
        return Response({"data": data})


class CollectionQueueDetailView(AdapterCompatAPIView):
    def get(self, request, queue_id: str):
        data = service.open_queue(request, queue_id, force_refresh=self.force_refresh(request))
        return Response({"data": data})


class ClearanceBridgeHealthView(AdapterCompatAPIView):
    def get(self, request):
        return Response(
            {
                "status": "ok",
                "service": "DNSC Clearance API",
                "compatibility": "CLMS Adapter API",
                "n8n_enabled": True,
                "source_system": "DNSC_SYSTEMS_API",
                "version": "2.2.0",
            }
        )
