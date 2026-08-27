"""Ответы на нажатия inline-кнопок VK (message_event)."""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["answer_callback", "parse_event_payload"]


def _normalize_payload(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def parse_event_payload(event) -> dict[str, Any] | None:
    raw = getattr(event, "payload", None)
    if raw is not None:
        return _normalize_payload(raw)

    obj = getattr(event, "object", None)
    if obj is not None:
        return _normalize_payload(getattr(obj, "payload", None))

    getter = getattr(event, "get_payload_json", None)
    if callable(getter):
        return _normalize_payload(getter())
    return None


def _event_ids(event) -> tuple[str, int, int]:
    event_id = getattr(event, "event_id", None)
    user_id = getattr(event, "user_id", None)
    peer_id = getattr(event, "peer_id", None)
    if event_id is None or user_id is None or peer_id is None:
        obj = getattr(event, "object", None)
        if obj is not None:
            event_id = event_id or obj.event_id
            user_id = user_id or obj.user_id
            peer_id = peer_id or obj.peer_id
    return event_id, user_id, peer_id


async def answer_callback(event, *, snackbar: str | None = None) -> None:
    """Подтвердить нажатие callback-кнопки, чтобы убрать кружок загрузки."""
    try:
        if snackbar and hasattr(event, "show_snackbar"):
            await event.show_snackbar(snackbar)
            return
        if hasattr(event, "send_empty_answer"):
            await event.send_empty_answer()
            return
    except Exception:
        logger.exception("Failed to answer callback via event helpers, payload=%s", parse_event_payload(event))

    event_id, user_id, peer_id = _event_ids(event)
    event_data = json.dumps({"type": "show_snackbar", "text": snackbar}, ensure_ascii=False) if snackbar else None
    await event.ctx_api.messages.send_message_event_answer(
        event_id=event_id,
        user_id=user_id,
        peer_id=peer_id,
        event_data=event_data,
    )
