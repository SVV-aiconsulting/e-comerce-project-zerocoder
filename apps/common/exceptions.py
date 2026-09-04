"""Доменные исключения для backend интернет-магазина."""


class ShopError(Exception):
    """Базовое исключение для ошибок бизнес-логики магазина."""


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


class QuantityStepError(MinQuantityError):
    """Количество не является кратным минимальной фасовке товара."""

    def __init__(self, product_name: str, min_quantity):
        self.product_name = product_name
        self.min_quantity = min_quantity
        ShopError.__init__(
            self,
            f"Количество для «{product_name}» должно быть кратно минимальному заказу: {min_quantity}",
        )


class DeliveryError(ShopError):
    """Ошибка валидации правила доставки."""


class ChannelIdentityAlreadyLinkedError(ShopError):
    """Внешний идентификатор канала уже привязан к другому клиенту."""


class CartNotAvailableError(ShopError):
    """Корзина уже оформлена или недоступна для заказа."""
