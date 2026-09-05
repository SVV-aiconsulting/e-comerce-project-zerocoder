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


def _recipient_from_values(name: str, phone: str, email: str = "") -> dict:
    if not phone:
        raise DeliveryDataIncompleteError(
            "Для создания доставки нужен телефон получателя"
        )
    parts = (name or "").split()
    recipient = {
        "first_name": parts[0] if parts else "Клиент",
        "phone": f"+{phone}",
    }
    if len(parts) > 1:
        recipient["last_name"] = parts[1]
    if len(parts) > 2:
        recipient["patronymic"] = " ".join(parts[2:])
    if email:
        recipient["email"] = email
    return recipient


def _recipient(order) -> dict:
    return _recipient_from_values(
        order.customer_name_snapshot,
        order.customer_phone_snapshot,
        order.customer_email_snapshot,
    )


def _billing_info_for_payment_method(payment_method: str, delivery_cost=Decimal("0")) -> dict:
    if payment_method == PaymentMethod.CARD_PREPAYMENT:
        return {"payment_method": "already_paid", "delivery_cost": 0}
    if payment_method == PaymentMethod.CARD_ON_DELIVERY:
        return {
            "payment_method": "card_on_receipt",
            "delivery_cost": rubles_to_kopecks(delivery_cost),
        }
    raise DeliveryDataIncompleteError(
        "Яндекс Доставка по России не принимает наличные: выберите онлайн-оплату "
        "или оплату картой при получении"
    )


def _billing_info(order) -> dict:
    return _billing_info_for_payment_method(order.payment_method, order.delivery_cost)


def _offer_items_from_lines(lines, *, place_barcode: str) -> list[dict]:
    vat_code = settings.YANDEX_DELIVERY_VAT_CODE
    if vat_code not in VALID_VAT_CODES:
        raise DeliveryDataIncompleteError("Некорректный YANDEX_DELIVERY_VAT_CODE")
    result = []
    for product, quantity, product_name, unit_price in lines:
        if not product.has_delivery_dimensions:
            raise DeliveryDataIncompleteError(
                f"Не заполнены вес и габариты товара: {product.name}"
            )
        quantity = Decimal(quantity)
        is_whole = quantity == quantity.to_integral_value()
        count = int(quantity) if is_whole else 1
        billed_unit_price = unit_price if is_whole else unit_price * quantity
        billing_details = {
            "unit_price": rubles_to_kopecks(billed_unit_price),
            "assessed_unit_price": rubles_to_kopecks(billed_unit_price),
            "nds": vat_code,
        }
        if settings.YANDEX_DELIVERY_MERCHANT_INN:
            billing_details["inn"] = settings.YANDEX_DELIVERY_MERCHANT_INN
        result.append(
            {
                "count": count,
                "name": product_name,
                "article": product.public_code,
                "billing_details": billing_details,
                "physical_dims": {
                    "dx": product.delivery_length_cm,
                    "dy": product.delivery_width_cm,
                    "dz": product.delivery_height_cm,
                },
                "place_barcode": place_barcode,
                "fitting": False,
                "refused_count": 0,
            }
        )
    return result


def _offer_items(order, *, place_barcode: str) -> list[dict]:
    return _offer_items_from_lines(
        (
            (item.product, item.quantity, item.product_name_snapshot, item.unit_price)
            for item in order.items.select_related("product").all()
        ),
        place_barcode=place_barcode,
    )


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

    place_barcode = f"{order.public_number}-1"
    payload = {
        "info": {
            "operator_request_id": order.public_number,
            "comment": order.customer_comment,
        },
        "source": {"platform_station": {"platform_id": station_id}},
        "destination": destination,
        "items": _offer_items(order, place_barcode=place_barcode),
        "places": [{"physical_dims": package.as_yandex_payload(), "barcode": place_barcode}],
        "billing_info": _billing_info(order),
        "recipient_info": _recipient(order),
        "last_mile_policy": LastMilePolicy.TIME_INTERVAL,
        "particular_items_refuse": False,
        "forbid_unboxing": False,
    }
    return payload, asdict(package)


def build_draft_offer_payload(draft, station_id: str) -> tuple[dict, dict, str]:
    """Собрать оффер из AI-черновика без создания CRM-заказа."""
    if not draft.delivery_address.strip():
        raise DeliveryDataIncompleteError("Не указан адрес доставки")
    package = DeliveryPackageService.from_draft(draft)
    operator_request_id = f"draft-{draft.public_id.hex}-r{draft.revision}"
    place_barcode = f"{operator_request_id}-1"
    customer_name = draft.customer.name if draft.customer_id else "Клиент"
    lines = (
        (item.product, item.requested_quantity, item.product.name, item.product.base_price)
        for item in draft.items.select_related("product").order_by("line_number")
    )
    payload = {
        "info": {"operator_request_id": operator_request_id, "comment": draft.customer_comment},
        "source": {"platform_station": {"platform_id": station_id}},
        "destination": {
            "type": "custom_location",
            "custom_location": {
                "details": {"full_address": " ".join(draft.delivery_address.split())}
            },
        },
        "items": _offer_items_from_lines(lines, place_barcode=place_barcode),
        "places": [{"physical_dims": package.as_yandex_payload(), "barcode": place_barcode}],
        "billing_info": _billing_info_for_payment_method(draft.payment_method),
        "recipient_info": _recipient_from_values(
            customer_name, draft.contact_phone, draft.contact_email
        ),
        "last_mile_policy": LastMilePolicy.TIME_INTERVAL,
        "particular_items_refuse": False,
        "forbid_unboxing": False,
    }
    return payload, asdict(package), operator_request_id


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
    def create_for_draft(
        cls,
        draft,
        *,
        client: YandexDeliveryClient | None = None,
    ) -> DeliveryQuote:
        """Получить реальный оффер до подтверждения AI-черновика.

        Метод Яндекса не бронирует и не подтверждает доставку. Сохраняется только
        самый ранний оффер: именно его стоимость и срок видит клиент в preview.
        """
        api_client = client or YandexDeliveryClient()
        config = api_client.config
        config.validate()
        payload, package_snapshot, operator_request_id = build_draft_offer_payload(
            draft, config.station_id
        )
        fingerprint = request_fingerprint(payload)
        try:
            response = api_client.create_offers(payload)
        except YandexDeliveryAPIError as exc:
            quote = DeliveryQuote.objects.create(
                order_draft=draft,
                environment=config.environment,
                kind=DeliveryQuoteKind.OFFER,
                status=DeliveryQuoteStatus.FAILED,
                request_fingerprint=fingerprint,
                operator_request_id=operator_request_id,
                destination_address=draft.delivery_address,
                package_snapshot=package_snapshot,
                request_payload=payload,
                response_payload=exc.response_payload,
                error_code=exc.code,
                error_message=str(exc),
            )
            DeliverySyncEvent.objects.create(
                quote=quote,
                operation=DeliveryOperation.OFFERS_CREATE,
                succeeded=False,
                http_status=exc.status_code,
                request_payload=payload,
                response_payload=exc.response_payload,
                error_code=exc.code,
                error_message=str(exc),
            )
            return quote

        offers = response.get("offers")
        if not isinstance(offers, list) or not offers:
            raise YandexDeliveryAPIError(
                "Яндекс Доставка не вернула доступных офферов",
                response_payload=response,
            )
        offer = min(
            offers,
            key=lambda value: _parse_timestamp(
                (value.get("offer_details") or {}).get("delivery_interval", {}).get("min")
            )
            or datetime.max.replace(tzinfo=dt_timezone.utc),
        )
        external_offer_id = str(offer.get("offer_id", "")).strip()
        if not external_offer_id:
            raise YandexDeliveryAPIError(
                "Яндекс Доставка вернула оффер без идентификатора",
                response_payload=offer,
            )
        details = offer.get("offer_details") or {}
        amount, currency = parse_money(str(details.get("pricing_total", "")))
        delivery_interval = details.get("delivery_interval") or {}
        pickup_interval = details.get("pickup_interval") or {}
        delivery_from = _parse_timestamp(delivery_interval.get("min"))
        delivery_days = None
        if delivery_from is not None:
            delivery_days = max(0, (delivery_from.date() - timezone.localdate()).days)
        quote = DeliveryQuote.objects.create(
            order_draft=draft,
            environment=config.environment,
            kind=DeliveryQuoteKind.OFFER,
            status=DeliveryQuoteStatus.SUCCEEDED,
            request_fingerprint=fingerprint,
            operator_request_id=operator_request_id,
            external_offer_id=external_offer_id,
            last_mile_policy=str(
                delivery_interval.get("policy", LastMilePolicy.TIME_INTERVAL)
            ),
            destination_address=draft.delivery_address,
            package_snapshot=package_snapshot,
            amount=amount,
            currency=currency,
            delivery_days=delivery_days,
            expires_at=_parse_timestamp(offer.get("expires_at")),
            delivery_from=delivery_from,
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
        return quote

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
