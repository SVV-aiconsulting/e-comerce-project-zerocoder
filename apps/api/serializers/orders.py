"""Сериализаторы заказов."""
from rest_framework import serializers

from apps.api.serializers.common import ChannelContextSerializer
from apps.common.enums import PaymentMethod, ReceivingType, TimeInterval
from apps.orders.models import Order, OrderItem


class CreateOrderRequestSerializer(ChannelContextSerializer):
    customer_id = serializers.IntegerField()
    receiving_type = serializers.ChoiceField(choices=ReceivingType.values)
    payment_method = serializers.ChoiceField(choices=PaymentMethod.values)
    desired_date = serializers.DateField(required=False, allow_null=True)
    desired_time_interval = serializers.ChoiceField(
        choices=TimeInterval.values,
        required=False,
        allow_blank=True,
    )
    delivery_address = serializers.CharField(required=False, allow_blank=True)
    customer_comment = serializers.CharField(required=False, allow_blank=True)
    customer_email = serializers.EmailField(required=False, allow_blank=True)
    is_new_customer = serializers.BooleanField(required=False, default=False)
    delivery_quote_id = serializers.IntegerField(required=False, allow_null=True)


class OrderItemSerializer(serializers.ModelSerializer):
    unit_label = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = (
            "product_name_snapshot",
            "product_unit_snapshot",
            "unit_label",
            "quantity",
            "unit_price",
            "total_price",
        )

    def get_unit_label(self, obj: OrderItem) -> str:
        from apps.common.enums import ProductUnit

        return dict(ProductUnit.choices).get(obj.product_unit_snapshot, obj.product_unit_snapshot)


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    order_status_label = serializers.CharField(source="get_order_status_display", read_only=True)
    payment_status_label = serializers.CharField(source="get_payment_status_display", read_only=True)
    payment_method_label = serializers.CharField(source="get_payment_method_display", read_only=True)
    receiving_type_label = serializers.CharField(source="get_receiving_type_display", read_only=True)

    class Meta:
        model = Order
        fields = (
            "public_number",
            "customer_code_snapshot",
            "customer_name_snapshot",
            "customer_phone_snapshot",
            "customer_email_snapshot",
            "channel",
            "source_external_user_id_snapshot",
            "is_new_customer",
            "receiving_type",
            "receiving_type_label",
            "desired_date",
            "desired_time_interval",
            "delivery_address",
            "customer_comment",
            "items_total",
            "discount_amount",
            "delivery_cost",
            "total_amount",
            "order_status",
            "order_status_label",
            "payment_status",
            "payment_status_label",
            "payment_method",
            "payment_method_label",
            "items",
            "created_at",
        )


class OrderListSerializer(serializers.ModelSerializer):
    order_status_label = serializers.CharField(source="get_order_status_display", read_only=True)

    class Meta:
        model = Order
        fields = (
            "public_number",
            "total_amount",
            "order_status",
            "order_status_label",
            "created_at",
        )
