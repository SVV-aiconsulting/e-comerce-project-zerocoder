"""Сериализаторы корзины."""
from decimal import Decimal

from rest_framework import serializers

from apps.api.serializers.catalog import ProductListSerializer
from apps.api.serializers.common import ChannelContextSerializer
from apps.carts.models import Cart, CartItem
from apps.orders.pricing import PricingService


class CartItemSerializer(serializers.ModelSerializer):
    product = ProductListSerializer(read_only=True)
    line_total = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = ("product", "quantity", "line_total")

    def get_line_total(self, obj: CartItem) -> str:
        total = (obj.product.base_price * obj.quantity).quantize(Decimal("0.01"))
        return str(total)


class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True, source="prefetched_items")
    items_total = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = (
            "id",
            "channel",
            "external_user_id",
            "customer_id",
            "status",
            "items",
            "items_total",
        )

    def get_items_total(self, obj: Cart) -> str:
        items = getattr(obj, "prefetched_items", obj.items.select_related("product").all())
        return str(PricingService.calculate_items_total(items))


class CartItemUpdateSerializer(ChannelContextSerializer):
    quantity = serializers.DecimalField(max_digits=10, decimal_places=3)
