"""Бизнес-логика корзины."""
from decimal import Decimal

from django.db import transaction

from apps.carts.models import Cart, CartItem
from apps.catalog.models import Product
from apps.catalog.services import CatalogService
from apps.common.enums import CartStatus, Channel
from apps.common.exceptions import CartEmptyError, CartNotAvailableError, MinQuantityError
from apps.customers.models import Customer


class CartService:
    """Сервис операций с корзиной покупок."""

    @staticmethod
    def get_or_create_active_cart(
        *,
        channel: str,
        external_user_id: str,
        customer: Customer | None = None,
    ) -> Cart:
        cart = Cart.objects.filter(
            channel=channel,
            external_user_id=external_user_id,
            status=CartStatus.ACTIVE,
        ).first()
        if cart:
            if customer and cart.customer_id and cart.customer_id != customer.id:
                raise CartNotAvailableError("Корзина принадлежит другому клиенту")
            if customer and not cart.customer_id:
                cart.customer = customer
                cart.save(update_fields=["customer", "updated_at"])
            return cart

        return Cart.objects.create(
            channel=channel,
            external_user_id=external_user_id,
            customer=customer,
            status=CartStatus.ACTIVE,
        )

    @staticmethod
    def set_item_quantity(cart: Cart, product: Product, quantity: Decimal) -> CartItem:
        """Установить количество товара в корзине (заменяет текущее значение)."""
        CatalogService.check_availability(product, quantity)

        item, created = CartItem.objects.get_or_create(
            cart=cart,
            product=product,
            defaults={"quantity": quantity},
        )
        if not created:
            item.quantity = quantity
            item.save(update_fields=["quantity", "updated_at"])
        return item

    @staticmethod
    def add_item(cart: Cart, product: Product, quantity: Decimal) -> CartItem:
        """Устаревший алиас для set_item_quantity."""
        return CartService.set_item_quantity(cart, product, quantity)

    @staticmethod
    def update_quantity(cart: Cart, product: Product, quantity: Decimal) -> CartItem:
        CatalogService.check_availability(product, quantity)
        item = CartItem.objects.get(cart=cart, product=product)
        item.quantity = quantity
        item.save(update_fields=["quantity", "updated_at"])
        return item

    @staticmethod
    def remove_item(cart: Cart, product: Product) -> None:
        CartItem.objects.filter(cart=cart, product=product).delete()

    @staticmethod
    def clear(cart: Cart) -> None:
        cart.items.all().delete()

    @staticmethod
    def get_contents(cart: Cart):
        return cart.items.select_related("product").all()

    @staticmethod
    def validate_min_quantity(product: Product, quantity: Decimal) -> None:
        if quantity < product.min_quantity:
            raise MinQuantityError(product.name, product.min_quantity)

    @staticmethod
    def validate_cart_for_order(cart: Cart) -> None:
        items = list(CartService.get_contents(cart))
        if not items:
            raise CartEmptyError("Корзина пуста")
        for item in items:
            CatalogService.check_availability(item.product, item.quantity)

    @staticmethod
    @transaction.atomic
    def mark_as_ordered(cart: Cart) -> None:
        cart.status = CartStatus.ORDERED
        cart.save(update_fields=["status", "updated_at"])
