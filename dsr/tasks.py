from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from celery import shared_task
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import DSRCaseEvent, DSRDataQuarantine, DSRRequest


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
    with transaction.atomic():
        dsr = DSRRequest.objects.select_for_update().get(pk=dsr_id)
        if dsr.state != DSRRequest.State.DEPT_SEARCH:
            return

        payload = _simulate_fides_query(dsr.patient_id)
        payload_bytes = json.dumps(payload, sort_keys=True).encode()
        digest = hashlib.sha256(payload_bytes).hexdigest()
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
        DSRRequest.State.SUBMITTED,
        DSRRequest.State.ACCEPTED, DSRRequest.State.DEPT_SEARCH,
        DSRRequest.State.PRIVACY_REVIEW, DSRRequest.State.LEGAL_REVIEW,
        DSRRequest.State.RESPONSE_PREP, DSRRequest.State.NEEDS_INFORMATION,
        DSRRequest.State.ESCALATED,
    ]
    now = timezone.now()
    requests = DSRRequest.objects.filter(state__in=active_states).filter(
        Q(response_due_at__lt=now) | Q(legal_deadline_at__lt=now)
    )
    recorded = 0
    for dsr in requests.iterator():
        deadlines = [
            ("Internal target deadline passed", dsr.response_due_at),
            ("Legal deadline passed", dsr.legal_deadline_at),
        ]
        for summary, due_at in deadlines:
            if due_at is None or due_at >= now:
                continue
            if dsr.events.filter(
                event_type=DSRCaseEvent.EventType.DEADLINE_OVERDUE,
                summary=summary,
            ).exists():
                continue
            DSRCaseEvent.objects.create(
                dsr=dsr,
                actor_id="sla-monitor",
                event_type=DSRCaseEvent.EventType.DEADLINE_OVERDUE,
                summary=summary,
            )
            recorded += 1
    return recorded