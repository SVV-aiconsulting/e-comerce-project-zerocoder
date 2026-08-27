"""Сборка и запуск Telegram-бота."""
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from bot.api.client import StorefrontApiClient
from bot.config import Settings
from bot.handlers import (
    ai_orders,
    cart,
    catalog,
    checkout,
    menu,
    orders,
    product,
    registration,
    start,
)
from bot.middlewares.api_client import ApiClientMiddleware, PrivateChatMiddleware

logger = logging.getLogger(__name__)


def create_dispatcher(settings: Settings, api_client: StorefrontApiClient) -> Dispatcher:
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    dp.update.middleware(PrivateChatMiddleware())
    dp.update.middleware(ApiClientMiddleware(api_client, settings))

    dp.include_router(start.router)
    dp.include_router(registration.router)
    dp.include_router(menu.router)
    dp.include_router(catalog.router)
    dp.include_router(product.router)
    dp.include_router(cart.router)
    dp.include_router(checkout.router)
    dp.include_router(orders.router)
    # Catch-all свободного текста должен оставаться последним: FSM и меню приоритетнее.
    dp.include_router(ai_orders.router)

    return dp


async def on_startup(api_client: StorefrontApiClient) -> None:
    try:
        health = await api_client.health()
        logger.info("Backend health: %s", health)
        meta = await api_client.get_meta()
        logger.info("Meta loaded: %s keys", list(meta.keys()))
    except Exception as exc:
        logger.warning("Backend not available at startup: %s", exc)


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
