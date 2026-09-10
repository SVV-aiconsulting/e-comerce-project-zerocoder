from decimal import Decimal

import pytest

from apps.discounts.models import DiscountRule
from apps.discounts.services import DiscountService


@pytest.mark.django_db
def test_same_priority_rules_choose_the_larger_applicable_discount(customer):
    DiscountRule.objects.create(
        name="5% от 3000",
        priority=100,
        min_order_amount=Decimal("3000"),
        discount_percent=Decimal("5"),
    )
    ten_percent = DiscountRule.objects.create(
        name="10% от 6000",
        priority=100,
        min_order_amount=Decimal("6000"),
        discount_percent=Decimal("10"),
    )

    selected = DiscountService.select_applicable_rule(customer, Decimal("6500"))

    assert selected == ten_percent


@pytest.mark.django_db
def test_lower_priority_number_still_wins_over_a_larger_discount(customer):
    preferred = DiscountRule.objects.create(
        name="Менеджерский приоритет",
        priority=10,
        discount_percent=Decimal("5"),
    )
    DiscountRule.objects.create(
        name="Нижний приоритет",
        priority=100,
        discount_percent=Decimal("20"),
    )

    assert DiscountService.select_applicable_rule(customer, Decimal("1000")) == preferred
