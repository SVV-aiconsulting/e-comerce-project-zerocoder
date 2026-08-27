"""Сериализаторы checkout."""
from rest_framework import serializers

from apps.api.serializers.common import ChannelContextSerializer
from apps.common.enums import ReceivingType


class CheckoutPreviewRequestSerializer(ChannelContextSerializer):
    customer_id = serializers.IntegerField()
    receiving_type = serializers.ChoiceField(choices=ReceivingType.values)


class CheckoutPreviewResponseSerializer(serializers.Serializer):
    items_total = serializers.DecimalField(max_digits=12, decimal_places=2)
    discount_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    delivery_cost = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    free_delivery = serializers.BooleanField()
