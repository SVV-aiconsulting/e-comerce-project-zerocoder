"""E2E тест полного сценария REST API."""
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.common.enums import Channel, PaymentMethod, ReceivingType


@pytest.mark.django_db
def test_full_storefront_flow(settings, product, delivery_rule):
    settings.ADAPTER_API_TOKENS = ["test-token"]
    client = APIClient()
    client.credentials(HTTP_X_ADAPTER_TOKEN="test-token")

    identify_response = client.post(
        "/api/identify-customer/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "e2e-user",
            "phone": "+7 (912) 345-67-82",
            "display_name": "E2E Клиент",
            "phone_verification_source": "platform_contact",
        },
        format="json",
    )
    assert identify_response.status_code == 200
    customer_id = identify_response.data["customer_id"]
    customer_code = identify_response.data["customer_public_code"]

    catalog_response = client.get("/api/products/")
    assert catalog_response.status_code == 200
    assert len(catalog_response.data) >= 1

    cart_response = client.put(
        f"/api/cart/items/{product.pk}/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "e2e-user",
            "customer_id": customer_id,
            "quantity": "2.000",
        },
        format="json",
    )
    assert cart_response.status_code == 200
    assert cart_response.data["items_total"] == "200.00"

    preview_response = client.post(
        "/api/checkout/preview/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "e2e-user",
            "customer_id": customer_id,
            "receiving_type": ReceivingType.DELIVERY,
        },
        format="json",
    )
    assert preview_response.status_code == 200
    assert str(preview_response.data["total_amount"]) == "500.00"

    order_response = client.post(
        "/api/orders/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "e2e-user",
            "customer_id": customer_id,
            "receiving_type": ReceivingType.DELIVERY,
            "payment_method": PaymentMethod.CASH_ON_DELIVERY,
            "delivery_address": "ул. E2E, 1",
            "is_new_customer": True,
        },
        format="json",
    )
    assert order_response.status_code == 201
    public_number = order_response.data["public_number"]

    detail_response = client.get(
        f"/api/orders/{public_number}/",
        {"channel": Channel.TELEGRAM, "external_user_id": "e2e-user"},
    )
    assert detail_response.status_code == 200
    assert detail_response.data["public_number"] == public_number

    history_response = client.get(
        f"/api/customers/{customer_code}/orders/",
        {"channel": Channel.TELEGRAM, "external_user_id": "e2e-user"},
    )
    assert history_response.status_code == 200
    assert len(history_response.data) == 1
