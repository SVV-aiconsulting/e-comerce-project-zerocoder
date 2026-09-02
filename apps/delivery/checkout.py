"""Единый расчёт доставки ручной корзины для API, Telegram и website."""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from apps.carts.services import CartService
from apps.common.enums import PaymentMethod, ReceivingType
from apps.delivery.exceptions import DeliveryDataIncompleteError
from apps.delivery.models import DeliveryQuote, DeliveryQuoteKind, DeliveryQuoteStatus
from apps.delivery.quote_service import YandexDeliveryQuoteService
from apps.orders.pricing import OrderTotals, PricingService


@dataclass(frozen=True)
class CheckoutDeliveryPreview:
    totals: OrderTotals
    quote: DeliveryQuote | None = None


def normalize_delivery_address(value: str) -> str:
    return " ".join((value or "").split())


class CheckoutDeliveryService:
    """Рассчитывает и затем проверяет цену, показанную клиенту до заказа."""

    @classmethod
    def preview(
        cls,
        *,
        cart,
        customer,
        receiving_type: str,
        delivery_address: str = "",
        payment_method: str = PaymentMethod.CARD_PREPAYMENT,
    ) -> CheckoutDeliveryPreview:
        CartService.validate_cart_for_order(cart)
        cart_items = list(CartService.get_contents(cart))
        if customer is None:
            items_total = PricingService.calculate_items_total(cart_items)
            totals = OrderTotals(
                items_total=items_total,
                discount_amount=Decimal("0.00"),
                delivery_cost=Decimal("0.00"),
                total_amount=items_total,
                free_delivery=False,
            )
        else:
            totals = PricingService.calculate_order_totals(
                customer=customer,
                cart_items=cart_items,
                receiving_type=receiving_type,
            )

        if receiving_type == ReceivingType.PICKUP:
            totals.delivery_cost = Decimal("0.00")
            totals.total_amount = PricingService.calculate_total(
                totals.items_total,
                totals.discount_amount,
                totals.delivery_cost,
            )
            return CheckoutDeliveryPreview(totals=totals)

        address = normalize_delivery_address(delivery_address)
        if settings.YANDEX_DELIVERY_ENABLED:
            if len(address) < 5:
                raise DeliveryDataIncompleteError(
                    "Укажите полный адрес для расчёта Яндекс Доставки"
                )
            quote = YandexDeliveryQuoteService.quote_cart(
                cart,
                destination_address=address,
                items_total=totals.items_total,
                payment_method=payment_method,
            )
            if quote.status != DeliveryQuoteStatus.SUCCEEDED or quote.amount is None:
                raise DeliveryDataIncompleteError(
                    quote.error_message
                    or "Яндекс Доставка не смогла рассчитать стоимость по этому адресу"
                )
            totals.delivery_cost = Decimal("0.00") if totals.free_delivery else quote.amount
            totals.total_amount = PricingService.calculate_total(
                totals.items_total,
                totals.discount_amount,
                totals.delivery_cost,
            )
            return CheckoutDeliveryPreview(totals=totals, quote=quote)

        return CheckoutDeliveryPreview(totals=totals)

    @staticmethod
    def selected_quote(
        *,
        cart,
        receiving_type: str,
        delivery_address: str,
        quote_id: int | None,
    ) -> DeliveryQuote | None:
        if receiving_type == ReceivingType.PICKUP or not settings.YANDEX_DELIVERY_ENABLED:
            return None
        if not quote_id:
            raise DeliveryDataIncompleteError(
                "Сначала рассчитайте и подтвердите стоимость доставки"
            )
        quote = DeliveryQuote.objects.filter(
            pk=quote_id,
            cart=cart,
            kind=DeliveryQuoteKind.PRELIMINARY,
            status=DeliveryQuoteStatus.SUCCEEDED,
        ).first()
        if quote is None or quote.amount is None:
            raise DeliveryDataIncompleteError("Расчёт доставки не найден или недоступен")
        if normalize_delivery_address(quote.destination_address) != normalize_delivery_address(
            delivery_address
        ):
            raise DeliveryDataIncompleteError(
                "Адрес изменился — рассчитайте доставку ещё раз"
            )
        ttl = timedelta(seconds=settings.YANDEX_DELIVERY_QUOTE_TTL_SECONDS)
        if quote.created_at < timezone.now() - ttl:
            quote.status = DeliveryQuoteStatus.EXPIRED
            quote.save(update_fields=["status", "updated_at"])
            raise DeliveryDataIncompleteError(
                "Расчёт доставки устарел — получите новую стоимость"
            )
        return quote

    @staticmethod
    def attach_quote(quote: DeliveryQuote | None, order) -> None:
        if quote is None:
            return
        quote.order = order
        quote.status = DeliveryQuoteStatus.SELECTED
        quote.save(update_fields=["order", "status", "updated_at"])

    @staticmethod
    def delivery_cost_for_quote(*, cart, customer, quote: DeliveryQuote | None) -> Decimal | None:
        if quote is None:
            return None
        totals = PricingService.calculate_order_totals(
            customer=customer,
            cart_items=list(CartService.get_contents(cart)),
            receiving_type=ReceivingType.DELIVERY,
        )
        return Decimal("0.00") if totals.free_delivery else quote.amount
