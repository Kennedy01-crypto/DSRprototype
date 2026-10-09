from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from dsr.models import (
    DSRCaseEvent,
    DSRCommunication,
    DSRDecision,
    DSRAuditLog,
    DSRRequest,
    DSRResponsePublication,
    DSRReviewTask,
    DSRSourceSearch,
)
from dsr.tasks import monitor_sla_deadlines


class DSRApiTests(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()

    def create_request(
        self,
        patient_id: str = "alice",
        state: str = DSRRequest.State.ACCEPTED,
        due_at=None,
    ) -> DSRRequest:
        request = DSRRequest.objects.create(
            patient_id=patient_id,
            request_type=DSRRequest.RequestType.ACCESS,
            description="Synthetic test request",
            department="Records",
            response_due_at=due_at or timezone.now() + timedelta(days=10),
        )
        request.accept_submission(actor_id=patient_id, reason="test intake")
        request.save(update_fields=("state", "updated_at"))
        if state == DSRRequest.State.DEPT_SEARCH:
            request.start_departmental_search(actor_id="staff-1", reason="test")
            request.save(update_fields=("state", "updated_at"))
        return request

    def create_completed_search(self, request: DSRRequest) -> DSRSourceSearch:
        return DSRSourceSearch.objects.create(
            dsr=request,
            source_system=DSRSourceSearch.SourceSystem.CIMS,
            status=DSRSourceSearch.Status.COMPLETE,
            summary="Synthetic search complete; no source data was retrieved.",
            created_by="reviewer",
            updated_by="reviewer",
        )

    def prepare_response(self, request: DSRRequest) -> None:
        if not request.source_searches.exists():
            self.create_completed_search(request)
        if request.state == DSRRequest.State.DEPT_SEARCH:
            request.send_to_privacy_review(actor_id="reviewer", reason="Ready for review")
            request.save(update_fields=("state", "updated_at"))
        if request.state == DSRRequest.State.PRIVACY_REVIEW:
            request.approve_privacy_review(actor_id="reviewer", reason="Review complete")
            request.save(update_fields=("state", "updated_at"))
        if not request.decisions.exists():
            DSRDecision.objects.create(
                dsr=request,
                category="overall",
                outcome=DSRDecision.Outcome.FULFILLED,
                rationale="Synthetic final decision.",
                recorded_by="reviewer",
            )

    def test_patient_can_submit_and_only_list_own_requests(self) -> None:
        self.client.credentials(HTTP_AUTHORIZATION="Bearer patient:alice")
        response = self.client.post(
            "/api/v1/dsrs/submit/",
            {
                "request_type": "ACCESS",
                "description": "Synthetic request",
                "department": "Records",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertIn("response_due_at", response.data)
        self.assertNotIn("statutory_deadline", response.data)

        self.create_request(patient_id="bob")
        listed = self.client.get("/api/v1/dsrs/my-requests/")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.data), 1)

    def test_patient_case_detail_is_limited_to_their_own_requests(self) -> None:
        own_request = self.create_request(patient_id="alice")
        other_request = self.create_request(patient_id="bob")
        self.client.credentials(HTTP_AUTHORIZATION="Bearer patient:alice")

        own_detail = self.client.get(f"/api/v1/dsrs/{own_request.pk}/")
        other_detail = self.client.get(f"/api/v1/dsrs/{other_request.pk}/")

        self.assertEqual(own_detail.status_code, 200)
        self.assertTrue(own_detail.data["can_withdraw"])
        self.assertEqual(other_detail.status_code, 404)

    def test_role_permissions_reject_cross_role_access(self) -> None:
        self.client.credentials(HTTP_AUTHORIZATION="Bearer patient:alice")
        self.assertEqual(self.client.get("/api/v1/dpo/dsrs/").status_code, 403)

        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:staff-1")
        self.assertEqual(
            self.client.get("/api/v1/dsrs/my-requests/").status_code, 403
        )

    def test_dpo_case_detail_returns_available_actions_and_combined_activity(self) -> None:
        request = self.create_request()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:staff-1")

        detail = self.client.get(f"/api/v1/dpo/dsrs/{request.pk}/")

        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["request"]["id"], request.pk)
        self.assertIn(
            {
                "label": "Start departmental search",
                "action": "start_departmental_search",
            },
            detail.data["available_actions"],
        )
        self.assertEqual(detail.data["activity"][0]["kind"], "transition")
        self.assertIn("Submitted", detail.data["activity"][0]["summary"])

        assigned = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/assignment/",
            {"assigned_to": "reviewer-2"},
            format="json",
        )
        self.assertEqual(assigned.status_code, 200)
        detail = self.client.get(f"/api/v1/dpo/dsrs/{request.pk}/")
        self.assertEqual(detail.data["activity"][0]["kind"], "case")
        self.assertIn("reviewer-2", detail.data["activity"][0]["summary"])

    def test_transition_requires_reason_for_rejection_and_audits_changes(self) -> None:
        request = self.create_request()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:staff-1")

        missing_reason = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {"action": "reject"},
            format="json",
        )
        self.assertEqual(missing_reason.status_code, 400)

        rejected = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {"action": "reject", "reason": "Synthetic test reason"},
            format="json",
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(DSRAuditLog.objects.filter(dsr=request).count(), 2)
        self.assertEqual(
            DSRAuditLog.objects.filter(dsr=request).last().actor_id, "staff-1"
        )

    def test_unavailable_transition_returns_conflict_without_changing_state(self) -> None:
        request = self.create_request()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:staff-1")
        response = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {
                "action": "approve_and_close",
                "reason": "Not ready",
                "resolution_status": DSRRequest.ResolutionStatus.FULFILLED,
            },
            format="json",
        )
        request = DSRRequest.objects.get(pk=request.pk)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(request.state, DSRRequest.State.ACCEPTED)

    def test_dpo_can_advance_department_search_to_privacy_review(self) -> None:
        request = self.create_request(state=DSRRequest.State.DEPT_SEARCH)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:reviewer")

        response = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {"action": "send_to_privacy_review", "reason": "Search complete"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        request = DSRRequest.objects.get(pk=request.pk)
        self.assertEqual(request.state, DSRRequest.State.PRIVACY_REVIEW)

    def test_source_search_workbench_gates_decisions_and_response_preparation(self) -> None:
        request = self.create_request()
        request.start_departmental_search(actor_id="reviewer", reason="Start mock search")
        request.save(update_fields=("state", "updated_at"))
        request.send_to_privacy_review(actor_id="reviewer", reason="Open privacy review")
        request.save(update_fields=("state", "updated_at"))
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:reviewer")

        draft = self.client.get(
            f"/api/v1/dpo/dsrs/{request.pk}/response-draft/"
        )
        self.assertEqual(draft.status_code, 200)
        self.assertFalse(draft.data["searches_complete"])
        self.assertIn("not been sent", draft.data["draft_text"])
        self.assertNotIn("clinical data", draft.data["draft_text"].lower())
        self.assertIn("No decision recorded", draft.data["draft_text"])
        self.assertIn("No patient updates recorded", draft.data["draft_text"])

        final_decision = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/decisions/",
            {
                "category": "overall",
                "outcome": DSRDecision.Outcome.FULFILLED,
                "rationale": "Synthetic final decision.",
            },
            format="json",
        )
        self.assertEqual(final_decision.status_code, 409)
        further_review = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/decisions/",
            {
                "category": "overall",
                "outcome": DSRDecision.Outcome.FURTHER_REVIEW,
                "rationale": "Search remains incomplete.",
            },
            format="json",
        )
        self.assertEqual(further_review.status_code, 201)
        patient_update = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/communications/",
            {"message": "Your synthetic request is being reviewed."},
            format="json",
        )
        self.assertEqual(patient_update.status_code, 201)
        blocked_transition = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {"action": "approve_privacy_review"},
            format="json",
        )
        self.assertEqual(blocked_transition.status_code, 409)

        create = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/source-searches/",
            {
                "source_system": DSRSourceSearch.SourceSystem.CIMS,
                "status": DSRSourceSearch.Status.PENDING,
            },
            format="json",
        )
        self.assertEqual(create.status_code, 201, create.data)
        search_id = create.data["id"]
        duplicate = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/source-searches/",
            {"source_system": DSRSourceSearch.SourceSystem.CIMS},
            format="json",
        )
        self.assertEqual(duplicate.status_code, 409)
        invalid_completion = self.client.patch(
            f"/api/v1/dpo/dsrs/{request.pk}/source-searches/{search_id}/",
            {"status": DSRSourceSearch.Status.COMPLETE},
            format="json",
        )
        self.assertEqual(invalid_completion.status_code, 400)
        blocked = self.client.patch(
            f"/api/v1/dpo/dsrs/{request.pk}/source-searches/{search_id}/",
            {
                "status": DSRSourceSearch.Status.BLOCKED,
                "summary": "Synthetic access is not available.",
                "evidence_reference": "MOCK-REF-001",
            },
            format="json",
        )
        self.assertEqual(blocked.status_code, 200)
        still_blocked = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {"action": "approve_privacy_review"},
            format="json",
        )
        self.assertEqual(still_blocked.status_code, 409)
        complete = self.client.patch(
            f"/api/v1/dpo/dsrs/{request.pk}/source-searches/{search_id}/",
            {
                "status": DSRSourceSearch.Status.COMPLETE,
                "summary": "Synthetic mock review complete; no source records copied.",
            },
            format="json",
        )
        self.assertEqual(complete.status_code, 200)
        decision = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/decisions/",
            {
                "category": "overall",
                "outcome": DSRDecision.Outcome.FULFILLED,
                "rationale": "Synthetic rationale after documented searches.",
            },
            format="json",
        )
        self.assertEqual(decision.status_code, 201)
        approved = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {"action": "approve_privacy_review"},
            format="json",
        )
        self.assertEqual(approved.status_code, 200)
        final_draft = self.client.get(
            f"/api/v1/dpo/dsrs/{request.pk}/response-draft/"
        )
        self.assertTrue(final_draft.data["searches_complete"])
        self.assertIn("MOCK-REF-001", str(final_draft.data["source_searches"]))
        self.assertIn("Synthetic rationale after documented searches.", final_draft.data["draft_text"])
        self.assertIn("Your synthetic request is being reviewed.", final_draft.data["draft_text"])
        self.assertTrue(
            DSRCaseEvent.objects.filter(
                dsr=request,
                event_type=DSRCaseEvent.EventType.SOURCE_SEARCH_RECORDED,
            ).exists()
        )

    def test_response_finalization_requires_readiness_and_versions_are_immutable(self) -> None:
        request = self.create_request(state=DSRRequest.State.DEPT_SEARCH)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:staff-1")
        endpoint = f"/api/v1/dpo/dsrs/{request.pk}/response-versions/"

        incomplete = self.client.post(
            endpoint, {"content": "Not ready"}, format="json"
        )
        self.assertEqual(incomplete.status_code, 409)

        self.create_completed_search(request)
        request.send_to_privacy_review(
            actor_id="reviewer", reason="Ready for response preparation"
        )
        request.save(update_fields=("state", "updated_at"))
        request.approve_privacy_review(
            actor_id="reviewer", reason="Review completed"
        )
        request.save(update_fields=("state", "updated_at"))

        no_decision = self.client.post(
            endpoint, {"content": "No decision recorded."}, format="json"
        )
        self.assertEqual(no_decision.status_code, 409)
        DSRDecision.objects.create(
            dsr=request,
            category="overall",
            outcome=DSRDecision.Outcome.FURTHER_REVIEW,
            rationale="Synthetic further review required.",
            recorded_by="reviewer",
        )
        further_review = self.client.post(
            endpoint, {"content": "Further review remains."}, format="json"
        )
        self.assertEqual(further_review.status_code, 409)
        DSRDecision.objects.create(
            dsr=request,
            category="overall",
            outcome=DSRDecision.Outcome.FULFILLED,
            rationale="Synthetic final decision.",
            recorded_by="reviewer",
        )

        first = self.client.post(
            endpoint, {"content": "Final response, version one."}, format="json"
        )
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.data["version"], 1)
        self.assertEqual(first.data["finalized_by"], "staff-1")
        self.assertEqual(first.data["case_snapshot"]["case_id"], request.pk)

        second = self.client.post(
            endpoint, {"content": "Revised final response."}, format="json"
        )
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(second.data["version"], 2)
        self.assertEqual(
            list(request.response_versions.values_list("version", flat=True)),
            [2, 1],
        )

        version = request.response_versions.get(version=1)
        version.content = "Attempted overwrite"
        with self.assertRaises(ValueError):
            version.save()
        self.assertEqual(
            request.response_versions.get(version=1).content,
            "Final response, version one.",
        )
        search = request.source_searches.get()
        search.status = DSRSourceSearch.Status.BLOCKED
        search.summary = "Synthetic blocker."
        search.save(update_fields=("status", "summary", "updated_at"))
        incomplete_after_preparation = self.client.post(
            endpoint, {"content": "Search no longer complete."}, format="json"
        )
        self.assertEqual(incomplete_after_preparation.status_code, 409)

    def test_only_published_response_is_visible_to_owning_patient(self) -> None:
        request = self.create_request(state=DSRRequest.State.DEPT_SEARCH)
        self.prepare_response(request)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:staff-1")
        versions_url = f"/api/v1/dpo/dsrs/{request.pk}/response-versions/"
        finalized = self.client.post(
            versions_url, {"content": "Patient-facing final answer."}, format="json"
        )
        self.assertEqual(finalized.status_code, 201)

        self.client.credentials(HTTP_AUTHORIZATION="Bearer patient:alice")
        patient_url = f"/api/v1/dsrs/{request.pk}/responses/"
        self.assertEqual(self.client.get(patient_url).data, [])
        self.assertEqual(
            self.client.get(
                f"/api/v1/dsrs/{request.pk + 999}/responses/"
            ).status_code,
            404,
        )

        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:staff-1")
        published = self.client.post(
            f"{versions_url}{finalized.data['id']}/publish/", format="json"
        )
        self.assertEqual(published.status_code, 201, published.data)
        self.assertEqual(published.data["publication"]["published_by"], "staff-1")
        self.assertTrue(
            request.events.filter(
                event_type=DSRCaseEvent.EventType.RESPONSE_PUBLISHED
            ).exists()
        )

        self.client.credentials(HTTP_AUTHORIZATION="Bearer patient:alice")
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + "dpo:" + "staff-1")
        unpublished = self.client.post(
            versions_url,
            {"content": "This version is still private."},
            format="json",
        )
        self.assertEqual(unpublished.status_code, 201)
        self.client.credentials(
            HTTP_AUTHORIZATION="Bearer " + "patient:" + "alice"
        )
        patient_responses = self.client.get(patient_url)
        self.assertEqual(patient_responses.status_code, 200)
        self.assertEqual(len(patient_responses.data), 1)
        self.assertEqual(
            patient_responses.data[0]["content"], "Patient-facing final answer."
        )
        self.assertNotIn("case_snapshot", patient_responses.data[0])
        self.assertNotIn("finalized_by", patient_responses.data[0])
        self.assertNotIn(
            "This version is still private.", str(patient_responses.data)
        )
        self.client.post(
            "/demo/login/", {"role": "patient", "principal_id": "alice"}
        )
        patient_page = self.client.get("/patient/")
        self.assertContains(patient_page, "Patient-facing final answer.")
        self.assertNotContains(patient_page, "This version is still private.")

    def test_dpo_can_resume_escalated_case_with_audited_reason(self) -> None:
        request = self.create_request()
        request.escalate(actor_id="system", reason="Needs DPO attention")
        request.save(update_fields=("state", "escalated_from", "updated_at"))
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:reviewer")

        missing_reason = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {"action": "resume_escalated"},
            format="json",
        )
        self.assertEqual(missing_reason.status_code, 400)

        resumed = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {"action": "resume_escalated", "reason": "Assigned to reviewer"},
            format="json",
        )
        self.assertEqual(resumed.status_code, 200)
        request = DSRRequest.objects.get(pk=request.pk)
        self.assertEqual(request.state, DSRRequest.State.ACCEPTED)
        self.assertEqual(request.audit_logs.last().reason, "Assigned to reviewer")

        waiting = self.create_request(patient_id="waiting")
        waiting.request_information(
            actor_id="reviewer", reason="Clarify the requested records"
        )
        waiting.save(update_fields=("state", "updated_at"))
        waiting.escalate(actor_id="sla-monitor", reason="Response target exceeded")
        waiting.save(update_fields=("state", "escalated_from", "updated_at"))
        resumed_waiting = self.client.post(
            f"/api/v1/dpo/dsrs/{waiting.pk}/transition/",
            {"action": "resume_escalated", "reason": "Review restarted"},
            format="json",
        )
        self.assertEqual(resumed_waiting.status_code, 200)
        waiting = DSRRequest.objects.get(pk=waiting.pk)
        self.assertEqual(waiting.state, DSRRequest.State.NEEDS_INFORMATION)

    def test_patient_can_withdraw_only_their_own_active_request(self) -> None:
        request = self.create_request()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer patient:bob")

        other_subject = self.client.post(f"/api/v1/dsrs/{request.pk}/withdraw/")
        self.assertEqual(other_subject.status_code, 404)

        self.client.credentials(HTTP_AUTHORIZATION="Bearer patient:alice")
        response = self.client.post(
            f"/api/v1/dsrs/{request.pk}/withdraw/",
            {"reason": "No longer needed"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        withdrawn = DSRRequest.objects.get(pk=request.pk)
        self.assertEqual(withdrawn.state, DSRRequest.State.WITHDRAWN)
        self.assertEqual(
            withdrawn.resolution_status, DSRRequest.ResolutionStatus.WITHDRAWN
        )
        self.assertEqual(withdrawn.audit_logs.last().reason, "No longer needed")
        self.assertEqual(
            self.client.post(f"/api/v1/dsrs/{request.pk}/withdraw/").status_code,
            409,
        )

    def test_information_request_and_patient_response_resume_case(self) -> None:
        request = self.create_request()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:reviewer")
        asked = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {"action": "request_information", "reason": "Clarify request scope"},
            format="json",
        )
        self.assertEqual(asked.status_code, 200)
        request = DSRRequest.objects.get(pk=request.pk)
        self.assertEqual(request.state, DSRRequest.State.NEEDS_INFORMATION)

        self.client.credentials(HTTP_AUTHORIZATION="Bearer patient:alice")
        response = self.client.post(
            f"/api/v1/dsrs/{request.pk}/communications/",
            {"message": "Please include the fictional 2025 visit."},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        request = DSRRequest.objects.get(pk=request.pk)
        self.assertEqual(request.state, DSRRequest.State.ACCEPTED)
        self.assertEqual(
            request.communications.get().direction,
            DSRCommunication.Direction.PATIENT_TO_DPO,
        )
        self.assertEqual(request.audit_logs.last().actor_id, "alice")
        self.assertEqual(
            self.client.post(
                f"/api/v1/dsrs/{request.pk}/communications/",
                {"message": "Unrequested follow-up"},
                format="json",
            ).status_code,
            409,
        )

    def test_dpo_queue_filters_and_csv_export_are_scoped_and_audited(self) -> None:
        overdue = self.create_request(
            patient_id="=mock-formula",
            due_at=timezone.now() - timedelta(days=1),
        )
        unassigned = self.create_request(patient_id="unassigned")
        unassigned.assigned_to = "reviewer-2"
        unassigned.save(update_fields=("assigned_to",))
        escalated = self.create_request(patient_id="escalated")
        escalated.escalate(actor_id="system", reason="Synthetic escalation")
        escalated.save(update_fields=("state", "escalated_from", "updated_at"))
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:reviewer")

        filtered = self.client.get("/api/v1/dpo/dsrs/?queue=overdue")
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual([item["id"] for item in filtered.data["requests"]], [overdue.pk])
        self.assertEqual(filtered.data["filters"]["queue_filter"], "overdue")
        self.assertEqual(
            self.client.get("/api/v1/dpo/dsrs/?queue=escalated").data["requests"][0]["id"],
            escalated.pk,
        )
        self.assertEqual(
            self.client.get("/api/v1/dpo/dsrs/?queue=unassigned").status_code,
            200,
        )

        export = self.client.get("/api/v1/dpo/dsrs/export/?queue=overdue")
        self.assertEqual(export.status_code, 200)
        self.assertIn("text/csv", export["Content-Type"])
        self.assertIn(b"'=mock-formula", export.content)
        self.assertTrue(
            overdue.events.filter(
                event_type=DSRCaseEvent.EventType.CASES_EXPORTED
            ).exists()
        )

        self.client.credentials(HTTP_AUTHORIZATION="Bearer patient:bob")
        self.assertEqual(
            self.client.get("/api/v1/dpo/dsrs/export/").status_code, 403
        )

    def test_demo_patient_and_dpo_pages_are_role_scoped(self) -> None:
        request = self.create_request()
        self.client.post(
            "/demo/login/",
            {"role": "patient", "principal_id": "alice"},
        )
        self.assertEqual(self.client.get("/patient/").status_code, 200)
        self.assertRedirects(self.client.get("/dpo/"), "/demo/login/")

        self.client.post(
            "/demo/login/",
            {"role": "dpo", "principal_id": "reviewer"},
        )
        queue_export = self.client.get("/dpo/export/")
        self.assertEqual(queue_export.status_code, 200)
        self.assertIn("text/csv", queue_export["Content-Type"])
        detail = self.client.get(f"/dpo/requests/{request.pk}/")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Workflow audit trail")
        self.assertContains(detail, "Mock source-search checklist")
        search_response = self.client.post(
            f"/dpo/requests/{request.pk}/",
            {
                "operation": "source_search_create",
                "source_system": DSRSourceSearch.SourceSystem.ERPS,
                "status": DSRSourceSearch.Status.COMPLETE,
                "summary": "Synthetic finance search completed without reading records.",
                "evidence_reference": "MOCK-ERPS-CASE-1",
            },
        )
        self.assertEqual(search_response.status_code, 302)
        self.assertTrue(request.source_searches.filter(source_system="ERPS").exists())
        response = self.client.post(
            f"/dpo/requests/{request.pk}/",
            {"action": "start_departmental_search", "reason": "Synthetic search"},
        )
        self.assertEqual(response.status_code, 302)
        request = DSRRequest.objects.get(pk=request.pk)
        self.assertEqual(request.state, DSRRequest.State.DEPT_SEARCH)

    def test_browser_dpo_can_finalize_publish_and_close_response(self) -> None:
        request = self.create_request(state=DSRRequest.State.DEPT_SEARCH)
        self.prepare_response(request)
        self.client.post(
            "/demo/login/",
            {"role": "dpo", "principal_id": "reviewer"},
        )

        detail_url = f"/dpo/requests/{request.pk}/"
        page = self.client.get(detail_url)
        self.assertContains(page, "Finalize new version")
        self.assertContains(page, "Ready for final response review")

        finalized = self.client.post(
            detail_url,
            {
                "operation": "response_finalize",
                "content": "Final response prepared in the browser.",
            },
        )
        self.assertEqual(finalized.status_code, 302)
        version = request.response_versions.get()
        published = self.client.post(
            detail_url,
            {"operation": "response_publish", "version_id": version.pk},
        )
        self.assertEqual(published.status_code, 302)
        self.assertContains(self.client.get(detail_url), "Published")

        closed = self.client.post(
            detail_url,
            {
                "action": "approve_and_close",
                "reason": "Published response is ready.",
                "resolution_status": DSRRequest.ResolutionStatus.FULFILLED,
            },
        )
        self.assertEqual(closed.status_code, 302)
        self.assertEqual(
            DSRRequest.objects.get(pk=request.pk).state,
            DSRRequest.State.CLOSED,
        )

    def test_patient_browser_can_answer_a_question_and_withdraw_a_case(self) -> None:
        waiting = self.create_request()
        waiting.request_information(actor_id="reviewer", reason="Clarify scope")
        waiting.save(update_fields=("state", "updated_at"))
        withdrawable = self.create_request(patient_id="alice")
        self.client.post(
            "/demo/login/",
            {"role": "patient", "principal_id": "alice"},
        )

        portal = self.client.get("/patient/")
        self.assertContains(portal, "Provide the requested information")
        answer = self.client.post(
            "/patient/",
            {
                "operation": "patient_response",
                "request_id": waiting.pk,
                "message": "Please include the synthetic visit.",
            },
        )
        self.assertEqual(answer.status_code, 302)
        waiting = DSRRequest.objects.get(pk=waiting.pk)
        self.assertEqual(waiting.state, DSRRequest.State.ACCEPTED)
        self.assertEqual(
            waiting.communications.get().direction,
            DSRCommunication.Direction.PATIENT_TO_DPO,
        )

        withdrawal = self.client.post(
            "/patient/",
            {
                "operation": "withdraw",
                "request_id": withdrawable.pk,
                "reason": "No longer needed",
            },
        )
        self.assertEqual(withdrawal.status_code, 302)
        withdrawable = DSRRequest.objects.get(pk=withdrawable.pk)
        self.assertEqual(withdrawable.state, DSRRequest.State.WITHDRAWN)

    def test_dpo_can_assign_add_tasks_record_decisions_and_updates(self) -> None:
        request = self.create_request()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:staff-1")

        assigned = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/assignment/",
            {"assigned_to": "reviewer-2"},
            format="json",
        )
        self.assertEqual(assigned.status_code, 200)
        self.assertEqual(assigned.data["assigned_to"], "reviewer-2")

        created_task = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/tasks/",
            {"title": "Review mock source results", "details": "Synthetic test only"},
            format="json",
        )
        self.assertEqual(created_task.status_code, 201)
        task_id = created_task.data["id"]
        updated_task = self.client.patch(
            f"/api/v1/dpo/dsrs/{request.pk}/tasks/{task_id}/",
            {"status": DSRReviewTask.Status.COMPLETED},
            format="json",
        )
        self.assertEqual(updated_task.status_code, 200)
        self.assertIsNotNone(updated_task.data["completed_at"])

        decision = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/decisions/",
            {
                "category": "mock-erasure-review",
                "outcome": DSRDecision.Outcome.FURTHER_REVIEW,
                "rationale": "Synthetic case requires additional policy review.",
            },
            format="json",
        )
        self.assertEqual(decision.status_code, 201)

        communication = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/communications/",
            {"message": "Your synthetic request is under review."},
            format="json",
        )
        self.assertEqual(communication.status_code, 201)
        self.assertTrue(
            DSRCaseEvent.objects.filter(
                dsr=request,
                event_type=DSRCaseEvent.EventType.PATIENT_UPDATE_RECORDED,
            ).exists()
        )

        self.client.credentials(HTTP_AUTHORIZATION="Bearer patient:alice")
        patient_updates = self.client.get(
            f"/api/v1/dsrs/{request.pk}/communications/"
        )
        self.assertEqual(patient_updates.status_code, 200)
        self.assertEqual(len(patient_updates.data), 1)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer patient:bob")
        self.assertEqual(
            self.client.get(
                f"/api/v1/dsrs/{request.pk}/communications/"
            ).status_code,
            404,
        )

    def test_close_requires_resolution_and_records_final_decision(self) -> None:
        request = self.create_request(state=DSRRequest.State.DEPT_SEARCH)
        self.prepare_response(request)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:reviewer")
        cannot_close_yet = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {
                "action": "approve_and_close",
                "reason": "Response has not been published.",
                "resolution_status": DSRRequest.ResolutionStatus.PARTIALLY_FULFILLED,
            },
            format="json",
        )
        self.assertEqual(cannot_close_yet.status_code, 409)
        versions_url = f"/api/v1/dpo/dsrs/{request.pk}/response-versions/"
        finalized = self.client.post(
            versions_url, {"content": "Synthetic final response."}, format="json"
        )
        self.assertEqual(finalized.status_code, 201)
        published = self.client.post(
            f"{versions_url}{finalized.data['id']}/publish/", format="json"
        )
        self.assertEqual(published.status_code, 201)
        response = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {
                "action": "approve_and_close",
                "reason": "Synthetic resolution documented.",
                "resolution_status": DSRRequest.ResolutionStatus.PARTIALLY_FULFILLED,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        closed_request = DSRRequest.objects.get(pk=request.pk)
        self.assertEqual(closed_request.state, DSRRequest.State.CLOSED)
        self.assertEqual(
            closed_request.resolution_status,
            DSRRequest.ResolutionStatus.PARTIALLY_FULFILLED,
        )
        self.assertIsNotNone(closed_request.closed_at)
        self.assertEqual(
            closed_request.decisions.filter(category="overall").last().recorded_by,
            "reviewer",
        )
        self.assertEqual(
            DSRResponsePublication.objects.filter(
                response_version__dsr=request
            ).count(),
            1,
        )


class DSRDeadlineTests(TestCase):
    @override_settings(
        DSR_LONG_RESPONSE_TARGET_DAYS=5,
        DSR_DEFAULT_RESPONSE_TARGET_DAYS=3,
    )
    def test_new_request_uses_configured_demo_target(self) -> None:
        before = timezone.now()
        request = DSRRequest.objects.create(
            patient_id="alice",
            request_type=DSRRequest.RequestType.ACCESS,
            description="Synthetic request",
            response_due_at=None,
        )
        self.assertAlmostEqual(
            (request.response_due_at - before).total_seconds(),
            timedelta(days=5).total_seconds(),
            delta=2,
        )

    def test_overdue_response_preparation_request_can_be_escalated(self) -> None:
        request = DSRRequest.objects.create(
            patient_id="alice",
            request_type=DSRRequest.RequestType.ACCESS,
            description="Synthetic request",
            response_due_at=timezone.now() - timedelta(days=1),
        )
        request.accept_submission(actor_id="alice", reason="test intake")
        request.save(update_fields=("state", "updated_at"))
        request.start_departmental_search(actor_id="staff-1", reason="test")
        request.save(update_fields=("state", "updated_at"))
        request.send_to_privacy_review(actor_id="worker", reason="test")
        request.save(update_fields=("state", "updated_at"))
        DSRSourceSearch.objects.create(
            dsr=request,
            source_system=DSRSourceSearch.SourceSystem.CIMS,
            status=DSRSourceSearch.Status.COMPLETE,
            summary="Synthetic mock search complete.",
            created_by="staff-1",
            updated_by="staff-1",
        )
        request.approve_privacy_review(actor_id="staff-1", reason="test")
        request.save(update_fields=("state", "updated_at"))

        self.assertEqual(request.state, DSRRequest.State.RESPONSE_PREP)
        self.assertEqual(monitor_sla_deadlines(), 1)
        request = DSRRequest.objects.get(pk=request.pk)
        self.assertEqual(request.state, DSRRequest.State.ESCALATED)
        self.assertEqual(request.audit_logs.last().actor_id, "sla-monitor")
        self.assertEqual(monitor_sla_deadlines(), 0)
