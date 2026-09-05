"""Детерминированная подготовка и аудит расчётов Яндекс Доставки."""

from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
from typing import Iterable

from django.db import transaction

from apps.common.enums import PaymentMethod
from apps.carts.services import CartService
from apps.delivery.exceptions import (
    DeliveryDataIncompleteError,
    YandexDeliveryAPIError,
)
from apps.delivery.models import (
    DeliveryOperation,
    DeliveryEnvironment,
    DeliveryQuote,
    DeliveryQuoteKind,
    DeliveryQuoteStatus,
    DeliverySyncEvent,
    LastMilePolicy,
)
from apps.delivery.yandex.client import YandexDeliveryClient


@dataclass(frozen=True)
class DeliveryPackage:
    weight_gross: int
    length_cm: int
    width_cm: int
    height_cm: int

    def as_yandex_payload(self) -> dict:
        return {
            "weight_gross": self.weight_gross,
            "dx": self.length_cm,
            "dy": self.width_cm,
            "dz": self.height_cm,
        }


@dataclass(frozen=True)
class DeliveryLine:
    product: object
    quantity: Decimal


class DeliveryPackageService:
    """Строит одно консервативное грузоместо для MVP."""

    @staticmethod
    def _ceil(value: Decimal) -> int:
        return int(value.to_integral_value(rounding=ROUND_CEILING))

    @classmethod
    def build(cls, lines: Iterable[DeliveryLine]) -> DeliveryPackage:
        total_weight = Decimal("0")
        max_length = 0
        max_width = 0
        stacked_height = Decimal("0")
        missing_products: list[str] = []
        has_lines = False

        for line in lines:
            has_lines = True
            product = line.product
            quantity = Decimal(line.quantity)
            if quantity <= 0:
                raise DeliveryDataIncompleteError(
                    f"Некорректное количество товара {product.name}"
                )
            if not product.has_delivery_dimensions:
                missing_products.append(product.name)
                continue

            total_weight += Decimal(product.delivery_weight_grams) * quantity
            max_length = max(max_length, product.delivery_length_cm)
            max_width = max(max_width, product.delivery_width_cm)
            stacked_height += Decimal(product.delivery_height_cm) * quantity

        if not has_lines:
            raise DeliveryDataIncompleteError("В заказе нет товаров для доставки")
        if missing_products:
            names = ", ".join(sorted(set(missing_products)))
            raise DeliveryDataIncompleteError(
                f"Не заполнены вес и габариты товаров: {names}"
            )

        return DeliveryPackage(
            weight_gross=max(1, cls._ceil(total_weight)),
            length_cm=max_length,
            width_cm=max_width,
            height_cm=max(1, cls._ceil(stacked_height)),
        )

    @classmethod
    def from_order(cls, order) -> DeliveryPackage:
        return cls.build(
            DeliveryLine(product=item.product, quantity=item.quantity)
            for item in order.items.select_related("product").all()
        )

    @classmethod
    def from_draft(cls, draft) -> DeliveryPackage:
        lines = []
        for item in draft.items.select_related("product").all():
            if item.product_id is None or item.requested_quantity is None:
                raise DeliveryDataIncompleteError(
                    f"Позиция «{item.raw_product_name}» ещё не сопоставлена с каталогом"
                )
            lines.append(
                DeliveryLine(product=item.product, quantity=item.requested_quantity)
            )
        return cls.build(lines)


def rubles_to_kopecks(value: Decimal) -> int:
    return int((Decimal(value) * 100).quantize(Decimal("1")))


def build_pricing_payload(
    *,
    station_id: str,
    destination_address: str = "",
    destination_station_id: str = "",
    package: DeliveryPackage,
    items_total: Decimal,
    payment_method: str,
    last_mile_policy: str = LastMilePolicy.TIME_INTERVAL,
) -> dict:
    address = " ".join((destination_address or "").split())
    destination_station_id = (destination_station_id or "").strip()
    if last_mile_policy == LastMilePolicy.TIME_INTERVAL and not address:
        raise DeliveryDataIncompleteError("Не указан адрес доставки")
    if last_mile_policy == LastMilePolicy.SELF_PICKUP and not destination_station_id:
        raise DeliveryDataIncompleteError(
            "Предварительный расчёт ПВЗ требует platform_station_id выбранного ПВЗ"
        )
    if last_mile_policy not in LastMilePolicy.values:
        raise DeliveryDataIncompleteError("Неизвестный способ доставки последней мили")

    if payment_method == PaymentMethod.CARD_ON_DELIVERY:
        yandex_payment_method = "card_on_receipt"
        client_price = rubles_to_kopecks(items_total)
    elif payment_method == PaymentMethod.CARD_PREPAYMENT:
        yandex_payment_method = "already_paid"
        client_price = 0
    else:
        raise DeliveryDataIncompleteError(
            "Наложенный платёж наличными будет поддержан на этапе создания оффера; "
            "для предварительного расчёта выберите онлайн-оплату или карту при получении"
        )

    return {
        "source": {"platform_station_id": station_id},
        "destination": (
            {"address": address}
            if last_mile_policy == LastMilePolicy.TIME_INTERVAL
            else {"platform_station_id": destination_station_id}
        ),
        "tariff": last_mile_policy,
        "total_weight": package.weight_gross,
        "total_assessed_price": rubles_to_kopecks(items_total),
        "client_price": client_price,
        "payment_method": yandex_payment_method,
        "places": [{"physical_dims": package.as_yandex_payload()}],
    }


def request_fingerprint(payload: dict) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class YandexDeliveryQuoteService:
    """Создаёт предварительный расчёт и сохраняет полный технический аудит."""

    @classmethod
    def quote_order(
        cls,
        order,
        *,
        client: YandexDeliveryClient | None = None,
    ) -> DeliveryQuote:
        package = DeliveryPackageService.from_order(order)
        return cls._quote(
            cart=None,
            order=order,
            order_draft=None,
            destination_address=order.delivery_address,
            package=package,
            items_total=order.items_total,
            payment_method=order.payment_method,
            client=client,
        )

    @classmethod
    def quote_draft(
        cls,
        draft,
        *,
        client: YandexDeliveryClient | None = None,
    ) -> DeliveryQuote:
        package = DeliveryPackageService.from_draft(draft)
        items_total = sum(
            (
                item.product.base_price * item.requested_quantity
                for item in draft.items.select_related("product").all()
            ),
            Decimal("0"),
        ).quantize(Decimal("0.01"))
        return cls._quote(
            cart=None,
            order=None,
            order_draft=draft,
            destination_address=draft.delivery_address,
            package=package,
            items_total=items_total,
            payment_method=draft.payment_method,
            client=client,
        )

    @classmethod
    def quote_cart(
        cls,
        cart,
        *,
        destination_address: str,
        items_total: Decimal,
        payment_method: str = PaymentMethod.CARD_PREPAYMENT,
        client: YandexDeliveryClient | None = None,
    ) -> DeliveryQuote:
        """Рассчитать доставку активной ручной корзины до создания CRM-заказа."""
        CartService.validate_cart_for_order(cart)
        package = DeliveryPackageService.build(
            DeliveryLine(product=item.product, quantity=item.quantity)
            for item in CartService.get_contents(cart)
        )
        # Предварительная цена нужна до выбора оплаты. Для cash-on-delivery
        # используем тариф уже оплаченного заказа: наличные не передаются в API
        # Яндекса и остаются бизнес-условием WebMarket.
        quote_payment_method = (
            payment_method
            if payment_method in {
                PaymentMethod.CARD_PREPAYMENT,
                PaymentMethod.CARD_ON_DELIVERY,
            }
            else PaymentMethod.CARD_PREPAYMENT
        )
        return cls._quote(
            cart=cart,
            order=None,
            order_draft=None,
            destination_address=destination_address,
            package=package,
            items_total=items_total,
            payment_method=quote_payment_method,
            client=client,
        )

    @classmethod
    @transaction.atomic
    def _quote(
        cls,
        *,
        cart,
        order,
        order_draft,
        destination_address: str,
        package: DeliveryPackage,
        items_total: Decimal,
        payment_method: str,
        client: YandexDeliveryClient | None,
    ) -> DeliveryQuote:
        api_client = client or YandexDeliveryClient()
        config = api_client.config
        config.validate()
        payload = build_pricing_payload(
            station_id=config.station_id,
            destination_address=destination_address,
            package=package,
            items_total=items_total,
            payment_method=payment_method,
        )
        fingerprint = request_fingerprint(payload)

        recovered_error = None
        try:
            result = api_client.calculate_price(payload)
        except YandexDeliveryAPIError as exc:
            # Общедоступный test-контур периодически отвечает transient-ошибкой
            # (в том числе HTTP 500) на неизменный валидный payload. Один
            # ограниченный retry повышает стабильность MVP и никогда не
            # применяется в production.
            if (
                config.environment == DeliveryEnvironment.TEST
                and (exc.retryable or exc.code == "no_delivery_options")
            ):
                recovered_error = exc
                try:
                    result = api_client.calculate_price(payload)
                except YandexDeliveryAPIError as retry_exc:
                    exc = retry_exc
                else:
                    exc = None
            if exc is not None:
                quote = DeliveryQuote.objects.create(
                    cart=cart,
                    order=order,
                    order_draft=order_draft,
                    environment=config.environment,
                    kind=DeliveryQuoteKind.PRELIMINARY,
                    status=DeliveryQuoteStatus.FAILED,
                    request_fingerprint=fingerprint,
                    destination_address=destination_address,
                    package_snapshot=asdict(package),
                    request_payload=payload,
                    response_payload=exc.response_payload,
                    error_code=exc.code,
                    error_message=str(exc),
                )
                DeliverySyncEvent.objects.create(
                    quote=quote,
                    operation=DeliveryOperation.PRICING,
                    succeeded=False,
                    http_status=exc.status_code,
                    request_payload=payload,
                    response_payload=exc.response_payload,
                    error_code=exc.code,
                    error_message=str(exc),
                )
                return quote

        response_payload = result.raw_payload
        if recovered_error is not None:
            response_payload = {
                **response_payload,
                "_webmarket_retry": {
                    "attempts": 2,
                    "recovered_code": recovered_error.code,
                    "recovered_message": str(recovered_error),
                },
            }

        quote = DeliveryQuote.objects.create(
            cart=cart,
            order=order,
            order_draft=order_draft,
            environment=config.environment,
            kind=DeliveryQuoteKind.PRELIMINARY,
            status=DeliveryQuoteStatus.SUCCEEDED,
            request_fingerprint=fingerprint,
            destination_address=destination_address,
            package_snapshot=asdict(package),
            amount=result.amount,
            currency=result.currency,
            delivery_days=result.delivery_days,
            request_payload=payload,
            response_payload=response_payload,
        )
        DeliverySyncEvent.objects.create(
            quote=quote,
            operation=DeliveryOperation.PRICING,
            succeeded=True,
            http_status=200,
            request_payload=payload,
            response_payload=response_payload,
        )
        return quote
