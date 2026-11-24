from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from dsr import services
from dsr.exceptions import CaseNotFound, WorkflowConflict
from dsr.models import DSRCaseEvent, DSRDecision, DSRRequest, DSRSourceSearch


def new_case(patient_id: str = "alice") -> DSRRequest:
    return services.submit_request(
        patient_id,
        {
            "request_type": DSRRequest.RequestType.ACCESS,
            "description": "Synthetic request",
            "department": "Records",
        },
    )


class ServiceLayerTests(TestCase):
    """The services are usable without HTTP, e.g. from Celery tasks or connectors."""

    def test_full_case_lifecycle_through_services_only(self) -> None:
        dsr = new_case()
        staff = {"actor_id": "dpo-1"}

        def move(action: str, **extra) -> DSRRequest:
            return services.apply_transition(
                dsr.pk, action=action, actor_role="dpo", reason="Step.", **staff, **extra
            )

        self.assertEqual(move("start_departmental_search").state, DSRRequest.State.DEPT_SEARCH)
        self.assertEqual(move("send_to_privacy_review").state, DSRRequest.State.PRIVACY_REVIEW)
        search = services.create_source_search(
            dsr.pk,
            **staff,
            data={"source_system": DSRSourceSearch.SourceSystem.CIMS, "status": "PENDING"},
        )
        with self.assertRaises(Exception):
            move("approve_privacy_review")  # Pending search blocks the gate.
        services.update_source_search(
            dsr.pk, search.pk, **staff, data={"status": "COMPLETE", "summary": "Found 3."}
        )
        self.assertEqual(move("approve_privacy_review").state, DSRRequest.State.RESPONSE_PREP)
        services.record_decision(
            dsr.pk,
            **staff,
            data={"category": "overall", "outcome": DSRDecision.Outcome.FULFILLED, "rationale": "OK"},
        )
        version = services.finalize_response(dsr.pk, **staff, content="Your records.")
        services.publish_response(dsr.pk, version.pk, **staff)
        closed = move("approve_and_close", resolution_status=DSRRequest.ResolutionStatus.FULFILLED)
        self.assertEqual(closed.state, DSRRequest.State.CLOSED)
        self.assertEqual(closed.resolution_status, DSRRequest.ResolutionStatus.FULFILLED)

    def test_refused_action_writes_nothing(self) -> None:
        dsr = new_case()
        events_before = DSRCaseEvent.objects.filter(dsr=dsr).count()
        with self.assertRaises(WorkflowConflict):
            services.finalize_response(dsr.pk, actor_id="dpo-1", content="Too early.")
        self.assertEqual(DSRCaseEvent.objects.filter(dsr=dsr).count(), events_before)
        self.assertFalse(dsr.response_versions.exists())

    def test_patient_scope_hides_other_patients_cases(self) -> None:
        dsr = new_case("alice")
        with self.assertRaises(CaseNotFound):
            services.withdraw_request(dsr.pk, patient_id="bob", reason="")
        self.assertEqual(DSRRequest.objects.get(pk=dsr.pk).state, DSRRequest.State.SUBMITTED)


class ApiSurfaceTests(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()

    def test_browser_workspace_pages_are_gone(self) -> None:
        for path in ("/demo/login/", "/patient/", "/dpo/", "/admin/", "/dpo/export/", "/dpo/requests/1/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
        index = self.client.get("/")
        self.assertEqual(index.status_code, 200)
        self.assertEqual(index.json()["service"], "dsr-api")

    def test_dpo_case_detail_includes_evidence_and_response_state(self) -> None:
        dsr = new_case()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:dpo-1")
        detail = self.client.get(f"/api/v1/dpo/dsrs/{dsr.pk}/")
        self.assertEqual(detail.status_code, 200)
        for key in ("source_searches", "decisions", "response_versions", "response_readiness"):
            self.assertIn(key, detail.data)
        self.assertEqual(
            detail.data["response_readiness"], "The case must be in Response Preparation."
        )

    def test_workflow_errors_map_to_http_status(self) -> None:
        dsr = new_case()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:dpo-1")
        conflict = self.client.post(
            f"/api/v1/dpo/dsrs/{dsr.pk}/response-versions/", {"content": "x"}, format="json"
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.data["detail"], "The case must be in Response Preparation.")
        missing = self.client.post(
            f"/api/v1/dpo/dsrs/{dsr.pk + 999}/assignment/", {"assigned_to": "x"}, format="json"
        )
        self.assertEqual(missing.status_code, 404)
