"""Маппинг кодов ошибок API на сообщения для пользователя."""
from bot.api.errors import ApiError, BackendUnavailableError

PHONE_INVALID_USER_MESSAGE = (
    "Номер телефона указан неверно. Формат: 79991234567. "
    "Попробуйте снова через кнопку регистрации."
)
CONFLICT_USER_MESSAGE = (
    "Не удалось завершить регистрацию. Свяжитесь с менеджером магазина."
)
BACKEND_UNAVAILABLE_MESSAGE = "Магазин временно недоступен. Попробуйте через несколько минут."
GENERIC_ERROR_MESSAGE = "Произошла ошибка. Попробуйте позже."
NOT_IDENTIFIED_MESSAGE = "Сначала пройдите регистрацию: /start"
SESSION_STALE_MESSAGE = "Сессия устарела. Нажмите /start"
CHECKOUT_SESSION_STALE_MESSAGE = (
    "Сессия оформления устарела. Откройте корзину и начните оформление заново."
)


def is_phone_validation_error(exc: ApiError) -> bool:
    if exc.code == "validation_error" and "phone" in exc.details:
        return True
    return "телефон" in exc.message.lower() or "79991234567" in exc.message


def user_message_for_error(exc: Exception) -> str:
    if isinstance(exc, BackendUnavailableError):
        return BACKEND_UNAVAILABLE_MESSAGE

    if not isinstance(exc, ApiError):
        return GENERIC_ERROR_MESSAGE

    if is_phone_validation_error(exc):
        return PHONE_INVALID_USER_MESSAGE

    mapping = {
        "authentication_failed": BACKEND_UNAVAILABLE_MESSAGE,
        "permission_denied": BACKEND_UNAVAILABLE_MESSAGE,
        "customer_identity_required": NOT_IDENTIFIED_MESSAGE,
        "order_access_denied": "Этот заказ вам недоступен.",
        "customer_context_mismatch": SESSION_STALE_MESSAGE,
        "cart_customer_mismatch": SESSION_STALE_MESSAGE,
        "cart_already_ordered": "Корзина уже оформлена. Откройте «Мои заказы».",
        "channel_identity_conflict": CONFLICT_USER_MESSAGE,
        "empty_cart": "Корзина пуста. Добавьте товары из каталога.",
        "product_not_found": "Товар не найден.",
        "product_inactive": "Товар недоступен.",
        "product_unavailable": exc.message,
        "invalid_quantity": exc.message,
        "delivery_error": exc.message,
        "order_not_found": "Заказ не найден.",
        "customer_not_found": SESSION_STALE_MESSAGE,
        "validation_error": "Проверьте введённые данные.",
    }
    return mapping.get(exc.code, exc.message or GENERIC_ERROR_MESSAGE)
