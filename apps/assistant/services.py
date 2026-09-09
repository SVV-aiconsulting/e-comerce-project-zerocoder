"""Ограниченный GigaChat tool-calling оркестратор над backend WebMarket."""

import json
import time
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils import timezone

from apps.assistant.prompts import ASSISTANT_TOOLS_SYSTEM_PROMPT
from apps.assistant.runtime import get_assistant_runtime
from apps.assistant.tools import AssistantToolExecutor
from apps.common.enums import PaymentMethod
from apps.intake.ai.providers.gigachat import get_gigachat_provider
from apps.intake.enums import AssistantMessageRole, AssistantTurnStatus, OrderDraftStatus
from apps.intake.exceptions import LLMProviderError
from apps.intake.models import AssistantMessage, AssistantTurn, OrderDraft


class OrderAssistantService:
    """Единый stateful-сценарий Telegram, website и следующих адаптеров."""

    @classmethod
    def process(cls, event, draft, *, provider=None):
        runtime = get_assistant_runtime()
        if not runtime.enabled:
            return draft
        # Совместимый внутренний путь для старого флага в тестах и переходных
        # инсталляциях. Новая конфигурация AI_ASSISTANT_ENABLED использует tools.
        if not settings.AI_ASSISTANT_ENABLED and settings.AI_ORDER_PROCESSING_ENABLED:
            return cls._process_legacy(event, draft)

        from apps.intake.leases import fenced_write

        with fenced_write():
            AssistantMessage.objects.get_or_create(
                event=event,
                role=AssistantMessageRole.USER,
                defaults={
                    "conversation_key": event.conversation_key,
                    "content": event.raw_text,
                },
            )
            existing_response = AssistantMessage.objects.filter(
                event=event,
                role=AssistantMessageRole.ASSISTANT,
            ).first()
            if existing_response is not None:
                return OrderDraft.objects.get(pk=draft.pk)

            turn, _ = AssistantTurn.objects.get_or_create(
                event=event,
                defaults={
                    "draft": draft,
                    "provider": runtime.provider,
                    "model_name": runtime.model,
                    "prompt_profile": runtime.prompt_profile,
                },
            )
        started = time.monotonic()
        messages = cls._history(event)
        backend = AssistantToolExecutor(event=event, draft=draft, turn=turn)
        system_prompt = cls._system_prompt(backend)
        llm = provider or get_gigachat_provider()
        action_url = ""
        response_type = "assistant"
        model_calls = tool_calls = input_tokens = output_tokens = 0
        last_tool_name = ""
        last_tool_result = None

        try:
            if draft.status not in {
                OrderDraftStatus.AWAITING_CONFIRMATION,
                OrderDraftStatus.CONVERTED,
            }:
                draft = backend._refresh_state(draft)
            cancellation = backend.cancellation_action()
            if cancellation is not None:
                tool_name, arguments = cancellation
                result = backend.execute(tool_name, arguments, 1)
                tool_calls = 1
                content, response_type, action_url = cls._render_tool_response(
                    tool_name, result, ""
                )
                cls._save_response(
                    event,
                    content,
                    response_type=response_type,
                    action_url=action_url,
                )
                cls._finish_turn(
                    turn,
                    AssistantTurnStatus.SUCCEEDED,
                    started,
                    model_calls,
                    tool_calls,
                    input_tokens,
                    output_tokens,
                )
                return OrderDraft.objects.get(pk=draft.pk)
            stale_cart = backend.stale_cart_action()
            if stale_cart is not None:
                stale_action, arguments = stale_cart
                tool_name = "clear_cart" if stale_action == "clear_cart" else "get_cart"
                result = backend.execute(tool_name, arguments, 1)
                tool_calls = 1
                if stale_action == "prompt_stale_cart":
                    content = cls._render_stale_cart_prompt(result)
                    response_type = "stale_cart_choice"
                    action_url = ""
                elif stale_action == "keep_stale_cart":
                    content = (
                        "Корзина сохранена и остаётся актуальной. Можем продолжить "
                        "оформление или изменить любые параметры заказа."
                    )
                    response_type = "cart_kept"
                    action_url = ""
                else:
                    content, response_type, action_url = cls._render_tool_response(
                        tool_name, result, ""
                    )
                cls._save_response(
                    event,
                    content,
                    response_type=response_type,
                    action_url=action_url,
                )
                cls._finish_turn(
                    turn,
                    AssistantTurnStatus.SUCCEEDED,
                    started,
                    model_calls,
                    tool_calls,
                    input_tokens,
                    output_tokens,
                )
                return OrderDraft.objects.get(pk=draft.pk)
            # A message can contain both products and checkout terms (common
            # for email). Apply products first; otherwise an early checkout
            # response would discard the delivery, payment and address part.
            cart_actions = backend.cart_mutation_actions()
            checkout = None if cart_actions else backend.checkout_action()
            if checkout is not None:
                tool_name, arguments = checkout
                result = backend.execute(tool_name, arguments, 1)
                tool_calls = 1
                rendered_tool = tool_name
                if (
                    result.get("ok") is not False
                    and not result.get("missing_fields")
                ):
                    result = backend.execute("preview_order", {}, 2)
                    rendered_tool = "preview_order"
                    tool_calls = 2
                content, response_type, action_url = cls._render_tool_response(
                    rendered_tool, result, ""
                )
                cls._save_response(
                    event,
                    content,
                    response_type=response_type,
                    action_url=action_url,
                )
                cls._finish_turn(
                    turn,
                    AssistantTurnStatus.SUCCEEDED,
                    started,
                    model_calls,
                    tool_calls,
                    input_tokens,
                    output_tokens,
                )
                return OrderDraft.objects.get(pk=draft.pk)
            # A card payment cannot proceed without a receipt email. Do not
            # let an unrelated message fall through to the conversational
            # model, which would lose the checkout step and ask a generic
            # catalogue question instead.
            # Show an email validation error only when email is the next
            # required value.  An address or another checkout term in the same
            # message must be parsed first instead of being rejected as email.
            if set(draft.missing_fields or []) == {"contact_email"}:
                cls._save_response(
                    event,
                    "Похоже, адрес email указан неверно. Укажите корректный email "
                    "для электронного чека ЮKassa.",
                    response_type="invalid_email",
                )
                cls._finish_turn(
                    turn,
                    AssistantTurnStatus.SUCCEEDED,
                    started,
                    model_calls,
                    tool_calls,
                    input_tokens,
                    output_tokens,
                )
                return OrderDraft.objects.get(pk=draft.pk)
            # В website личность создаётся отдельным сообщением с именем и
            # телефоном. Контакты привязывает intake до запуска ассистента,
            # поэтому нужен один серверный refresh и расчёт без лишнего «да».
            # Это не действует на историю/старые сессии: только новое событие,
            # уже связанное с CRM-клиентом, может завершить checkout-preview.
            if event.channel == "website" and (
                event.customer_id or event.raw_payload.get("contact_email")
            ):
                refreshed = backend._refresh_state(backend._draft())
                if (
                    refreshed.status == OrderDraftStatus.READY_FOR_PREVIEW
                    or (
                        refreshed.receiving_type == "delivery"
                        and set(refreshed.missing_fields) == {"payment_method"}
                    )
                ):
                    result = backend.execute("preview_order", {}, 1)
                    content, response_type, action_url = cls._render_tool_response(
                        "preview_order", result, ""
                    )
                    cls._save_response(
                        event,
                        content,
                        response_type=response_type,
                        action_url=action_url,
                    )
                    cls._finish_turn(
                        turn,
                        AssistantTurnStatus.SUCCEEDED,
                        started,
                        model_calls,
                        1,
                        input_tokens,
                        output_tokens,
                    )
                    return OrderDraft.objects.get(pk=draft.pk)
            if (
                event.channel == "website"
                and event.raw_payload.get("contact_name")
                and not event.raw_payload.get("contact_phone")
                and event.customer_id is None
                and "customer" in (draft.missing_fields or [])
            ):
                cls._save_response(
                    event,
                    "Спасибо, имя записал. Теперь укажите контактный телефон в формате 9XXXXXXXXX.",
                    response_type="contact_requested",
                )
                cls._finish_turn(
                    turn,
                    AssistantTurnStatus.SUCCEEDED,
                    started,
                    model_calls,
                    tool_calls,
                    input_tokens,
                    output_tokens,
                )
                return OrderDraft.objects.get(pk=draft.pk)
            if cart_actions:
                result = None
                for call_index, (tool_name, arguments) in enumerate(
                    cart_actions, start=1
                ):
                    result = backend.execute(tool_name, arguments, call_index)
                    tool_calls += 1
                    if result.get("ok") is False:
                        break
                rendered_tool = "set_cart_item"
                unavailable = backend.unavailable_catalog_action()
                if result.get("ok") is not False and unavailable is not None:
                    tool_name, arguments = unavailable
                    alternatives = backend.execute(
                        tool_name, arguments, tool_calls + 1
                    )
                    tool_calls += 1
                    alternatives_content, _, _ = cls._render_tool_response(
                        tool_name, alternatives, ""
                    )
                    content, response_type, action_url = cls._render_tool_response(
                        rendered_tool, result, ""
                    )
                    content = f"{content}\n\n{alternatives_content}"
                elif result.get("ok") is not False:
                    checkout = backend.checkout_action()
                    if checkout is not None:
                        tool_name, arguments = checkout
                        result = backend.execute(tool_name, arguments, tool_calls + 1)
                        tool_calls += 1
                        rendered_tool = tool_name
                        if result.get("ok") is not False and not result.get("missing_fields"):
                            result = backend.execute("preview_order", {}, tool_calls + 1)
                            tool_calls += 1
                            rendered_tool = "preview_order"
                content, response_type, action_url = cls._render_tool_response(
                    rendered_tool, result, ""
                ) if unavailable is None else (content, response_type, action_url)
                cls._save_response(
                    event,
                    content,
                    response_type=response_type,
                    action_url=action_url,
                )
                cls._finish_turn(
                    turn,
                    AssistantTurnStatus.SUCCEEDED,
                    started,
                    model_calls,
                    tool_calls,
                    input_tokens,
                    output_tokens,
                )
                return OrderDraft.objects.get(pk=draft.pk)
            catalog = backend.catalog_action()
            if catalog is not None:
                tool_name, arguments = catalog
                result = backend.execute(tool_name, arguments, 1)
                tool_calls = 1
                content, response_type, action_url = cls._render_tool_response(
                    tool_name, result, ""
                )
                cls._save_response(
                    event,
                    content,
                    response_type=response_type,
                    action_url=action_url,
                )
                cls._finish_turn(
                    turn,
                    AssistantTurnStatus.SUCCEEDED,
                    started,
                    model_calls,
                    tool_calls,
                    input_tokens,
                    output_tokens,
                )
                return OrderDraft.objects.get(pk=draft.pk)
            reference = backend.referential_catalog_action()
            if reference is not None:
                tool_name, arguments = reference
                result = backend.execute(tool_name, arguments, 1)
                tool_calls = 1
                content, response_type, action_url = cls._render_tool_response(
                    tool_name, result, ""
                )
                cls._save_response(
                    event,
                    content,
                    response_type=response_type,
                    action_url=action_url,
                )
                cls._finish_turn(
                    turn,
                    AssistantTurnStatus.SUCCEEDED,
                    started,
                    model_calls,
                    tool_calls,
                    input_tokens,
                    output_tokens,
                )
                return OrderDraft.objects.get(pk=draft.pk)
            repeat_order = backend.repeat_order_action()
            if repeat_order is not None:
                tool_name, arguments = repeat_order
                repeated = backend.execute(tool_name, arguments, 1)
                tool_calls = 1
                result = repeated
                rendered_tool = tool_name
                if repeated.get("ok") is not False:
                    result = backend.execute("preview_order", {}, 2)
                    rendered_tool = "preview_order"
                    tool_calls = 2
                content, response_type, action_url = cls._render_tool_response(
                    rendered_tool, result, ""
                )
                cls._save_response(
                    event,
                    content,
                    response_type=response_type,
                    action_url=action_url,
                )
                cls._finish_turn(
                    turn,
                    AssistantTurnStatus.SUCCEEDED,
                    started,
                    model_calls,
                    tool_calls,
                    input_tokens,
                    output_tokens,
                )
                return OrderDraft.objects.get(pk=draft.pk)
            for authoritative_action in (
                backend.cart_read_action(),
                backend.order_history_action(),
            ):
                if authoritative_action is None:
                    continue
                tool_name, arguments = authoritative_action
                result = backend.execute(tool_name, arguments, 1)
                tool_calls = 1
                content, response_type, action_url = cls._render_tool_response(
                    tool_name, result, ""
                )
                cls._save_response(
                    event,
                    content,
                    response_type=response_type,
                    action_url=action_url,
                )
                cls._finish_turn(
                    turn,
                    AssistantTurnStatus.SUCCEEDED,
                    started,
                    model_calls,
                    tool_calls,
                    input_tokens,
                    output_tokens,
                )
                return OrderDraft.objects.get(pk=draft.pk)
            preview_request = backend.preview_action()
            if preview_request is not None:
                tool_name, arguments = preview_request
                result = backend.execute(tool_name, arguments, 1)
                tool_calls = 1
                content, response_type, action_url = cls._render_tool_response(
                    tool_name, result, ""
                )
                cls._save_response(
                    event,
                    content,
                    response_type=response_type,
                    action_url=action_url,
                )
                cls._finish_turn(
                    turn,
                    AssistantTurnStatus.SUCCEEDED,
                    started,
                    model_calls,
                    tool_calls,
                    input_tokens,
                    output_tokens,
                )
                return OrderDraft.objects.get(pk=draft.pk)
            if (
                backend._explicit_confirmation(event)
                and draft.status == OrderDraftStatus.AWAITING_CONFIRMATION
                and draft.previewed_revision == draft.revision
            ):
                result = backend.execute(
                    "confirm_order",
                    {"preview_revision": draft.previewed_revision},
                    1,
                )
                tool_calls = 1
                content, response_type, action_url = cls._render_tool_response(
                    "confirm_order", result, ""
                )
                cls._save_response(
                    event,
                    content,
                    response_type=response_type,
                    action_url=action_url,
                )
                cls._finish_turn(
                    turn,
                    AssistantTurnStatus.SUCCEEDED,
                    started,
                    model_calls,
                    tool_calls,
                    input_tokens,
                    output_tokens,
                )
                return OrderDraft.objects.get(pk=draft.pk)

            semantic_catalog = backend.semantic_catalog_request()
            all_definitions = backend.definitions()
            semantic_definitions = [
                definition
                for definition in all_definitions
                if definition["name"] == "recommend_products"
            ]
            for call_index in range(1, settings.AI_ASSISTANT_MAX_TOOL_CALLS + 2):
                from apps.intake.leases import check_lease
                check_lease()
                completion = llm.generate_with_tools(
                    system_prompt=system_prompt,
                    messages=messages,
                    functions=(
                        semantic_definitions
                        if semantic_catalog and call_index == 1
                        else all_definitions
                    ),
                )
                model_calls += 1
                input_tokens += completion.input_tokens or 0
                output_tokens += completion.output_tokens or 0
                turn.model_name = completion.model_name

                if completion.function_call is None:
                    if (
                        last_tool_name == "repeat_order"
                        and last_tool_result
                        and last_tool_result.get("ok") is not False
                    ):
                        last_tool_name = "preview_order"
                        last_tool_result = backend.execute(
                            "preview_order", {}, tool_calls + 1
                        )
                        tool_calls += 1
                    if not last_tool_name:
                        authoritative = backend.authoritative_fallback_action()
                        if authoritative is not None:
                            last_tool_name, arguments = authoritative
                            last_tool_result = backend.execute(
                                last_tool_name, arguments, tool_calls + 1
                            )
                            tool_calls += 1
                    content, response_type, action_url = cls._render_tool_response(
                        last_tool_name,
                        last_tool_result,
                        completion.content.strip(),
                    )
                    cls._save_response(
                        event,
                        content,
                        response_type=response_type,
                        action_url=action_url,
                    )
                    cls._finish_turn(turn, AssistantTurnStatus.SUCCEEDED, started, model_calls, tool_calls, input_tokens, output_tokens)
                    return OrderDraft.objects.get(pk=draft.pk)

                if call_index > settings.AI_ASSISTANT_MAX_TOOL_CALLS:
                    cls._save_response(
                        event,
                        "Я остановил обработку, чтобы не повторять действия. Уточните, пожалуйста, что именно нужно изменить в заказе.",
                        response_type="tool_limit",
                    )
                    cls._finish_turn(turn, AssistantTurnStatus.TOOL_LIMIT, started, model_calls, tool_calls, input_tokens, output_tokens, error_code="tool_limit")
                    return OrderDraft.objects.get(pk=draft.pk)

                function_call = completion.function_call
                assistant_call = {
                    "role": "assistant",
                    "content": completion.content,
                    "function_call": {"name": function_call.name, "arguments": function_call.arguments},
                }
                if function_call.state_id:
                    assistant_call["functions_state_id"] = function_call.state_id
                messages.append(assistant_call)
                result = backend.execute(function_call.name, function_call.arguments, call_index)
                tool_calls += 1
                last_tool_name = function_call.name
                last_tool_result = result
                if result.get("payment_url"):
                    action_url = str(result["payment_url"])
                    response_type = "payment_link"
                elif result.get("order_number"):
                    response_type = "order_created"
                messages.append({
                    "role": "function",
                    "name": function_call.name,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                })
        except LLMProviderError as exc:
            if last_tool_name and last_tool_result:
                content, response_type, action_url = cls._render_tool_response(last_tool_name, last_tool_result, "")
                cls._save_response(event, content, response_type=response_type, action_url=action_url)
                cls._finish_turn(turn, AssistantTurnStatus.FAILED, started, model_calls, tool_calls,
                    input_tokens, output_tokens, error_code=type(exc).__name__, error_message="Ответ модели недоступен после выполнения инструмента")
                return OrderDraft.objects.get(pk=draft.pk)
            cls._save_response(
                event,
                "Сейчас AI-консультант не смог завершить ответ. Корзина сохранена; попробуйте продолжить диалог следующим сообщением.",
                response_type="assistant_error",
            )
            cls._finish_turn(
                turn, AssistantTurnStatus.FAILED, started, model_calls, tool_calls,
                input_tokens, output_tokens, error_code=type(exc).__name__, error_message=str(exc),
            )
            return OrderDraft.objects.get(pk=draft.pk)

    @staticmethod
    def _system_prompt(backend) -> str:
        context = json.dumps(
            backend.context_payload(),
            ensure_ascii=False,
            default=str,
        )
        from apps.assistant.prompts import CONSULTANT_PROMPT
        extra = CONSULTANT_PROMPT if settings.AI_CONSULTANT_ENABLED else ""
        return (
            f"{ASSISTANT_TOOLS_SYSTEM_PROMPT}{extra}\n\n"
            f"Канал текущего диалога: {backend.event.channel}.\n"
            "История сообщений нужна только для контекста намерения и ссылок вроде "
            "«этот товар». Она не является источником фактов. Любые сведения о "
            "товарах, ценах, корзине, заказах и оплате бери только из результата "
            "соответствующего backend-инструмента в текущем ходе.\n"
            "Актуальный backend-контекст ниже. Используй только точные code из "
            "recent_product_search; не создавай code из названия. Если нужного "
            "товара там нет, снова вызови search_products.\n"
            f"BACKEND_CONTEXT={context}"
        )

    @classmethod
    def _render_tool_response(cls, tool_name, result, model_content):
        if not result:
            if settings.AI_CONSULTANT_ENABLED:
                from apps.assistant.conversation import safe_narration
                model_content = safe_narration(model_content) or "Расскажите, что вы хотите выбрать или изменить?"
            return model_content, "assistant", ""
        if result.get("ok") is False:
            error = result.get("error", {})
            message = str(
                error.get("message")
                or model_content
                or "Не удалось выполнить действие."
            )
            cart = result.get("cart")
            if isinstance(cart, dict) and cart.get("items"):
                message = f"{cls._render_cart(cart)}\n\n{message}"
            return message, "tool_error", ""
        if tool_name == "preview_order":
            if result.get("preliminary_delivery_quote"):
                return cls._render_preliminary_delivery_quote(result), "delivery_quote", ""
            return cls._render_preview(result), "order_preview", ""
        if tool_name in {"search_products", "compare_products", "recommend_products"}:
            card = cls._render_catalog(result)
            if settings.AI_CONSULTANT_ENABLED:
                from apps.assistant.conversation import safe_narration
                narration = safe_narration(model_content)
                if not result.get("products"):
                    narration = (
                        "Назовите другой продукт или опишите ваши предпочтения — "
                        "я проверю каталог ещё раз."
                    )
                elif not narration:
                    if tool_name == "compare_products":
                        narration = "Хотите добавить один из вариантов в заказ?"
                    elif result.get("scope") == "selection":
                        narration = "Укажите количество для каждого из этих товаров."
                    elif len(result.get("products", [])) > 1:
                        narration = (
                            "Какой вариант показать подробнее или добавить в заказ?"
                        )
                    else:
                        narration = (
                            "Добавить этот товар в заказ? Укажите нужное количество."
                        )
                return f"{card}\n\n{narration}", "catalog", ""
            return card, "catalog", ""
        if tool_name == "get_cart":
            return cls._render_cart(result), "cart", ""
        if tool_name in {"set_cart_item", "remove_cart_item", "configure_checkout"}:
            lines = [cls._render_cart(result)]
            if not result.get("missing_fields"):
                lines.extend(
                    ["", "Все обязательные параметры заполнены. Рассчитать актуальный итог?"]
                )
            response_type = (
                "checkout_updated"
                if tool_name == "configure_checkout"
                else "cart_updated"
            )
            return "\n".join(lines), response_type, ""
        if tool_name == "confirm_order":
            url = str(result.get("payment_url") or "")
            lines = [
                "Ваш заказ оформлен. При необходимости наш менеджер свяжется с вами.",
                "",
                f"Номер: {result.get('order_number')}",
                f"Сумма: {cls._money(result.get('total_amount'))} ₽",
            ]
            if result.get("payment_pending"):
                lines.extend(["", "Заказ сохранён, но ссылка оплаты пока недоступна. Запросите ссылку повторно."])
            if url:
                lines.extend(["", "Для оплаты банковской картой перейдите по ссылке:"])
            return "\n".join(lines), "payment_link" if url else "order_created", url
        if tool_name == "get_payment_link":
            url = str(result.get("payment_url") or "")
            text = f"Ссылка для оплаты заказа {result.get('order_number')}:"
            return text, "payment_link", url
        if tool_name == "list_customer_orders":
            return cls._render_orders(result), "order_history", ""
        if tool_name == "get_cancellation_options":
            return cls._render_cancellation_options(result), "cancellation_choice", ""
        if tool_name == "clear_cart":
            return str(result.get("message")), "cart_cleared", ""
        if tool_name == "cancel_order":
            return str(result.get("message")), "order_cancelled", ""
        return model_content, "assistant", ""

    @staticmethod
    def _render_catalog(result) -> str:
        products = result.get("products", [])
        query = str(result.get("query") or "").strip()
        unavailable_item = str(result.get("unavailable_item") or "").strip()
        if not products:
            if unavailable_item:
                return f"Товара «{unavailable_item}» сейчас нет в нашем каталоге."
            return f"По запросу «{query}» активных товаров в каталоге не найдено."
        if result.get("scope") not in {"full_catalog", "recommendation"} and len(products) == 1:
            product = products[0]
            lines = [
                product["name"],
                (
                    f"Цена: {OrderAssistantService._money(product['price'])} ₽ "
                    f"за {product['unit_label'].lower()}"
                ),
                (
                    "Минимальный заказ: "
                    f"{OrderAssistantService._quantity(product['min_quantity'])} "
                    f"{product['unit_label'].lower()}"
                ),
                (
                    "Описание: "
                    f"{product.get('description') or 'в карточке товара не указано'}"
                ),
            ]
            return "\n".join(lines)
        if unavailable_item:
            title = f"Товара «{unavailable_item}» сейчас нет в нашем каталоге. Из близких вариантов могу предложить:"
        elif result.get("scope") == "recommendation":
            title = f"По запросу «{query}» могу предложить:"
        else:
            title = "Полный каталог активных товаров:" if result.get("scope") == "full_catalog" else f"Товары по запросу «{query}»:"
        lines = [title]
        for product in products:
            lines.append(
                f"• {product['name']} — {OrderAssistantService._money(product['price'])} ₽ "
                f"за {product['unit_label'].lower()}; минимальный заказ: "
                f"{OrderAssistantService._quantity(product['min_quantity'])} {product['unit_label'].lower()}"
            )
            if result.get("scope") == "recommendation" and product.get("description"):
                lines.append(f"  {product['description']}")
        if result.get("scope") == "comparison":
            lines.extend(f"{p['name']}: {p.get('description') or 'Описание в каталоге не указано.'}" for p in products)
            units = {p.get("unit") for p in products}
            if len(units) == 1:
                lowest = min(Decimal(str(p["price"])) for p in products)
                cheapest = [p for p in products if Decimal(str(p["price"])) == lowest]
                if len(cheapest) == 1:
                    product = cheapest[0]
                    lines.append(
                        f"Самая низкая цена за {product['unit_label'].lower()}: "
                        f"{product['name']} — {OrderAssistantService._money(product['price'])} ₽."
                    )
        return "\n".join(lines)

    @staticmethod
    def _render_cart(result) -> str:
        items = result.get("items", [])
        if not items:
            return "Текущая корзина пуста."
        lines = ["Текущий состав заказа:"]
        for item in items:
            lines.append(
                f"• {item['name']}: {OrderAssistantService._quantity(item['quantity'])} "
                f"{item.get('unit_label', item['unit'])} × "
                f"{OrderAssistantService._money(item['unit_price'])} ₽ = "
                f"{OrderAssistantService._money(item['line_total'])} ₽"
            )
        if result.get("receiving_type") == "delivery":
            lines.append(f"Адрес доставки: {result.get('delivery_address') or 'ещё не указан'}")
        elif result.get("receiving_type") == "pickup":
            lines.append("Получение: самовывоз")
        if result.get("payment_method"):
            payment_labels = {
                PaymentMethod.CASH_ON_DELIVERY: "наличными при получении",
                PaymentMethod.CARD_ON_DELIVERY: "картой при получении",
                PaymentMethod.CARD_PREPAYMENT: "картой онлайн",
            }
            lines.append(
                "Способ оплаты: "
                f"{payment_labels.get(result['payment_method'], result['payment_method'])}"
            )
        if result.get("total_amount") is not None:
            lines.append(f"Итого последнего расчёта: {OrderAssistantService._money(result['total_amount'])} ₽")
        missing = result.get("missing_fields") or []
        if "receiving_type" in missing:
            lines.extend(["", "Выберите способ получения: доставка или самовывоз."])
        elif "delivery_address" in missing:
            lines.extend(["", "Укажите адрес доставки."])
        elif "contact_phone" in missing or "customer" in missing:
            lines.extend(["", "Для оформления заказа прошу сообщить Ваше имя и телефон в формате 9XXXXXXXXX."])
        elif "payment_method" in missing:
            lines.extend(["", "Выберите способ оплаты: наличными при получении или картой онлайн."])
        elif "contact_email" in missing:
            lines.extend(["", "Для онлайн-оплаты укажите email для электронного чека."])
        return "\n".join(lines)

    @staticmethod
    def _render_preliminary_delivery_quote(result) -> str:
        quote = result["preliminary_delivery_quote"]
        lines = ["Параметры доставки:"]
        lines.append(f"Адрес: {result.get('delivery_address')}")
        lines.append(
            f"Стоимость доставки: {OrderAssistantService._money(quote['delivery_cost'])} ₽"
        )
        if quote.get("delivery_days") is not None:
            lines.append(f"Ориентировочный срок: {quote['delivery_days']} дн.")
        lines.extend(
            [
                f"Итого с доставкой: {OrderAssistantService._money(quote['total_amount'])} ₽",
                "",
                "Выберите способ оплаты: наличными при получении или картой онлайн.",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _render_cancellation_options(result) -> str:
        cart = result.get("current_cart", {})
        orders = result.get("active_orders", [])
        if not cart.get("has_items") and not orders:
            return "У вас нет текущей корзины и активных оформленных заказов для отмены."
        lines = ["Уточните, что именно вы хотите отменить:"]
        if cart.get("has_items"):
            lines.append("• текущее оформление — очистить корзину и начать заново;")
        if orders:
            lines.append("• оформленный заказ:")
            for order in orders:
                lines.append(
                    f"  — {order['number']}: {order['status_label']}, "
                    f"оплата — {order['payment_status_label']}, "
                    f"сумма {OrderAssistantService._money(order['total_amount'])} ₽"
                )
        lines.append("Ответьте «корзину» либо укажите номер оформленного заказа.")
        return "\n".join(lines)

    @staticmethod
    def _render_stale_cart_prompt(result) -> str:
        cart = OrderAssistantService._render_cart(result)
        return (
            "С момента последнего диалога прошёл час, а в корзине остались товары.\n\n"
            f"{cart}\n\n"
            "Этот состав ещё актуален? Ответьте «продолжить» либо «очистить корзину»."
        )

    @staticmethod
    def _render_preview(result) -> str:
        preview = result.get("preview", {})
        lines = ["Проверьте заказ:", "", "Состав:"]
        for item in result.get("items", []):
            lines.append(
                f"• {item['name']}: {OrderAssistantService._quantity(item['quantity'])} {item.get('unit_label', item['unit'])} × "
                f"{OrderAssistantService._money(item['unit_price'])} ₽ = "
                f"{OrderAssistantService._money(item['line_total'])} ₽"
            )
        lines.extend(
            [
                "",
                f"Товары: {OrderAssistantService._money(preview.get('items_total'))} ₽",
                f"Скидка: {OrderAssistantService._money(preview.get('discount_amount'))} ₽",
            ]
        )
        if result.get("receiving_type") == "delivery":
            lines.extend(
                [
                    f"Адрес доставки: {result.get('delivery_address')}",
                    f"Стоимость доставки: {OrderAssistantService._money(preview.get('delivery_cost'))} ₽",
                ]
            )
            if preview.get("delivery_days") is not None:
                lines.append(f"Ориентировочный срок: {preview['delivery_days']} дн.")
        else:
            lines.append("Получение: самовывоз")
        lines.extend([f"Итого: {OrderAssistantService._money(preview.get('total_amount'))} ₽", ""])
        if result.get("receiving_type") == "delivery":
            lines.append(
                "Если состав, адрес, стоимость и срок доставки вас устраивают, "
                "подтвердите заказ одним сообщением."
            )
        else:
            lines.append(
                "Если состав и итоговая сумма вас устраивают, подтвердите заказ "
                "одним сообщением."
            )
        return "\n".join(lines)

    @staticmethod
    def _render_orders(result) -> str:
        orders = result.get("orders", [])
        if not orders:
            return "У вас пока нет оформленных заказов."
        lines = ["Ваши последние заказы:"]
        for order in orders:
            lines.extend(
                [
                    "",
                    f"Заказ {order['number']}",
                    f"Статус заказа: {order['status']}",
                    f"Статус оплаты: {order['payment_status']}",
                    f"Сумма: {OrderAssistantService._money(order['total_amount'])} ₽",
                ]
            )
            for item in order.get("items", []):
                lines.append(
                    f"• {item['name']}: {OrderAssistantService._quantity(item['quantity'])} {item.get('unit_label', item['unit'])} × "
                    f"{OrderAssistantService._money(item['unit_price'])} ₽ = "
                    f"{OrderAssistantService._money(item['total_price'])} ₽"
                )
        return "\n".join(lines)

    @staticmethod
    def _money(value) -> str:
        try:
            return f"{Decimal(str(value)).quantize(Decimal('0.01')):.2f}"
        except (InvalidOperation, TypeError, ValueError):
            return "—"

    @staticmethod
    def _quantity(value) -> str:
        try:
            rendered = format(Decimal(str(value)).normalize(), "f")
            return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered
        except (InvalidOperation, TypeError, ValueError):
            return "—"

    @staticmethod
    def _process_legacy(event, draft):
        from apps.intake.ai.services import AIExtractionService
        from apps.intake.clarifications import ClarificationService
        from apps.intake.draft_application import DraftExtractionApplier
        from apps.intake.enums import OrderDraftStatus
        from apps.intake.fulfillment import DraftOrderConversionService, DraftPricingService

        extraction, _run = AIExtractionService.extract_with_repair(event, draft)
        ClarificationService.record_pending_answer(draft, event)
        draft = DraftExtractionApplier.apply(draft, extraction)
        if draft.status == OrderDraftStatus.READY_FOR_PREVIEW:
            draft = DraftPricingService.preview(draft)
        if draft.status == OrderDraftStatus.CONFIRMED:
            order = DraftOrderConversionService.convert(draft)
            if settings.YOOKASSA_ENABLED and order.payment_method == "card_prepayment":
                from apps.payments.services import PaymentService

                PaymentService.ensure_payment_link(order)
        else:
            ClarificationService.sync_next_question(draft, event)
        return draft

    @staticmethod
    def _history(event) -> list[dict]:
        rows = list(
            AssistantMessage.objects.filter(
                conversation_key=event.conversation_key,
                event__channel=event.channel,
                event__external_user_id=event.external_user_id,
            )
            .order_by("-created_at", "-id")[: settings.AI_ASSISTANT_HISTORY_MESSAGES]
        )
        history = []
        for row in reversed(rows):
            content = row.content
            history.append({"role": row.role, "content": content})
        return history

    @staticmethod
    def _save_response(event, content, *, response_type, action_url=""):
        from apps.intake.leases import fenced_write
        with fenced_write():
            AssistantMessage.objects.get_or_create(
                event=event,
                role=AssistantMessageRole.ASSISTANT,
                defaults={
                    "conversation_key": event.conversation_key,
                    "content": content,
                    "response_type": response_type,
                    "action_url": action_url,
                },
            )
            from apps.assistant.conversation import remember
            remember(event, content, response_type)

    @staticmethod
    def _finish_turn(turn, status, started, model_calls, tool_calls, input_tokens, output_tokens, *, error_code="", error_message=""):
        from apps.intake.leases import fenced_write

        with fenced_write():
            turn.status = status
            turn.model_calls = model_calls
            turn.tool_calls = tool_calls
            turn.input_tokens = input_tokens
            turn.output_tokens = output_tokens
            turn.latency_ms = int((time.monotonic() - started) * 1000)
            turn.error_code = error_code[:64]
            turn.error_message = error_message[:2000]
            turn.completed_at = timezone.now()
            turn.save()
