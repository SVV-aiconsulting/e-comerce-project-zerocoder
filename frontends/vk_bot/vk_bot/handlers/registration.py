"""Регистрация по номеру телефона."""
from __future__ import annotations

import logging

from vkbottle.bot import Message

from vk_bot.api.errors import ApiError, BackendUnavailableError
from vk_bot.constants import CONFLICT_USER_MESSAGE
from vk_bot.handlers.catalog import show_catalog
from vk_bot.handlers.common import answer_api_error
from vk_bot.keyboards import main_menu_keyboard
from vk_bot.services.error_messages import is_phone_validation_error
from vk_bot.services.session import apply_identify_response
from vk_bot.states import RegistrationStates
from vk_bot.texts import REGISTRATION_PROMPT, REGISTRATION_RETRY, REGISTRATION_SUCCESS
from vk_bot.utils import (
    basic_phone_check,
    channel,
    get_session,
    normalize_phone_input,
    save_session,
    send_message,
)

logger = logging.getLogger(__name__)


async def prompt_registration(message: Message, bot) -> None:
    await bot.state_dispenser.set(message.peer_id, RegistrationStates.WAITING_PHONE)
    await send_message(message.ctx_api, message.peer_id, REGISTRATION_PROMPT)


async def handle_phone_registration(message: Message, api_holder: dict) -> None:
    phone_raw = (message.text or "").strip()
    user_id = message.from_id
    peer_id = message.peer_id
    api = api_holder["api"]
    bot = api_holder["bot"]

    if not basic_phone_check(phone_raw):
        await send_message(message.ctx_api, peer_id, REGISTRATION_RETRY)
        return

    session = get_session(str(user_id))
    phone = normalize_phone_input(phone_raw)
    payload = {
        "channel": channel(),
        "external_user_id": str(user_id),
        "phone": phone,
        "phone_verification_source": "manual_input",
        "display_name": session.get("display_name") or "Покупатель",
    }

    try:
        response = await api.identify_customer(payload)
    except ApiError as exc:
        if exc.code == "channel_identity_conflict" or (
            exc.status_code == 409 and exc.code in {"channel_identity_conflict", "api_error"}
        ):
            await bot.state_dispenser.delete(peer_id)
            await send_message(message.ctx_api, peer_id, CONFLICT_USER_MESSAGE)
            return
        if is_phone_validation_error(exc):
            await send_message(message.ctx_api, peer_id, REGISTRATION_RETRY)
            return
        await answer_api_error(message.ctx_api, peer_id, exc)
        return
    except BackendUnavailableError as exc:
        await answer_api_error(message.ctx_api, peer_id, exc)
        return

    if response.get("status") == "conflict":
        await bot.state_dispenser.delete(peer_id)
        await send_message(message.ctx_api, peer_id, CONFLICT_USER_MESSAGE)
        return

    if response.get("status") != "identified":
        await send_message(message.ctx_api, peer_id, REGISTRATION_RETRY)
        return

    session = apply_identify_response(session, response)
    save_session(str(user_id), session)
    await bot.state_dispenser.delete(peer_id)

    name = session.get("display_name") or "друг"
    await send_message(
        message.ctx_api,
        peer_id,
        REGISTRATION_SUCCESS.format(name=name),
        main_menu_keyboard(),
    )
    await show_catalog(message.ctx_api, peer_id, user_id, api, api_holder)


def register_registration_handlers(bot, api_holder: dict) -> None:
    @bot.on.message(state=RegistrationStates.WAITING_PHONE)
    async def phone_handler(message: Message):
        await handle_phone_registration(message, api_holder)
