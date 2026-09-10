from collections import deque
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.core.management import call_command
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
from apps.intake.models import (
    AssistantMessage,
    AssistantToolCall,
    AssistantTurn,
    ConversationMemory,
    OrderDraftItem,
)
from apps.intake.processors import InboundEventProcessor
from apps.intake.responses import InboundEventResponseService
from apps.intake.services import InboundEventService, OrderDraftService
from apps.intake.fulfillment import DraftPricingService
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
        "Ок",
        "ок",
        "OK",
        "Оформляйте заказ",
        "Оформляем",
        "Согласен",
        "Оформите заказ",
        "Можно оформлять",
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


def test_email_confirmation_ignores_reply_subject():
    event = SimpleNamespace(
        kind="message",
        channel="email",
        raw_payload={},
        raw_text="Тема: Re: Ваш заказ\n\nСогласен",
    )
    assert AssistantToolExecutor._explicit_confirmation(event) is True


def test_email_confirmation_ignores_quoted_order_thread():
    event = SimpleNamespace(
        kind="message",
        channel="email",
        raw_payload={},
        raw_text=(
            "Тема: Re: Ваш заказ\n\nСогласен\n\n"
            "> Проверьте заказ: ...\n"
        ),
    )

    assert AssistantToolExecutor._explicit_confirmation(event) is True


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


def test_product_word_typo_matches_only_close_catalog_word():
    assert AssistantToolExecutor._variant_is_mentioned(
        "Какой состав дигустационного набора?", "Дегустационный набор"
    )
    assert not AssistantToolExecutor._variant_is_mentioned(
        "Какая есть икра?", "Краб камчатский"
    )


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
def test_consultant_resolves_price_followup_from_server_backed_options(
    customer, product, settings
):
    settings.AI_CONSULTANT_ENABLED = True
    other = Product.objects.create(
        public_code="TEST-TROUT",
        name="Форель",
        unit=product.unit,
        min_quantity=product.min_quantity,
        base_price=Decimal("80.00"),
        is_active=True,
    )
    event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="referential-price",
        external_user_id="12345",
        conversation_key="referential-dialog",
        customer=customer,
        raw_text="Какой из них дешевле?",
    ).event
    draft, _ = OrderDraftService.get_or_create_active(
        channel=event.channel,
        external_user_id=event.external_user_id,
        conversation_key=event.conversation_key,
        customer=customer,
    )
    turn = AssistantTurn.objects.create(event=event, draft=draft)
    ConversationMemory.objects.create(
        channel=event.channel,
        external_user_id=event.external_user_id,
        conversation_key=event.conversation_key,
        options=[
            {"code": product.public_code, "name": product.name},
            {"code": other.public_code, "name": other.name},
        ],
    )
    backend = AssistantToolExecutor(event=event, draft=draft, turn=turn)

    action = backend.referential_catalog_action()
    assert action == (
        "compare_products",
        {"product_codes": [product.public_code, other.public_code]},
    )
    result = backend.execute(*action, call_index=1)
    content = OrderAssistantService._render_catalog(result)
    assert "Самая низкая цена" in content
    assert "Форель — 80.00 ₽" in content

    backend.event.raw_text = "Расскажите про второй"
    assert backend.referential_catalog_action() == (
        "search_products",
        {"query": "Форель", "limit": 1},
    )


def test_website_identity_request_is_a_separate_natural_language_step():
    message = AssistantToolExecutor._missing_fields_message(["customer"])

    assert message.startswith("Для оформления заказа прошу сообщить Ваше имя")
    assert message.endswith("9XXXXXXXXX.")
    assert "одном сообщении" not in message


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


@pytest.mark.django_db
def test_missing_haddock_does_not_fuzzy_match_caviar(product):
    product.name = "Икра лососёвая"
    product.public_code = "TEST-CAVIAR-NOT-HADDOCK"
    product.save(update_fields=["name", "public_code", "updated_at"])
    backend = object.__new__(AssistantToolExecutor)

    result = backend._tool_search_products(SearchProductsArgs(query="пикша", limit=30))

    assert result["products"] == []


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
def test_model_history_preserves_real_questions_and_prior_answers(
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
    assert assistant_history == ["Икра и ошибочно краб, цена 999 ₽"]
    # Historical text is context, not authoritative product data.


@pytest.mark.django_db
def test_catalog_action_prefers_specific_red_fish_alias(customer, product):
    from apps.catalog.models import ProductAlias

    ProductAlias.objects.create(product=product, alias="рыба")
    ProductAlias.objects.create(product=product, alias="красная рыба")
    white = Product.objects.create(
        public_code="WHITE-FISH",
        name="Треска",
        unit=product.unit,
        min_quantity=product.min_quantity,
        base_price=product.base_price,
        is_active=True,
    )
    ProductAlias.objects.create(product=white, alias="рыба")
    event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="red-fish-catalog",
        external_user_id="12345",
        conversation_key="red-fish-dialog",
        customer=customer,
        raw_text="Что у вас есть из красной рыбы?",
    ).event
    draft, _ = OrderDraftService.get_or_create_active(
        channel=event.channel,
        external_user_id=event.external_user_id,
        conversation_key=event.conversation_key,
        customer=customer,
    )
    turn = AssistantTurn.objects.create(event=event, draft=draft)
    backend = AssistantToolExecutor(event=event, draft=draft, turn=turn)

    assert backend.catalog_action() == (
        "search_products",
        {"query": "красная рыба", "limit": 30},
    )
    result = backend._tool_search_products(SearchProductsArgs(query="красная рыба", limit=30))
    assert [row["name"] for row in result["products"]] == [product.name]


@pytest.mark.django_db
def test_consultant_catalog_queries_are_specific_and_support_multiple_categories(
    customer, settings
):
    settings.AI_CONSULTANT_ENABLED = True
    call_command("load_demo_data")

    def search(text, suffix):
        event = InboundEventService.register(
            channel=Channel.TELEGRAM,
            external_event_id=f"specific-catalog-{suffix}",
            external_user_id="specific-catalog-user",
            conversation_key="specific-catalog-dialog",
            customer=customer,
            raw_text=text,
        ).event
        draft, _ = OrderDraftService.get_or_create_active(
            channel=event.channel,
            external_user_id=event.external_user_id,
            conversation_key=event.conversation_key,
            customer=customer,
        )
        turn = AssistantTurn.objects.create(event=event, draft=draft)
        backend = AssistantToolExecutor(event=event, draft=draft, turn=turn)
        action = backend.catalog_action()
        assert action is not None
        return backend.execute(*action, call_index=1)

    red = search("Какая у вас есть красная рыба?", "red")
    mixed = search("А есть белая рыба и креветки?", "mixed")

    assert [row["code"] for row in red["products"]] == [
        "DEMO-SALMON",
        "DEMO-TROUT",
    ]
    assert [row["code"] for row in mixed["products"]] == [
        "DEMO-COD",
        "DEMO-SHRIMP",
        "DEMO-FLOUNDER",
    ]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "text",
    [
        "Покажите каталог",
        "Что у вас есть в продаже?",
        "Покажите ваши товары",
        "Что вы продаёте?",
    ],
)
def test_natural_full_catalog_phrases_return_full_catalog(
    text, customer, settings
):
    settings.AI_CONSULTANT_ENABLED = True
    call_command("load_demo_data")
    event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id=f"full-catalog-{abs(hash(text))}",
        external_user_id="full-catalog-user",
        conversation_key="full-catalog-dialog",
        customer=customer,
        raw_text=text,
    ).event
    draft, _ = OrderDraftService.get_or_create_active(
        channel=event.channel,
        external_user_id=event.external_user_id,
        conversation_key=event.conversation_key,
        customer=customer,
    )
    turn = AssistantTurn.objects.create(event=event, draft=draft)
    backend = AssistantToolExecutor(event=event, draft=draft, turn=turn)

    assert backend.catalog_action() == (
        "search_products",
        {"query": "", "limit": 30},
    )


@pytest.mark.django_db
def test_inflected_specific_alias_does_not_degrade_to_general_alias(
    customer, settings
):
    settings.AI_CONSULTANT_ENABLED = True
    call_command("load_demo_data")
    event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="white-fish-inflected",
        external_user_id="white-fish-user",
        conversation_key="white-fish-dialog",
        customer=customer,
        raw_text="Что у вас есть из белой рыбы?",
    ).event
    draft, _ = OrderDraftService.get_or_create_active(
        channel=event.channel,
        external_user_id=event.external_user_id,
        conversation_key=event.conversation_key,
        customer=customer,
    )
    turn = AssistantTurn.objects.create(event=event, draft=draft)
    backend = AssistantToolExecutor(event=event, draft=draft, turn=turn)

    action = backend.catalog_action()
    assert action == ("search_products", {"query": "белая рыба", "limit": 30})
    result = backend.execute(*action, call_index=1)
    assert [row["code"] for row in result["products"]] == [
        "DEMO-COD",
        "DEMO-FLOUNDER",
    ]


@pytest.mark.django_db
def test_white_fish_selection_and_absent_fish_use_verified_catalog_matches(
    customer, settings
):
    settings.AI_CONSULTANT_ENABLED = True
    call_command("load_demo_data")

    def backend_for(text, suffix):
        event = InboundEventService.register(
            channel=Channel.TELEGRAM,
            external_event_id=f"catalog-selection-{suffix}",
            external_user_id="catalog-selection-user",
            conversation_key="catalog-selection-dialog",
            customer=customer,
            raw_text=text,
        ).event
        draft, _ = OrderDraftService.get_or_create_active(
            channel=event.channel,
            external_user_id=event.external_user_id,
            conversation_key=event.conversation_key,
            customer=customer,
        )
        turn = AssistantTurn.objects.create(event=event, draft=draft)
        return AssistantToolExecutor(event=event, draft=draft, turn=turn)

    white = backend_for("Хочу белую рыбу", "white")
    white_action = white.catalog_action()
    assert white_action == (
        "search_products",
        {"query": "белая рыба", "limit": 30},
    )
    assert {row["code"] for row in white.execute(*white_action, call_index=1)["products"]} == {
        "DEMO-COD",
        "DEMO-FLOUNDER",
    }

    haddock = backend_for("А где пикша?", "haddock")
    haddock_action = haddock.catalog_action()
    assert haddock_action[0] == "recommend_products"
    result = haddock.execute(*haddock_action, call_index=1)
    assert result["unavailable_item"] == "пикша"
    assert {row["code"] for row in result["products"]} == {
        "DEMO-COD",
        "DEMO-FLOUNDER",
    }

    sockeye = backend_for("Есть нерка?", "sockeye")
    sockeye_action = sockeye.catalog_action()
    assert sockeye_action[0] == "recommend_products"
    result = sockeye.execute(*sockeye_action, call_index=1)
    assert result["unavailable_item"] == "нерка"
    assert {row["code"] for row in result["products"]} == {
        "DEMO-SALMON",
        "DEMO-TROUT",
    }

    follow_up = backend_for("и тунец", "tuna-follow-up")
    follow_up_action = follow_up.catalog_action()
    assert follow_up_action is not None
    assert [row["code"] for row in follow_up.execute(
        *follow_up_action, call_index=1
    )["products"]] == ["DEMO-TUNA"]


@pytest.mark.django_db
def test_semantic_catalog_context_contains_complete_product_cards(
    customer, product
):
    product.description = "Описание из актуального каталога"
    product.save(update_fields=["description", "updated_at"])
    event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="semantic-catalog-context",
        external_user_id="semantic-user",
        conversation_key="semantic-dialog",
        customer=customer,
        raw_text="Что есть из моллюсков?",
    ).event
    draft, _ = OrderDraftService.get_or_create_active(
        channel=event.channel,
        external_user_id=event.external_user_id,
        conversation_key=event.conversation_key,
        customer=customer,
    )
    turn = AssistantTurn.objects.create(event=event, draft=draft)
    snapshot = AssistantToolExecutor(event=event, draft=draft, turn=turn).context_payload()[
        "catalog_snapshot"
    ]

    card = next(row for row in snapshot if row["code"] == product.public_code)
    assert card["name"] == product.name
    assert card["description"] == "Описание из актуального каталога"
    assert "aliases" in card
    assert card["unit"]
    assert card["min_quantity"]
    assert card["price"] == str(product.base_price)
    assert card["currency"] == "RUB"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "text",
    [
        "Что у вас есть из малюсков?",
        "Есть ли у вас пикша?",
        "Посоветуйте что-нибудь нежное",
        "Добавьте треску и пикшу",
    ],
)
def test_semantic_catalog_requests_are_routed_to_catalog_analysis(
    text, customer, settings
):
    settings.AI_CONSULTANT_ENABLED = True
    call_command("load_demo_data")
    event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id=f"semantic-route-{abs(hash(text))}",
        external_user_id="semantic-route-user",
        conversation_key="semantic-route-dialog",
        customer=customer,
        raw_text=text,
    ).event
    draft, _ = OrderDraftService.get_or_create_active(
        channel=event.channel,
        external_user_id=event.external_user_id,
        conversation_key=event.conversation_key,
        customer=customer,
    )
    turn = AssistantTurn.objects.create(event=event, draft=draft)
    backend = AssistantToolExecutor(event=event, draft=draft, turn=turn)

    assert backend.semantic_catalog_request() is True


@pytest.mark.django_db
def test_semantic_recommendation_returns_only_validated_catalog_cards(
    customer, settings, monkeypatch
):
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_CONSULTANT_ENABLED = True
    call_command("load_demo_data")
    codes = ["DEMO-SCALLOP", "DEMO-MUSSELS", "DEMO-SQUID", "DEMO-OCTOPUS"]
    provider = ScriptedProvider([
        tool("recommend_products", {
            "query": "моллюски",
            "product_codes": codes,
            "unavailable_item": "",
            "alternative_alias": "",
        }),
        answer("Какой из вариантов вам больше подходит?"),
    ])
    monkeypatch.setattr(
        "apps.assistant.services.get_gigachat_provider", lambda: provider
    )
    event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="semantic-molluscs",
        external_user_id="semantic-molluscs-user",
        conversation_key="semantic-molluscs-dialog",
        customer=customer,
        raw_text="Что подойдёт для быстрого приготовления?",
    ).event

    InboundEventProcessor.process(event.pk)
    event.status = InboundEventStatus.PROCESSED
    event.save(update_fields=["status", "updated_at"])
    event.refresh_from_db()
    response = InboundEventResponseService.present(event)["response"]["message"]

    for name in ("Гребешок морской", "Мидии в створках", "Кальмар очищенный", "Осьминог"):
        assert name in response
    assert "Краб камчатский" not in response
    assert "Морской еж" not in response
    assert [definition["name"] for definition in provider.calls[0]["functions"]] == [
        "recommend_products"
    ]
    assert "set_cart_item" in {
        definition["name"] for definition in provider.calls[1]["functions"]
    }


@pytest.mark.django_db
def test_misspelled_mollusc_category_returns_every_managed_catalog_match(
    customer, settings
):
    settings.AI_CONSULTANT_ENABLED = True
    call_command("load_demo_data")
    event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="managed-molluscs",
        external_user_id="managed-molluscs-user",
        conversation_key="managed-molluscs-dialog",
        customer=customer,
        raw_text="Что у вас есть из малюсков?",
    ).event
    draft, _ = OrderDraftService.get_or_create_active(
        channel=event.channel,
        external_user_id=event.external_user_id,
        conversation_key=event.conversation_key,
        customer=customer,
    )
    turn = AssistantTurn.objects.create(event=event, draft=draft)
    backend = AssistantToolExecutor(event=event, draft=draft, turn=turn)

    action = backend.catalog_action()
    assert action == ("search_products", {"query": "малюски", "limit": 30})
    result = backend.execute(*action, call_index=1)
    assert {row["code"] for row in result["products"]} == {
        "DEMO-SCALLOP",
        "DEMO-MUSSELS",
        "DEMO-SQUID",
        "DEMO-OCTOPUS",
    }


@pytest.mark.django_db
def test_unavailable_product_names_absence_and_real_alternative(
    customer, settings, monkeypatch
):
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_CONSULTANT_ENABLED = True
    call_command("load_demo_data")
    provider = ScriptedProvider([
        tool("recommend_products", {
            "query": "пикша",
            "product_codes": ["DEMO-COD", "DEMO-SQUID"],
            "unavailable_item": "пикша",
            "alternative_alias": "белая рыба",
        }),
        answer("Подойдёт треска или подобрать по другому критерию?"),
    ])
    monkeypatch.setattr(
        "apps.assistant.services.get_gigachat_provider", lambda: provider
    )
    event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="unavailable-haddock",
        external_user_id="unavailable-user",
        conversation_key="unavailable-dialog",
        customer=customer,
        raw_text="Есть ли у вас пикша?",
    ).event

    InboundEventProcessor.process(event.pk)
    event.status = InboundEventStatus.PROCESSED
    event.save(update_fields=["status", "updated_at"])
    event.refresh_from_db()
    response = InboundEventResponseService.present(event)["response"]["message"]

    assert "«пикша» сейчас нет" in response
    assert "Треска" in response
    assert "Лосось" not in response
    assert "Кальмар" not in response


@pytest.mark.django_db
def test_multiple_items_without_conjunction_keep_each_quantity(
    customer, settings
):
    settings.AI_CONSULTANT_ENABLED = True
    call_command("load_demo_data")
    event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="multi-without-conjunction",
        external_user_id="multi-without-conjunction-user",
        conversation_key="multi-without-conjunction-dialog",
        customer=customer,
        raw_text="тунец 1 кг осьминог 2 кг",
    ).event
    draft, _ = OrderDraftService.get_or_create_active(
        channel=event.channel,
        external_user_id=event.external_user_id,
        conversation_key=event.conversation_key,
        customer=customer,
    )
    turn = AssistantTurn.objects.create(event=event, draft=draft)
    actions = AssistantToolExecutor(event=event, draft=draft, turn=turn).cart_mutation_actions()

    assert actions == [
        ("set_cart_item", {"product_code": "DEMO-TUNA", "quantity": 1.0}),
        ("set_cart_item", {"product_code": "DEMO-OCTOPUS", "quantity": 2.0}),
    ]


@pytest.mark.django_db
def test_multiple_named_items_without_quantities_are_kept_for_followup(
    customer, settings
):
    settings.AI_CONSULTANT_ENABLED = True
    call_command("load_demo_data")
    event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="multi-selection-no-quantity",
        external_user_id="multi-selection-user",
        conversation_key="multi-selection-dialog",
        customer=customer,
        raw_text="Хочу заказать тунца и осьминога",
    ).event
    draft, _ = OrderDraftService.get_or_create_active(
        channel=event.channel,
        external_user_id=event.external_user_id,
        conversation_key=event.conversation_key,
        customer=customer,
    )
    turn = AssistantTurn.objects.create(event=event, draft=draft)
    backend = AssistantToolExecutor(event=event, draft=draft, turn=turn)

    action = backend.catalog_action()
    assert action == (
        "search_products",
        {"query": "Осьминог | Тунец", "limit": 30},
    )
    result = backend.execute(*action, call_index=1)
    assert result["scope"] == "selection"
    assert {row["code"] for row in result["products"]} == {
        "DEMO-TUNA",
        "DEMO-OCTOPUS",
    }


@pytest.mark.django_db
def test_known_and_unknown_quantified_items_are_not_partially_applied(
    customer, settings
):
    settings.AI_CONSULTANT_ENABLED = True
    call_command("load_demo_data")
    event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="known-unknown-items",
        external_user_id="known-unknown-user",
        conversation_key="known-unknown-dialog",
        customer=customer,
        raw_text="Добавьте треску 1 кг и пикшу 1 кг",
    ).event
    draft, _ = OrderDraftService.get_or_create_active(
        channel=event.channel,
        external_user_id=event.external_user_id,
        conversation_key=event.conversation_key,
        customer=customer,
    )
    turn = AssistantTurn.objects.create(event=event, draft=draft)
    backend = AssistantToolExecutor(event=event, draft=draft, turn=turn)

    assert backend.cart_mutation_actions() is None
    assert draft.items.count() == 0


@pytest.mark.django_db
def test_multi_item_order_reports_absent_fish_with_catalog_alternatives(
    customer, settings, monkeypatch
):
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_CONSULTANT_ENABLED = True
    call_command("load_demo_data")
    provider = ScriptedProvider([])
    monkeypatch.setattr(
        "apps.assistant.services.get_gigachat_provider", lambda: provider
    )
    event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="multi-item-with-haddock",
        external_user_id="multi-item-with-haddock-user",
        conversation_key="multi-item-with-haddock-dialog",
        customer=customer,
        raw_text="Хочу заказать кальмара 2 упаковки, икру 1 банку, камбалу 1 кг и пикшу 1 кг",
    ).event

    draft, _ = OrderDraftService.get_or_create_active(
        channel=event.channel,
        external_user_id=event.external_user_id,
        conversation_key=event.conversation_key,
        customer=customer,
    )
    turn = AssistantTurn.objects.create(event=event, draft=draft)
    backend = AssistantToolExecutor(event=event, draft=draft, turn=turn)
    unavailable = backend.unavailable_catalog_action()
    assert unavailable is not None
    assert unavailable[0] == "recommend_products"

    InboundEventProcessor.process(event.pk)
    event.status = InboundEventStatus.PROCESSED
    event.save(update_fields=["status", "updated_at"])
    event.refresh_from_db()
    response = InboundEventResponseService.present(event)["response"]["message"]

    assert set(
        event.draft.items.values_list("product__public_code", flat=True)
    ) == {"DEMO-SQUID", "DEMO-CAVIAR", "DEMO-FLOUNDER"}
    assert "«пикша» сейчас нет" in response
    assert "Треска" in response
    assert "Камбала" in response
    assert provider.calls == []


@pytest.mark.django_db
def test_consultant_adds_each_explicit_product_quantity_without_model_guess(
    customer, settings, monkeypatch
):
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_CONSULTANT_ENABLED = True
    call_command("load_demo_data")
    provider = ScriptedProvider([])
    monkeypatch.setattr(
        "apps.assistant.services.get_gigachat_provider", lambda: provider
    )
    event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="deterministic-multi-item",
        external_user_id="multi-item-user",
        conversation_key="multi-item-dialog",
        customer=customer,
        raw_text="Хочу заказать осьминога 1 кг и краба 1 упаковку",
    ).event

    InboundEventProcessor.process(event.pk)
    event.status = InboundEventStatus.PROCESSED
    event.save(update_fields=["status", "updated_at"])
    event.refresh_from_db()
    items = list(
        event.draft.items.select_related("product")
        .order_by("line_number")
        .values_list("product__public_code", "requested_quantity")
    )
    response = InboundEventResponseService.present(event)["response"]["message"]

    assert items == [
        ("DEMO-OCTOPUS", Decimal("1")),
        ("DEMO-CRAB", Decimal("1")),
    ]
    assert "Осьминог" in response
    assert "Краб камчатский" in response
    assert "доставка или самовывоз" in response
    assert response.lower().count("доставка или самовывоз") == 1
    assert provider.calls == []


@pytest.mark.django_db
def test_consultant_adds_inflected_multiple_products_without_model_guess(
    customer, settings, monkeypatch
):
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_CONSULTANT_ENABLED = True
    call_command("load_demo_data")
    provider = ScriptedProvider([])
    monkeypatch.setattr(
        "apps.assistant.services.get_gigachat_provider", lambda: provider
    )
    event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="deterministic-inflected-multi-item",
        external_user_id="inflected-multi-item-user",
        conversation_key="inflected-multi-item-dialog",
        customer=customer,
        raw_text="Хочу заказать осьминога 1 кг и тунца 2 кг",
    ).event

    InboundEventProcessor.process(event.pk)
    event.status = InboundEventStatus.PROCESSED
    event.save(update_fields=["status", "updated_at"])
    event.refresh_from_db()
    response = InboundEventResponseService.present(event)["response"]["message"]

    assert list(
        event.draft.items.select_related("product")
        .order_by("line_number")
        .values_list("product__public_code", "requested_quantity")
    ) == [
        ("DEMO-OCTOPUS", Decimal("1")),
        ("DEMO-TUNA", Decimal("2")),
    ]
    assert "Осьминог" in response
    assert "Тунец" in response
    assert "доставка или самовывоз" in response
    assert provider.calls == []


@pytest.mark.django_db
def test_single_message_order_applies_address_delivery_payment_and_previews(
    customer, settings, monkeypatch
):
    """Email-like orders must preserve every explicit checkout term."""
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_CONSULTANT_ENABLED = True
    settings.YANDEX_DELIVERY_ENABLED = True
    customer.email = "stored@example.com"
    customer.save(update_fields=["email", "updated_at"])
    call_command("load_demo_data")
    provider = ScriptedProvider([])
    monkeypatch.setattr("apps.assistant.services.get_gigachat_provider", lambda: provider)

    def fake_quote(cart, **kwargs):
        return DeliveryQuote.objects.create(
            cart=cart,
            environment=DeliveryEnvironment.TEST,
            kind=DeliveryQuoteKind.PRELIMINARY,
            status=DeliveryQuoteStatus.SUCCEEDED,
            request_fingerprint="b" * 64,
            destination_address=kwargs["destination_address"],
            amount=Decimal("321.50"),
            currency="RUB",
            delivery_days=2,
        )

    monkeypatch.setattr(
        "apps.delivery.checkout.YandexDeliveryQuoteService.quote_cart", fake_quote
    )
    event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="one-message-checkout",
        external_user_id="one-message-user",
        conversation_key="one-message-dialog",
        customer=customer,
        raw_text=(
            "Хочу 2 упаковки креветок по адресу: Мурманск Ленина 64. "
            "Оплата картой. Email buyer@example.com"
        ),
    ).event

    InboundEventProcessor.process(event.pk)
    event.status = InboundEventStatus.PROCESSED
    event.save(update_fields=["status", "updated_at"])
    event.refresh_from_db()
    draft = event.draft
    response = InboundEventResponseService.present(event)["response"]

    assert list(draft.items.values_list("product__public_code", flat=True)) == ["DEMO-SHRIMP"]
    assert draft.items.get().requested_quantity == Decimal("2")
    assert draft.receiving_type == ReceivingType.DELIVERY
    assert draft.delivery_address == "Мурманск Ленина 64"
    assert draft.payment_method == PaymentMethod.CARD_PREPAYMENT
    assert draft.desired_date is None
    assert response["type"] == "order_preview", response
    assert "Проверьте заказ" in response["message"]
    assert "Креветки тигровые" in response["message"]
    assert "Чек будет направлен на: buyer@example.com" in response["message"]
    customer.refresh_from_db()
    assert customer.email == "buyer@example.com"
    assert provider.calls == []


@pytest.mark.django_db
def test_single_message_order_keeps_address_before_requesting_receipt_email(
    customer, settings, monkeypatch
):
    """A delivery address is not an invalid email while card checkout is open."""
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_CONSULTANT_ENABLED = True
    settings.YANDEX_DELIVERY_ENABLED = False
    customer.email = ""
    customer.save(update_fields=["email", "updated_at"])
    call_command("load_demo_data")
    provider = ScriptedProvider([])
    monkeypatch.setattr("apps.assistant.services.get_gigachat_provider", lambda: provider)
    event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="one-message-receipt-email",
        external_user_id="one-message-receipt-user",
        conversation_key="one-message-receipt-dialog",
        customer=customer,
        raw_text="Хочу 2 кг лосося по адресу Мурманск Ленина 64. Оплата картой",
    ).event

    InboundEventProcessor.process(event.pk)
    event.status = InboundEventStatus.PROCESSED
    event.save(update_fields=["status", "updated_at"])
    event.refresh_from_db()
    draft = event.draft
    response = InboundEventResponseService.present(event)["response"]

    assert draft.receiving_type == ReceivingType.DELIVERY
    assert draft.delivery_address == "Мурманск Ленина 64"
    assert draft.payment_method == PaymentMethod.CARD_PREPAYMENT
    assert draft.missing_fields == ["contact_email"]
    assert response["type"] == "checkout_updated"
    assert "укажите email" in response["message"]
    assert provider.calls == []


@pytest.mark.django_db
def test_website_name_step_naturally_requests_phone(customer, settings, monkeypatch):
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_CONSULTANT_ENABLED = True
    provider = ScriptedProvider([])
    monkeypatch.setattr(
        "apps.assistant.services.get_gigachat_provider", lambda: provider
    )
    event = InboundEventService.register(
        channel=Channel.WEBSITE,
        external_event_id="website-name-step",
        external_user_id="website-name-user",
        conversation_key="website-name-dialog",
        customer=None,
        raw_text="Меня зовут Алексей",
        raw_payload={"contact_name": "Алексей", "contact_phone": ""},
    ).event
    draft, _ = OrderDraftService.get_or_create_active(
        channel=event.channel,
        external_user_id=event.external_user_id,
        conversation_key=event.conversation_key,
        customer=None,
    )
    draft.status = OrderDraftStatus.NEEDS_CLARIFICATION
    draft.missing_fields = ["customer"]
    draft.save(update_fields=["status", "missing_fields", "updated_at"])

    InboundEventProcessor.process(event.pk)
    event.status = InboundEventStatus.PROCESSED
    event.save(update_fields=["status", "updated_at"])
    event.refresh_from_db()
    response = InboundEventResponseService.present(event)["response"]["message"]

    assert "имя записал" in response
    assert "телефон" in response
    assert provider.calls == []


@pytest.mark.django_db
def test_assistant_resumes_manual_cart_checkout(customer, product, settings, monkeypatch):
    settings.AI_ASSISTANT_ENABLED = True
    cart = CartService.get_or_create_active_cart(
        channel=Channel.TELEGRAM,
        external_user_id="12345",
        customer=customer,
    )
    CartService.set_item_quantity(cart, product, Decimal("2"))
    provider = ScriptedProvider([])
    monkeypatch.setattr("apps.assistant.services.get_gigachat_provider", lambda: provider)
    event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="resume-manual-cart",
        external_user_id="12345",
        conversation_key="resume-manual-cart",
        customer=customer,
        raw_text="Давайте оформим заказ",
    ).event

    InboundEventProcessor.process(event.pk)
    event.status = InboundEventStatus.PROCESSED
    event.save(update_fields=["status", "updated_at"])
    event.refresh_from_db()
    response = InboundEventResponseService.present(event)["response"]

    assert product.name in response["message"]
    assert "доставка или самовывоз" in response["message"]
    assert provider.calls == []


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
def test_email_reply_confirms_current_preview_and_returns_payment_link(
    customer, product, delivery_rule, settings, monkeypatch
):
    settings.AI_ASSISTANT_ENABLED = True
    settings.YANDEX_DELIVERY_ENABLED = False
    settings.YOOKASSA_ENABLED = True
    customer.email = "buyer@example.com"
    customer.save(update_fields=["email", "updated_at"])
    conversation = "email:confirmation-fixture"
    draft, _ = OrderDraftService.get_or_create_active(
        channel=Channel.EMAIL,
        external_user_id=conversation,
        conversation_key=conversation,
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
    draft.receiving_type = ReceivingType.PICKUP
    draft.payment_method = PaymentMethod.CARD_PREPAYMENT
    draft.contact_phone = customer.phone
    draft.contact_email = customer.email
    draft.save(update_fields=[
        "receiving_type", "payment_method", "contact_phone", "contact_email", "updated_at"
    ])
    AssistantToolExecutor._refresh_state(draft)
    draft = DraftPricingService.preview(draft)
    provider = ScriptedProvider([])
    monkeypatch.setattr("apps.assistant.services.get_gigachat_provider", lambda: provider)

    def fake_payment(order):
        return Payment.objects.create(
            order=order,
            amount=order.total_amount,
            description=f"Оплата заказа {order.public_number}",
            confirmation_url="https://yookassa.example.test/pay/email-confirmed",
        )

    monkeypatch.setattr("apps.assistant.tools.PaymentService.ensure_payment_link", fake_payment)
    event = InboundEventService.register(
        channel=Channel.EMAIL,
        external_event_id="email-confirmation-reply",
        external_user_id=conversation,
        conversation_key=conversation,
        customer=customer,
        raw_text="Тема: Re: Проверьте заказ\n\nОформляйте",
        raw_payload={"contact_email": customer.email},
    ).event

    InboundEventProcessor.process(event.pk)
    event.status = InboundEventStatus.PROCESSED
    event.save(update_fields=["status", "updated_at"])
    event.refresh_from_db()
    response = InboundEventResponseService.present(event)["response"]

    assert Order.objects.count() == 1
    assert response["type"] in {"order_created", "payment_link"}
    assert response["action_url"].endswith("/email-confirmed")
    assert provider.calls == []


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
    draft.contact_email = "buyer@example.com"
    draft.save(
        update_fields=[
            "receiving_type",
            "delivery_address",
            "payment_method",
            "contact_phone",
            "contact_email",
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

    def failed_quote(cart, **kwargs):
        return DeliveryQuote.objects.create(
            cart=cart,
            environment=DeliveryEnvironment.TEST,
            kind=DeliveryQuoteKind.PRELIMINARY,
            status=DeliveryQuoteStatus.FAILED,
            request_fingerprint="f" * 64,
            destination_address=kwargs["destination_address"],
            error_code="no_delivery_options",
            error_message="No delivery options for interval",
        )

    monkeypatch.setattr(
        "apps.delivery.checkout.YandexDeliveryQuoteService.quote_cart",
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
    draft.contact_email = "buyer@example.com"
    draft.save(
        update_fields=[
            "receiving_type",
            "delivery_address",
            "payment_method",
            "contact_phone",
            "contact_email",
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

    def failed_quote(cart, **kwargs):
        return DeliveryQuote.objects.create(
            cart=cart,
            environment=DeliveryEnvironment.TEST,
            kind=DeliveryQuoteKind.PRELIMINARY,
            status=DeliveryQuoteStatus.FAILED,
            request_fingerprint="e" * 64,
            destination_address=kwargs["destination_address"],
            error_code="500",
            error_message="Internal Server Error",
        )

    monkeypatch.setattr(
        "apps.delivery.checkout.YandexDeliveryQuoteService.quote_cart",
        failed_quote,
    )
    monkeypatch.setattr(
        "apps.delivery.offer_service.YandexDeliveryOfferService.create_for_cart",
        failed_quote,
    )
    backend = AssistantToolExecutor(event=event, draft=draft, turn=turn)

    result = backend.execute("preview_order", {}, 1)

    assert result["ok"] is False
    assert "временно недоступен" in result["error"]["message"]
    assert "не предложила вариант" not in result["error"]["message"]


@pytest.mark.django_db
def test_card_prepayment_requires_receipt_email_before_preview(customer, product, settings):
    settings.YANDEX_DELIVERY_ENABLED = False
    conversation = "card-receipt-email-dialog"
    draft = seed_active_draft_with_product(customer, product, conversation)
    draft.receiving_type = ReceivingType.PICKUP
    draft.payment_method = PaymentMethod.CARD_PREPAYMENT
    draft.save(update_fields=["receiving_type", "payment_method", "updated_at"])
    AssistantToolExecutor._refresh_state(draft)
    event = InboundEventService.register(
        channel=Channel.WEBSITE,
        external_event_id="card-receipt-email",
        external_user_id="web:receipt-email",
        conversation_key=conversation,
        customer=customer,
        raw_text="buyer@example.com",
        raw_payload={"contact_email": "buyer@example.com"},
    ).event
    turn = AssistantTurn.objects.create(event=event, draft=draft)
    backend = AssistantToolExecutor(event=event, draft=draft, turn=turn)

    without_email = backend.execute("preview_order", {}, 1)
    assert without_email["ok"] is False
    assert "email" in without_email["error"]["message"]

    action = backend.checkout_action()
    assert action == ("configure_checkout", {"contact_email": "buyer@example.com"})
    configured = backend.execute(*action, call_index=2)
    assert configured["missing_fields"] == []
    preview = backend.execute("preview_order", {}, 3)
    assert preview["ok"] is True
    assert preview["requires_explicit_confirmation"] is True


@pytest.mark.django_db
def test_invalid_email_reply_keeps_checkout_on_receipt_email_step(
    customer, product, settings, monkeypatch
):
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_CONSULTANT_ENABLED = True
    settings.YANDEX_DELIVERY_ENABLED = False
    conversation = "invalid-receipt-email-dialog"
    draft = seed_active_draft_with_product(customer, product, conversation)
    draft.receiving_type = ReceivingType.PICKUP
    draft.payment_method = PaymentMethod.CARD_PREPAYMENT
    draft.save(update_fields=["receiving_type", "payment_method", "updated_at"])
    AssistantToolExecutor._refresh_state(draft)
    provider = ScriptedProvider([])
    monkeypatch.setattr("apps.assistant.services.get_gigachat_provider", lambda: provider)
    event = InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id="invalid-receipt-email",
        external_user_id="12345",
        conversation_key=conversation,
        customer=customer,
        raw_text="Пррмс",
    ).event

    InboundEventProcessor.process(event.pk)
    event.status = InboundEventStatus.PROCESSED
    event.save(update_fields=["status", "updated_at"])
    response = InboundEventResponseService.present(event)["response"]
    draft.refresh_from_db()

    assert response["type"] == "invalid_email"
    assert "адрес email указан неверно" in response["message"]
    assert draft.missing_fields == ["contact_email"]
    assert provider.calls == []


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

    # Cart and explicit checkout fields are now resolved by the deterministic
    # layer. Keep one ordinary conversational reply to verify that a preview
    # remains part of the model context before a separate confirmation.
    provider = ScriptedProvider([
        answer("Заказ создаётся только после отдельного явного подтверждения клиента.")
    ])
    monkeypatch.setattr("apps.assistant.services.get_gigachat_provider", lambda: provider)

    def fake_quote(cart, **kwargs):
        return DeliveryQuote.objects.create(
            cart=cart,
            environment=DeliveryEnvironment.TEST,
            kind=DeliveryQuoteKind.PRELIMINARY,
            status=DeliveryQuoteStatus.SUCCEEDED,
            request_fingerprint="a" * 64,
            destination_address=kwargs["destination_address"],
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

    monkeypatch.setattr("apps.delivery.checkout.YandexDeliveryQuoteService.quote_cart", fake_quote)
    monkeypatch.setattr("apps.assistant.tools.PaymentService.ensure_payment_link", fake_payment)

    texts = [
        f"Хочу два товара {product.name}",
        "Доставка на Москва, Тверская улица, 1",
        "Оплачу картой онлайн, чек на buyer@example.com",
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
    assert any("Проверьте заказ:" in message.get("content", "") for message in last_prompt_messages)
    assert "Она не является источником фактов" in provider.calls[-1]["system_prompt"]
    assert product.public_code in provider.calls[-1]["system_prompt"]
    assert all("parameters" in function for function in provider.calls[0]["functions"])
