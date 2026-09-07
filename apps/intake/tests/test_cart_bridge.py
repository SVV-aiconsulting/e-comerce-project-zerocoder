from decimal import Decimal

import pytest

from apps.carts.services import CartService
from apps.common.enums import Channel
from apps.intake.cart_bridge import UnifiedCartBridge
from apps.intake.services import OrderDraftService


@pytest.mark.django_db
def test_manual_and_assistant_use_the_same_channel_cart(customer, product):
    cart = CartService.get_or_create_active_cart(
        channel=Channel.TELEGRAM,
        external_user_id="shared-cart-user",
        customer=customer,
    )
    CartService.set_item_quantity(cart, product, Decimal("2"))
    draft, _ = OrderDraftService.get_or_create_active(
        channel=Channel.TELEGRAM,
        external_user_id="shared-cart-user",
        conversation_key="shared-cart-user",
        customer=customer,
    )

    UnifiedCartBridge.cart_to_draft(draft)
    assert draft.items.get(product=product).requested_quantity == Decimal("2")

    draft.items.filter(product=product).update(requested_quantity=Decimal("3"))
    UnifiedCartBridge.draft_to_cart(draft)
    assert cart.items.get(product=product).quantity == Decimal("3")
