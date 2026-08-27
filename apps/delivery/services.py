"""Бизнес-логика доставки."""
from decimal import Decimal

from apps.common.exceptions import DeliveryError
from apps.delivery.models import DeliveryRule


class DeliveryService:
    """Сервис расчёта стоимости доставки."""

    @staticmethod
    def get_active_rule() -> DeliveryRule | None:
        return DeliveryRule.objects.filter(is_active=True).order_by("-created_at").first()

    @staticmethod
    def calculate_delivery_cost(
        items_total: Decimal,
        rule: DeliveryRule | None = None,
        *,
        free_delivery: bool = False,
    ) -> Decimal:
        if free_delivery:
            return Decimal("0")

        if rule is None:
            rule = DeliveryService.get_active_rule()

        if rule is None:
            return Decimal("0")

        if items_total < rule.min_order_amount:
            raise DeliveryError(
                f"Минимальная сумма заказа для доставки: {rule.min_order_amount}"
            )

        if rule.free_delivery_from is not None and items_total >= rule.free_delivery_from:
            return Decimal("0")

        return rule.delivery_cost
