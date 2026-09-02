"""Детерминированные preview и конвертация подтверждённого AI-черновика."""
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.db import transaction

from apps.carts.services import CartService
from apps.common.enums import ReceivingType, StatusChangeSource
from apps.common.exceptions import DeliveryError
from apps.delivery.models import (
    DeliveryQuoteStatus,
    Shipment,
    ShipmentStatus,
)
from apps.delivery.quote_service import YandexDeliveryQuoteService
from apps.intake.enums import OrderDraftStatus
from apps.intake.exceptions import DraftStateError
from apps.intake.models import OrderDraft
from apps.intake.services import OrderDraftService
from apps.orders.pricing import PricingService
from apps.orders.services import OrderService


@dataclass(frozen=True)
class PricingItem:
    product: object
    quantity: Decimal


class DraftPricingService:
    @staticmethod
    def preview(draft: OrderDraft) -> OrderDraft:
        draft = OrderDraft.objects.select_related("customer").get(pk=draft.pk)
        OrderDraftService.validate_ready_for_preview(draft)
        items = [
            PricingItem(product=item.product, quantity=item.requested_quantity)
            for item in draft.items.select_related("product").order_by("line_number")
        ]
        totals = PricingService.calculate_order_totals(
            customer=draft.customer,
            cart_items=items,
            receiving_type=draft.receiving_type,
        )
        if (
            settings.YANDEX_DELIVERY_ENABLED
            and draft.receiving_type == ReceivingType.DELIVERY
        ):
            try:
                quote = YandexDeliveryQuoteService.quote_draft(draft)
            except DeliveryError:
                quote = None
            if not quote or quote.status != DeliveryQuoteStatus.SUCCEEDED:
                draft.status = OrderDraftStatus.NEEDS_CLARIFICATION
                draft.missing_fields = ["delivery_quote"]
                draft.save(update_fields=["status", "missing_fields", "updated_at"])
                return draft
            delivery_cost = Decimal("0") if totals.free_delivery else quote.amount
            totals.delivery_cost = delivery_cost
            totals.total_amount = PricingService.calculate_total(
                totals.items_total,
                totals.discount_amount,
                delivery_cost,
            )
        return OrderDraftService.record_preview(
            draft,
            items_total=totals.items_total,
            discount_amount=totals.discount_amount,
            delivery_cost=totals.delivery_cost,
            total_amount=totals.total_amount,
        )


class DraftOrderConversionService:
    @staticmethod
    @transaction.atomic
    def convert(draft: OrderDraft):
        locked = OrderDraft.objects.select_for_update().get(pk=draft.pk)
        if locked.converted_order_id:
            return locked.converted_order
        if locked.status != OrderDraftStatus.CONFIRMED:
            raise DraftStateError("Черновик ещё не подтверждён клиентом")
        if locked.confirmed_revision != locked.revision:
            raise DraftStateError("Подтверждена устаревшая версия черновика")
        if locked.customer_id is None:
            raise DraftStateError("Для создания заказа нужен клиент")

        OrderDraftService.validate_ready_for_preview(locked)
        cart = CartService.get_or_create_active_cart(
            channel=locked.channel,
            external_user_id=locked.external_user_id,
            customer=locked.customer,
        )
        CartService.clear(cart)
        for item in locked.items.select_related("product").order_by("line_number"):
            CartService.set_item_quantity(cart, item.product, item.requested_quantity)

        source = (
            locked.channel
            if locked.channel in StatusChangeSource.values
            else StatusChangeSource.AUTOMATIC
        )
        order = OrderService.create_order_from_cart(
            cart,
            customer=locked.customer,
            channel=locked.channel,
            receiving_type=locked.receiving_type,
            payment_method=locked.payment_method,
            desired_date=locked.desired_date,
            desired_time_interval=locked.desired_time_interval,
            delivery_address=locked.delivery_address,
            customer_comment=locked.customer_comment,
            customer_phone_snapshot=locked.contact_phone,
            customer_email_snapshot=locked.contact_email,
            delivery_cost_override=locked.delivery_cost,
            status_source=source,
            is_new_customer=locked.customer.orders_count == 0,
        )
        quote = (
            locked.delivery_quotes.filter(status=DeliveryQuoteStatus.SUCCEEDED)
            .order_by("-created_at")
            .first()
        )
        if quote is not None:
            quote.order = order
            quote.status = DeliveryQuoteStatus.SELECTED
            quote.save(update_fields=["order", "status", "updated_at"])
            Shipment.objects.create(
                order=order,
                quote=quote,
                environment=quote.environment,
                status=ShipmentStatus.DRAFT,
                amount=quote.amount,
                currency=quote.currency,
            )
        OrderDraftService.mark_converted(locked, order)
        return order
