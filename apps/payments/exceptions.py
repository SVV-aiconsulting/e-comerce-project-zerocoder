"""Ошибки платёжного домена."""

from apps.common.exceptions import ShopError


class PaymentError(ShopError):
    """Базовая ошибка платёжного процесса."""


class PaymentConfigurationError(PaymentError):
    """Конфигурация ЮKassa неполна или небезопасна."""


class PaymentDataError(PaymentError):
    """Заказ или платёж не удовлетворяет условиям операции."""


class YooKassaAPIError(PaymentError):
    """ЮKassa недоступна или отклонила запрос."""

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
