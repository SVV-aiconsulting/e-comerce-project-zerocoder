"""Запросы на чтение для клиентов."""
from apps.customers.models import Customer


def get_customer_by_id(customer_id: int):
    return Customer.objects.filter(pk=customer_id).first()


def get_customer_by_public_code(public_code: str):
    return Customer.objects.filter(public_code=public_code).first()
