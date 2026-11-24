"""Mock adapters for the prototype. No external system is contacted.

Test hooks, based on the patient ID:

* contains ``outage``: every search raises ``ConnectorUnavailable``
* contains ``badcreds``: every search raises ``ConnectorError``
"""

from __future__ import annotations

import hashlib

from .base import (
    ConnectorError,
    ConnectorUnavailable,
    PatientNotifier,
    SearchResult,
    SourceConnector,
    SubjectRef,
)


class MockSourceConnector(SourceConnector):
    def search(self, subject: SubjectRef) -> SearchResult:
        if "outage" in subject.patient_id:
            raise ConnectorUnavailable(f"{self.display_name} is not responding (mock outage).")
        if "badcreds" in subject.patient_id:
            raise ConnectorError(f"{self.display_name} rejected the service credentials (mock).")
        digest = hashlib.sha256(f"{self.system}:{subject.patient_id}".encode()).hexdigest()
        found = int(digest[:4], 16) % 15
        return SearchResult(
            records_found=found,
            summary=(
                f"Automated mock search of {self.display_name}: {found} record(s) matched "
                "the patient ID. No data was copied."
            ),
            reference=f"MOCK-{self.system}-{subject.case_id}-{digest[:8].upper()}",
        )


class PortalOnlyNotifier(PatientNotifier):
    """Records that a notification would be sent. Sends nothing."""

    channel = "portal-only (mock)"

    def notify(self, *, patient_id: str, case_id: int, message: str) -> str:
        return f"MOCK-NOTIFY-{case_id}-{hashlib.sha256(message.encode()).hexdigest()[:8].upper()}"
