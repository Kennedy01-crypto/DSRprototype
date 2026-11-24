"""Interfaces for source-system connectors and patient notifiers.

To connect a real system (for example CIMS, or Microsoft Dynamics 365
through the Dataverse Web API), subclass ``SourceConnector``, implement
``search``, and point ``DSR_SOURCE_CONNECTORS[<code>]`` at the class in
settings. Nothing else in the workflow changes.

Rules every implementation must follow:

* Return counts and references only. Never put record contents in a
  ``SearchResult``; results are stored on the case and shown to staff.
* Raise ``ConnectorUnavailable`` for problems that may pass (timeouts,
  outages, rate limits). The job is retried with backoff.
* Raise ``ConnectorError`` for problems a retry cannot fix (bad
  credentials, unknown subject format). The job fails at once and a person
  is asked to act.
* Be safe to call more than once for the same request: a job can be
  retried after a crash without knowing whether the last call got through.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SubjectRef:
    """Who to look for. Real connectors map this to the system's own IDs."""

    patient_id: str
    case_id: int


@dataclass(frozen=True)
class SearchResult:
    records_found: int
    summary: str  # One line for staff, e.g. "12 lab reports matched."
    reference: str  # Where the evidence is kept, e.g. an export or ticket ID.


class ConnectorUnavailable(Exception):
    """Temporary failure. The job will be retried."""


class ConnectorError(Exception):
    """Permanent failure. The job stops and needs a person."""


class SourceConnector(ABC):
    def __init__(self, system: str, display_name: str) -> None:
        self.system = system
        self.display_name = display_name

    @abstractmethod
    def search(self, subject: SubjectRef) -> SearchResult:
        """Find what this system holds about the subject."""


class PatientNotifier(ABC):
    """Tells a patient there is something new in the portal.

    Messages must not contain case details: they only point the patient to
    the portal, where they sign in to read the update.
    """

    channel: str = "unknown"

    @abstractmethod
    def notify(self, *, patient_id: str, case_id: int, message: str) -> str:
        """Send the message and return the provider's reference."""
