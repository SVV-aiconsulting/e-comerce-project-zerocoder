"""Тесты корзины REST API."""
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.common.enums import Channel


@pytest.fixture
def api_client(settings):
    settings.ADAPTER_API_TOKENS = ["test-token"]
    client = APIClient()
    client.credentials(HTTP_X_ADAPTER_TOKEN="test-token")
    return client


@pytest.mark.django_db
def test_cart_get_creates_empty_cart(api_client, customer):
    response = api_client.get(
        "/api/cart/",
        {"channel": Channel.TELEGRAM, "external_user_id": "12345", "customer_id": customer.pk},
    )

    assert response.status_code == 200
    assert response.data["channel"] == Channel.TELEGRAM
    assert response.data["items"] == []
    assert response.data["items_total"] == "0.00"


@pytest.mark.django_db
def test_cart_put_item_sets_quantity(api_client, customer, product):
    response = api_client.put(
        f"/api/cart/items/{product.pk}/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "12345",
            "customer_id": customer.pk,
            "quantity": "2.000",
        },
        format="json",
    )

    assert response.status_code == 200
    assert len(response.data["items"]) == 1
    assert response.data["items"][0]["quantity"] == "2.000"
    assert response.data["items_total"] == "200.00"


@pytest.mark.django_db
def test_cart_put_zero_quantity_removes_item(api_client, customer, product, active_cart):
    from apps.carts.services import CartService

    CartService.set_item_quantity(active_cart, product, Decimal("1"))

    response = api_client.put(
        f"/api/cart/items/{product.pk}/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "12345",
            "customer_id": customer.pk,
            "quantity": "0",
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.data["items"] == []


@pytest.mark.django_db
def test_cart_invalid_quantity_returns_422(api_client, customer, product):
    response = api_client.put(
        f"/api/cart/items/{product.pk}/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "12345",
            "customer_id": customer.pk,
            "quantity": "0.001",
        },
        format="json",
    )

    assert response.status_code == 422
    assert response.data["error"]["code"] == "invalid_quantity"


@pytest.mark.django_db
def test_cart_without_token_returns_401(settings, customer):
    settings.ADAPTER_API_TOKENS = ["test-token"]
    client = APIClient()
    response = client.get(
        "/api/cart/",
        {"channel": Channel.TELEGRAM, "external_user_id": "12345"},
    )
    assert response.status_code == 401


@pytest.mark.django_db
def test_cart_clear(api_client, customer, product, active_cart):
    from apps.carts.services import CartService

    CartService.set_item_quantity(active_cart, product, Decimal("1"))

    response = api_client.delete(
        "/api/cart/items/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "12345",
            "customer_id": customer.pk,
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.data["items"] == []


@pytest.mark.django_db
def test_cart_put_inactive_product_returns_404(api_client, customer, inactive_product):
    response = api_client.put(
        f"/api/cart/items/{inactive_product.pk}/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "12345",
            "customer_id": customer.pk,
            "quantity": "1.000",
        },
        format="json",
    )

    assert response.status_code == 404
    assert response.data["error"]["code"] == "product_inactive"


@pytest.mark.django_db
def test_cart_delete_unknown_product_returns_404(api_client, customer):
    response = api_client.delete(
        "/api/cart/items/99999/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "12345",
            "customer_id": customer.pk,
        },
        format="json",
    )

    assert response.status_code == 404
    assert response.data["error"]["code"] == "product_not_found"
