import json
from decimal import Decimal

import pytest

from apps.common.enums import Channel
from apps.intake.ai.schemas import OrderExtraction
from apps.intake.ai.services import AIExtractionService
from apps.intake.enums import ClarificationStatus, InboundEventStatus, OrderDraftStatus
from apps.intake.models import Clarification, OrderDraft
from apps.intake.processors import InboundEventProcessor
from apps.intake.responses import InboundEventResponseService
from apps.intake.services import InboundEventService
from apps.delivery.models import DeliveryEnvironment, DeliveryQuote, DeliveryQuoteKind, DeliveryQuoteStatus
from apps.delivery.quote_service import YandexDeliveryQuoteService
from apps.orders.models import Order
from apps.payments.models import Payment
from apps.payments.services import PaymentService


def extraction(*, confirmation="none"):
    return OrderExtraction.model_validate_json(
        json.dumps(
            {
                "intent": "create_order",
                "items": [
                    {
                        "raw_product_name": "Тестовый товар",
                        "quantity": 2,
                        "unit": "piece",
                        "attributes": [],
                        "confidence": 0.97,
                    }
                ],
                "receiving_type": "pickup",
                "desired_date": None,
                "desired_time_interval": None,
                "delivery_address": None,
                "payment_method": "card_prepayment",
                "customer_comment": None,
                "confirmation": confirmation,
                "missing_fields": [],
                "clarification_needed": False,
                "confidence": 0.95,
            }
        ),
        strict=True,
    )


def register_event(event_id, customer, text):
    return InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id=event_id,
        external_user_id="12345",
        conversation_key="processor-ai-e2e",
        customer=customer,
        raw_text=text,
    ).event


@pytest.mark.django_db
def test_ai_processor_previews_confirms_and_creates_one_order(
    customer,
    product,
    settings,
    monkeypatch,
):
    settings.AI_ORDER_PROCESSING_ENABLED = True

    def fake_extract(_event, _draft, **_kwargs):
        confirmation = "confirm" if "подтверждаю" in _event.raw_text.casefold() else "none"
        return extraction(confirmation=confirmation), None

    monkeypatch.setattr(AIExtractionService, "extract_with_repair", fake_extract)
    first_event = register_event("processor-ai-1", customer, "Два тестовых товара")

    first_outcome = InboundEventProcessor.process(first_event.pk)

    draft = OrderDraft.objects.get(pk=first_outcome.draft_id)
    assert draft.status == OrderDraftStatus.AWAITING_CONFIRMATION
    assert draft.contact_phone == customer.phone
    assert draft.total_amount == product.base_price * 2
    assert Clarification.objects.get(draft=draft).field_path == "confirmation"

    confirmation_event = register_event(
        "processor-ai-2",
        customer,
        "Да, подтверждаю",
    )
    second_outcome = InboundEventProcessor.process(confirmation_event.pk)

    draft.refresh_from_db()
    assert second_outcome.draft_id == draft.pk
    assert draft.status == OrderDraftStatus.CONVERTED
    assert Order.objects.count() == 1
    order = Order.objects.get()
    assert order.items.get().quantity == 2
    assert order.customer_phone_snapshot == customer.phone
    assert order.source_external_user_id_snapshot == "12345"
    assert not Clarification.objects.filter(
        draft=draft,
        status=ClarificationStatus.PENDING,
    ).exists()


@pytest.mark.django_db
def test_assistant_full_dialog_delivery_confirmation_and_yookassa_link(
    customer,
    product,
    delivery_rule,
    settings,
    monkeypatch,
):
    settings.AI_ASSISTANT_ENABLED = False
    settings.AI_ORDER_PROCESSING_ENABLED = True
    settings.YANDEX_DELIVERY_ENABLED = True
    settings.YOOKASSA_ENABLED = True

    def structured(*, receiving=None, address=None, payment=None, confirmation="none"):
        return OrderExtraction.model_validate_json(
            json.dumps(
                {
                    "intent": "create_order",
                    "items": [
                        {
                            "raw_product_name": product.name,
                            "quantity": 2,
                            "unit": product.unit,
                            "attributes": [],
                            "confidence": 0.99,
                        }
                    ],
                    "receiving_type": receiving,
                    "desired_date": None,
                    "desired_time_interval": None,
                    "delivery_address": address,
                    "payment_method": payment,
                    "customer_comment": None,
                    "confirmation": confirmation,
                    "missing_fields": [],
                    "clarification_needed": False,
                    "confidence": 0.98,
                }
            ),
            strict=True,
        )

    def fake_extract(event, _draft, **_kwargs):
        text = event.raw_text.casefold()
        if "подтверждаю" in text:
            parsed = structured(
                receiving="delivery",
                address="Москва, Тверская улица, 1",
                payment="card_prepayment",
                confirmation="confirm",
            )
        elif "карт" in text:
            parsed = structured(
                receiving="delivery",
                address="Москва, Тверская улица, 1",
                payment="card_prepayment",
            )
        elif "тверская" in text:
            parsed = structured(
                receiving="delivery",
                address="Москва, Тверская улица, 1",
            )
        else:
            parsed = structured()
        return parsed, None

    def fake_quote(draft):
        return DeliveryQuote.objects.create(
            order_draft=draft,
            environment=DeliveryEnvironment.TEST,
            kind=DeliveryQuoteKind.PRELIMINARY,
            status=DeliveryQuoteStatus.SUCCEEDED,
            request_fingerprint="f" * 64,
            destination_address=draft.delivery_address,
            amount=Decimal("321.50"),
            currency="RUB",
            delivery_days=2,
        )

    def fake_payment(order):
        return Payment.objects.create(
            order=order,
            amount=order.total_amount,
            description=f"Оплата заказа {order.public_number}",
            confirmation_url="https://yookassa.example.test/pay/assistant",
        )

    monkeypatch.setattr(AIExtractionService, "extract_with_repair", fake_extract)
    monkeypatch.setattr(YandexDeliveryQuoteService, "quote_draft", fake_quote)
    monkeypatch.setattr(PaymentService, "ensure_payment_link", fake_payment)

    texts = [
        "Хочу два тестовых товара",
        "Доставка: Москва, Тверская улица, 1",
        "Оплачу банковской картой",
        "Да, подтверждаю заказ",
    ]
    events = []
    for number, text in enumerate(texts, start=1):
        event = register_event(f"assistant-full-{number}", customer, text)
        InboundEventProcessor.process(event.pk)
        event.status = InboundEventStatus.PROCESSED
        event.save(update_fields=["status", "updated_at"])
        event.refresh_from_db()
        events.append(event)

    order = Order.objects.get()
    response = InboundEventResponseService.present(events[-1])
    assert order.delivery_address == "Москва, Тверская улица, 1"
    assert str(order.delivery_cost) == "321.50"
    assert order.payment_method == "card_prepayment"
    assert response["response"]["type"] == "payment_link"
    assert response["response"]["action_url"].startswith("https://")
