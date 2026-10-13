from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from django.db import models
from django.utils import timezone
from django.core.validators import MaxValueValidator, MinValueValidator
from django_fsm import FSMField, TransitionNotAllowed, transition


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
        ACCESS = "ACCESS", "Access / Copy"
        PORTABILITY = "PORTABILITY", "Data transfer / Portability"
        RECTIFICATION = "RECTIFICATION", "Rectification / Correction"
        ERASURE = "ERASURE", "Deletion / Erasure"
        RESTRICTION = "RESTRICTION", "Restriction"
        OBJECTION = "OBJECTION", "Objection"
        OTHER = "OTHER", "Other"

    class ResolutionStatus(models.TextChoices):
        OPEN = "OPEN", "Open"
        FULFILLED = "FULFILLED", "Fulfilled"
        PARTIALLY_FULFILLED = "PARTIALLY_FULFILLED", "Partially fulfilled"
        RETAINED = "RETAINED", "Retained with rationale"
        REJECTED = "REJECTED", "Rejected"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"
        DUPLICATE = "DUPLICATE", "Duplicate"

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
    legal_review_completed = models.BooleanField(default=False)
    independent_review_required = models.BooleanField(default=False)
    independent_review_reason = models.TextField(blank=True)
    assigned_to = models.CharField(max_length=128, blank=True)
    escalated_from = models.CharField(max_length=32, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    response_due_at = models.DateTimeField()
    legal_deadline_at = models.DateTimeField(null=True, blank=True)
    duplicate_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="duplicate_requests",
    )
    reconsideration_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reconsiderations",
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-submitted_at",)

    def save(self, *args: object, **kwargs: object) -> None:
        if self.response_due_at is None:
            submitted_at = self.submitted_at or timezone.now()
            self.response_due_at = submitted_at + self.sla_duration()
        if self._state.adding and self.legal_deadline_at is None:
            policy = DSRDeadlinePolicy.objects.filter(
                request_type=self.request_type
            ).first()
            if policy and policy.legal_deadline_days is not None:
                submitted_at = self.submitted_at or timezone.now()
                self.legal_deadline_at = submitted_at + timedelta(
                    days=policy.legal_deadline_days
                )
        super().save(*args, **kwargs)

    def sla_duration(self) -> timedelta:
        policy = DSRDeadlinePolicy.objects.get(request_type=self.request_type)
        return timedelta(days=policy.internal_target_days)

    def time_remaining(self) -> timedelta:
        return self.response_due_at - timezone.now()

    @property
    def major_status(self) -> str:
        if self.state in {
            self.State.CLOSED,
            self.State.REJECTED,
            self.State.WITHDRAWN,
        }:
            return "Completed"
        if self.state == self.State.SUBMITTED:
            return "Submitted"
        return "In Progress"

    @property
    def workflow_substatus(self) -> str:
        if self.major_status == "Completed":
            if self.resolution_status == self.ResolutionStatus.RETAINED:
                return self.ResolutionStatus.REJECTED.label
            return self.get_resolution_status_display()
        if self.state == self.State.ACCEPTED:
            return self.State.DEPT_SEARCH.label
        if self.state == self.State.ESCALATED:
            return self.State.LEGAL_REVIEW.label
        return self.get_state_display()

    @property
    def latest_response_version(self) -> DSRResponseVersion | None:
        return self.response_versions.order_by("-version").first()

    @property
    def requires_patient_response(self) -> bool:
        return self.state == self.State.NEEDS_INFORMATION

    @property
    def can_withdraw(self) -> bool:
        return self.state not in {
            self.State.CLOSED,
            self.State.REJECTED,
            self.State.WITHDRAWN,
        } and not self.has_ever_published_response()

    @property
    def is_overdue(self) -> bool:
        return (
            self.state
            in {
                self.State.SUBMITTED,
                self.State.ACCEPTED,
                self.State.DEPT_SEARCH,
                self.State.PRIVACY_REVIEW,
                self.State.LEGAL_REVIEW,
                self.State.RESPONSE_PREP,
                self.State.NEEDS_INFORMATION,
                self.State.ESCALATED,
            }
            and (
                self.response_due_at < timezone.now()
                or (
                    self.legal_deadline_at is not None
                    and self.legal_deadline_at < timezone.now()
                )
            )
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
        latest_version = self.latest_response_version
        return bool(
            latest_version is not None
            and self.response_versions.filter(
                pk=latest_version.pk,
                publication__isnull=False,
            ).exists()
        )

    def has_ever_published_response(self) -> bool:
        return self.response_versions.filter(publication__isnull=False).exists()

    def review_snapshot(self, response_version: DSRResponseVersion) -> dict[str, object]:
        searches = [
            list(search)
            for search in self.source_searches.order_by("pk").values_list(
                "source_system",
                "status",
                "summary",
                "evidence_reference",
            )
        ]
        decisions = [
            list(decision)
            for decision in self.decisions.order_by("pk").values_list(
                "category",
                "outcome",
                "rationale",
                "recorded_by",
            )
        ]
        content_digest = hashlib.sha256(
            response_version.content.encode("utf-8")
        ).hexdigest()
        return {
            "request_type": self.request_type,
            "description": self.description,
            "resolution_status": self.resolution_status,
            "duplicate_of": self.duplicate_of_id,
            "independent_review_required": self.independent_review_required,
            "independent_review_reason": self.independent_review_reason,
            "source_searches": searches,
            "decisions": decisions,
            "response_version_id": response_version.pk,
            "response_content_sha256": content_digest,
        }

    def has_valid_legal_review(self, resolution_status: str | None = None) -> bool:
        review_request = self.legal_review_requests.order_by("-pk").first()
        if review_request is None:
            return False
        decision = getattr(review_request, "decision", None)
        latest_version = self.latest_response_version
        if (
            decision is None
            or decision.outcome != DSRLegalReviewDecision.Outcome.APPROVED
            or latest_version is None
            or review_request.response_version_id != latest_version.pk
            or (
                resolution_status is not None
                and review_request.proposed_outcome != resolution_status
            )
            or review_request.case_snapshot != self.review_snapshot(latest_version)
        ):
            return False
        return True

    def independent_review_required_for(self, resolution_status: str) -> bool:
        return self.independent_review_required or resolution_status in {
            self.ResolutionStatus.REJECTED,
            self.ResolutionStatus.RETAINED,
            self.ResolutionStatus.PARTIALLY_FULFILLED,
        }

    def flag_for_independent_review(self, *, reason: str) -> None:
        if self.major_status == "Completed":
            raise TransitionNotAllowed("Completed cases cannot be reclassified.")
        if self.has_published_response():
            raise TransitionNotAllowed(
                "A published case outcome cannot be reclassified for review."
            )
        self.independent_review_required = True
        self.independent_review_reason = reason

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
        target=State.RESPONSE_PREP,
    )
    def mark_for_duplicate(
        self,
        *,
        actor_id: str,
        reason: str,
        duplicate_of: DSRRequest,
    ) -> None:
        if (
            duplicate_of.pk == self.pk
            or duplicate_of.patient_id != self.patient_id
            or duplicate_of.request_type != self.request_type
            or duplicate_of.duplicate_of_id is not None
        ):
            raise TransitionNotAllowed(
                "A duplicate must point to a different canonical case for the same requester and request type."
            )
        self._set_transition_context(actor_id, reason)
        self.duplicate_of = duplicate_of
        self.resolution_status = self.ResolutionStatus.DUPLICATE

    @transition(field=state, source=State.SUBMITTED, target=State.ACCEPTED)
    def accept_submission(self, *, actor_id: str, reason: str = "") -> None:
        """Accept requests authenticated by the Patient Portal."""
        self._set_transition_context(actor_id, reason)

    @transition(
        field=state,
        source=[State.SUBMITTED, State.ACCEPTED],
        target=State.DEPT_SEARCH,
    )
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

    @transition(
        field=state,
        source=State.RESPONSE_PREP,
        target=State.LEGAL_REVIEW,
    )
    def request_legal_review(
        self,
        *,
        actor_id: str,
        reason: str,
        proposed_outcome: str,
    ) -> None:
        self._set_transition_context(actor_id, reason)
        self.legal_review_completed = False

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
    def approve_legal_review(self, *, actor_id: str, reason: str) -> None:
        self._set_transition_context(actor_id, reason)
        self.legal_review_completed = True

    @transition(
        field=state,
        source=State.LEGAL_REVIEW,
        target=State.RESPONSE_PREP,
    )
    def return_from_legal_review(
        self, *, actor_id: str, reason: str
    ) -> None:
        self._set_transition_context(actor_id, reason)
        self.legal_review_completed = False

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
        if (
            self.independent_review_required_for(resolution_status)
            and not self.has_valid_legal_review(resolution_status)
        ):
            raise TransitionNotAllowed(
                "This outcome requires a current, independent review."
            )
        latest_decision = self.decisions.filter(category="overall").order_by("-pk").first()
        if latest_decision is None or latest_decision.outcome != resolution_status:
            raise TransitionNotAllowed(
                "The closing outcome must match the latest recorded overall decision."
            )
        self._set_transition_context(actor_id, reason)
        self.resolution_status = resolution_status
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
        target=State.CLOSED,
    )
    def close_as_duplicate(
        self,
        *,
        actor_id: str,
        reason: str,
        duplicate_of: DSRRequest,
    ) -> None:
        latest_version = self.latest_response_version
        if not self.has_published_response() or latest_version is None:
            raise TransitionNotAllowed(
                "Publish the duplicate outcome to the patient portal before closing."
            )
        if (
            duplicate_of.duplicate_of_id is not None
            or self.duplicate_of_id != duplicate_of.pk
            or self.resolution_status != self.ResolutionStatus.DUPLICATE
            or latest_version.case_snapshot.get("resolution")
            != self.get_resolution_status_display()
            or latest_version.case_snapshot.get("duplicate_of") != duplicate_of.pk
        ):
            raise TransitionNotAllowed(
                "Finalize and publish a duplicate outcome linked to a canonical case before closing."
            )
        if (
            self.independent_review_required
            and not self.has_valid_legal_review(self.ResolutionStatus.DUPLICATE)
        ):
            raise TransitionNotAllowed(
                "This duplicate outcome requires current independent review before closure."
            )
        self._set_transition_context(actor_id, reason)
        self.duplicate_of = duplicate_of
        self.resolution_status = self.ResolutionStatus.DUPLICATE
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
        conditions=[lambda instance: not instance.has_ever_published_response()],
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


class DSRDeadlinePolicy(models.Model):
    request_type = models.CharField(
        max_length=32,
        choices=DSRRequest.RequestType.choices,
        unique=True,
    )
    internal_target_days = models.PositiveSmallIntegerField(
        validators=(MinValueValidator(1), MaxValueValidator(3650))
    )
    legal_deadline_days = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=(MinValueValidator(1), MaxValueValidator(3650)),
    )
    updated_by = models.CharField(max_length=128, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("request_type",)


class DSRDeadlinePolicyAudit(models.Model):
    policy = models.ForeignKey(
        DSRDeadlinePolicy,
        on_delete=models.PROTECT,
        related_name="audit_entries",
    )
    actor_id = models.CharField(max_length=128)
    changed_values = models.JSONField()
    reason = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("timestamp", "pk")

    def save(self, *args: object, **kwargs: object) -> None:
        if self.pk is not None:
            raise ValueError("Deadline policy audit entries are append-only")
        super().save(*args, **kwargs)


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
        INDEPENDENT_REVIEW_REQUIRED = (
            "INDEPENDENT_REVIEW_REQUIRED",
            "Independent review required",
        )
        LEGAL_REVIEW_REQUESTED = "LEGAL_REVIEW_REQUESTED", "Legal review requested"
        LEGAL_REVIEW_DECIDED = "LEGAL_REVIEW_DECIDED", "Legal review decision recorded"
        TASK_CREATED = "TASK_CREATED", "Review task created"
        TASK_UPDATED = "TASK_UPDATED", "Review task updated"
        DECISION_RECORDED = "DECISION_RECORDED", "Decision recorded"
        PATIENT_UPDATE_RECORDED = "PATIENT_UPDATE_RECORDED", "Patient update recorded"
        PATIENT_RESPONSE_RECORDED = "PATIENT_RESPONSE_RECORDED", "Patient response recorded"
        CASES_EXPORTED = "CASES_EXPORTED", "Case included in a DPO export"
        SOURCE_SEARCH_RECORDED = "SOURCE_SEARCH_RECORDED", "Source search recorded"
        RESPONSE_FINALIZED = "RESPONSE_FINALIZED", "Response finalized"
        RESPONSE_PUBLISHED = "RESPONSE_PUBLISHED", "Response published to portal"
        DEADLINE_OVERDUE = "DEADLINE_OVERDUE", "Deadline passed"

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
        DUPLICATE = "DUPLICATE", "Duplicate"
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


class DSRLegalReviewRequest(models.Model):
    dsr = models.ForeignKey(
        DSRRequest,
        on_delete=models.PROTECT,
        related_name="legal_review_requests",
    )
    requested_by = models.CharField(max_length=128)
    request_reason = models.TextField()
    proposed_outcome = models.CharField(
        max_length=32,
        choices=DSRRequest.ResolutionStatus.choices,
    )
    response_version = models.ForeignKey(
        "dsr.DSRResponseVersion",
        on_delete=models.PROTECT,
        related_name="legal_review_requests",
    )
    case_snapshot = models.JSONField()
    requested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("requested_at", "pk")

    def save(self, *args: object, **kwargs: object) -> None:
        if self.pk is not None:
            raise ValueError("Legal review requests are append-only")
        super().save(*args, **kwargs)


class DSRLegalReviewDecision(models.Model):
    class Outcome(models.TextChoices):
        APPROVED = "APPROVED", "Approved"
        CHANGES_REQUESTED = "CHANGES_REQUESTED", "Changes requested"

    review_request = models.OneToOneField(
        DSRLegalReviewRequest,
        on_delete=models.PROTECT,
        related_name="decision",
    )
    reviewer_id = models.CharField(max_length=128)
    outcome = models.CharField(max_length=24, choices=Outcome.choices)
    rationale = models.TextField()
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("decided_at", "pk")

    def save(self, *args: object, **kwargs: object) -> None:
        if self.pk is not None:
            raise ValueError("Legal review decisions are append-only")
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