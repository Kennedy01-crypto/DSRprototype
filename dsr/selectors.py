"""Read-side helpers: queue filtering and the data shown on case screens.

Nothing in this module writes to the database.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta

from django.db.models import Q, QuerySet
from django.utils import timezone

from .models import DSRLegalReviewDecision, DSRRequest, DSRSourceSearch
from .serializers import (
    DSRCommunicationReadSerializer,
    DSRDecisionReadSerializer,
    DSRSourceSearchReadSerializer,
)

IN_PROGRESS_STATES = (
    DSRRequest.State.ACCEPTED,
    DSRRequest.State.DEPT_SEARCH,
    DSRRequest.State.PRIVACY_REVIEW,
    DSRRequest.State.LEGAL_REVIEW,
    DSRRequest.State.RESPONSE_PREP,
    DSRRequest.State.NEEDS_INFORMATION,
    DSRRequest.State.ESCALATED,
)
ACTIVE_STATES = (DSRRequest.State.SUBMITTED, *IN_PROGRESS_STATES)
COMPLETED_STATES = (
    DSRRequest.State.CLOSED,
    DSRRequest.State.REJECTED,
    DSRRequest.State.WITHDRAWN,
)

REVIEW_DECISION_ACTIONS = frozenset({"approve_legal_review", "return_from_legal_review"})

# What the DPO is offered in each stage. The FSM and services still enforce
# every rule; this list only decides which buttons appear.
TRANSITION_ACTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    DSRRequest.State.SUBMITTED: (
        ("Begin processing", "start_departmental_search"),
        ("Request more information", "request_information"),
        ("Flag for independent review", "flag_for_independent_review"),
        ("Mark as duplicate", "mark_for_duplicate"),
    ),
    DSRRequest.State.ACCEPTED: (
        ("Start departmental search", "start_departmental_search"),
        ("Request more information", "request_information"),
        ("Escalate", "escalate"),
        ("Flag for independent review", "flag_for_independent_review"),
        ("Mark as duplicate", "mark_for_duplicate"),
    ),
    DSRRequest.State.DEPT_SEARCH: (
        ("Send to privacy review", "send_to_privacy_review"),
        ("Request more information", "request_information"),
        ("Escalate", "escalate"),
        ("Flag for independent review", "flag_for_independent_review"),
        ("Mark as duplicate", "mark_for_duplicate"),
    ),
    DSRRequest.State.PRIVACY_REVIEW: (
        ("Approve privacy review", "approve_privacy_review"),
        ("Request more information", "request_information"),
        ("Escalate", "escalate"),
        ("Flag for independent review", "flag_for_independent_review"),
        ("Mark as duplicate", "mark_for_duplicate"),
    ),
    DSRRequest.State.LEGAL_REVIEW: (
        ("Approve legal review", "approve_legal_review"),
        ("Return for changes", "return_from_legal_review"),
        ("Request more information", "request_information"),
        ("Escalate", "escalate"),
        ("Mark as duplicate", "mark_for_duplicate"),
    ),
    DSRRequest.State.RESPONSE_PREP: (
        ("Approve and close", "approve_and_close"),
        ("Request independent review", "request_legal_review"),
        ("Complete duplicate case", "close_as_duplicate"),
        ("Request more information", "request_information"),
        ("Escalate", "escalate"),
        ("Flag for independent review", "flag_for_independent_review"),
        ("Mark as duplicate", "mark_for_duplicate"),
    ),
    DSRRequest.State.NEEDS_INFORMATION: (
        ("Flag for independent review", "flag_for_independent_review"),
        ("Mark as duplicate", "mark_for_duplicate"),
    ),
    DSRRequest.State.ESCALATED: (
        ("Resume escalated case", "resume_escalated"),
        ("Flag for independent review", "flag_for_independent_review"),
        ("Mark as duplicate", "mark_for_duplicate"),
    ),
}


def filtered_dpo_requests(
    params: Mapping[str, str],
) -> tuple[QuerySet[DSRRequest], dict[str, str]]:
    requests = DSRRequest.objects.all()
    state_filter = params.get("state", "")
    type_filter = params.get("type", "")
    department_filter = params.get("department", "").strip()
    assignee_filter = params.get("assigned_to", "").strip()
    queue_filter = params.get("queue", "")
    valid_states = {value for value, _label in DSRRequest.State.choices}
    valid_types = {value for value, _label in DSRRequest.RequestType.choices}
    if state_filter == "SUBMITTED":
        requests = requests.filter(state=DSRRequest.State.SUBMITTED)
    elif state_filter == "IN_PROGRESS":
        requests = requests.filter(state__in=IN_PROGRESS_STATES)
    elif state_filter == "COMPLETED":
        requests = requests.filter(state__in=COMPLETED_STATES)
    elif state_filter in valid_states:
        requests = requests.filter(state=state_filter)
    else:
        state_filter = ""
    if type_filter in valid_types:
        requests = requests.filter(request_type=type_filter)
    else:
        type_filter = ""
    if department_filter:
        requests = requests.filter(department__iexact=department_filter)
    if assignee_filter:
        requests = requests.filter(assigned_to__iexact=assignee_filter)
    now = timezone.now()
    if queue_filter == "overdue":
        requests = requests.filter(state__in=ACTIVE_STATES).filter(
            Q(response_due_at__lt=now) | Q(legal_deadline_at__lt=now)
        )
    elif queue_filter == "due_soon":
        requests = (
            requests.filter(state__in=ACTIVE_STATES)
            .exclude(Q(response_due_at__lt=now) | Q(legal_deadline_at__lt=now))
            .filter(
                Q(response_due_at__lte=now + timedelta(days=7))
                | Q(legal_deadline_at__lte=now + timedelta(days=7))
            )
            .filter(Q(response_due_at__gt=now) | Q(legal_deadline_at__gt=now))
        )
    elif queue_filter == "unassigned":
        requests = requests.filter(assigned_to="", state__in=ACTIVE_STATES)
    elif queue_filter == "waiting":
        requests = requests.filter(state=DSRRequest.State.NEEDS_INFORMATION)
    elif queue_filter == "escalated":
        requests = requests.filter(state=DSRRequest.State.ESCALATED)
    else:
        queue_filter = ""
    return requests, {
        "state_filter": state_filter,
        "type_filter": type_filter,
        "department_filter": department_filter,
        "assignee_filter": assignee_filter,
        "queue_filter": queue_filter,
    }


def case_activity(dsr: DSRRequest) -> list[dict[str, str]]:
    """Stage changes and other case events, newest first."""
    activity: list[tuple[datetime, dict[str, str]]] = []
    for item in dsr.audit_logs.all():
        summary = (
            f"{DSRRequest.State(item.from_state).label} → "
            f"{DSRRequest.State(item.to_state).label}"
        )
        if item.reason:
            summary += f": {item.reason}"
        activity.append(
            (
                item.timestamp,
                {
                    "kind": "transition",
                    "actor_id": item.actor_id,
                    "summary": summary,
                    "timestamp": item.timestamp.isoformat(),
                },
            )
        )
    for item in dsr.events.all():
        activity.append(
            (
                item.timestamp,
                {
                    "kind": "case",
                    "actor_id": item.actor_id,
                    "summary": item.summary,
                    "timestamp": item.timestamp.isoformat(),
                },
            )
        )
    activity.sort(key=lambda item: item[0], reverse=True)
    return [record for _timestamp, record in activity]


def available_transition_actions(
    dsr: DSRRequest,
    actor_id: str,
) -> tuple[tuple[str, str], ...]:
    """Actions the DPO screen offers for this case. Review decisions are
    never offered to a DPO; independent reviewers get their own list."""
    actions = tuple(
        action
        for action in TRANSITION_ACTIONS.get(dsr.state, ())
        if action[1] not in REVIEW_DECISION_ACTIONS
    )
    if dsr.independent_review_required:
        actions = tuple(a for a in actions if a[1] != "flag_for_independent_review")
    if dsr.duplicate_of_id is not None:
        actions = tuple(a for a in actions if a[1] != "mark_for_duplicate")
    return actions


def reviewer_actions(dsr: DSRRequest, reviewer_id: str) -> list[tuple[str, str]] | None:
    """Actions for an independent reviewer, or None if no undecided review
    on this case is assigned to them."""
    review_request = (
        dsr.legal_review_requests.filter(assigned_reviewer=reviewer_id)
        .order_by("-pk")
        .first()
    )
    if (
        review_request is None
        or DSRLegalReviewDecision.objects.filter(review_request=review_request).exists()
    ):
        return None
    latest_version = dsr.latest_response_version
    unchanged = (
        latest_version is not None
        and review_request.response_version_id == latest_version.pk
        and review_request.case_snapshot == dsr.review_snapshot(latest_version)
    )
    actions = [("Return for changes", "return_from_legal_review")]
    if unchanged:
        actions.insert(0, ("Approve independent review", "approve_legal_review"))
    return actions


def build_response_draft(dsr: DSRRequest) -> dict[str, object]:
    """A starting point for the response text, assembled only from case records."""
    searches = list(dsr.source_searches.all())
    decisions = list(dsr.decisions.all())
    communications = list(dsr.communications.all())
    complete = bool(searches) and all(
        item.status == DSRSourceSearch.Status.COMPLETE for item in searches
    )
    decision_summary = "\n".join(
        f"- {item.category}: {item.get_outcome_display()} — {item.rationale}"
        for item in decisions
    ) or "- No decision recorded."
    update_summary = "\n".join(
        f"- {item.get_direction_display()}: {item.message}"
        for item in communications
    ) or "- No patient updates recorded."
    return {
        "case_id": dsr.pk,
        "request_type": dsr.get_request_type_display(),
        "submitted_at": dsr.submitted_at.isoformat(),
        "response_due_at": dsr.response_due_at.isoformat(),
        "legal_deadline_at": (
            dsr.legal_deadline_at.isoformat() if dsr.legal_deadline_at else None
        ),
        "request_description": dsr.description,
        "searches_complete": complete,
        "source_searches": DSRSourceSearchReadSerializer(searches, many=True).data,
        "decisions": DSRDecisionReadSerializer(decisions, many=True).data,
        "patient_updates": DSRCommunicationReadSerializer(communications, many=True).data,
        "resolution": dsr.get_resolution_status_display(),
        "draft_text": (
            f"Subject: Update on your {dsr.get_request_type_display().lower()} request "
            f"(DSR-{dsr.pk})\n\n"
            "We have recorded and reviewed your request. "
            "The case information currently recorded for review is:\n"
            f"- Request: {dsr.description}\n"
            f"- Source-search checklist complete: {'Yes' if complete else 'No'}\n"
            f"- Recorded decisions:\n{decision_summary}\n"
            f"- Recorded patient updates:\n{update_summary}\n"
            f"- Current recorded resolution: {dsr.get_resolution_status_display()}\n\n"
            "This is a draft for authorized staff review. Confirm the search evidence, "
            "decision rationale, applicable policy, and response content before sending. "
            "It contains no retrieved source-system data and has not been sent."
        ),
        "notice": (
            "Generated only from case-management records. This draft is not a legal "
            "determination, does not contain source-system data, and is not sent."
        ),
    }
