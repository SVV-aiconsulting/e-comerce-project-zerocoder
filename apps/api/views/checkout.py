"""Превью оформления заказа."""
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.auth import AdapterTokenAuthentication
from apps.api.helpers import get_active_cart, resolve_customer_context
from apps.api.serializers.checkout import (
    CheckoutPreviewRequestSerializer,
    CheckoutPreviewResponseSerializer,
)
from apps.carts.services import CartService
from apps.orders.pricing import PricingService


class CheckoutPreviewView(APIView):
    """Расчёт сумм заказа до оформления."""

    authentication_classes = [AdapterTokenAuthentication]
    permission_classes = []

    def post(self, request):
        serializer = CheckoutPreviewRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        customer = resolve_customer_context(
            channel=data["channel"],
            external_user_id=data["external_user_id"],
            customer_id=data["customer_id"],
        )
        cart = get_active_cart(
            channel=data["channel"],
            external_user_id=data["external_user_id"],
            customer=customer,
        )
        CartService.validate_cart_for_order(cart)
        cart_items = list(CartService.get_contents(cart))

        totals = PricingService.calculate_order_totals(
            customer=customer,
            cart_items=cart_items,
            receiving_type=data["receiving_type"],
        )

        response_data = {
            "items_total": totals.items_total,
            "discount_amount": totals.discount_amount,
            "delivery_cost": totals.delivery_cost,
            "total_amount": totals.total_amount,
            "free_delivery": totals.free_delivery,
        }
        response_serializer = CheckoutPreviewResponseSerializer(data=response_data)
        response_serializer.is_valid(raise_exception=True)
        return Response(response_serializer.validated_data)
