from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from rest_framework.permissions import BasePermission

from .authentication import SimulatedPrincipal

if TYPE_CHECKING:  # Importing these at runtime creates a cycle with DRF settings.
    from rest_framework.request import Request
    from rest_framework.views import APIView


class RolePermission(BasePermission):
    """Allow a request only if its principal's role is declared by the view.

    Views declare ``allowed_roles`` either as a set of roles that applies to
    every method, or as a mapping of HTTP method to roles. This is the default
    permission class, so it fails closed: a view that declares nothing, or a
    method missing from the mapping, is denied to everyone.

    Object-level rules (a contributor's own task, a reviewer's own review)
    stay in the views, because they depend on the case being accessed.
    """

    message = "Your role is not permitted to use this endpoint."

    def has_permission(self, request: Request, view: APIView) -> bool:
        principal = request.user
        if not isinstance(principal, SimulatedPrincipal):
            return False
        allowed = getattr(view, "allowed_roles", None)
        if isinstance(allowed, Mapping):
            allowed = allowed.get(request.method or "", frozenset())
        return bool(allowed) and principal.role in allowed
