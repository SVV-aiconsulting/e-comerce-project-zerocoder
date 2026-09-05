"""Ограниченный GigaChat tool-calling оркестратор над backend WebMarket."""

import json
import time
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils import timezone

from apps.assistant.prompts import ASSISTANT_TOOLS_SYSTEM_PROMPT
from apps.assistant.runtime import get_assistant_runtime
from apps.assistant.tools import AssistantToolExecutor
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
            draft.refresh_from_db()
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
            checkout = backend.checkout_action()
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

            for call_index in range(1, settings.AI_ASSISTANT_MAX_TOOL_CALLS + 2):
                completion = llm.generate_with_tools(
                    system_prompt=system_prompt,
                    messages=messages,
                    functions=backend.definitions(),
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
        return (
            f"{ASSISTANT_TOOLS_SYSTEM_PROMPT}\n\n"
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
            return cls._render_preview(result), "order_preview", ""
        if tool_name == "search_products":
            return cls._render_catalog(result), "catalog", ""
        if tool_name == "get_cart":
            return cls._render_cart(result), "cart", ""
        if tool_name in {"set_cart_item", "remove_cart_item", "configure_checkout"}:
            lines = [cls._render_cart(result), ""]
            lines.append(
                AssistantToolExecutor._missing_fields_message(
                    result.get("missing_fields")
                )
                if result.get("missing_fields")
                else "Все обязательные параметры заполнены. Рассчитать актуальный итог?"
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
        if not products:
            return f"По запросу «{query}» активных товаров в каталоге не найдено."
        if result.get("scope") != "full_catalog" and len(products) == 1:
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
        title = "Полный каталог активных товаров:" if result.get("scope") == "full_catalog" else f"Товары по запросу «{query}»:"
        lines = [title]
        for product in products:
            lines.append(
                f"• {product['name']} — {OrderAssistantService._money(product['price'])} ₽ "
                f"за {product['unit_label'].lower()}; минимальный заказ: "
                f"{OrderAssistantService._quantity(product['min_quantity'])} {product['unit_label'].lower()}"
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
            lines.append(f"Способ оплаты: {result['payment_method']}")
        if result.get("total_amount") is not None:
            lines.append(f"Итого последнего расчёта: {OrderAssistantService._money(result['total_amount'])} ₽")
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
        lines.extend(
            [
                f"Итого: {OrderAssistantService._money(preview.get('total_amount'))} ₽",
                "",
                "Если состав, адрес, стоимость и срок доставки вас устраивают, подтвердите заказ одним сообщением.",
            ]
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
            if row.role == AssistantMessageRole.ASSISTANT:
                # Предыдущий текст ассистента неизменно хранится в БД для аудита
                # и показа клиенту, но не возвращается модели как источник фактов.
                # Актуальные значения приходят только через BACKEND_CONTEXT/tools.
                response_type = row.response_type or "assistant"
                content = (
                    f"[Ранее отправлен ответ типа {response_type}. Не используй "
                    "его как источник фактов; проверь актуальные данные через "
                    "backend-инструмент текущего хода.]"
                )
            history.append({"role": row.role, "content": content})
        return history

    @staticmethod
    def _save_response(event, content, *, response_type, action_url=""):
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

    @staticmethod
    def _finish_turn(turn, status, started, model_calls, tool_calls, input_tokens, output_tokens, *, error_code="", error_message=""):
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
