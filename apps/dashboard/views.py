"""Сводные показатели и очередь исключений для сотрудников магазина."""
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Avg, Count, Q, Sum
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from apps.common.enums import Channel, OrderStatus, PaymentStatus
from apps.customers.models import CustomerIdentityConflict, IdentityConflictStatus
from apps.delivery.models import DeliveryQuote, DeliveryQuoteStatus, Shipment, ShipmentStatus
from apps.intake.enums import (
    AIRunStatus,
    ClarificationStatus,
    InboundEventStatus,
    OrderDraftStatus,
)
from apps.intake.models import AIExtractionRun, Clarification, InboundEvent, OrderDraft
from apps.orders.models import Order
from apps.payments.models import Payment, PaymentState, Refund, RefundState


DEFAULT_PERIOD_DAYS = 30


@dataclass(frozen=True)
class AttentionItem:
    """Одна строка единой очереди для перехода к объекту в Django Admin."""

    category: str
    label: str
    detail: str
    created_at: datetime
    admin_url: str


def _parse_date(value: str | None, fallback: date) -> date:
    if not value:
        return fallback
    try:
        return date.fromisoformat(value)
    except ValueError:
        return fallback


def _period(request: HttpRequest) -> tuple[date, date, datetime, datetime]:
    today = timezone.localdate()
    start = _parse_date(request.GET.get("from"), today - timedelta(days=DEFAULT_PERIOD_DAYS - 1))
    end = _parse_date(request.GET.get("to"), today)
    if start > end:
        start, end = end, start
    start_at = timezone.make_aware(datetime.combine(start, time.min))
    end_at = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min))
    return start, end, start_at, end_at


def _admin_url(model_name: str, pk: int) -> str:
    return reverse(f"admin:{model_name}_change", args=[pk])


def _attention_items() -> list[AttentionItem]:
    items: list[AttentionItem] = []

    for draft in OrderDraft.objects.filter(
        manager_attention_required=True
    ).order_by("-updated_at")[:20]:
        items.append(
            AttentionItem(
                "AI-заказ",
                f"Черновик {draft.public_id}",
                draft.escalation_reason or draft.get_status_display(),
                draft.updated_at,
                _admin_url("intake_orderdraft", draft.pk),
            )
        )
    for event in InboundEvent.objects.filter(status=InboundEventStatus.FAILED).order_by(
        "-updated_at"
    )[:20]:
        items.append(
            AttentionItem(
                "Входящий канал",
                f"{event.get_channel_display()}: {event.external_event_id}",
                event.last_error or "Событие не обработано",
                event.updated_at,
                _admin_url("intake_inboundevent", event.pk),
            )
        )
    for clarification in Clarification.objects.filter(
        status=ClarificationStatus.PENDING
    ).select_related("draft").order_by("-asked_at")[:20]:
        items.append(
            AttentionItem(
                "Уточнение клиента",
                f"Черновик {clarification.draft.public_id}",
                clarification.question,
                clarification.asked_at,
                _admin_url("intake_clarification", clarification.pk),
            )
        )
    for conflict in CustomerIdentityConflict.objects.filter(
        status=IdentityConflictStatus.PENDING
    ).order_by("-created_at")[:20]:
        items.append(
            AttentionItem(
                "Идентификация",
                conflict.get_contact_type_display(),
                conflict.contact_value,
                conflict.created_at,
                _admin_url("customers_customeridentityconflict", conflict.pk),
            )
        )
    for quote in DeliveryQuote.objects.filter(status=DeliveryQuoteStatus.FAILED).order_by(
        "-updated_at"
    )[:20]:
        items.append(
            AttentionItem(
                "Доставка",
                "Ошибка расчёта доставки",
                quote.error_message or quote.error_code or "Нужна повторная проверка",
                quote.updated_at,
                _admin_url("delivery_deliveryquote", quote.pk),
            )
        )
    for shipment in Shipment.objects.filter(status=ShipmentStatus.FAILED).order_by(
        "-updated_at"
    )[:20]:
        items.append(
            AttentionItem(
                "Доставка",
                f"Заказ {shipment.order.public_number}",
                shipment.last_error or "Ошибка оформления или синхронизации доставки",
                shipment.updated_at,
                _admin_url("delivery_shipment", shipment.pk),
            )
        )
    for payment in Payment.objects.filter(state=PaymentState.FAILED).select_related("order").order_by(
        "-updated_at"
    )[:20]:
        items.append(
            AttentionItem(
                "Оплата",
                f"Заказ {payment.order.public_number}",
                payment.last_error or "Не удалось создать оплату",
                payment.updated_at,
                _admin_url("payments_payment", payment.pk),
            )
        )
    for refund in Refund.objects.filter(state=RefundState.FAILED).select_related(
        "payment__order"
    ).order_by("-updated_at")[:20]:
        items.append(
            AttentionItem(
                "Возврат",
                f"Заказ {refund.payment.order.public_number}",
                refund.last_error or "Не удалось выполнить возврат",
                refund.updated_at,
                _admin_url("payments_refund", refund.pk),
            )
        )
    return sorted(items, key=lambda item: item.created_at, reverse=True)[:30]


def _counts(items: Iterable[AttentionItem]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in items:
        result[item.category] = result.get(item.category, 0) + 1
    return result


@staff_member_required
def manager_dashboard(request: HttpRequest) -> HttpResponse:
    """Показывает показатели периода только авторизованному сотруднику."""
    start, end, start_at, end_at = _period(request)
    orders = Order.objects.filter(created_at__gte=start_at, created_at__lt=end_at)
    order_totals = orders.aggregate(
        count=Count("id"),
        revenue=Sum("total_amount"),
        paid=Count("id", filter=Q(payment_status=PaymentStatus.PAID)),
    )
    channels = list(
        orders.values("channel")
        .annotate(count=Count("id"), revenue=Sum("total_amount"))
        .order_by("channel")
    )
    for row in channels:
        row["label"] = Channel(row["channel"]).label
        row["revenue"] = row["revenue"] or Decimal("0")
    order_statuses = list(orders.values("order_status").annotate(count=Count("id")))
    for row in order_statuses:
        row["label"] = OrderStatus(row["order_status"]).label
    payment_statuses = list(
        orders.values("payment_status").annotate(count=Count("id"))
    )
    for row in payment_statuses:
        row["label"] = PaymentStatus(row["payment_status"]).label
    shipment_statuses = list(
        Shipment.objects.filter(created_at__gte=start_at, created_at__lt=end_at)
        .values("status")
        .annotate(count=Count("id"))
    )
    for row in shipment_statuses:
        row["label"] = ShipmentStatus(row["status"]).label

    ai_runs = AIExtractionRun.objects.filter(created_at__gte=start_at, created_at__lt=end_at)
    ai_stats = ai_runs.aggregate(
        total=Count("id"),
        succeeded=Count("id", filter=Q(status=AIRunStatus.SUCCEEDED)),
        failed=Count(
            "id",
            filter=Q(
                status__in=[AIRunStatus.SCHEMA_INVALID, AIRunStatus.PROVIDER_ERROR]
            ),
        ),
        avg_latency=Avg("latency_ms"),
    )
    draft_count = OrderDraft.objects.filter(created_at__gte=start_at, created_at__lt=end_at).count()
    automated_orders = orders.filter(source_draft__isnull=False).count()
    attention = _attention_items()
    order_count = order_totals["count"] or 0

    context = {
        "period_start": start,
        "period_end": end,
        "order_count": order_count,
        "revenue": order_totals["revenue"] or Decimal("0"),
        "paid_count": order_totals["paid"] or 0,
        "paid_share": round((order_totals["paid"] or 0) * 100 / order_count) if order_count else 0,
        "automated_orders": automated_orders,
        "automation_share": round(automated_orders * 100 / order_count) if order_count else 0,
        "draft_count": draft_count,
        "ai_stats": ai_stats,
        "channels": channels,
        "order_statuses": order_statuses,
        "payment_statuses": payment_statuses,
        "shipment_statuses": shipment_statuses,
        "attention": attention,
        "attention_counts": _counts(attention),
    }
    return render(request, "dashboard/manager_dashboard.html", context)
