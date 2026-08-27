"""Бизнес-логика заказов."""
from decimal import Decimal

from django.db import transaction

from apps.carts.models import Cart
from apps.carts.services import CartService
from apps.common.enums import CartStatus, OrderStatus, StatusChangeSource
from apps.common.exceptions import CartNotAvailableError
from apps.common.utils import generate_public_number
from apps.customers.models import Customer
from apps.customers.services import CustomerService
from apps.orders.models import Order, OrderItem, OrderStatusHistory
from apps.orders.pricing import PricingService


class OrderService:
    """Сервис создания и управления заказами."""

    @staticmethod
    def save_customer_snapshot(
        customer: Customer,
        *,
        phone: str | None = None,
        email: str | None = None,
    ) -> dict:
        return {
            "customer_code_snapshot": customer.public_code,
            "customer_name_snapshot": customer.name,
            "customer_phone_snapshot": customer.phone if phone is None else phone,
            "customer_email_snapshot": customer.email if email is None else email,
        }

    @staticmethod
    def save_product_snapshot(product) -> dict:
        return {
            "product_name_snapshot": product.name,
            "product_unit_snapshot": product.unit,
            "unit_price": product.base_price,
        }

    @staticmethod
    def create_order_items(order: Order, cart_items) -> list[OrderItem]:
        items = []
        for cart_item in cart_items:
            snapshot = OrderService.save_product_snapshot(cart_item.product)
            total_price = (snapshot["unit_price"] * cart_item.quantity).quantize(Decimal("0.01"))
            items.append(
                OrderItem(
                    order=order,
                    product=cart_item.product,
                    quantity=cart_item.quantity,
                    total_price=total_price,
                    **snapshot,
                )
            )
        return OrderItem.objects.bulk_create(items)

    @staticmethod
    def create_status_history(
        order: Order,
        *,
        old_status: str = "",
        new_status: str | None = None,
        source: str = StatusChangeSource.AUTOMATIC,
        changed_by=None,
        comment: str = "",
    ) -> OrderStatusHistory:
        return OrderStatusHistory.objects.create(
            order=order,
            old_status=old_status,
            new_status=new_status or order.order_status,
            source=source,
            changed_by=changed_by,
            comment=comment,
        )

    @classmethod
    @transaction.atomic
    def create_order_from_cart(
        cls,
        cart: Cart,
        *,
        customer: Customer,
        channel: str,
        receiving_type: str,
        payment_method: str,
        desired_date=None,
        desired_time_interval: str = "",
        delivery_address: str = "",
        customer_comment: str = "",
        manager_comment: str = "",
        customer_phone_snapshot: str | None = None,
        customer_email_snapshot: str | None = None,
        delivery_cost_override: Decimal | None = None,
        status_source: str = StatusChangeSource.AUTOMATIC,
        is_new_customer: bool = False,
    ) -> Order:
        cart = Cart.objects.select_for_update().get(pk=cart.pk)
        if cart.status != CartStatus.ACTIVE:
            raise CartNotAvailableError("Корзина уже оформлена или недоступна")

        if cart.customer_id and cart.customer_id != customer.pk:
            raise CartNotAvailableError("Корзина принадлежит другому клиенту")
        if not cart.customer_id:
            cart.customer = customer
            cart.save(update_fields=["customer", "updated_at"])

        CartService.validate_cart_for_order(cart)
        cart_items = list(CartService.get_contents(cart))

        totals = PricingService.calculate_order_totals(
            customer=customer,
            cart_items=cart_items,
            receiving_type=receiving_type,
        )
        if delivery_cost_override is not None:
            delivery_cost_override = Decimal(delivery_cost_override).quantize(
                Decimal("0.01")
            )
            if delivery_cost_override < 0:
                raise ValueError("Стоимость доставки не может быть отрицательной")
            totals.delivery_cost = delivery_cost_override
            totals.total_amount = PricingService.calculate_total(
                totals.items_total,
                totals.discount_amount,
                delivery_cost_override,
            )

        public_number = generate_public_number(
            lambda number: Order.objects.filter(public_number=number).exists()
        )

        order = Order.objects.create(
            public_number=public_number,
            customer=customer,
            channel=channel,
            source_external_user_id_snapshot=cart.external_user_id,
            is_new_customer=is_new_customer,
            receiving_type=receiving_type,
            desired_date=desired_date,
            desired_time_interval=desired_time_interval,
            delivery_address=delivery_address,
            customer_comment=customer_comment,
            manager_comment=manager_comment,
            items_total=totals.items_total,
            discount_amount=totals.discount_amount,
            delivery_cost=totals.delivery_cost,
            total_amount=totals.total_amount,
            payment_method=payment_method,
            order_status=OrderStatus.NEW,
            **cls.save_customer_snapshot(
                customer,
                phone=customer_phone_snapshot,
                email=customer_email_snapshot,
            ),
        )

        cls.create_order_items(order, cart_items)
        cls.create_status_history(
            order,
            old_status="",
            new_status=OrderStatus.NEW,
            source=status_source,
        )
        CartService.mark_as_ordered(cart)
        CustomerService.update_stats_after_order(customer, totals.total_amount)

        return order

    @staticmethod
    def change_status(
        order: Order,
        new_status: str,
        *,
        source: str = StatusChangeSource.DJANGO_ADMIN,
        changed_by=None,
        comment: str = "",
    ) -> Order:
        old_status = order.order_status
        if old_status == new_status:
            return order

        order.order_status = new_status
        order.save(update_fields=["order_status", "updated_at"])
        OrderService.create_status_history(
            order,
            old_status=old_status,
            new_status=new_status,
            source=source,
            changed_by=changed_by,
            comment=comment,
        )
        return order
