"""Бронирование, синхронизация и отмена отправлений Яндекс Доставки."""

from django.utils import timezone

from apps.common.enums import PaymentMethod, PaymentStatus
from apps.delivery.exceptions import DeliveryDataIncompleteError, YandexDeliveryAPIError
from apps.delivery.models import (
    DeliveryOperation,
    DeliveryQuoteKind,
    DeliveryQuoteStatus,
    DeliverySyncEvent,
    Shipment,
    ShipmentStatus,
)
from apps.delivery.yandex.client import YandexDeliveryClient


def normalize_yandex_status(external_status: str) -> str:
    """Консервативно свести расширяемую статусную модель Яндекса к CRM."""

    status = (external_status or "").upper()
    if "CANCEL" in status or "RETURNED" in status:
        return ShipmentStatus.CANCELLED
    if any(marker in status for marker in ("DELIVERED", "RECEIVED", "FINISHED")):
        return ShipmentStatus.DELIVERED
    if any(
        marker in status
        for marker in ("TRANSPORTATION", "DELIVERY", "COURIER", "PICKUP")
    ):
        return ShipmentStatus.IN_TRANSIT
    if status:
        return ShipmentStatus.CONFIRMED
    return ShipmentStatus.FAILED


class YandexShipmentService:
    @classmethod
    def confirm_quote(
        cls,
        quote,
        *,
        client: YandexDeliveryClient | None = None,
    ) -> Shipment:
        if quote.kind != DeliveryQuoteKind.OFFER:
            raise DeliveryDataIncompleteError(
                "Подтвердить можно только оффер, предварительная оценка не бронируется"
            )
        if quote.status != DeliveryQuoteStatus.SUCCEEDED or not quote.external_offer_id:
            raise DeliveryDataIncompleteError("Оффер не готов к подтверждению")
        if quote.order_id is None:
            raise DeliveryDataIncompleteError("Оффер ещё не связан с финальным заказом")
        if (
            quote.order.payment_method == PaymentMethod.CARD_PREPAYMENT
            and quote.order.payment_status != PaymentStatus.PAID
        ):
            raise DeliveryDataIncompleteError(
                "Оффер с онлайн-предоплатой подтверждается только после webhook оплаты"
            )
        if quote.expires_at and quote.expires_at <= timezone.now():
            quote.status = DeliveryQuoteStatus.EXPIRED
            quote.save(update_fields=["status", "updated_at"])
            raise DeliveryDataIncompleteError("Срок действия оффера истёк")

        shipment, _ = Shipment.objects.get_or_create(
            order=quote.order,
            defaults={
                "quote": quote,
                "environment": quote.environment,
                "amount": quote.amount,
                "currency": quote.currency,
            },
        )
        shipment.quote = quote
        shipment.status = ShipmentStatus.CONFIRMING
        shipment.last_error = ""
        shipment.save(update_fields=["quote", "status", "last_error", "updated_at"])

        api_client = client or YandexDeliveryClient()
        request_payload = {"offer_id": quote.external_offer_id}
        try:
            request_id = api_client.confirm_offer(quote.external_offer_id)
        except YandexDeliveryAPIError as exc:
            shipment.status = ShipmentStatus.FAILED
            shipment.last_error = str(exc)
            shipment.save(update_fields=["status", "last_error", "updated_at"])
            DeliverySyncEvent.objects.create(
                shipment=shipment,
                quote=quote,
                operation=DeliveryOperation.OFFER_CONFIRM,
                succeeded=False,
                http_status=exc.status_code,
                request_payload=request_payload,
                response_payload=exc.response_payload,
                error_code=exc.code,
                error_message=str(exc),
            )
            raise

        response_payload = {"request_id": request_id}
        shipment.external_request_id = request_id
        shipment.status = ShipmentStatus.CONFIRMED
        shipment.external_status = "CREATED"
        shipment.provider_payload = response_payload
        shipment.last_synced_at = timezone.now()
        shipment.save(
            update_fields=[
                "external_request_id",
                "status",
                "external_status",
                "provider_payload",
                "last_synced_at",
                "updated_at",
            ]
        )
        quote.status = DeliveryQuoteStatus.SELECTED
        quote.save(update_fields=["status", "updated_at"])
        DeliverySyncEvent.objects.create(
            shipment=shipment,
            quote=quote,
            operation=DeliveryOperation.OFFER_CONFIRM,
            succeeded=True,
            http_status=200,
            request_payload=request_payload,
            response_payload=response_payload,
        )
        return shipment

    @classmethod
    def sync(
        cls,
        shipment: Shipment,
        *,
        client: YandexDeliveryClient | None = None,
    ) -> Shipment:
        if not shipment.external_request_id:
            raise DeliveryDataIncompleteError("У доставки ещё нет ID заявки Яндекса")
        api_client = client or YandexDeliveryClient()
        request_payload = {"request_id": shipment.external_request_id, "slim": True}
        try:
            response = api_client.get_request_info(shipment.external_request_id)
        except YandexDeliveryAPIError as exc:
            shipment.last_error = str(exc)
            shipment.save(update_fields=["last_error", "updated_at"])
            DeliverySyncEvent.objects.create(
                shipment=shipment,
                operation=DeliveryOperation.INFO,
                succeeded=False,
                http_status=exc.status_code,
                request_payload=request_payload,
                response_payload=exc.response_payload,
                error_code=exc.code,
                error_message=str(exc),
            )
            raise

        state = response.get("state") if isinstance(response.get("state"), dict) else {}
        external_status = str(state.get("status", ""))
        shipment.external_status = external_status
        shipment.status = normalize_yandex_status(external_status)
        shipment.tracking_url = str(response.get("sharing_url", ""))
        shipment.provider_payload = response
        shipment.last_error = ""
        shipment.last_synced_at = timezone.now()
        shipment.save(
            update_fields=[
                "external_status",
                "status",
                "tracking_url",
                "provider_payload",
                "last_error",
                "last_synced_at",
                "updated_at",
            ]
        )
        DeliverySyncEvent.objects.create(
            shipment=shipment,
            operation=DeliveryOperation.INFO,
            succeeded=True,
            http_status=200,
            request_payload=request_payload,
            response_payload=response,
        )
        return shipment

    @classmethod
    def cancel(
        cls,
        shipment: Shipment,
        *,
        client: YandexDeliveryClient | None = None,
    ) -> Shipment:
        if not shipment.external_request_id:
            raise DeliveryDataIncompleteError("У доставки ещё нет ID заявки Яндекса")
        if shipment.status == ShipmentStatus.CANCELLED:
            return shipment
        api_client = client or YandexDeliveryClient()
        request_payload = {"request_id": shipment.external_request_id}
        try:
            response = api_client.cancel_request(shipment.external_request_id)
        except YandexDeliveryAPIError as exc:
            shipment.last_error = str(exc)
            shipment.save(update_fields=["last_error", "updated_at"])
            DeliverySyncEvent.objects.create(
                shipment=shipment,
                operation=DeliveryOperation.CANCEL,
                succeeded=False,
                http_status=exc.status_code,
                request_payload=request_payload,
                response_payload=exc.response_payload,
                error_code=exc.code,
                error_message=str(exc),
            )
            raise

        cancellation_status = str(response.get("status", "")).upper()
        shipment.status = (
            ShipmentStatus.CANCELLED
            if cancellation_status == "SUCCESS"
            else ShipmentStatus.CANCELLING
        )
        shipment.external_status = f"CANCEL_{cancellation_status or 'UNKNOWN'}"
        shipment.provider_payload = response
        shipment.last_error = "" if cancellation_status != "ERROR" else str(
            response.get("description", "Ошибка отмены")
        )
        shipment.last_synced_at = timezone.now()
        shipment.save(
            update_fields=[
                "status",
                "external_status",
                "provider_payload",
                "last_error",
                "last_synced_at",
                "updated_at",
            ]
        )
        DeliverySyncEvent.objects.create(
            shipment=shipment,
            operation=DeliveryOperation.CANCEL,
            succeeded=cancellation_status in {"CREATED", "SUCCESS"},
            http_status=200,
            request_payload=request_payload,
            response_payload=response,
            error_code="" if cancellation_status != "ERROR" else "cancel_error",
            error_message=shipment.last_error,
        )
        return shipment
