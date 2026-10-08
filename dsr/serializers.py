from __future__ import annotations

import re

from rest_framework import serializers

from .models import (
    DSRCommunication,
    DSRDecision,
    DSRRequest,
    DSRResponsePublication,
    DSRResponseVersion,
    DSRReviewTask,
    DSRSourceSearch,
)


class DSRSubmitSerializer(serializers.ModelSerializer):
    class Meta:
        model = DSRRequest
        fields = ("request_type", "description", "department")


class DSRStatusSerializer(serializers.ModelSerializer):
    time_remaining_seconds = serializers.SerializerMethodField()
    response_due_at = serializers.DateTimeField(read_only=True)
    state = serializers.CharField(source="get_state_display", read_only=True)
    resolution_status = serializers.CharField(
        source="get_resolution_status_display", read_only=True
    )

    class Meta:
        model = DSRRequest
        fields = (
            "id", "request_type", "description", "department", "state",
            "assigned_to", "resolution_status",
            "submitted_at", "response_due_at", "time_remaining_seconds",
            "closed_at",
        )

    def get_time_remaining_seconds(self, obj: DSRRequest) -> int:
        return max(0, int(obj.time_remaining().total_seconds()))


class DSRTransitionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=(
        "start_departmental_search", "send_to_privacy_review",
        "approve_privacy_review", "request_legal_review", "approve_legal_review",
        "request_information", "resume_escalated", "approve_and_close",
        "reject", "escalate",
    ))
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    resolution_status = serializers.ChoiceField(
        choices=(
            DSRRequest.ResolutionStatus.FULFILLED,
            DSRRequest.ResolutionStatus.PARTIALLY_FULFILLED,
            DSRRequest.ResolutionStatus.RETAINED,
        ),
        required=False,
        allow_blank=True,
    )

    def validate(self, attrs: dict[str, str]) -> dict[str, str]:
        if (
            attrs["action"] in {
                "reject",
                "escalate",
                "approve_and_close",
                "request_information",
                "resume_escalated",
            }
            and not attrs["reason"].strip()
        ):
            raise serializers.ValidationError(
                {
                    "reason": (
                        "A reason is required when rejecting, escalating, "
                        "requesting information, resuming an escalation, "
                        "or closing a request."
                    )
                }
            )
        if attrs["action"] == "approve_and_close" and not attrs.get("resolution_status"):
            raise serializers.ValidationError(
                {"resolution_status": "Select the outcome before closing the request."}
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