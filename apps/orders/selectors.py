"""Запросы на чтение для заказов."""
from apps.orders.models import Order


def get_order_by_number(public_number: str):
    return (
        Order.objects.filter(public_number=public_number)
        .select_related("customer")
        .prefetch_related("items")
        .first()
    )


def get_orders_for_customer(customer_id: int, *, limit: int = 10):
    return (
        Order.objects.filter(customer_id=customer_id)
        .order_by("-created_at")[:limit]
    )
