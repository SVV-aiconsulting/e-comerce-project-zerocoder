"""Общие фикстуры pytest."""
from decimal import Decimal

import pytest

from apps.carts.services import CartService
from apps.catalog.models import Product
from apps.common.enums import Channel, CustomerSource, ProductUnit, ReceivingType
from apps.common.utils import generate_public_code
from apps.customers.services import CustomerService
from apps.delivery.models import DeliveryRule


@pytest.fixture
def product(db):
    return Product.objects.create(
        public_code=generate_public_code(
            lambda code: Product.objects.filter(public_code=code).exists()
        ),
        name="Тестовый товар",
        unit=ProductUnit.PIECE,
        min_quantity=Decimal("1"),
        base_price=Decimal("100.00"),
        is_active=True,
    )


@pytest.fixture
def inactive_product(db):
    return Product.objects.create(
        public_code=generate_public_code(
            lambda code: Product.objects.filter(public_code=code).exists()
        ),
        name="Неактивный товар",
        unit=ProductUnit.PIECE,
        min_quantity=Decimal("1"),
        base_price=Decimal("50.00"),
        is_active=False,
    )


@pytest.fixture
def customer(db):
    return CustomerService.create_customer(
        name="Иван Тестов",
        phone="79123456789",
        first_source=CustomerSource.TELEGRAM,
        channel=Channel.TELEGRAM,
        external_user_id="12345",
        username="ivan_test",
        phone_verified=True,
    )


@pytest.fixture
def delivery_rule(db):
    return DeliveryRule.objects.create(
        name="Стандартная доставка",
        is_active=True,
        delivery_cost=Decimal("300.00"),
        free_delivery_from=Decimal("5000.00"),
        min_order_amount=Decimal("0"),
    )


@pytest.fixture
def active_cart(db, customer):
    return CartService.get_or_create_active_cart(
        channel=Channel.TELEGRAM,
        external_user_id="12345",
        customer=customer,
    )
