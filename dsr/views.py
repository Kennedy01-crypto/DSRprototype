"""HTTP API for the DSR workflow.

Views are deliberately thin: authenticate, check the role (``RolePermission``),
validate input shape (serializers), call one function in ``dsr.services``, and
serialize the result. Workflow rules live in ``dsr.services``; read-side
helpers in ``dsr.selectors``; errors map to HTTP in ``dsr.exceptions``.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from . import selectors, services
from .authentication import PatientPortalJWTAuthentication, SimulatedPrincipal
from .exceptions import CaseNotFound
from .models import (
    DSRDeadlinePolicy,
    DSRLegalReviewRequest,
    DSRRequest,
    DSRReviewTask,
    DSRSourceSearch,
)
from .permissions import RolePermission
from .serializers import (
    DSRAssignmentSerializer,
    DSRCaseEscalationReadSerializer,
    DSRCaseExceptionCreateSerializer,
    DSRCaseExceptionReadSerializer,
    DSRCommunicationReadSerializer,
    DSRCommunicationSerializer,
    DSRDeadlinePolicySerializer,
    DSRDeadlinePolicyUpdateSerializer,
    DSRDecisionReadSerializer,
    DSRDecisionSerializer,
    DSRDPOStatusSerializer,
    DSRJurisdictionUpdateSerializer,
    DSRLegalReviewRequestReadSerializer,
    DSRPublishedResponseReadSerializer,
    DSRResponseFinalizeSerializer,
    DSRResponseVersionReadSerializer,
    DSRReviewTaskReadSerializer,
    DSRReviewTaskSerializer,
    DSRReviewTaskUpdateSerializer,
    DSRSourceSearchCreateSerializer,
    DSRSourceSearchReadSerializer,
    DSRSourceSearchUpdateSerializer,
    DSRStatusSerializer,
    DSRSubmitSerializer,
    DSRTransitionSerializer,
    DSRWithdrawSerializer,
)


def api_index(request: HttpRequest) -> JsonResponse:
    """Plain liveness response at ``/``. The user interface is the Next.js app."""
    return JsonResponse(
        {
            "service": "dsr-api",
            "status": "ok",
            "api": "/api/v1/",
            "ui": "Use the Next.js frontend. This service has no browser pages.",
        }
    )


# --------------------------------------------------------------------------
# Base classes
# --------------------------------------------------------------------------


class RoleAPIView(APIView):
    """``RolePermission`` enforces ``allowed_roles`` before any handler runs,
    so handlers can rely on ``request.user`` having one of those roles."""

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


class DPOAPIView(RoleAPIView):
    allowed_roles = frozenset({"dpo"})


# --------------------------------------------------------------------------
# Patient portal
# --------------------------------------------------------------------------


class SubmitDSRView(PatientEndpoint):
    def post(self, request: Request) -> Response:
        patient_id = self.principal_id(request)
        serializer = DSRSubmitSerializer(data=request.data, context={"patient_id": patient_id})
        serializer.is_valid(raise_exception=True)
        dsr = services.submit_request(patient_id, serializer.validated_data)
        return Response(DSRStatusSerializer(dsr).data, status=status.HTTP_201_CREATED)


class MyRequestsView(PatientEndpoint):
    def get(self, request: Request) -> Response:
        requests = DSRRequest.objects.filter(patient_id=self.principal_id(request))
        return Response(DSRStatusSerializer(requests, many=True).data)


class PatientDSRDetailView(PatientEndpoint):
    def get(self, request: Request, pk: int) -> Response:
        dsr = get_object_or_404(DSRRequest, pk=pk, patient_id=self.principal_id(request))
        return Response({**DSRStatusSerializer(dsr).data, "can_withdraw": dsr.can_withdraw})


class PatientPublishedResponsesView(PatientEndpoint):
    def get(self, request: Request, pk: int) -> Response:
        dsr = get_object_or_404(DSRRequest, pk=pk, patient_id=self.principal_id(request))
        versions = dsr.response_versions.filter(publication__isnull=False).select_related(
            "publication"
        )
        return Response(DSRPublishedResponseReadSerializer(versions, many=True).data)


class PatientCommunicationView(PatientEndpoint):
    def get(self, request: Request, pk: int) -> Response:
        dsr = get_object_or_404(DSRRequest, pk=pk, patient_id=self.principal_id(request))
        return Response(DSRCommunicationReadSerializer(dsr.communications.all(), many=True).data)

    def post(self, request: Request, pk: int) -> Response:
        serializer = DSRCommunicationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        communication = services.record_patient_reply(
            pk,
            patient_id=self.principal_id(request),
            message=serializer.validated_data["message"],
        )
        return Response(
            DSRCommunicationReadSerializer(communication).data,
            status=status.HTTP_201_CREATED,
        )


class DSRWithdrawalView(PatientEndpoint):
    def post(self, request: Request, pk: int) -> Response:
        serializer = DSRWithdrawSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        dsr = services.withdraw_request(
            pk,
            patient_id=self.principal_id(request),
            reason=serializer.validated_data["reason"],
        )
        return Response(DSRStatusSerializer(dsr).data)


# --------------------------------------------------------------------------
# DPO queue, reports and policy
# --------------------------------------------------------------------------


class DSRConsoleView(DPOAPIView):
    def get(self, request: Request) -> Response:
        requests, filters = selectors.filtered_dpo_requests(request.query_params)
        heatmap: dict[str, int] = {}
        for dsr in requests:
            key = dsr.department or "Unassigned"
            heatmap[key] = heatmap.get(key, 0) + 1
        return Response(
            {
                "requests": DSRDPOStatusSerializer(requests, many=True).data,
                "departmental_heatmap": heatmap,
                "filters": filters,
            }
        )


CSV_COLUMNS = (
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


def _csv_safe_cell(value: object) -> str:
    """Prefix values a spreadsheet would treat as a formula."""
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


class DSRCSVExportView(DPOAPIView):
    def get(self, request: Request) -> HttpResponse:
        requests, _filters = selectors.filtered_dpo_requests(request.query_params)
        cases = list(requests.order_by("pk"))
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="dsr-case-report.csv"'
        writer = csv.writer(response)
        writer.writerow(CSV_COLUMNS)
        for dsr in cases:
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
        services.record_export(cases, actor_id=self.principal_id(request))
        return response


class DSRDeadlinePolicyView(DPOAPIView):
    def get(self, request: Request) -> Response:
        latest: dict[tuple[str, str], DSRDeadlinePolicy] = {}
        for policy in DSRDeadlinePolicy.objects.filter(
            effective_at__lte=timezone.now()
        ).order_by("jurisdiction_code", "request_type", "-version"):
            latest.setdefault((policy.jurisdiction_code, policy.request_type), policy)
        return Response(DSRDeadlinePolicySerializer(list(latest.values()), many=True).data)

    def patch(self, request: Request) -> Response:
        request_type = request.data.get("request_type")
        if request_type not in DSRRequest.RequestType.values:
            return Response(
                {"request_type": "Choose a valid request type."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = DSRDeadlinePolicyUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        policy = services.update_deadline_policy(
            actor_id=self.principal_id(request),
            request_type=request_type,
            data=serializer.validated_data,
        )
        return Response(DSRDeadlinePolicySerializer(policy).data)


# --------------------------------------------------------------------------
# DPO case workspace
# --------------------------------------------------------------------------


class DSRCaseDetailView(DPOAPIView):
    allowed_roles = frozenset({"dpo", "independent_reviewer"})

    def get(self, request: Request, pk: int) -> Response:
        actor_id = self.principal_id(request)
        dsr = get_object_or_404(DSRRequest, pk=pk)
        common = {
            "request": DSRDPOStatusSerializer(dsr).data,
            "activity": selectors.case_activity(dsr),
            "work_items": DSRReviewTaskReadSerializer(dsr.review_tasks.all(), many=True).data,
            "escalations": DSRCaseEscalationReadSerializer(
                dsr.escalations.all(), many=True
            ).data,
            "exceptions": DSRCaseExceptionReadSerializer(dsr.exceptions.all(), many=True).data,
        }
        if request.user.role == "independent_reviewer":
            return Response(self._reviewer_view(dsr, actor_id, common))

        actions = selectors.available_transition_actions(dsr, actor_id)
        return Response(
            {
                **common,
                "communications": DSRCommunicationReadSerializer(
                    dsr.communications.all(), many=True
                ).data,
                "legal_reviews": DSRLegalReviewRequestReadSerializer(
                    dsr.legal_review_requests.select_related(
                        "response_version"
                    ).prefetch_related("decision"),
                    many=True,
                ).data,
                "source_searches": DSRSourceSearchReadSerializer(
                    dsr.source_searches.all(), many=True
                ).data,
                "decisions": DSRDecisionReadSerializer(dsr.decisions.all(), many=True).data,
                "response_versions": DSRResponseVersionReadSerializer(
                    dsr.response_versions.select_related("publication"), many=True
                ).data,
                "response_readiness": services.response_signoff_error(dsr),
                "available_actions": [
                    {"label": label, "action": action} for label, action in actions
                ],
            }
        )

    @staticmethod
    def _reviewer_view(dsr: DSRRequest, reviewer_id: str, common: dict) -> dict:
        actions = selectors.reviewer_actions(dsr, reviewer_id)
        if actions is None:
            raise PermissionDenied("No independent review is assigned to this reviewer.")
        review_request = (
            dsr.legal_review_requests.filter(assigned_reviewer=reviewer_id)
            .order_by("-pk")
            .first()
        )
        return {
            **common,
            "communications": [],
            "legal_reviews": DSRLegalReviewRequestReadSerializer(
                [review_request], many=True
            ).data,
            "review_material": {
                "response_version": review_request.response_version.version,
                "response_content": review_request.response_version.content,
                "case_snapshot": review_request.case_snapshot,
                "source_searches": DSRSourceSearchReadSerializer(
                    dsr.source_searches.all(), many=True
                ).data,
                "decisions": DSRDecisionReadSerializer(dsr.decisions.all(), many=True).data,
            },
            "available_actions": [
                {"label": label, "action": action} for label, action in actions
            ],
        }


class DSRTransitionView(RoleAPIView):
    allowed_roles = frozenset({"dpo", "independent_reviewer"})

    def post(self, request: Request, pk: int) -> Response:
        role = self.principal(request).role
        action = request.data.get("action")
        if role == "independent_reviewer":
            if action not in selectors.REVIEW_DECISION_ACTIONS:
                raise PermissionDenied(
                    "Independent reviewers may only approve or return their assigned review."
                )
        elif action in selectors.REVIEW_DECISION_ACTIONS:
            raise PermissionDenied("Use an Independent Reviewer identity to decide this review.")
        serializer = DSRTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        dsr = services.apply_transition(
            pk,
            action=values["action"],
            actor_id=self.principal_id(request),
            actor_role=role,
            reason=values["reason"],
            resolution_status=values.get("resolution_status"),
            proposed_outcome=values.get("proposed_outcome"),
            duplicate_of_id=values.get("duplicate_of"),
            assigned_reviewer=values.get("assigned_reviewer", ""),
        )
        return Response(DSRDPOStatusSerializer(dsr).data)


class DSRAssignmentView(DPOAPIView):
    def post(self, request: Request, pk: int) -> Response:
        serializer = DSRAssignmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        dsr = services.assign_case(
            pk,
            actor_id=self.principal_id(request),
            assigned_to=serializer.validated_data["assigned_to"],
        )
        return Response(DSRDPOStatusSerializer(dsr).data)


class DSRJurisdictionView(DPOAPIView):
    def patch(self, request: Request, pk: int) -> Response:
        serializer = DSRJurisdictionUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        dsr = services.set_jurisdiction(
            pk,
            actor_id=self.principal_id(request),
            jurisdiction_code=serializer.validated_data["jurisdiction_code"],
            reason=serializer.validated_data["reason"],
        )
        return Response(DSRDPOStatusSerializer(dsr).data)


class DSRCommunicationView(DPOAPIView):
    def get(self, request: Request, pk: int) -> Response:
        dsr = get_object_or_404(DSRRequest, pk=pk)
        return Response(DSRCommunicationReadSerializer(dsr.communications.all(), many=True).data)

    def post(self, request: Request, pk: int) -> Response:
        serializer = DSRCommunicationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        communication = services.send_patient_update(
            pk,
            actor_id=self.principal_id(request),
            message=serializer.validated_data["message"],
        )
        return Response(
            DSRCommunicationReadSerializer(communication).data,
            status=status.HTTP_201_CREATED,
        )


class DSRCaseExceptionView(DPOAPIView):
    def get(self, request: Request, pk: int) -> Response:
        dsr = get_object_or_404(DSRRequest, pk=pk)
        return Response(DSRCaseExceptionReadSerializer(dsr.exceptions.all(), many=True).data)

    def post(self, request: Request, pk: int) -> Response:
        serializer = DSRCaseExceptionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        exception = services.record_case_exception(
            pk, actor_id=self.principal_id(request), data=serializer.validated_data
        )
        return Response(
            DSRCaseExceptionReadSerializer(exception).data,
            status=status.HTTP_201_CREATED,
        )


# --------------------------------------------------------------------------
# Work items
# --------------------------------------------------------------------------


class DSRReviewTaskListView(DPOAPIView):
    allowed_roles = {
        "GET": frozenset({"dpo", "department_contributor"}),
        "POST": frozenset({"dpo"}),
    }

    def get(self, request: Request, pk: int) -> Response:
        dsr = get_object_or_404(DSRRequest, pk=pk)
        tasks = dsr.review_tasks.all()
        if request.user.role == "department_contributor":
            tasks = tasks.filter(assigned_to=self.principal_id(request))
            # Contributors only learn that a case exists if they work on it.
            if not tasks.exists():
                raise CaseNotFound("No request matches the given query.")
        return Response(DSRReviewTaskReadSerializer(tasks, many=True).data)

    def post(self, request: Request, pk: int) -> Response:
        serializer = DSRReviewTaskSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task = services.create_task(
            pk, actor_id=self.principal_id(request), data=serializer.validated_data
        )
        return Response(DSRReviewTaskReadSerializer(task).data, status=status.HTTP_201_CREATED)


class DSRReviewTaskDetailView(DPOAPIView):
    allowed_roles = frozenset({"dpo", "department_contributor"})

    def patch(self, request: Request, pk: int, task_pk: int) -> Response:
        principal = self.principal(request)
        task = get_object_or_404(DSRReviewTask, dsr_id=pk, pk=task_pk)
        services.check_task_access(task, actor_id=principal.patient_id, role=principal.role)
        serializer = DSRReviewTaskUpdateSerializer(data=request.data, instance=task, partial=True)
        serializer.is_valid(raise_exception=True)
        task = services.update_task(
            pk,
            task_pk,
            actor_id=principal.patient_id,
            role=principal.role,
            data=serializer.validated_data,
        )
        return Response(DSRReviewTaskReadSerializer(task).data)


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


# --------------------------------------------------------------------------
# Source searches, decisions and responses
# --------------------------------------------------------------------------


class DSRSourceSearchListView(DPOAPIView):
    def get(self, request: Request, pk: int) -> Response:
        dsr = get_object_or_404(DSRRequest, pk=pk)
        return Response(DSRSourceSearchReadSerializer(dsr.source_searches.all(), many=True).data)

    def post(self, request: Request, pk: int) -> Response:
        serializer = DSRSourceSearchCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        search = services.create_source_search(
            pk, actor_id=self.principal_id(request), data=serializer.validated_data
        )
        return Response(
            DSRSourceSearchReadSerializer(search).data, status=status.HTTP_201_CREATED
        )


class DSRSourceSearchDetailView(DPOAPIView):
    def patch(self, request: Request, pk: int, search_pk: int) -> Response:
        search = get_object_or_404(DSRSourceSearch, dsr_id=pk, pk=search_pk)
        serializer = DSRSourceSearchUpdateSerializer(search, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        search = services.update_source_search(
            pk, search_pk, actor_id=self.principal_id(request), data=serializer.validated_data
        )
        return Response(DSRSourceSearchReadSerializer(search).data)


class DSRDecisionListView(DPOAPIView):
    def get(self, request: Request, pk: int) -> Response:
        dsr = get_object_or_404(DSRRequest, pk=pk)
        return Response(DSRDecisionReadSerializer(dsr.decisions.all(), many=True).data)

    def post(self, request: Request, pk: int) -> Response:
        serializer = DSRDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        decision = services.record_decision(
            pk, actor_id=self.principal_id(request), data=serializer.validated_data
        )
        return Response(DSRDecisionReadSerializer(decision).data, status=status.HTTP_201_CREATED)


class DSRResponseDraftView(DPOAPIView):
    def get(self, request: Request, pk: int) -> Response:
        dsr = get_object_or_404(DSRRequest, pk=pk)
        return Response(selectors.build_response_draft(dsr))


class DSRResponseVersionListView(DPOAPIView):
    def get(self, request: Request, pk: int) -> Response:
        dsr = get_object_or_404(DSRRequest, pk=pk)
        return Response(
            DSRResponseVersionReadSerializer(
                dsr.response_versions.select_related("publication"), many=True
            ).data
        )

    def post(self, request: Request, pk: int) -> Response:
        serializer = DSRResponseFinalizeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        version = services.finalize_response(
            pk,
            actor_id=self.principal_id(request),
            content=serializer.validated_data["content"],
        )
        return Response(
            DSRResponseVersionReadSerializer(version).data, status=status.HTTP_201_CREATED
        )


class DSRResponsePublishView(DPOAPIView):
    def post(self, request: Request, pk: int, version_pk: int) -> Response:
        version = services.publish_response(pk, version_pk, actor_id=self.principal_id(request))
        return Response(
            DSRResponseVersionReadSerializer(version).data, status=status.HTTP_201_CREATED
        )
