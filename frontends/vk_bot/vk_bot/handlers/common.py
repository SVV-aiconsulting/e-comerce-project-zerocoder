"""Общие обработчики ошибок и идентификации."""
from __future__ import annotations

import logging

from vk_bot.api.client import StorefrontApiClient
from vk_bot.api.errors import ApiError, BackendUnavailableError
from vk_bot.services.error_messages import NOT_IDENTIFIED_MESSAGE, user_message_for_error
from vk_bot.services.session import apply_identify_response, is_identified
from vk_bot.utils import channel, get_session, save_session, send_message, user_display_name

logger = logging.getLogger(__name__)


async def answer_api_error(api, peer_id: int, exc: Exception) -> None:
    logger.warning("API error: %s", exc)
    await send_message(api, peer_id, user_message_for_error(exc))


async def identify_without_phone(
    api: StorefrontApiClient,
    user_id: int,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
) -> dict:
    session = get_session(str(user_id))
    display_name = user_display_name(first_name, last_name)
    session["display_name"] = display_name
    save_session(str(user_id), session)

    payload = {
        "channel": channel(),
        "external_user_id": str(user_id),
        "display_name": display_name,
    }
    return await api.identify_customer(payload)


async def ensure_identified(
    api: StorefrontApiClient,
    user_id: int,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
) -> dict | None:
    session = get_session(str(user_id))
    if is_identified(session):
        return session

    response = await identify_without_phone(
        api,
        user_id,
        first_name=first_name,
        last_name=last_name,
    )
    if response.get("status") != "identified":
        return None

    session = apply_identify_response(session, response)
    save_session(str(user_id), session)
    return session
