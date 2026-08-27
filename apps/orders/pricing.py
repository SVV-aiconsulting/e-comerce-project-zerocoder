"""Расчёт стоимости заказа."""
from dataclasses import dataclass
from decimal import Decimal

from apps.carts.models import CartItem
from apps.customers.models import Customer
from apps.delivery.services import DeliveryService
from apps.discounts.services import DiscountService


@dataclass
class OrderTotals:
    """Рассчитанные суммы заказа."""

    items_total: Decimal
    discount_amount: Decimal
    delivery_cost: Decimal
    total_amount: Decimal
    free_delivery: bool = False


class PricingService:
    """Координирует расчёт позиций, скидки и доставки."""

    @staticmethod
    def calculate_items_total(cart_items) -> Decimal:
        total = Decimal("0")
        for item in cart_items:
            total += item.product.base_price * item.quantity
        return total.quantize(Decimal("0.01"))

    @staticmethod
    def calculate_discount(customer: Customer, items_total: Decimal) -> tuple[Decimal, bool]:
        rule = DiscountService.select_applicable_rule(customer, items_total)
        discount = DiscountService.calculate_discount(rule, items_total)
        free_delivery = bool(rule and rule.free_delivery)
        return discount, free_delivery

    @staticmethod
    def calculate_delivery(items_total: Decimal, *, free_delivery: bool = False) -> Decimal:
        return DeliveryService.calculate_delivery_cost(
            items_total,
            free_delivery=free_delivery,
        )

    @staticmethod
    def calculate_total(
        items_total: Decimal,
        discount_amount: Decimal,
        delivery_cost: Decimal,
    ) -> Decimal:
        total = items_total - discount_amount + delivery_cost
        return max(total, Decimal("0")).quantize(Decimal("0.01"))

    @classmethod
    def calculate_order_totals(
        cls,
        *,
        customer: Customer,
        cart_items: list[CartItem],
        receiving_type: str | None = None,
    ) -> OrderTotals:
        from apps.common.enums import ReceivingType

        items_total = cls.calculate_items_total(cart_items)
        discount_amount, free_delivery = cls.calculate_discount(customer, items_total)

        if receiving_type == ReceivingType.PICKUP:
            delivery_cost = Decimal("0")
        else:
            delivery_cost = cls.calculate_delivery(items_total, free_delivery=free_delivery)

        total_amount = cls.calculate_total(
            items_total,
            discount_amount,
            delivery_cost,
        )
        return OrderTotals(
            items_total=items_total,
            discount_amount=discount_amount,
            delivery_cost=delivery_cost,
            total_amount=total_amount,
            free_delivery=free_delivery,
        )
