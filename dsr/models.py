from __future__ import annotations

from datetime import timedelta
from typing import ClassVar

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
        CLOSED = "CLOSED", "Closed"
        REJECTED = "REJECTED", "Rejected"
        ESCALATED = "ESCALATED", "Escalated"

    class RequestType(models.TextChoices):
        ACCESS = "ACCESS", "Access"
        PORTABILITY = "PORTABILITY", "Portability"
        RECTIFICATION = "RECTIFICATION", "Rectification"
        ERASURE = "ERASURE", "Erasure"
        RESTRICTION = "RESTRICTION", "Restriction"
        OBJECTION = "OBJECTION", "Objection"

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
    submitted_at = models.DateTimeField(auto_now_add=True)
    statutory_deadline = models.DateTimeField()
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-submitted_at",)

    def save(self, *args: object, **kwargs: object) -> None:
        if self.statutory_deadline is None:
            submitted_at = self.submitted_at or timezone.now()
            self.statutory_deadline = submitted_at + self.sla_duration()
        super().save(*args, **kwargs)

    def sla_duration(self) -> timedelta:
        days = 30 if self.request_type in self.LONG_SLA_TYPES else 14
        return timedelta(days=days)

    def time_remaining(self) -> timedelta:
        return self.statutory_deadline - timezone.now()

    def _set_transition_context(self, actor_id: str, reason: str) -> None:
        self._transition_actor_id = actor_id
        self._transition_reason = reason

    @transition(field=state, source=State.SUBMITTED, target=State.ACCEPTED)
    def accept_submission(self, *, actor_id: str, reason: str = "") -> None:
        """Accept requests authenticated by the Patient Portal."""
        self._set_transition_context(actor_id, reason)

    @transition(field=state, source=State.ACCEPTED, target=State.DEPT_SEARCH)
    def start_departmental_search(self, *, actor_id: str, reason: str = "") -> None:
        self._set_transition_context(actor_id, reason)

    @transition(field=state, source=State.DEPT_SEARCH, target=State.PRIVACY_REVIEW)
    def send_to_privacy_review(self, *, actor_id: str, reason: str = "") -> None:
        self._set_transition_context(actor_id, reason)

    @transition(field=state, source=State.PRIVACY_REVIEW, target=State.LEGAL_REVIEW)
    def request_legal_review(self, *, actor_id: str, reason: str = "") -> None:
        self._set_transition_context(actor_id, reason)

    @transition(field=state, source=State.PRIVACY_REVIEW, target=State.RESPONSE_PREP)
    def approve_privacy_review(self, *, actor_id: str, reason: str = "") -> None:
        self._set_transition_context(actor_id, reason)

    @transition(field=state, source=State.LEGAL_REVIEW, target=State.RESPONSE_PREP)
    def approve_legal_review(self, *, actor_id: str, reason: str = "") -> None:
        self._set_transition_context(actor_id, reason)

    @transition(field=state, source=State.RESPONSE_PREP, target=State.CLOSED)
    def approve_and_close(self, *, actor_id: str, reason: str = "") -> None:
        self._set_transition_context(actor_id, reason)
        self.closed_at = timezone.now()

    @transition(
        field=state,
        source=[State.ACCEPTED, State.DEPT_SEARCH, State.PRIVACY_REVIEW, State.LEGAL_REVIEW],
        target=State.REJECTED,
    )
    def reject(self, *, actor_id: str, reason: str) -> None:
        self._set_transition_context(actor_id, reason)

    @transition(
        field=state,
        source=[State.ACCEPTED, State.DEPT_SEARCH, State.PRIVACY_REVIEW, State.LEGAL_REVIEW],
        target=State.ESCALATED,
    )
    def escalate(self, *, actor_id: str, reason: str) -> None:
        self._set_transition_context(actor_id, reason)


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