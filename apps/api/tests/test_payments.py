from decimal import Decimal
from types import SimpleNamespace

import pytest
from rest_framework.test import APIClient

from apps.carts.services import CartService
from apps.common.enums import Channel, PaymentMethod, ReceivingType
from apps.orders.services import OrderService
from apps.payments.models import Payment, PaymentEnvironment
from apps.payments.services import PaymentService, YooKassaWebhookService


@pytest.fixture
def api_client(settings):
    settings.ADAPTER_API_TOKENS = ["test-token"]
    client = APIClient()
    client.credentials(HTTP_X_ADAPTER_TOKEN="test-token")
    return client


def make_order(active_cart, product, customer, delivery_rule):
    CartService.set_item_quantity(active_cart, product, Decimal("1"))
    return OrderService.create_order_from_cart(
        active_cart,
        customer=customer,
        channel=Channel.TELEGRAM,
        receiving_type=ReceivingType.PICKUP,
        payment_method=PaymentMethod.CARD_PREPAYMENT,
    )


@pytest.mark.django_db
def test_client_can_request_payment_link_for_own_order(
    api_client, active_cart, product, customer, delivery_rule, monkeypatch
):
    order = make_order(active_cart, product, customer, delivery_rule)
    payment = Payment.objects.create(
        order=order,
        environment=PaymentEnvironment.TEST,
        amount=order.total_amount,
        description="Оплата",
        confirmation_url="https://yookassa.test/confirm/payment-1",
    )
    monkeypatch.setattr(
        PaymentService, "ensure_payment_link", classmethod(lambda cls, _order: payment)
    )

    response = api_client.post(
        f"/api/orders/{order.public_number}/payments/",
        {"channel": Channel.TELEGRAM, "external_user_id": "12345"},
        format="json",
    )

    assert response.status_code == 201
    assert response.data["id"] == payment.pk
    assert response.data["confirmation_url"].endswith("payment-1")


@pytest.mark.django_db
def test_payment_link_rejects_other_channel_identity(
    api_client, active_cart, product, customer, delivery_rule
):
    order = make_order(active_cart, product, customer, delivery_rule)

    response = api_client.post(
        f"/api/orders/{order.public_number}/payments/",
        {"channel": Channel.TELEGRAM, "external_user_id": "unknown"},
        format="json",
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_yookassa_webhook_rejects_incomplete_payload():
    response = APIClient().post(
        "/api/webhooks/payments/yookassa/",
        {"event": "payment.succeeded", "object": {}},
        format="json",
    )
    assert response.status_code == 422
    assert response.data["error"]["code"] == "payment_error"


@pytest.mark.django_db
def test_yookassa_webhook_uses_original_ip_from_nginx(settings, monkeypatch):
    settings.YOOKASSA_VERIFY_WEBHOOK_IP = True
    received = {}

    def fake_process(cls, payload, *, remote_ip, client=None):
        received["remote_ip"] = remote_ip
        return SimpleNamespace(pk=17)

    monkeypatch.setattr(
        YooKassaWebhookService,
        "process",
        classmethod(fake_process),
    )
    response = APIClient().post(
        "/api/webhooks/payments/yookassa/",
        {"event": "payment.succeeded", "object": {"id": "pay-1"}},
        format="json",
        HTTP_X_REAL_IP="185.71.76.1",
        REMOTE_ADDR="172.18.0.5",
    )

    assert response.status_code == 200
    assert received["remote_ip"] == "185.71.76.1"
