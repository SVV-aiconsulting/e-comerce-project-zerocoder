import json

import pytest

from apps.common.enums import Channel
from apps.intake.ai.schemas import OrderExtraction
from apps.intake.ai.services import AIExtractionService
from apps.intake.enums import ClarificationStatus, OrderDraftStatus
from apps.intake.models import Clarification, OrderDraft
from apps.intake.processors import InboundEventProcessor
from apps.intake.services import InboundEventService
from apps.orders.models import Order


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

    def fake_extract(_event, _draft):
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
