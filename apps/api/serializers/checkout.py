"""Сериализаторы checkout."""
from rest_framework import serializers

from apps.api.serializers.common import ChannelContextSerializer
from apps.common.enums import PaymentMethod, ReceivingType, TimeInterval


class CheckoutPreviewRequestSerializer(ChannelContextSerializer):
    customer_id = serializers.IntegerField()
    contact_phone = serializers.CharField(required=False, allow_blank=True)
    contact_email = serializers.EmailField(required=False, allow_blank=True)
    customer_comment = serializers.CharField(required=False, allow_blank=True)
    desired_date = serializers.DateField(required=False, allow_null=True)
    desired_time_interval = serializers.ChoiceField(
        choices=TimeInterval.values,
        required=False,
        allow_blank=True,
    )
    receiving_type = serializers.ChoiceField(choices=ReceivingType.values)
    delivery_address = serializers.CharField(required=False, allow_blank=True)
    payment_method = serializers.ChoiceField(
        choices=PaymentMethod.values,
        required=False,
        default=PaymentMethod.CARD_PREPAYMENT,
    )


class CheckoutPreviewResponseSerializer(serializers.Serializer):
    preview_id = serializers.UUIDField(required=False)
    expires_at = serializers.DateTimeField(required=False)
    items_total = serializers.DecimalField(max_digits=12, decimal_places=2)
    discount_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    delivery_cost = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    free_delivery = serializers.BooleanField()
    delivery_quote_id = serializers.IntegerField(required=False, allow_null=True)
    delivery_days = serializers.IntegerField(required=False, allow_null=True)
    delivery_provider = serializers.CharField(required=False, allow_blank=True)
    delivery_address = serializers.CharField(required=False, allow_blank=True)


class CheckoutStateSerializer(ChannelContextSerializer):
    receiving_type = serializers.ChoiceField(
        choices=ReceivingType.values, required=False, allow_blank=True
    )
    delivery_address = serializers.CharField(required=False, allow_blank=True)
    payment_method = serializers.ChoiceField(
        choices=PaymentMethod.values, required=False, allow_blank=True
    )
    customer_comment = serializers.CharField(required=False, allow_blank=True)
    contact_phone = serializers.CharField(required=False, allow_blank=True, max_length=32)
    contact_email = serializers.EmailField(required=False, allow_blank=True)
    desired_date = serializers.DateField(required=False, allow_null=True)
    desired_time_interval = serializers.ChoiceField(
        choices=TimeInterval.values,
        required=False,
        allow_blank=True,
    )
