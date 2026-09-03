"""Заказы."""

from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.auth import AdapterTokenAuthentication
from apps.api.exceptions import OrderAccessDenied, OrderNotFound
from apps.api.helpers import (
    get_active_cart,
    get_customer_by_code_or_raise,
    resolve_customer_context,
    resolve_customer_from_identity,
)
from apps.api.serializers.common import ChannelIdentitySerializer
from apps.api.serializers.orders import (
    CreateOrderRequestSerializer,
    OrderListSerializer,
    OrderSerializer,
)
from apps.common.enums import PaymentMethod, StatusChangeSource
from apps.delivery.checkout import CheckoutDeliveryService
from apps.orders import selectors as order_selectors
from apps.orders.services import OrderService
from apps.payments.exceptions import PaymentDataError


class CreateOrderView(APIView):
    """Создать заказ из активной корзины."""

    authentication_classes = [AdapterTokenAuthentication]
    permission_classes = []

    def post(self, request):
        serializer = CreateOrderRequestSerializer(data=request.data)
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
        if data["payment_method"] == PaymentMethod.CARD_PREPAYMENT and not (
            data.get("customer_email") or customer.email
        ):
            raise PaymentDataError(
                "Для онлайн-оплаты укажите email: на него ЮKassa отправит электронный чек"
            )
        quote = CheckoutDeliveryService.selected_quote(
            cart=cart,
            receiving_type=data["receiving_type"],
            delivery_address=data.get("delivery_address", ""),
            quote_id=data.get("delivery_quote_id"),
        )
        delivery_cost_override = CheckoutDeliveryService.delivery_cost_for_quote(
            cart=cart,
            customer=customer,
            quote=quote,
        )

        order = OrderService.create_order_from_cart(
            cart,
            customer=customer,
            channel=data["channel"],
            receiving_type=data["receiving_type"],
            payment_method=data["payment_method"],
            desired_date=data.get("desired_date"),
            desired_time_interval=data.get("desired_time_interval", ""),
            delivery_address=data.get("delivery_address", ""),
            customer_comment=data.get("customer_comment", ""),
            customer_email_snapshot=data.get("customer_email") or None,
            delivery_cost_override=delivery_cost_override,
            is_new_customer=data.get("is_new_customer", False),
            status_source=StatusChangeSource.API,
        )
        CheckoutDeliveryService.attach_quote(quote, order)

        order = order_selectors.get_order_by_number(order.public_number)
        return Response(OrderSerializer(order).data, status=201)


class OrderDetailView(APIView):
    """Детали заказа по public_number."""

    authentication_classes = [AdapterTokenAuthentication]
    permission_classes = []

    def get(self, request, public_number: str):
        serializer = ChannelIdentitySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        customer = resolve_customer_from_identity(
            channel=data["channel"],
            external_user_id=data["external_user_id"],
        )
        order = order_selectors.get_order_by_number(public_number)
        if order is None:
            raise OrderNotFound()
        if order.customer_id != customer.pk:
            raise OrderAccessDenied()
        return Response(OrderSerializer(order).data)


class CustomerOrdersView(APIView):
    """Последние заказы клиента."""

    authentication_classes = [AdapterTokenAuthentication]
    permission_classes = []

    def get(self, request, public_code: str):
        serializer = ChannelIdentitySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        identity_customer = resolve_customer_from_identity(
            channel=data["channel"],
            external_user_id=data["external_user_id"],
        )
        customer = get_customer_by_code_or_raise(public_code)
        if customer.pk != identity_customer.pk:
            raise OrderAccessDenied()

        orders = order_selectors.get_orders_for_customer(customer.pk)
        return Response(OrderListSerializer(orders, many=True).data)
