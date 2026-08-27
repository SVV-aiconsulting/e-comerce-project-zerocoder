"""Сериализаторы единой точки приёма событий от frontend-адаптеров."""
import json

from rest_framework import serializers

from apps.common.enums import Channel
from apps.intake.enums import InboundEventKind

MAX_RAW_PAYLOAD_BYTES = 64 * 1024


class InboundEventRequestSerializer(serializers.Serializer):
    channel = serializers.ChoiceField(choices=Channel.values)
    external_event_id = serializers.CharField(max_length=255)
    external_user_id = serializers.CharField(max_length=255)
    conversation_key = serializers.CharField(max_length=255)
    customer_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    kind = serializers.ChoiceField(
        choices=InboundEventKind.values,
        required=False,
        default=InboundEventKind.MESSAGE,
    )
    raw_text = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        trim_whitespace=False,
        max_length=20_000,
    )
    raw_payload = serializers.JSONField(required=False, default=dict)
    payload_schema_version = serializers.IntegerField(
        required=False,
        default=1,
        min_value=1,
        max_value=32_767,
    )

    def validate_raw_payload(self, value):
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        if len(encoded) > MAX_RAW_PAYLOAD_BYTES:
            raise serializers.ValidationError("Payload превышает 64 КБ")
        return value


class InboundEventLookupSerializer(serializers.Serializer):
    channel = serializers.ChoiceField(choices=Channel.values)
    external_user_id = serializers.CharField(max_length=255)
