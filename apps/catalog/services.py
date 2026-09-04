"""Бизнес-логика каталога."""
from decimal import Decimal

from apps.catalog import selectors
from apps.catalog.models import Product
from apps.common.exceptions import MinQuantityError, ProductUnavailableError, QuantityStepError


class CatalogService:
    """Сервис для работы с товарами."""

    @staticmethod
    def get_active_products():
        return selectors.get_active_products()

    @staticmethod
    def get_product(*, product_id: int | None = None, public_code: str | None = None) -> Product | None:
        if product_id is not None:
            return selectors.get_product_by_id(product_id)
        if public_code is not None:
            return selectors.get_product_by_code(public_code)
        return None

    @staticmethod
    def check_availability(product: Product, quantity: Decimal) -> None:
        if not product.is_active:
            raise ProductUnavailableError(f"Товар «{product.name}» недоступен для заказа")
        if quantity < product.min_quantity:
            raise MinQuantityError(product.name, product.min_quantity)
        # min_quantity is both the first allowed quantity and the packaging step.
        # Decimal fields use three fractional digits, therefore modulo comparison is
        # exact and does not inherit floating-point rounding errors from a frontend.
        if quantity % product.min_quantity != Decimal("0"):
            raise QuantityStepError(product.name, product.min_quantity)
