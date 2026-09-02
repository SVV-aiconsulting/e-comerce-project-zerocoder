"""Тесты заказов REST API."""
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.carts.services import CartService
from apps.common.enums import Channel, PaymentMethod, ReceivingType
from apps.delivery.models import (
    DeliveryEnvironment,
    DeliveryQuote,
    DeliveryQuoteStatus,
)
from apps.orders.models import Order


@pytest.fixture
def api_client(settings):
    settings.ADAPTER_API_TOKENS = ["test-token"]
    client = APIClient()
    client.credentials(HTTP_X_ADAPTER_TOKEN="test-token")
    return client


@pytest.mark.django_db
def test_create_order(api_client, customer, product, active_cart, delivery_rule):
    CartService.set_item_quantity(active_cart, product, Decimal("2"))

    response = api_client.post(
        "/api/orders/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "12345",
            "customer_id": customer.pk,
            "receiving_type": ReceivingType.DELIVERY,
            "payment_method": PaymentMethod.CASH_ON_DELIVERY,
            "delivery_address": "ул. Тестовая, 1",
        },
        format="json",
    )

    assert response.status_code == 201
    assert response.data["public_number"]
    assert str(response.data["total_amount"]) == "500.00"
    assert len(response.data["items"]) == 1
    assert Order.objects.filter(public_number=response.data["public_number"]).exists()


@pytest.mark.django_db
def test_get_order_by_number(api_client, customer, product, active_cart, delivery_rule):
    CartService.set_item_quantity(active_cart, product, Decimal("1"))
    create_response = api_client.post(
        "/api/orders/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "12345",
            "customer_id": customer.pk,
            "receiving_type": ReceivingType.PICKUP,
            "payment_method": PaymentMethod.CASH_ON_DELIVERY,
        },
        format="json",
    )
    public_number = create_response.data["public_number"]

    response = api_client.get(
        f"/api/orders/{public_number}/",
        {"channel": Channel.TELEGRAM, "external_user_id": "12345"},
    )

    assert response.status_code == 200
    assert response.data["public_number"] == public_number


@pytest.mark.django_db
def test_customer_orders_list(api_client, customer, product, active_cart, delivery_rule):
    CartService.set_item_quantity(active_cart, product, Decimal("1"))
    api_client.post(
        "/api/orders/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "12345",
            "customer_id": customer.pk,
            "receiving_type": ReceivingType.PICKUP,
            "payment_method": PaymentMethod.CASH_ON_DELIVERY,
        },
        format="json",
    )

    response = api_client.get(
        f"/api/customers/{customer.public_code}/orders/",
        {"channel": Channel.TELEGRAM, "external_user_id": "12345"},
    )

    assert response.status_code == 200
    assert len(response.data) == 1


@pytest.mark.django_db
def test_create_order_twice_returns_empty_cart(api_client, customer, product, active_cart, delivery_rule):
    CartService.set_item_quantity(active_cart, product, Decimal("1"))
    payload = {
        "channel": Channel.TELEGRAM,
        "external_user_id": "12345",
        "customer_id": customer.pk,
        "receiving_type": ReceivingType.PICKUP,
        "payment_method": PaymentMethod.CASH_ON_DELIVERY,
    }
    api_client.post("/api/orders/", payload, format="json")
    response = api_client.post("/api/orders/", payload, format="json")

    assert response.status_code == 422
    assert response.data["error"]["code"] == "empty_cart"


@pytest.mark.django_db
def test_create_order_keeps_confirmed_yandex_quote(
    api_client,
    customer,
    product,
    active_cart,
    delivery_rule,
    settings,
):
    settings.YANDEX_DELIVERY_ENABLED = True
    CartService.set_item_quantity(active_cart, product, Decimal("1"))
    quote = DeliveryQuote.objects.create(
        cart=active_cart,
        environment=DeliveryEnvironment.TEST,
        status=DeliveryQuoteStatus.SUCCEEDED,
        request_fingerprint="b" * 64,
        destination_address="Москва, Тверская, 1",
        amount=Decimal("444.00"),
    )

    response = api_client.post(
        "/api/orders/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "12345",
            "customer_id": customer.pk,
            "receiving_type": ReceivingType.DELIVERY,
            "payment_method": PaymentMethod.CASH_ON_DELIVERY,
            "delivery_address": "Москва, Тверская, 1",
            "delivery_quote_id": quote.pk,
        },
        format="json",
    )

    assert response.status_code == 201
    assert str(response.data["delivery_cost"]) == "444.00"
    assert str(response.data["total_amount"]) == "544.00"
    quote.refresh_from_db()
    assert quote.status == DeliveryQuoteStatus.SELECTED
    assert quote.order.public_number == response.data["public_number"]
