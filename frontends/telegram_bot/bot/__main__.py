"""Точка входа: python -m bot"""
import asyncio
import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError

from bot.api.client import StorefrontApiClient
from bot.app import create_dispatcher, on_startup, setup_logging
from bot.config import get_settings

logger = logging.getLogger(__name__)


def create_bot(settings) -> Bot:
    if settings.telegram_proxy:
        logger.info("Using Telegram proxy: %s", settings.telegram_proxy)
        session = AiohttpSession(proxy=settings.telegram_proxy)
        return Bot(
            token=settings.telegram_bot_token,
            session=session,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
    return Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.telegram_bot_log_level)

    api_client = StorefrontApiClient(
        base_url=settings.backend_api_base_url,
        adapter_token=settings.adapter_api_token,
        timeout=settings.http_timeout_seconds,
    )

    await on_startup(api_client)

    bot = create_bot(settings)
    dp = create_dispatcher(settings, api_client)

    if not settings.telegram_bot_use_polling:
        raise RuntimeError("Only polling is supported on this stage")

    logger.info("Starting polling...")
    while True:
        try:
            await dp.start_polling(bot)
            break
        except TelegramNetworkError as exc:
            logger.error(
                "Cannot reach Telegram API (%s). Retry in 30s. "
                "If you are in RU: set TELEGRAM_PROXY or run the bot on the host, not in Docker.",
                exc,
            )
            await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(main())
