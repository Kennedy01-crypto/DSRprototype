from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from dsr import services
from dsr.exceptions import WorkflowConflict
from dsr.models import (
    DSRCaseEvent,
    DSRDecision,
    DSRRequest,
    DSRSourceSearch,
    IntegrationJob,
)

DPO = {"actor_id": "dpo-1"}


def case_in_privacy_review(patient_id: str = "alice") -> DSRRequest:
    dsr, _ = services.submit_request(
        patient_id,
        {"request_type": "ACCESS", "description": "Synthetic", "department": "Records"},
    )
    for action in ("start_departmental_search", "send_to_privacy_review"):
        services.apply_transition(dsr.pk, action=action, actor_role="dpo", reason="", **DPO)
    return DSRRequest.objects.get(pk=dsr.pk)


def make_due(job: IntegrationJob) -> None:
    IntegrationJob.objects.filter(pk=job.pk).update(next_attempt_at=timezone.now())


class AutomatedSearchTests(TestCase):
    def test_successful_search_completes_the_checklist_line(self) -> None:
        dsr = case_in_privacy_review()
        job = services.request_automated_search(dsr.pk, source_system="CIMS", **DPO)
        search = DSRSourceSearch.objects.get(dsr=dsr, source_system="CIMS")
        self.assertEqual(search.status, DSRSourceSearch.Status.PENDING)
        with self.assertRaises(Exception):  # Pending search keeps the gate shut.
            services.apply_transition(
                dsr.pk, action="approve_privacy_review", actor_role="dpo", reason="", **DPO
            )

        self.assertEqual(services.run_integration_job(job.pk), IntegrationJob.Status.SUCCEEDED)
        search.refresh_from_db()
        self.assertEqual(search.status, DSRSourceSearch.Status.COMPLETE)
        self.assertTrue(search.evidence_reference.startswith("MOCK-CIMS-"))
        self.assertEqual(search.updated_by, "integration:CIMS")
        self.assertIn("No data was copied", search.summary)
        self.assertTrue(
            dsr.events.filter(
                event_type=DSRCaseEvent.EventType.INTEGRATION_UPDATE,
                summary__startswith="Automated CIMS (mock source) search completed",
            ).exists()
        )
        services.apply_transition(
            dsr.pk, action="approve_privacy_review", actor_role="dpo", reason="", **DPO
        )

    def test_asking_twice_returns_the_queued_job(self) -> None:
        dsr = case_in_privacy_review()
        first = services.request_automated_search(dsr.pk, source_system="DYNAMICS", **DPO)
        second = services.request_automated_search(dsr.pk, source_system="DYNAMICS", **DPO)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(IntegrationJob.objects.filter(dsr=dsr).count(), 1)

    def test_running_a_job_twice_does_nothing_the_second_time(self) -> None:
        dsr = case_in_privacy_review()
        job = services.request_automated_search(dsr.pk, source_system="CIMS", **DPO)
        services.run_integration_job(job.pk)
        self.assertIsNone(services.run_integration_job(job.pk))
        self.assertEqual(IntegrationJob.objects.get(pk=job.pk).attempts, 1)

    def test_outage_retries_with_backoff_then_blocks_the_search(self) -> None:
        dsr = case_in_privacy_review("outage-patient")
        job = services.request_automated_search(dsr.pk, source_system="CIMS", **DPO)

        self.assertEqual(services.run_integration_job(job.pk), IntegrationJob.Status.PENDING)
        job.refresh_from_db()
        self.assertEqual(job.attempts, 1)
        self.assertGreater(job.next_attempt_at, timezone.now() + timedelta(seconds=20))
        self.assertIsNone(services.run_integration_job(job.pk))  # Not due yet.

        for _ in range(job.max_attempts - 1):
            make_due(job)
            services.run_integration_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, IntegrationJob.Status.FAILED)
        self.assertEqual(job.attempts, job.max_attempts)
        self.assertIn("ConnectorUnavailable", job.last_error)
        search = DSRSourceSearch.objects.get(pk=job.source_search_id)
        self.assertEqual(search.status, DSRSourceSearch.Status.BLOCKED)

        retried = services.retry_integration_job(job.pk, actor_id="dpo-2")
        self.assertEqual(retried.status, IntegrationJob.Status.PENDING)
        self.assertEqual(retried.attempts, 0)
        search.refresh_from_db()
        self.assertEqual(search.status, DSRSourceSearch.Status.PENDING)

    def test_permanent_error_fails_at_once(self) -> None:
        dsr = case_in_privacy_review("badcreds-patient")
        job = services.request_automated_search(dsr.pk, source_system="ERPS", **DPO)
        self.assertEqual(services.run_integration_job(job.pk), IntegrationJob.Status.FAILED)
        self.assertEqual(IntegrationJob.objects.get(pk=job.pk).attempts, 1)

    def test_staff_entry_is_not_overwritten_by_a_late_result(self) -> None:
        dsr = case_in_privacy_review()
        job = services.request_automated_search(dsr.pk, source_system="CIMS", **DPO)
        services.update_source_search(
            dsr.pk,
            job.source_search_id,
            actor_id="dpo-1",
            data={"status": "COMPLETE", "summary": "Searched by hand."},
        )
        services.run_integration_job(job.pk)
        search = DSRSourceSearch.objects.get(pk=job.source_search_id)
        self.assertEqual(search.summary, "Searched by hand.")

    def test_only_configured_systems_and_open_cases(self) -> None:
        dsr = case_in_privacy_review()
        with self.assertRaises(WorkflowConflict):
            services.request_automated_search(dsr.pk, source_system="OTHER", **DPO)
        job = services.request_automated_search(dsr.pk, source_system="CIMS", **DPO)
        services.run_integration_job(job.pk)
        with self.assertRaises(WorkflowConflict):
            services.request_automated_search(dsr.pk, source_system="CIMS", **DPO)
        with self.assertRaises(WorkflowConflict):
            services.retry_integration_job(job.pk, actor_id="dpo-1")


class OutboxDeliveryTests(TestCase):
    def test_job_is_handed_to_the_worker_after_commit(self) -> None:
        dsr = case_in_privacy_review()
        with mock.patch("dsr.tasks.run_integration_job.delay") as delay, self.captureOnCommitCallbacks(
            execute=True
        ):
            job = services.request_automated_search(dsr.pk, source_system="CIMS", **DPO)
        delay.assert_called_once_with(job.pk)

    def test_lost_hand_off_is_picked_up_by_the_dispatcher(self) -> None:
        dsr = case_in_privacy_review()
        with mock.patch(
            "dsr.tasks.run_integration_job.delay", side_effect=ConnectionError("redis down")
        ), self.captureOnCommitCallbacks(execute=True):
            job = services.request_automated_search(dsr.pk, source_system="CIMS", **DPO)
        self.assertIn(job.pk, services.due_integration_job_ids())

    def test_dispatcher_frees_jobs_whose_worker_died(self) -> None:
        dsr = case_in_privacy_review()
        job = services.request_automated_search(dsr.pk, source_system="CIMS", **DPO)
        IntegrationJob.objects.filter(pk=job.pk).update(
            status=IntegrationJob.Status.RUNNING,
            started_at=timezone.now() - timedelta(minutes=30),
        )
        self.assertIn(job.pk, services.due_integration_job_ids())
        self.assertEqual(IntegrationJob.objects.get(pk=job.pk).status, IntegrationJob.Status.PENDING)


class PatientNotificationTests(TestCase):
    def test_publishing_queues_a_notification_without_case_details(self) -> None:
        dsr = case_in_privacy_review()
        DSRSourceSearch.objects.create(
            dsr=dsr, source_system="CIMS", status="COMPLETE", summary="ok",
            created_by="x", updated_by="x",
        )
        services.apply_transition(
            dsr.pk, action="approve_privacy_review", actor_role="dpo", reason="", **DPO
        )
        services.record_decision(
            dsr.pk, data={"category": "overall", "outcome": DSRDecision.Outcome.FULFILLED,
                          "rationale": "ok"}, **DPO,
        )
        version = services.finalize_response(dsr.pk, content="Private response text.", **DPO)
        services.publish_response(dsr.pk, version.pk, **DPO)

        job = IntegrationJob.objects.get(dsr=dsr, kind=IntegrationJob.Kind.PATIENT_NOTIFICATION)
        message = job.payload["message"]
        self.assertIn(f"DSR-{dsr.pk}", message)
        for private in ("Private response text.", "Synthetic", dsr.patient_id):
            self.assertNotIn(private, message)
        self.assertEqual(services.run_integration_job(job.pk), IntegrationJob.Status.SUCCEEDED)
        self.assertTrue(
            dsr.events.filter(summary__startswith="Patient notified").exists()
        )


class ApiTests(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()

    def submit(self, key: str | None):
        headers = {"HTTP_IDEMPOTENCY_KEY": key} if key is not None else {}
        return self.client.post(
            "/api/v1/dsrs/submit/",
            {"request_type": "ACCESS", "description": "Copy please", "department": "Records"},
            format="json",
            **headers,
        )

    def test_submission_with_the_same_key_is_not_duplicated(self) -> None:
        self.client.credentials(HTTP_AUTHORIZATION="Bearer patient:alice")
        first = self.submit("form-123")
        second = self.submit("form-123")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual(DSRRequest.objects.filter(patient_id="alice").count(), 1)

        self.client.credentials(HTTP_AUTHORIZATION="Bearer patient:bob")
        self.assertEqual(self.submit("form-123").status_code, 201)  # Keys are per patient.

    def test_submission_without_a_key_and_bad_keys(self) -> None:
        self.client.credentials(HTTP_AUTHORIZATION="Bearer patient:alice")
        self.assertEqual(self.submit(None).status_code, 201)
        self.assertEqual(self.submit(None).status_code, 201)
        self.assertEqual(self.submit("has spaces").status_code, 400)
        self.assertEqual(DSRRequest.objects.count(), 2)

    def test_dpo_endpoints(self) -> None:
        dsr = case_in_privacy_review()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:dpo-1")
        queued = self.client.post(
            f"/api/v1/dpo/dsrs/{dsr.pk}/source-searches/automated/",
            {"source_system": "DYNAMICS"},
            format="json",
        )
        self.assertEqual(queued.status_code, 202, queued.data)
        self.assertEqual(queued.data["status"], "PENDING")
        unsupported = self.client.post(
            f"/api/v1/dpo/dsrs/{dsr.pk}/source-searches/automated/",
            {"source_system": "OTHER"},
            format="json",
        )
        self.assertEqual(unsupported.status_code, 409)

        detail = self.client.get(f"/api/v1/dpo/dsrs/{dsr.pk}/")
        self.assertIn("DYNAMICS", detail.data["automated_source_systems"])
        self.assertEqual(len(detail.data["integration_jobs"]), 1)

        listing = self.client.get("/api/v1/dpo/integration-jobs/?status=PENDING")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.data["counts"]["PENDING"], 1)
        self.assertEqual(len(listing.data["jobs"]), 1)
        retry = self.client.post(f"/api/v1/dpo/integration-jobs/{queued.data['id']}/retry/")
        self.assertEqual(retry.status_code, 409)
        missing = self.client.post("/api/v1/dpo/integration-jobs/999999/retry/")
        self.assertEqual(missing.status_code, 404)
