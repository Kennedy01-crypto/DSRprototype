"""Errors raised by the workflow service layer, and how the API reports them.

Services raise these plain exceptions so they can be called from API views,
Celery tasks, management commands or future integrations alike. The DRF
exception handler below is the only place that turns them into HTTP responses.
"""

from __future__ import annotations

from typing import Any

from django_fsm import TransitionNotAllowed
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


class WorkflowError(Exception):
    """Base class for errors raised by ``dsr.services``."""


class CaseNotFound(WorkflowError):
    """The case (or a record on it) does not exist, or is not visible to the caller."""


class WorkflowConflict(WorkflowError):
    """The action is not allowed in the case's current state. Nothing was changed."""


class WorkflowForbidden(WorkflowError):
    """The caller's role is allowed, but not for this particular case or record."""


class WorkflowValidationError(WorkflowError):
    """Input that is valid on its own but not for this case, keyed by field."""

    def __init__(self, errors: dict[str, Any]) -> None:
        super().__init__(errors)
        self.errors = errors


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    if isinstance(exc, CaseNotFound):
        return Response({"detail": str(exc) or "Not found."}, status=status.HTTP_404_NOT_FOUND)
    if isinstance(exc, WorkflowForbidden):
        return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
    if isinstance(exc, WorkflowValidationError):
        return Response(exc.errors, status=status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, (WorkflowConflict, TransitionNotAllowed)):
        return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
    return drf_exception_handler(exc, context)
