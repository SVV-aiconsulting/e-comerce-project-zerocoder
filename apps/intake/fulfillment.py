"""Детерминированные preview и конвертация подтверждённого AI-черновика."""
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.db import transaction

from apps.carts.services import CartService
from apps.common.enums import ReceivingType, StatusChangeSource
from apps.common.exceptions import DeliveryError
from apps.delivery.models import (
    DeliveryEnvironment,
    DeliveryQuoteStatus,
    Shipment,
    ShipmentStatus,
)
from apps.delivery.quote_service import YandexDeliveryQuoteService
from apps.delivery.offer_service import YandexDeliveryOfferService
from apps.delivery.checkout import CheckoutDeliveryService
from apps.intake.cart_bridge import UnifiedCartBridge
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
        cart = UnifiedCartBridge.draft_to_cart(draft)
        previous_quote_id = (
            cart.delivery_quotes.order_by("-id").values_list("id", flat=True).first()
            or 0
        )
        try:
            checkout_preview = CheckoutDeliveryService.preview(
                cart=cart,
                customer=draft.customer,
                receiving_type=draft.receiving_type,
                delivery_address=draft.delivery_address,
                payment_method=draft.payment_method,
            )
        except DeliveryError:
            quote = cart.delivery_quotes.order_by("-created_at", "-id").first()
            if quote is not None:
                quote.order_draft = draft
                quote.save(update_fields=["order_draft", "updated_at"])
            draft.status = OrderDraftStatus.NEEDS_CLARIFICATION
            draft.missing_fields = ["delivery_quote"]
            draft.save(update_fields=["status", "missing_fields", "updated_at"])
            return draft
        totals = checkout_preview.totals
        cart.delivery_quotes.filter(id__gt=previous_quote_id).update(order_draft=draft)
        if checkout_preview.quote is not None:
            checkout_preview.quote.order_draft = draft
            checkout_preview.quote.save(update_fields=["order_draft", "updated_at"])
        from apps.carts.checkout import CheckoutService
        draft.checkout_preview = CheckoutService.record(cart=cart, customer=draft.customer, result=checkout_preview)
        draft.save(update_fields=["checkout_preview"])
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
        source = (
            locked.channel
            if locked.channel in StatusChangeSource.values
            else StatusChangeSource.AUTOMATIC
        )
        from apps.carts.checkout import CheckoutService
        order = CheckoutService.create_order(
            preview_id=locked.checkout_preview.public_id if locked.checkout_preview_id else None,
            channel=locked.channel, external_user_id=locked.external_user_id,
            customer=locked.customer, status_source=source,
            is_new_customer=locked.customer.orders_count == 0)
        if locked.checkout_preview.quote_id:
            quote = locked.checkout_preview.quote
            Shipment.objects.get_or_create(order=order, defaults={"quote": quote,
                "environment": quote.environment, "status": ShipmentStatus.DRAFT,
                "amount": quote.amount, "currency": quote.currency})
        OrderDraftService.mark_converted(locked, order)
        return order
