"""Request IDs and structured logs.

Every HTTP request gets an ID: the caller's ``X-Request-ID`` if it looks
safe, otherwise a new one. The ID is returned in the response header and
added to every log line written while the request is handled, so one
user-visible problem can be traced through the frontend and backend logs.
The Next.js proxy sends its own ID, so both sides log the same value.

Logs are one JSON object per line, ready for a log collector.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections.abc import Callable
from contextvars import ContextVar
from datetime import datetime, timezone

from django.http import HttpRequest, HttpResponse

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")
logger = logging.getLogger("dsr.requests")


class RequestIdMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        supplied = request.headers.get("X-Request-ID", "")
        request_id = supplied if SAFE_REQUEST_ID.fullmatch(supplied) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.monotonic()
        try:
            response = self.get_response(request)
            response["X-Request-ID"] = request_id
            if request.path not in ("/healthz", "/readyz"):
                user = getattr(request, "user", None)
                role = getattr(user, "role", None)
                logger.info(
                    "request",
                    extra={
                        "method": request.method,
                        "path": request.path,
                        "status": response.status_code,
                        "duration_ms": round((time.monotonic() - started) * 1000, 1),
                        "actor": f"{role}:{user.patient_id}" if role else "anonymous",
                    },
                )
            return response
        finally:
            request_id_var.reset(token)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "request_id"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        entry.update(
            {key: value for key, value in vars(record).items() if key not in _STANDARD_ATTRS}
        )
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)
