from __future__ import annotations

import csv
import re
from datetime import datetime, timedelta
from collections.abc import Mapping
from typing import Callable

from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django_fsm import TransitionNotAllowed
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import PatientPortalJWTAuthentication, SimulatedPrincipal
from .permissions import RolePermission
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
from .serializers import (
    DSRAssignmentSerializer,
    DSRCommunicationSerializer,
    DSRCaseExceptionCreateSerializer,
    DSRCaseExceptionReadSerializer,
    DSRCaseEscalationReadSerializer,
    DSRCaseExceptionReadSerializer,
    DSRDecisionSerializer,
    DSRCommunicationReadSerializer,
    DSRDecisionReadSerializer,
    DSRDeadlinePolicySerializer,
    DSRDeadlinePolicyUpdateSerializer,
    DSRJurisdictionUpdateSerializer,
    DSRDPOStatusSerializer,
    DSRLegalReviewRequestReadSerializer,
    DSRReviewTaskSerializer,
    DSRReviewTaskReadSerializer,
    DSRReviewTaskUpdateSerializer,
    DSRSourceSearchCreateSerializer,
    DSRSourceSearchReadSerializer,
    DSRSourceSearchUpdateSerializer,
    DSRResponseFinalizeSerializer,
    DSRPublishedResponseReadSerializer,
    DSRResponseVersionReadSerializer,
    DSRStatusSerializer,
    DSRSubmitSerializer,
    DSRTransitionSerializer,
    DSRWithdrawSerializer,
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
OPEN_QUEUE_STATES = (*ACTIVE_STATES, DSRRequest.State.ESCALATED)

TRANSITION_ACTIONS = {
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


def _record_case_event(dsr: DSRRequest, actor_id: str, event_type: str, summary: str) -> None:
    DSRCaseEvent.objects.create(
        dsr=dsr,
        actor_id=actor_id,
        event_type=event_type,
        summary=summary,
    )


def _filtered_dpo_requests(params: QueryDict):
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
        requests = requests.filter(
            state__in=(
                DSRRequest.State.CLOSED,
                DSRRequest.State.REJECTED,
                DSRRequest.State.WITHDRAWN,
            )
        )
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
        requests = requests.filter(
            state__in=ACTIVE_STATES
        ).exclude(
            Q(response_due_at__lt=now) | Q(legal_deadline_at__lt=now)
        ).filter(
            Q(response_due_at__lte=now + timedelta(days=7))
            | Q(legal_deadline_at__lte=now + timedelta(days=7))
        ).filter(
            Q(response_due_at__gt=now) | Q(legal_deadline_at__gt=now)
        )
    elif queue_filter == "unassigned":
        requests = requests.filter(assigned_to="", state__in=OPEN_QUEUE_STATES)
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


def _case_activity(dsr: DSRRequest) -> list[dict[str, str]]:
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


def _available_transition_actions(
    dsr: DSRRequest,
    actor_id: str,
) -> tuple[tuple[str, str], ...]:
    actions = TRANSITION_ACTIONS.get(dsr.state, ())
    actions = tuple(
        action
        for action in actions
        if action[1] not in {"approve_legal_review", "return_from_legal_review"}
    )
    if dsr.independent_review_required:
        actions = tuple(
            action
            for action in actions
            if action[1] != "flag_for_independent_review"
        )
    if dsr.duplicate_of_id is not None:
        actions = tuple(
            action for action in actions if action[1] != "mark_for_duplicate"
        )
    if dsr.state == DSRRequest.State.LEGAL_REVIEW:
        review_request = dsr.legal_review_requests.order_by("-pk").first()
        latest_version = dsr.latest_response_version
        can_decide_independently = (
            review_request is not None
            and not DSRLegalReviewDecision.objects.filter(
                review_request=review_request
            ).exists()
            and review_request.requested_by != actor_id
            and review_request.response_version.finalized_by != actor_id
            and (not dsr.assigned_to or dsr.assigned_to != actor_id)
        )
        if not can_decide_independently:
            actions = tuple(
                action
                for action in actions
                if action[1] not in {"approve_legal_review", "return_from_legal_review"}
            )
        elif (
            latest_version is None
            or latest_version.pk != review_request.response_version_id
            or review_request.case_snapshot != dsr.review_snapshot(latest_version)
        ):
            actions = tuple(
                action for action in actions if action[1] != "approve_legal_review"
            )
    return actions


def _csv_safe_cell(value: object) -> str:
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _build_response_draft(dsr: DSRRequest) -> dict[str, object]:
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
        "source_searches": DSRSourceSearchReadSerializer(
            searches, many=True
        ).data,
        "decisions": DSRDecisionReadSerializer(decisions, many=True).data,
        "patient_updates": DSRCommunicationReadSerializer(
            communications, many=True
        ).data,
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


def _response_signoff_error(dsr: DSRRequest) -> str | None:
    if dsr.state != DSRRequest.State.RESPONSE_PREP:
        return "The case must be in Response Preparation."
    if (
        dsr.resolution_status != DSRRequest.ResolutionStatus.DUPLICATE
        and not dsr.source_searches_complete()
    ):
        return "Complete all mock source searches before finalizing a response."
    if dsr.review_tasks.filter(
        required_for_completion=True
    ).exclude(status=DSRReviewTask.Status.COMPLETED).exists():
        return "Complete all required work items before finalizing a response."
    latest_decision = dsr.decisions.order_by("-created_at", "-pk").first()
    if latest_decision is None or latest_decision.outcome == DSRDecision.Outcome.FURTHER_REVIEW:
        return "Record a final decision before finalizing a response."
    return None


def _response_publication_error(
    dsr: DSRRequest,
    version: DSRResponseVersion,
) -> str | None:
    latest_version = dsr.latest_response_version
    if latest_version is None or latest_version.pk != version.pk:
        return "Only the latest finalized response version can be published."
    if hasattr(version, "publication"):
        return "This response version has already been published."
    latest_decision = dsr.decisions.filter(category="overall").order_by("-pk").first()
    if (
        latest_decision is None
        or latest_decision.outcome == DSRDecision.Outcome.FURTHER_REVIEW
    ):
        return "Record a final overall decision before publishing."
    if (
        dsr.independent_review_required_for(latest_decision.outcome)
        and not dsr.has_valid_legal_review(latest_decision.outcome)
    ):
        return (
            "A current independent review of this response version and outcome "
            "is required before publication."
        )
    return None


def _response_case_snapshot(dsr: DSRRequest) -> dict[str, object]:
    draft = _build_response_draft(dsr)
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


def _dpo_csv_response(params: QueryDict, actor_id: str) -> HttpResponse:
    requests, _filters = _filtered_dpo_requests(params)
    requests = requests.order_by("pk")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="dsr-case-report.csv"'
    writer = csv.writer(response)
    writer.writerow(
        (
            "case_id",
            "subject_id",
            "request_type",
            "department",
            "major_status",
            "sub_status",
            "assigned_to",
            "submitted_at",
            "internal_target_at",
            "legal_deadline_at",
            "resolution_status",
            "closed_at",
        )
    )
    with transaction.atomic():
        for dsr in requests:
            writer.writerow(
                _csv_safe_cell(value)
                for value in (
                    dsr.pk,
                    dsr.patient_id,
                    dsr.get_request_type_display(),
                    dsr.department,
                    dsr.major_status,
                    dsr.workflow_substatus,
                    dsr.assigned_to,
                    dsr.submitted_at.isoformat(),
                    dsr.response_due_at.isoformat(),
                    dsr.legal_deadline_at.isoformat() if dsr.legal_deadline_at else "",
                    dsr.get_resolution_status_display(),
                    dsr.closed_at.isoformat() if dsr.closed_at else "",
                )
            )
            _record_case_event(
                dsr,
                actor_id,
                DSRCaseEvent.EventType.CASES_EXPORTED,
                "Case included in a DPO CSV report.",
            )
    return response


def _apply_transition(
    dsr: DSRRequest,
    *,
    action_name: str,
    actor_id: str,
    reason: str,
    resolution_status: str | None = None,
    proposed_outcome: str | None = None,
    duplicate_of_id: int | None = None,
    assigned_reviewer: str = "",
) -> None:
    if (
        action_name == "send_to_privacy_review"
        and dsr.state == DSRRequest.State.PRIVACY_REVIEW
    ):
        return

    if action_name in {"flag_for_independent_review", "mark_for_duplicate"}:
        if action_name == "flag_for_independent_review":
            dsr.flag_for_independent_review(reason=reason)
            dsr.save(
                update_fields=(
                    "independent_review_required",
                    "independent_review_reason",
                    "updated_at",
                )
            )
            _record_case_event(
                dsr,
                actor_id,
                DSRCaseEvent.EventType.INDEPENDENT_REVIEW_REQUIRED,
                f"Independent review required: {reason}",
            )
            return
        duplicate_of = DSRRequest.objects.filter(
            pk=duplicate_of_id,
            patient_id=dsr.patient_id,
            request_type=dsr.request_type,
            duplicate_of__isnull=True,
        ).first()
        if duplicate_of is None:
            raise TransitionNotAllowed(
                "Select a different canonical request for the same patient and request type."
            )
        dsr.mark_for_duplicate(
            actor_id=actor_id,
            reason=reason,
            duplicate_of=duplicate_of,
        )
        dsr.save(
            update_fields=(
                "state",
                "duplicate_of",
                "resolution_status",
                "updated_at",
            )
        )
        _record_case_event(
            dsr,
            actor_id,
            DSRCaseEvent.EventType.DECISION_RECORDED,
            f"Marked as duplicate of DSR-{duplicate_of.pk}: {reason}",
        )
        return

    review_request = None
    review_version = None
    if action_name == "request_legal_review":
        if proposed_outcome is None:
            raise TransitionNotAllowed("Select a proposed outcome for legal review.")
        review_version = dsr.latest_response_version
        latest_decision = dsr.decisions.filter(category="overall").order_by("-pk").first()
        if (
            review_version is None
            or hasattr(review_version, "publication")
            or latest_decision is None
            or latest_decision.outcome != proposed_outcome
        ):
            raise TransitionNotAllowed(
                "Finalize an unpublished response for the latest overall decision before requesting review."
            )
        # Check reviewer independence now rather than only at approval time:
        # a blank or conflicted reviewer would leave the case stuck in review.
        assigned_reviewer = assigned_reviewer.strip()
        if not assigned_reviewer:
            raise TransitionNotAllowed("Assign an independent reviewer for this review.")
        if assigned_reviewer in {
            actor_id,
            review_version.finalized_by,
            dsr.assigned_to,
        }:
            raise TransitionNotAllowed(
                "The independent reviewer cannot be the requester, the response author, "
                "or the assigned case handler."
            )
    if action_name in {"approve_legal_review", "return_from_legal_review"}:
        review_request = dsr.legal_review_requests.order_by("-pk").first()
        if review_request is None:
            raise TransitionNotAllowed("There is no legal review awaiting a decision.")
        if DSRLegalReviewDecision.objects.filter(
            review_request=review_request
        ).exists():
            raise TransitionNotAllowed("The latest legal review already has a decision.")
        if (
            review_request.requested_by == actor_id
            or review_request.response_version.finalized_by == actor_id
            or (dsr.assigned_to and dsr.assigned_to == actor_id)
            or review_request.assigned_reviewer != actor_id
        ):
            raise TransitionNotAllowed(
                "The requester, response author, or assigned case handler cannot approve their own independent review."
            )
        latest_version = dsr.latest_response_version
        if action_name == "approve_legal_review" and (
            latest_version is None
            or review_request.response_version_id != latest_version.pk
            or review_request.case_snapshot != dsr.review_snapshot(latest_version)
        ):
            raise TransitionNotAllowed(
                "The case changed after review was requested. Finalize a new response and request review again."
            )

    action: Callable[..., None] = getattr(dsr, action_name)
    kwargs: dict[str, object] = {"actor_id": actor_id, "reason": reason}
    if action_name == "approve_and_close":
        if resolution_status is None:
            raise ValueError("A resolution status is required to close a request.")
        kwargs["resolution_status"] = resolution_status
    if action_name == "request_legal_review":
        kwargs["proposed_outcome"] = proposed_outcome
    if action_name == "close_as_duplicate":
        duplicate_of = DSRRequest.objects.filter(
            pk=duplicate_of_id,
            patient_id=dsr.patient_id,
            request_type=dsr.request_type,
            duplicate_of__isnull=True,
        ).first()
        if duplicate_of is None:
            raise TransitionNotAllowed(
                "Select a different canonical request for the same requester and request type."
            )
        kwargs["duplicate_of"] = duplicate_of
    action(**kwargs)
    dsr.save(
        update_fields=(
            "state",
            "resolution_status",
            "closed_at",
            "escalated_from",
            "waiting_from_state",
            "duplicate_of",
            "legal_review_completed",
            "updated_at",
        )
    )
    if action_name == "request_legal_review":
        if review_version is None:
            raise TransitionNotAllowed("Finalize a response before requesting review.")
        review_request = DSRLegalReviewRequest.objects.create(
            dsr=dsr,
            requested_by=actor_id,
            request_reason=reason,
            proposed_outcome=proposed_outcome or "",
            assigned_reviewer=assigned_reviewer,
            response_version=review_version,
            case_snapshot=dsr.review_snapshot(review_version),
        )
        _record_case_event(
            dsr,
            actor_id,
            DSRCaseEvent.EventType.LEGAL_REVIEW_REQUESTED,
            f"Independent review requested for response version {review_version.version} "
            f"and outcome {review_request.get_proposed_outcome_display()}.",
        )
    if action_name in {"approve_legal_review", "return_from_legal_review"}:
        if review_request is None:
            raise TransitionNotAllowed("There is no legal review awaiting a decision.")
        outcome = (
            DSRLegalReviewDecision.Outcome.APPROVED
            if action_name == "approve_legal_review"
            else DSRLegalReviewDecision.Outcome.CHANGES_REQUESTED
        )
        DSRLegalReviewDecision.objects.create(
            review_request=review_request,
            reviewer_id=actor_id,
            outcome=outcome,
            rationale=reason,
        )
        _record_case_event(
            dsr,
            actor_id,
            DSRCaseEvent.EventType.LEGAL_REVIEW_DECIDED,
            f"Independent review {outcome.lower().replace('_', ' ')}: {reason}",
        )
    if action_name in {"approve_and_close", "close_as_duplicate"}:
        DSRDecision.objects.create(
            dsr=dsr,
            category="overall",
            outcome=(
                DSRDecision.Outcome.DUPLICATE
                if action_name == "close_as_duplicate"
                else dsr.resolution_status
            ),
            rationale=reason,
            recorded_by=actor_id,
        )
        _record_case_event(
            dsr,
            actor_id,
            DSRCaseEvent.EventType.DECISION_RECORDED,
            f"Final resolution recorded: {dsr.get_resolution_status_display()}.",
        )


@require_GET
def homepage(request: HttpRequest) -> HttpResponse:
    return HttpResponse(
        """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DSR Management API</title>
  <style>
    body { font: 16px/1.6 system-ui, sans-serif; margin: 3rem auto; max-width: 760px; padding: 0 1.25rem; color: #202124; }
    h1, h2 { line-height: 1.2; }
    code { background: #f1f3f4; padding: .15rem .35rem; border-radius: 4px; }
    li { margin: .5rem 0; }
  </style>
</head>
<body>
  <h1>Data Subject Request Management API</h1>
  <p>This prototype lets patients submit and track data subject requests while DPOs review requests and manage their workflow.</p>
  <p><strong>Mock-data demo only:</strong> sign-in is simulated. Do not enter real patient or clinical information.</p>
  <p><a href="/demo/login/?role=patient">Patient portal</a> | <a href="/demo/login/?role=dpo">DPO workspace</a> | <a href="/demo/login/?role=admin">Admin workspace</a></p>
  <p>To load fictional example cases, run <code>docker compose exec web python manage.py seed_demo_data</code>.</p>
  <h2>Available endpoints</h2>
  <ul>
    <li><code>GET /</code> — this homepage</li>
    <li><code>POST /api/v1/dsrs/submit/</code> — submit a patient request</li>
    <li><code>GET /api/v1/dsrs/my-requests/</code> — list the authenticated patient's requests</li>
    <li><code>GET /api/v1/dpo/dsrs/</code> — list all requests and a departmental heatmap</li>
    <li><code>GET/PATCH /api/v1/dpo/policies/</code> — manage request deadline policies</li>
    <li><code>POST /api/v1/dpo/dsrs/&lt;id&gt;/transition/</code> — transition a request as a DPO</li>
  </ul>
  <p>API calls require a simulated bearer token. Use <code>Bearer patient:&lt;id&gt;</code> for patient routes or <code>Bearer dpo:&lt;id&gt;</code> for DPO routes.</p>
  <p>See <code>API_README.md</code> in the repository for endpoint details, request examples, and workflow actions.</p>
</body>
</html>"""
    )


def _demo_identity(request: HttpRequest, role: str | None = None) -> str | None:
    session_role = request.session.get("demo_role")
    principal_id = request.session.get("demo_principal_id")
    if session_role not in {"patient", "dpo", "admin"} or not isinstance(principal_id, str):
        return None
    if role is not None and session_role != role:
        return None
    return principal_id


@require_http_methods(["GET", "POST"])
def demo_login(request: HttpRequest) -> HttpResponse:
    role = request.POST.get("role", request.GET.get("role", "patient"))
    if role not in {"patient", "dpo", "admin"}:
        role = "patient"
    error = ""
    if request.method == "POST":
        principal_id = request.POST.get("principal_id", "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", principal_id):
            error = "Enter an ID with 1-128 letters, numbers, dots, underscores, colons, or hyphens."
        else:
            request.session.cycle_key()
            request.session["demo_role"] = role
            request.session["demo_principal_id"] = principal_id
            return redirect(
                {
                    "patient": "patient-home",
                    "dpo": "dpo-home",
                    "admin": "admin-home",
                }[role]
            )
    return render(request, "dsr/login.html", {"role": role, "error": error})


@require_POST
def demo_logout(request: HttpRequest) -> HttpResponse:
    request.session.flush()
    return redirect("homepage")


@require_http_methods(["GET", "POST"])
def patient_home(request: HttpRequest) -> HttpResponse:
    patient_id = _demo_identity(request, "patient")
    if patient_id is None:
        return redirect("demo-login")

    errors: dict[str, list[str]] = {}
    if request.method == "POST":
        operation = request.POST.get("operation", "submit")
        if operation == "withdraw":
            with transaction.atomic():
                dsr = get_object_or_404(
                    DSRRequest.objects.select_for_update(),
                    pk=request.POST.get("request_id"),
                    patient_id=patient_id,
                )
                serializer = DSRWithdrawSerializer(data=request.POST)
                if serializer.is_valid():
                    try:
                        dsr.withdraw(
                            actor_id=patient_id,
                            reason=serializer.validated_data["reason"]
                            or "Withdrawn by the data subject",
                        )
                        dsr.save(
                            update_fields=(
                                "state",
                                "resolution_status",
                                "closed_at",
                                "escalated_from",
                                "updated_at",
                            )
                        )
                        return redirect("patient-home")
                    except TransitionNotAllowed:
                        errors = {"request": ["This request can no longer be withdrawn."]}
                else:
                    errors = serializer.errors
        elif operation == "patient_response":
            with transaction.atomic():
                dsr = get_object_or_404(
                    DSRRequest.objects.select_for_update(),
                    pk=request.POST.get("request_id"),
                    patient_id=patient_id,
                )
                serializer = DSRCommunicationSerializer(data=request.POST)
                if serializer.is_valid():
                    if dsr.state != DSRRequest.State.NEEDS_INFORMATION:
                        errors = {
                            "request": [
                                "This request is not waiting for more information."
                            ]
                        }
                    else:
                        serializer.save(
                            dsr=dsr,
                            direction=DSRCommunication.Direction.PATIENT_TO_DPO,
                            created_by=patient_id,
                        )
                        _record_case_event(
                            dsr,
                            patient_id,
                            DSRCaseEvent.EventType.PATIENT_RESPONSE_RECORDED,
                            "The subject provided requested information.",
                        )
                        dsr.provide_information(
                            actor_id=patient_id,
                            reason="The subject provided requested information",
                        )
                        dsr.save(update_fields=("state", "waiting_from_state", "updated_at"))
                        return redirect("patient-home")
                else:
                    errors = serializer.errors
        else:
            serializer = DSRSubmitSerializer(data=request.POST)
            if serializer.is_valid():
                with transaction.atomic():
                    serializer.save(patient_id=patient_id)
                return redirect("patient-home")
            errors = serializer.errors
    requests = DSRRequest.objects.filter(patient_id=patient_id).prefetch_related(
        "communications", "response_versions__publication"
    )
    return render(
        request,
        "dsr/patient_home.html",
        {"patient_id": patient_id, "requests": requests, "errors": errors},
    )


@require_GET
def dpo_home(request: HttpRequest) -> HttpResponse:
    dpo_id = _demo_identity(request, "dpo")
    if dpo_id is None:
        return redirect("demo-login")

    requests, filters = _filtered_dpo_requests(request.GET)
    filtered_count = requests.count()
    now = timezone.now()
    return render(
        request,
        "dsr/dpo_home.html",
        {
            "dpo_id": dpo_id,
            "requests": requests,
            "total_count": DSRRequest.objects.count(),
            "filtered_count": filtered_count,
            **filters,
            "states": (
                ("SUBMITTED", "Submitted"),
                ("IN_PROGRESS", "In Progress"),
                ("COMPLETED", "Completed"),
            ),
            "request_types": DSRRequest.RequestType.choices,
            "active_count": DSRRequest.objects.filter(state__in=ACTIVE_STATES).count(),
            "overdue_count": DSRRequest.objects.filter(
                state__in=ACTIVE_STATES
            ).filter(
                Q(response_due_at__lt=now) | Q(legal_deadline_at__lt=now)
            ).count(),
        },
    )


@require_GET
def admin_home(request: HttpRequest) -> HttpResponse:
    admin_id = _demo_identity(request, "admin")
    if admin_id is None:
        return redirect("demo-login")
    return render(request, "dsr/admin_home.html", {"admin_id": admin_id})


@require_GET
def dpo_export(request: HttpRequest) -> HttpResponse:
    dpo_id = _demo_identity(request, "dpo")
    if dpo_id is None:
        return redirect("demo-login")
    return _dpo_csv_response(request.GET, dpo_id)


@require_http_methods(["GET", "POST"])
def dpo_request_detail(request: HttpRequest, pk: int) -> HttpResponse:
    dpo_id = _demo_identity(request, "dpo")
    if dpo_id is None:
        return redirect("demo-login")
    errors: list[str] = []
    with transaction.atomic():
        dsr = get_object_or_404(DSRRequest.objects.select_for_update(), pk=pk)
        if request.method == "POST":
            operation = request.POST.get("operation", "transition")
            if operation == "assign":
                assignment_serializer = DSRAssignmentSerializer(data=request.POST)
                if not assignment_serializer.is_valid():
                    errors.extend(
                        str(message)
                        for field_errors in assignment_serializer.errors.values()
                        for message in field_errors
                    )
                else:
                    assigned_to = assignment_serializer.validated_data["assigned_to"]
                    dsr.assigned_to = assigned_to
                    dsr.save(update_fields=("assigned_to", "updated_at"))
                    _record_case_event(
                        dsr,
                        dpo_id,
                        DSRCaseEvent.EventType.ASSIGNED,
                        f"Assignment changed to {assigned_to or 'unassigned'}.",
                    )
                    return redirect("dpo-request", pk=dsr.pk)
            elif operation == "task_create":
                task_serializer = DSRReviewTaskSerializer(data=request.POST)
                if task_serializer.is_valid():
                    task = task_serializer.save(
                        dsr=dsr,
                        created_by=dpo_id,
                        updated_by=dpo_id,
                    )
                    _record_case_event(
                        dsr,
                        dpo_id,
                        DSRCaseEvent.EventType.TASK_CREATED,
                        f"Review task created: {task.title}.",
                    )
                    return redirect("dpo-request", pk=dsr.pk)
                errors = [
                    str(message)
                    for field_errors in task_serializer.errors.values()
                    for message in field_errors
                ]
            elif operation == "task_update":
                task_id = request.POST.get("task_id", "")
                task = get_object_or_404(DSRReviewTask, dsr=dsr, pk=task_id)
                task_serializer = DSRReviewTaskUpdateSerializer(data=request.POST)
                if task_serializer.is_valid():
                    task.status = task_serializer.validated_data["status"]
                    task.updated_by = dpo_id
                    task.completed_at = (
                        timezone.now()
                        if task.status == DSRReviewTask.Status.COMPLETED
                        else None
                    )
                    task.save(update_fields=("status", "updated_by", "completed_at", "updated_at"))
                    _record_case_event(
                        dsr,
                        dpo_id,
                        DSRCaseEvent.EventType.TASK_UPDATED,
                        f"Review task {task.pk} updated to {task.get_status_display()}.",
                    )
                    return redirect("dpo-request", pk=dsr.pk)
                errors = [
                    str(message)
                    for field_errors in task_serializer.errors.values()
                    for message in field_errors
                ]
            elif operation == "source_search_create":
                search_serializer = DSRSourceSearchCreateSerializer(data=request.POST)
                if search_serializer.is_valid():
                    source_system = search_serializer.validated_data["source_system"]
                    if dsr.source_searches.filter(source_system=source_system).exists():
                        errors = ["A search for this mock source is already recorded."]
                    else:
                        search = search_serializer.save(
                            dsr=dsr,
                            created_by=dpo_id,
                            updated_by=dpo_id,
                        )
                        _record_case_event(
                            dsr,
                            dpo_id,
                            DSRCaseEvent.EventType.SOURCE_SEARCH_RECORDED,
                            f"{search.get_source_system_display()} search recorded as "
                            f"{search.get_status_display()}.",
                        )
                        return redirect("dpo-request", pk=dsr.pk)
                else:
                    errors = [
                        str(message)
                        for field_errors in search_serializer.errors.values()
                        for message in field_errors
                    ]
            elif operation == "source_search_update":
                search = get_object_or_404(
                    DSRSourceSearch,
                    dsr=dsr,
                    pk=request.POST.get("search_id", ""),
                )
                search_serializer = DSRSourceSearchUpdateSerializer(
                    search, data=request.POST
                )
                if search_serializer.is_valid():
                    search = search_serializer.save(updated_by=dpo_id)
                    _record_case_event(
                        dsr,
                        dpo_id,
                        DSRCaseEvent.EventType.SOURCE_SEARCH_RECORDED,
                        f"{search.get_source_system_display()} search updated to "
                        f"{search.get_status_display()}.",
                    )
                    return redirect("dpo-request", pk=dsr.pk)
                errors = [
                    str(message)
                    for field_errors in search_serializer.errors.values()
                    for message in field_errors
                ]
            elif operation == "decision":
                decision_serializer = DSRDecisionSerializer(data=request.POST)
                if decision_serializer.is_valid():
                    if (
                        decision_serializer.validated_data["outcome"]
                        != DSRDecision.Outcome.FURTHER_REVIEW
                        and dsr.resolution_status
                        != DSRRequest.ResolutionStatus.DUPLICATE
                        and not dsr.source_searches_complete()
                    ):
                        errors = [
                            "Complete all source searches before recording a final decision."
                        ]
                    else:
                        decision = decision_serializer.save(
                            dsr=dsr, recorded_by=dpo_id
                        )
                        _record_case_event(
                            dsr,
                            dpo_id,
                            DSRCaseEvent.EventType.DECISION_RECORDED,
                            f"Decision recorded for {decision.category}: {decision.get_outcome_display()}.",
                        )
                        return redirect("dpo-request", pk=dsr.pk)
                errors = [
                    str(message)
                    for field_errors in decision_serializer.errors.values()
                    for message in field_errors
                ]
            elif operation == "communication":
                communication_serializer = DSRCommunicationSerializer(data=request.POST)
                if communication_serializer.is_valid():
                    communication_serializer.save(dsr=dsr, created_by=dpo_id)
                    _record_case_event(
                        dsr,
                        dpo_id,
                        DSRCaseEvent.EventType.PATIENT_UPDATE_RECORDED,
                        "Patient update recorded in the prototype. No external message was sent.",
                    )
                    return redirect("dpo-request", pk=dsr.pk)
                errors = [
                    str(message)
                    for field_errors in communication_serializer.errors.values()
                    for message in field_errors
                ]
            elif operation == "response_finalize":
                error = _response_signoff_error(dsr)
                serializer = DSRResponseFinalizeSerializer(data=request.POST)
                if error:
                    errors = [error]
                elif serializer.is_valid():
                    latest_version = dsr.response_versions.order_by("-version").first()
                    version = DSRResponseVersion.objects.create(
                        dsr=dsr,
                        version=(latest_version.version + 1) if latest_version else 1,
                        content=serializer.validated_data["content"],
                        case_snapshot=_response_case_snapshot(dsr),
                        finalized_by=dpo_id,
                    )
                    _record_case_event(
                        dsr,
                        dpo_id,
                        DSRCaseEvent.EventType.RESPONSE_FINALIZED,
                        f"Response version {version.version} finalized for review/sign-off.",
                    )
                    return redirect("dpo-request", pk=dsr.pk)
                else:
                    errors = [
                        str(message)
                        for field_errors in serializer.errors.values()
                        for message in field_errors
                    ]
            elif operation == "response_publish":
                error = _response_signoff_error(dsr)
                version = get_object_or_404(
                    DSRResponseVersion,
                    dsr=dsr,
                    pk=request.POST.get("version_id", ""),
                )
                if error:
                    errors = [error]
                elif publication_error := _response_publication_error(dsr, version):
                    errors = [publication_error]
                else:
                    publication = DSRResponsePublication.objects.create(
                        response_version=version,
                        published_by=dpo_id,
                    )
                    _record_case_event(
                        dsr,
                        dpo_id,
                        DSRCaseEvent.EventType.RESPONSE_PUBLISHED,
                        f"Response version {version.version} published to the patient portal "
                        f"by {publication.published_by}. No external message was sent.",
                    )
                    return redirect("dpo-request", pk=dsr.pk)
            else:
                serializer = DSRTransitionSerializer(data=request.POST)
                if serializer.is_valid():
                    action_name = serializer.validated_data["action"]
                    try:
                        _apply_transition(
                            dsr,
                            action_name=action_name,
                            actor_id=dpo_id,
                            reason=serializer.validated_data["reason"],
                            resolution_status=serializer.validated_data.get(
                                "resolution_status"
                            ),
                            proposed_outcome=serializer.validated_data.get(
                                "proposed_outcome"
                            ),
                            duplicate_of_id=serializer.validated_data.get(
                                "duplicate_of"
                            ),
                            assigned_reviewer=serializer.validated_data.get(
                                "assigned_reviewer", ""
                            ),
                        )
                        return redirect("dpo-request", pk=dsr.pk)
                    except TransitionNotAllowed as error:
                        errors.append(
                            str(error)
                        )
                else:
                    errors = [
                        str(message)
                        for field_errors in serializer.errors.values()
                        for message in field_errors
                    ]
    return render(
        request,
        "dsr/dpo_request.html",
        {
            "dpo_id": dpo_id,
            "dsr": dsr,
            "audit_logs": dsr.audit_logs.all(),
            "events": dsr.events.all(),
            "tasks": dsr.review_tasks.all(),
            "decisions": dsr.decisions.all(),
            "communications": dsr.communications.all(),
            "source_searches": dsr.source_searches.all(),
            "source_search_statuses": DSRSourceSearch.Status.choices,
            "source_systems": DSRSourceSearch.SourceSystem.choices,
            "source_searches_complete": dsr.source_searches_complete(),
            "response_draft": _build_response_draft(dsr),
            "response_versions": dsr.response_versions.select_related("publication").all(),
            "response_signoff_error": _response_signoff_error(dsr),
            "actions": _available_transition_actions(dsr, dpo_id),
            "legal_reviews": dsr.legal_review_requests.select_related(
                "response_version", "decision"
            ),
            "errors": errors,
            "task_statuses": DSRReviewTask.Status.choices,
            "decision_outcomes": DSRDecision.Outcome.choices,
        },
    )


class RoleAPIView(APIView):
    """Base for API views. ``RolePermission`` enforces ``allowed_roles``
    before any handler runs, so handlers can rely on ``request.user`` being a
    principal with one of those roles."""

    authentication_classes = (PatientPortalJWTAuthentication,)
    permission_classes = (RolePermission,)
    allowed_roles: frozenset[str] | Mapping[str, frozenset[str]] = frozenset()

    def principal(self, request: Request) -> SimulatedPrincipal:
        assert isinstance(request.user, SimulatedPrincipal)
        return request.user

    def principal_id(self, request: Request) -> str:
        return self.principal(request).patient_id


class PatientEndpoint(RoleAPIView):
    allowed_roles = frozenset({"patient"})

    def _principal(self, request: Request) -> SimulatedPrincipal:
        return self.principal(request)


class SubmitDSRView(PatientEndpoint):
    def post(self, request: Request) -> Response:
        principal = self._principal(request)
        serializer = DSRSubmitSerializer(
            data=request.data,
            context={"patient_id": principal.patient_id},
        )
        serializer.is_valid(raise_exception=True)
        dsr = serializer.save(patient_id=principal.patient_id)
        return Response(DSRStatusSerializer(dsr).data, status=status.HTTP_201_CREATED)


class MyRequestsView(PatientEndpoint):
    def get(self, request: Request) -> Response:
        principal = self._principal(request)
        requests = DSRRequest.objects.filter(patient_id=principal.patient_id)
        return Response(DSRStatusSerializer(requests, many=True).data)


class PatientDSRDetailView(PatientEndpoint):
    def get(self, request: Request, pk: int) -> Response:
        principal = self._principal(request)
        dsr = get_object_or_404(DSRRequest, pk=pk, patient_id=principal.patient_id)
        return Response(
            {
                **DSRStatusSerializer(dsr).data,
                "can_withdraw": dsr.can_withdraw,
            }
        )


class DPOAPIView(RoleAPIView):
    allowed_roles = frozenset({"dpo"})


class DSRCaseDetailView(DPOAPIView):
    allowed_roles = frozenset({"dpo", "independent_reviewer"})

    def get(self, request: Request, pk: int) -> Response:
        actor_id = self.principal_id(request)
        dsr = get_object_or_404(DSRRequest, pk=pk)
        if request.user.role == "independent_reviewer":
            review_request = dsr.legal_review_requests.filter(
                assigned_reviewer=actor_id
            ).order_by("-pk").first()
            if (
                review_request is None
                or DSRLegalReviewDecision.objects.filter(
                    review_request=review_request
                ).exists()
            ):
                raise PermissionDenied("No independent review is assigned to this reviewer.")
            latest_version = dsr.latest_response_version
            unchanged = (
                latest_version is not None
                and review_request.response_version_id == latest_version.pk
                and review_request.case_snapshot == dsr.review_snapshot(latest_version)
            )
            available_actions = [("Return for changes", "return_from_legal_review")]
            if unchanged:
                available_actions.insert(
                    0, ("Approve independent review", "approve_legal_review")
                )
            return Response(
                {
                    "request": DSRDPOStatusSerializer(dsr).data,
                    "activity": _case_activity(dsr),
                    "communications": [],
                    "legal_reviews": DSRLegalReviewRequestReadSerializer(
                        [review_request], many=True
                    ).data,
                    "work_items": DSRReviewTaskReadSerializer(
                        dsr.review_tasks.all(), many=True
                    ).data,
                    "escalations": DSRCaseEscalationReadSerializer(
                        dsr.escalations.all(), many=True
                    ).data,
                    "exceptions": DSRCaseExceptionReadSerializer(
                        dsr.exceptions.all(), many=True
                    ).data,
                    "review_material": {
                        "response_version": review_request.response_version.version,
                        "response_content": review_request.response_version.content,
                        "case_snapshot": review_request.case_snapshot,
                        "source_searches": DSRSourceSearchReadSerializer(
                            dsr.source_searches.all(), many=True
                        ).data,
                        "decisions": DSRDecisionReadSerializer(
                            dsr.decisions.all(), many=True
                        ).data,
                    },
                    "available_actions": [
                        {"label": label, "action": action}
                        for label, action in available_actions
                    ],
                    "work_items": DSRReviewTaskReadSerializer(
                        dsr.review_tasks.all(), many=True
                    ).data,
                    "escalations": DSRCaseEscalationReadSerializer(
                        dsr.escalations.all(), many=True
                    ).data,
                    "exceptions": DSRCaseExceptionReadSerializer(
                        dsr.exceptions.all(), many=True
                    ).data,
                }
            )
        actions = _available_transition_actions(dsr, actor_id)
        return Response(
            {
                "request": DSRDPOStatusSerializer(dsr).data,
                "activity": _case_activity(dsr),
                "communications": DSRCommunicationReadSerializer(
                    dsr.communications.all(), many=True
                ).data,
                "legal_reviews": DSRLegalReviewRequestReadSerializer(
                    dsr.legal_review_requests.select_related(
                        "response_version"
                    ).prefetch_related("decision"),
                    many=True,
                ).data,
                "work_items": DSRReviewTaskReadSerializer(
                    dsr.review_tasks.all(), many=True
                ).data,
                "escalations": DSRCaseEscalationReadSerializer(
                    dsr.escalations.all(), many=True
                ).data,
                "exceptions": DSRCaseExceptionReadSerializer(
                    dsr.exceptions.all(), many=True
                ).data,
                "available_actions": [
                    {"label": label, "action": action}
                    for label, action in actions
                ],
            }
        )


class DSRAssignmentView(DPOAPIView):
    def post(self, request: Request, pk: int) -> Response:
        actor_id = self.principal_id(request)
        serializer = DSRAssignmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            dsr = get_object_or_404(DSRRequest.objects.select_for_update(), pk=pk)
            dsr.assigned_to = serializer.validated_data["assigned_to"]
            dsr.save(update_fields=("assigned_to", "updated_at"))
            _record_case_event(
                dsr,
                actor_id,
                DSRCaseEvent.EventType.ASSIGNED,
                f"Assignment changed to {dsr.assigned_to or 'unassigned'}.",
            )
        return Response(DSRDPOStatusSerializer(dsr).data)


class DSRJurisdictionView(DPOAPIView):
    def patch(self, request: Request, pk: int) -> Response:
        actor_id = self.principal_id(request)
        serializer = DSRJurisdictionUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            dsr = get_object_or_404(DSRRequest.objects.select_for_update(), pk=pk)
            if (
                dsr.state != DSRRequest.State.SUBMITTED
                or dsr.source_searches.exists()
                or dsr.review_tasks.exists()
                or dsr.decisions.exists()
                or dsr.response_versions.exists()
                or dsr.exceptions.exists()
            ):
                return Response(
                    {"detail": "Jurisdiction can be classified only during intake, before processing starts."},
                    status=status.HTTP_409_CONFLICT,
                )
            policy = DSRDeadlinePolicy.objects.filter(
                request_type=dsr.request_type,
                jurisdiction_code=serializer.validated_data["jurisdiction_code"],
                effective_at__lte=timezone.now(),
            ).order_by("-version").first()
            if policy is None:
                return Response(
                    {"detail": "No effective policy is configured for this jurisdiction and request type."},
                    status=status.HTTP_409_CONFLICT,
                )
            dsr.jurisdiction_code = policy.jurisdiction_code
            dsr.deadline_policy = policy
            dsr.response_due_at = dsr.submitted_at + timedelta(
                days=policy.internal_target_days
            )
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
            _record_case_event(
                dsr,
                actor_id,
                DSRCaseEvent.EventType.JURISDICTION_POLICY_ASSIGNED,
                f"Intake jurisdiction set to {policy.jurisdiction_code}, policy version {policy.version}: "
                f"{serializer.validated_data['reason']}",
            )
        return Response(DSRDPOStatusSerializer(dsr).data)


class DSRReviewTaskListView(DPOAPIView):
    allowed_roles = {
        "GET": frozenset({"dpo", "department_contributor"}),
        "POST": frozenset({"dpo"}),
    }

    def get(self, request: Request, pk: int) -> Response:
        actor_id = self.principal_id(request)
        dsr = get_object_or_404(DSRRequest, pk=pk)
        tasks = dsr.review_tasks.all()
        if request.user.role == "department_contributor":
            tasks = tasks.filter(assigned_to=actor_id)
            # Contributors only learn that a case exists if they work on it.
            if not tasks.exists():
                raise Http404
        return Response(DSRReviewTaskReadSerializer(tasks, many=True).data)

    def post(self, request: Request, pk: int) -> Response:
        actor_id = self.principal_id(request)
        serializer = DSRReviewTaskSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            dsr = get_object_or_404(DSRRequest.objects.select_for_update(), pk=pk)
            task = serializer.save(dsr=dsr, created_by=actor_id, updated_by=actor_id)
            _record_case_event(
                dsr,
                actor_id,
                DSRCaseEvent.EventType.TASK_CREATED,
                f"Review task created: {task.title}.",
            )
        return Response(DSRReviewTaskReadSerializer(task).data, status=status.HTTP_201_CREATED)


class DSRAssignedWorkView(RoleAPIView):
    allowed_roles = frozenset({"department_contributor"})

    def get(self, request: Request) -> Response:
        tasks = DSRReviewTask.objects.filter(
            assigned_to=self.principal_id(request)
        ).select_related("dsr")
        return Response(
            [
                {
                    **DSRReviewTaskReadSerializer(task).data,
                    "request_id": task.dsr_id,
                    "request_type": task.dsr.get_request_type_display(),
                    "case_stage": task.dsr.case_stage,
                }
                for task in tasks
            ]
        )


class DSRAssignedReviewsView(RoleAPIView):
    allowed_roles = frozenset({"independent_reviewer"})

    def get(self, request: Request) -> Response:
        reviews = DSRLegalReviewRequest.objects.filter(
            assigned_reviewer=self.principal_id(request),
            decision__isnull=True,
        ).select_related("dsr", "response_version")
        return Response(
            [
                {
                    "request_id": review.dsr_id,
                    "request_type": review.dsr.get_request_type_display(),
                    "case_stage": review.dsr.case_stage,
                    "requested_by": review.requested_by,
                    "request_reason": review.request_reason,
                    "proposed_outcome": review.get_proposed_outcome_display(),
                    "response_version": review.response_version.version,
                    "requested_at": review.requested_at,
                }
                for review in reviews
            ]
        )


class DSRReviewTaskDetailView(DPOAPIView):
    allowed_roles = frozenset({"dpo", "department_contributor"})

    def patch(self, request: Request, pk: int, task_pk: int) -> Response:
        actor_id = self.principal_id(request)
        with transaction.atomic():
            dsr = get_object_or_404(DSRRequest.objects.select_for_update(), pk=pk)
            task = get_object_or_404(DSRReviewTask.objects.select_for_update(), dsr=dsr, pk=task_pk)
            if (
                request.user.role == "department_contributor"
                and task.assigned_to != actor_id
            ):
                raise PermissionDenied("Department contributors can update only their assigned work.")
            serializer = DSRReviewTaskUpdateSerializer(
                data=request.data,
                instance=task,
                partial=True,
            )
            serializer.is_valid(raise_exception=True)
            task.status = serializer.validated_data.get("status", task.status)
            task.completion_summary = serializer.validated_data.get(
                "completion_summary", task.completion_summary
            )
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
            _record_case_event(
                dsr,
                actor_id,
                DSRCaseEvent.EventType.TASK_UPDATED,
                f"Review task {task.pk} updated to {task.get_status_display()}.",
            )
        return Response(DSRReviewTaskReadSerializer(task).data)


class DSRDecisionListView(DPOAPIView):
    def get(self, request: Request, pk: int) -> Response:
        self.principal_id(request)
        dsr = get_object_or_404(DSRRequest, pk=pk)
        return Response(DSRDecisionReadSerializer(dsr.decisions.all(), many=True).data)

    def post(self, request: Request, pk: int) -> Response:
        actor_id = self.principal_id(request)
        serializer = DSRDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            dsr = get_object_or_404(DSRRequest.objects.select_for_update(), pk=pk)
            if (
                serializer.validated_data["outcome"]
                != DSRDecision.Outcome.FURTHER_REVIEW
                and dsr.resolution_status != DSRRequest.ResolutionStatus.DUPLICATE
                and not dsr.source_searches_complete()
            ):
                return Response(
                    {
                        "detail": (
                            "Complete all source searches before recording "
                            "a final decision."
                        )
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            decision = serializer.save(dsr=dsr, recorded_by=actor_id)
            _record_case_event(
                dsr,
                actor_id,
                DSRCaseEvent.EventType.DECISION_RECORDED,
                f"Decision recorded for {decision.category}: {decision.get_outcome_display()}.",
            )
        return Response(DSRDecisionReadSerializer(decision).data, status=status.HTTP_201_CREATED)


class DSRSourceSearchListView(DPOAPIView):
    def get(self, request: Request, pk: int) -> Response:
        self.principal_id(request)
        dsr = get_object_or_404(DSRRequest, pk=pk)
        return Response(
            DSRSourceSearchReadSerializer(dsr.source_searches.all(), many=True).data
        )

    def post(self, request: Request, pk: int) -> Response:
        actor_id = self.principal_id(request)
        serializer = DSRSourceSearchCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            dsr = get_object_or_404(DSRRequest.objects.select_for_update(), pk=pk)
            if dsr.source_searches.filter(
                source_system=serializer.validated_data["source_system"]
            ).exists():
                return Response(
                    {"detail": "A search for this mock source is already recorded."},
                    status=status.HTTP_409_CONFLICT,
                )
            search = serializer.save(
                dsr=dsr,
                created_by=actor_id,
                updated_by=actor_id,
            )
            _record_case_event(
                dsr,
                actor_id,
                DSRCaseEvent.EventType.SOURCE_SEARCH_RECORDED,
                f"{search.get_source_system_display()} search recorded as "
                f"{search.get_status_display()}.",
            )
        return Response(
            DSRSourceSearchReadSerializer(search).data,
            status=status.HTTP_201_CREATED,
        )


class DSRSourceSearchDetailView(DPOAPIView):
    def patch(self, request: Request, pk: int, search_pk: int) -> Response:
        actor_id = self.principal_id(request)
        with transaction.atomic():
            dsr = get_object_or_404(DSRRequest.objects.select_for_update(), pk=pk)
            search = get_object_or_404(
                DSRSourceSearch.objects.select_for_update(),
                dsr=dsr,
                pk=search_pk,
            )
            serializer = DSRSourceSearchUpdateSerializer(
                search, data=request.data, partial=True
            )
            serializer.is_valid(raise_exception=True)
            search = serializer.save(updated_by=actor_id)
            _record_case_event(
                dsr,
                actor_id,
                DSRCaseEvent.EventType.SOURCE_SEARCH_RECORDED,
                f"{search.get_source_system_display()} search updated to "
                f"{search.get_status_display()}.",
            )
        return Response(DSRSourceSearchReadSerializer(search).data)


class DSRResponseDraftView(DPOAPIView):
    def get(self, request: Request, pk: int) -> Response:
        self.principal_id(request)
        dsr = get_object_or_404(DSRRequest, pk=pk)
        return Response(_build_response_draft(dsr))


class DSRResponseVersionListView(DPOAPIView):
    def get(self, request: Request, pk: int) -> Response:
        self.principal_id(request)
        dsr = get_object_or_404(DSRRequest, pk=pk)
        return Response(
            DSRResponseVersionReadSerializer(
                dsr.response_versions.select_related("publication"), many=True
            ).data
        )

    def post(self, request: Request, pk: int) -> Response:
        actor_id = self.principal_id(request)
        serializer = DSRResponseFinalizeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            dsr = get_object_or_404(DSRRequest.objects.select_for_update(), pk=pk)
            error = _response_signoff_error(dsr)
            if error:
                return Response({"detail": error}, status=status.HTTP_409_CONFLICT)
            latest_version = dsr.response_versions.order_by("-version").first()
            version = DSRResponseVersion.objects.create(
                dsr=dsr,
                version=(latest_version.version + 1) if latest_version else 1,
                content=serializer.validated_data["content"],
                case_snapshot=_response_case_snapshot(dsr),
                finalized_by=actor_id,
            )
            _record_case_event(
                dsr,
                actor_id,
                DSRCaseEvent.EventType.RESPONSE_FINALIZED,
                f"Response version {version.version} finalized for review/sign-off.",
            )
        return Response(
            DSRResponseVersionReadSerializer(version).data,
            status=status.HTTP_201_CREATED,
        )


class DSRResponsePublishView(DPOAPIView):
    def post(self, request: Request, pk: int, version_pk: int) -> Response:
        actor_id = self.principal_id(request)
        with transaction.atomic():
            dsr = get_object_or_404(DSRRequest.objects.select_for_update(), pk=pk)
            error = _response_signoff_error(dsr)
            if error:
                return Response({"detail": error}, status=status.HTTP_409_CONFLICT)
            version = get_object_or_404(
                DSRResponseVersion.objects.select_for_update(),
                dsr=dsr,
                pk=version_pk,
            )
            publication_error = _response_publication_error(dsr, version)
            if publication_error:
                return Response(
                    {"detail": publication_error},
                    status=status.HTTP_409_CONFLICT,
                )
            publication = DSRResponsePublication.objects.create(
                response_version=version,
                published_by=actor_id,
            )
            _record_case_event(
                dsr,
                actor_id,
                DSRCaseEvent.EventType.RESPONSE_PUBLISHED,
                f"Response version {version.version} published to the patient portal. "
                "No external message was sent.",
            )
        return Response(
            DSRResponseVersionReadSerializer(version).data,
            status=status.HTTP_201_CREATED,
        )


class PatientPublishedResponsesView(PatientEndpoint):
    def get(self, request: Request, pk: int) -> Response:
        principal = self._principal(request)
        dsr = get_object_or_404(
            DSRRequest, pk=pk, patient_id=principal.patient_id
        )
        versions = dsr.response_versions.filter(
            publication__isnull=False
        ).select_related("publication")
        return Response(
            DSRPublishedResponseReadSerializer(versions, many=True).data
        )


class DSRCommunicationView(DPOAPIView):
    def get(self, request: Request, pk: int) -> Response:
        self.principal_id(request)
        dsr = get_object_or_404(DSRRequest, pk=pk)
        return Response(
            DSRCommunicationReadSerializer(dsr.communications.all(), many=True).data
        )

    def post(self, request: Request, pk: int) -> Response:
        actor_id = self.principal_id(request)
        serializer = DSRCommunicationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            dsr = get_object_or_404(DSRRequest.objects.select_for_update(), pk=pk)
            communication = serializer.save(
                dsr=dsr,
                direction=DSRCommunication.Direction.DPO_TO_PATIENT,
                created_by=actor_id,
            )
            _record_case_event(
                dsr,
                actor_id,
                DSRCaseEvent.EventType.PATIENT_UPDATE_RECORDED,
                "Patient update recorded in the prototype. No external message was sent.",
            )
        return Response(
            DSRCommunicationReadSerializer(communication).data,
            status=status.HTTP_201_CREATED,
        )


class PatientCommunicationView(PatientEndpoint):
    def get(self, request: Request, pk: int) -> Response:
        principal = self._principal(request)
        dsr = get_object_or_404(DSRRequest, pk=pk, patient_id=principal.patient_id)
        return Response(
            DSRCommunicationReadSerializer(dsr.communications.all(), many=True).data
        )

    def post(self, request: Request, pk: int) -> Response:
        principal = self._principal(request)
        serializer = DSRCommunicationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            dsr = get_object_or_404(
                DSRRequest.objects.select_for_update(),
                pk=pk,
                patient_id=principal.patient_id,
            )
            if dsr.state != DSRRequest.State.NEEDS_INFORMATION:
                return Response(
                    {"detail": "This request is not waiting for more information."},
                    status=status.HTTP_409_CONFLICT,
                )
            communication = serializer.save(
                dsr=dsr,
                direction=DSRCommunication.Direction.PATIENT_TO_DPO,
                created_by=principal.patient_id,
            )
            _record_case_event(
                dsr,
                principal.patient_id,
                DSRCaseEvent.EventType.PATIENT_RESPONSE_RECORDED,
                "The subject provided requested information.",
            )
            dsr.provide_information(
                actor_id=principal.patient_id,
                reason="The subject provided requested information",
            )
            dsr.save(update_fields=("state", "waiting_from_state", "updated_at"))
        return Response(
            DSRCommunicationReadSerializer(communication).data,
            status=status.HTTP_201_CREATED,
        )


class DSRWithdrawalView(PatientEndpoint):
    def post(self, request: Request, pk: int) -> Response:
        principal = self._principal(request)
        serializer = DSRWithdrawSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            dsr = get_object_or_404(
                DSRRequest.objects.select_for_update(),
                pk=pk,
                patient_id=principal.patient_id,
            )
            try:
                dsr.withdraw(
                    actor_id=principal.patient_id,
                    reason=serializer.validated_data["reason"]
                    or "Withdrawn by the data subject",
                )
            except TransitionNotAllowed:
                return Response(
                    {"detail": "This request can no longer be withdrawn."},
                    status=status.HTTP_409_CONFLICT,
                )
            dsr.save(
                update_fields=(
                    "state",
                    "resolution_status",
                    "closed_at",
                    "escalated_from",
                    "updated_at",
                )
            )
        return Response(DSRStatusSerializer(dsr).data)


class DSRConsoleView(DPOAPIView):
    def get(self, request: Request) -> Response:
        requests, filters = _filtered_dpo_requests(request.query_params)
        data = DSRDPOStatusSerializer(requests, many=True).data
        heatmap: dict[str, int] = {}
        for dsr in requests:
            heatmap[dsr.department or "Unassigned"] = heatmap.get(dsr.department or "Unassigned", 0) + 1
        return Response(
            {"requests": data, "departmental_heatmap": heatmap, "filters": filters}
        )


class DSRDeadlinePolicyView(DPOAPIView):
    def get(self, request: Request) -> Response:
        self.principal_id(request)
        candidates = DSRDeadlinePolicy.objects.filter(
            effective_at__lte=timezone.now()
        ).order_by(
            "jurisdiction_code", "request_type", "-version"
        )
        latest: dict[tuple[str, str], DSRDeadlinePolicy] = {}
        for policy in candidates:
            latest.setdefault((policy.jurisdiction_code, policy.request_type), policy)
        policies = list(latest.values())
        return Response(DSRDeadlinePolicySerializer(policies, many=True).data)

    def patch(self, request: Request) -> Response:
        actor_id = self.principal_id(request)
        request_type = request.data.get("request_type")
        valid_types = {value for value, _label in DSRRequest.RequestType.choices}
        if request_type not in valid_types:
            return Response(
                {"request_type": "Choose a valid request type."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = DSRDeadlinePolicyUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            updated = serializer.validated_data
            jurisdiction_code = updated["jurisdiction_code"]
            previous = DSRDeadlinePolicy.objects.filter(
                request_type=request_type,
                jurisdiction_code=jurisdiction_code,
                effective_at__lte=timezone.now(),
            ).order_by("-version").first()
            policy_values = {
                "internal_target_days": updated["internal_target_days"],
                "legal_deadline_days": updated["legal_deadline_days"],
                "policy_source": updated.get("policy_source", ""),
                "clock_start_event": updated["clock_start_event"],
                "calendar_days": updated["calendar_days"],
                "extension_allowed": updated["extension_allowed"],
                "maximum_extension_days": updated["maximum_extension_days"],
                "pause_allowed": updated["pause_allowed"],
                "pause_reasons": updated["pause_reasons"],
                "notification_required": updated["notification_required"],
            }
            changed_values = {
                name: {
                    "from": getattr(previous, name) if previous is not None else None,
                    "to": value,
                }
                for name, value in policy_values.items()
                if previous is None or getattr(previous, name) != value
            }
            if previous is not None and not changed_values:
                return Response(DSRDeadlinePolicySerializer(previous).data)
            policy = DSRDeadlinePolicy.objects.create(
                request_type=request_type,
                jurisdiction_code=jurisdiction_code,
                version=(
                    DSRDeadlinePolicy.objects.filter(
                        request_type=request_type,
                        jurisdiction_code=jurisdiction_code,
                    ).order_by("-version").values_list("version", flat=True).first()
                    or 0
                ) + 1,
                effective_at=timezone.now(),
                updated_by=actor_id,
                **policy_values,
            )
            DSRDeadlinePolicyAudit.objects.create(
                policy=policy,
                actor_id=actor_id,
                changed_values=changed_values,
                reason=updated["reason"],
            )
        return Response(DSRDeadlinePolicySerializer(policy).data)


class DSRCaseExceptionView(DPOAPIView):
    def get(self, request: Request, pk: int) -> Response:
        self.principal_id(request)
        dsr = get_object_or_404(DSRRequest, pk=pk)
        return Response(
            DSRCaseExceptionReadSerializer(dsr.exceptions.all(), many=True).data
        )

    def post(self, request: Request, pk: int) -> Response:
        actor_id = self.principal_id(request)
        serializer = DSRCaseExceptionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            dsr = get_object_or_404(DSRRequest.objects.select_for_update(), pk=pk)
            if dsr.major_status == "Completed":
                return Response(
                    {"detail": "Exceptions cannot be added to a completed case."},
                    status=status.HTTP_409_CONFLICT,
                )
            values = serializer.validated_data
            exception_type = values["exception_type"]
            policy = dsr.deadline_policy
            effect_seconds = 0
            effect_days = values.get("deadline_effect_days")
            if exception_type == DSRCaseException.ExceptionType.CLARIFICATION and dsr.state not in {
                DSRRequest.State.SUBMITTED,
                DSRRequest.State.ACCEPTED,
                DSRRequest.State.DEPT_SEARCH,
                DSRRequest.State.PRIVACY_REVIEW,
                DSRRequest.State.LEGAL_REVIEW,
                DSRRequest.State.RESPONSE_PREP,
            }:
                return Response(
                    {"detail": "This case is not in a stage where clarification can be requested."},
                    status=status.HTTP_409_CONFLICT,
                )
            if exception_type == DSRCaseException.ExceptionType.EXTENSION:
                if (
                    policy is None
                    or not policy.extension_allowed
                    or effect_days is None
                    or effect_days > policy.maximum_extension_days
                ):
                    return Response(
                        {"detail": "This policy does not authorize the requested extension."},
                        status=status.HTTP_409_CONFLICT,
                    )
                if dsr.legal_deadline_at is None:
                    return Response(
                        {"detail": "A legal deadline must be configured before extending it."},
                        status=status.HTTP_409_CONFLICT,
                    )
            elif exception_type == DSRCaseException.ExceptionType.DEADLINE_PAUSE:
                if (
                    policy is None
                    or not policy.pause_allowed
                    or str(values.get("policy_reason", "")).strip()
                    not in policy.pause_reasons
                    or effect_days is None
                ):
                    return Response(
                        {"detail": "This policy does not authorize the requested pause."},
                        status=status.HTTP_409_CONFLICT,
                    )
                if dsr.legal_deadline_at is None:
                    return Response(
                        {"detail": "A legal deadline must be configured before pausing it."},
                        status=status.HTTP_409_CONFLICT,
                    )
            if exception_type in {
                DSRCaseException.ExceptionType.EXTENSION,
                DSRCaseException.ExceptionType.DEADLINE_PAUSE,
            }:
                if policy is not None and policy.notification_required and not str(
                    values.get("patient_notification", "")
                ).strip():
                    return Response(
                        {"patient_notification": "This policy requires patient notification."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                effect_seconds = int(effect_days) * 24 * 60 * 60
                dsr.legal_deadline_at += timedelta(seconds=effect_seconds)
                dsr.save(update_fields=("legal_deadline_at", "updated_at"))

            notification = str(values.get("patient_notification", "")).strip()
            exception = DSRCaseException.objects.create(
                dsr=dsr,
                exception_type=exception_type,
                reason=values["reason"],
                policy_reason=str(values.get("policy_reason", "")).strip(),
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
            if exception_type == DSRCaseException.ExceptionType.CLARIFICATION:
                dsr.request_information(actor_id=actor_id, reason=values["reason"])
                dsr.save(
                    update_fields=("state", "waiting_from_state", "updated_at")
                )
            _record_case_event(
                dsr,
                actor_id,
                DSRCaseEvent.EventType.EXCEPTION_RECORDED,
                f"{exception.get_exception_type_display()} recorded: {exception.reason}",
            )
        return Response(
            DSRCaseExceptionReadSerializer(exception).data,
            status=status.HTTP_201_CREATED,
        )


class DSRCSVExportView(DPOAPIView):
    def get(self, request: Request) -> HttpResponse:
        actor_id = self.principal_id(request)
        return _dpo_csv_response(request.query_params, actor_id)


class DSRTransitionView(RoleAPIView):
    allowed_roles = frozenset({"dpo", "independent_reviewer"})
    REVIEW_DECISION_ACTIONS = frozenset({"approve_legal_review", "return_from_legal_review"})

    def post(self, request: Request, pk: int) -> Response:
        actor_id = self.principal_id(request)
        action_name = request.data.get("action")
        if request.user.role == "independent_reviewer":
            if action_name not in self.REVIEW_DECISION_ACTIONS:
                raise PermissionDenied(
                    "Independent reviewers may only approve or return their assigned review."
                )
        elif action_name in self.REVIEW_DECISION_ACTIONS:
            raise PermissionDenied(
                "Use an Independent Reviewer identity to decide this review."
            )
        serializer = DSRTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            dsr = get_object_or_404(DSRRequest.objects.select_for_update(), pk=pk)
            if request.user.role == "independent_reviewer":
                review_request = dsr.legal_review_requests.order_by("-pk").first()
                if review_request is None or review_request.assigned_reviewer != actor_id:
                    raise PermissionDenied("This review is assigned to another reviewer.")
            try:
                _apply_transition(
                    dsr,
                    action_name=serializer.validated_data["action"],
                    actor_id=actor_id,
                    reason=serializer.validated_data["reason"],
                    resolution_status=serializer.validated_data.get(
                        "resolution_status"
                    ),
                    proposed_outcome=serializer.validated_data.get(
                        "proposed_outcome"
                    ),
                    duplicate_of_id=serializer.validated_data.get("duplicate_of"),
                    assigned_reviewer=serializer.validated_data.get(
                        "assigned_reviewer", ""
                    ),
                )
            except TransitionNotAllowed as error:
                return Response(
                    {"detail": str(error)},
                    status=status.HTTP_409_CONFLICT,
                )
        return Response(DSRDPOStatusSerializer(dsr).data)