"""Главное меню и навигация."""
from __future__ import annotations

from vkbottle.bot import Message
from vkbottle.dispatch.rules.base import FuncRule

from vk_bot.api.errors import ApiError, BackendUnavailableError
from vk_bot.constants import MENU_CART, MENU_CATALOG, MENU_HELP, MENU_ORDERS
from vk_bot.handlers.cart import show_cart
from vk_bot.handlers.catalog import show_catalog
from vk_bot.handlers.common import answer_api_error, ensure_identified
from vk_bot.services.error_messages import NOT_IDENTIFIED_MESSAGE
from vk_bot.handlers.orders import show_orders_list
from vk_bot.keyboards import main_menu_keyboard
from vk_bot.texts import HELP_TEXT
from vk_bot.utils import send_message


async def _handle_menu_action(message: Message, api_holder: dict, action: str) -> None:
    user_id = message.from_id
    peer_id = message.peer_id
    api = api_holder["api"]
    try:
        session = await ensure_identified(api, user_id)
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(message.ctx_api, peer_id, exc)
        return

    if session is None:
        await send_message(message.ctx_api, peer_id, NOT_IDENTIFIED_MESSAGE)
        return

    if action == "catalog":
        await show_catalog(message.ctx_api, peer_id, user_id, api, api_holder)
    elif action == "cart":
        await show_cart(message.ctx_api, peer_id, user_id, api)
    elif action == "orders":
        await show_orders_list(message.ctx_api, peer_id, user_id, api)


def register_menu_handlers(bot, api_holder: dict) -> None:
    from vkbottle import GroupEventType
    from vkbottle.bot import MessageEvent
    from vk_bot.rules import cmd_payload
    from vk_bot.utils_events import answer_callback

    @bot.on.message(FuncRule(lambda m: (m.text or "").strip() == MENU_CATALOG))
    async def menu_catalog(message: Message):
        await _handle_menu_action(message, api_holder, "catalog")

    @bot.on.message(FuncRule(lambda m: (m.text or "").strip() == MENU_CART))
    async def menu_cart(message: Message):
        await _handle_menu_action(message, api_holder, "cart")

    @bot.on.message(FuncRule(lambda m: (m.text or "").strip() == MENU_ORDERS))
    async def menu_orders(message: Message):
        await _handle_menu_action(message, api_holder, "orders")

    @bot.on.message(FuncRule(lambda m: (m.text or "").strip() == MENU_HELP))
    async def menu_help(message: Message):
        await send_message(message.ctx_api, message.peer_id, HELP_TEXT, main_menu_keyboard())

    @bot.on.raw_event(GroupEventType.MESSAGE_EVENT, MessageEvent, cmd_payload("menu"))
    async def menu_callback(event: MessageEvent):
        await send_message(
            event.ctx_api,
            event.peer_id,
            "Меню:",
            main_menu_keyboard(),
        )
        await answer_callback(event)
