"""Вспомогательные функции VK-бота."""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from vkbottle import Keyboard

from vk_bot.constants import CHANNEL
from vk_bot.services.session import empty_session

if TYPE_CHECKING:
    from vkbottle.tools import VKAPI

_sessions: dict[str, dict] = {}


def channel() -> str:
    return CHANNEL


def user_display_name(first_name: str | None, last_name: str | None) -> str:
    parts = [part for part in [first_name or "", last_name or ""] if part]
    return " ".join(parts).strip() or "Покупатель"


def get_session(external_user_id: str) -> dict:
    session = _sessions.get(external_user_id)
    if session is None:
        session = empty_session(external_user_id)
        _sessions[external_user_id] = session
    elif session.get("external_user_id") != external_user_id:
        session = empty_session(external_user_id)
        _sessions[external_user_id] = session
    return session


def save_session(external_user_id: str, session: dict) -> None:
    _sessions[external_user_id] = session


def update_session(external_user_id: str, **kwargs) -> dict:
    session = get_session(external_user_id)
    session.update(kwargs)
    save_session(external_user_id, session)
    return session


def clear_checkout_state(external_user_id: str) -> None:
    update_session(
        external_user_id,
        receiving_type=None,
        delivery_address="",
        payment_method=None,
        customer_comment="",
        checkout_preview=None,
    )


def parse_decimal(value: str | Decimal) -> Decimal:
    return Decimal(str(value))


def basic_phone_check(phone: str) -> bool:
    digits = "".join(ch for ch in phone if ch.isdigit())
    return 10 <= len(digits) <= 15


def normalize_phone_input(phone: str) -> str:
    return "".join(ch for ch in phone if ch.isdigit())


async def send_message(
    api: VKAPI,
    peer_id: int,
    text: str,
    keyboard: Keyboard | None = None,
    *,
    attachment: str | None = None,
) -> int | None:
    message_id = await api.messages.send(
        peer_id=peer_id,
        message=text,
        keyboard=keyboard.get_json() if keyboard else None,
        attachment=attachment,
        random_id=0,
    )
    if isinstance(message_id, int):
        return message_id
    return getattr(message_id, "message_id", None)


async def edit_peer_message(
    api: VKAPI,
    peer_id: int,
    conversation_message_id: int,
    text: str,
    keyboard: Keyboard | None = None,
) -> None:
    await api.messages.edit(
        peer_id=peer_id,
        conversation_message_id=conversation_message_id,
        message=text,
        keyboard=keyboard.get_json() if keyboard else None,
    )


async def delete_peer_message(
    api: VKAPI,
    peer_id: int,
    conversation_message_id: int,
) -> None:
    await api.messages.delete(
        peer_id=peer_id,
        cmids=conversation_message_id,
        delete_for_all=1,
    )


def reset_sessions_for_tests() -> None:
    _sessions.clear()
