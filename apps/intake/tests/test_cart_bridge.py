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
    cart.receiving_type = "delivery"
    cart.delivery_address = "Москва, Тверская, 1"
    cart.payment_method = "card_prepayment"
    cart.save(update_fields=["receiving_type", "delivery_address", "payment_method", "updated_at"])
    draft, _ = OrderDraftService.get_or_create_active(
        channel=Channel.TELEGRAM,
        external_user_id="shared-cart-user",
        conversation_key="shared-cart-user",
        customer=customer,
    )

    UnifiedCartBridge.cart_to_draft(draft)
    assert draft.items.get(product=product).requested_quantity == Decimal("2")
    draft.refresh_from_db()
    assert draft.receiving_type == "delivery"
    assert draft.delivery_address == "Москва, Тверская, 1"
    assert draft.payment_method == "card_prepayment"

    draft.items.filter(product=product).update(requested_quantity=Decimal("3"))
    UnifiedCartBridge.draft_to_cart(draft)
    assert cart.items.get(product=product).quantity == Decimal("3")


@pytest.mark.django_db
def test_fresh_website_dialogue_imports_items_but_not_checkout_details(product):
    cart = CartService.get_or_create_active_cart(
        channel=Channel.WEBSITE,
        external_user_id="web:fresh-dialogue",
    )
    CartService.set_item_quantity(cart, product, Decimal("2"))
    cart.receiving_type = "delivery"
    cart.delivery_address = "Москва, Разина, 15"
    cart.payment_method = "card_prepayment"
    cart.contact_phone = "79991234567"
    cart.contact_email = "old@example.com"
    cart.save(update_fields=[
        "receiving_type", "delivery_address", "payment_method",
        "contact_phone", "contact_email", "updated_at",
    ])
    draft, _ = OrderDraftService.get_or_create_active(
        channel=Channel.WEBSITE,
        external_user_id="web:fresh-dialogue",
        conversation_key="web:fresh-dialogue:assistant:new",
    )

    UnifiedCartBridge.cart_to_draft(draft, include_checkout=False)

    draft.refresh_from_db()
    assert draft.items.get(product=product).requested_quantity == Decimal("2")
    assert draft.receiving_type == ""
    assert draft.delivery_address == ""
    assert draft.payment_method == ""
    assert draft.contact_phone == ""
    assert draft.contact_email == ""
