"""Оркестрация диалога над проверяемыми backend-сервисами WebMarket."""

from django.conf import settings

from apps.assistant.runtime import get_assistant_runtime
from apps.intake.ai.services import AIExtractionService
from apps.intake.clarifications import ClarificationService
from apps.intake.draft_application import DraftExtractionApplier
from apps.intake.enums import OrderDraftStatus
from apps.intake.fulfillment import DraftOrderConversionService, DraftPricingService


class OrderAssistantService:
    """Единый сценарий заказа для Telegram, website и следующих адаптеров."""

    @classmethod
    def process(cls, event, draft):
        runtime = get_assistant_runtime()
        if not runtime.enabled:
            return draft

        extraction, _run = AIExtractionService.extract_with_repair(
            event,
            draft,
            provider_name=runtime.provider,
            prompt_profile=runtime.prompt_profile,
        )
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
