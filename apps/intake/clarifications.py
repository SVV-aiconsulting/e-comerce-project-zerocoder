"""Управляемый цикл уточняющих вопросов без выдуманных бизнес-данных."""
import re

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Product
from apps.intake.enums import ClarificationStatus, OrderDraftStatus
from apps.intake.models import Clarification, OrderDraft

ITEM_FIELD_PATTERN = re.compile(r"^items\.(\d+)\.(product|quantity|unit|validation)$")


class ClarificationService:
    @classmethod
    @transaction.atomic
    def record_pending_answer(cls, draft: OrderDraft, event) -> Clarification | None:
        clarification = (
            Clarification.objects.select_for_update()
            .filter(draft=draft, status=ClarificationStatus.PENDING)
            .order_by("asked_at", "id")
            .first()
        )
        if clarification is None:
            return None
        clarification.status = ClarificationStatus.ANSWERED
        clarification.answered_by_event = event
        clarification.answer_text = event.raw_text
        clarification.answered_at = timezone.now()
        clarification.save(
            update_fields=[
                "status",
                "answered_by_event",
                "answer_text",
                "answered_at",
                "updated_at",
            ]
        )
        return clarification

    @classmethod
    @transaction.atomic
    def sync_next_question(cls, draft: OrderDraft, event) -> Clarification | None:
        locked = OrderDraft.objects.select_for_update().get(pk=draft.pk)
        field_path = cls._next_field_path(locked)
        pending = list(
            Clarification.objects.select_for_update().filter(
                draft=locked,
                status=ClarificationStatus.PENDING,
            )
        )
        for clarification in pending:
            if clarification.field_path == field_path:
                return clarification
            clarification.status = ClarificationStatus.CANCELLED
            clarification.save(update_fields=["status", "updated_at"])

        if not field_path:
            return None
        attempt_number = (
            Clarification.objects.filter(draft=locked, field_path=field_path).count()
            + 1
        )
        if attempt_number > settings.INTAKE_MAX_CLARIFICATION_ATTEMPTS:
            locked.status = OrderDraftStatus.ESCALATED
            locked.manager_attention_required = True
            locked.escalation_reason = (
                f"Превышен лимит уточнений поля {field_path}"
            )
            locked.save(
                update_fields=[
                    "status",
                    "manager_attention_required",
                    "escalation_reason",
                    "updated_at",
                ]
            )
            return None

        return Clarification.objects.create(
            draft=locked,
            field_path=field_path,
            question=cls._build_question(locked, field_path),
            trigger_event=event,
            attempt_number=attempt_number,
        )

    @staticmethod
    def _next_field_path(draft: OrderDraft) -> str:
        if draft.status == OrderDraftStatus.AWAITING_CONFIRMATION:
            return "confirmation"
        if draft.status == OrderDraftStatus.NEEDS_CLARIFICATION and draft.missing_fields:
            return draft.missing_fields[0]
        return ""

    @classmethod
    def _build_question(cls, draft: OrderDraft, field_path: str) -> str:
        questions = {
            "customer": (
                "Чтобы оформить заказ, укажите телефон или email для связи. "
                "На сайте также отметьте согласие на обработку этих данных."
            ),
            "receiving_type": "Как вы хотите получить заказ: доставка или самовывоз?",
            "delivery_address": "Укажите полный адрес доставки.",
            "contact_phone": (
                "Для Яндекс Доставки нужен контактный телефон получателя. "
                "Укажите номер в формате +7XXXXXXXXXX."
            ),
            "payment_method": (
                "Как удобнее оплатить: наличными при получении или банковской "
                "картой онлайн? При оплате картой я пришлю безопасную ссылку ЮKassa."
            ),
            "delivery_payment_method": (
                "Яндекс Доставка не принимает наличные. Выберите онлайн-оплату "
                "или оплату картой при получении."
            ),
            "desired_date": "Уточните желаемую дату получения заказа.",
            "delivery_quote": (
                "Не удалось получить расчёт Яндекс Доставки. Проверьте адрес и "
                "напишите «повторить расчёт» или выберите самовывоз."
            ),
            "items": "Какие товары и в каком количестве вы хотите заказать?",
            "sales_intent": (
                "Этот товар есть в каталоге. Хотите добавить его в заказ? "
                "Напишите нужное количество."
            ),
            "order_status": (
                "Для проверки статуса откройте «Мои заказы» или напишите номер заказа."
            ),
            "assistant_intent": (
                "Здравствуйте! Расскажите, что хотите купить, например: "
                "«хочу рыбу на ужин» или «добавь килограмм креветок»."
            ),
            "confirmation": cls._confirmation_question(draft),
        }
        if field_path in questions:
            return questions[field_path]

        match = ITEM_FIELD_PATTERN.match(field_path)
        if not match:
            return "Пожалуйста, уточните недостающие данные заказа."
        index, field = int(match.group(1)), match.group(2)
        items = list(draft.items.order_by("line_number"))
        if index >= len(items):
            return "Пожалуйста, уточните недостающие данные позиции заказа."
        item = items[index]
        if field == "product":
            candidates = list(
                Product.objects.filter(pk__in=item.candidate_product_ids, is_active=True)
                .order_by("name")[:5]
            )
            if candidates:
                options = "; ".join(product.name for product in candidates)
                return f"Какой товар вы имели в виду: {options}?"
            return f"Не нашёл в каталоге «{item.raw_product_name}». Уточните название."
        if field == "quantity":
            return f"Сколько товара «{item.raw_product_name}» вам нужно?"
        if field == "unit":
            return (
                f"Уточните единицу количества для «{item.raw_product_name}»: "
                "килограммы, штуки или упаковки?"
            )
        return f"Уточните количество или единицу для «{item.raw_product_name}»."

    @staticmethod
    def _confirmation_question(draft: OrderDraft) -> str:
        lines = []
        for item in draft.items.select_related("product").order_by("line_number"):
            name = item.product.name if item.product_id else item.raw_product_name
            lines.append(f"• {name} — {item.requested_quantity} {item.requested_unit}")
        payment = {
            "cash_on_delivery": "наличными при получении",
            "card_on_delivery": "картой при получении",
            "card_prepayment": "картой онлайн",
        }.get(draft.payment_method, draft.payment_method or "—")
        parts = ["Проверьте заказ:", *lines]
        if draft.items_total is not None:
            parts.append(f"Товары: {draft.items_total} ₽")
        if draft.receiving_type == "delivery":
            parts.append(f"Доставка: {draft.delivery_cost or 0} ₽, {draft.delivery_address}")
        else:
            parts.append("Получение: самовывоз")
        parts.extend(
            [
                f"Оплата: {payment}",
                f"Итого: {draft.total_amount if draft.total_amount is not None else '—'} ₽",
                "Подтверждаете оформление? Ответьте «да» или сообщите изменения.",
            ]
        )
        return "\n".join(parts)
