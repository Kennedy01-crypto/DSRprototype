from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from dsr.models import (
    DSRCaseEvent,
    DSRCommunication,
    DSRDataQuarantine,
    DSRDecision,
    DSRAuditLog,
    DSRDeadlinePolicy,
    DSRDeadlinePolicyAudit,
    DSRLegalReviewDecision,
    DSRLegalReviewRequest,
    DSRRequest,
    DSRResponsePublication,
    DSRReviewTask,
    DSRSourceSearch,
)
from dsr.tasks import monitor_sla_deadlines, trigger_fides_data_collection


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
        self.assertEqual(response.data["state"], "Submitted")
        self.assertEqual(response.data["request_type_label"], "Access / Copy")
        self.assertIsNone(response.data["legal_deadline_at"])
        self.assertNotIn("sub_status", response.data)

        self.create_request(patient_id="bob")
        listed = self.client.get("/api/v1/dsrs/my-requests/")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.data), 1)

    def test_dpo_deadline_policy_updates_are_audited_and_apply_to_new_requests(self) -> None:
        existing = self.create_request(due_at=timezone.now() + timedelta(days=10))
        original_target = existing.response_due_at
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:staff-1")

        missing_reason = self.client.patch(
            "/api/v1/dpo/policies/",
            {
                "request_type": DSRRequest.RequestType.ACCESS,
                "internal_target_days": 7,
                "legal_deadline_days": 21,
            },
            format="json",
        )
        self.assertEqual(missing_reason.status_code, 400)
        self.assertFalse(DSRDeadlinePolicyAudit.objects.exists())

        updated = self.client.patch(
            "/api/v1/dpo/policies/",
            {
                "request_type": DSRRequest.RequestType.ACCESS,
                "internal_target_days": 7,
                "legal_deadline_days": 21,
                "reason": "Approved demonstration policy update.",
            },
            format="json",
        )

        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data["internal_target_days"], 7)
        self.assertEqual(updated.data["legal_deadline_days"], 21)
        audit = DSRDeadlinePolicyAudit.objects.get(
            policy__request_type=DSRRequest.RequestType.ACCESS
        )
        self.assertEqual(audit.actor_id, "staff-1")
        self.assertEqual(audit.reason, "Approved demonstration policy update.")
        self.assertEqual(
            audit.changed_values["legal_deadline_days"],
            {"from": None, "to": 21},
        )

        existing = DSRRequest.objects.get(pk=existing.pk)
        self.assertEqual(existing.response_due_at, original_target)
        self.assertIsNone(existing.legal_deadline_at)

        self.client.credentials(HTTP_AUTHORIZATION="Bearer patient:alice")
        created = self.client.post(
            "/api/v1/dsrs/submit/",
            {
                "request_type": DSRRequest.RequestType.ACCESS,
                "description": "A new synthetic request.",
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        created_request = DSRRequest.objects.get(pk=created.data["id"])
        self.assertAlmostEqual(
            (created_request.response_due_at - timezone.now()).total_seconds(),
            timedelta(days=7).total_seconds(),
            delta=3,
        )
        self.assertAlmostEqual(
            (created_request.legal_deadline_at - timezone.now()).total_seconds(),
            timedelta(days=21).total_seconds(),
            delta=3,
        )

    def test_admin_cannot_access_case_or_deadline_policy_apis(self) -> None:
        self.client.credentials(HTTP_AUTHORIZATION="Bearer admin:admin-1")
        self.assertEqual(self.client.get("/api/v1/dpo/dsrs/").status_code, 403)
        self.assertEqual(
            self.client.get("/api/v1/dpo/policies/").status_code, 403
        )
        self.assertEqual(
            self.client.get("/api/v1/dsrs/my-requests/").status_code, 403
        )

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
        self.assertEqual(detail.data["request"]["state"], "In Progress")
        self.assertEqual(detail.data["request"]["sub_status"], "Departmental Search")

    def test_duplicate_closure_requires_a_matching_different_case(self) -> None:
        request = self.create_request(patient_id="alice")
        original = self.create_request(patient_id="alice")
        other_patient = self.create_request(patient_id="bob")
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:staff-1")

        self_duplicate = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {
                "action": "mark_for_duplicate",
                "reason": "This request repeats an existing case.",
                "duplicate_of": request.pk,
            },
            format="json",
        )
        self.assertEqual(self_duplicate.status_code, 409)
        wrong_owner = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {
                "action": "mark_for_duplicate",
                "reason": "This request repeats an existing case.",
                "duplicate_of": other_patient.pk,
            },
            format="json",
        )
        self.assertEqual(wrong_owner.status_code, 409)

        marked = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {
                "action": "mark_for_duplicate",
                "reason": "This request repeats an existing case.",
                "duplicate_of": original.pk,
            },
            format="json",
        )
        self.assertEqual(marked.status_code, 200, marked.data)
        self.assertEqual(marked.data["sub_status"], "Response Preparation")
        duplicate_decision = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/decisions/",
            {
                "category": "overall",
                "outcome": DSRRequest.ResolutionStatus.DUPLICATE,
                "rationale": "Duplicate of the existing synthetic case.",
            },
            format="json",
        )
        self.assertEqual(duplicate_decision.status_code, 201)
        versions_url = f"/api/v1/dpo/dsrs/{request.pk}/response-versions/"
        version = self.client.post(
            versions_url,
            {"content": f"This request duplicates DSR-{original.pk}."},
            format="json",
        )
        self.assertEqual(version.status_code, 201, version.data)
        published = self.client.post(
            f"{versions_url}{version.data['id']}/publish/",
            format="json",
        )
        self.assertEqual(published.status_code, 201, published.data)

        closed = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {
                "action": "close_as_duplicate",
                "reason": "Duplicate outcome published.",
                "duplicate_of": original.pk,
            },
            format="json",
        )
        self.assertEqual(closed.status_code, 200, closed.data)
        self.assertEqual(closed.data["state"], "Completed")
        self.assertEqual(closed.data["resolution_status"], "Duplicate")
        self.assertEqual(closed.data["duplicate_of"], original.pk)

    def test_refusal_requires_reason_legal_review_and_published_response(self) -> None:
        request = self.create_request(state=DSRRequest.State.DEPT_SEARCH)
        self.prepare_response(request)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:staff-1")

        unsupported_direct_rejection = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {"action": "reject"},
            format="json",
        )
        self.assertEqual(unsupported_direct_rejection.status_code, 400)

        missing_reason = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {
                "action": "approve_and_close",
                "resolution_status": DSRRequest.ResolutionStatus.REJECTED,
            },
            format="json",
        )
        self.assertEqual(missing_reason.status_code, 400)

        refusal_decision = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/decisions/",
            {
                "category": "overall",
                "outcome": DSRRequest.ResolutionStatus.REJECTED,
                "rationale": "Synthetic refusal decision.",
            },
            format="json",
        )
        self.assertEqual(refusal_decision.status_code, 201)
        versions_url = f"/api/v1/dpo/dsrs/{request.pk}/response-versions/"
        finalized = self.client.post(
            versions_url, {"content": "Synthetic refusal response."}, format="json"
        )
        self.assertEqual(finalized.status_code, 201)

        blocked = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {
                "action": "approve_and_close",
                "reason": "Documented refusal rationale.",
                "resolution_status": DSRRequest.ResolutionStatus.REJECTED,
            },
            format="json",
        )
        self.assertEqual(blocked.status_code, 409)

        legal_review = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {
                "action": "request_legal_review",
                "reason": "Review the refusal.",
                "proposed_outcome": DSRRequest.ResolutionStatus.REJECTED,
            },
            format="json",
        )
        self.assertEqual(legal_review.status_code, 200, legal_review.data)
        self.assertEqual(legal_review.data["state"], "In Progress")
        self.assertEqual(legal_review.data["sub_status"], "Legal / Management Review")
        same_person_approval = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {
                "action": "approve_legal_review",
                "reason": "I reviewed my own work.",
            },
            format="json",
        )
        self.assertEqual(same_person_approval.status_code, 409)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:independent-reviewer")
        legal_approval = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {
                "action": "approve_legal_review",
                "reason": "Independent review completed.",
            },
            format="json",
        )
        self.assertEqual(legal_approval.status_code, 200, legal_approval.data)
        self.assertEqual(legal_approval.data["legal_review_completed"], True)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:staff-1")
        published = self.client.post(
            f"{versions_url}{finalized.data['id']}/publish/", format="json"
        )
        self.assertEqual(published.status_code, 201)
        rejected = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {
                "action": "approve_and_close",
                "reason": "Documented refusal rationale.",
                "resolution_status": DSRRequest.ResolutionStatus.REJECTED,
            },
            format="json",
        )
        self.assertEqual(rejected.status_code, 200, rejected.data)
        self.assertEqual(rejected.data["state"], "Completed")
        closed_request = DSRRequest.objects.get(pk=request.pk)
        self.assertEqual(
            closed_request.resolution_status,
            DSRRequest.ResolutionStatus.REJECTED,
        )
        self.assertEqual(
            DSRAuditLog.objects.filter(dsr=request).last().reason,
            "Documented refusal rationale.",
        )
        self.assertEqual(
            DSRAuditLog.objects.filter(dsr=request).last().actor_id, "staff-1"
        )

    def test_independent_review_is_separate_and_invalidated_by_case_changes(self) -> None:
        request = self.create_request(state=DSRRequest.State.DEPT_SEARCH)
        self.prepare_response(request)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:author")
        flagged = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {
                "action": "flag_for_independent_review",
                "reason": "The case includes a sensitive and complex request.",
            },
            format="json",
        )
        self.assertEqual(flagged.status_code, 200, flagged.data)
        self.assertTrue(flagged.data["independent_review_required"])

        versions_url = f"/api/v1/dpo/dsrs/{request.pk}/response-versions/"
        first_version = self.client.post(
            versions_url,
            {"content": "First synthetic response version."},
            format="json",
        )
        self.assertEqual(first_version.status_code, 201)
        review = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {
                "action": "request_legal_review",
                "reason": "Please independently review this response.",
                "proposed_outcome": DSRRequest.ResolutionStatus.FULFILLED,
            },
            format="json",
        )
        self.assertEqual(review.status_code, 200, review.data)
        detail = self.client.get(f"/api/v1/dpo/dsrs/{request.pk}/")
        self.assertNotIn(
            "approve_legal_review",
            [item["action"] for item in detail.data["available_actions"]],
        )

        changed_decision = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/decisions/",
            {
                "category": "overall",
                "outcome": DSRDecision.Outcome.FULFILLED,
                "rationale": "Updated synthetic rationale after review request.",
            },
            format="json",
        )
        self.assertEqual(changed_decision.status_code, 201)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:reviewer-1")
        stale_approval = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {
                "action": "approve_legal_review",
                "reason": "The case changed after review began.",
            },
            format="json",
        )
        self.assertEqual(stale_approval.status_code, 409)
        returned = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {
                "action": "return_from_legal_review",
                "reason": "Update the response to reflect the revised rationale.",
            },
            format="json",
        )
        self.assertEqual(returned.status_code, 200)

        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:author")
        second_version = self.client.post(
            versions_url,
            {"content": "Updated synthetic response version."},
            format="json",
        )
        self.assertEqual(second_version.status_code, 201)
        second_review = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {
                "action": "request_legal_review",
                "reason": "Review the updated response and evidence.",
                "proposed_outcome": DSRRequest.ResolutionStatus.FULFILLED,
            },
            format="json",
        )
        self.assertEqual(second_review.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:reviewer-2")
        approval = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {
                "action": "approve_legal_review",
                "reason": "Updated response and evidence independently reviewed.",
            },
            format="json",
        )
        self.assertEqual(approval.status_code, 200, approval.data)
        review_records = DSRLegalReviewRequest.objects.filter(dsr=request)
        self.assertEqual(review_records.count(), 2)
        self.assertEqual(
            DSRLegalReviewDecision.objects.filter(
                review_request=review_records.last()
            ).get().reviewer_id,
            "reviewer-2",
        )
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:author")
        published = self.client.post(
            f"{versions_url}{second_version.data['id']}/publish/",
            format="json",
        )
        self.assertEqual(published.status_code, 201, published.data)

    def test_reconsideration_must_reference_own_completed_same_type_case(self) -> None:
        completed = self.create_request(patient_id="alice")
        self.client.credentials(HTTP_AUTHORIZATION="Bearer patient:alice")
        withdrawn = self.client.post(
            f"/api/v1/dsrs/{completed.pk}/withdraw/",
            {"reason": "No longer needed."},
            format="json",
        )
        self.assertEqual(withdrawn.status_code, 200)

        self.client.credentials(HTTP_AUTHORIZATION="Bearer patient:bob")
        other_patient_link = self.client.post(
            "/api/v1/dsrs/submit/",
            {
                "request_type": DSRRequest.RequestType.ACCESS,
                "description": "Synthetic reconsideration.",
                "reconsideration_of": completed.pk,
            },
            format="json",
        )
        self.assertEqual(other_patient_link.status_code, 400)

        self.client.credentials(HTTP_AUTHORIZATION="Bearer patient:alice")
        wrong_type = self.client.post(
            "/api/v1/dsrs/submit/",
            {
                "request_type": DSRRequest.RequestType.ERASURE,
                "description": "Synthetic reconsideration.",
                "reconsideration_of": completed.pk,
            },
            format="json",
        )
        self.assertEqual(wrong_type.status_code, 400)
        reconsideration = self.client.post(
            "/api/v1/dsrs/submit/",
            {
                "request_type": DSRRequest.RequestType.ACCESS,
                "description": "Synthetic reconsideration.",
                "reconsideration_of": completed.pk,
            },
            format="json",
        )
        self.assertEqual(reconsideration.status_code, 201, reconsideration.data)
        self.assertEqual(reconsideration.data["reconsideration_of"], completed.pk)

    def test_patient_cannot_withdraw_after_a_response_was_published(self) -> None:
        request = self.create_request(state=DSRRequest.State.DEPT_SEARCH)
        self.prepare_response(request)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:staff-1")
        versions_url = f"/api/v1/dpo/dsrs/{request.pk}/response-versions/"
        version = self.client.post(
            versions_url,
            {"content": "Synthetic published response."},
            format="json",
        )
        self.assertEqual(version.status_code, 201)
        published = self.client.post(
            f"{versions_url}{version.data['id']}/publish/",
            format="json",
        )
        self.assertEqual(published.status_code, 201)

        self.client.credentials(HTTP_AUTHORIZATION="Bearer patient:alice")
        detail = self.client.get(f"/api/v1/dsrs/{request.pk}/")
        self.assertFalse(detail.data["can_withdraw"])
        withdrawal = self.client.post(
            f"/api/v1/dsrs/{request.pk}/withdraw/",
            {"reason": "Withdrawal after outcome publication."},
            format="json",
        )
        self.assertEqual(withdrawal.status_code, 409)
        self.assertEqual(
            DSRRequest.objects.get(pk=request.pk).state,
            DSRRequest.State.RESPONSE_PREP,
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

    def test_data_collection_task_ignores_duplicate_delivery_after_transition(self) -> None:
        request = self.create_request(state=DSRRequest.State.DEPT_SEARCH)

        trigger_fides_data_collection.run(request.pk)
        trigger_fides_data_collection.run(request.pk)

        request = DSRRequest.objects.get(pk=request.pk)
        self.assertEqual(request.state, DSRRequest.State.PRIVACY_REVIEW)
        self.assertEqual(DSRDataQuarantine.objects.filter(dsr=request).count(), 1)
        self.assertEqual(
            DSRAuditLog.objects.filter(
                dsr=request,
                from_state=DSRRequest.State.DEPT_SEARCH,
                to_state=DSRRequest.State.PRIVACY_REVIEW,
            ).count(),
            1,
        )

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
        dpo_message = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/communications/",
            {"message": "Please clarify the scope of this synthetic request."},
            format="json",
        )
        self.assertEqual(dpo_message.status_code, 201)
        asked = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {"action": "request_information", "reason": "Clarify request scope"},
            format="json",
        )
        self.assertEqual(asked.status_code, 200)
        request = DSRRequest.objects.get(pk=request.pk)
        self.assertEqual(request.state, DSRRequest.State.NEEDS_INFORMATION)

        self.client.credentials(HTTP_AUTHORIZATION="Bearer patient:alice")
        patient_thread = self.client.get(
            f"/api/v1/dsrs/{request.pk}/communications/"
        )
        self.assertEqual(patient_thread.status_code, 200)
        self.assertEqual(len(patient_thread.data), 1)
        self.assertEqual(
            patient_thread.data[0]["message"],
            "Please clarify the scope of this synthetic request.",
        )
        response = self.client.post(
            f"/api/v1/dsrs/{request.pk}/communications/",
            {"message": "Please include the fictional 2025 visit."},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        request = DSRRequest.objects.get(pk=request.pk)
        self.assertEqual(request.state, DSRRequest.State.ACCEPTED)
        self.assertEqual(
            request.communications.get(
                direction=DSRCommunication.Direction.PATIENT_TO_DPO
            ).direction,
            DSRCommunication.Direction.PATIENT_TO_DPO,
        )
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:staff-1")
        case_detail = self.client.get(f"/api/v1/dpo/dsrs/{request.pk}/")
        self.assertEqual(case_detail.status_code, 200)
        self.assertEqual(len(case_detail.data["communications"]), 2)
        self.assertEqual(
            {item["direction"] for item in case_detail.data["communications"]},
            {"DPO to patient", "Patient to DPO"},
        )
        self.assertEqual(
            {item["message"] for item in case_detail.data["communications"]},
            {
                "Please clarify the scope of this synthetic request.",
                "Please include the fictional 2025 visit.",
            },
        )
        self.assertEqual(request.audit_logs.last().actor_id, "alice")
        self.client.credentials(HTTP_AUTHORIZATION="Bearer patient:alice")
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
        due_soon = self.create_request(
            patient_id="due-soon",
            due_at=timezone.now() + timedelta(days=3),
        )
        legal_overdue = self.create_request(
            patient_id="legal-overdue",
            due_at=timezone.now() + timedelta(days=10),
        )
        legal_overdue.legal_deadline_at = timezone.now() - timedelta(days=1)
        legal_overdue.save(update_fields=("legal_deadline_at", "updated_at"))
        legal_due_soon = self.create_request(
            patient_id="legal-due-soon",
            due_at=timezone.now() + timedelta(days=10),
        )
        legal_due_soon.legal_deadline_at = timezone.now() + timedelta(days=3)
        legal_due_soon.save(update_fields=("legal_deadline_at", "updated_at"))
        unassigned = self.create_request(patient_id="unassigned")
        unassigned.assigned_to = "reviewer-2"
        unassigned.save(update_fields=("assigned_to",))
        escalated = self.create_request(patient_id="escalated")
        escalated.escalate(actor_id="system", reason="Synthetic escalation")
        escalated.save(update_fields=("state", "escalated_from", "updated_at"))
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:reviewer")

        filtered = self.client.get("/api/v1/dpo/dsrs/?queue=overdue")
        self.assertEqual(filtered.status_code, 200)
        self.assertCountEqual(
            [item["id"] for item in filtered.data["requests"]],
            [overdue.pk, legal_overdue.pk],
        )
        self.assertEqual(filtered.data["filters"]["queue_filter"], "overdue")
        due_soon_filtered = self.client.get("/api/v1/dpo/dsrs/?queue=due_soon")
        self.assertEqual(due_soon_filtered.status_code, 200)
        self.assertCountEqual(
            [item["id"] for item in due_soon_filtered.data["requests"]],
            [due_soon.pk, legal_due_soon.pk],
        )
        self.assertIsNotNone(
            next(
                item["legal_deadline_at"]
                for item in due_soon_filtered.data["requests"]
                if item["id"] == legal_due_soon.pk
            )
        )
        department_filtered = self.client.get(
            "/api/v1/dpo/dsrs/?department=records"
        )
        self.assertEqual(department_filtered.status_code, 200)
        self.assertIn(
            due_soon.pk,
            [item["id"] for item in department_filtered.data["requests"]],
        )
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
            waiting.communications.get(
                direction=DSRCommunication.Direction.PATIENT_TO_DPO
            ).direction,
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
        DSRDecision.objects.create(
            dsr=request,
            category="overall",
            outcome=DSRDecision.Outcome.PARTIALLY_FULFILLED,
            rationale="Synthetic partial resolution.",
            recorded_by="reviewer",
        )
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
        missing_legal_review = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {
                "action": "approve_and_close",
                "reason": "Synthetic resolution documented.",
                "resolution_status": DSRRequest.ResolutionStatus.PARTIALLY_FULFILLED,
            },
            format="json",
        )
        self.assertEqual(missing_legal_review.status_code, 409)
        request_legal_review = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {
                "action": "request_legal_review",
                "reason": "Review partial outcome.",
                "proposed_outcome": DSRRequest.ResolutionStatus.PARTIALLY_FULFILLED,
            },
            format="json",
        )
        self.assertEqual(request_legal_review.status_code, 200, request_legal_review.data)
        legal_review_approved = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {
                "action": "approve_legal_review",
                "reason": "Independent review completed.",
            },
            format="json",
        )
        self.assertEqual(legal_review_approved.status_code, 409)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:independent-reviewer")
        legal_review_approved = self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {
                "action": "approve_legal_review",
                "reason": "Independent review completed.",
            },
            format="json",
        )
        self.assertEqual(legal_review_approved.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:staff-1")
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
            "staff-1",
        )
        self.assertEqual(
            DSRResponsePublication.objects.filter(
                response_version__dsr=request
            ).count(),
            1,
        )


class DSRDeadlineTests(TestCase):
    def test_new_request_uses_policy_target_and_has_no_unconfigured_legal_deadline(self) -> None:
        policy = DSRDeadlinePolicy.objects.get(
            request_type=DSRRequest.RequestType.ACCESS
        )
        policy.internal_target_days = 5
        policy.legal_deadline_days = None
        policy.save()
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
        self.assertIsNone(request.legal_deadline_at)

    def test_overdue_deadline_is_audited_without_changing_workflow_state(self) -> None:
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
        self.assertEqual(request.state, DSRRequest.State.RESPONSE_PREP)
        deadline_event = request.events.get(
            event_type=DSRCaseEvent.EventType.DEADLINE_OVERDUE
        )
        self.assertEqual(deadline_event.actor_id, "sla-monitor")
        self.assertEqual(deadline_event.summary, "Internal target deadline passed")
        self.assertEqual(monitor_sla_deadlines(), 0)
