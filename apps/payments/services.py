"""Детерминированные операции оплаты ЮKassa и обработка её уведомлений."""
import hashlib
import ipaddress
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.common.enums import PaymentMethod, PaymentStatus
from apps.payments.exceptions import PaymentDataError, YooKassaAPIError
from apps.payments.models import (
    Payment,
    PaymentEnvironment,
    PaymentProvider,
    PaymentState,
    PaymentWebhookEvent,
    Refund,
    RefundState,
)
from apps.payments.yookassa.client import YooKassaClient

YOOKASSA_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "185.71.76.0/27",
        "185.71.77.0/27",
        "77.75.153.0/25",
        "77.75.154.128/25",
        "77.75.156.11/32",
        "77.75.156.35/32",
        "2a02:5180::/32",
    )
)
VALID_VAT_CODES = {1, 2, 3, 4, 5, 6}


def _money(value, *, field: str) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise PaymentDataError(f"ЮKassa вернула некорректное поле {field}") from exc
    if amount < 0:
        raise PaymentDataError(f"ЮKassa вернула отрицательное поле {field}")
    return amount


def _parse_date(value) -> datetime | None:
    if not value:
        return None
    parsed = parse_datetime(str(value))
    if parsed is None:
        return None
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed


def _payment_state(value: str) -> str:
    return {
        "pending": PaymentState.PENDING,
        "waiting_for_capture": PaymentState.WAITING_FOR_CAPTURE,
        "succeeded": PaymentState.SUCCEEDED,
        "canceled": PaymentState.CANCELED,
    }.get(value, PaymentState.FAILED)


def _refund_state(value: str) -> str:
    return {
        "pending": RefundState.PENDING,
        "succeeded": RefundState.SUCCEEDED,
        "canceled": RefundState.CANCELED,
    }.get(value, RefundState.FAILED)


def _receipt_for_order(order, *, payment_mode: str) -> dict:
    if settings.YOOKASSA_DEFAULT_VAT_CODE not in VALID_VAT_CODES:
        raise PaymentDataError("YOOKASSA_DEFAULT_VAT_CODE должен быть числом от 1 до 6")
    customer = {}
    if order.customer_email_snapshot:
        customer["email"] = order.customer_email_snapshot
    elif order.customer_phone_snapshot:
        customer["phone"] = f"+{order.customer_phone_snapshot}"
    else:
        raise PaymentDataError(
            "Для формирования чека нужен email или телефон покупателя"
        )

    items = []
    for item in order.items.all().order_by("id"):
        items.append(
            {
                "description": item.product_name_snapshot[:128],
                "quantity": str(item.quantity),
                "amount": {"value": f"{item.total_price:.2f}", "currency": "RUB"},
                "vat_code": settings.YOOKASSA_DEFAULT_VAT_CODE,
                "payment_mode": payment_mode,
                "payment_subject": "commodity",
            }
        )
    if order.delivery_cost:
        items.append(
            {
                "description": "Доставка",
                "quantity": "1",
                "amount": {"value": f"{order.delivery_cost:.2f}", "currency": "RUB"},
                "vat_code": settings.YOOKASSA_DEFAULT_VAT_CODE,
                "payment_mode": payment_mode,
                "payment_subject": "service",
            }
        )
    return {"customer": customer, "items": items}


def _payment_payload(payment: Payment) -> dict:
    return {
        "amount": {"value": f"{payment.amount:.2f}", "currency": payment.currency},
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": settings.YOOKASSA_RETURN_URL,
        },
        "description": payment.description,
        "metadata": {"order_public_number": payment.order.public_number},
        "receipt": payment.receipt_data,
    }


class PaymentService:
    """Создание ссылок, сверка платежа и возвраты с блокировками БД."""

    @classmethod
    def ensure_payment_link(
        cls,
        order,
        *,
        client: YooKassaClient | None = None,
    ) -> Payment:
        if order.payment_method != PaymentMethod.CARD_PREPAYMENT:
            raise PaymentDataError("Платёжная ссылка доступна только для онлайн-предоплаты")
        if order.payment_status == PaymentStatus.PAID:
            latest = order.payments.filter(state=PaymentState.SUCCEEDED).first()
            if latest is not None:
                return latest
            raise PaymentDataError("Заказ уже отмечен как оплаченный")

        api_client = client or YooKassaClient()
        api_client.config.validate()

        with transaction.atomic():
            locked_order = type(order).objects.select_for_update().get(pk=order.pk)
            payment = (
                Payment.objects.select_for_update()
                .filter(
                    order=locked_order,
                    provider=PaymentProvider.YOOKASSA,
                    state__in=[PaymentState.PENDING, PaymentState.WAITING_FOR_CAPTURE],
                )
                .order_by("-created_at")
                .first()
            )
            if payment is None:
                payment = Payment.objects.create(
                    order=locked_order,
                    environment=api_client.config.environment,
                    amount=locked_order.total_amount,
                    currency="RUB",
                    description=f"Оплата заказа {locked_order.public_number}",
                    receipt_data=_receipt_for_order(
                        locked_order,
                        payment_mode="full_prepayment",
                    ),
                )
            elif payment.external_id and payment.confirmation_url:
                return payment

        try:
            payload = api_client.create_payment(
                _payment_payload(payment), idempotence_key=str(payment.idempotence_key)
            )
        except YooKassaAPIError as exc:
            update = {"last_error": str(exc), "updated_at": timezone.now()}
            if not exc.retryable:
                update["state"] = PaymentState.FAILED
            Payment.objects.filter(pk=payment.pk).update(**update)
            raise
        return cls._apply_provider_payment(payment.pk, payload)

    @classmethod
    def _apply_provider_payment(cls, payment_id: int, payload: dict) -> Payment:
        external_id = str(payload.get("id", "")).strip()
        if not external_id:
            raise PaymentDataError("ЮKassa не вернула ID платежа")
        with transaction.atomic():
            payment = Payment.objects.select_for_update().select_related("order").get(
                pk=payment_id
            )
            cls._validate_provider_payment(payment, payload)
            confirmation = payload.get("confirmation")
            confirmation = confirmation if isinstance(confirmation, dict) else {}
            cancellation = payload.get("cancellation_details")
            cancellation = cancellation if isinstance(cancellation, dict) else {}
            payment.external_id = external_id
            payment.state = _payment_state(str(payload.get("status", "")))
            payment.confirmation_url = str(confirmation.get("confirmation_url", ""))
            payment.expires_at = _parse_date(payload.get("expires_at"))
            payment.paid_at = _parse_date(payload.get("captured_at"))
            payment.cancellation_code = str(cancellation.get("party_code", ""))
            payment.cancellation_description = str(cancellation.get("reason", ""))
            payment.provider_payload = payload
            payment.last_error = ""
            payment.save()
            cls._sync_order_payment_status(payment.order, payment.state)
            return payment

    @staticmethod
    def _validate_provider_payment(payment: Payment, payload: dict) -> None:
        amount = payload.get("amount")
        amount = amount if isinstance(amount, dict) else {}
        if _money(amount.get("value"), field="amount.value") != payment.amount:
            raise PaymentDataError("Сумма платежа ЮKassa не совпадает с заказом")
        if str(amount.get("currency", "")) != payment.currency:
            raise PaymentDataError("Валюта платежа ЮKassa не совпадает с заказом")
        metadata = payload.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        if metadata.get("order_public_number") != payment.order.public_number:
            raise PaymentDataError("Платёж ЮKassa связан с другим заказом")

    @staticmethod
    def _sync_order_payment_status(order, payment_state: str) -> None:
        target = {
            PaymentState.PENDING: PaymentStatus.WAITING,
            PaymentState.WAITING_FOR_CAPTURE: PaymentStatus.WAITING,
            PaymentState.SUCCEEDED: PaymentStatus.PAID,
            PaymentState.CANCELED: PaymentStatus.UNPAID,
            PaymentState.FAILED: PaymentStatus.UNPAID,
        }[payment_state]
        if order.payment_status != target:
            order.payment_status = target
            order.save(update_fields=["payment_status", "updated_at"])

    @classmethod
    def sync_payment(cls, payment: Payment, *, client: YooKassaClient | None = None) -> Payment:
        if not payment.external_id:
            raise PaymentDataError("У платежа нет внешнего ID ЮKassa")
        api_client = client or YooKassaClient()
        payload = api_client.get_payment(payment.external_id)
        return cls._apply_provider_payment(payment.pk, payload)

    @classmethod
    def cancel_payment(cls, payment: Payment, *, client: YooKassaClient | None = None) -> Payment:
        if payment.state not in {PaymentState.PENDING, PaymentState.WAITING_FOR_CAPTURE}:
            return payment
        if not payment.external_id:
            raise PaymentDataError("У платежа нет внешнего ID ЮKassa")
        api_client = client or YooKassaClient()
        payload = api_client.cancel_payment(
            payment.external_id, idempotence_key=str(payment.idempotence_key)
        )
        return cls._apply_provider_payment(payment.pk, payload)

    @classmethod
    def create_refund(
        cls,
        payment: Payment,
        *,
        amount: Decimal,
        reason: str = "",
        client: YooKassaClient | None = None,
    ) -> Refund:
        if payment.state != PaymentState.SUCCEEDED or not payment.external_id:
            raise PaymentDataError("Возврат возможен только для успешно оплаченного платежа")
        amount = _money(amount, field="refund.amount")
        if amount <= 0:
            raise PaymentDataError("Сумма возврата должна быть больше нуля")
        already_refunded = (
            payment.refunds.filter(state__in=[RefundState.PENDING, RefundState.SUCCEEDED])
            .aggregate(total=models.Sum("amount"))["total"]
            or Decimal("0")
        )
        if already_refunded + amount > payment.amount:
            raise PaymentDataError("Сумма возвратов превышает сумму платежа")
        refund = Refund.objects.create(
            payment=payment,
            amount=amount,
            currency=payment.currency,
            reason=reason[:256],
            receipt_data=_receipt_for_order(
                payment.order,
                payment_mode="full_payment",
            ),
        )
        api_client = client or YooKassaClient()
        payload = {
            "payment_id": payment.external_id,
            "amount": {"value": f"{amount:.2f}", "currency": payment.currency},
            "description": reason[:256] or f"Возврат по заказу {payment.order.public_number}",
            "receipt": refund.receipt_data,
        }
        try:
            response = api_client.create_refund(
                payload, idempotence_key=str(refund.idempotence_key)
            )
        except YooKassaAPIError as exc:
            refund.last_error = str(exc)
            if not exc.retryable:
                refund.state = RefundState.FAILED
            refund.save()
            raise
        external_id = str(response.get("id", "")).strip()
        if not external_id:
            raise PaymentDataError("ЮKassa не вернула ID возврата")
        refund.external_id = external_id
        refund.state = _refund_state(str(response.get("status", "")))
        refund.provider_payload = response
        refund.last_error = ""
        refund.save()
        return refund


class YooKassaWebhookService:
    """Защищённый приём webhook: сигнал сверяется с GET /payments/{id}."""

    @staticmethod
    def _fingerprint(payload: dict) -> str:
        obj = payload.get("object") if isinstance(payload.get("object"), dict) else {}
        source = {
            "event": payload.get("event", ""),
            "id": obj.get("id", ""),
            "status": obj.get("status", ""),
        }
        encoded = json.dumps(source, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _ip_is_allowed(remote_ip: str | None) -> bool:
        if not settings.YOOKASSA_VERIFY_WEBHOOK_IP:
            return True
        try:
            address = ipaddress.ip_address(remote_ip or "")
        except ValueError:
            return False
        return any(address in network for network in YOOKASSA_NETWORKS)

    @classmethod
    def process(
        cls,
        payload: dict,
        *,
        remote_ip: str | None,
        client: YooKassaClient | None = None,
    ) -> PaymentWebhookEvent:
        if not isinstance(payload, dict):
            raise PaymentDataError("Webhook ЮKassa должен быть JSON-объектом")
        event_type = str(payload.get("event", ""))
        obj = payload.get("object") if isinstance(payload.get("object"), dict) else {}
        external_id = str(obj.get("id", "")).strip()
        if not event_type.startswith("payment.") or not external_id:
            raise PaymentDataError("Webhook ЮKassa не содержит событие платежа")
        fingerprint = cls._fingerprint(payload)
        try:
            event, created = PaymentWebhookEvent.objects.get_or_create(
                fingerprint=fingerprint,
                defaults={
                    "provider": PaymentProvider.YOOKASSA,
                    "event_type": event_type,
                    "remote_ip": remote_ip if remote_ip else None,
                    "payload": payload,
                },
            )
        except IntegrityError:
            event = PaymentWebhookEvent.objects.get(fingerprint=fingerprint)
            created = False
        if not created and event.processed:
            return event
        if not cls._ip_is_allowed(remote_ip):
            event.processing_error = "Webhook получен с недопустимого IP"
            event.processed = True
            event.save(update_fields=["processing_error", "processed", "updated_at"])
            return event
        payment = Payment.objects.filter(
            provider=PaymentProvider.YOOKASSA, external_id=external_id
        ).select_related("order").first()
        if payment is None:
            event.processing_error = "Платёж ЮKassa не найден в CRM"
            event.processed = True
            event.save(update_fields=["processing_error", "processed", "updated_at"])
            return event
        api_client = client or YooKassaClient()
        provider_payment = api_client.get_payment(external_id)
        try:
            payment = PaymentService._apply_provider_payment(payment.pk, provider_payment)
        except PaymentDataError as exc:
            event.payment = payment
            event.processing_error = str(exc)
            event.processed = True
            event.save(
                update_fields=["payment", "processing_error", "processed", "updated_at"]
            )
            return event
        event.payment = payment
        event.verified = True
        event.processed = True
        event.processing_error = ""
        event.save(
            update_fields=[
                "payment",
                "verified",
                "processed",
                "processing_error",
                "updated_at",
            ]
        )
        return event
