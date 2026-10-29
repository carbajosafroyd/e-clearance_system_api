from __future__ import annotations

import json
import os
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from api.clearance.bridge_errors import ClearanceBridgeError


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def _normalize_base_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if not raw.lower().startswith(("http://", "https://")):
        raw = "http://" + raw.lstrip("/")
    parts = urlsplit(raw)
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _join(base: str, path: str) -> str:
    route = str(path or "").strip()
    if not route.startswith("/"):
        route = "/" + route
    return base.rstrip("/") + route


class N8nGateway:
    def __init__(self) -> None:
        self.base_url = _normalize_base_url(_env("N8N_BASE_URL", "http://127.0.0.1:5678"))
        self.session_path = _env("N8N_SESSION_VALIDATE_PATH", "/webhook/cms/auth/session")
        self.collection_context_path = _env(
            "N8N_COLLECTION_CONTEXT_PATH",
            "/webhook/cms/bff/collection-context",
        )
        self.tenant_id = _env("TENANT_ID", _env("CLMS_TENANT_ID", "TENANT-DNSC"))
        self.unit_id = _env("UNIT_ID", _env("CLMS_UNIT_ID", "UNIT-IC"))
        try:
            self.timeout = float(_env("CLMS_REQUEST_TIMEOUT_SECONDS", "30"))
        except ValueError:
            self.timeout = 30.0

    @staticmethod
    def _incoming_headers(request) -> dict[str, str]:
        return {str(key).lower(): str(value) for key, value in request.headers.items()}

    def _forward_headers(self, request) -> dict[str, str]:
        incoming = self._incoming_headers(request)
        headers = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": _env(
        "CLMS_HTTP_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/153.0.0.0 Safari/537.36",
    ),
    "X-Tenant-ID": incoming.get("x-tenant-id", self.tenant_id),
    "X-Unit-ID": incoming.get("x-unit-id", self.unit_id),
    "X-Request-ID": incoming.get("x-request-id", ""),
}
        if incoming.get("cookie"):
            headers["Cookie"] = incoming["cookie"]
        if incoming.get("authorization"):
            headers["Authorization"] = incoming["authorization"]
        if incoming.get("x-assignment-id"):
            headers["X-Assignment-ID"] = incoming["x-assignment-id"]
        return headers

    @staticmethod
    def _decode(raw: bytes) -> Any:
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    @staticmethod
    def _unwrap_data(payload: Any) -> Any:
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    def _post(self, url: str, request, body: dict[str, Any], *, unavailable_code: str, unavailable_message: str) -> Any:
        payload = json.dumps(body).encode("utf-8")
        req = Request(
            url,
            data=payload,
            method="POST",
            headers=self._forward_headers(request),
        )
        try:
            with urlopen(req, timeout=self.timeout) as response:
                raw = response.read()
                status_code = int(getattr(response, "status", 200))
                decoded = self._decode(raw)
        except HTTPError as exc:
            raw = exc.read()
            status_code = int(exc.code)
            decoded = self._decode(raw)
        except (URLError, socket.timeout, TimeoutError) as exc:
            raise ClearanceBridgeError(503, unavailable_code, unavailable_message) from exc

        if 200 <= status_code < 300:
            return decoded

        if isinstance(decoded, dict):
            error = decoded.get("error")
            if isinstance(error, dict):
                raise ClearanceBridgeError(
                    status_code,
                    str(error.get("code") or "N8N_REQUEST_FAILED"),
                    str(error.get("message") or "The CLMS workflow rejected the request."),
                    error.get("details") or {},
                )
            detail = decoded.get("detail")
            if detail:
                raise ClearanceBridgeError(status_code, "N8N_REQUEST_FAILED", str(detail), decoded)

        raise ClearanceBridgeError(status_code, "N8N_REQUEST_FAILED", "The CLMS workflow rejected the request.")

    def validate_session(self, request) -> dict[str, Any]:
        payload = self._post(
            _join(self.base_url, self.session_path),
            request,
            {"tenant_id": self.tenant_id, "unit_id": self.unit_id},
            unavailable_code="AUTH_SERVICE_UNAVAILABLE",
            unavailable_message="The CLMS authentication service is temporarily unavailable.",
        )
        data = self._unwrap_data(payload)
        if not isinstance(data, dict) or data.get("authenticated") is not True:
            raise ClearanceBridgeError(401, "SESSION_REQUIRED", "Your CLMS session is no longer active. Please sign in again.")
        return data

    def collection_context(self, request) -> dict[str, Any]:
        payload = self._post(
            _join(self.base_url, self.collection_context_path),
            request,
            {
                "tenant_id": self.tenant_id,
                "unit_id": self.unit_id,
                "action": "GET_COLLECTION_CONTEXT",
            },
            unavailable_code="COLLECTION_CONTEXT_UNAVAILABLE",
            unavailable_message="The CLMS collection routing context is temporarily unavailable.",
        )
        data = self._unwrap_data(payload)
        if not isinstance(data, dict):
            raise ClearanceBridgeError(502, "COLLECTION_CONTEXT_INVALID", "The CLMS collection routing context returned an unexpected response.")
        return data
