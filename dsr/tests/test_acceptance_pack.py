from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from dsr.models import DSRDecision, DSRRequest


class DSRAcceptancePackTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        call_command("seed_demo_data", verbosity=0)

    def test_seed_command_is_repeatable_and_leaves_existing_examples_unchanged(self) -> None:
        initial_count = DSRRequest.objects.count()
        call_command("seed_demo_data", verbosity=0)

        self.assertEqual(DSRRequest.objects.count(), initial_count)
        self.assertEqual(
            DSRRequest.objects.filter(
                patient_id__startswith="mock-patient-acceptance-"
            ).count(),
            5,
        )

    def test_access_scenario_exposes_only_its_published_response_to_its_patient(self) -> None:
        request = DSRRequest.objects.get(
            patient_id="mock-patient-acceptance-access"
        )
        self.assertEqual(request.state, DSRRequest.State.CLOSED)

        client = APIClient()
        client.credentials(
            HTTP_AUTHORIZATION="Bearer patient:mock-patient-acceptance-access"
        )
        own_responses = client.get(f"/api/v1/dsrs/{request.pk}/responses/")
        self.assertEqual(own_responses.status_code, 200)
        self.assertEqual(len(own_responses.data), 1)
        self.assertIn("Synthetic response", own_responses.data[0]["content"])
        self.assertNotIn("case_snapshot", own_responses.data[0])

        client.credentials(
            HTTP_AUTHORIZATION="Bearer patient:mock-patient-acceptance-erasure"
        )
        self.assertEqual(
            client.get(f"/api/v1/dsrs/{request.pk}/responses/").status_code,
            404,
        )

    def test_erasure_retention_exercise_is_explicitly_hypothetical(self) -> None:
        request = DSRRequest.objects.get(
            patient_id="mock-patient-acceptance-erasure"
        )
        decision = request.decisions.get(category="overall")

        self.assertEqual(request.request_type, DSRRequest.RequestType.ERASURE)
        self.assertEqual(request.state, DSRRequest.State.RESPONSE_PREP)
        self.assertEqual(decision.outcome, DSRDecision.Outcome.RETAINED)
        self.assertIn("Hypothetical training entry only", decision.rationale)
        self.assertIn("not a statement", decision.rationale)
        self.assertFalse(request.has_published_response())

    def test_pending_or_blocked_searches_prevent_advancing_and_final_decisions(self) -> None:
        request = DSRRequest.objects.get(
            patient_id="mock-patient-acceptance-search"
        )
        self.assertEqual(request.state, DSRRequest.State.PRIVACY_REVIEW)
        self.assertFalse(request.source_searches_complete())

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer dpo:acceptance-reviewer")
        transition = client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {"action": "approve_privacy_review"},
            format="json",
        )
        final_decision = client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/decisions/",
            {
                "category": "overall",
                "outcome": DSRDecision.Outcome.FULFILLED,
                "rationale": "Attempted final outcome in acceptance test.",
            },
            format="json",
        )
        response_version = client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/response-versions/",
            {"content": "Attempted final response."},
            format="json",
        )

        self.assertEqual(transition.status_code, 409)
        self.assertEqual(final_decision.status_code, 409)
        self.assertEqual(response_version.status_code, 409)

    def test_clarification_response_resumes_review(self) -> None:
        request = DSRRequest.objects.get(
            patient_id="mock-patient-acceptance-clarification"
        )
        self.assertEqual(request.state, DSRRequest.State.NEEDS_INFORMATION)
        self.assertTrue(request.communications.exists())

        client = APIClient()
        client.credentials(
            HTTP_AUTHORIZATION="Bearer patient:mock-patient-acceptance-clarification"
        )
        response = client.post(
            f"/api/v1/dsrs/{request.pk}/communications/",
            {"message": "Clarification provided using fictional details."},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        request = DSRRequest.objects.get(pk=request.pk)
        self.assertEqual(request.state, DSRRequest.State.ACCEPTED)

    def test_overdue_escalation_can_be_resumed_without_resetting_its_target(self) -> None:
        request = DSRRequest.objects.get(
            patient_id="mock-patient-acceptance-escalated"
        )
        self.assertEqual(request.state, DSRRequest.State.ESCALATED)
        self.assertLess(request.response_due_at, timezone.now())
        original_target = request.response_due_at

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer dpo:acceptance-reviewer")
        resumed = client.post(
            f"/api/v1/dpo/dsrs/{request.pk}/transition/",
            {"action": "resume_escalated", "reason": "Resume acceptance exercise."},
            format="json",
        )

        self.assertEqual(resumed.status_code, 200)
        request = DSRRequest.objects.get(pk=request.pk)
        self.assertEqual(request.state, DSRRequest.State.ACCEPTED)
        self.assertEqual(request.response_due_at, original_target)
        self.assertLess(request.response_due_at, timezone.now() - timedelta(days=1))
