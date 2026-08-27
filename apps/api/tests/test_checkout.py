"""Тесты checkout preview REST API."""
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.carts.services import CartService
from apps.common.enums import Channel, ReceivingType
from apps.discounts.models import DiscountRule


@pytest.fixture
def api_client(settings):
    settings.ADAPTER_API_TOKENS = ["test-token"]
    client = APIClient()
    client.credentials(HTTP_X_ADAPTER_TOKEN="test-token")
    return client


@pytest.mark.django_db
def test_checkout_preview_with_delivery(api_client, customer, product, active_cart, delivery_rule):
    CartService.set_item_quantity(active_cart, product, Decimal("2"))

    response = api_client.post(
        "/api/checkout/preview/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "12345",
            "customer_id": customer.pk,
            "receiving_type": ReceivingType.DELIVERY,
        },
        format="json",
    )

    assert response.status_code == 200
    assert str(response.data["items_total"]) == "200.00"
    assert str(response.data["delivery_cost"]) == "300.00"
    assert str(response.data["total_amount"]) == "500.00"


@pytest.mark.django_db
def test_checkout_preview_pickup_no_delivery(api_client, customer, product, active_cart, delivery_rule):
    CartService.set_item_quantity(active_cart, product, Decimal("1"))

    response = api_client.post(
        "/api/checkout/preview/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "12345",
            "customer_id": customer.pk,
            "receiving_type": ReceivingType.PICKUP,
        },
        format="json",
    )

    assert response.status_code == 200
    assert str(response.data["delivery_cost"]) == "0.00"
    assert str(response.data["total_amount"]) == "100.00"


@pytest.mark.django_db
def test_checkout_preview_with_discount(api_client, customer, product, active_cart, delivery_rule):
    DiscountRule.objects.create(
        name="Скидка 10%",
        is_active=True,
        priority=10,
        discount_percent=Decimal("10"),
        min_order_amount=Decimal("0"),
    )
    CartService.set_item_quantity(active_cart, product, Decimal("2"))

    response = api_client.post(
        "/api/checkout/preview/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "12345",
            "customer_id": customer.pk,
            "receiving_type": ReceivingType.PICKUP,
        },
        format="json",
    )

    assert response.status_code == 200
    assert str(response.data["discount_amount"]) == "20.00"
    assert str(response.data["total_amount"]) == "180.00"


@pytest.mark.django_db
def test_checkout_preview_empty_cart_returns_422(api_client, customer, active_cart):
    response = api_client.post(
        "/api/checkout/preview/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "12345",
            "customer_id": customer.pk,
            "receiving_type": ReceivingType.DELIVERY,
        },
        format="json",
    )

    assert response.status_code == 422
    assert response.data["error"]["code"] == "empty_cart"
