from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import TelegramObject

from bot.api.client import StorefrontApiClient
from bot.config import Settings


class ApiClientMiddleware(BaseMiddleware):
    def __init__(self, api_client: StorefrontApiClient, settings: Settings) -> None:
        self.api_client = api_client
        self.settings = settings

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["api"] = self.api_client
        data["settings"] = self.settings
        return await handler(event, data)


class PrivateChatMiddleware(BaseMiddleware):
    @staticmethod
    def _get_chat(event: TelegramObject):
        chat = getattr(event, "chat", None)
        if chat is not None:
            return chat
        message = getattr(event, "message", None)
        if message is not None:
            return getattr(message, "chat", None)
        return None

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat = self._get_chat(event)
        if chat is not None and chat.type != ChatType.PRIVATE:
            return None
        return await handler(event, data)
