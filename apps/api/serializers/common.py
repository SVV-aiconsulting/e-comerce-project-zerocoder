"""Общие serializers REST API."""
from rest_framework import serializers

from apps.common.enums import Channel


class ChannelContextSerializer(serializers.Serializer):
    """Контекст канала для операций с корзиной и заказами."""

    channel = serializers.ChoiceField(choices=Channel.values)
    external_user_id = serializers.CharField(max_length=128)
    customer_id = serializers.IntegerField(required=False, allow_null=True)


class ChannelIdentitySerializer(serializers.Serializer):
    """Контекст канала без customer_id (чтение заказов)."""

    channel = serializers.ChoiceField(choices=Channel.values)
    external_user_id = serializers.CharField(max_length=128)
