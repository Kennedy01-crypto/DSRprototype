from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from dsr.models import (
    DSRDecision,
    DSRLegalReviewRequest,
    DSRRequest,
    DSRSourceSearch,
)


class WorkflowTestMixin:
    client: APIClient

    def create_request(self, patient_id: str = "alice") -> DSRRequest:
        request = DSRRequest.objects.create(
            patient_id=patient_id,
            request_type=DSRRequest.RequestType.ACCESS,
            description="Synthetic test request",
            department="Records",
            response_due_at=timezone.now() + timedelta(days=10),
        )
        request.accept_submission(actor_id=patient_id, reason="test intake")
        request.save(update_fields=("state", "updated_at"))
        return request

    def advance_to(self, request: DSRRequest, target: str) -> DSRRequest:
        """Move a freshly accepted request forward to ``target``."""
        steps = [
            (DSRRequest.State.DEPT_SEARCH, "start_departmental_search"),
            (DSRRequest.State.PRIVACY_REVIEW, "send_to_privacy_review"),
            (DSRRequest.State.RESPONSE_PREP, "approve_privacy_review"),
        ]
        for state, action in steps:
            if request.state == target:
                break
            if action == "approve_privacy_review":
                DSRSourceSearch.objects.create(
                    dsr=request,
                    source_system=DSRSourceSearch.SourceSystem.CIMS,
                    status=DSRSourceSearch.Status.COMPLETE,
                    summary="Synthetic search complete.",
                    created_by="staff",
                    updated_by="staff",
                )
            getattr(request, action)(actor_id="staff", reason="test")
            request.save(update_fields=("state", "updated_at"))
            self.assertEqual(request.state, state)  # type: ignore[attr-defined]
        return request

    def transition(self, request: DSRRequest, payload: dict, token: str):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return self.client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/", payload, format="json"
        )


class IndependentReviewAssignmentTests(WorkflowTestMixin, TestCase):
    """A review must be assigned to someone who can actually decide it."""

    def setUp(self) -> None:
        self.client = APIClient()
        self.dsr = self.advance_to(
            self.create_request(), DSRRequest.State.RESPONSE_PREP
        )
        self.dsr.assigned_to = "handler"
        self.dsr.save(update_fields=("assigned_to", "updated_at"))
        DSRDecision.objects.create(
            dsr=self.dsr,
            category="overall",
            outcome=DSRDecision.Outcome.FULFILLED,
            rationale="Synthetic decision.",
            recorded_by="author",
        )
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:author")
        finalized = self.client.post(
            f"/api/v1/dpo/dsrs/{self.dsr.pk}/response-versions/",
            {"content": "Synthetic response."},
            format="json",
        )
        self.assertEqual(finalized.status_code, 201, finalized.data)

    def request_review(self, reviewer: str | None):
        payload = {
            "action": "request_legal_review",
            "reason": "Please review.",
            "proposed_outcome": DSRRequest.ResolutionStatus.FULFILLED,
        }
        if reviewer is not None:
            payload["assigned_reviewer"] = reviewer
        return self.transition(self.dsr, payload, "dpo:requester")

    def assert_still_in_response_prep(self) -> None:
        self.dsr = DSRRequest.objects.get(pk=self.dsr.pk)
        self.assertEqual(self.dsr.state, DSRRequest.State.RESPONSE_PREP)
        self.assertFalse(DSRLegalReviewRequest.objects.filter(dsr=self.dsr).exists())

    def test_reviewer_is_required(self) -> None:
        for reviewer in (None, "", "   "):
            with self.subTest(reviewer=reviewer):
                response = self.request_review(reviewer)
                self.assertEqual(response.status_code, 400)
                self.assertIn("assigned_reviewer", response.data)
        self.assert_still_in_response_prep()

    def test_conflicted_reviewers_are_rejected_at_request_time(self) -> None:
        for reviewer in ("requester", "author", "handler"):
            with self.subTest(reviewer=reviewer):
                response = self.request_review(reviewer)
                self.assertEqual(response.status_code, 409)
                self.assertIn("cannot be the requester", response.data["detail"])
        self.assert_still_in_response_prep()

    def test_independent_reviewer_can_complete_the_review(self) -> None:
        requested = self.request_review("independent-1")
        self.assertEqual(requested.status_code, 200, requested.data)
        self.dsr = DSRRequest.objects.get(pk=self.dsr.pk)
        self.assertEqual(self.dsr.state, DSRRequest.State.LEGAL_REVIEW)

        approved = self.transition(
            self.dsr,
            {"action": "approve_legal_review", "reason": "Looks right."},
            "independent_reviewer:independent-1",
        )
        self.assertEqual(approved.status_code, 200, approved.data)
        self.dsr = DSRRequest.objects.get(pk=self.dsr.pk)
        self.assertEqual(self.dsr.state, DSRRequest.State.RESPONSE_PREP)
        self.assertTrue(self.dsr.has_valid_legal_review())


class ResumeToPreviousStageTests(WorkflowTestMixin, TestCase):
    """Waiting for the subject or escalating must not rewind the case."""

    def setUp(self) -> None:
        self.client = APIClient()

    def patient_responds(self, request: DSRRequest):
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer patient:{request.patient_id}"
        )
        return self.client.post(
            f"/api/v1/dsrs/{request.pk}/communications/",
            {"message": "Here is the extra detail."},
            format="json",
        )

    def test_information_request_returns_to_the_stage_it_left(self) -> None:
        for stage in (
            DSRRequest.State.ACCEPTED,
            DSRRequest.State.PRIVACY_REVIEW,
            DSRRequest.State.RESPONSE_PREP,
        ):
            with self.subTest(stage=stage):
                dsr = self.advance_to(self.create_request(f"p-{stage}"), stage)
                asked = self.transition(
                    dsr,
                    {"action": "request_information", "reason": "Clarify scope."},
                    "dpo:staff",
                )
                self.assertEqual(asked.status_code, 200, asked.data)

                responded = self.patient_responds(dsr)
                self.assertEqual(responded.status_code, 201, responded.data)
                dsr = DSRRequest.objects.get(pk=dsr.pk)
                self.assertEqual(dsr.state, stage)
                self.assertEqual(dsr.waiting_from_state, "")

    def test_escalation_resumes_at_the_stage_it_left(self) -> None:
        dsr = self.advance_to(self.create_request(), DSRRequest.State.RESPONSE_PREP)
        escalated = self.transition(
            dsr, {"action": "escalate", "reason": "Needs DPO input."}, "dpo:staff"
        )
        self.assertEqual(escalated.status_code, 200, escalated.data)

        resumed = self.transition(
            dsr, {"action": "resume_escalated", "reason": "Resolved."}, "dpo:staff"
        )
        self.assertEqual(resumed.status_code, 200, resumed.data)
        dsr = DSRRequest.objects.get(pk=dsr.pk)
        self.assertEqual(dsr.state, DSRRequest.State.RESPONSE_PREP)
        self.assertEqual(dsr.escalated_from, "")
        self.assertIsNotNone(dsr.escalations.get().resolved_at)

    def test_escalation_while_waiting_keeps_the_original_stage(self) -> None:
        dsr = self.advance_to(self.create_request(), DSRRequest.State.PRIVACY_REVIEW)
        for action in ("request_information", "escalate", "resume_escalated"):
            response = self.transition(
                dsr, {"action": action, "reason": "Step."}, "dpo:staff"
            )
            self.assertEqual(response.status_code, 200, (action, response.data))
        dsr = DSRRequest.objects.get(pk=dsr.pk)
        self.assertEqual(dsr.state, DSRRequest.State.NEEDS_INFORMATION)
        self.assertEqual(dsr.waiting_from_state, DSRRequest.State.PRIVACY_REVIEW)

        self.assertEqual(self.patient_responds(dsr).status_code, 201)
        dsr = DSRRequest.objects.get(pk=dsr.pk)
        self.assertEqual(dsr.state, DSRRequest.State.PRIVACY_REVIEW)

    def test_records_without_a_saved_stage_fall_back_to_accepted(self) -> None:
        dsr = self.advance_to(self.create_request(), DSRRequest.State.RESPONSE_PREP)
        dsr.escalate(actor_id="staff", reason="Legacy record.")
        dsr.escalated_from = ""
        dsr.save(update_fields=("state", "escalated_from", "updated_at"))

        resumed = self.transition(
            dsr, {"action": "resume_escalated", "reason": "Resolved."}, "dpo:staff"
        )
        self.assertEqual(resumed.status_code, 200, resumed.data)
        dsr = DSRRequest.objects.get(pk=dsr.pk)
        self.assertEqual(dsr.state, DSRRequest.State.ACCEPTED)

    def test_resuming_into_departmental_search_does_not_recollect_data(self) -> None:
        dsr = self.create_request()
        with mock.patch(
            "dsr.tasks.trigger_fides_data_collection.delay"
        ) as collect, self.captureOnCommitCallbacks(execute=True):
            started = self.transition(
                dsr, {"action": "start_departmental_search"}, "dpo:staff"
            )
        self.assertEqual(started.status_code, 200, started.data)
        collect.assert_called_once_with(dsr.pk)

        for action in ("escalate", "resume_escalated"):
            with mock.patch(
                "dsr.tasks.trigger_fides_data_collection.delay"
            ) as collect, self.captureOnCommitCallbacks(execute=True):
                response = self.transition(
                    dsr, {"action": action, "reason": "Step."}, "dpo:staff"
                )
            self.assertEqual(response.status_code, 200, response.data)
            collect.assert_not_called()
        dsr = DSRRequest.objects.get(pk=dsr.pk)
        self.assertEqual(dsr.state, DSRRequest.State.DEPT_SEARCH)
