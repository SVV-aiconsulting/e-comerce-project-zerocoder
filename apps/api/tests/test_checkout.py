"""Тесты checkout preview REST API."""
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.carts.services import CartService
from apps.common.enums import Channel, PaymentMethod, ReceivingType
from apps.delivery.models import DeliveryEnvironment, DeliveryQuote, DeliveryQuoteStatus
from apps.delivery.quote_service import YandexDeliveryQuoteService
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


@pytest.mark.django_db
def test_checkout_preview_uses_yandex_for_address(
    api_client,
    customer,
    product,
    active_cart,
    delivery_rule,
    settings,
    monkeypatch,
):
    settings.YANDEX_DELIVERY_ENABLED = True
    product.delivery_weight_grams = 1000
    product.delivery_length_cm = 30
    product.delivery_width_cm = 20
    product.delivery_height_cm = 10
    product.save()
    CartService.set_item_quantity(active_cart, product, Decimal("2"))

    def fake_quote(cart, **kwargs):
        assert kwargs["destination_address"] == "Москва, Тверская, 1"
        assert kwargs["payment_method"] == PaymentMethod.CARD_PREPAYMENT
        return DeliveryQuote.objects.create(
            cart=cart,
            environment=DeliveryEnvironment.TEST,
            status=DeliveryQuoteStatus.SUCCEEDED,
            request_fingerprint="a" * 64,
            destination_address=kwargs["destination_address"],
            amount=Decimal("421.50"),
            delivery_days=2,
        )

    monkeypatch.setattr(YandexDeliveryQuoteService, "quote_cart", fake_quote)
    response = api_client.post(
        "/api/checkout/preview/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "12345",
            "customer_id": customer.pk,
            "receiving_type": ReceivingType.DELIVERY,
            "delivery_address": "Москва, Тверская, 1",
            "payment_method": PaymentMethod.CARD_PREPAYMENT,
        },
        format="json",
    )

    assert response.status_code == 200
    assert str(response.data["delivery_cost"]) == "421.50"
    assert str(response.data["total_amount"]) == "621.50"
    assert response.data["delivery_quote_id"]
    assert response.data["delivery_days"] == 2
