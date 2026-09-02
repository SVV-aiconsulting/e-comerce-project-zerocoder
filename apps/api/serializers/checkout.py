"""Сериализаторы checkout."""
from rest_framework import serializers

from apps.api.serializers.common import ChannelContextSerializer
from apps.common.enums import PaymentMethod, ReceivingType


class CheckoutPreviewRequestSerializer(ChannelContextSerializer):
    customer_id = serializers.IntegerField()
    receiving_type = serializers.ChoiceField(choices=ReceivingType.values)
    delivery_address = serializers.CharField(required=False, allow_blank=True)
    payment_method = serializers.ChoiceField(
        choices=PaymentMethod.values,
        required=False,
        default=PaymentMethod.CARD_PREPAYMENT,
    )


class CheckoutPreviewResponseSerializer(serializers.Serializer):
    items_total = serializers.DecimalField(max_digits=12, decimal_places=2)
    discount_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    delivery_cost = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    free_delivery = serializers.BooleanField()
    delivery_quote_id = serializers.IntegerField(required=False, allow_null=True)
    delivery_days = serializers.IntegerField(required=False, allow_null=True)
    delivery_provider = serializers.CharField(required=False, allow_blank=True)
    delivery_address = serializers.CharField(required=False, allow_blank=True)
