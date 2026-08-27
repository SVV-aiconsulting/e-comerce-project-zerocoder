import json

import pytest

from apps.common.enums import Channel
from apps.intake.ai.schemas import OrderExtraction
from apps.intake.clarifications import ClarificationService
from apps.intake.draft_application import DraftExtractionApplier
from apps.intake.enums import OrderDraftStatus
from apps.intake.fulfillment import DraftOrderConversionService, DraftPricingService
from apps.intake.services import InboundEventService, OrderDraftService
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
                        "confidence": 0.95,
                    }
                ],
                "receiving_type": "pickup",
                "desired_date": None,
                "desired_time_interval": None,
                "delivery_address": None,
                "payment_method": "card_prepayment",
                "customer_comment": "Позвонить заранее",
                "confirmation": confirmation,
                "missing_fields": [],
                "clarification_needed": False,
                "confidence": 0.94,
            }
        ),
        strict=True,
    )


def register_event(event_id, text):
    return InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id=event_id,
        external_user_id="12345",
        conversation_key="fulfillment-1",
        raw_text=text,
    ).event


@pytest.mark.django_db
def test_preview_confirmation_and_conversion_are_idempotent(customer, product):
    draft, _ = OrderDraftService.get_or_create_active(
        customer=customer,
        channel=Channel.TELEGRAM,
        external_user_id="12345",
        conversation_key="fulfillment-1",
    )
    draft = DraftExtractionApplier.apply(draft, extraction())

    preview = DraftPricingService.preview(draft)

    assert preview.status == OrderDraftStatus.AWAITING_CONFIRMATION
    assert preview.items_total == product.base_price * 2
    assert preview.delivery_cost == 0
    assert preview.total_amount == product.base_price * 2
    question_event = register_event("preview-event", "начальный заказ")
    question = ClarificationService.sync_next_question(preview, question_event)
    assert question.field_path == "confirmation"
    assert str(preview.total_amount) in question.question

    answer_event = register_event("confirm-event", "Да, подтверждаю")
    ClarificationService.record_pending_answer(preview, answer_event)
    confirmed = DraftExtractionApplier.apply(preview, extraction(confirmation="confirm"))
    assert confirmed.status == OrderDraftStatus.CONFIRMED

    first_order = DraftOrderConversionService.convert(confirmed)
    second_order = DraftOrderConversionService.convert(confirmed)

    confirmed.refresh_from_db()
    assert first_order.pk == second_order.pk
    assert Order.objects.count() == 1
    assert confirmed.status == OrderDraftStatus.CONVERTED
    assert confirmed.converted_order_id == first_order.pk
    assert first_order.items.count() == 1
    assert first_order.items.get().quantity == 2
