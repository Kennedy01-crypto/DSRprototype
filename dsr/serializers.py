from __future__ import annotations

from rest_framework import serializers

from .models import DSRRequest


class DSRSubmitSerializer(serializers.ModelSerializer):
    class Meta:
        model = DSRRequest
        fields = ("request_type", "description", "department")


class DSRStatusSerializer(serializers.ModelSerializer):
    time_remaining_seconds = serializers.SerializerMethodField()
    statutory_deadline = serializers.DateTimeField(read_only=True)
    state = serializers.CharField(source="get_state_display", read_only=True)

    class Meta:
        model = DSRRequest
        fields = (
            "id", "request_type", "description", "department", "state",
            "submitted_at", "statutory_deadline", "time_remaining_seconds",
            "closed_at",
        )

    def get_time_remaining_seconds(self, obj: DSRRequest) -> int:
        return max(0, int(obj.time_remaining().total_seconds()))


class DSRTransitionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=(
        "start_departmental_search", "approve_privacy_review", "request_legal_review",
        "approve_legal_review", "approve_and_close", "reject", "escalate",
    ))
    reason = serializers.CharField(required=False, allow_blank=True, default="")