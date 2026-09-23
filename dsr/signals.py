from __future__ import annotations

from typing import Any

from django.db import transaction
from django.dispatch import receiver
from django_fsm.signals import post_transition

from .models import DSRAuditLog, DSRRequest


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

    if target == DSRRequest.State.DEPT_SEARCH:
        from .tasks import trigger_fides_data_collection

        transaction.on_commit(lambda: trigger_fides_data_collection.delay(instance.pk))

    instance.__dict__.pop("_transition_actor_id", None)
    instance.__dict__.pop("_transition_reason", None)