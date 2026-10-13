from __future__ import annotations

import re

from django.utils import timezone
from rest_framework import serializers

from .models import (
    DSRCommunication,
    DSRDecision,
    DSRDeadlinePolicy,
    DSRLegalReviewDecision,
    DSRLegalReviewRequest,
    DSRRequest,
    DSRResponsePublication,
    DSRResponseVersion,
    DSRReviewTask,
    DSRSourceSearch,
)


class DSRSubmitSerializer(serializers.ModelSerializer):
    reconsideration_of = serializers.PrimaryKeyRelatedField(
        queryset=DSRRequest.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = DSRRequest
        fields = (
            "request_type",
            "description",
            "department",
            "reconsideration_of",
        )

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        previous_request = attrs.get("reconsideration_of")
        if previous_request is None:
            return attrs
        if not isinstance(previous_request, DSRRequest):
            raise serializers.ValidationError(
                {"reconsideration_of": "Select a valid completed request."}
            )
        if previous_request.patient_id != self.context["patient_id"]:
            raise serializers.ValidationError(
                {"reconsideration_of": "The selected request does not belong to you."}
            )
        if previous_request.major_status != "Completed":
            raise serializers.ValidationError(
                {"reconsideration_of": "Only completed requests can be reconsidered."}
            )
        if previous_request.duplicate_of_id is not None:
            raise serializers.ValidationError(
                {
                    "reconsideration_of": (
                        "Choose the original request rather than a duplicate case."
                    )
                }
            )
        if previous_request.request_type != attrs.get("request_type"):
            raise serializers.ValidationError(
                {"reconsideration_of": "A reconsideration must use the same request type."}
            )
        return attrs


class DSRStatusSerializer(serializers.ModelSerializer):
    time_remaining_seconds = serializers.SerializerMethodField()
    legal_time_remaining_seconds = serializers.SerializerMethodField()
    response_due_at = serializers.DateTimeField(read_only=True)
    request_type_label = serializers.CharField(
        source="get_request_type_display", read_only=True
    )
    state = serializers.SerializerMethodField()
    resolution_status = serializers.SerializerMethodField()
    legal_deadline_at = serializers.DateTimeField(read_only=True)
    requires_patient_response = serializers.BooleanField(read_only=True)

    class Meta:
        model = DSRRequest
        fields = (
            "id", "request_type", "request_type_label", "description",
            "department", "state",
            "assigned_to", "resolution_status", "legal_deadline_at",
            "requires_patient_response", "legal_time_remaining_seconds",
            "duplicate_of", "reconsideration_of", "submitted_at",
            "response_due_at", "time_remaining_seconds",
            "closed_at",
        )

    def get_state(self, obj: DSRRequest) -> str:
        return obj.major_status

    def get_time_remaining_seconds(self, obj: DSRRequest) -> int:
        return max(0, int(obj.time_remaining().total_seconds()))

    def get_legal_time_remaining_seconds(self, obj: DSRRequest) -> int | None:
        if obj.legal_deadline_at is None:
            return None
        return max(
            0,
            int((obj.legal_deadline_at - timezone.now()).total_seconds()),
        )

    def get_resolution_status(self, obj: DSRRequest) -> str:
        if obj.resolution_status == DSRRequest.ResolutionStatus.RETAINED:
            return "Rejected (Refused)"
        return obj.get_resolution_status_display()


class DSRDPOStatusSerializer(DSRStatusSerializer):
    sub_status = serializers.CharField(source="workflow_substatus", read_only=True)
    legal_review_completed = serializers.SerializerMethodField()
    proposed_outcome = serializers.SerializerMethodField()
    independent_review_required = serializers.BooleanField(read_only=True)
    independent_review_reason = serializers.CharField(read_only=True)

    class Meta(DSRStatusSerializer.Meta):
        fields = (
            *DSRStatusSerializer.Meta.fields,
            "sub_status",
            "legal_review_completed",
            "proposed_outcome",
            "independent_review_required",
            "independent_review_reason",
        )

    def get_legal_review_completed(self, obj: DSRRequest) -> bool:
        decision = obj.decisions.filter(category="overall").order_by("-pk").first()
        return bool(
            decision is not None
            and obj.has_valid_legal_review(decision.outcome)
        )

    def get_proposed_outcome(self, obj: DSRRequest) -> str | None:
        decision = obj.decisions.filter(category="overall").order_by("-pk").first()
        return decision.outcome if decision else None


class DSRLegalReviewDecisionReadSerializer(serializers.ModelSerializer):
    outcome = serializers.CharField(source="get_outcome_display", read_only=True)

    class Meta:
        model = DSRLegalReviewDecision
        fields = ("reviewer_id", "outcome", "rationale", "decided_at")


class DSRLegalReviewRequestReadSerializer(serializers.ModelSerializer):
    decision = DSRLegalReviewDecisionReadSerializer(read_only=True)

    class Meta:
        model = DSRLegalReviewRequest
        fields = (
            "id",
            "requested_by",
            "request_reason",
            "proposed_outcome",
            "response_version",
            "requested_at",
            "decision",
        )


class DSRDeadlinePolicySerializer(serializers.ModelSerializer):
    request_type_label = serializers.CharField(
        source="get_request_type_display", read_only=True
    )
    legal_deadline_days = serializers.IntegerField(
        allow_null=True, min_value=1, max_value=3650
    )
    internal_target_days = serializers.IntegerField(
        min_value=1, max_value=3650
    )

    class Meta:
        model = DSRDeadlinePolicy
        fields = (
            "request_type",
            "request_type_label",
            "internal_target_days",
            "legal_deadline_days",
            "updated_by",
            "updated_at",
        )
        read_only_fields = ("request_type", "updated_by", "updated_at")


class DSRDeadlinePolicyUpdateSerializer(serializers.Serializer):
    internal_target_days = serializers.IntegerField(min_value=1, max_value=3650)
    legal_deadline_days = serializers.IntegerField(
        allow_null=True, min_value=1, max_value=3650
    )
    reason = serializers.CharField(min_length=1, max_length=2000)

    def validate_reason(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("A reason is required.")
        return value.strip()


class DSRTransitionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=(
        "start_departmental_search", "send_to_privacy_review",
        "approve_privacy_review", "request_legal_review", "approve_legal_review",
        "return_from_legal_review", "request_information", "resume_escalated",
        "approve_and_close", "escalate", "close_as_duplicate",
        "flag_for_independent_review", "mark_for_duplicate",
    ))
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    duplicate_of = serializers.IntegerField(required=False, min_value=1)
    resolution_status = serializers.ChoiceField(
        choices=(
            DSRRequest.ResolutionStatus.FULFILLED,
            DSRRequest.ResolutionStatus.PARTIALLY_FULFILLED,
            DSRRequest.ResolutionStatus.REJECTED,
            DSRRequest.ResolutionStatus.RETAINED,
        ),
        required=False,
        allow_blank=True,
    )
    proposed_outcome = serializers.ChoiceField(
        choices=(
            DSRRequest.ResolutionStatus.FULFILLED,
            DSRRequest.ResolutionStatus.PARTIALLY_FULFILLED,
            DSRRequest.ResolutionStatus.REJECTED,
            DSRRequest.ResolutionStatus.RETAINED,
            DSRRequest.ResolutionStatus.DUPLICATE,
        ),
        required=False,
    )

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        if (
            attrs["action"] in {
                "escalate",
                "request_legal_review",
                "approve_and_close",
                "request_information",
                "resume_escalated",
                "close_as_duplicate",
                "approve_legal_review",
                "return_from_legal_review",
                "flag_for_independent_review",
                "mark_for_duplicate",
            }
            and not attrs["reason"].strip()
        ):
            raise serializers.ValidationError(
                {
                    "reason": (
                        "A reason is required for escalations, legal/management "
                        "review, information requests, resuming an escalation, "
                        "duplicate closures, and final outcomes."
                    )
                }
            )
        if attrs["action"] == "approve_and_close" and not attrs.get("resolution_status"):
            raise serializers.ValidationError(
                {"resolution_status": "Select the outcome before closing the request."}
            )
        if attrs["action"] in {
            "mark_for_duplicate",
            "close_as_duplicate",
        } and not attrs.get("duplicate_of"):
            raise serializers.ValidationError(
                {"duplicate_of": "Select the original request this duplicates."}
            )
        if attrs["action"] == "request_legal_review" and not attrs.get(
            "proposed_outcome"
        ):
            raise serializers.ValidationError(
                {"proposed_outcome": "Select the outcome being sent for review."}
            )
        return attrs


class DSRAssignmentSerializer(serializers.Serializer):
    assigned_to = serializers.CharField(max_length=128, allow_blank=True)

    def validate_assigned_to(self, value: str) -> str:
        if value and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
            raise serializers.ValidationError("Enter a valid mock staff ID.")
        return value


class DSRReviewTaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = DSRReviewTask
        fields = ("title", "details", "assigned_to")
        extra_kwargs = {
            "title": {"required": True},
            "details": {"required": False, "allow_blank": True},
            "assigned_to": {"required": False, "allow_blank": True},
        }


class DSRReviewTaskUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=DSRReviewTask.Status.choices)


class DSRDecisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DSRDecision
        fields = ("category", "outcome", "rationale")


class DSRCommunicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = DSRCommunication
        fields = ("message",)


class DSRSourceSearchCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = DSRSourceSearch
        fields = (
            "source_system",
            "status",
            "summary",
            "evidence_reference",
        )

    def validate(self, attrs: dict[str, str]) -> dict[str, str]:
        status_value = attrs.get("status", DSRSourceSearch.Status.PENDING)
        summary = attrs.get("summary", "").strip()
        if status_value != DSRSourceSearch.Status.PENDING and not summary:
            raise serializers.ValidationError(
                {"summary": "Record a short outcome for completed or blocked searches."}
            )
        return attrs


class DSRSourceSearchUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = DSRSourceSearch
        fields = ("status", "summary", "evidence_reference")
        extra_kwargs = {
            "status": {"required": False},
            "summary": {"required": False, "allow_blank": True},
            "evidence_reference": {"required": False, "allow_blank": True},
        }

    def validate(self, attrs: dict[str, str]) -> dict[str, str]:
        status_value = attrs.get("status", self.instance.status)
        summary = attrs.get("summary", self.instance.summary).strip()
        if status_value != DSRSourceSearch.Status.PENDING and not summary:
            raise serializers.ValidationError(
                {"summary": "Record a short outcome for completed or blocked searches."}
            )
        return attrs


class DSRResponseFinalizeSerializer(serializers.Serializer):
    content = serializers.CharField()

    def validate_content(self, value: str) -> str:
        content = value.strip()
        if not content:
            raise serializers.ValidationError("Final response content is required.")
        return content


class DSRWithdrawSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class DSRReviewTaskReadSerializer(serializers.ModelSerializer):
    status = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = DSRReviewTask
        fields = (
            "id",
            "title",
            "details",
            "assigned_to",
            "status",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
            "completed_at",
        )


class DSRDecisionReadSerializer(serializers.ModelSerializer):
    outcome = serializers.CharField(source="get_outcome_display", read_only=True)

    class Meta:
        model = DSRDecision
        fields = ("id", "category", "outcome", "rationale", "recorded_by", "created_at")


class DSRCommunicationReadSerializer(serializers.ModelSerializer):
    direction = serializers.CharField(source="get_direction_display", read_only=True)

    class Meta:
        model = DSRCommunication
        fields = ("id", "message", "direction", "created_by", "created_at")


class DSRSourceSearchReadSerializer(serializers.ModelSerializer):
    source_system = serializers.CharField(
        source="get_source_system_display", read_only=True
    )
    status = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = DSRSourceSearch
        fields = (
            "id",
            "source_system",
            "status",
            "summary",
            "evidence_reference",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        )


class DSRResponsePublicationReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = DSRResponsePublication
        fields = ("published_by", "published_at")


class DSRResponseVersionReadSerializer(serializers.ModelSerializer):
    publication = DSRResponsePublicationReadSerializer(read_only=True)

    class Meta:
        model = DSRResponseVersion
        fields = (
            "id",
            "version",
            "content",
            "case_snapshot",
            "finalized_by",
            "finalized_at",
            "publication",
        )


class DSRPublishedResponseReadSerializer(serializers.ModelSerializer):
    publication = DSRResponsePublicationReadSerializer(read_only=True)

    class Meta:
        model = DSRResponseVersion
        fields = ("version", "content", "publication")