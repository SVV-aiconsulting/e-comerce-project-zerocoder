"""Превью оформления заказа."""
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.auth import AdapterTokenAuthentication
from apps.api.helpers import get_active_cart, resolve_customer_context
from apps.api.serializers.checkout import (
    CheckoutPreviewRequestSerializer,
    CheckoutPreviewResponseSerializer,
    CheckoutStateSerializer,
)
from apps.customers.validators import normalize_phone
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
        for field in cart.CHECKOUT_FIELDS:
            if field in data:
                setattr(cart, field, data[field])
        cart.contact_phone = data.get("contact_phone") or cart.contact_phone or customer.phone
        cart.contact_email = data.get("contact_email") or cart.contact_email or customer.email
        cart.save(update_fields=[*cart.CHECKOUT_FIELDS, "updated_at"])
        preview = CheckoutDeliveryService.preview(cart=cart, customer=customer,
            receiving_type=cart.receiving_type, delivery_address=cart.delivery_address,
            payment_method=cart.payment_method)
        from apps.carts.checkout import CheckoutService
        snapshot = CheckoutService.record(cart=cart, customer=customer, result=preview)
        totals = preview.totals
        quote = preview.quote

        response_data = {
            "preview_id": str(snapshot.public_id),
            "expires_at": snapshot.expires_at,
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


class CheckoutStateView(APIView):
    """Общее состояние незавершённого checkout для ручного UI и ассистента."""

    authentication_classes = [AdapterTokenAuthentication]
    permission_classes = []

    @staticmethod
    def _payload(cart):
        return {
            "receiving_type": cart.receiving_type,
            "delivery_address": cart.delivery_address,
            "payment_method": cart.payment_method,
            "customer_comment": cart.customer_comment,
            "contact_phone": cart.contact_phone,
            "contact_email": cart.contact_email,
            "desired_date": cart.desired_date,
            "desired_time_interval": cart.desired_time_interval,
        }

    def patch(self, request):
        serializer = CheckoutStateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        customer = resolve_customer_context(
            channel=data["channel"],
            external_user_id=data["external_user_id"],
            customer_id=data.get("customer_id"),
        )
        cart = get_active_cart(
            channel=data["channel"],
            external_user_id=data["external_user_id"],
            customer=customer,
        )
        changed = []
        for field in (
            "receiving_type",
            "delivery_address",
            "payment_method",
            "customer_comment",
            "contact_email",
            "desired_date",
            "desired_time_interval",
        ):
            if field in data and data[field] != getattr(cart, field):
                setattr(cart, field, data[field])
                changed.append(field)
        if "contact_phone" in data:
            try:
                value = normalize_phone(data["contact_phone"]) if data["contact_phone"] else ""
            except DjangoValidationError as exc:
                raise ValidationError({"contact_phone": "Некорректный номер телефона."}) from exc
            if value != cart.contact_phone:
                cart.contact_phone = value
                changed.append("contact_phone")
        if changed:
            cart.save(update_fields=[*changed, "updated_at"])
        return Response(self._payload(cart))
