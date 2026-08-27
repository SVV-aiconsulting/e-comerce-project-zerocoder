"""Повторная идентификация пользователя через API."""
from aiogram.types import CallbackQuery, Message, User

from bot.api.client import StorefrontApiClient
from bot.api.errors import ApiError, BackendUnavailableError
from bot.constants import CHANNEL
from bot.services.error_messages import NOT_IDENTIFIED_MESSAGE
from bot.services.session import apply_identify_response, get_session, is_identified, save_session


def _user_display_name(user: User) -> str:
    return (
        " ".join(part for part in [user.first_name or "", user.last_name or ""] if part).strip()
        or (user.username or "Покупатель")
    )


async def identify_without_phone(api: StorefrontApiClient, user: User) -> dict:
    payload = {
        "channel": CHANNEL,
        "external_user_id": str(user.id),
        "username": user.username or "",
        "display_name": _user_display_name(user),
    }
    return await api.identify_customer(payload)


async def ensure_identified(
    actor: Message | CallbackQuery,
    state,
    api: StorefrontApiClient,
) -> dict | None:
    """Вернуть session при успехе, None если нужна регистрация.

    ApiError и BackendUnavailableError пробрасываются наверх.
    """
    user = actor.from_user
    external_user_id = str(user.id)
    session = await get_session(state, external_user_id)
    session["username"] = user.username or ""
    session["display_name"] = _user_display_name(user)

    if is_identified(session):
        await save_session(state, session)
        return session

    response = await identify_without_phone(api, user)

    if response.get("status") != "identified":
        return None

    session = apply_identify_response(session, response)
    await save_session(state, session)
    return session


async def require_identified_callback(
    callback: CallbackQuery,
    state,
    api: StorefrontApiClient,
) -> dict | None:
    """Идентификация для inline callback; при ошибке отвечает пользователю."""
    from bot.handlers.common import answer_api_error

    try:
        session = await ensure_identified(callback, state, api)
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(callback, exc)
        await callback.answer()
        return None

    if session is None:
        await callback.message.answer(NOT_IDENTIFIED_MESSAGE)
        await callback.answer()
        return None

    return session
