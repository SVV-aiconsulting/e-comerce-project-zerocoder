"""Правила маршрутизации callback-кнопок VK."""
from typing import Any

from vkbottle.dispatch.rules.base import ABCRule

from vk_bot.utils_events import parse_event_payload


class CmdPayloadRule(ABCRule):
    """Совпадение по полю cmd; дополнительные поля payload (id и т.д.) допускаются."""

    def __init__(self, cmd: str):
        self.cmd = cmd

    async def check(self, event) -> bool:
        payload = parse_event_payload(event)
        return isinstance(payload, dict) and payload.get("cmd") == self.cmd


def cmd_payload(cmd: str) -> CmdPayloadRule:
    return CmdPayloadRule(cmd)
