from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from dsr.models import (
    DSRCaseEvent,
    DSRCommunication,
    DSRDecision,
    DSRRequest,
    DSRResponsePublication,
    DSRResponseVersion,
    DSRReviewTask,
    DSRSourceSearch,
)


SCENARIOS = (
    {
        "key": "access",
        "patient_id": "mock-patient-access",
        "request_type": DSRRequest.RequestType.ACCESS,
        "description": "Synthetic scenario: request access to a fictional visit summary.",
        "department": "Mock Clinical Records",
        "assigned_to": "mock-reviewer-1",
    },
    {
        "key": "rectification",
        "patient_id": "mock-patient-rectification",
        "request_type": DSRRequest.RequestType.RECTIFICATION,
        "description": "Synthetic scenario: request correction of fictional contact details.",
        "department": "Mock Patient Administration",
        "assigned_to": "mock-reviewer-2",
    },
    {
        "key": "erasure-review",
        "patient_id": "mock-patient-erasure",
        "request_type": DSRRequest.RequestType.ERASURE,
        "description": (
            "Synthetic scenario: request erasure of fictional health-related data; "
            "requires case-by-case policy review and does not presume an outcome."
        ),
        "department": "Mock Privacy Office",
        "assigned_to": "",
    },
    {
        "key": "access-published",
        "patient_id": "mock-patient-acceptance-access",
        "request_type": DSRRequest.RequestType.ACCESS,
        "description": (
            "Acceptance scenario: view a fictional access request with a "
            "published response."
        ),
        "department": "Mock Clinical Records",
        "assigned_to": "mock-reviewer-1",
    },
    {
        "key": "erasure-retention-exercise",
        "patient_id": "mock-patient-acceptance-erasure",
        "request_type": DSRRequest.RequestType.ERASURE,
        "description": (
            "Acceptance scenario: review a fictional erasure request with a "
            "hypothetical retention rationale."
        ),
        "department": "Mock Privacy Office",
        "assigned_to": "mock-reviewer-2",
    },
    {
        "key": "incomplete-search",
        "patient_id": "mock-patient-acceptance-search",
        "request_type": DSRRequest.RequestType.ACCESS,
        "description": (
            "Acceptance scenario: pending and blocked mock searches must prevent "
            "a final decision."
        ),
        "department": "Mock Clinical Records",
        "assigned_to": "mock-reviewer-1",
    },
    {
        "key": "clarification",
        "patient_id": "mock-patient-acceptance-clarification",
        "request_type": DSRRequest.RequestType.ACCESS,
        "description": (
            "Acceptance scenario: respond to a clarification request and resume review."
        ),
        "department": "Mock Patient Administration",
        "assigned_to": "mock-reviewer-2",
    },
    {
        "key": "overdue-escalated",
        "patient_id": "mock-patient-acceptance-escalated",
        "request_type": DSRRequest.RequestType.RECTIFICATION,
        "description": (
            "Acceptance scenario: an overdue fictional request has been escalated."
        ),
        "department": "Mock Patient Administration",
        "assigned_to": "",
    },
)


class Command(BaseCommand):
    help = "Create synthetic workflow and acceptance scenarios for local demos."

    @transaction.atomic
    def handle(self, *args: object, **options: object) -> None:
        created_count = 0
        for scenario in SCENARIOS:
            request, created = DSRRequest.objects.get_or_create(
                patient_id=scenario["patient_id"],
                description=scenario["description"],
                defaults={
                    "request_type": scenario["request_type"],
                    "department": scenario["department"],
                    "response_due_at": timezone.now() + timedelta(days=21),
                    "assigned_to": scenario["assigned_to"],
                },
            )
            if not created:
                continue
            request.accept_submission(
                actor_id=request.patient_id,
                reason="Synthetic demo intake",
            )
            request.save(update_fields=("state", "updated_at"))
            if scenario["key"] in {
                "rectification",
                "erasure-review",
                "erasure-retention-exercise",
                "incomplete-search",
                "clarification",
                "overdue-escalated",
            }:
                DSRReviewTask.objects.create(
                    dsr=request,
                    title="Review synthetic case information",
                    details="Demonstration task only; no external system was queried.",
                    assigned_to=scenario["assigned_to"],
                    created_by="demo-seed",
                    updated_by="demo-seed",
                )
                DSRCaseEvent.objects.create(
                    dsr=request,
                    actor_id="demo-seed",
                    event_type=DSRCaseEvent.EventType.TASK_CREATED,
                    summary="Synthetic review task added for demonstration.",
                )
            if scenario["key"] == "erasure-review":
                DSRDecision.objects.create(
                    dsr=request,
                    category="mock-erasure-review",
                    outcome=DSRDecision.Outcome.FURTHER_REVIEW,
                    rationale=(
                        "Synthetic example only: outcome remains pending an "
                        "authorized case-by-case review."
                    ),
                    recorded_by="demo-seed",
                )
                DSRCaseEvent.objects.create(
                    dsr=request,
                    actor_id="demo-seed",
                    event_type=DSRCaseEvent.EventType.DECISION_RECORDED,
                    summary="Synthetic example marked for further review.",
                )
            if scenario["key"] in {
                "access-published",
                "erasure-retention-exercise",
                "incomplete-search",
                "clarification",
                "overdue-escalated",
            }:
                self._prepare_acceptance_scenario(request, scenario["key"])
            created_count += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Created {created_count} synthetic DSR scenario(s); "
                "existing scenarios were left unchanged."
            )
        )

    def _prepare_acceptance_scenario(self, request: DSRRequest, key: str) -> None:
        actor_id = "demo-seed"
        if key == "overdue-escalated":
            request.response_due_at = timezone.now() - timedelta(days=2)
            request.save(update_fields=("response_due_at",))
        request.start_departmental_search(
            actor_id=actor_id, reason="Synthetic acceptance scenario setup."
        )
        request.save(update_fields=("state", "updated_at"))

        if key == "clarification":
            request.request_information(
                actor_id=actor_id,
                reason="Please clarify which fictional visit is in scope.",
            )
            request.save(update_fields=("state", "updated_at"))
            DSRCommunication.objects.create(
                dsr=request,
                direction=DSRCommunication.Direction.DPO_TO_PATIENT,
                message=(
                    "For this fictional request, please clarify which visit "
                    "you mean. Do not enter real health information."
                ),
                created_by=actor_id,
            )
            return

        searches = (
            (
                DSRSourceSearch.SourceSystem.CIMS,
                DSRSourceSearch.Status.COMPLETE,
                "Mock checklist exercise only; no CIMS data was accessed.",
            ),
            (
                DSRSourceSearch.SourceSystem.ERPS,
                DSRSourceSearch.Status.COMPLETE,
                "Mock checklist exercise only; no ERPS data was accessed.",
            ),
        )
        if key == "incomplete-search":
            searches = (
                (
                    DSRSourceSearch.SourceSystem.CIMS,
                    DSRSourceSearch.Status.PENDING,
                    "",
                ),
                (
                    DSRSourceSearch.SourceSystem.ERPS,
                    DSRSourceSearch.Status.BLOCKED,
                    "Synthetic blocker for demonstrating the workflow gate.",
                ),
            )
        for source_system, status_value, summary in searches:
            DSRSourceSearch.objects.create(
                dsr=request,
                source_system=source_system,
                status=status_value,
                summary=summary,
                evidence_reference=f"MOCK-{source_system}-{request.pk}",
                created_by=actor_id,
                updated_by=actor_id,
            )
            DSRCaseEvent.objects.create(
                dsr=request,
                actor_id=actor_id,
                event_type=DSRCaseEvent.EventType.SOURCE_SEARCH_RECORDED,
                summary=(
                    f"{source_system} mock checklist item recorded as "
                    f"{status_value.lower()}."
                ),
            )

        request.send_to_privacy_review(
            actor_id=actor_id, reason="Synthetic searches entered for exercise."
        )
        request.save(update_fields=("state", "updated_at"))
        if key == "incomplete-search":
            DSRDecision.objects.create(
                dsr=request,
                category="overall",
                outcome=DSRDecision.Outcome.FURTHER_REVIEW,
                rationale=(
                    "Synthetic exercise: searches are pending or blocked; "
                    "no final outcome is recorded."
                ),
                recorded_by=actor_id,
            )
            return

        if key == "overdue-escalated":
            request.escalate(
                actor_id=actor_id,
                reason="Synthetic overdue acceptance scenario.",
            )
            request.save(update_fields=("state", "escalated_from", "updated_at"))
            return

        request.approve_privacy_review(
            actor_id=actor_id, reason="Synthetic checklist complete for exercise."
        )
        request.save(update_fields=("state", "updated_at"))

        if key == "erasure-retention-exercise":
            DSRDecision.objects.create(
                dsr=request,
                category="overall",
                outcome=DSRDecision.Outcome.RETAINED,
                rationale=(
                    "Hypothetical training entry only. This is not a statement "
                    "that retention is legally required or that erasure must be "
                    "refused. Confirm any real case-specific decision and reasons "
                    "with authorized AAR DPO/legal reviewers."
                ),
                recorded_by=actor_id,
            )
            return

        DSRDecision.objects.create(
            dsr=request,
            category="overall",
            outcome=DSRDecision.Outcome.FULFILLED,
            rationale="Synthetic acceptance outcome; no real records were searched.",
            recorded_by=actor_id,
        )
        version = DSRResponseVersion.objects.create(
            dsr=request,
            version=1,
            content=(
                "Synthetic response: this fictional access request has been "
                "reviewed for this demonstration. No clinical records are included."
            ),
            case_snapshot={
                "case_id": request.pk,
                "synthetic": True,
                "note": "No PARAS, CIMS, or ERPS data was accessed.",
            },
            finalized_by=actor_id,
        )
        publication = DSRResponsePublication.objects.create(
            response_version=version,
            published_by=actor_id,
        )
        DSRCaseEvent.objects.create(
            dsr=request,
            actor_id=actor_id,
            event_type=DSRCaseEvent.EventType.RESPONSE_FINALIZED,
            summary="Synthetic acceptance response version 1 finalized.",
        )
        DSRCaseEvent.objects.create(
            dsr=request,
            actor_id=publication.published_by,
            event_type=DSRCaseEvent.EventType.RESPONSE_PUBLISHED,
            summary="Synthetic acceptance response published to the portal.",
        )
        request.approve_and_close(
            actor_id=actor_id,
            reason="Synthetic published response completed.",
            resolution_status=DSRRequest.ResolutionStatus.FULFILLED,
        )
        request.save(
            update_fields=(
                "state",
                "resolution_status",
                "closed_at",
                "updated_at",
            )
        )
