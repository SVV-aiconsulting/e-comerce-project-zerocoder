from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from apps.common.enums import Channel, CustomerSource, ProductUnit, ReceivingType
from apps.intake.enums import (
    AIRunPurpose,
    ClarificationStatus,
    ItemMatchStatus,
    OrderDraftStatus,
)
from apps.intake.models import AIExtractionRun, Clarification, OrderDraft, OrderDraftItem
from apps.intake.services import InboundEventService, OrderDraftService


@pytest.mark.django_db
def test_email_is_supported_channel_and_customer_source():
    assert Channel.EMAIL == "email"
    assert CustomerSource.EMAIL == "email"


@pytest.mark.django_db
def test_email_channel_maps_to_email_customer_source():
    from apps.customers.services import CustomerService

    assert CustomerService.resolve_source_from_channel(Channel.EMAIL) == CustomerSource.EMAIL


@pytest.mark.django_db
def test_inbound_event_registration_is_idempotent():
    payload = {
        "channel": Channel.TELEGRAM,
        "external_event_id": "message-100",
        "external_user_id": "user-1",
        "conversation_key": "chat-1",
        "raw_text": "Две упаковки креветок",
    }

    first = InboundEventService.register(**payload)
    second = InboundEventService.register(**{**payload, "raw_text": "дубликат"})

    assert first.created is True
    assert second.created is False
    assert first.event.pk == second.event.pk
    assert second.event.raw_text == "Две упаковки креветок"


@pytest.mark.django_db
def test_only_one_active_draft_per_conversation(customer):
    defaults = {
        "customer": customer,
        "channel": Channel.TELEGRAM,
        "external_user_id": "user-1",
        "conversation_key": "chat-1",
    }
    OrderDraft.objects.create(**defaults)

    with pytest.raises(IntegrityError), transaction.atomic():
        OrderDraft.objects.create(**defaults)


@pytest.mark.django_db
def test_new_draft_allowed_after_previous_is_cancelled(customer):
    defaults = {
        "customer": customer,
        "channel": Channel.TELEGRAM,
        "external_user_id": "user-1",
        "conversation_key": "chat-1",
    }
    old = OrderDraft.objects.create(**defaults)
    old.status = OrderDraftStatus.CANCELLED
    old.save(update_fields=["status", "updated_at"])

    new = OrderDraft.objects.create(**defaults)

    assert new.pk != old.pk


@pytest.mark.django_db
def test_get_or_create_active_draft_reuses_conversation(customer):
    first, first_created = OrderDraftService.get_or_create_active(
        customer=customer,
        channel=Channel.TELEGRAM,
        external_user_id="user-1",
        conversation_key="chat-1",
    )
    second, second_created = OrderDraftService.get_or_create_active(
        customer=customer,
        channel=Channel.TELEGRAM,
        external_user_id="user-1",
        conversation_key="chat-1",
    )

    assert first_created is True
    assert second_created is False
    assert second.pk == first.pk


@pytest.mark.django_db
def test_matched_item_requires_product(customer):
    draft = OrderDraft.objects.create(
        customer=customer,
        channel=Channel.TELEGRAM,
        external_user_id="user-1",
        conversation_key="chat-1",
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        OrderDraftItem.objects.create(
            draft=draft,
            line_number=1,
            raw_product_name="Креветки",
            requested_quantity=Decimal("1"),
            requested_unit=ProductUnit.PACKAGE,
            match_status=ItemMatchStatus.MATCHED,
        )


@pytest.mark.django_db
def test_only_one_pending_clarification_per_field(customer):
    draft = OrderDraft.objects.create(
        customer=customer,
        channel=Channel.TELEGRAM,
        external_user_id="user-1",
        conversation_key="chat-1",
    )
    Clarification.objects.create(
        draft=draft,
        field_path="items.0.product",
        question="Какой товар выбрать?",
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        Clarification.objects.create(
            draft=draft,
            field_path="items.0.product",
            question="Уточните товар",
            status=ClarificationStatus.PENDING,
        )


@pytest.mark.django_db
def test_ai_run_keeps_prompt_version_and_structured_output(customer):
    event = InboundEventService.register(
        channel=Channel.EMAIL,
        external_event_id="email-1",
        external_user_id="buyer@example.test",
        conversation_key="thread-1",
        raw_text="Нужен тестовый товар",
    ).event
    run = AIExtractionRun.objects.create(
        event=event,
        purpose=AIRunPurpose.EXTRACTION,
        prompt_id="ORDER_EXTRACTION",
        prompt_version="1.0.0",
        input_hash="a" * 64,
        structured_output={"intent": "create_order"},
    )

    assert run.prompt_version == "1.0.0"
    assert run.structured_output["intent"] == "create_order"


@pytest.mark.django_db
def test_preview_confirmation_and_change_invalidate_confirmation(customer, product):
    draft, created = OrderDraftService.get_or_create_active(
        customer=customer,
        channel=Channel.TELEGRAM,
        external_user_id="user-1",
        conversation_key="chat-1",
    )
    assert created is True
    draft.receiving_type = ReceivingType.PICKUP
    draft.save(update_fields=["receiving_type", "updated_at"])
    OrderDraftItem.objects.create(
        draft=draft,
        line_number=1,
        raw_product_name=product.name,
        requested_quantity=Decimal("2"),
        requested_unit=product.unit,
        product=product,
        match_status=ItemMatchStatus.MATCHED,
    )

    previewed = OrderDraftService.record_preview(
        draft,
        items_total=Decimal("200"),
        discount_amount=Decimal("0"),
        delivery_cost=Decimal("0"),
        total_amount=Decimal("200"),
    )
    confirmed = OrderDraftService.confirm(previewed)

    assert confirmed.status == OrderDraftStatus.CONFIRMED
    assert confirmed.confirmed_revision == confirmed.revision

    changed = OrderDraftService.record_change(confirmed)

    assert changed.status == OrderDraftStatus.COLLECTING
    assert changed.revision == 2
    assert changed.previewed_revision is None
    assert changed.confirmed_revision is None
    assert changed.total_amount is None
