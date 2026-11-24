"""Chooses the adapter for each system, from settings."""

from __future__ import annotations

from django.conf import settings
from django.utils.module_loading import import_string

from .base import ConnectorError, PatientNotifier, SourceConnector


def automated_source_systems() -> list[str]:
    """Source-system codes that have a connector configured."""
    return list(getattr(settings, "DSR_SOURCE_CONNECTORS", {}))


def get_connector(system: str) -> SourceConnector:
    from dsr.models import DSRSourceSearch

    path = getattr(settings, "DSR_SOURCE_CONNECTORS", {}).get(system)
    if not path:
        raise ConnectorError(f"No connector is configured for {system}.")
    label = DSRSourceSearch.SourceSystem(system).label
    return import_string(path)(system=system, display_name=label)


def get_patient_notifier() -> PatientNotifier:
    return import_string(
        getattr(
            settings,
            "DSR_PATIENT_NOTIFIER",
            "dsr.integrations.mock.PortalOnlyNotifier",
        )
    )()
