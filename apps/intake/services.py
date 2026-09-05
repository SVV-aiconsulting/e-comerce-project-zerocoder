"""Транзакционные операции с событиями и черновиками AI-заказов."""
import logging
from dataclasses import dataclass
from decimal import Decimal
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.catalog.services import CatalogService
from apps.common.exceptions import MinQuantityError, ProductUnavailableError
from apps.common.enums import ReceivingType
from apps.intake.enums import (
    ACTIVE_DRAFT_STATUSES,
    ClarificationStatus,
    InboundEventStatus,
    ItemMatchStatus,
    OrderDraftStatus,
    OrderIntent,
)
from apps.intake.exceptions import DraftNotReadyError, DraftStateError
from apps.intake.models import InboundEvent, OrderDraft

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EventRegistrationResult:
    event: InboundEvent
    created: bool


class InboundEventService:
    """Приём события с защитой от повторной доставки каналом."""

    @staticmethod
    def register(**event_data) -> EventRegistrationResult:
        lookup = {
            "channel": event_data.pop("channel"),
            "external_event_id": event_data.pop("external_event_id"),
        }
        event, created = InboundEvent.objects.get_or_create(
            **lookup,
            defaults=event_data,
        )
        return EventRegistrationResult(event=event, created=created)

    @classmethod
    def enqueue(cls, event: InboundEvent) -> bool:
        """Зафиксировать событие в durable-очереди и опубликовать после commit."""
        with transaction.atomic():
            locked = InboundEvent.objects.select_for_update().get(pk=event.pk)
            if locked.status != InboundEventStatus.RECEIVED:
                return False

            locked.status = InboundEventStatus.QUEUED
            locked.next_retry_at = None
            locked.save(update_fields=["status", "next_retry_at", "updated_at"])
            transaction.on_commit(
                lambda event_id=locked.pk: cls.publish(event_id),
                robust=True,
            )
        return True

    @staticmethod
    def publish(event_id: int) -> bool:
        """Опубликовать ID события; запись в PostgreSQL остаётся источником истины."""
        from apps.intake.tasks import process_inbound_event

        try:
            process_inbound_event.apply_async(
                args=[event_id],
                queue="intake",
                retry=False,
            )
        except Exception as exc:  # публикацию восстановит периодический dispatcher
            error = f"Ошибка публикации {type(exc).__name__}: {str(exc)}"[:2000]
            InboundEvent.objects.filter(pk=event_id).update(
                last_error=error,
                next_retry_at=timezone.now() + timedelta(seconds=30),
            )
            logger.warning("Не удалось опубликовать InboundEvent id=%s", event_id)
            return False

        InboundEvent.objects.filter(pk=event_id).update(next_retry_at=None)
        return True


class OrderDraftService:
    """Управляемые переходы черновика с блокировкой от конкурентных запросов."""

    @staticmethod
    def get_or_create_active(
        *,
        channel: str,
        external_user_id: str,
        conversation_key: str,
        customer=None,
        intent: str = OrderIntent.CREATE_ORDER,
    ) -> tuple[OrderDraft, bool]:
        existing = OrderDraft.objects.filter(
            channel=channel,
            conversation_key=conversation_key,
            status__in=ACTIVE_DRAFT_STATUSES,
        ).first()
        if existing:
            return existing, False

        try:
            with transaction.atomic():
                draft = OrderDraft.objects.create(
                    channel=channel,
                    external_user_id=external_user_id,
                    conversation_key=conversation_key,
                    customer=customer,
                    intent=intent,
                )
            return draft, True
        except IntegrityError:
            draft = OrderDraft.objects.get(
                channel=channel,
                conversation_key=conversation_key,
                status__in=ACTIVE_DRAFT_STATUSES,
            )
            return draft, False

    @staticmethod
    @transaction.atomic
    def record_change(draft: OrderDraft) -> OrderDraft:
        locked = OrderDraft.objects.select_for_update().get(pk=draft.pk)
        if locked.status not in ACTIVE_DRAFT_STATUSES:
            raise DraftStateError("Завершённый черновик нельзя изменить")

        locked.revision += 1
        locked.previewed_revision = None
        locked.confirmed_revision = None
        locked.priced_at = None
        locked.confirmed_at = None
        locked.items_total = None
        locked.discount_amount = None
        locked.delivery_cost = None
        locked.total_amount = None
        locked.status = OrderDraftStatus.COLLECTING
        locked.save(
            update_fields=[
                "revision",
                "previewed_revision",
                "confirmed_revision",
                "priced_at",
                "confirmed_at",
                "items_total",
                "discount_amount",
                "delivery_cost",
                "total_amount",
                "status",
                "updated_at",
            ]
        )
        return locked

    @staticmethod
    def validate_ready_for_preview(draft: OrderDraft) -> None:
        errors = []
        if not draft.customer_id:
            errors.append("Не идентифицирован клиент")
        if not draft.receiving_type:
            errors.append("Не выбран способ получения")
        if draft.receiving_type == ReceivingType.DELIVERY and not draft.delivery_address.strip():
            errors.append("Не указан адрес доставки")
        if (
            settings.YANDEX_DELIVERY_ENABLED
            and draft.receiving_type == ReceivingType.DELIVERY
            and not draft.contact_phone
        ):
            errors.append("Не указан телефон получателя для Яндекс Доставки")
        if (
            settings.YANDEX_DELIVERY_ENABLED
            and draft.receiving_type == ReceivingType.DELIVERY
            and draft.payment_method == "cash_on_delivery"
        ):
            errors.append("Яндекс Доставка не принимает наличные при получении")
        if draft.payment_method == "card_prepayment" and not draft.contact_email:
            errors.append("Не указан email для электронного чека ЮKassa")
        if draft.clarifications.filter(status=ClarificationStatus.PENDING).exists():
            errors.append("Есть неотвеченные уточнения")

        items = list(draft.items.select_related("product"))
        if not items:
            errors.append("В черновике нет товаров")
        for item in items:
            if (
                item.match_status != ItemMatchStatus.MATCHED
                or item.product_id is None
                or item.requested_quantity is None
            ):
                errors.append(f"Позиция {item.line_number} не готова")
            else:
                # Черновик мог ждать ответа клиента часами: цену берём при
                # preview из текущей карточки товара, а доступность и минимум
                # перепроверяем непосредственно перед подтверждением.
                try:
                    CatalogService.check_availability(
                        item.product, item.requested_quantity
                    )
                except (MinQuantityError, ProductUnavailableError) as exc:
                    errors.append(str(exc))

        if errors:
            raise DraftNotReadyError("; ".join(errors))

    @classmethod
    @transaction.atomic
    def record_preview(
        cls,
        draft: OrderDraft,
        *,
        items_total: Decimal,
        discount_amount: Decimal,
        delivery_cost: Decimal,
        total_amount: Decimal,
    ) -> OrderDraft:
        locked = OrderDraft.objects.select_for_update().get(pk=draft.pk)
        cls.validate_ready_for_preview(locked)
        if any(value < 0 for value in (items_total, discount_amount, delivery_cost, total_amount)):
            raise DraftNotReadyError("Суммы preview не могут быть отрицательными")

        locked.items_total = items_total
        locked.discount_amount = discount_amount
        locked.delivery_cost = delivery_cost
        locked.total_amount = total_amount
        locked.previewed_revision = locked.revision
        locked.confirmed_revision = None
        locked.confirmed_at = None
        locked.priced_at = timezone.now()
        locked.status = OrderDraftStatus.AWAITING_CONFIRMATION
        locked.save(
            update_fields=[
                "items_total",
                "discount_amount",
                "delivery_cost",
                "total_amount",
                "previewed_revision",
                "confirmed_revision",
                "confirmed_at",
                "priced_at",
                "status",
                "updated_at",
            ]
        )
        return locked

    @staticmethod
    @transaction.atomic
    def confirm(draft: OrderDraft) -> OrderDraft:
        locked = OrderDraft.objects.select_for_update().get(pk=draft.pk)
        if locked.status != OrderDraftStatus.AWAITING_CONFIRMATION:
            raise DraftStateError("Черновик не ожидает подтверждения")
        if locked.previewed_revision != locked.revision:
            raise DraftStateError("Черновик изменился после расчёта")

        locked.confirmed_revision = locked.revision
        locked.confirmed_at = timezone.now()
        locked.status = OrderDraftStatus.CONFIRMED
        locked.save(
            update_fields=[
                "confirmed_revision",
                "confirmed_at",
                "status",
                "updated_at",
            ]
        )
        return locked

    @staticmethod
    @transaction.atomic
    def mark_converted(draft: OrderDraft, order) -> OrderDraft:
        locked = OrderDraft.objects.select_for_update().get(pk=draft.pk)
        if locked.converted_order_id:
            if locked.converted_order_id != order.pk:
                raise DraftStateError("Черновик уже связан с другим заказом")
            return locked
        if (
            locked.status != OrderDraftStatus.CONFIRMED
            or locked.confirmed_revision != locked.revision
        ):
            raise DraftStateError("Можно преобразовать только актуальный подтверждённый черновик")

        locked.converted_order = order
        locked.status = OrderDraftStatus.CONVERTED
        locked.save(update_fields=["converted_order", "status", "updated_at"])
        return locked
