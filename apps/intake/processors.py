"""Нормализация входящих событий до подключения LLM-парсинга."""

from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError

from apps.customers.validators import normalize_email, normalize_phone
from apps.intake.enums import InboundEventKind, InboundEventStatus, OrderIntent
from apps.intake.models import InboundEvent, OrderDraft
from apps.intake.services import OrderDraftService
from apps.intake.cart_bridge import UnifiedCartBridge


@dataclass(frozen=True)
class ProcessingOutcome:
    status: str
    draft_id: int | None = None


class InboundEventProcessor:
    """Связывает событие с серверным черновиком; AI-этап будет добавлен позже."""

    @staticmethod
    def process(event_id: int) -> ProcessingOutcome:
        from apps.intake.leases import fenced_write

        # Normalize and attach the event in one short fenced transaction. The
        # potentially slow LLM call begins only after this block is committed.
        with fenced_write():
            event = InboundEvent.objects.select_related("customer").get(pk=event_id)
            if event.kind == InboundEventKind.MESSAGE and not event.raw_text.strip():
                return ProcessingOutcome(status=InboundEventStatus.IGNORED)

            draft, created = OrderDraftService.get_or_create_active(
                channel=event.channel,
                external_user_id=event.external_user_id,
                conversation_key=event.conversation_key,
                customer=event.customer,
                intent=OrderIntent.UNKNOWN,
            )
            if event.customer_id and draft.customer_id is None:
                locked_draft = OrderDraft.objects.select_for_update().get(pk=draft.pk)
                if locked_draft.customer_id is None:
                    locked_draft.customer_id = event.customer_id
                    locked_draft.save(update_fields=["customer", "updated_at"])

            contact_phone = str(event.raw_payload.get("contact_phone", "")).strip()
            contact_email = str(event.raw_payload.get("contact_email", "")).strip()
            if not contact_phone and event.customer_id and event.channel != "website":
                contact_phone = event.customer.phone
            if not contact_email and event.customer_id and event.channel != "website":
                contact_email = event.customer.email
            try:
                contact_phone = normalize_phone(contact_phone) if contact_phone else ""
            except ValidationError:
                contact_phone = ""
            try:
                contact_email = normalize_email(contact_email) if contact_email else ""
            except ValidationError:
                contact_email = ""
            contact_updates = {}
            if contact_phone and contact_phone != draft.contact_phone:
                contact_updates["contact_phone"] = contact_phone
            if contact_email and contact_email != draft.contact_email:
                contact_updates["contact_email"] = contact_email
            if contact_updates:
                OrderDraft.objects.filter(pk=draft.pk).update(**contact_updates)
                draft.refresh_from_db(fields=list(contact_updates))

            # A fresh website AI conversation can reuse only the products from
            # its browser cart.  Address, receiving method, payment method and
            # contacts are an explicit decision in this conversation and must
            # never leak from an earlier manual checkout.
            fresh_website_assistant_dialogue = (
                created
                and event.channel == "website"
                and event.raw_payload.get("source") == "website_ai_assistant"
            )
            draft = UnifiedCartBridge.cart_to_draft(
                draft,
                include_checkout=not fresh_website_assistant_dialogue,
            )
            InboundEvent.objects.filter(pk=event.pk).update(
                draft_id=draft.pk,
                customer_id=event.customer_id or draft.customer_id,
            )
        if settings.AI_ASSISTANT_ENABLED or settings.AI_ORDER_PROCESSING_ENABLED:
            from apps.assistant.services import OrderAssistantService

            draft = OrderAssistantService.process(event, draft)
        return ProcessingOutcome(
            status=InboundEventStatus.PROCESSED,
            draft_id=draft.pk,
        )
