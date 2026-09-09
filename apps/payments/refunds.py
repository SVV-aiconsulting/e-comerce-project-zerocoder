"""Reserve receipt quantities and retry the same provider operation."""
import copy
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.utils import timezone
from apps.payments.models import Payment, PaymentState, Refund, RefundState
from apps.payments.exceptions import PaymentDataError, YooKassaAPIError
from apps.payments.yookassa.client import YooKassaClient


def create_refund(payment, *, amount=None, lines=None, operation_id=None, reason="", client=None):
    from apps.payments.services import _receipt_for_order, _refund_state
    if lines is not None and operation_id is None:
        raise PaymentDataError("Для частичного возврата нужны позиции и уникальный ID операции")
    try:
        key = uuid.UUID(str(operation_id)) if operation_id else uuid.uuid5(
            uuid.NAMESPACE_URL, f"webmarket:full-refund:{payment.pk}"
        )
    except (ValueError, TypeError, AttributeError):
        raise PaymentDataError("Некорректный ID операции возврата") from None
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        if payment.state != PaymentState.SUCCEEDED or not payment.external_id:
            raise PaymentDataError("Возврат возможен только для оплаченного платежа")
        original = copy.deepcopy(payment.receipt_data)
        if not original.get("items"):
            original = _receipt_for_order(payment.order, payment_mode="full_prepayment")
        reserved = list(payment.refunds.filter(
            state__in=[RefundState.PENDING, RefundState.SUCCEEDED]
        ))
        if any(not row.allocations for row in reserved):
            raise PaymentDataError("Предыдущий возврат требует сверки состава менеджером")
        used = {}
        for row in reserved:
            for index, quantity in row.allocations.items():
                used[index] = used.get(index, Decimal(0)) + Decimal(quantity)
        existing = Refund.objects.filter(idempotence_key=key).first()
        if existing is not None and lines is None:
            requested = dict(existing.allocations)
        elif lines is None:
            requested = {}
            for index, item in enumerate(original["items"]):
                remaining = Decimal(item["quantity"]) - used.get(str(index), Decimal(0))
                if remaining > 0:
                    requested[str(index)] = str(remaining)
        else:
            requested = {str(k): str(v) for k, v in lines.items()}
        if existing:
            if existing.payment_id != payment.pk or existing.allocations != requested:
                raise PaymentDataError("ID возврата уже использован с другими параметрами")
            refund = existing
        else:
            receipt = copy.deepcopy(original)
            receipt["items"] = []
            total = Decimal(0)
            if not requested:
                raise PaymentDataError("Выберите позиции для возврата")
            for index, value in requested.items():
                try:
                    n = int(index)
                    if str(n) != index or n < 0:
                        raise ValueError()
                    item = copy.deepcopy(original["items"][n])
                    quantity = Decimal(value)
                    if not quantity.is_finite() or quantity <= 0 or quantity.as_tuple().exponent < -3:
                        raise ValueError()
                    if quantity + used.get(index, Decimal(0)) > Decimal(item["quantity"]):
                        raise ValueError()
                    if item.get("measure") == "piece" and quantity != quantity.to_integral_value():
                        raise ValueError()
                except (ValueError, IndexError, InvalidOperation):
                    raise PaymentDataError("Недопустимое количество возвращаемой позиции") from None
                item["quantity"] = str(quantity)
                line = quantity * Decimal(item["amount"]["value"])
                if line != line.quantize(Decimal("0.01")):
                    raise PaymentDataError("Сумма позиции требует уточнения количества для точности до копейки")
                total += line
                receipt["items"].append(item)
            if total <= 0 or total + sum((r.amount for r in reserved), Decimal(0)) > payment.amount:
                raise PaymentDataError("Сумма возвратов превышает оплату")
            if amount is not None:
                try:
                    supplied_amount = Decimal(str(amount)).quantize(Decimal("0.01"))
                except (InvalidOperation, ValueError):
                    raise PaymentDataError("Некорректная сумма возврата") from None
                if supplied_amount != total:
                    raise PaymentDataError("Сумма не соответствует возвращаемым позициям")
            refund = Refund.objects.create(payment=payment, idempotence_key=key, allocations=requested,
                amount=total, currency=payment.currency, reason=reason[:256], receipt_data=receipt)
    if refund.state in (RefundState.SUCCEEDED, RefundState.CANCELED, RefundState.FAILED):
        return refund
    api = client or YooKassaClient()
    try:
        if refund.external_id:
            response = api.get_refund(refund.external_id)
        else:
            if refund.created_at < timezone.now() - timedelta(hours=23):
                raise PaymentDataError("Неопределённый возврат старше окна идемпотентности: нужна сверка менеджером")
            response = api.create_refund({"payment_id": payment.external_id,
                "amount": {"value": f"{refund.amount:.2f}", "currency": refund.currency},
                "description": refund.reason or f"Возврат заказа {payment.order.public_number}",
                "receipt": refund.receipt_data}, idempotence_key=str(refund.idempotence_key))
    except YooKassaAPIError as exc:
        Refund.objects.filter(pk=refund.pk).update(last_error=str(exc), **({} if exc.retryable else {"state":RefundState.FAILED}))
        raise
    if not response.get("id"):
        raise PaymentDataError("Провайдер не вернул ID возврата")
    if response.get("payment_id") and response["payment_id"] != payment.external_id:
        raise PaymentDataError("Возврат относится к другому платежу")
    if response.get("amount") and (Decimal(response["amount"]["value"]) != refund.amount or response["amount"]["currency"] != refund.currency):
        raise PaymentDataError("Сумма возврата провайдера не совпадает")
    with transaction.atomic():
        refund = Refund.objects.select_for_update().get(pk=refund.pk)
        if refund.state != RefundState.SUCCEEDED:
            refund.external_id = response["id"]
            refund.state = _refund_state(response.get("status", ""))
            refund.provider_payload = response
            refund.last_error = ""
            refund.save()
    return refund
