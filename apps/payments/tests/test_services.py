from decimal import Decimal
import json

import httpx
import pytest
from django.test import override_settings

from apps.carts.services import CartService
from apps.common.enums import Channel, PaymentMethod, PaymentStatus, ReceivingType
from apps.discounts.models import DiscountRule
from apps.orders.services import OrderService
from apps.payments.models import Payment, PaymentEnvironment, PaymentState, RefundState
from apps.payments.services import PaymentService, YooKassaWebhookService
from apps.payments.yookassa.client import YooKassaClient, YooKassaConfig


def client_config():
    return YooKassaConfig(
        enabled=True,
        environment=PaymentEnvironment.TEST,
        shop_id="test-shop",
        secret_key="test-secret",
        return_url="http://localhost:8000/payment/return/",
        timeout_seconds=5,
    )


def make_order(active_cart, product, customer, delivery_rule):
    CartService.set_item_quantity(active_cart, product, Decimal("2"))
    return OrderService.create_order_from_cart(
        active_cart,
        customer=customer,
        channel=Channel.TELEGRAM,
        receiving_type=ReceivingType.DELIVERY,
        payment_method=PaymentMethod.CARD_PREPAYMENT,
        delivery_address="Москва, Тверская, 1",
    )


def payment_response(order, *, status="pending", amount="500.00"):
    return {
        "id": "pay-test-1",
        "status": status,
        "amount": {"value": amount, "currency": "RUB"},
        "confirmation": {
            "type": "redirect",
            "confirmation_url": "https://yookassa.test/confirm/pay-test-1",
        },
        "metadata": {"order_public_number": order.public_number},
    }


@pytest.mark.django_db
def test_payment_link_is_idempotent(active_cart, product, customer, delivery_rule):
    customer.email = "buyer@example.com"
    customer.save(update_fields=["email", "updated_at"])
    order = make_order(active_cart, product, customer, delivery_rule)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=payment_response(order))

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = YooKassaClient(client_config(), http_client=http_client)
        first = PaymentService.ensure_payment_link(order, client=client)
        second = PaymentService.ensure_payment_link(order, client=client)

    order.refresh_from_db()
    assert first.pk == second.pk
    assert first.state == PaymentState.PENDING
    assert first.confirmation_url.endswith("pay-test-1")
    assert order.payment_status == PaymentStatus.WAITING
    assert len(calls) == 1
    assert first.receipt_data["customer"]["email"] == "buyer@example.com"
    assert len(first.receipt_data["items"]) == 2
    assert first.receipt_data["items"][0]["amount"]["value"] == "100.00"
    assert first.receipt_data["items"][0]["measure"] == "piece"


@pytest.mark.django_db
def test_receipt_uses_unit_price_and_distributes_discount(
    active_cart, product, customer, delivery_rule
):
    product.unit = "kg"
    product.base_price = Decimal("2100.00")
    product.save(update_fields=["unit", "base_price", "updated_at"])
    customer.email = "buyer@example.com"
    customer.save(update_fields=["email", "updated_at"])
    DiscountRule.objects.create(
        name="Тестовая скидка",
        discount_percent=Decimal("5.00"),
        min_order_amount=Decimal("0.00"),
    )
    CartService.set_item_quantity(active_cart, product, Decimal("1.500"))
    order = OrderService.create_order_from_cart(
        active_cart,
        customer=customer,
        channel=Channel.TELEGRAM,
        receiving_type=ReceivingType.DELIVERY,
        payment_method=PaymentMethod.CARD_PREPAYMENT,
        delivery_address="Москва, Тверская, 1",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        item = payload["receipt"]["items"][0]
        assert item["quantity"] == "1.500"
        assert item["amount"]["value"] == "1995.00"
        assert item["measure"] == "kilogram"
        assert Decimal(item["quantity"]) * Decimal(item["amount"]["value"]) == Decimal(
            "2992.50"
        )
        assert sum(
            Decimal(line["quantity"]) * Decimal(line["amount"]["value"])
            for line in payload["receipt"]["items"]
        ) == order.total_amount
        return httpx.Response(200, json=payment_response(order, amount=str(order.total_amount)))

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        PaymentService.ensure_payment_link(
            order,
            client=YooKassaClient(client_config(), http_client=http_client),
        )


@pytest.mark.django_db
@override_settings(YOOKASSA_VERIFY_WEBHOOK_IP=False)
def test_webhook_rechecks_provider_and_is_idempotent(
    active_cart, product, customer, delivery_rule
):
    order = make_order(active_cart, product, customer, delivery_rule)
    payment = Payment.objects.create(
        order=order,
        environment=PaymentEnvironment.TEST,
        amount=order.total_amount,
        description="Оплата",
        external_id="pay-test-1",
        receipt_data={"items": []},
    )
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        response = payment_response(order, status="succeeded")
        response["receipt_registration"] = {"status": "pending"}
        return httpx.Response(200, json=response)

    body = {
        "type": "notification",
        "event": "payment.succeeded",
        "object": {"id": "pay-test-1", "status": "succeeded"},
    }
    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = YooKassaClient(client_config(), http_client=http_client)
        event = YooKassaWebhookService.process(body, remote_ip="127.0.0.1", client=client)
        duplicate = YooKassaWebhookService.process(
            body, remote_ip="127.0.0.1", client=client
        )

    payment.refresh_from_db()
    order.refresh_from_db()
    assert event.pk == duplicate.pk
    assert event.verified is True
    assert payment.state == PaymentState.SUCCEEDED
    assert payment.receipt_registration_status == "pending"
    assert order.payment_status == PaymentStatus.PAID
    assert len(calls) == 1


@pytest.mark.django_db
def test_refund_is_created_for_successful_payment(
    active_cart, product, customer, delivery_rule
):
    customer.email = "buyer@example.com"
    customer.save(update_fields=["email", "updated_at"])
    order = make_order(active_cart, product, customer, delivery_rule)
    payment = Payment.objects.create(
        order=order,
        environment=PaymentEnvironment.TEST,
        amount=order.total_amount,
        description="Оплата",
        external_id="pay-test-1",
        state=PaymentState.SUCCEEDED,
        receipt_data={"items": []},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/refunds")
        assert json.loads(request.content)["payment_id"] == "pay-test-1"
        return httpx.Response(200, json={"id": "refund-1", "status": "succeeded"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = YooKassaClient(client_config(), http_client=http_client)
        refund = PaymentService.create_refund(
            payment,
            amount=Decimal("100.00"),
            reason="Тест",
            client=client,
        )
    assert refund.state == RefundState.SUCCEEDED
    assert refund.external_id == "refund-1"
