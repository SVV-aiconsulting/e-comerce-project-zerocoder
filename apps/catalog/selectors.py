"""Запросы на чтение для каталога."""
from apps.catalog.models import Product


def get_active_products():
    return Product.active_objects.active().prefetch_related("images")


def get_product_by_id(product_id: int):
    return Product.objects.filter(pk=product_id).prefetch_related("images").first()


def get_product_by_code(public_code: str):
    return Product.objects.filter(public_code=public_code).prefetch_related("images").first()
