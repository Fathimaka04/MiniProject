"""Serializers for the gaze app API."""
from rest_framework import serializers

from ..models import CaregiverMessage, Request


class RequestCreateSerializer(serializers.ModelSerializer):
    """Input for POST /api/requests/."""

    class Meta:
        model = Request
        fields = ("phrase", "is_emergency", "pain_level")
        extra_kwargs = {"is_emergency": {"default": False}}

    def validate_phrase(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Phrase cannot be empty.")
        return value


class RequestStatusSerializer(serializers.ModelSerializer):
    """A request and whether a caregiver has acknowledged it."""

    acknowledged = serializers.BooleanField(source="is_acknowledged", read_only=True)
    acknowledged_by = serializers.SerializerMethodField()
    message = serializers.SerializerMethodField()

    class Meta:
        model = Request
        fields = (
            "id", "phrase", "is_emergency", "pain_level", "timestamp",
            "acknowledged", "acknowledged_by", "acknowledged_at", "message",
        )

    def get_acknowledged_by(self, obj):
        return obj.acknowledged_by.display_name if obj.acknowledged_by else None

    def get_message(self, obj):
        """Text the gaze app can show or speak to the patient."""
        if obj.is_acknowledged:
            return "Help is on the way" if obj.is_emergency else "Your caregiver has seen your request"
        return "Waiting for a caregiver"


class CaregiverMessageSerializer(serializers.ModelSerializer):
    """A caregiver message delivered to the gaze app."""

    sender = serializers.SerializerMethodField()

    class Meta:
        model = CaregiverMessage
        fields = ("id", "text", "sender", "created_at")

    def get_sender(self, obj):
        return obj.caregiver.display_name if obj.caregiver else "Caregiver"


class PairDeviceSerializer(serializers.Serializer):
    """Input for POST /api/pair/."""

    code = serializers.CharField(max_length=20)
    device_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
