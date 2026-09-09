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
        from apps.carts.checkout import CheckoutService
        order = CheckoutService.create_order(preview_id=data.get("preview_id"),
            channel=data["channel"], external_user_id=data["external_user_id"], customer=customer,
            receiving_type=data["receiving_type"], payment_method=data["payment_method"],
            delivery_address=data.get("delivery_address", ""),
            customer_comment=data.get("customer_comment", ""),
            status_source=StatusChangeSource.API)
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
        from apps.orders.access import OrderAccessService
        if not OrderAccessService.visible(channel=data["channel"], external_user_id=data["external_user_id"]).filter(pk=order.pk).exists():
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

        from apps.orders.access import OrderAccessService
        orders = OrderAccessService.visible(channel=data["channel"], external_user_id=data["external_user_id"]).filter(customer=customer).order_by("-created_at")
        return Response(OrderListSerializer(orders, many=True).data)
