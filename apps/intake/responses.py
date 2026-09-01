"""Единое представление результата диалога для channel-адаптеров."""
from dataclasses import dataclass

from apps.intake.enums import (
    ClarificationStatus,
    InboundEventStatus,
    OrderDraftStatus,
)

PENDING_EVENT_STATUSES = {
    InboundEventStatus.RECEIVED,
    InboundEventStatus.QUEUED,
    InboundEventStatus.PROCESSING,
    InboundEventStatus.RETRY_SCHEDULED,
}


@dataclass(frozen=True)
class ChannelResponse:
    response_id: str
    type: str
    message: str
    action_url: str = ""


class InboundEventResponseService:
    @classmethod
    def present(cls, event) -> dict:
        payload = {
            "event_id": str(event.public_id),
            "status": event.status,
            "complete": event.status not in PENDING_EVENT_STATUSES,
            "draft": None,
            "response": None,
        }
        draft = event.draft
        if draft is not None:
            payload["draft"] = {
                "id": str(draft.public_id),
                "status": draft.status,
                "revision": draft.revision,
                "total_amount": (
                    str(draft.total_amount) if draft.total_amount is not None else None
                ),
            }

        response = cls._response_for(event, draft)
        if response is not None:
            payload["response"] = {
                "id": response.response_id,
                "type": response.type,
                "message": response.message,
                "action_url": response.action_url,
            }
        return payload

    @staticmethod
    def _response_for(event, draft) -> ChannelResponse | None:
        if event.status in PENDING_EVENT_STATUSES:
            return None
        if event.status == InboundEventStatus.FAILED:
            return ChannelResponse(
                response_id=f"event:{event.public_id}:failed",
                type="processing_failed",
                message=(
                    "Не удалось автоматически обработать сообщение. "
                    "Заказ передан менеджеру, попробуйте также повторить запрос позже."
                ),
            )
        if event.status == InboundEventStatus.IGNORED:
            return ChannelResponse(
                response_id=f"event:{event.public_id}:ignored",
                type="ignored",
                message="Отправьте текстом, какие товары и в каком количестве вам нужны.",
            )
        if draft is None:
            return None

        clarification = (
            draft.clarifications.filter(status=ClarificationStatus.PENDING)
            .order_by("asked_at", "id")
            .first()
        )
        if clarification is not None:
            return ChannelResponse(
                response_id=f"clarification:{clarification.pk}",
                type="clarification",
                message=clarification.question,
            )
        if draft.status == OrderDraftStatus.CONVERTED and draft.converted_order_id:
            order = draft.converted_order
            payment = (
                order.payments.filter(
                    confirmation_url__gt="",
                    state__in=["pending", "waiting_for_capture"],
                )
                .order_by("-created_at")
                .first()
            )
            if payment is not None:
                return ChannelResponse(
                    response_id=f"payment:{payment.pk}",
                    type="payment_link",
                    message=(
                        f"Заказ {order.public_number} оформлен. "
                        f"Итого: {order.total_amount} ₽. "
                        f"Оплатите по ссылке: {payment.confirmation_url}"
                    ),
                    action_url=payment.confirmation_url,
                )
            return ChannelResponse(
                response_id=f"order:{order.public_number}",
                type="order_created",
                message=(
                    f"Заказ {order.public_number} оформлен. "
                    f"Итого: {order.total_amount} ₽."
                ),
            )
        if draft.status == OrderDraftStatus.CANCELLED:
            return ChannelResponse(
                response_id=f"draft:{draft.public_id}:cancelled",
                type="order_cancelled",
                message="Оформление заказа отменено.",
            )
        if draft.status == OrderDraftStatus.ESCALATED:
            return ChannelResponse(
                response_id=f"draft:{draft.public_id}:escalated",
                type="manager_escalation",
                message=(
                    "Не удалось однозначно оформить заказ автоматически. "
                    "Диалог передан менеджеру."
                ),
            )
        return ChannelResponse(
            response_id=f"draft:{draft.public_id}:revision:{draft.revision}",
            type="accepted",
            message="Сообщение принято, продолжаю собирать данные заказа.",
        )
