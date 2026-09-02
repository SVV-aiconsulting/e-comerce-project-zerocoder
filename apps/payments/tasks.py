"""Фоновая сверка ЮKassa и уведомления клиентов об успешной оплате."""

import httpx
from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.common.enums import Channel
from apps.payments.exceptions import PaymentError
from apps.payments.models import Payment, PaymentState
from apps.payments.services import PaymentService


def _send_telegram_message(*, chat_id: str, text: str) -> None:
    token = settings.TELEGRAM_BOT_TOKEN.strip()
    if not token:
        raise RuntimeError("Не задан TELEGRAM_BOT_TOKEN для уведомлений")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    with httpx.Client(timeout=settings.TELEGRAM_NOTIFICATION_TIMEOUT_SECONDS) as client:
        response = client.post(url, json={"chat_id": chat_id, "text": text})
    if response.status_code >= 400:
        raise RuntimeError(f"Telegram API вернул HTTP {response.status_code}")


@shared_task(name="payments.notify_succeeded")
def notify_payment_succeeded(payment_id: int):
    payment = Payment.objects.select_related("order").filter(pk=payment_id).first()
    if payment is None:
        return {"status": "missing"}
    if payment.state != PaymentState.SUCCEEDED:
        return {"status": "not_paid"}
    if payment.paid_notification_sent_at:
        return {"status": "already_sent"}
    order = payment.order
    if order.channel != Channel.TELEGRAM or not order.source_external_user_id_snapshot:
        return {"status": "channel_without_push"}

    Payment.objects.filter(pk=payment.pk).update(
        paid_notification_attempts=payment.paid_notification_attempts + 1,
        paid_notification_error="",
    )
    try:
        _send_telegram_message(
            chat_id=order.source_external_user_id_snapshot,
            text="Ваш заказ оплачен.",
        )
    except (httpx.HTTPError, RuntimeError) as exc:
        Payment.objects.filter(pk=payment.pk).update(
            paid_notification_error=f"{type(exc).__name__}: {str(exc)}"[:1000],
        )
        return {"status": "retry_later"}

    Payment.objects.filter(
        pk=payment.pk,
        paid_notification_sent_at__isnull=True,
    ).update(
        paid_notification_sent_at=timezone.now(),
        paid_notification_error="",
    )
    return {"status": "sent"}


@shared_task(name="payments.sync_pending")
def sync_pending_payments():
    payments = list(
        Payment.objects.filter(
            state__in=[PaymentState.PENDING, PaymentState.WAITING_FOR_CAPTURE],
            external_id__gt="",
        )
        .select_related("order")
        .order_by("updated_at")[: settings.PAYMENT_SYNC_BATCH_SIZE]
    )
    synced = failed = 0
    for payment in payments:
        try:
            PaymentService.sync_payment(payment)
            synced += 1
        except PaymentError as exc:
            payment.last_error = f"{type(exc).__name__}: {str(exc)}"[:1000]
            payment.save(update_fields=["last_error", "updated_at"])
            failed += 1
    return {"selected": len(payments), "synced": synced, "failed": failed}


@shared_task(name="payments.dispatch_paid_notifications")
def dispatch_paid_notifications():
    payment_ids = list(
        Payment.objects.filter(
            state=PaymentState.SUCCEEDED,
            paid_notification_sent_at__isnull=True,
            order__channel=Channel.TELEGRAM,
        )
        .exclude(order__source_external_user_id_snapshot="")
        .values_list("pk", flat=True)[: settings.PAYMENT_SYNC_BATCH_SIZE]
    )
    for payment_id in payment_ids:
        transaction.on_commit(
            lambda current_id=payment_id: notify_payment_succeeded.delay(current_id)
        )
    return {"selected": len(payment_ids)}
