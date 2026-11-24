from datetime import datetime, timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from dsr import deadlines, services
from dsr.exceptions import WorkflowConflict
from dsr.models import DSRCaseException, DSRRequest, IntegrationJob
from dsr.tasks import monitor_sla_deadlines

NAIROBI = timezone.get_fixed_timezone(180)


def local(year: int, month: int, day: int, hour: int = 10) -> datetime:
    return datetime(year, month, day, hour, tzinfo=NAIROBI)


def new_case(patient_id: str = "alice", request_type: str = "ACCESS") -> DSRRequest:
    dsr, _ = services.submit_request(
        patient_id, {"request_type": request_type, "description": "Synthetic", "department": ""}
    )
    return dsr


def set_policy(**overrides) -> None:
    values = {
        "jurisdiction_code": "KE",
        "internal_target_days": 10,
        "legal_deadline_days": 21,
        "policy_source": "Test policy",
        "clock_start_event": "SUBMITTED",
        "calendar_days": True,
        "extension_allowed": True,
        "maximum_extension_days": 14,
        "pause_allowed": True,
        "pause_reasons": ["Awaiting identity verification"],
        "notification_required": True,
        "reason": "Test setup",
    }
    values.update(overrides)
    services.update_deadline_policy(actor_id="dpo-1", request_type="ACCESS", data=values)


class StaleEditTests(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer dpo:dpo-1")
        self.dsr = new_case()

    def assign(self, revision: int | None, who: str):
        headers = {"HTTP_IF_MATCH": str(revision)} if revision is not None else {}
        return self.client.post(
            f"/api/v1/dpo/dsrs/{self.dsr.pk}/assignment/",
            {"assigned_to": who},
            format="json",
            **headers,
        )

    def test_second_edit_from_a_stale_screen_is_refused(self) -> None:
        revision = self.client.get(f"/api/v1/dpo/dsrs/{self.dsr.pk}/").data["request"]["revision"]
        first = self.assign(revision, "officer-a")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data["revision"], revision + 1)

        stale = self.assign(revision, "officer-b")
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.data["code"], "stale_case")
        self.assertEqual(DSRRequest.objects.get(pk=self.dsr.pk).assigned_to, "officer-a")

        fresh = self.assign(revision + 1, "officer-b")
        self.assertEqual(fresh.status_code, 200)

    def test_without_if_match_there_is_no_check(self) -> None:
        self.assertEqual(self.assign(None, "x").status_code, 200)
        self.assertEqual(self.assign(None, "y").status_code, 200)

    def test_a_refused_action_does_not_change_the_revision(self) -> None:
        before = DSRRequest.objects.get(pk=self.dsr.pk).revision
        with self.assertRaises(WorkflowConflict):
            services.finalize_response(self.dsr.pk, actor_id="dpo-1", content="too early")
        self.assertEqual(DSRRequest.objects.get(pk=self.dsr.pk).revision, before)

    def test_notification_jobs_do_not_make_screens_stale_but_searches_do(self) -> None:
        notify = services._queue_patient_notification(self.dsr, actor_id="dpo-1", reason="t")
        before = DSRRequest.objects.get(pk=self.dsr.pk).revision
        services.run_integration_job(notify.pk)
        self.assertEqual(DSRRequest.objects.get(pk=self.dsr.pk).revision, before)

        search = services.request_automated_search(self.dsr.pk, actor_id="dpo-1", source_system="CIMS")
        before = DSRRequest.objects.get(pk=self.dsr.pk).revision
        services.run_integration_job(search.pk)
        self.assertEqual(DSRRequest.objects.get(pk=self.dsr.pk).revision, before + 1)
        self.assertEqual(
            IntegrationJob.objects.get(pk=search.pk).status, IntegrationJob.Status.SUCCEEDED
        )


@override_settings(DSR_PUBLIC_HOLIDAYS=["2026-12-25", "2026-12-28"])
class BusinessDayTests(TestCase):
    def test_add_business_days_skips_weekends_and_holidays(self) -> None:
        friday = local(2026, 12, 18)
        self.assertEqual(deadlines.add_days(friday, 1, calendar_days=False), local(2026, 12, 21))
        # Thu 24 + 1 -> skip Fri 25 (holiday), Sat, Sun, Mon 28 (holiday) -> Tue 29.
        self.assertEqual(
            deadlines.add_days(local(2026, 12, 24), 1, calendar_days=False), local(2026, 12, 29)
        )
        self.assertEqual(deadlines.add_days(friday, 3, calendar_days=True), local(2026, 12, 21))

    def test_policy_in_business_days_sets_both_deadlines(self) -> None:
        set_policy(calendar_days=False, internal_target_days=5, legal_deadline_days=10)
        with mock.patch("django.utils.timezone.now", return_value=local(2026, 12, 18)):
            dsr = new_case()
        self.assertEqual(timezone.localtime(dsr.response_due_at, NAIROBI), local(2026, 12, 29))
        self.assertEqual(timezone.localtime(dsr.legal_deadline_at, NAIROBI), local(2027, 1, 5))


class ExtensionAndPauseTests(TestCase):
    def setUp(self) -> None:
        set_policy()
        self.dsr = new_case()

    def exception(self, exception_type: str, **extra):
        data = {
            "exception_type": exception_type,
            "reason": "Test",
            "patient_notification": "We need more time.",
            **extra,
        }
        return services.record_case_exception(self.dsr.pk, actor_id="dpo-1", data=data)

    def test_extension_moves_both_clocks_together(self) -> None:
        before = DSRRequest.objects.get(pk=self.dsr.pk)
        self.exception("EXTENSION", deadline_effect_days=7)
        after = DSRRequest.objects.get(pk=self.dsr.pk)
        self.assertEqual(after.legal_deadline_at - before.legal_deadline_at, timedelta(days=7))
        self.assertEqual(after.response_due_at - before.response_due_at, timedelta(days=7))

    def test_pause_stops_the_clock_and_resume_adds_the_paused_time(self) -> None:
        start = timezone.now()
        before = DSRRequest.objects.get(pk=self.dsr.pk)
        with mock.patch("django.utils.timezone.now", return_value=start):
            self.exception("DEADLINE_PAUSE", policy_reason="Awaiting identity verification")
        paused = DSRRequest.objects.get(pk=self.dsr.pk)
        self.assertEqual(paused.deadline_paused_at, start)
        # Remaining time is frozen while paused.
        self.assertEqual(paused.time_remaining(), before.response_due_at - start)
        with self.assertRaises(WorkflowConflict):
            self.exception("DEADLINE_PAUSE", policy_reason="Awaiting identity verification")

        with mock.patch("django.utils.timezone.now", return_value=start + timedelta(days=3, hours=4)):
            resumed_exception = self.exception("DEADLINE_RESUME", patient_notification="")
        resumed = DSRRequest.objects.get(pk=self.dsr.pk)
        self.assertIsNone(resumed.deadline_paused_at)
        shift = timedelta(days=3, hours=4)
        self.assertEqual(resumed.response_due_at - before.response_due_at, shift)
        self.assertEqual(resumed.legal_deadline_at - before.legal_deadline_at, shift)
        self.assertEqual(resumed_exception.deadline_effect_seconds, int(shift.total_seconds()))
        with self.assertRaises(WorkflowConflict):
            self.exception("DEADLINE_RESUME")

    def test_paused_cases_are_not_reported_overdue(self) -> None:
        DSRRequest.objects.filter(pk=self.dsr.pk).update(
            response_due_at=timezone.now() - timedelta(days=1)
        )
        self.exception("DEADLINE_PAUSE", policy_reason="Awaiting identity verification")
        paused = DSRRequest.objects.get(pk=self.dsr.pk)
        self.assertFalse(paused.is_overdue)
        self.assertEqual(monitor_sla_deadlines(), 0)

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer dpo:dpo-1")
        overdue = client.get("/api/v1/dpo/dsrs/?queue=overdue").data["requests"]
        paused_queue = client.get("/api/v1/dpo/dsrs/?queue=paused").data["requests"]
        self.assertNotIn(self.dsr.pk, [item["id"] for item in overdue])
        self.assertIn(self.dsr.pk, [item["id"] for item in paused_queue])

    def test_pause_needs_an_approved_reason(self) -> None:
        with self.assertRaises(WorkflowConflict):
            self.exception("DEADLINE_PAUSE", policy_reason="Not on the list")
        self.assertFalse(
            DSRCaseException.objects.filter(dsr=self.dsr, exception_type="DEADLINE_PAUSE").exists()
        )


class OperationsTests(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()

    def test_request_id_is_echoed_or_generated(self) -> None:
        echoed = self.client.get("/healthz", HTTP_X_REQUEST_ID="abc12345-from-frontend")
        self.assertEqual(echoed["X-Request-ID"], "abc12345-from-frontend")
        generated = self.client.get("/healthz", HTTP_X_REQUEST_ID="bad id with spaces")
        self.assertRegex(generated["X-Request-ID"], r"^[0-9a-f]{32}$")

    def test_readiness_reports_each_dependency(self) -> None:
        with mock.patch("redis.Redis.ping", return_value=True):
            ready = self.client.get("/readyz")
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json()["checks"], {"database": "ok", "queue": "ok"})
        with mock.patch("redis.Redis.ping", side_effect=ConnectionError("down")):
            not_ready = self.client.get("/readyz")
        self.assertEqual(not_ready.status_code, 503)
        self.assertEqual(not_ready.json()["checks"]["queue"], "unavailable (ConnectionError)")
        self.assertEqual(self.client.get("/healthz").status_code, 200)
