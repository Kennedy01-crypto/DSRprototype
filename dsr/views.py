from __future__ import annotations

import csv
import re
from datetime import datetime
from typing import Callable

from django.db import transaction
from django.http import HttpRequest, HttpResponse, QueryDict
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
from .models import (
    DSRCaseEvent,
    DSRCommunication,
    DSRDecision,
    DSRRequest,
    DSRResponsePublication,
    DSRResponseVersion,
    DSRReviewTask,
    DSRSourceSearch,
)
from .serializers import (
    DSRAssignmentSerializer,
    DSRCommunicationSerializer,
    DSRDecisionSerializer,
    DSRCommunicationReadSerializer,
    DSRDecisionReadSerializer,
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

ACTIVE_STATES = (
    DSRRequest.State.ACCEPTED,
    DSRRequest.State.DEPT_SEARCH,
    DSRRequest.State.PRIVACY_REVIEW,
    DSRRequest.State.LEGAL_REVIEW,
    DSRRequest.State.RESPONSE_PREP,
    DSRRequest.State.NEEDS_INFORMATION,
)
OPEN_QUEUE_STATES = (*ACTIVE_STATES, DSRRequest.State.ESCALATED)

TRANSITION_ACTIONS = {
    DSRRequest.State.ACCEPTED: (
        ("Start departmental search", "start_departmental_search"),
        ("Request more information", "request_information"),
        ("Reject request", "reject"),
        ("Escalate", "escalate"),
    ),
    DSRRequest.State.DEPT_SEARCH: (
        ("Send to privacy review", "send_to_privacy_review"),
        ("Request more information", "request_information"),
        ("Reject request", "reject"),
        ("Escalate", "escalate"),
    ),
    DSRRequest.State.PRIVACY_REVIEW: (
        ("Request legal review", "request_legal_review"),
        ("Approve privacy review", "approve_privacy_review"),
        ("Request more information", "request_information"),
        ("Reject request", "reject"),
        ("Escalate", "escalate"),
    ),
    DSRRequest.State.LEGAL_REVIEW: (
        ("Approve legal review", "approve_legal_review"),
        ("Request more information", "request_information"),
        ("Reject request", "reject"),
        ("Escalate", "escalate"),
    ),
    DSRRequest.State.RESPONSE_PREP: (
        ("Approve and close", "approve_and_close"),
        ("Request more information", "request_information"),
        ("Escalate", "escalate"),
    ),
    DSRRequest.State.ESCALATED: (
        ("Resume escalated case", "resume_escalated"),
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
    assignee_filter = params.get("assigned_to", "").strip()
    queue_filter = params.get("queue", "")
    valid_states = {value for value, _label in DSRRequest.State.choices}
    valid_types = {value for value, _label in DSRRequest.RequestType.choices}
    if state_filter in valid_states:
        requests = requests.filter(state=state_filter)
    else:
        state_filter = ""
    if type_filter in valid_types:
        requests = requests.filter(request_type=type_filter)
    else:
        type_filter = ""
    if assignee_filter:
        requests = requests.filter(assigned_to__iexact=assignee_filter)
    now = timezone.now()
    if queue_filter == "overdue":
        requests = requests.filter(state__in=ACTIVE_STATES, response_due_at__lt=now)
    elif queue_filter == "unassigned":
        requests = requests.filter(assigned_to="", state__in=OPEN_QUEUE_STATES)
    elif queue_filter == "escalated":
        requests = requests.filter(state=DSRRequest.State.ESCALATED)
    else:
        queue_filter = ""
    return requests, {
        "state_filter": state_filter,
        "type_filter": type_filter,
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
    if not dsr.source_searches_complete():
        return "Complete all mock source searches before finalizing a response."
    latest_decision = dsr.decisions.order_by("-created_at", "-pk").first()
    if latest_decision is None or latest_decision.outcome == DSRDecision.Outcome.FURTHER_REVIEW:
        return "Record a final decision before finalizing a response."
    return None


def _response_case_snapshot(dsr: DSRRequest) -> dict[str, object]:
    draft = _build_response_draft(dsr)
    return {
        "case_id": dsr.pk,
        "request_type": dsr.get_request_type_display(),
        "description": dsr.description,
        "department": dsr.department,
        "submitted_at": dsr.submitted_at.isoformat(),
        "response_due_at": dsr.response_due_at.isoformat(),
        "resolution": dsr.get_resolution_status_display(),
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
            "state",
            "assigned_to",
            "submitted_at",
            "response_due_at",
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
                    dsr.get_state_display(),
                    dsr.assigned_to,
                    dsr.submitted_at.isoformat(),
                    dsr.response_due_at.isoformat(),
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
) -> None:
    if (
        action_name == "resume_escalated"
        and dsr.escalated_from == DSRRequest.State.NEEDS_INFORMATION
    ):
        action_name = "resume_escalated_waiting_for_information"
    action: Callable[..., None] = getattr(dsr, action_name)
    kwargs: dict[str, str] = {"actor_id": actor_id, "reason": reason}
    if action_name == "approve_and_close":
        if resolution_status is None:
            raise ValueError("A resolution status is required to close a request.")
        kwargs["resolution_status"] = resolution_status
    action(**kwargs)
    if action_name in {
        "resume_escalated",
        "resume_escalated_waiting_for_information",
    }:
        dsr.escalated_from = ""
    dsr.save(
        update_fields=(
            "state",
            "resolution_status",
            "closed_at",
            "escalated_from",
            "updated_at",
        )
    )
    if action_name in {"approve_and_close", "reject"}:
        DSRDecision.objects.create(
            dsr=dsr,
            category="overall",
            outcome=(
                DSRDecision.Outcome.REJECTED
                if action_name == "reject"
                else dsr.resolution_status
            ),
            rationale=reason,
            recorded_by=actor_id,
        )
        _record_case_event(
            dsr,
            actor_id,
            DSRCaseEvent.EventType.DECISION_RECORDED,
            (
                "Request rejected and rationale recorded."
                if action_name == "reject"
                else f"Final resolution recorded: {dsr.get_resolution_status_display()}."
            ),
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
  <p><a href="/demo/login/?role=patient">Patient portal</a> | <a href="/demo/login/?role=dpo">DPO workspace</a></p>
  <p>To load fictional example cases, run <code>docker compose exec web python manage.py seed_demo_data</code>.</p>
  <h2>Available endpoints</h2>
  <ul>
    <li><code>GET /</code> — this homepage</li>
    <li><code>POST /api/v1/dsrs/submit/</code> — submit a patient request</li>
    <li><code>GET /api/v1/dsrs/my-requests/</code> — list the authenticated patient's requests</li>
    <li><code>GET /api/v1/dpo/dsrs/</code> — list all requests and a departmental heatmap</li>
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
    if session_role not in {"patient", "dpo"} or not isinstance(principal_id, str):
        return None
    if role is not None and session_role != role:
        return None
    return principal_id


@require_http_methods(["GET", "POST"])
def demo_login(request: HttpRequest) -> HttpResponse:
    role = request.POST.get("role", request.GET.get("role", "patient"))
    if role not in {"patient", "dpo"}:
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
            return redirect("patient-home" if role == "patient" else "dpo-home")
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
                        dsr.save(update_fields=("state", "updated_at"))
                        return redirect("patient-home")
                else:
                    errors = serializer.errors
        else:
            serializer = DSRSubmitSerializer(data=request.POST)
            if serializer.is_valid():
                with transaction.atomic():
                    dsr = serializer.save(patient_id=patient_id)
                    dsr.accept_submission(
                        actor_id=patient_id,
                        reason="Submitted through the prototype patient portal",
                    )
                    dsr.save(update_fields=("state", "updated_at"))
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
            "states": DSRRequest.State.choices,
            "request_types": DSRRequest.RequestType.choices,
            "active_count": DSRRequest.objects.filter(state__in=ACTIVE_STATES).count(),
            "overdue_count": DSRRequest.objects.filter(
                response_due_at__lt=now, state__in=ACTIVE_STATES
            ).count(),
        },
    )


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
                elif hasattr(version, "publication"):
                    errors = ["This response version has already been published."]
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
                        )
                        return redirect("dpo-request", pk=dsr.pk)
                    except TransitionNotAllowed:
                        errors.append(
                            "That action is not available from the request's current state."
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
            "actions": TRANSITION_ACTIONS.get(dsr.state, ()),
            "errors": errors,
            "task_statuses": DSRReviewTask.Status.choices,
            "decision_outcomes": DSRDecision.Outcome.choices,
        },
    )


class PatientEndpoint(APIView):
    authentication_classes = (PatientPortalJWTAuthentication,)

    def _principal(self, request: Request) -> SimulatedPrincipal:
        if not isinstance(request.user, SimulatedPrincipal) or request.user.role != "patient":
            raise PermissionDenied("A patient portal identity is required")
        return request.user


class SubmitDSRView(PatientEndpoint):
    def post(self, request: Request) -> Response:
        principal = self._principal(request)
        serializer = DSRSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            dsr = serializer.save(patient_id=principal.patient_id)
            dsr.accept_submission(actor_id=principal.patient_id, reason="Authenticated portal intake")
            dsr.save(update_fields=("state", "updated_at"))
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


class DPOPermission:
    @staticmethod
    def check(request: Request) -> None:
        if not isinstance(request.user, SimulatedPrincipal) or request.user.role != "dpo":
            raise PermissionDenied("DPO role is required")


class DPOAPIView(APIView):
    authentication_classes = (PatientPortalJWTAuthentication,)

    def principal_id(self, request: Request) -> str:
        DPOPermission.check(request)
        if not isinstance(request.user, SimulatedPrincipal):
            raise PermissionDenied("DPO role is required")
        return request.user.patient_id


class DSRCaseDetailView(DPOAPIView):
    def get(self, request: Request, pk: int) -> Response:
        self.principal_id(request)
        dsr = get_object_or_404(DSRRequest, pk=pk)
        actions = TRANSITION_ACTIONS.get(dsr.state, ())
        return Response(
            {
                "request": DSRStatusSerializer(dsr).data,
                "activity": _case_activity(dsr),
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
        return Response(DSRStatusSerializer(dsr).data)


class DSRReviewTaskListView(DPOAPIView):
    def get(self, request: Request, pk: int) -> Response:
        self.principal_id(request)
        dsr = get_object_or_404(DSRRequest, pk=pk)
        return Response(DSRReviewTaskReadSerializer(dsr.review_tasks.all(), many=True).data)

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


class DSRReviewTaskDetailView(DPOAPIView):
    def patch(self, request: Request, pk: int, task_pk: int) -> Response:
        actor_id = self.principal_id(request)
        serializer = DSRReviewTaskUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            dsr = get_object_or_404(DSRRequest.objects.select_for_update(), pk=pk)
            task = get_object_or_404(DSRReviewTask.objects.select_for_update(), dsr=dsr, pk=task_pk)
            task.status = serializer.validated_data["status"]
            task.updated_by = actor_id
            task.completed_at = (
                timezone.now() if task.status == DSRReviewTask.Status.COMPLETED else None
            )
            task.save(update_fields=("status", "updated_by", "completed_at", "updated_at"))
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
            if hasattr(version, "publication"):
                return Response(
                    {"detail": "This response version has already been published."},
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
            dsr.save(update_fields=("state", "updated_at"))
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


class DSRConsoleView(APIView):
    authentication_classes = (PatientPortalJWTAuthentication,)

    def get(self, request: Request) -> Response:
        DPOPermission.check(request)
        requests, filters = _filtered_dpo_requests(request.query_params)
        data = DSRStatusSerializer(requests, many=True).data
        heatmap: dict[str, int] = {}
        for dsr in requests:
            heatmap[dsr.department or "Unassigned"] = heatmap.get(dsr.department or "Unassigned", 0) + 1
        return Response(
            {"requests": data, "departmental_heatmap": heatmap, "filters": filters}
        )


class DSRCSVExportView(DPOAPIView):
    def get(self, request: Request) -> HttpResponse:
        actor_id = self.principal_id(request)
        return _dpo_csv_response(request.query_params, actor_id)


class DSRTransitionView(APIView):
    authentication_classes = (PatientPortalJWTAuthentication,)

    def post(self, request: Request, pk: int) -> Response:
        DPOPermission.check(request)
        serializer = DSRTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            dsr = get_object_or_404(DSRRequest.objects.select_for_update(), pk=pk)
            try:
                _apply_transition(
                    dsr,
                    action_name=serializer.validated_data["action"],
                    actor_id=request.user.patient_id,
                    reason=serializer.validated_data["reason"],
                    resolution_status=serializer.validated_data.get(
                        "resolution_status"
                    ),
                )
            except TransitionNotAllowed:
                return Response(
                    {"detail": "That action is not available from the request's current state."},
                    status=status.HTTP_409_CONFLICT,
                )
        return Response(DSRStatusSerializer(dsr).data)