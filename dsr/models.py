from __future__ import annotations

from datetime import timedelta
from typing import ClassVar

from django.conf import settings
from django.db import models
from django.utils import timezone
from django_fsm import FSMField, transition


class DSRRequest(models.Model):
    class State(models.TextChoices):
        SUBMITTED = "SUBMITTED", "Submitted"
        ACCEPTED = "ACCEPTED", "Accepted / Under Assessment"
        DEPT_SEARCH = "DEPT_SEARCH", "Departmental Search"
        PRIVACY_REVIEW = "PRIVACY_REVIEW", "Privacy Review"
        LEGAL_REVIEW = "LEGAL_REVIEW", "Legal / Management Review"
        RESPONSE_PREP = "RESPONSE_PREP", "Response Preparation"
        NEEDS_INFORMATION = "NEEDS_INFORMATION", "Waiting for Subject Information"
        CLOSED = "CLOSED", "Closed"
        REJECTED = "REJECTED", "Rejected"
        ESCALATED = "ESCALATED", "Escalated"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"

    class RequestType(models.TextChoices):
        ACCESS = "ACCESS", "Access"
        PORTABILITY = "PORTABILITY", "Portability"
        RECTIFICATION = "RECTIFICATION", "Rectification"
        ERASURE = "ERASURE", "Erasure"
        RESTRICTION = "RESTRICTION", "Restriction"
        OBJECTION = "OBJECTION", "Objection"

    class ResolutionStatus(models.TextChoices):
        OPEN = "OPEN", "Open"
        FULFILLED = "FULFILLED", "Fulfilled"
        PARTIALLY_FULFILLED = "PARTIALLY_FULFILLED", "Partially fulfilled"
        RETAINED = "RETAINED", "Retained with rationale"
        REJECTED = "REJECTED", "Rejected"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"

    LONG_SLA_TYPES: ClassVar[frozenset[str]] = frozenset(
        {RequestType.ACCESS, RequestType.PORTABILITY}
    )

    patient_id = models.CharField(max_length=128, db_index=True)
    request_type = models.CharField(max_length=32, choices=RequestType.choices)
    description = models.TextField()
    department = models.CharField(max_length=128, blank=True)
    state = FSMField(
        max_length=32,
        choices=State.choices,
        default=State.SUBMITTED,
        protected=True,
    )
    resolution_status = models.CharField(
        max_length=32,
        choices=ResolutionStatus.choices,
        default=ResolutionStatus.OPEN,
    )
    assigned_to = models.CharField(max_length=128, blank=True)
    escalated_from = models.CharField(max_length=32, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    response_due_at = models.DateTimeField()
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-submitted_at",)

    def save(self, *args: object, **kwargs: object) -> None:
        if self.response_due_at is None:
            submitted_at = self.submitted_at or timezone.now()
            self.response_due_at = submitted_at + self.sla_duration()
        super().save(*args, **kwargs)

    def sla_duration(self) -> timedelta:
        setting_name = (
            "DSR_LONG_RESPONSE_TARGET_DAYS"
            if self.request_type in self.LONG_SLA_TYPES
            else "DSR_DEFAULT_RESPONSE_TARGET_DAYS"
        )
        days = getattr(settings, setting_name)
        return timedelta(days=days)

    def time_remaining(self) -> timedelta:
        return self.response_due_at - timezone.now()

    @property
    def can_withdraw(self) -> bool:
        return self.state not in {
            self.State.CLOSED,
            self.State.REJECTED,
            self.State.WITHDRAWN,
        }

    @property
    def is_overdue(self) -> bool:
        return (
            self.state
            in {
                self.State.ACCEPTED,
                self.State.DEPT_SEARCH,
                self.State.PRIVACY_REVIEW,
                self.State.LEGAL_REVIEW,
                self.State.RESPONSE_PREP,
                self.State.NEEDS_INFORMATION,
            }
            and self.response_due_at < timezone.now()
        )

    def _set_transition_context(self, actor_id: str, reason: str) -> None:
        self._transition_actor_id = actor_id
        self._transition_reason = reason

    def source_searches_complete(self) -> bool:
        searches = self.source_searches.all()
        return searches.exists() and not searches.exclude(
            status=DSRSourceSearch.Status.COMPLETE
        ).exists()

    def has_published_response(self) -> bool:
        return self.response_versions.filter(publication__isnull=False).exists()

    @transition(field=state, source=State.SUBMITTED, target=State.ACCEPTED)
    def accept_submission(self, *, actor_id: str, reason: str = "") -> None:
        """Accept requests authenticated by the Patient Portal."""
        self._set_transition_context(actor_id, reason)

    @transition(field=state, source=State.ACCEPTED, target=State.DEPT_SEARCH)
    def start_departmental_search(self, *, actor_id: str, reason: str = "") -> None:
        self._set_transition_context(actor_id, reason)

    @transition(
        field=state,
        source=[
            State.ACCEPTED,
            State.DEPT_SEARCH,
            State.PRIVACY_REVIEW,
            State.LEGAL_REVIEW,
            State.RESPONSE_PREP,
        ],
        target=State.NEEDS_INFORMATION,
    )
    def request_information(self, *, actor_id: str, reason: str) -> None:
        self._set_transition_context(actor_id, reason)

    @transition(field=state, source=State.NEEDS_INFORMATION, target=State.ACCEPTED)
    def provide_information(self, *, actor_id: str, reason: str) -> None:
        self._set_transition_context(actor_id, reason)

    @transition(field=state, source=State.ESCALATED, target=State.ACCEPTED)
    def resume_escalated(self, *, actor_id: str, reason: str) -> None:
        self._set_transition_context(actor_id, reason)
        self.escalated_from = ""

    @transition(
        field=state,
        source=State.ESCALATED,
        target=State.NEEDS_INFORMATION,
    )
    def resume_escalated_waiting_for_information(
        self, *, actor_id: str, reason: str
    ) -> None:
        self._set_transition_context(actor_id, reason)
        self.escalated_from = ""

    @transition(field=state, source=State.DEPT_SEARCH, target=State.PRIVACY_REVIEW)
    def send_to_privacy_review(self, *, actor_id: str, reason: str = "") -> None:
        self._set_transition_context(actor_id, reason)

    @transition(field=state, source=State.PRIVACY_REVIEW, target=State.LEGAL_REVIEW)
    def request_legal_review(self, *, actor_id: str, reason: str = "") -> None:
        self._set_transition_context(actor_id, reason)

    @transition(
        field=state,
        source=State.PRIVACY_REVIEW,
        target=State.RESPONSE_PREP,
        conditions=[lambda instance: instance.source_searches_complete()],
    )
    def approve_privacy_review(self, *, actor_id: str, reason: str = "") -> None:
        self._set_transition_context(actor_id, reason)

    @transition(
        field=state,
        source=State.LEGAL_REVIEW,
        target=State.RESPONSE_PREP,
        conditions=[lambda instance: instance.source_searches_complete()],
    )
    def approve_legal_review(self, *, actor_id: str, reason: str = "") -> None:
        self._set_transition_context(actor_id, reason)

    @transition(
        field=state,
        source=State.RESPONSE_PREP,
        target=State.CLOSED,
        conditions=[lambda instance: instance.has_published_response()],
    )
    def approve_and_close(
        self,
        *,
        actor_id: str,
        reason: str,
        resolution_status: str,
    ) -> None:
        self._set_transition_context(actor_id, reason)
        self.resolution_status = resolution_status
        self.closed_at = timezone.now()

    @transition(
        field=state,
        source=[State.ACCEPTED, State.DEPT_SEARCH, State.PRIVACY_REVIEW, State.LEGAL_REVIEW],
        target=State.REJECTED,
    )
    def reject(self, *, actor_id: str, reason: str) -> None:
        self._set_transition_context(actor_id, reason)
        self.resolution_status = self.ResolutionStatus.REJECTED
        self.closed_at = timezone.now()

    @transition(
        field=state,
        source=[
            State.SUBMITTED,
            State.ACCEPTED,
            State.DEPT_SEARCH,
            State.PRIVACY_REVIEW,
            State.LEGAL_REVIEW,
            State.RESPONSE_PREP,
            State.NEEDS_INFORMATION,
            State.ESCALATED,
        ],
        target=State.WITHDRAWN,
    )
    def withdraw(self, *, actor_id: str, reason: str = "") -> None:
        self._set_transition_context(actor_id, reason)
        self.resolution_status = self.ResolutionStatus.WITHDRAWN
        self.closed_at = timezone.now()
        self.escalated_from = ""

    @transition(
        field=state,
        source=[
            State.ACCEPTED,
            State.DEPT_SEARCH,
            State.PRIVACY_REVIEW,
            State.LEGAL_REVIEW,
            State.RESPONSE_PREP,
            State.NEEDS_INFORMATION,
        ],
        target=State.ESCALATED,
    )
    def escalate(self, *, actor_id: str, reason: str) -> None:
        self._set_transition_context(actor_id, reason)
        self.escalated_from = self.state


class DSRAuditLog(models.Model):
    dsr = models.ForeignKey(DSRRequest, on_delete=models.PROTECT, related_name="audit_logs")
    actor_id = models.CharField(max_length=128)
    from_state = models.CharField(max_length=32)
    to_state = models.CharField(max_length=32)
    reason = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("timestamp", "pk")

    def save(self, *args: object, **kwargs: object) -> None:
        if self.pk is not None:
            raise ValueError("DSRAuditLog entries are append-only")
        super().save(*args, **kwargs)


class DSRCaseEvent(models.Model):
    class EventType(models.TextChoices):
        ASSIGNED = "ASSIGNED", "Assigned"
        TASK_CREATED = "TASK_CREATED", "Review task created"
        TASK_UPDATED = "TASK_UPDATED", "Review task updated"
        DECISION_RECORDED = "DECISION_RECORDED", "Decision recorded"
        PATIENT_UPDATE_RECORDED = "PATIENT_UPDATE_RECORDED", "Patient update recorded"
        PATIENT_RESPONSE_RECORDED = "PATIENT_RESPONSE_RECORDED", "Patient response recorded"
        CASES_EXPORTED = "CASES_EXPORTED", "Case included in a DPO export"
        SOURCE_SEARCH_RECORDED = "SOURCE_SEARCH_RECORDED", "Source search recorded"
        RESPONSE_FINALIZED = "RESPONSE_FINALIZED", "Response finalized"
        RESPONSE_PUBLISHED = "RESPONSE_PUBLISHED", "Response published to portal"

    dsr = models.ForeignKey(DSRRequest, on_delete=models.PROTECT, related_name="events")
    actor_id = models.CharField(max_length=128)
    event_type = models.CharField(max_length=32, choices=EventType.choices)
    summary = models.CharField(max_length=255)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("timestamp", "pk")

    def save(self, *args: object, **kwargs: object) -> None:
        if self.pk is not None:
            raise ValueError("DSR case events are append-only")
        super().save(*args, **kwargs)


class DSRReviewTask(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        COMPLETED = "COMPLETED", "Completed"
        BLOCKED = "BLOCKED", "Blocked"

    dsr = models.ForeignKey(DSRRequest, on_delete=models.PROTECT, related_name="review_tasks")
    title = models.CharField(max_length=160)
    details = models.TextField(blank=True)
    assigned_to = models.CharField(max_length=128, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    created_by = models.CharField(max_length=128)
    updated_by = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("status", "created_at", "pk")


class DSRDecision(models.Model):
    class Outcome(models.TextChoices):
        FULFILLED = "FULFILLED", "Fulfilled"
        PARTIALLY_FULFILLED = "PARTIALLY_FULFILLED", "Partially fulfilled"
        RETAINED = "RETAINED", "Retained with rationale"
        REJECTED = "REJECTED", "Rejected"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"
        FURTHER_REVIEW = "FURTHER_REVIEW", "Further review required"

    dsr = models.ForeignKey(DSRRequest, on_delete=models.PROTECT, related_name="decisions")
    category = models.CharField(max_length=80, default="overall")
    outcome = models.CharField(max_length=32, choices=Outcome.choices)
    rationale = models.TextField()
    recorded_by = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "pk")

    def save(self, *args: object, **kwargs: object) -> None:
        if self.pk is not None:
            raise ValueError("DSR decisions are append-only")
        super().save(*args, **kwargs)


class DSRCommunication(models.Model):
    class Direction(models.TextChoices):
        DPO_TO_PATIENT = "DPO_TO_PATIENT", "DPO to patient"
        PATIENT_TO_DPO = "PATIENT_TO_DPO", "Patient to DPO"

    dsr = models.ForeignKey(
        DSRRequest, on_delete=models.PROTECT, related_name="communications"
    )
    message = models.TextField()
    direction = models.CharField(
        max_length=16,
        choices=Direction.choices,
        default=Direction.DPO_TO_PATIENT,
    )
    created_by = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "pk")

    def save(self, *args: object, **kwargs: object) -> None:
        if self.pk is not None:
            raise ValueError("DSR communications are append-only")
        super().save(*args, **kwargs)


class DSRSourceSearch(models.Model):
    class SourceSystem(models.TextChoices):
        PARAS = "PARAS", "PARAS (mock source)"
        CIMS = "CIMS", "CIMS (mock source)"
        ERPS = "ERPS", "ERPS (mock source)"
        OTHER = "OTHER", "Other mock source"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        COMPLETE = "COMPLETE", "Complete"
        BLOCKED = "BLOCKED", "Blocked"

    dsr = models.ForeignKey(
        DSRRequest, on_delete=models.PROTECT, related_name="source_searches"
    )
    source_system = models.CharField(max_length=16, choices=SourceSystem.choices)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    summary = models.TextField(blank=True)
    evidence_reference = models.CharField(max_length=256, blank=True)
    created_by = models.CharField(max_length=128)
    updated_by = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("source_system", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=("dsr", "source_system"),
                name="unique_dsr_source_search",
            )
        ]


class DSRResponseVersion(models.Model):
    dsr = models.ForeignKey(
        DSRRequest, on_delete=models.PROTECT, related_name="response_versions"
    )
    version = models.PositiveIntegerField()
    content = models.TextField()
    case_snapshot = models.JSONField(default=dict)
    finalized_by = models.CharField(max_length=128)
    finalized_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-version",)
        constraints = [
            models.UniqueConstraint(
                fields=("dsr", "version"),
                name="unique_dsr_response_version",
            )
        ]

    def save(self, *args: object, **kwargs: object) -> None:
        if self.pk is not None:
            raise ValueError("Finalized response versions are immutable")
        super().save(*args, **kwargs)


class DSRResponsePublication(models.Model):
    response_version = models.OneToOneField(
        DSRResponseVersion,
        on_delete=models.PROTECT,
        related_name="publication",
    )
    published_by = models.CharField(max_length=128)
    published_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args: object, **kwargs: object) -> None:
        if self.pk is not None:
            raise ValueError("Response publication records are append-only")
        super().save(*args, **kwargs)

class DSRDataQuarantine(models.Model):
    dsr = models.OneToOneField(DSRRequest, on_delete=models.PROTECT, related_name="quarantine")
    storage_reference = models.CharField(max_length=512, unique=True)
    encryption_key_reference = models.CharField(max_length=256)
    source_systems = models.JSONField(default=list)
    payload_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        verbose_name = "DSR data quarantine record"