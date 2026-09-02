from decimal import Decimal

import pytest

from apps.carts.services import CartService
from apps.common.enums import Channel, PaymentMethod, ReceivingType
from apps.orders.services import OrderService
from apps.payments.models import Payment, PaymentEnvironment, PaymentState
from apps.payments.tasks import notify_payment_succeeded, sync_pending_payments


def make_payment(active_cart, product, customer):
    CartService.set_item_quantity(active_cart, product, Decimal("1"))
    order = OrderService.create_order_from_cart(
        active_cart,
        customer=customer,
        channel=Channel.TELEGRAM,
        receiving_type=ReceivingType.PICKUP,
        payment_method=PaymentMethod.CARD_PREPAYMENT,
    )
    return Payment.objects.create(
        order=order,
        environment=PaymentEnvironment.TEST,
        state=PaymentState.SUCCEEDED,
        external_id="pay-notification-test",
        amount=order.total_amount,
        description="Оплата тестового заказа",
    )


@pytest.mark.django_db
def test_paid_telegram_notification_is_sent_once(
    active_cart,
    product,
    customer,
    monkeypatch,
):
    payment = make_payment(active_cart, product, customer)
    sent = []
    monkeypatch.setattr(
        "apps.payments.tasks._send_telegram_message",
        lambda **kwargs: sent.append(kwargs),
    )

    first = notify_payment_succeeded(payment.pk)
    second = notify_payment_succeeded(payment.pk)

    payment.refresh_from_db()
    assert first["status"] == "sent"
    assert second["status"] == "already_sent"
    assert sent == [{"chat_id": "12345", "text": "Ваш заказ оплачен."}]
    assert payment.paid_notification_sent_at is not None
    assert payment.paid_notification_attempts == 1


@pytest.mark.django_db
def test_pending_payments_are_rechecked_as_webhook_fallback(
    active_cart,
    product,
    customer,
    settings,
    monkeypatch,
):
    payment = make_payment(active_cart, product, customer)
    payment.state = PaymentState.PENDING
    payment.save(update_fields=["state", "updated_at"])
    settings.PAYMENT_SYNC_BATCH_SIZE = 10
    synced = []
    monkeypatch.setattr(
        "apps.payments.tasks.PaymentService.sync_payment",
        classmethod(lambda cls, current: synced.append(current.pk)),
    )

    result = sync_pending_payments()

    assert result == {"selected": 1, "synced": 1, "failed": 0}
    assert synced == [payment.pk]
