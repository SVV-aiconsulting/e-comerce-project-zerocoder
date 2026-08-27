"""Точка входа: python -m vk_bot"""
import asyncio
import logging

from vk_bot.api.client import StorefrontApiClient
from vk_bot.config import get_settings
from vk_bot.main import create_bot, on_startup, setup_logging

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.vk_bot_log_level)

    api_client = StorefrontApiClient(
        base_url=settings.backend_api_base_url,
        adapter_token=settings.adapter_api_token,
        timeout=settings.http_timeout_seconds,
    )

    await on_startup(api_client)

    if not settings.vk_bot_use_longpoll:
        raise RuntimeError("Only Long Poll is supported on this stage")

    bot = create_bot(settings, api_client)
    logger.info("Starting VK Long Poll...")
    try:
        await bot.run_polling()
    except Exception as exc:
        message = str(exc).lower()
        if "longpoll" in message and "not enabled" in message:
            logger.error(
                "VK Long Poll не включён в настройках сообщества. "
                "Включите: Управление → Работа с API → Long Poll API → Включено. "
                "Подробнее: docs/VK_BOT.md"
            )
            raise SystemExit(1) from exc
        raise


if __name__ == "__main__":
    asyncio.run(main())
