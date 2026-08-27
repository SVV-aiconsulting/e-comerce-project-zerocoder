import json
from decimal import Decimal

import pytest

from apps.catalog.models import ProductAlias
from apps.common.enums import Channel, ProductUnit
from apps.intake.ai.schemas import OrderExtraction
from apps.intake.clarifications import ClarificationService
from apps.intake.draft_application import DraftExtractionApplier
from apps.intake.enums import ClarificationStatus, OrderDraftStatus
from apps.intake.models import Clarification
from apps.intake.services import InboundEventService, OrderDraftService


def register_event(event_id, conversation_key, text="Тестовый ответ"):
    return InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id=event_id,
        external_user_id="12345",
        conversation_key=conversation_key,
        raw_text=text,
    ).event


def ambiguous_extraction():
    return OrderExtraction.model_validate_json(
        json.dumps(
            {
                "intent": "create_order",
                "items": [
                    {
                        "raw_product_name": "рыба",
                        "quantity": 1,
                        "unit": "piece",
                        "attributes": [],
                        "confidence": 0.6,
                    }
                ],
                "receiving_type": "pickup",
                "desired_date": None,
                "desired_time_interval": None,
                "delivery_address": None,
                "payment_method": "card_prepayment",
                "customer_comment": None,
                "confirmation": "none",
                "missing_fields": [],
                "clarification_needed": True,
                "confidence": 0.7,
            }
        ),
        strict=True,
    )


@pytest.mark.django_db
def test_ambiguous_product_question_uses_real_catalog_candidates(customer, product):
    second = type(product).objects.create(
        public_code="CLARIFY-SECOND",
        name="Вторая рыба",
        unit=ProductUnit.PIECE,
        min_quantity=Decimal("1"),
        base_price=Decimal("250"),
        is_active=True,
    )
    ProductAlias.objects.create(product=product, alias="рыба")
    ProductAlias.objects.create(product=second, alias="рыба")
    draft, _ = OrderDraftService.get_or_create_active(
        customer=customer,
        channel=Channel.TELEGRAM,
        external_user_id="12345",
        conversation_key="clarify-1",
    )
    draft = DraftExtractionApplier.apply(draft, ambiguous_extraction())
    event = register_event("clarify-event-1", "clarify-1")

    clarification = ClarificationService.sync_next_question(draft, event)

    assert clarification.field_path == "items.0.product"
    assert product.name in clarification.question
    assert second.name in clarification.question
    assert Clarification.objects.filter(status=ClarificationStatus.PENDING).count() == 1


@pytest.mark.django_db
def test_pending_question_is_recorded_as_answered(customer):
    draft, _ = OrderDraftService.get_or_create_active(
        customer=customer,
        channel=Channel.TELEGRAM,
        external_user_id="12345",
        conversation_key="clarify-2",
    )
    draft.status = OrderDraftStatus.NEEDS_CLARIFICATION
    draft.missing_fields = ["receiving_type"]
    draft.save(update_fields=["status", "missing_fields", "updated_at"])
    first = register_event("clarify-event-2", "clarify-2")
    clarification = ClarificationService.sync_next_question(draft, first)
    answer = register_event("clarify-event-3", "clarify-2", "Самовывоз")

    answered = ClarificationService.record_pending_answer(draft, answer)

    answered.refresh_from_db()
    assert answered.pk == clarification.pk
    assert answered.status == ClarificationStatus.ANSWERED
    assert answered.answered_by_event == answer
    assert answered.answer_text == "Самовывоз"


@pytest.mark.django_db
def test_clarification_attempt_limit_escalates_to_manager(customer, settings):
    settings.INTAKE_MAX_CLARIFICATION_ATTEMPTS = 2
    draft, _ = OrderDraftService.get_or_create_active(
        customer=customer,
        channel=Channel.TELEGRAM,
        external_user_id="12345",
        conversation_key="clarify-3",
    )
    draft.status = OrderDraftStatus.NEEDS_CLARIFICATION
    draft.missing_fields = ["receiving_type"]
    draft.save(update_fields=["status", "missing_fields", "updated_at"])

    for number in (1, 2):
        trigger = register_event(f"limit-trigger-{number}", "clarify-3")
        question = ClarificationService.sync_next_question(draft, trigger)
        assert question.attempt_number == number
        answer = register_event(f"limit-answer-{number}", "clarify-3", "не знаю")
        ClarificationService.record_pending_answer(draft, answer)

    final_event = register_event("limit-final", "clarify-3")
    assert ClarificationService.sync_next_question(draft, final_event) is None

    draft.refresh_from_db()
    assert draft.status == OrderDraftStatus.ESCALATED
    assert draft.manager_attention_required is True
    assert "receiving_type" in draft.escalation_reason


@pytest.mark.django_db
def test_malformed_item_path_returns_safe_generic_question(customer):
    draft, _ = OrderDraftService.get_or_create_active(
        customer=customer,
        channel=Channel.TELEGRAM,
        external_user_id="12345",
        conversation_key="clarify-malformed",
    )

    question = ClarificationService._build_question(draft, "items.99.quantity")

    assert question == "Пожалуйста, уточните недостающие данные позиции заказа."


@pytest.mark.django_db
def test_yandex_delivery_phone_question_is_explicit(customer):
    draft, _ = OrderDraftService.get_or_create_active(
        customer=customer,
        channel=Channel.EMAIL,
        external_user_id="email-phone",
        conversation_key="clarify-phone",
    )

    question = ClarificationService._build_question(draft, "contact_phone")

    assert "Яндекс Доставки" in question
    assert "+7" in question
