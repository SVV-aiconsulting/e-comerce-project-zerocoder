"""Ошибки домена и API доставки."""

from apps.common.exceptions import DeliveryError


class DeliveryConfigurationError(DeliveryError):
    """Конфигурация внешней доставки неполна или небезопасна."""


class DeliveryDataIncompleteError(DeliveryError):
    """В заказе не хватает данных для внешнего расчёта."""


class YandexDeliveryAPIError(DeliveryError):
    """Яндекс Доставка вернула ошибку или недоступна."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str = "",
        response_payload: dict | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.response_payload = response_payload or {}
        self.retryable = retryable
