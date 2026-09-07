"""Синхронизация общей корзины канала и рабочего AI-черновика."""

from django.db import transaction
from django.db.models import Max

from apps.carts.models import Cart
from apps.carts.services import CartService
from apps.common.enums import CartStatus
from apps.intake.enums import ItemMatchStatus, ResolutionSource
from apps.intake.models import OrderDraft, OrderDraftItem


class UnifiedCartBridge:
    @staticmethod
    @transaction.atomic
    def cart_to_draft(draft: OrderDraft) -> OrderDraft:
        ordered_after_draft = Cart.objects.filter(
            channel=draft.channel,
            external_user_id=draft.external_user_id,
            status=CartStatus.ORDERED,
            updated_at__gt=draft.updated_at,
        ).exists()
        cart_existed = Cart.objects.filter(
            channel=draft.channel,
            external_user_id=draft.external_user_id,
            status=CartStatus.ACTIVE,
        ).exists()
        cart = CartService.get_or_create_active_cart(
            channel=draft.channel,
            external_user_id=draft.external_user_id,
            customer=draft.customer,
        )
        cart_items = list(cart.items.select_related("product"))
        if ordered_after_draft:
            # Ручной checkout уже завершил эту общую корзину. Старый AI-черновик
            # не должен воскресить позиции и создать повторный заказ.
            draft.items.all().delete()
            return draft
        # Пустая новая корзина не должна стирать уже существующий AI-черновик.
        if not cart_items:
            if not cart_existed and draft.items.exists():
                UnifiedCartBridge.draft_to_cart(draft)
            elif cart.updated_at > draft.updated_at:
                draft.items.all().delete()
                OrderDraft.objects.filter(pk=draft.pk).update(
                    receiving_type="",
                    delivery_address="",
                    payment_method="",
                    customer_comment="",
                )
            elif draft.items.exists():
                UnifiedCartBridge.draft_to_cart(draft)
            return draft
        cart_product_ids = {item.product_id for item in cart_items}
        draft.items.exclude(product_id__in=cart_product_ids).delete()
        line = draft.items.aggregate(value=Max("line_number"))["value"] or 0
        for cart_item in cart_items:
            item = draft.items.filter(product_id=cart_item.product_id).first()
            if item:
                if item.requested_quantity != cart_item.quantity:
                    item.requested_quantity = cart_item.quantity
                    item.save(update_fields=["requested_quantity", "updated_at"])
                continue
            line += 1
            product = cart_item.product
            OrderDraftItem.objects.create(
                draft=draft,
                line_number=line,
                raw_product_name=product.name,
                requested_quantity=cart_item.quantity,
                requested_unit=product.unit,
                product=product,
                match_status=ItemMatchStatus.MATCHED,
                candidate_product_ids=[product.pk],
                resolution_source=ResolutionSource.EXACT,
                resolution_confidence=1,
            )
        checkout_updates = {}
        for field in (
            "receiving_type",
            "delivery_address",
            "payment_method",
            "customer_comment",
        ):
            value = getattr(cart, field)
            if value != getattr(draft, field):
                checkout_updates[field] = value
        for field in ("contact_phone", "contact_email"):
            value = getattr(cart, field) or getattr(draft, field)
            if not value and draft.customer_id:
                value = getattr(draft.customer, field.removeprefix("contact_"), "")
            if value and value != getattr(draft, field):
                checkout_updates[field] = value
        if checkout_updates:
            OrderDraft.objects.filter(pk=draft.pk).update(**checkout_updates)
            draft.refresh_from_db(fields=list(checkout_updates))
        return draft

    @staticmethod
    @transaction.atomic
    def draft_to_cart(draft: OrderDraft):
        cart = CartService.get_or_create_active_cart(
            channel=draft.channel,
            external_user_id=draft.external_user_id,
            customer=draft.customer,
        )
        CartService.clear(cart)
        for item in draft.items.select_related("product").filter(product__isnull=False):
            CartService.set_item_quantity(cart, item.product, item.requested_quantity)
        checkout_fields = (
            "receiving_type",
            "delivery_address",
            "payment_method",
            "customer_comment",
            "contact_phone",
            "contact_email",
        )
        changed = []
        for field in checkout_fields:
            value = getattr(draft, field)
            if value != getattr(cart, field):
                setattr(cart, field, value)
                changed.append(field)
        if changed:
            cart.save(update_fields=[*changed, "updated_at"])
        return cart
