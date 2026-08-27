"""Команда старта и первичная идентификация."""
from __future__ import annotations

import logging

from vkbottle.bot import Message
from vkbottle.dispatch.rules.base import CommandRule, FuncRule

from vk_bot.api.errors import ApiError, BackendUnavailableError
from vk_bot.handlers.catalog import show_catalog
from vk_bot.handlers.common import answer_api_error, identify_without_phone
from vk_bot.handlers.registration import prompt_registration
from vk_bot.keyboards import main_menu_keyboard
from vk_bot.services.session import apply_identify_response, is_identified
from vk_bot.texts import START_FAILED, WELCOME_BACK
from vk_bot.utils import get_session, save_session, send_message

logger = logging.getLogger(__name__)

START_TEXTS = {"/start", "начать", "start", "Начать"}


def is_start_message(message: Message) -> bool:
    text = (message.text or "").strip()
    lowered = text.lower()
    return lowered in {t.lower() for t in START_TEXTS}


async def handle_start(message: Message, api_holder: dict) -> None:
    user_id = message.from_id
    peer_id = message.peer_id
    api = api_holder["api"]
    bot = api_holder["bot"]

    session = get_session(str(user_id))
    was_identified = is_identified(session)

    await bot.state_dispenser.delete(peer_id)

    try:
        response = await identify_without_phone(api, user_id)
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(message.ctx_api, peer_id, exc)
        return

    if response.get("status") == "identified":
        session = apply_identify_response(session, response)
        save_session(str(user_id), session)

        name = session.get("display_name") or "друг"
        if was_identified:
            await show_catalog(message.ctx_api, peer_id, user_id, api, api_holder)
            await send_message(message.ctx_api, peer_id, "Меню:", main_menu_keyboard())
            return

        await send_message(
            message.ctx_api,
            peer_id,
            WELCOME_BACK.format(name=name),
            main_menu_keyboard(),
        )
        await show_catalog(message.ctx_api, peer_id, user_id, api, api_holder)
        return

    if response.get("status") == "registration_required":
        await prompt_registration(message, bot)
        return

    await send_message(message.ctx_api, peer_id, START_FAILED, main_menu_keyboard())


def register_start_handlers(bot, api_holder: dict) -> None:
    @bot.on.message(FuncRule(is_start_message))
    async def start_handler(message: Message):
        await handle_start(message, api_holder)

    @bot.on.message(CommandRule("cancel"))
    async def cancel_handler(message: Message):
        user_id = str(message.from_id)
        session = get_session(user_id)
        await api_holder["bot"].state_dispenser.delete(message.peer_id)
        await send_message(
            message.ctx_api,
            message.peer_id,
            "Действие отменено.",
            main_menu_keyboard(),
        )
        save_session(user_id, session)
