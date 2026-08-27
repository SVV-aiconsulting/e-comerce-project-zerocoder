"""Вспомогательные функции для handlers."""
import logging
from functools import wraps
from typing import Any, Awaitable, Callable

from aiogram.types import CallbackQuery, Message

from bot.api.errors import ApiError, BackendUnavailableError
from bot.constants import CHANNEL
from bot.services.error_messages import user_message_for_error

logger = logging.getLogger(__name__)


def telegram_user_context(user) -> dict[str, str]:
    display_name = " ".join(
        part for part in [user.first_name or "", user.last_name or ""] if part
    ).strip() or (user.username or "Покупатель")
    return {
        "channel": CHANNEL,
        "external_user_id": str(user.id),
        "username": user.username or "",
        "display_name": display_name,
    }


def handle_api_errors(func: Callable[..., Awaitable[Any]]):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except (ApiError, BackendUnavailableError) as exc:
            logger.warning("API error in %s: %s", func.__name__, exc)
            message = None
            for arg in args:
                if isinstance(arg, Message):
                    message = arg
                    break
            if message is None:
                message = kwargs.get("message")
            if message is not None:
                await message.answer(user_message_for_error(exc))
            raise

    return wrapper


async def answer_api_error(target: Message | CallbackQuery, exc: Exception) -> None:
    logger.warning("API error: %s", exc)
    text = user_message_for_error(exc)
    if isinstance(target, CallbackQuery):
        await target.message.answer(text)
    elif isinstance(target, Message):
        await target.answer(text)
