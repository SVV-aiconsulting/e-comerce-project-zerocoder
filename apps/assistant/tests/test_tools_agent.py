from collections import deque
from decimal import Decimal

import pytest

from apps.assistant.services import OrderAssistantService
from apps.common.enums import Channel
from apps.delivery.models import (
    DeliveryEnvironment,
    DeliveryQuote,
    DeliveryQuoteKind,
    DeliveryQuoteStatus,
)
from apps.intake.ai.providers.base import FunctionCall, ToolCompletion
from apps.intake.enums import AssistantMessageRole, AssistantToolCallStatus, InboundEventStatus
from apps.intake.models import AssistantMessage, AssistantToolCall, AssistantTurn
from apps.intake.processors import InboundEventProcessor
from apps.intake.responses import InboundEventResponseService
from apps.intake.services import InboundEventService
from apps.orders.models import Order
from apps.payments.models import Payment


def tool(name, arguments):
    return ToolCompletion(
        content="",
        model_name="GigaChat-2:fixture",
        function_call=FunctionCall(name=name, arguments=arguments, state_id=f"state-{name}"),
        input_tokens=10,
        output_tokens=5,
    )


def answer(content):
    return ToolCompletion(
        content=content,
        model_name="GigaChat-2:fixture",
        input_tokens=10,
        output_tokens=5,
    )


class ScriptedProvider:
    def __init__(self, completions):
        self.completions = deque(completions)
        self.calls = []

    def generate_with_tools(self, **kwargs):
        self.calls.append(kwargs)
        return self.completions.popleft()


def register_event(number, customer, text):
    return InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id=f"tools-agent-{number}",
        external_user_id="12345",
        conversation_key="tools-agent-dialog",
        customer=customer,
        raw_text=text,
    ).event


@pytest.mark.django_db
def test_tools_agent_full_checkout_is_stateful_audited_and_idempotent(
    customer, product, delivery_rule, settings, monkeypatch
):
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_ORDER_PROCESSING_ENABLED = True
    settings.AI_ASSISTANT_MAX_TOOL_CALLS = 8
    settings.AI_ASSISTANT_HISTORY_MESSAGES = 20
    settings.YANDEX_DELIVERY_ENABLED = True
    settings.YOOKASSA_ENABLED = True
    customer.email = "buyer@example.com"
    customer.save(update_fields=["email", "updated_at"])

    provider = ScriptedProvider(
        [
            tool("search_products", {"query": product.name, "limit": 5}),
            tool("set_cart_item", {"product_code": product.public_code, "quantity": 2.0}),
            answer("Добавил два товара. Нужна доставка или самовывоз?"),
            tool("configure_checkout", {"receiving_type": "delivery", "delivery_address": "Москва, Тверская улица, 1"}),
            answer("Адрес записан. Как будете оплачивать?"),
            tool("configure_checkout", {"payment_method": "card_prepayment", "contact_email": "buyer@example.com"}),
            answer("Выбрана онлайн-оплата. Рассчитать итог?"),
            tool("preview_order", {}),
            answer("Итого рассчитано. Подтверждаете оформление заказа?"),
            tool("confirm_order", {"preview_revision": 4, "confirmation": "confirmed"}),
            answer("Без явного подтверждения заказ не создан. Напишите «подтверждаю»."),
            tool("confirm_order", {"preview_revision": 4, "confirmation": "confirmed"}),
            answer("Ваш заказ оформлен. Ссылка на оплату подготовлена."),
        ]
    )
    monkeypatch.setattr("apps.assistant.services.get_gigachat_provider", lambda: provider)

    def fake_quote(draft):
        return DeliveryQuote.objects.create(
            order_draft=draft,
            environment=DeliveryEnvironment.TEST,
            kind=DeliveryQuoteKind.PRELIMINARY,
            status=DeliveryQuoteStatus.SUCCEEDED,
            request_fingerprint="a" * 64,
            destination_address=draft.delivery_address,
            amount=Decimal("321.50"),
            currency="RUB",
            delivery_days=2,
        )

    def fake_payment(order):
        payment, _ = Payment.objects.get_or_create(
            order=order,
            defaults={
                "amount": order.total_amount,
                "description": f"Оплата заказа {order.public_number}",
                "confirmation_url": "https://yookassa.example.test/pay/tools-agent",
            },
        )
        return payment

    monkeypatch.setattr("apps.intake.fulfillment.YandexDeliveryQuoteService.quote_draft", fake_quote)
    monkeypatch.setattr("apps.assistant.tools.PaymentService.ensure_payment_link", fake_payment)

    texts = [
        f"Хочу два товара {product.name}",
        "Доставка на Москва, Тверская улица, 1",
        "Оплачу картой онлайн, чек на buyer@example.com",
        "Рассчитай итог",
        "Спасибо",
        "Да, подтверждаю заказ",
    ]
    events = []
    for number, text in enumerate(texts, start=1):
        event = register_event(number, customer, text)
        InboundEventProcessor.process(event.pk)
        event.status = InboundEventStatus.PROCESSED
        event.save(update_fields=["status", "updated_at"])
        event.refresh_from_db()
        events.append(event)

    assert Order.objects.count() == 1
    order = Order.objects.get()
    assert order.items.get().quantity == Decimal("2")
    assert str(order.delivery_cost) == "321.50"
    assert Payment.objects.filter(order=order).count() == 1
    assert AssistantTurn.objects.count() == len(texts)
    assert AssistantToolCall.objects.filter(status=AssistantToolCallStatus.SUCCEEDED).exists()
    assert AssistantMessage.objects.filter(role=AssistantMessageRole.ASSISTANT).count() == len(texts)

    refused = InboundEventResponseService.present(events[-2])
    assert "не создан" in refused["response"]["message"]
    final = InboundEventResponseService.present(events[-1])
    assert final["response"]["message"] == "Ваш заказ оформлен. Ссылка на оплату подготовлена."
    assert final["response"]["action_url"].endswith("/tools-agent")

    calls_before = len(provider.calls)
    OrderAssistantService.process(events[-1], events[-1].draft, provider=provider)
    assert len(provider.calls) == calls_before
    assert Order.objects.count() == 1
    assert Payment.objects.count() == 1

    last_prompt_messages = provider.calls[-1]["messages"]
    assert any(message.get("content") == "Итого рассчитано. Подтверждаете оформление заказа?" for message in last_prompt_messages)
    assert all("parameters" in function for function in provider.calls[0]["functions"])
