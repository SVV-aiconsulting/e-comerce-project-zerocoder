import json
from decimal import Decimal

import pytest

from apps.catalog.models import ProductAlias
from apps.common.enums import Channel, ProductUnit
from apps.intake.ai.schemas import OrderExtraction
from apps.intake.draft_application import DraftExtractionApplier
from apps.intake.enums import ItemMatchStatus, OrderDraftStatus
from apps.intake.services import OrderDraftService


def extraction(**overrides):
    payload = {
        "intent": "create_order",
        "items": [
            {
                "raw_product_name": "тестовый товар",
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
        "customer_comment": None,
        "confirmation": "none",
        "missing_fields": [],
        "clarification_needed": False,
        "confidence": 0.93,
    }
    payload.update(overrides)
    return OrderExtraction.model_validate_json(json.dumps(payload), strict=True)


@pytest.mark.django_db
def test_apply_extraction_creates_ready_draft_item(customer, product):
    draft, _ = OrderDraftService.get_or_create_active(
        customer=customer,
        channel=Channel.TELEGRAM,
        external_user_id="12345",
        conversation_key="apply-1",
    )

    result = DraftExtractionApplier.apply(draft, extraction())

    item = result.items.get()
    assert result.status == OrderDraftStatus.READY_FOR_PREVIEW
    assert result.revision == 2
    assert result.missing_fields == []
    assert item.product == product
    assert item.requested_quantity == Decimal("2")
    assert item.match_status == ItemMatchStatus.MATCHED


@pytest.mark.django_db
def test_apply_extraction_marks_ambiguous_product_for_clarification(customer, product):
    ProductAlias.objects.create(product=product, alias="рыба")
    second = type(product).objects.create(
        public_code="SECOND-TEST",
        name="Второй товар",
        unit=ProductUnit.PIECE,
        min_quantity=Decimal("1"),
        base_price=Decimal("200"),
        is_active=True,
    )
    ProductAlias.objects.create(product=second, alias="рыба")
    draft, _ = OrderDraftService.get_or_create_active(
        customer=customer,
        channel=Channel.TELEGRAM,
        external_user_id="12345",
        conversation_key="apply-2",
    )
    parsed = extraction(
        items=[
            {
                "raw_product_name": "рыба",
                "quantity": 1,
                "unit": "piece",
                "attributes": [],
                "confidence": 0.7,
            }
        ]
    )

    result = DraftExtractionApplier.apply(draft, parsed)

    item = result.items.get()
    assert result.status == OrderDraftStatus.NEEDS_CLARIFICATION
    assert "items.0.product" in result.missing_fields
    assert item.match_status == ItemMatchStatus.AMBIGUOUS
    assert set(item.candidate_product_ids) == {product.pk, second.pk}


@pytest.mark.django_db
def test_server_validation_rejects_unit_mismatch(customer, product):
    draft, _ = OrderDraftService.get_or_create_active(
        customer=customer,
        channel=Channel.TELEGRAM,
        external_user_id="12345",
        conversation_key="apply-3",
    )
    parsed = extraction(
        items=[
            {
                "raw_product_name": product.name,
                "quantity": 1,
                "unit": "kg",
                "attributes": [],
                "confidence": 0.9,
            }
        ]
    )

    result = DraftExtractionApplier.apply(draft, parsed)

    item = result.items.get()
    assert result.status == OrderDraftStatus.NEEDS_CLARIFICATION
    assert "unit_mismatch" in item.validation_errors
    assert item.match_status == ItemMatchStatus.INVALID


@pytest.mark.django_db
def test_yandex_delivery_requires_operational_phone(customer, product, settings):
    settings.YANDEX_DELIVERY_ENABLED = True
    draft, _ = OrderDraftService.get_or_create_active(
        customer=customer,
        channel=Channel.EMAIL,
        external_user_id="email-user",
        conversation_key="delivery-phone",
    )

    result = DraftExtractionApplier.apply(
        draft,
        extraction(
            receiving_type="delivery",
            delivery_address="Москва, Тверская улица, 1",
        ),
    )

    assert result.status == OrderDraftStatus.NEEDS_CLARIFICATION
    assert "contact_phone" in result.missing_fields


@pytest.mark.django_db
def test_yandex_delivery_rejects_cash_payment(customer, product, settings):
    settings.YANDEX_DELIVERY_ENABLED = True
    draft, _ = OrderDraftService.get_or_create_active(
        customer=customer,
        channel=Channel.TELEGRAM,
        external_user_id="cash-user",
        conversation_key="delivery-payment",
    )
    draft.contact_phone = customer.phone
    draft.save(update_fields=["contact_phone", "updated_at"])

    result = DraftExtractionApplier.apply(
        draft,
        extraction(
            receiving_type="delivery",
            delivery_address="Москва, Тверская улица, 1",
            payment_method="cash_on_delivery",
        ),
    )

    assert result.status == OrderDraftStatus.NEEDS_CLARIFICATION
    assert "delivery_payment_method" in result.missing_fields


@pytest.mark.django_db
def test_greeting_never_attempts_order_preview(customer):
    draft, _ = OrderDraftService.get_or_create_active(
        customer=customer,
        channel=Channel.TELEGRAM,
        external_user_id="greeting-user",
        conversation_key="greeting-conversation",
    )

    result = DraftExtractionApplier.apply(
        draft,
        extraction(intent="unknown", items=[]),
    )

    assert result.status == OrderDraftStatus.NEEDS_CLARIFICATION
    assert result.missing_fields == ["assistant_intent"]


@pytest.mark.django_db
def test_product_question_without_order_intent_stays_in_consultation(customer, product):
    draft, _ = OrderDraftService.get_or_create_active(
        customer=customer,
        channel=Channel.TELEGRAM,
        external_user_id="question-user",
        conversation_key="question-conversation",
    )

    result = DraftExtractionApplier.apply(
        draft,
        extraction(intent="product_question"),
    )

    assert result.status == OrderDraftStatus.NEEDS_CLARIFICATION
    assert result.missing_fields == ["sales_intent"]
