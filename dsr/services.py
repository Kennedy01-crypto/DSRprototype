"""Workflow rules and every write to a case.

Each public function is one business operation. It runs in its own database
transaction, locks the case row first, checks the rules, writes, and records
the matching case event. Callers (API views today; Celery tasks, commands and
integrations later) pass plain values and handle the errors in
``dsr.exceptions``. Input shape and format checks stay in the serializers;
rules that depend on the case's current state live here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone
from django_fsm import TransitionNotAllowed

from .exceptions import (
    CaseNotFound,
    WorkflowConflict,
    WorkflowForbidden,
    WorkflowValidationError,
)
from .models import (
    DSRCaseEvent,
    DSRCaseException,
    DSRCommunication,
    DSRDecision,
    DSRDeadlinePolicy,
    DSRDeadlinePolicyAudit,
    DSRLegalReviewDecision,
    DSRLegalReviewRequest,
    DSRRequest,
    DSRResponsePublication,
    DSRResponseVersion,
    DSRReviewTask,
    DSRSourceSearch,
)
from .selectors import build_response_draft

# Fields any transition may change; saved together after every transition.
TRANSITION_FIELDS = (
    "state",
    "resolution_status",
    "closed_at",
    "escalated_from",
    "waiting_from_state",
    "duplicate_of",
    "legal_review_completed",
    "updated_at",
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def record_case_event(dsr: DSRRequest, actor_id: str, event_type: str, summary: str) -> None:
    DSRCaseEvent.objects.create(
        dsr=dsr, actor_id=actor_id, event_type=event_type, summary=summary
    )


def _lock_case(dsr_id: int, **scope: Any) -> DSRRequest:
    """Load and lock a case. ``scope`` narrows visibility, e.g. ``patient_id``."""
    try:
        return DSRRequest.objects.select_for_update().get(pk=dsr_id, **scope)
    except DSRRequest.DoesNotExist as error:
        raise CaseNotFound("No request matches the given query.") from error


def _canonical_case_for(dsr: DSRRequest, duplicate_of_id: int | None) -> DSRRequest | None:
    return DSRRequest.objects.filter(
        pk=duplicate_of_id,
        patient_id=dsr.patient_id,
        request_type=dsr.request_type,
        duplicate_of__isnull=True,
    ).first()


# --------------------------------------------------------------------------
# Patient operations
# --------------------------------------------------------------------------


@transaction.atomic
def submit_request(patient_id: str, data: Mapping[str, Any]) -> DSRRequest:
    return DSRRequest.objects.create(patient_id=patient_id, **data)


@transaction.atomic
def withdraw_request(dsr_id: int, *, patient_id: str, reason: str) -> DSRRequest:
    dsr = _lock_case(dsr_id, patient_id=patient_id)
    try:
        dsr.withdraw(actor_id=patient_id, reason=reason or "Withdrawn by the data subject")
    except TransitionNotAllowed as error:
        raise WorkflowConflict("This request can no longer be withdrawn.") from error
    dsr.save(update_fields=TRANSITION_FIELDS)
    return dsr


@transaction.atomic
def record_patient_reply(dsr_id: int, *, patient_id: str, message: str) -> DSRCommunication:
    """The patient answers an information request; the case returns to the
    stage it was waiting from."""
    dsr = _lock_case(dsr_id, patient_id=patient_id)
    if dsr.state != DSRRequest.State.NEEDS_INFORMATION:
        raise WorkflowConflict("This request is not waiting for more information.")
    communication = DSRCommunication.objects.create(
        dsr=dsr,
        message=message,
        direction=DSRCommunication.Direction.PATIENT_TO_DPO,
        created_by=patient_id,
    )
    record_case_event(
        dsr,
        patient_id,
        DSRCaseEvent.EventType.PATIENT_RESPONSE_RECORDED,
        "The subject provided requested information.",
    )
    dsr.provide_information(
        actor_id=patient_id, reason="The subject provided requested information"
    )
    dsr.save(update_fields=TRANSITION_FIELDS)
    return communication


# --------------------------------------------------------------------------
# Case administration
# --------------------------------------------------------------------------


@transaction.atomic
def send_patient_update(dsr_id: int, *, actor_id: str, message: str) -> DSRCommunication:
    dsr = _lock_case(dsr_id)
    communication = DSRCommunication.objects.create(
        dsr=dsr,
        message=message,
        direction=DSRCommunication.Direction.DPO_TO_PATIENT,
        created_by=actor_id,
    )
    record_case_event(
        dsr,
        actor_id,
        DSRCaseEvent.EventType.PATIENT_UPDATE_RECORDED,
        "Patient update recorded in the prototype. No external message was sent.",
    )
    return communication


@transaction.atomic
def assign_case(dsr_id: int, *, actor_id: str, assigned_to: str) -> DSRRequest:
    dsr = _lock_case(dsr_id)
    dsr.assigned_to = assigned_to
    dsr.save(update_fields=("assigned_to", "updated_at"))
    record_case_event(
        dsr,
        actor_id,
        DSRCaseEvent.EventType.ASSIGNED,
        f"Assignment changed to {dsr.assigned_to or 'unassigned'}.",
    )
    return dsr


@transaction.atomic
def set_jurisdiction(
    dsr_id: int, *, actor_id: str, jurisdiction_code: str, reason: str
) -> DSRRequest:
    """Re-select the deadline policy during intake. Recalculates both dates."""
    dsr = _lock_case(dsr_id)
    if (
        dsr.state != DSRRequest.State.SUBMITTED
        or dsr.source_searches.exists()
        or dsr.review_tasks.exists()
        or dsr.decisions.exists()
        or dsr.response_versions.exists()
        or dsr.exceptions.exists()
    ):
        raise WorkflowConflict(
            "Jurisdiction can be classified only during intake, before processing starts."
        )
    policy = (
        DSRDeadlinePolicy.objects.filter(
            request_type=dsr.request_type,
            jurisdiction_code=jurisdiction_code,
            effective_at__lte=timezone.now(),
        )
        .order_by("-version")
        .first()
    )
    if policy is None:
        raise WorkflowConflict(
            "No effective policy is configured for this jurisdiction and request type."
        )
    dsr.jurisdiction_code = policy.jurisdiction_code
    dsr.deadline_policy = policy
    dsr.response_due_at = dsr.submitted_at + timedelta(days=policy.internal_target_days)
    dsr.legal_deadline_at = (
        dsr.submitted_at + timedelta(days=policy.legal_deadline_days)
        if policy.legal_deadline_days is not None
        else None
    )
    dsr.save(
        update_fields=(
            "jurisdiction_code",
            "deadline_policy",
            "response_due_at",
            "legal_deadline_at",
            "updated_at",
        )
    )
    record_case_event(
        dsr,
        actor_id,
        DSRCaseEvent.EventType.JURISDICTION_POLICY_ASSIGNED,
        f"Intake jurisdiction set to {policy.jurisdiction_code}, policy version "
        f"{policy.version}: {reason}",
    )
    return dsr


# --------------------------------------------------------------------------
# Work items
# --------------------------------------------------------------------------


@transaction.atomic
def create_task(dsr_id: int, *, actor_id: str, data: Mapping[str, Any]) -> DSRReviewTask:
    dsr = _lock_case(dsr_id)
    task = DSRReviewTask.objects.create(
        dsr=dsr, created_by=actor_id, updated_by=actor_id, **data
    )
    record_case_event(
        dsr,
        actor_id,
        DSRCaseEvent.EventType.TASK_CREATED,
        f"Review task created: {task.title}.",
    )
    return task


def check_task_access(task: DSRReviewTask, *, actor_id: str, role: str) -> None:
    if role == "department_contributor" and task.assigned_to != actor_id:
        raise WorkflowForbidden("Department contributors can update only their assigned work.")


@transaction.atomic
def update_task(
    dsr_id: int,
    task_id: int,
    *,
    actor_id: str,
    role: str,
    data: Mapping[str, Any],
) -> DSRReviewTask:
    dsr = _lock_case(dsr_id)
    task = DSRReviewTask.objects.select_for_update().filter(dsr=dsr, pk=task_id).first()
    if task is None:
        raise CaseNotFound("No work item matches the given query.")
    check_task_access(task, actor_id=actor_id, role=role)
    task.status = data.get("status", task.status)
    task.completion_summary = data.get("completion_summary", task.completion_summary)
    task.updated_by = actor_id
    task.completed_at = (
        timezone.now() if task.status == DSRReviewTask.Status.COMPLETED else None
    )
    task.save(
        update_fields=(
            "status",
            "completion_summary",
            "updated_by",
            "completed_at",
            "updated_at",
        )
    )
    record_case_event(
        dsr,
        actor_id,
        DSRCaseEvent.EventType.TASK_UPDATED,
        f"Review task {task.pk} updated to {task.get_status_display()}.",
    )
    return task


# --------------------------------------------------------------------------
# Source searches and decisions
# --------------------------------------------------------------------------


@transaction.atomic
def create_source_search(
    dsr_id: int, *, actor_id: str, data: Mapping[str, Any]
) -> DSRSourceSearch:
    dsr = _lock_case(dsr_id)
    if dsr.source_searches.filter(source_system=data["source_system"]).exists():
        raise WorkflowConflict("A search for this mock source is already recorded.")
    search = DSRSourceSearch.objects.create(
        dsr=dsr, created_by=actor_id, updated_by=actor_id, **data
    )
    record_case_event(
        dsr,
        actor_id,
        DSRCaseEvent.EventType.SOURCE_SEARCH_RECORDED,
        f"{search.get_source_system_display()} search recorded as "
        f"{search.get_status_display()}.",
    )
    return search


@transaction.atomic
def update_source_search(
    dsr_id: int, search_id: int, *, actor_id: str, data: Mapping[str, Any]
) -> DSRSourceSearch:
    dsr = _lock_case(dsr_id)
    search = DSRSourceSearch.objects.select_for_update().filter(dsr=dsr, pk=search_id).first()
    if search is None:
        raise CaseNotFound("No source search matches the given query.")
    for field in ("status", "summary", "evidence_reference"):
        if field in data:
            setattr(search, field, data[field])
    search.updated_by = actor_id
    search.save()
    record_case_event(
        dsr,
        actor_id,
        DSRCaseEvent.EventType.SOURCE_SEARCH_RECORDED,
        f"{search.get_source_system_display()} search updated to "
        f"{search.get_status_display()}.",
    )
    return search


@transaction.atomic
def record_decision(dsr_id: int, *, actor_id: str, data: Mapping[str, Any]) -> DSRDecision:
    dsr = _lock_case(dsr_id)
    if (
        data["outcome"] != DSRDecision.Outcome.FURTHER_REVIEW
        and dsr.resolution_status != DSRRequest.ResolutionStatus.DUPLICATE
        and not dsr.source_searches_complete()
    ):
        raise WorkflowConflict(
            "Complete all source searches before recording a final decision."
        )
    decision = DSRDecision.objects.create(dsr=dsr, recorded_by=actor_id, **data)
    record_case_event(
        dsr,
        actor_id,
        DSRCaseEvent.EventType.DECISION_RECORDED,
        f"Decision recorded for {decision.category}: {decision.get_outcome_display()}.",
    )
    return decision


# --------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------


def response_signoff_error(dsr: DSRRequest) -> str | None:
    """Why a response cannot be finalized or published yet, or None."""
    if dsr.state != DSRRequest.State.RESPONSE_PREP:
        return "The case must be in Response Preparation."
    if (
        dsr.resolution_status != DSRRequest.ResolutionStatus.DUPLICATE
        and not dsr.source_searches_complete()
    ):
        return "Complete all mock source searches before finalizing a response."
    if dsr.review_tasks.filter(required_for_completion=True).exclude(
        status=DSRReviewTask.Status.COMPLETED
    ).exists():
        return "Complete all required work items before finalizing a response."
    latest_decision = dsr.decisions.order_by("-created_at", "-pk").first()
    if latest_decision is None or latest_decision.outcome == DSRDecision.Outcome.FURTHER_REVIEW:
        return "Record a final decision before finalizing a response."
    return None


def response_publication_error(dsr: DSRRequest, version: DSRResponseVersion) -> str | None:
    """Why this version cannot be published, or None."""
    latest_version = dsr.latest_response_version
    if latest_version is None or latest_version.pk != version.pk:
        return "Only the latest finalized response version can be published."
    if hasattr(version, "publication"):
        return "This response version has already been published."
    latest_decision = dsr.decisions.filter(category="overall").order_by("-pk").first()
    if latest_decision is None or latest_decision.outcome == DSRDecision.Outcome.FURTHER_REVIEW:
        return "Record a final overall decision before publishing."
    if dsr.independent_review_required_for(
        latest_decision.outcome
    ) and not dsr.has_valid_legal_review(latest_decision.outcome):
        return (
            "A current independent review of this response version and outcome "
            "is required before publication."
        )
    return None


def response_case_snapshot(dsr: DSRRequest) -> dict[str, object]:
    draft = build_response_draft(dsr)
    return {
        "case_id": dsr.pk,
        "request_type": dsr.get_request_type_display(),
        "description": dsr.description,
        "department": dsr.department,
        "duplicate_of": dsr.duplicate_of_id,
        "resolution": dsr.get_resolution_status_display(),
        "submitted_at": dsr.submitted_at.isoformat(),
        "response_due_at": dsr.response_due_at.isoformat(),
        "legal_deadline_at": (
            dsr.legal_deadline_at.isoformat() if dsr.legal_deadline_at else None
        ),
        "source_searches": draft["source_searches"],
        "decisions": draft["decisions"],
        "patient_updates": draft["patient_updates"],
        "captured_at": timezone.now().isoformat(),
    }


@transaction.atomic
def finalize_response(dsr_id: int, *, actor_id: str, content: str) -> DSRResponseVersion:
    dsr = _lock_case(dsr_id)
    error = response_signoff_error(dsr)
    if error:
        raise WorkflowConflict(error)
    latest_version = dsr.response_versions.order_by("-version").first()
    version = DSRResponseVersion.objects.create(
        dsr=dsr,
        version=(latest_version.version + 1) if latest_version else 1,
        content=content,
        case_snapshot=response_case_snapshot(dsr),
        finalized_by=actor_id,
    )
    record_case_event(
        dsr,
        actor_id,
        DSRCaseEvent.EventType.RESPONSE_FINALIZED,
        f"Response version {version.version} finalized for review/sign-off.",
    )
    return version


@transaction.atomic
def publish_response(dsr_id: int, version_id: int, *, actor_id: str) -> DSRResponseVersion:
    dsr = _lock_case(dsr_id)
    error = response_signoff_error(dsr)
    if error:
        raise WorkflowConflict(error)
    version = (
        DSRResponseVersion.objects.select_for_update().filter(dsr=dsr, pk=version_id).first()
    )
    if version is None:
        raise CaseNotFound("No response version matches the given query.")
    error = response_publication_error(dsr, version)
    if error:
        raise WorkflowConflict(error)
    DSRResponsePublication.objects.create(response_version=version, published_by=actor_id)
    record_case_event(
        dsr,
        actor_id,
        DSRCaseEvent.EventType.RESPONSE_PUBLISHED,
        f"Response version {version.version} published to the patient portal. "
        "No external message was sent.",
    )
    return version


# --------------------------------------------------------------------------
# Exceptions, deadlines and policies
# --------------------------------------------------------------------------

CLARIFICATION_STATES = (
    DSRRequest.State.SUBMITTED,
    DSRRequest.State.ACCEPTED,
    DSRRequest.State.DEPT_SEARCH,
    DSRRequest.State.PRIVACY_REVIEW,
    DSRRequest.State.LEGAL_REVIEW,
    DSRRequest.State.RESPONSE_PREP,
)


@transaction.atomic
def record_case_exception(
    dsr_id: int, *, actor_id: str, data: Mapping[str, Any]
) -> DSRCaseException:
    dsr = _lock_case(dsr_id)
    if dsr.major_status == "Completed":
        raise WorkflowConflict("Exceptions cannot be added to a completed case.")
    exception_type = data["exception_type"]
    policy = dsr.deadline_policy
    effect_days = data.get("deadline_effect_days")
    notification = str(data.get("patient_notification", "")).strip()
    policy_reason = str(data.get("policy_reason", "")).strip()
    Type = DSRCaseException.ExceptionType

    if exception_type == Type.CLARIFICATION and dsr.state not in CLARIFICATION_STATES:
        raise WorkflowConflict(
            "This case is not in a stage where clarification can be requested."
        )
    if exception_type == Type.EXTENSION:
        if (
            policy is None
            or not policy.extension_allowed
            or effect_days is None
            or effect_days > policy.maximum_extension_days
        ):
            raise WorkflowConflict("This policy does not authorize the requested extension.")
        if dsr.legal_deadline_at is None:
            raise WorkflowConflict("A legal deadline must be configured before extending it.")
    elif exception_type == Type.DEADLINE_PAUSE:
        if (
            policy is None
            or not policy.pause_allowed
            or policy_reason not in policy.pause_reasons
            or effect_days is None
        ):
            raise WorkflowConflict("This policy does not authorize the requested pause.")
        if dsr.legal_deadline_at is None:
            raise WorkflowConflict("A legal deadline must be configured before pausing it.")

    effect_seconds = 0
    if exception_type in (Type.EXTENSION, Type.DEADLINE_PAUSE):
        if policy is not None and policy.notification_required and not notification:
            raise WorkflowValidationError(
                {"patient_notification": "This policy requires patient notification."}
            )
        effect_seconds = int(effect_days) * 24 * 60 * 60
        dsr.legal_deadline_at += timedelta(seconds=effect_seconds)
        dsr.save(update_fields=("legal_deadline_at", "updated_at"))

    exception = DSRCaseException.objects.create(
        dsr=dsr,
        exception_type=exception_type,
        reason=data["reason"],
        policy_reason=policy_reason,
        actor_id=actor_id,
        patient_notified_at=timezone.now() if notification else None,
        deadline_effect_seconds=effect_seconds,
    )
    if notification:
        DSRCommunication.objects.create(
            dsr=dsr,
            direction=DSRCommunication.Direction.DPO_TO_PATIENT,
            message=notification,
            created_by=actor_id,
        )
    if exception_type == Type.CLARIFICATION:
        dsr.request_information(actor_id=actor_id, reason=data["reason"])
        dsr.save(update_fields=TRANSITION_FIELDS)
    record_case_event(
        dsr,
        actor_id,
        DSRCaseEvent.EventType.EXCEPTION_RECORDED,
        f"{exception.get_exception_type_display()} recorded: {exception.reason}",
    )
    return exception


POLICY_FIELDS = (
    "internal_target_days",
    "legal_deadline_days",
    "policy_source",
    "clock_start_event",
    "calendar_days",
    "extension_allowed",
    "maximum_extension_days",
    "pause_allowed",
    "pause_reasons",
    "notification_required",
)


@transaction.atomic
def update_deadline_policy(
    *, actor_id: str, request_type: str, data: Mapping[str, Any]
) -> DSRDeadlinePolicy:
    """Create a new policy version if anything changed. Existing cases keep
    the policy version they were created with."""
    jurisdiction_code = data["jurisdiction_code"]
    previous = (
        DSRDeadlinePolicy.objects.filter(
            request_type=request_type,
            jurisdiction_code=jurisdiction_code,
            effective_at__lte=timezone.now(),
        )
        .order_by("-version")
        .first()
    )
    policy_values = {name: data[name] for name in POLICY_FIELDS if name != "policy_source"}
    policy_values["policy_source"] = data.get("policy_source", "")
    changed_values = {
        name: {"from": getattr(previous, name) if previous is not None else None, "to": value}
        for name, value in policy_values.items()
        if previous is None or getattr(previous, name) != value
    }
    if previous is not None and not changed_values:
        return previous
    latest_version = (
        DSRDeadlinePolicy.objects.filter(
            request_type=request_type, jurisdiction_code=jurisdiction_code
        )
        .order_by("-version")
        .values_list("version", flat=True)
        .first()
    )
    policy = DSRDeadlinePolicy.objects.create(
        request_type=request_type,
        jurisdiction_code=jurisdiction_code,
        version=(latest_version or 0) + 1,
        effective_at=timezone.now(),
        updated_by=actor_id,
        **policy_values,
    )
    DSRDeadlinePolicyAudit.objects.create(
        policy=policy,
        actor_id=actor_id,
        changed_values=changed_values,
        reason=data["reason"],
    )
    return policy


@transaction.atomic
def record_export(cases: Iterable[DSRRequest], *, actor_id: str) -> None:
    for dsr in cases:
        record_case_event(
            dsr,
            actor_id,
            DSRCaseEvent.EventType.CASES_EXPORTED,
            "Case included in a DPO CSV report.",
        )


# --------------------------------------------------------------------------
# Stage transitions
# --------------------------------------------------------------------------


def _check_reviewer_independence(
    review_request: DSRLegalReviewRequest, dsr: DSRRequest, actor_id: str
) -> None:
    if (
        review_request.requested_by == actor_id
        or review_request.response_version.finalized_by == actor_id
        or (dsr.assigned_to and dsr.assigned_to == actor_id)
        or review_request.assigned_reviewer != actor_id
    ):
        raise WorkflowConflict(
            "The requester, response author, or assigned case handler cannot approve "
            "their own independent review."
        )


@transaction.atomic
def apply_transition(
    dsr_id: int,
    *,
    action: str,
    actor_id: str,
    actor_role: str,
    reason: str,
    resolution_status: str | None = None,
    proposed_outcome: str | None = None,
    duplicate_of_id: int | None = None,
    assigned_reviewer: str = "",
) -> DSRRequest:
    """Run one named workflow action on a case.

    Which roles may call which actions is decided by the caller. This function
    enforces everything that depends on the case itself.
    """
    dsr = _lock_case(dsr_id)

    if actor_role == "independent_reviewer":
        latest_review = dsr.legal_review_requests.order_by("-pk").first()
        if latest_review is None or latest_review.assigned_reviewer != actor_id:
            raise WorkflowForbidden("This review is assigned to another reviewer.")

    if action == "send_to_privacy_review" and dsr.state == DSRRequest.State.PRIVACY_REVIEW:
        return dsr  # The collection worker already moved it; treat as done.

    if action == "flag_for_independent_review":
        dsr.flag_for_independent_review(reason=reason)
        dsr.save(
            update_fields=(
                "independent_review_required",
                "independent_review_reason",
                "updated_at",
            )
        )
        record_case_event(
            dsr,
            actor_id,
            DSRCaseEvent.EventType.INDEPENDENT_REVIEW_REQUIRED,
            f"Independent review required: {reason}",
        )
        return dsr

    if action == "mark_for_duplicate":
        duplicate_of = _canonical_case_for(dsr, duplicate_of_id)
        if duplicate_of is None:
            raise WorkflowConflict(
                "Select a different canonical request for the same patient and request type."
            )
        dsr.mark_for_duplicate(actor_id=actor_id, reason=reason, duplicate_of=duplicate_of)
        dsr.save(update_fields=TRANSITION_FIELDS)
        record_case_event(
            dsr,
            actor_id,
            DSRCaseEvent.EventType.DECISION_RECORDED,
            f"Marked as duplicate of DSR-{duplicate_of.pk}: {reason}",
        )
        return dsr

    review_request: DSRLegalReviewRequest | None = None
    review_version: DSRResponseVersion | None = None

    if action == "request_legal_review":
        if proposed_outcome is None:
            raise WorkflowConflict("Select a proposed outcome for legal review.")
        review_version = dsr.latest_response_version
        latest_decision = dsr.decisions.filter(category="overall").order_by("-pk").first()
        if (
            review_version is None
            or hasattr(review_version, "publication")
            or latest_decision is None
            or latest_decision.outcome != proposed_outcome
        ):
            raise WorkflowConflict(
                "Finalize an unpublished response for the latest overall decision before "
                "requesting review."
            )
        # Check reviewer independence now rather than only at approval time:
        # a blank or conflicted reviewer would leave the case stuck in review.
        assigned_reviewer = assigned_reviewer.strip()
        if not assigned_reviewer:
            raise WorkflowConflict("Assign an independent reviewer for this review.")
        if assigned_reviewer in {actor_id, review_version.finalized_by, dsr.assigned_to}:
            raise WorkflowConflict(
                "The independent reviewer cannot be the requester, the response author, "
                "or the assigned case handler."
            )

    if action in {"approve_legal_review", "return_from_legal_review"}:
        review_request = dsr.legal_review_requests.order_by("-pk").first()
        if review_request is None:
            raise WorkflowConflict("There is no legal review awaiting a decision.")
        if DSRLegalReviewDecision.objects.filter(review_request=review_request).exists():
            raise WorkflowConflict("The latest legal review already has a decision.")
        _check_reviewer_independence(review_request, dsr, actor_id)
        latest_version = dsr.latest_response_version
        if action == "approve_legal_review" and (
            latest_version is None
            or review_request.response_version_id != latest_version.pk
            or review_request.case_snapshot != dsr.review_snapshot(latest_version)
        ):
            raise WorkflowConflict(
                "The case changed after review was requested. Finalize a new response and "
                "request review again."
            )

    kwargs: dict[str, object] = {"actor_id": actor_id, "reason": reason}
    if action == "approve_and_close":
        if resolution_status is None:
            raise WorkflowValidationError(
                {"resolution_status": "Select the outcome before closing the request."}
            )
        kwargs["resolution_status"] = resolution_status
    if action == "request_legal_review":
        kwargs["proposed_outcome"] = proposed_outcome
    if action == "close_as_duplicate":
        duplicate_of = _canonical_case_for(dsr, duplicate_of_id)
        if duplicate_of is None:
            raise WorkflowConflict(
                "Select a different canonical request for the same requester and request type."
            )
        kwargs["duplicate_of"] = duplicate_of

    getattr(dsr, action)(**kwargs)
    dsr.save(update_fields=TRANSITION_FIELDS)

    if action == "request_legal_review":
        assert review_version is not None
        review_request = DSRLegalReviewRequest.objects.create(
            dsr=dsr,
            requested_by=actor_id,
            request_reason=reason,
            proposed_outcome=proposed_outcome or "",
            assigned_reviewer=assigned_reviewer,
            response_version=review_version,
            case_snapshot=dsr.review_snapshot(review_version),
        )
        record_case_event(
            dsr,
            actor_id,
            DSRCaseEvent.EventType.LEGAL_REVIEW_REQUESTED,
            f"Independent review requested for response version {review_version.version} "
            f"and outcome {review_request.get_proposed_outcome_display()}.",
        )
    if action in {"approve_legal_review", "return_from_legal_review"}:
        assert review_request is not None
        outcome = (
            DSRLegalReviewDecision.Outcome.APPROVED
            if action == "approve_legal_review"
            else DSRLegalReviewDecision.Outcome.CHANGES_REQUESTED
        )
        DSRLegalReviewDecision.objects.create(
            review_request=review_request,
            reviewer_id=actor_id,
            outcome=outcome,
            rationale=reason,
        )
        record_case_event(
            dsr,
            actor_id,
            DSRCaseEvent.EventType.LEGAL_REVIEW_DECIDED,
            f"Independent review {outcome.lower().replace('_', ' ')}: {reason}",
        )
    if action in {"approve_and_close", "close_as_duplicate"}:
        DSRDecision.objects.create(
            dsr=dsr,
            category="overall",
            outcome=(
                DSRDecision.Outcome.DUPLICATE
                if action == "close_as_duplicate"
                else dsr.resolution_status
            ),
            rationale=reason,
            recorded_by=actor_id,
        )
        record_case_event(
            dsr,
            actor_id,
            DSRCaseEvent.EventType.DECISION_RECORDED,
            f"Final resolution recorded: {dsr.get_resolution_status_display()}.",
        )
    return dsr
