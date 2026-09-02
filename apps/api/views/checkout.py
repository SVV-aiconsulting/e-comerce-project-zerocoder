"""Превью оформления заказа."""
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.auth import AdapterTokenAuthentication
from apps.api.helpers import get_active_cart, resolve_customer_context
from apps.api.serializers.checkout import (
    CheckoutPreviewRequestSerializer,
    CheckoutPreviewResponseSerializer,
)
from apps.delivery.checkout import CheckoutDeliveryService


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
        preview = CheckoutDeliveryService.preview(
            cart=cart,
            customer=customer,
            receiving_type=data["receiving_type"],
            delivery_address=data.get("delivery_address", ""),
            payment_method=data.get("payment_method"),
        )
        totals = preview.totals
        quote = preview.quote

        response_data = {
            "items_total": totals.items_total,
            "discount_amount": totals.discount_amount,
            "delivery_cost": totals.delivery_cost,
            "total_amount": totals.total_amount,
            "free_delivery": totals.free_delivery,
            "delivery_quote_id": quote.pk if quote else None,
            "delivery_days": quote.delivery_days if quote else None,
            "delivery_provider": quote.provider if quote else "",
            "delivery_address": quote.destination_address if quote else "",
        }
        response_serializer = CheckoutPreviewResponseSerializer(data=response_data)
        response_serializer.is_valid(raise_exception=True)
        return Response(response_serializer.validated_data)
