"""Переиспользование исключений API-клиента."""
from vk_bot.api.errors import ApiError, BackendUnavailableError

__all__ = ["ApiError", "BackendUnavailableError"]
