"""Корзина покупок."""
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.auth import AdapterTokenAuthentication
from apps.api.exceptions import ProductInactive, ProductNotFound
from apps.api.helpers import get_active_cart, resolve_customer_context
from apps.api.serializers.cart import CartItemUpdateSerializer, CartSerializer
from apps.api.serializers.common import ChannelContextSerializer
from apps.carts.services import CartService
from apps.catalog.services import CatalogService


class CartView(APIView):
    """Получить или создать активную корзину."""

    authentication_classes = [AdapterTokenAuthentication]
    permission_classes = []

    def get(self, request):
        serializer = ChannelContextSerializer(data=request.query_params)
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
        return Response(CartSerializer(cart, context={"request": request}).data)


class CartItemView(APIView):
    """Установить или удалить позицию корзины."""

    authentication_classes = [AdapterTokenAuthentication]
    permission_classes = []

    def put(self, request, product_id: int):
        serializer = CartItemUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        customer = resolve_customer_context(
            channel=data["channel"],
            external_user_id=data["external_user_id"],
            customer_id=data.get("customer_id"),
        )

        product = CatalogService.get_product(product_id=product_id)
        if product is None:
            raise ProductNotFound()
        if not product.is_active:
            raise ProductInactive()

        cart = get_active_cart(
            channel=data["channel"],
            external_user_id=data["external_user_id"],
            customer=customer,
        )

        quantity = data["quantity"]
        if quantity <= 0:
            CartService.remove_item(cart, product)
        else:
            CartService.set_item_quantity(cart, product, quantity)

        cart = get_active_cart(
            channel=data["channel"],
            external_user_id=data["external_user_id"],
            customer=customer,
        )
        return Response(CartSerializer(cart, context={"request": request}).data)

    def delete(self, request, product_id: int):
        serializer = ChannelContextSerializer(data=request.data or request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        customer = resolve_customer_context(
            channel=data["channel"],
            external_user_id=data["external_user_id"],
            customer_id=data.get("customer_id"),
        )

        product = CatalogService.get_product(product_id=product_id)
        if product is None:
            raise ProductNotFound()

        cart = get_active_cart(
            channel=data["channel"],
            external_user_id=data["external_user_id"],
            customer=customer,
        )
        CartService.remove_item(cart, product)
        cart = get_active_cart(
            channel=data["channel"],
            external_user_id=data["external_user_id"],
            customer=customer,
        )
        return Response(CartSerializer(cart, context={"request": request}).data)


class CartClearView(APIView):
    """Очистить корзину."""

    authentication_classes = [AdapterTokenAuthentication]
    permission_classes = []

    def delete(self, request):
        serializer = ChannelContextSerializer(data=request.data or request.query_params)
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
        CartService.clear(cart)
        cart = get_active_cart(
            channel=data["channel"],
            external_user_id=data["external_user_id"],
            customer=customer,
        )
        return Response(CartSerializer(cart, context={"request": request}).data)
