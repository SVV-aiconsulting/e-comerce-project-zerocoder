"""Формирование и сохранение бронируемых офферов Яндекс Доставки."""

from dataclasses import asdict
from datetime import datetime, time, timezone as dt_timezone
from decimal import Decimal

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.common.enums import PaymentMethod
from apps.delivery.exceptions import DeliveryDataIncompleteError, YandexDeliveryAPIError
from apps.delivery.models import (
    DeliveryOperation,
    DeliveryQuote,
    DeliveryQuoteKind,
    DeliveryQuoteStatus,
    DeliverySyncEvent,
    LastMilePolicy,
)
from apps.delivery.quote_service import (
    DeliveryPackageService,
    request_fingerprint,
    rubles_to_kopecks,
)
from apps.delivery.yandex.client import YandexDeliveryClient, parse_money

VALID_VAT_CODES = {-1, 0, 5, 7, 10, 22}


def _to_utc_iso(value: datetime) -> str:
    return value.astimezone(dt_timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _delivery_interval(order) -> dict | None:
    if not order.desired_date or not order.desired_time_interval:
        return None
    start_hour, end_hour = (
        int(part) for part in order.desired_time_interval.split("-", maxsplit=1)
    )
    current_timezone = timezone.get_current_timezone()
    start = timezone.make_aware(
        datetime.combine(order.desired_date, time(hour=start_hour)),
        current_timezone,
    )
    end = timezone.make_aware(
        datetime.combine(order.desired_date, time(hour=end_hour)),
        current_timezone,
    )
    return {"from": _to_utc_iso(start), "to": _to_utc_iso(end)}


def _recipient(order) -> dict:
    if not order.customer_phone_snapshot:
        raise DeliveryDataIncompleteError(
            "Для создания доставки нужен телефон получателя"
        )
    parts = order.customer_name_snapshot.split()
    recipient = {
        "first_name": parts[0] if parts else "Клиент",
        "phone": f"+{order.customer_phone_snapshot}",
    }
    if len(parts) > 1:
        recipient["last_name"] = parts[1]
    if len(parts) > 2:
        recipient["patronymic"] = " ".join(parts[2:])
    if order.customer_email_snapshot:
        recipient["email"] = order.customer_email_snapshot
    return recipient


def _billing_info(order) -> dict:
    if order.payment_method == PaymentMethod.CARD_PREPAYMENT:
        return {"payment_method": "already_paid", "delivery_cost": 0}
    if order.payment_method == PaymentMethod.CARD_ON_DELIVERY:
        return {
            "payment_method": "card_on_receipt",
            "delivery_cost": rubles_to_kopecks(order.delivery_cost),
        }
    raise DeliveryDataIncompleteError(
        "Яндекс Доставка по России не принимает наличные: выберите онлайн-оплату "
        "или оплату картой при получении"
    )


def _offer_items(order) -> list[dict]:
    vat_code = settings.YANDEX_DELIVERY_VAT_CODE
    if vat_code not in VALID_VAT_CODES:
        raise DeliveryDataIncompleteError("Некорректный YANDEX_DELIVERY_VAT_CODE")
    result = []
    for order_item in order.items.select_related("product").all():
        product = order_item.product
        if not product.has_delivery_dimensions:
            raise DeliveryDataIncompleteError(
                f"Не заполнены вес и габариты товара: {product.name}"
            )
        quantity = Decimal(order_item.quantity)
        is_whole = quantity == quantity.to_integral_value()
        count = int(quantity) if is_whole else 1
        unit_price = (
            order_item.unit_price if is_whole else order_item.total_price
        )
        billing_details = {
            "unit_price": rubles_to_kopecks(unit_price),
            "assessed_unit_price": rubles_to_kopecks(unit_price),
            "nds": vat_code,
        }
        if settings.YANDEX_DELIVERY_MERCHANT_INN:
            billing_details["inn"] = settings.YANDEX_DELIVERY_MERCHANT_INN
        result.append(
            {
                "count": count,
                "name": order_item.product_name_snapshot,
                "article": product.public_code,
                "billing_details": billing_details,
                "physical_dims": {
                    "dx": product.delivery_length_cm,
                    "dy": product.delivery_width_cm,
                    "dz": product.delivery_height_cm,
                },
                "fitting": False,
                "refused_count": 0,
            }
        )
    return result


def build_offer_payload(order, station_id: str) -> tuple[dict, dict]:
    if not order.delivery_address.strip():
        raise DeliveryDataIncompleteError("Не указан адрес доставки")
    package = DeliveryPackageService.from_order(order)
    destination = {
        "type": "custom_location",
        "custom_location": {
            "details": {"full_address": " ".join(order.delivery_address.split())}
        },
    }
    interval = _delivery_interval(order)
    if interval:
        destination["interval_utc"] = interval

    payload = {
        "info": {
            "operator_request_id": order.public_number,
            "comment": order.customer_comment,
        },
        "source": {"platform_station": {"platform_id": station_id}},
        "destination": destination,
        "items": _offer_items(order),
        "places": [{"physical_dims": package.as_yandex_payload()}],
        "billing_info": _billing_info(order),
        "recipient_info": _recipient(order),
        "last_mile_policy": LastMilePolicy.TIME_INTERVAL,
        "particular_items_refuse": False,
        "forbid_unboxing": False,
    }
    return payload, asdict(package)


def _parse_timestamp(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=dt_timezone.utc)
    parsed = parse_datetime(str(value))
    if parsed is None:
        return None
    return (
        timezone.make_aware(parsed, dt_timezone.utc)
        if timezone.is_naive(parsed)
        else parsed
    )


class YandexDeliveryOfferService:
    @classmethod
    def create_for_order(
        cls,
        order,
        *,
        client: YandexDeliveryClient | None = None,
    ) -> list[DeliveryQuote]:
        api_client = client or YandexDeliveryClient()
        config = api_client.config
        config.validate()
        payload, package_snapshot = build_offer_payload(order, config.station_id)
        fingerprint = request_fingerprint(payload)
        try:
            response = api_client.create_offers(payload)
        except YandexDeliveryAPIError as exc:
            failed = DeliveryQuote.objects.create(
                order=order,
                environment=config.environment,
                kind=DeliveryQuoteKind.OFFER,
                status=DeliveryQuoteStatus.FAILED,
                request_fingerprint=fingerprint,
                operator_request_id=order.public_number,
                destination_address=order.delivery_address,
                package_snapshot=package_snapshot,
                request_payload=payload,
                response_payload=exc.response_payload,
                error_code=exc.code,
                error_message=str(exc),
            )
            DeliverySyncEvent.objects.create(
                quote=failed,
                operation=DeliveryOperation.OFFERS_CREATE,
                succeeded=False,
                http_status=exc.status_code,
                request_payload=payload,
                response_payload=exc.response_payload,
                error_code=exc.code,
                error_message=str(exc),
            )
            return [failed]

        offers = response.get("offers")
        if not isinstance(offers, list) or not offers:
            raise YandexDeliveryAPIError(
                "Яндекс Доставка не вернула доступных офферов",
                response_payload=response,
            )

        quotes = []
        for offer in offers:
            external_offer_id = str(offer.get("offer_id", "")).strip()
            if not external_offer_id:
                raise YandexDeliveryAPIError(
                    "Яндекс Доставка вернула оффер без идентификатора",
                    response_payload=offer,
                )
            details = offer.get("offer_details", {})
            amount, currency = parse_money(str(details.get("pricing_total", "")))
            delivery_interval = details.get("delivery_interval") or {}
            pickup_interval = details.get("pickup_interval") or {}
            quote = DeliveryQuote.objects.create(
                order=order,
                environment=config.environment,
                kind=DeliveryQuoteKind.OFFER,
                status=DeliveryQuoteStatus.SUCCEEDED,
                request_fingerprint=fingerprint,
                operator_request_id=order.public_number,
                external_offer_id=external_offer_id,
                last_mile_policy=str(
                    delivery_interval.get("policy", LastMilePolicy.TIME_INTERVAL)
                ),
                destination_address=order.delivery_address,
                package_snapshot=package_snapshot,
                amount=amount,
                currency=currency,
                expires_at=_parse_timestamp(offer.get("expires_at")),
                delivery_from=_parse_timestamp(delivery_interval.get("min")),
                delivery_to=_parse_timestamp(delivery_interval.get("max")),
                pickup_from=_parse_timestamp(pickup_interval.get("min")),
                pickup_to=_parse_timestamp(pickup_interval.get("max")),
                request_payload=payload,
                response_payload=offer,
            )
            DeliverySyncEvent.objects.create(
                quote=quote,
                operation=DeliveryOperation.OFFERS_CREATE,
                succeeded=True,
                http_status=200,
                request_payload=payload,
                response_payload=offer,
            )
            quotes.append(quote)
        return quotes
