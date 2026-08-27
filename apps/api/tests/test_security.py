"""Тесты безопасности REST API (IDOR, customer context)."""
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.carts.services import CartService
from apps.common.enums import Channel, CustomerSource, PaymentMethod, ReceivingType
from apps.customers.services import CustomerService


@pytest.fixture
def api_client(settings):
    settings.ADAPTER_API_TOKENS = ["test-token"]
    client = APIClient()
    client.credentials(HTTP_X_ADAPTER_TOKEN="test-token")
    return client


@pytest.fixture
def other_customer(db):
    return CustomerService.create_customer(
        name="Другой Клиент",
        phone="79998887766",
        first_source=CustomerSource.TELEGRAM,
        channel=Channel.TELEGRAM,
        external_user_id="99999",
        username="other_user",
        phone_verified=True,
    )


@pytest.mark.django_db
def test_cart_get_rejects_wrong_customer_id(api_client, customer, other_customer, active_cart):
    response = api_client.get(
        "/api/cart/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "12345",
            "customer_id": other_customer.pk,
        },
    )

    assert response.status_code == 409
    assert response.data["error"]["code"] == "customer_context_mismatch"


@pytest.mark.django_db
def test_cart_put_rejects_wrong_customer_id(api_client, customer, other_customer, product, active_cart):
    response = api_client.put(
        f"/api/cart/items/{product.pk}/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "12345",
            "customer_id": other_customer.pk,
            "quantity": "1.000",
        },
        format="json",
    )

    assert response.status_code == 409
    assert response.data["error"]["code"] == "customer_context_mismatch"


@pytest.mark.django_db
def test_cart_clear_rejects_wrong_customer_id(api_client, customer, other_customer, product, active_cart):
    CartService.set_item_quantity(active_cart, product, Decimal("1"))

    response = api_client.delete(
        "/api/cart/items/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "12345",
            "customer_id": other_customer.pk,
        },
        format="json",
    )

    assert response.status_code == 409
    assert response.data["error"]["code"] == "customer_context_mismatch"


@pytest.mark.django_db
def test_checkout_preview_rejects_wrong_customer_id(
    api_client, customer, other_customer, product, active_cart, delivery_rule
):
    CartService.set_item_quantity(active_cart, product, Decimal("1"))

    response = api_client.post(
        "/api/checkout/preview/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "12345",
            "customer_id": other_customer.pk,
            "receiving_type": ReceivingType.PICKUP,
        },
        format="json",
    )

    assert response.status_code == 409
    assert response.data["error"]["code"] == "customer_context_mismatch"


@pytest.mark.django_db
def test_create_order_rejects_wrong_customer_id(
    api_client, customer, other_customer, product, active_cart, delivery_rule
):
    CartService.set_item_quantity(active_cart, product, Decimal("1"))

    response = api_client.post(
        "/api/orders/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "12345",
            "customer_id": other_customer.pk,
            "receiving_type": ReceivingType.PICKUP,
            "payment_method": PaymentMethod.CASH_ON_DELIVERY,
        },
        format="json",
    )

    assert response.status_code == 409
    assert response.data["error"]["code"] == "customer_context_mismatch"


@pytest.mark.django_db
def test_cart_customer_mismatch_when_cart_already_bound(
    api_client, customer, other_customer, product, active_cart
):
    CartService.set_item_quantity(active_cart, product, Decimal("1"))

    response = api_client.get(
        "/api/cart/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "12345",
            "customer_id": other_customer.pk,
        },
    )

    assert response.status_code == 409
    assert response.data["error"]["code"] == "customer_context_mismatch"
    active_cart.refresh_from_db()
    assert active_cart.customer_id == customer.pk


@pytest.mark.django_db
def test_order_detail_idor_denied(api_client, customer, other_customer, product, active_cart, delivery_rule):
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
        {"channel": Channel.TELEGRAM, "external_user_id": "99999"},
    )

    assert response.status_code == 403
    assert response.data["error"]["code"] == "order_access_denied"


@pytest.mark.django_db
def test_customer_orders_idor_denied(api_client, customer, other_customer, product, active_cart, delivery_rule):
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
        {"channel": Channel.TELEGRAM, "external_user_id": "99999"},
    )

    assert response.status_code == 403
    assert response.data["error"]["code"] == "order_access_denied"


@pytest.mark.django_db
def test_customer_id_mismatch_with_identity(api_client, customer, other_customer):
    response = api_client.get(
        "/api/cart/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "12345",
            "customer_id": other_customer.pk,
        },
    )

    assert response.status_code == 409
    assert response.data["error"]["code"] == "customer_context_mismatch"
