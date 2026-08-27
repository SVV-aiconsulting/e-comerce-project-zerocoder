from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.test import Client

from apps.carts.services import CartService
from apps.common.enums import Channel, PaymentMethod, PaymentStatus, ReceivingType
from apps.intake.enums import InboundEventStatus, OrderDraftStatus
from apps.intake.models import InboundEvent, OrderDraft
from apps.orders.services import OrderService


@pytest.mark.django_db
def test_dashboard_requires_staff_user():
    response = Client().get("/manager/dashboard/")

    assert response.status_code == 302
    assert "/admin/login/" in response.url


@pytest.mark.django_db
def test_dashboard_shows_metrics_channel_and_attention(
    active_cart, product, customer, delivery_rule
):
    CartService.set_item_quantity(active_cart, product, Decimal("1"))
    order = OrderService.create_order_from_cart(
        active_cart,
        customer=customer,
        channel=Channel.TELEGRAM,
        receiving_type=ReceivingType.PICKUP,
        payment_method=PaymentMethod.CARD_PREPAYMENT,
    )
    order.payment_status = PaymentStatus.PAID
    order.save(update_fields=["payment_status", "updated_at"])
    draft = OrderDraft.objects.create(
        customer=customer,
        channel=Channel.TELEGRAM,
        external_user_id="12345",
        conversation_key="attention-test",
        status=OrderDraftStatus.ESCALATED,
        manager_attention_required=True,
        escalation_reason="Нужно проверить адрес",
    )
    InboundEvent.objects.create(
        channel=Channel.TELEGRAM,
        external_event_id="failed-event",
        external_user_id="12345",
        conversation_key="failed-test",
        status=InboundEventStatus.FAILED,
        last_error="Ошибка адаптера",
    )
    user = User.objects.create_user(username="manager", password="secret", is_staff=True)
    client = Client()
    client.force_login(user)

    response = client.get("/manager/dashboard/")

    assert response.status_code == 200
    assert response.context["order_count"] == 1
    assert response.context["paid_count"] == 1
    assert response.context["channels"][0]["channel"] == Channel.TELEGRAM
    assert response.context["attention_counts"]["AI-заказ"] == 1
    assert response.context["attention_counts"]["Входящий канал"] == 1
    assert str(draft.public_id) in response.content.decode()
