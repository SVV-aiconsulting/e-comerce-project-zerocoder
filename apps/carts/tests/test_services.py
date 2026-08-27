from decimal import Decimal

import pytest

from apps.carts.services import CartService
from apps.common.enums import CartStatus, Channel
from apps.common.exceptions import MinQuantityError


@pytest.mark.django_db
def test_create_cart(active_cart, customer):
    assert active_cart.status == CartStatus.ACTIVE
    assert active_cart.customer == customer
    assert active_cart.channel == Channel.TELEGRAM


@pytest.mark.django_db
def test_set_item_quantity(active_cart, product):
    item = CartService.set_item_quantity(active_cart, product, Decimal("2"))
    assert item.quantity == Decimal("2")
    contents = list(CartService.get_contents(active_cart))
    assert len(contents) == 1
    assert contents[0].product == product


@pytest.mark.django_db
def test_add_item_alias(active_cart, product):
    item = CartService.add_item(active_cart, product, Decimal("3"))
    assert item.quantity == Decimal("3")


@pytest.mark.django_db
def test_min_quantity_validation(active_cart, product):
    product.min_quantity = Decimal("3")
    product.save()
    with pytest.raises(MinQuantityError):
        CartService.add_item(active_cart, product, Decimal("1"))
