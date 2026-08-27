from decimal import Decimal



import pytest



from apps.carts.services import CartService

from apps.common.enums import (
    CartStatus,
    Channel,
    CustomerSource,
    OrderStatus,
    PaymentMethod,
    ReceivingType,
)
from apps.customers.services import CustomerService

from apps.common.exceptions import (
    CartEmptyError,
    CartNotAvailableError,
    MinQuantityError,
    ProductUnavailableError,
)

from apps.orders.models import Order, OrderStatusHistory

from apps.orders.services import OrderService





def _create_order(active_cart, customer, **kwargs):

    defaults = {

        "customer": customer,

        "channel": Channel.TELEGRAM,

        "receiving_type": ReceivingType.DELIVERY,

        "payment_method": PaymentMethod.CASH_ON_DELIVERY,

        "delivery_address": "ул. Тестовая, 1",

    }

    defaults.update(kwargs)

    return OrderService.create_order_from_cart(active_cart, **defaults)





@pytest.mark.django_db

def test_create_order_from_cart(active_cart, product, customer, delivery_rule):

    CartService.set_item_quantity(active_cart, product, Decimal("2"))



    order = _create_order(active_cart, customer)



    assert order.public_number

    assert order.customer_code_snapshot == customer.public_code

    assert order.customer_name_snapshot == customer.name

    assert order.customer_phone_snapshot == customer.phone

    assert order.items_total == Decimal("200.00")

    assert order.delivery_cost == Decimal("300.00")

    assert order.total_amount == Decimal("500.00")



    order_item = order.items.get()

    assert order_item.product_name_snapshot == product.name

    assert order_item.product_unit_snapshot == product.unit

    assert order_item.unit_price == Decimal("100.00")

    assert order_item.total_price == Decimal("200.00")



    active_cart.refresh_from_db()

    assert active_cart.status == CartStatus.ORDERED



    history = OrderStatusHistory.objects.filter(order=order)

    assert history.count() == 1

    assert history.first().new_status == OrderStatus.NEW





@pytest.mark.django_db

def test_order_snapshot_isolated_from_price_changes(active_cart, product, customer, delivery_rule):

    CartService.set_item_quantity(active_cart, product, Decimal("1"))



    order = _create_order(

        active_cart,

        customer,

        receiving_type=ReceivingType.PICKUP,

        payment_method=PaymentMethod.CARD_ON_DELIVERY,

        delivery_address="",

    )



    product.base_price = Decimal("999.00")

    product.save()



    order_item = order.items.get()

    assert order_item.unit_price == Decimal("100.00")

    assert order.total_amount == Decimal("100.00")





@pytest.mark.django_db

def test_reorder_same_cart_raises(active_cart, product, customer, delivery_rule):

    CartService.set_item_quantity(active_cart, product, Decimal("1"))

    _create_order(active_cart, customer)



    with pytest.raises(CartNotAvailableError):

        _create_order(active_cart, customer)



    assert Order.objects.count() == 1





@pytest.mark.django_db

def test_empty_cart_cannot_be_ordered(active_cart, customer, delivery_rule):

    with pytest.raises(CartEmptyError):

        _create_order(active_cart, customer)





@pytest.mark.django_db

def test_inactive_product_cannot_be_ordered(active_cart, product, customer, delivery_rule):

    CartService.set_item_quantity(active_cart, product, Decimal("1"))

    product.is_active = False

    product.save()



    with pytest.raises(ProductUnavailableError):

        _create_order(active_cart, customer)





@pytest.mark.django_db
def test_min_quantity_enforced_on_order(active_cart, product, customer, delivery_rule):
    CartService.set_item_quantity(active_cart, product, Decimal("2"))
    product.min_quantity = Decimal("3")
    product.save()

    with pytest.raises(MinQuantityError) as exc_info:
        _create_order(active_cart, customer)

    assert "Минимальное количество" in str(exc_info.value)


@pytest.mark.django_db
def test_checkout_other_customer_cart_raises(active_cart, product, customer, delivery_rule):
    CartService.set_item_quantity(active_cart, product, Decimal("1"))
    other_customer = CustomerService.create_customer(
        name="Другой клиент",
        phone="79123456793",
        first_source=CustomerSource.TELEGRAM,
    )
    orders_before = Order.objects.count()

    with pytest.raises(CartNotAvailableError):
        _create_order(active_cart, other_customer)

    assert Order.objects.count() == orders_before


@pytest.mark.django_db
def test_checkout_anonymous_cart_links_customer(db, product, customer, delivery_rule):
    cart = CartService.get_or_create_active_cart(
        channel=Channel.TELEGRAM,
        external_user_id="anon-user",
        customer=None,
    )
    CartService.set_item_quantity(cart, product, Decimal("1"))

    order = _create_order(cart, customer)

    cart.refresh_from_db()
    assert cart.customer_id == customer.pk
    assert order.customer == customer


@pytest.mark.django_db
def test_email_only_customer_order_keeps_contact_and_channel_snapshot(product):
    customer = CustomerService.create_customer(
        name="Анна Email",
        email="anna@example.com",
        first_source=CustomerSource.EMAIL,
    )
    cart = CartService.get_or_create_active_cart(
        channel=Channel.EMAIL,
        external_user_id="email:anna",
        customer=customer,
    )
    CartService.set_item_quantity(cart, product, Decimal("1"))

    order = OrderService.create_order_from_cart(
        cart,
        customer=customer,
        channel=Channel.EMAIL,
        receiving_type=ReceivingType.PICKUP,
        payment_method=PaymentMethod.CARD_PREPAYMENT,
    )

    assert order.customer_phone_snapshot == ""
    assert order.customer_email_snapshot == "anna@example.com"
    assert order.source_external_user_id_snapshot == "email:anna"


@pytest.mark.django_db
def test_confirmed_delivery_cost_override_is_kept_in_order(
    active_cart,
    product,
    customer,
    delivery_rule,
):
    CartService.set_item_quantity(active_cart, product, Decimal("1"))

    order = _create_order(
        active_cart,
        customer,
        payment_method=PaymentMethod.CARD_PREPAYMENT,
        delivery_cost_override=Decimal("123.45"),
    )

    assert order.delivery_cost == Decimal("123.45")
    assert order.total_amount == Decimal("223.45")
