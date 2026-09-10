"""Бизнес-логика скидок."""
from decimal import Decimal

from django.utils import timezone

from apps.customers.models import Customer
from apps.discounts.models import DiscountRule


class DiscountService:
    """Сервис выбора правил скидок и расчёта скидки."""

    @staticmethod
    def select_applicable_rule(
        customer: Customer,
        items_total: Decimal,
    ) -> DiscountRule | None:
        today = timezone.localdate()
        rules = DiscountRule.objects.filter(is_active=True).order_by("priority", "id")
        applicable_by_priority: dict[int, list[DiscountRule]] = {}

        for rule in rules:
            if rule.date_start and today < rule.date_start:
                continue
            if rule.date_end and today > rule.date_end:
                continue
            if items_total < rule.min_order_amount:
                continue
            if customer.orders_count < rule.min_customer_orders:
                continue
            applicable_by_priority.setdefault(rule.priority, []).append(rule)

        for same_priority_rules in applicable_by_priority.values():
            # Priority remains the manager's primary choice. If several active
            # rules deliberately share it, select the larger item discount
            # deterministically instead of silently favouring the oldest row.
            return max(
                same_priority_rules,
                key=lambda rule: (
                    DiscountService.calculate_discount(rule, items_total),
                    bool(rule.free_delivery),
                    -rule.pk,
                ),
            )

        return None

    @staticmethod
    def calculate_discount(rule: DiscountRule | None, items_total: Decimal) -> Decimal:
        if rule is None:
            return Decimal("0")

        if rule.discount_amount > 0:
            return min(rule.discount_amount, items_total)

        if rule.discount_percent > 0:
            return (items_total * rule.discount_percent / Decimal("100")).quantize(
                Decimal("0.01")
            )

        return Decimal("0")
