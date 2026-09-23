from __future__ import annotations

import re
from dataclasses import dataclass

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed


@dataclass(frozen=True)
class SimulatedPrincipal:
    """Represents identity already verified by the external Patient Portal."""

    patient_id: str
    role: str = "patient"

    @property
    def is_authenticated(self) -> bool:
        return True


class PatientPortalJWTAuthentication(BaseAuthentication):
    """Prototype parser for portal-issued tokens: ``Bearer patient:<id>`` or ``Bearer dpo:<id>``."""

    token_pattern = re.compile(r"^(patient|dpo):([A-Za-z0-9_.:-]{1,128})$")

    def authenticate(self, request: object) -> tuple[SimulatedPrincipal, str] | None:
        header = request.META.get("HTTP_AUTHORIZATION", "")  # type: ignore[attr-defined]
        if not header:
            return None
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise AuthenticationFailed("Use a Bearer token from the Patient Portal")
        match = self.token_pattern.fullmatch(token)
        if match is None:
            raise AuthenticationFailed("Invalid simulated portal token")
        role, principal_id = match.groups()
        return SimulatedPrincipal(patient_id=principal_id, role=role), token