"""Сериализаторы REST API — реэкспорт для обратной совместимости."""
from apps.api.serializers.identify import (
    IdentifyCustomerRequestSerializer,
    IdentifyCustomerResponseSerializer,
)

__all__ = [
    "IdentifyCustomerRequestSerializer",
    "IdentifyCustomerResponseSerializer",
]
