from collections import deque
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.utils import timezone

from apps.assistant.services import OrderAssistantService
from apps.assistant.schemas import SearchProductsArgs
from apps.assistant.tools import AssistantToolExecutor
from apps.carts.services import CartService
from apps.catalog.models import Product
from apps.common.enums import (
    Channel,
    OrderStatus,
    PaymentMethod,
    ProductUnit,
    ReceivingType,
)
from apps.delivery.models import (
    DeliveryEnvironment,
    DeliveryQuote,
    DeliveryQuoteKind,
    DeliveryQuoteStatus,
)
from apps.intake.ai.providers.base import FunctionCall, ToolCompletion
from apps.intake.enums import (
    AssistantMessageRole,
    AssistantToolCallStatus,
    InboundEventStatus,
    ItemMatchStatus,
    OrderDraftStatus,
    ResolutionSource,
)
from apps.intake.models import AssistantMessage, AssistantToolCall, AssistantTurn, OrderDraftItem
from apps.intake.processors import InboundEventProcessor
from apps.intake.responses import InboundEventResponseService
from apps.intake.services import InboundEventService, OrderDraftService
from apps.orders.models import Order
from apps.orders.services import OrderService
from apps.payments.models import Payment


def test_function_schemas_are_compatible_with_gigachat():
    definitions = AssistantToolExecutor.definitions()

    assert definitions
    assert "anyOf" not in str(definitions)
    configure = next(item for item in definitions if item["name"] == "configure_checkout")
    assert configure["parameters"]["properties"]["receiving_type"]["type"] == "string"
    assert "receiving_type" not in configure["parameters"].get("required", [])


@pytest.mark.parametrize(
    "text",
    [
        "Да",
        "Подтверждаю",
        "Да, подтверждаю этот заказ",
        "Оформляйте заказ",
        "Оформляем",
        "Готов к оплате",
        "Я хочу оплатить свой заказ",
    ],
)
def test_explicit_confirmation_accepts_only_deliberate_phrases(text):
    event = SimpleNamespace(kind="message", raw_payload={}, raw_text=text)

    assert AssistantToolExecutor._explicit_confirmation(event) is True


@pytest.mark.parametrize("text", ["Спасибо", "Покажите итог", "Да, адрес верный"])
def test_explicit_confirmation_rejects_ordinary_dialogue(text):
    event = SimpleNamespace(kind="message", raw_payload={}, raw_text=text)

    assert AssistantToolExecutor._explicit_confirmation(event) is False


def test_preview_response_is_backend_rendered_with_delivery_and_one_confirmation():
    content = OrderAssistantService._render_preview(
        {
            "items": [
                {
                    "name": "Лосось",
                    "quantity": "2.000",
                    "unit": "kg",
                    "unit_label": "килограмм",
                    "unit_price": "1800.00",
                    "line_total": "3600.00000",
                }
            ],
            "receiving_type": "delivery",
            "delivery_address": "Москва, Чистопрудный бульвар, 12",
            "preview": {
                "items_total": "3600.00",
                "discount_amount": "180.00",
                "delivery_cost": "406.16",
                "total_amount": "3826.16",
                "delivery_days": 2,
            },
        }
    )

    assert "Лосось: 2 килограмм × 1800.00 ₽ = 3600.00 ₽" in content
    assert "Стоимость доставки: 406.16 ₽" in content
    assert "Ориентировочный срок: 2 дн." in content
    assert content.lower().count("подтверд") == 1


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Добавьте икру", False),
        ("Икру 2 банки", True),
        ("Добавьте две банки икры", True),
        ("Хочу полкило лосося", True),
    ],
)
def test_cart_mutation_requires_quantity_in_customer_message(text, expected):
    assert AssistantToolExecutor._message_has_quantity(text) is expected


def test_full_catalog_response_contains_price_unit_and_minimum():
    content = OrderAssistantService._render_catalog(
        {
            "scope": "full_catalog",
            "query": "",
            "products": [
                {
                    "name": "Лосось",
                    "price": "1800.00",
                    "unit_label": "Килограмм",
                    "min_quantity": "1.000",
                }
            ],
        }
    )

    assert "Полный каталог" in content
    assert "1800.00 ₽ за килограмм" in content
    assert "минимальный заказ: 1 килограмм" in content


@pytest.mark.django_db
def test_product_search_does_not_mix_fuzzy_match_into_literal_match(product):
    product.name = "Икра лососёвая"
    product.public_code = "TEST-CAVIAR"
    product.save(update_fields=["name", "public_code", "updated_at"])
    Product.objects.create(
        public_code="TEST-CRAB",
        name="Краб камчатский",
        unit=ProductUnit.PACKAGE,
        min_quantity=Decimal("1"),
        base_price=Decimal("4500.00"),
        is_active=True,
    )
    backend = object.__new__(AssistantToolExecutor)

    result = backend._tool_search_products(SearchProductsArgs(query="икра", limit=30))

    assert [item["name"] for item in result["products"]] == ["Икра лососёвая"]


def test_cart_update_response_uses_full_backend_cart_not_model_claim():
    content, response_type, _ = OrderAssistantService._render_tool_response(
        "set_cart_item",
        {
            "ok": True,
            "items": [
                {
                    "name": "Лосось",
                    "quantity": "2.000",
                    "unit": "kg",
                    "unit_label": "килограмм",
                    "unit_price": "1800.00",
                    "line_total": "3600.00",
                },
                {
                    "name": "Форель",
                    "quantity": "3.000",
                    "unit": "kg",
                    "unit_label": "килограмм",
                    "unit_price": "1450.00",
                    "line_total": "4350.00",
                },
            ],
            "missing_fields": [],
        },
        "Добавлена только форель",
    )

    assert response_type == "cart_updated"
    assert "Лосось" in content
    assert "Форель" in content
    assert "Добавлена только форель" not in content


@pytest.mark.django_db
def test_model_history_keeps_user_context_but_redacts_assistant_facts(
    customer, settings
):
    settings.AI_ASSISTANT_HISTORY_MESSAGES = 20
    first = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="history-user-facts",
        external_user_id="12345",
        conversation_key="history-trust-boundary",
        customer=customer,
        raw_text="Какая есть икра?",
    ).event
    AssistantMessage.objects.create(
        event=first,
        conversation_key=first.conversation_key,
        role=AssistantMessageRole.USER,
        content=first.raw_text,
    )
    AssistantMessage.objects.create(
        event=first,
        conversation_key=first.conversation_key,
        role=AssistantMessageRole.ASSISTANT,
        content="Икра и ошибочно краб, цена 999 ₽",
        response_type="catalog",
    )
    current = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="history-current-facts",
        external_user_id="12345",
        conversation_key=first.conversation_key,
        customer=customer,
        raw_text="А икра?",
    ).event

    history = OrderAssistantService._history(current)

    assert any(row["content"] == "Какая есть икра?" for row in history)
    assistant_history = [row["content"] for row in history if row["role"] == "assistant"]
    assert assistant_history
    assert all("ошибочно краб" not in content for content in assistant_history)
    assert all("backend-инструмент" in content for content in assistant_history)


@pytest.mark.django_db
def test_product_card_question_is_backend_rendered_with_exact_description(
    customer, product, settings, monkeypatch
):
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_ORDER_PROCESSING_ENABLED = True
    product.description = "Точный состав из карточки CRM."
    product.save(update_fields=["description", "updated_at"])
    provider = ScriptedProvider([])
    monkeypatch.setattr("apps.assistant.services.get_gigachat_provider", lambda: provider)
    event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="product-card-description",
        external_user_id="12345",
        conversation_key="product-card-dialog",
        customer=customer,
        raw_text=f"Что входит в {product.name}?",
    ).event

    InboundEventProcessor.process(event.pk)
    event.status = InboundEventStatus.PROCESSED
    event.save(update_fields=["status", "updated_at"])
    event.refresh_from_db()
    response = InboundEventResponseService.present(event)["response"]

    assert response["type"] == "catalog"
    assert "Точный состав из карточки CRM." in response["message"]
    assert provider.calls == []


@pytest.mark.django_db
def test_product_question_never_uses_model_memory(
    customer, product, settings, monkeypatch
):
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_ORDER_PROCESSING_ENABLED = True
    product.name = "Икра лососёвая"
    product.public_code = "TEST-CAVIAR-FACTS"
    product.save(update_fields=["name", "public_code", "updated_at"])
    Product.objects.create(
        public_code="TEST-CRAB-FACTS",
        name="Краб камчатский",
        unit=ProductUnit.PACKAGE,
        min_quantity=Decimal("1"),
        base_price=Decimal("4500.00"),
        is_active=True,
    )
    provider = ScriptedProvider([answer("По памяти: икра и краб")])
    monkeypatch.setattr("apps.assistant.services.get_gigachat_provider", lambda: provider)
    event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="catalog-facts-only-from-backend",
        external_user_id="12345",
        conversation_key="catalog-facts-dialog",
        customer=customer,
        raw_text="Какая есть икра?",
    ).event

    InboundEventProcessor.process(event.pk)
    event.status = InboundEventStatus.PROCESSED
    event.save(update_fields=["status", "updated_at"])
    event.refresh_from_db()
    response = InboundEventResponseService.present(event)["response"]

    assert response["type"] == "catalog"
    assert "Икра лососёвая" in response["message"]
    assert "Краб камчатский" not in response["message"]
    assert provider.calls == []


@pytest.mark.django_db
def test_delivery_and_address_steps_are_backend_routed(
    customer, product, settings, monkeypatch
):
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_ORDER_PROCESSING_ENABLED = True
    settings.YANDEX_DELIVERY_ENABLED = True
    conversation = "backend-checkout-routing-dialog"
    draft = seed_active_draft_with_product(customer, product, conversation)
    provider = ScriptedProvider([])
    monkeypatch.setattr("apps.assistant.services.get_gigachat_provider", lambda: provider)

    delivery_event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="backend-checkout-delivery",
        external_user_id="12345",
        conversation_key=conversation,
        customer=customer,
        raw_text="Доставка",
    ).event
    InboundEventProcessor.process(delivery_event.pk)
    delivery_event.status = InboundEventStatus.PROCESSED
    delivery_event.save(update_fields=["status", "updated_at"])
    delivery_event.refresh_from_db()
    delivery_response = InboundEventResponseService.present(delivery_event)["response"]

    address_event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="backend-checkout-address",
        external_user_id="12345",
        conversation_key=conversation,
        customer=customer,
        raw_text="Москва, 1-я Дубровская улица, 5",
    ).event
    InboundEventProcessor.process(address_event.pk)
    address_event.status = InboundEventStatus.PROCESSED
    address_event.save(update_fields=["status", "updated_at"])
    address_event.refresh_from_db()
    address_response = InboundEventResponseService.present(address_event)["response"]
    draft.refresh_from_db()

    assert delivery_response["type"] == "checkout_updated"
    assert "Укажите адрес доставки" in delivery_response["message"]
    assert address_response["type"] == "checkout_updated"
    assert "Выберите способ оплаты" in address_response["message"]
    assert draft.delivery_address == "Москва, 1-я Дубровская улица, 5"
    assert draft.missing_fields == ["payment_method"]
    assert provider.calls == []


@pytest.mark.django_db
def test_set_cart_item_rejects_code_of_different_explicit_product(
    customer, product
):
    trout = Product.objects.create(
        public_code="TEST-TROUT",
        name="Форель",
        unit=ProductUnit.KG,
        min_quantity=Decimal("1"),
        base_price=Decimal("1450"),
        is_active=True,
    )
    event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="wrong-product-code",
        external_user_id="12345",
        conversation_key="wrong-product-dialog",
        customer=customer,
        raw_text="Хочу форель 3 кг",
    ).event
    draft, _ = OrderDraftService.get_or_create_active(
        channel=Channel.TELEGRAM,
        external_user_id="12345",
        conversation_key=event.conversation_key,
        customer=customer,
    )
    turn = AssistantTurn.objects.create(event=event, draft=draft)
    backend = AssistantToolExecutor(event=event, draft=draft, turn=turn)

    result = backend.execute(
        "set_cart_item",
        {"product_code": product.public_code, "quantity": 3.0},
        1,
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "product_mismatch"
    assert result["error"]["mentioned_products"] == [
        {"code": trout.public_code, "name": trout.name}
    ]
    assert draft.items.count() == 0


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


def seed_active_draft_with_product(customer, product, conversation_key):
    draft, _ = OrderDraftService.get_or_create_active(
        channel=Channel.TELEGRAM,
        external_user_id="12345",
        conversation_key=conversation_key,
        customer=customer,
    )
    OrderDraftItem.objects.create(
        draft=draft,
        line_number=1,
        raw_product_name=product.name,
        requested_quantity=product.min_quantity,
        requested_unit=product.unit,
        product=product,
        match_status=ItemMatchStatus.MATCHED,
        candidate_product_ids=[product.pk],
        resolution_source=ResolutionSource.EXACT,
        resolution_confidence=Decimal("1"),
    )
    return draft


@pytest.mark.django_db
def test_short_yes_previews_complete_draft_without_cart_mutation(
    customer, product, delivery_rule, settings, monkeypatch
):
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_ORDER_PROCESSING_ENABLED = True
    settings.YANDEX_DELIVERY_ENABLED = False
    conversation = "ready-preview-dialog"
    draft = seed_active_draft_with_product(customer, product, conversation)
    draft.receiving_type = ReceivingType.PICKUP
    draft.payment_method = PaymentMethod.CASH_ON_DELIVERY
    draft.save(update_fields=["receiving_type", "payment_method", "updated_at"])
    AssistantToolExecutor._refresh_state(draft)
    provider = ScriptedProvider([])
    monkeypatch.setattr("apps.assistant.services.get_gigachat_provider", lambda: provider)
    event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="ready-preview-yes",
        external_user_id="12345",
        conversation_key=conversation,
        customer=customer,
        raw_text="да",
    ).event

    InboundEventProcessor.process(event.pk)
    event.status = InboundEventStatus.PROCESSED
    event.save(update_fields=["status", "updated_at"])
    event.refresh_from_db()
    response = InboundEventResponseService.present(event)["response"]

    assert response["type"] == "order_preview", response
    assert response["message"].lower().count("подтверд") == 1
    assert draft.items.get().requested_quantity == product.min_quantity
    assert provider.calls == []


@pytest.mark.django_db
def test_delivery_quote_is_shown_before_payment_selection(customer, product, settings):
    settings.YANDEX_DELIVERY_ENABLED = False
    conversation = "website-delivery-before-payment"
    draft = seed_active_draft_with_product(customer, product, conversation)
    draft.receiving_type = ReceivingType.DELIVERY
    draft.delivery_address = "Москва, Тверская улица, 1"
    draft.contact_phone = customer.phone
    draft.save(
        update_fields=["receiving_type", "delivery_address", "contact_phone", "updated_at"]
    )
    AssistantToolExecutor._refresh_state(draft)
    event = InboundEventService.register(
        channel=Channel.WEBSITE,
        external_event_id="website-delivery-before-payment",
        external_user_id="web:test",
        conversation_key=conversation,
        customer=customer,
        raw_text="Меня зовут Алексей, 89114564343",
    ).event
    turn = AssistantTurn.objects.create(event=event, draft=draft)

    result = AssistantToolExecutor(event=event, draft=draft, turn=turn).execute(
        "preview_order", {}, 1
    )

    assert result["ok"] is True
    assert result["preliminary_delivery_quote"]["delivery_cost"] == "0.00"
    draft.refresh_from_db()
    assert draft.payment_method == ""
    assert draft.status == OrderDraftStatus.NEEDS_CLARIFICATION
    assert draft.missing_fields == ["payment_method"]
    content, response_type, _ = OrderAssistantService._render_tool_response(
        "preview_order", result, ""
    )
    assert response_type == "delivery_quote"
    assert "Выберите способ оплаты" in content


@pytest.mark.django_db
def test_preview_surfaces_yandex_no_delivery_options(
    customer, product, delivery_rule, settings, monkeypatch
):
    settings.YANDEX_DELIVERY_ENABLED = True
    conversation = "delivery-preview-error-dialog"
    draft = seed_active_draft_with_product(customer, product, conversation)
    draft.receiving_type = ReceivingType.DELIVERY
    draft.delivery_address = "Москва, Тестовая улица, 1"
    draft.payment_method = PaymentMethod.CARD_PREPAYMENT
    draft.contact_phone = customer.phone
    draft.save(
        update_fields=[
            "receiving_type",
            "delivery_address",
            "payment_method",
            "contact_phone",
            "updated_at",
        ]
    )
    AssistantToolExecutor._refresh_state(draft)
    event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="delivery-preview-error",
        external_user_id="12345",
        conversation_key=conversation,
        customer=customer,
        raw_text="Рассчитайте итог",
    ).event
    turn = AssistantTurn.objects.create(event=event, draft=draft)

    def failed_quote(current_draft):
        return DeliveryQuote.objects.create(
            order_draft=current_draft,
            environment=DeliveryEnvironment.TEST,
            kind=DeliveryQuoteKind.PRELIMINARY,
            status=DeliveryQuoteStatus.FAILED,
            request_fingerprint="f" * 64,
            destination_address=current_draft.delivery_address,
            error_code="no_delivery_options",
            error_message="No delivery options for interval",
        )

    monkeypatch.setattr(
        "apps.intake.fulfillment.YandexDeliveryQuoteService.quote_draft",
        failed_quote,
    )
    backend = AssistantToolExecutor(event=event, draft=draft, turn=turn)

    result = backend.execute("preview_order", {}, 1)

    assert result["ok"] is False
    assert result["error"]["code"] == "no_delivery_options"
    assert "Измените адрес" in result["error"]["message"]
    assert result["error"]["provider_message"] == "No delivery options for interval"
    assert result["cart"]["items"][0]["name"] == product.name

    content, response_type, _ = OrderAssistantService._render_tool_response(
        "preview_order", result, ""
    )
    assert response_type == "tool_error"
    assert product.name in content
    assert "Яндекс Доставка не предложила вариант" in content


@pytest.mark.django_db
def test_preview_distinguishes_yandex_test_http_500(
    customer, product, delivery_rule, settings, monkeypatch
):
    settings.YANDEX_DELIVERY_ENABLED = True
    conversation = "delivery-preview-500-dialog"
    draft = seed_active_draft_with_product(customer, product, conversation)
    draft.receiving_type = ReceivingType.DELIVERY
    draft.delivery_address = "Москва, Тестовая улица, 1"
    draft.payment_method = PaymentMethod.CARD_PREPAYMENT
    draft.contact_phone = customer.phone
    draft.save(
        update_fields=[
            "receiving_type",
            "delivery_address",
            "payment_method",
            "contact_phone",
            "updated_at",
        ]
    )
    AssistantToolExecutor._refresh_state(draft)
    event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="delivery-preview-500",
        external_user_id="12345",
        conversation_key=conversation,
        customer=customer,
        raw_text="Рассчитайте итог",
    ).event
    turn = AssistantTurn.objects.create(event=event, draft=draft)

    def failed_quote(current_draft):
        return DeliveryQuote.objects.create(
            order_draft=current_draft,
            environment=DeliveryEnvironment.TEST,
            kind=DeliveryQuoteKind.PRELIMINARY,
            status=DeliveryQuoteStatus.FAILED,
            request_fingerprint="e" * 64,
            destination_address=current_draft.delivery_address,
            error_code="500",
            error_message="Internal Server Error",
        )

    monkeypatch.setattr(
        "apps.intake.fulfillment.YandexDeliveryQuoteService.quote_draft",
        failed_quote,
    )
    monkeypatch.setattr(
        "apps.intake.fulfillment.YandexDeliveryOfferService.create_for_draft",
        failed_quote,
    )
    backend = AssistantToolExecutor(event=event, draft=draft, turn=turn)

    result = backend.execute("preview_order", {}, 1)

    assert result["ok"] is False
    assert "временно недоступен" in result["error"]["message"]
    assert "не предложила вариант" not in result["error"]["message"]


@pytest.mark.django_db
def test_repeat_previous_order_immediately_returns_actual_preview(
    customer, product, delivery_rule, settings, monkeypatch
):
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_ORDER_PROCESSING_ENABLED = True
    settings.YANDEX_DELIVERY_ENABLED = False
    cart = CartService.get_or_create_active_cart(
        channel=Channel.TELEGRAM,
        external_user_id="12345",
        customer=customer,
    )
    CartService.set_item_quantity(cart, product, Decimal("2"))
    previous = OrderService.create_order_from_cart(
        cart,
        customer=customer,
        channel=Channel.TELEGRAM,
        receiving_type=ReceivingType.PICKUP,
        payment_method=PaymentMethod.CASH_ON_DELIVERY,
    )
    provider = ScriptedProvider([])
    monkeypatch.setattr("apps.assistant.services.get_gigachat_provider", lambda: provider)
    event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="repeat-and-preview",
        external_user_id="12345",
        conversation_key="repeat-and-preview-dialog",
        customer=customer,
        raw_text="Можно повторить мой предыдущий заказ?",
    ).event

    InboundEventProcessor.process(event.pk)
    event.status = InboundEventStatus.PROCESSED
    event.save(update_fields=["status", "updated_at"])
    event.refresh_from_db()
    response = InboundEventResponseService.present(event)["response"]

    assert response["type"] == "order_preview", response
    assert product.name in response["message"]
    assert "2" in response["message"]
    assert previous.public_number not in response["message"]
    assert Order.objects.count() == 1
    assert provider.calls == []


@pytest.mark.django_db
def test_ambiguous_cancel_asks_then_clears_current_cart(
    customer, product, settings, monkeypatch
):
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_ORDER_PROCESSING_ENABLED = True
    conversation = "cancel-choice-dialog"
    draft = seed_active_draft_with_product(customer, product, conversation)
    provider = ScriptedProvider([])
    monkeypatch.setattr("apps.assistant.services.get_gigachat_provider", lambda: provider)

    first = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="cancel-choice-1",
        external_user_id="12345",
        conversation_key=conversation,
        customer=customer,
        raw_text="Отмените заказ",
    ).event
    InboundEventProcessor.process(first.pk)
    first.status = InboundEventStatus.PROCESSED
    first.save(update_fields=["status", "updated_at"])
    first.refresh_from_db()
    choice = InboundEventResponseService.present(first)

    assert choice["response"]["type"] == "cancellation_choice"
    assert "что именно" in choice["response"]["message"]
    assert draft.items.count() == 1

    second = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="cancel-choice-2",
        external_user_id="12345",
        conversation_key=conversation,
        customer=customer,
        raw_text="Корзину",
    ).event
    InboundEventProcessor.process(second.pk)
    second.status = InboundEventStatus.PROCESSED
    second.save(update_fields=["status", "updated_at"])
    second.refresh_from_db()

    assert InboundEventResponseService.present(second)["response"]["type"] == "cart_cleared"
    assert draft.items.count() == 0
    assert provider.calls == []


@pytest.mark.django_db
def test_stale_cart_blocks_dialog_until_customer_decides(
    customer, product, settings, monkeypatch
):
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_ORDER_PROCESSING_ENABLED = True
    settings.AI_ASSISTANT_STALE_CART_SECONDS = 3600
    conversation = "stale-cart-dialog"
    draft = seed_active_draft_with_product(customer, product, conversation)
    provider = ScriptedProvider([])
    monkeypatch.setattr("apps.assistant.services.get_gigachat_provider", lambda: provider)
    previous = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="stale-cart-previous",
        external_user_id="12345",
        conversation_key=conversation,
        customer=customer,
        raw_text="Хочу товар",
    ).event
    previous.draft = draft
    previous.save(update_fields=["draft", "updated_at"])
    old_message = AssistantMessage.objects.create(
        event=previous,
        conversation_key=conversation,
        role=AssistantMessageRole.ASSISTANT,
        content="Продолжим позже",
    )
    AssistantMessage.objects.filter(pk=old_message.pk).update(
        created_at=timezone.now() - timedelta(hours=2)
    )

    current = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="stale-cart-current",
        external_user_id="12345",
        conversation_key=conversation,
        customer=customer,
        raw_text="Здравствуйте",
    ).event
    InboundEventProcessor.process(current.pk)
    current.status = InboundEventStatus.PROCESSED
    current.save(update_fields=["status", "updated_at"])
    current.refresh_from_db()
    response = InboundEventResponseService.present(current)

    assert response["response"]["type"] == "stale_cart_choice"
    assert "прошёл час" in response["response"]["message"]
    assert draft.items.count() == 1
    assert provider.calls == []


@pytest.mark.django_db
def test_customer_can_cancel_one_unpaid_placed_order_after_choice(
    customer, product, settings, monkeypatch
):
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_ORDER_PROCESSING_ENABLED = True
    conversation = "cancel-placed-dialog"
    cart = CartService.get_or_create_active_cart(
        channel=Channel.TELEGRAM,
        external_user_id="cancel-order-user",
        customer=customer,
    )
    CartService.set_item_quantity(cart, product, product.min_quantity)
    order = OrderService.create_order_from_cart(
        cart,
        customer=customer,
        channel=Channel.TELEGRAM,
        receiving_type=ReceivingType.PICKUP,
        payment_method=PaymentMethod.CASH_ON_DELIVERY,
    )
    OrderDraftService.get_or_create_active(
        channel=Channel.TELEGRAM,
        external_user_id="12345",
        conversation_key=conversation,
        customer=customer,
    )
    provider = ScriptedProvider([])
    monkeypatch.setattr("apps.assistant.services.get_gigachat_provider", lambda: provider)

    first = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="cancel-placed-1",
        external_user_id="12345",
        conversation_key=conversation,
        customer=customer,
        raw_text="Отмените заказ",
    ).event
    InboundEventProcessor.process(first.pk)
    first.status = InboundEventStatus.PROCESSED
    first.save(update_fields=["status", "updated_at"])
    second = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="cancel-placed-2",
        external_user_id="12345",
        conversation_key=conversation,
        customer=customer,
        raw_text=order.public_number,
    ).event
    InboundEventProcessor.process(second.pk)
    second.status = InboundEventStatus.PROCESSED
    second.save(update_fields=["status", "updated_at"])
    order.refresh_from_db()
    second.refresh_from_db()

    assert order.order_status == OrderStatus.CANCELLED
    assert InboundEventResponseService.present(second)["response"]["type"] == "order_cancelled"
    assert provider.calls == []


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
            tool("confirm_order", {"preview_revision": 4}),
            answer("Без явного подтверждения заказ не создан. Напишите «подтверждаю»."),
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
    assert "отдельного явного подтверждения" in refused["response"]["message"]
    final = InboundEventResponseService.present(events[-1])
    assert "Ваш заказ оформлен" in final["response"]["message"]
    assert order.public_number in final["response"]["message"]
    assert final["response"]["action_url"].endswith("/tools-agent")
    assert events[-1].assistant_turn.model_calls == 0

    calls_before = len(provider.calls)
    OrderAssistantService.process(events[-1], events[-1].draft, provider=provider)
    assert len(provider.calls) == calls_before
    assert Order.objects.count() == 1
    assert Payment.objects.count() == 1

    last_prompt_messages = provider.calls[-1]["messages"]
    assert not any(
        "Проверьте заказ:" in message.get("content", "")
        for message in last_prompt_messages
    )
    assert any(
        "ответ типа order_preview" in message.get("content", "")
        for message in last_prompt_messages
    )
    assert product.public_code in provider.calls[-1]["system_prompt"]
    assert all("parameters" in function for function in provider.calls[0]["functions"])
