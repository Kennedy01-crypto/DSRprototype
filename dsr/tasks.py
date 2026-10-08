from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .models import DSRAuditLog, DSRDataQuarantine, DSRRequest


def _simulate_fides_query(patient_id: str) -> dict[str, Any]:
    return {
        "patient_id": patient_id,
        "systems": {
            "emr": {"records": []},
            "hmis": {"records": []},
            "labs": {"records": []},
            "billing": {"records": []},
        },
        "collected_at": timezone.now().isoformat(),
    }


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def trigger_fides_data_collection(self: Any, dsr_id: int) -> None:
    dsr = DSRRequest.objects.get(pk=dsr_id)
    if dsr.state != DSRRequest.State.DEPT_SEARCH:
        return
    payload = _simulate_fides_query(dsr.patient_id)
    payload_bytes = json.dumps(payload, sort_keys=True).encode()
    digest = hashlib.sha256(payload_bytes).hexdigest()
    with transaction.atomic():
        DSRDataQuarantine.objects.update_or_create(
            dsr=dsr,
            defaults={
                "storage_reference": f"s3://encrypted-dsr-quarantine/{dsr.pk}/{digest}.json",
                "encryption_key_reference": f"kms://dsr/{dsr.pk}",
                "source_systems": ["EMR", "HMIS", "Labs", "Billing"],
                "payload_sha256": digest,
                "expires_at": timezone.now() + timedelta(days=30),
            },
        )
        dsr.send_to_privacy_review(actor_id="fides-worker", reason="Fides collection complete")
        dsr.save(update_fields=("state", "updated_at"))


@shared_task
def monitor_sla_deadlines() -> int:
    active_states = [
        DSRRequest.State.ACCEPTED, DSRRequest.State.DEPT_SEARCH,
        DSRRequest.State.PRIVACY_REVIEW, DSRRequest.State.LEGAL_REVIEW,
        DSRRequest.State.RESPONSE_PREP, DSRRequest.State.NEEDS_INFORMATION,
    ]
    overdue_requests = DSRRequest.objects.filter(
        state__in=active_states, response_due_at__lt=timezone.now()
    ).exclude(
        pk__in=DSRAuditLog.objects.filter(
            to_state=DSRRequest.State.ESCALATED
        ).values("dsr_id")
    )
    escalated = 0
    for dsr in overdue_requests.iterator():
        dsr.escalate(actor_id="sla-monitor", reason="Configured response target exceeded")
        dsr.save(update_fields=("state", "escalated_from", "updated_at"))
        escalated += 1
    return escalated