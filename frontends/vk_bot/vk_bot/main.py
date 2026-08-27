"""Сборка и запуск VK-бота."""
from __future__ import annotations

import logging

from vkbottle import Bot
from vkbottle.tools import PhotoMessageUploader

from vk_bot.api.client import StorefrontApiClient
from vk_bot.config import Settings
from vk_bot.handlers.cart import register_cart_handlers
from vk_bot.handlers.ai_orders import handle_natural_order_message
from vk_bot.handlers.catalog import register_catalog_handlers
from vk_bot.handlers.checkout import register_checkout_handlers
from vk_bot.handlers.menu import register_menu_handlers
from vk_bot.handlers.orders import register_orders_handlers
from vk_bot.handlers.registration import register_registration_handlers
from vk_bot.handlers.start import is_start_message, register_start_handlers

logger = logging.getLogger(__name__)


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


async def on_startup(api_client: StorefrontApiClient) -> None:
    try:
        health = await api_client.health()
        logger.info("Backend health: %s", health)
        meta = await api_client.get_meta()
        logger.info("Meta loaded: %s keys", list(meta.keys()))
    except Exception as exc:
        logger.warning("Backend not available at startup: %s", exc)

    logger.info(
        "VK callback buttons require Long Poll event «Действие с сообщением» (message_event). "
        "See docs/VK_BOT.md section 2.1"
    )


def create_bot(settings: Settings, api_client: StorefrontApiClient) -> Bot:
    bot = Bot(token=settings.vk_bot_token)
    photo_uploader = PhotoMessageUploader(bot.api)
    api_holder = {
        "api": api_client,
        "bot": bot,
        "settings": settings,
        "photo_uploader": photo_uploader,
    }

    register_start_handlers(bot, api_holder)
    register_registration_handlers(bot, api_holder)
    register_menu_handlers(bot, api_holder)
    register_catalog_handlers(bot, api_holder)
    register_cart_handlers(bot, api_holder)
    register_checkout_handlers(bot, api_holder)
    register_orders_handlers(bot, api_holder)
    register_message_event_fallback(bot)
    register_fallback_handler(bot, api_holder)

    return bot


def register_message_event_fallback(bot) -> None:
    """Логирует необработанные callback и снимает кружок загрузки."""
    from vkbottle import GroupEventType
    from vkbottle.bot import MessageEvent

    from vk_bot.utils_events import answer_callback, parse_event_payload

    @bot.on.raw_event(GroupEventType.MESSAGE_EVENT, MessageEvent)
    async def unhandled_message_event(event: MessageEvent):
        payload = parse_event_payload(event)
        logger.warning("Unhandled VK message_event payload=%s", payload)
        await answer_callback(event)


def register_fallback_handler(bot, api_holder: dict) -> None:
    from vkbottle.bot import Message
    from vkbottle.dispatch.rules.base import FuncRule

    @bot.on.message(FuncRule(lambda m: not is_start_message(m)))
    async def first_message_fallback(message: Message):
        state = await api_holder["bot"].state_dispenser.get(message.peer_id)
        if state is not None:
            return

        await handle_natural_order_message(message, api_holder)
