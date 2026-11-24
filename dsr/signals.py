from __future__ import annotations

from typing import Any

from django.db import transaction
from django.dispatch import receiver
from django.utils import timezone
from django_fsm.signals import post_transition

from .models import (
    DSRAuditLog,
    DSRCaseDisposition,
    DSRCaseEscalation,
    DSRRequest,
)


@receiver(post_transition, sender=DSRRequest)
def record_transition(
    sender: type[DSRRequest],
    instance: DSRRequest,
    name: str,
    source: str,
    target: str,
    **kwargs: Any,
) -> None:
    actor_id = str(getattr(instance, "_transition_actor_id", "system"))
    reason = str(getattr(instance, "_transition_reason", ""))
    DSRAuditLog.objects.create(
        dsr=instance,
        actor_id=actor_id,
        from_state=source,
        to_state=target,
        reason=reason,
    )

    if target == DSRRequest.State.ESCALATED and not instance.escalations.filter(
        resolved_at__isnull=True
    ).exists():
        DSRCaseEscalation.objects.create(
            dsr=instance,
            reason=reason,
            opened_by=actor_id,
        )
    elif source == DSRRequest.State.ESCALATED and target != DSRRequest.State.ESCALATED:
        escalation = instance.escalations.filter(
            resolved_at__isnull=True
        ).first()
        if escalation is not None:
            escalation.resolved_by = actor_id
            escalation.resolution = reason
            escalation.resolved_at = timezone.now()
            escalation.save(
                update_fields=("resolved_by", "resolution", "resolved_at")
            )

    if target in {
        DSRRequest.State.CLOSED,
        DSRRequest.State.REJECTED,
        DSRRequest.State.WITHDRAWN,
    } and not DSRCaseDisposition.objects.filter(dsr=instance).exists():
        is_patient_withdrawal = target == DSRRequest.State.WITHDRAWN
        publication = (
            instance.latest_response_version.publication
            if instance.latest_response_version is not None
            and hasattr(instance.latest_response_version, "publication")
            else None
        )
        DSRCaseDisposition.objects.create(
            dsr=instance,
            outcome=instance.resolution_status,
            initiated_by=(
                DSRCaseDisposition.Initiator.PATIENT
                if is_patient_withdrawal
                else DSRCaseDisposition.Initiator.ORGANIZATION
            ),
            actor_id=actor_id,
            reason=reason,
            communicated_at=publication.published_at if publication else None,
        )

    # Only a fresh departmental search starts collection. Resuming a case that
    # was paused in DEPT_SEARCH must not re-run it and overwrite quarantine.
    if name == "start_departmental_search":
        from .tasks import trigger_fides_data_collection

        transaction.on_commit(lambda: trigger_fides_data_collection.delay(instance.pk))

    instance.__dict__.pop("_transition_actor_id", None)
    instance.__dict__.pop("_transition_reason", None)