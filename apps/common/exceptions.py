"""Доменные исключения для backend интернет-магазина."""


class ShopError(Exception):
    """Базовое исключение для ошибок бизнес-логики магазина."""


class PreviewStaleError(ShopError):
    def __init__(self, message="Условия заказа изменились. Рассчитайте итог и подтвердите его заново."):
        super().__init__(message)


class ProductUnavailableError(ShopError):
    """Товар неактивен или недоступен для заказа."""


class CartEmptyError(ShopError):
    """Корзина пуста."""


class MinQuantityError(ShopError):
    """Количество товара ниже минимально допустимого."""

    def __init__(self, product_name: str, min_quantity):
        self.product_name = product_name
        self.min_quantity = min_quantity
        super().__init__(
            f"Минимальное количество для «{product_name}»: {min_quantity}"
        )


class DeliveryError(ShopError):
    """Ошибка валидации правила доставки."""


class ChannelIdentityAlreadyLinkedError(ShopError):
    """Внешний идентификатор канала уже привязан к другому клиенту."""


class CartNotAvailableError(ShopError):
    """Корзина уже оформлена или недоступна для заказа."""


class IntakeRateLimited(ShopError):
    def __init__(self, retry_after=60):
        self.retry_after = retry_after
        super().__init__("Слишком много сообщений. Дождитесь ответа и попробуйте позже.")
