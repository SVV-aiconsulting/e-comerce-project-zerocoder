"""Ограниченный GigaChat tool-calling оркестратор над backend WebMarket."""

import json
import time

from django.conf import settings
from django.utils import timezone

from apps.assistant.prompts import ASSISTANT_TOOLS_SYSTEM_PROMPT
from apps.assistant.runtime import get_assistant_runtime
from apps.assistant.tools import AssistantToolExecutor
from apps.intake.ai.providers.gigachat import get_gigachat_provider
from apps.intake.enums import AssistantMessageRole, AssistantTurnStatus
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
        llm = provider or get_gigachat_provider()
        action_url = ""
        response_type = "assistant"
        model_calls = tool_calls = input_tokens = output_tokens = 0

        try:
            for call_index in range(1, settings.AI_ASSISTANT_MAX_TOOL_CALLS + 2):
                completion = llm.generate_with_tools(
                    system_prompt=ASSISTANT_TOOLS_SYSTEM_PROMPT,
                    messages=messages,
                    functions=backend.definitions(),
                )
                model_calls += 1
                input_tokens += completion.input_tokens or 0
                output_tokens += completion.output_tokens or 0
                turn.model_name = completion.model_name

                if completion.function_call is None:
                    cls._save_response(
                        event,
                        completion.content.strip(),
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
        return [{"role": row.role, "content": row.content} for row in reversed(rows)]

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
