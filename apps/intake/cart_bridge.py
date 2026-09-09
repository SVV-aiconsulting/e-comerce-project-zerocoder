"""Versioned synchronization of the one channel basket and AI draft."""
from django.db import transaction
from apps.carts.models import Cart
from apps.carts.services import CartService
from apps.common.enums import CartStatus
from apps.intake.enums import ACTIVE_DRAFT_STATUSES, ItemMatchStatus, ResolutionSource
from apps.intake.models import OrderDraft, OrderDraftItem

class UnifiedCartBridge:
    @staticmethod
    @transaction.atomic
    def cart_to_draft(draft, *, include_checkout=True):
        """Synchronize the shared cart into an assistant draft.

        A new website assistant conversation may continue to use the current
        browser cart, but checkout details are conversation-scoped consent and
        intent.  They must not be silently treated as a choice in the new
        conversation.
        """
        from apps.intake.services import OrderDraftService
        if draft.cart_id:
            cart = Cart.objects.select_for_update().get(pk=draft.cart_id)
            if cart.status != CartStatus.ACTIVE:
                draft.items.all().delete()
                return draft
        else:
            cart = CartService.get_or_create_active_cart(channel=draft.channel,
                external_user_id=draft.external_user_id, customer=draft.customer)
            if not cart.items.exists() and draft.items.exists():
                UnifiedCartBridge.draft_to_cart(draft)
                return draft
        if draft.cart_id == cart.pk and draft.synced_cart_revision == cart.revision:
            return draft
        rows = list(cart.items.select_related("product").order_by("pk"))
        old = {i.product_id: i.requested_quantity for i in draft.items.all()}
        new = {i.product_id: i.quantity for i in rows}
        changes = (
            {f: getattr(cart, f) for f in Cart.CHECKOUT_FIELDS
             if getattr(cart, f) != getattr(draft, f)}
            if include_checkout else {}
        )
        # Contact supplied by the event remains available before checkout sync.
        changes = {f:v for f,v in changes.items() if v or f not in ("contact_phone", "contact_email")}
        if (old != new or changes) and draft.status in ACTIVE_DRAFT_STATUSES:
            draft = OrderDraftService.record_change(draft)
        if old != new:
            draft.items.all().delete()
            OrderDraftItem.objects.bulk_create([
                OrderDraftItem(draft=draft, line_number=n, raw_product_name=i.product.name,
                    requested_quantity=i.quantity, requested_unit=i.product.unit, product=i.product,
                    match_status=ItemMatchStatus.MATCHED, candidate_product_ids=[i.product_id],
                    resolution_source=ResolutionSource.EXACT, resolution_confidence=1)
                for n,i in enumerate(rows, 1)])
        OrderDraft.objects.filter(pk=draft.pk).update(cart=cart, synced_cart_revision=cart.revision, **changes)
        draft.refresh_from_db()
        return draft

    @staticmethod
    @transaction.atomic
    def draft_to_cart(draft):
        cart = CartService.get_or_create_active_cart(channel=draft.channel,
            external_user_id=draft.external_user_id, customer=draft.customer)
        cart = Cart.objects.select_for_update().get(pk=cart.pk)
        rows = list(draft.items.select_related("product").filter(product__isnull=False))
        cart.items.exclude(product_id__in=[i.product_id for i in rows]).delete()
        for item in rows:
            CartService.set_item_quantity(cart, item.product, item.requested_quantity)
        for field in Cart.CHECKOUT_FIELDS:
            setattr(cart, field, getattr(draft, field))
        cart.save(update_fields=[*Cart.CHECKOUT_FIELDS, "updated_at"])
        draft.cart = cart
        draft.synced_cart_revision = cart.revision
        draft.save(update_fields=["cart", "synced_cart_revision"])
        return cart
