import asyncio
import json

from vkbottle.dispatch.rules.base import PayloadRule

from vk_bot.rules import cmd_payload
from vk_bot.utils_events import parse_event_payload


class _FakePayloadEvent:
    def __init__(self, payload):
        self.payload = payload

    def get_payload_json(self):
        return self.payload


def test_cmd_payload_matches_extra_fields():
    rule = cmd_payload("prod_inc")
    event = _FakePayloadEvent({"cmd": "prod_inc", "id": 42})
    assert asyncio.run(rule.check(event)) is True


def test_cmd_payload_matches_json_string():
    rule = cmd_payload("prod_add")
    event = _FakePayloadEvent(json.dumps({"cmd": "prod_add", "id": 7}))
    assert asyncio.run(rule.check(event)) is True


def test_cmd_payload_rejects_other_cmd():
    rule = cmd_payload("prod_inc")
    event = _FakePayloadEvent({"cmd": "prod_dec", "id": 42})
    assert asyncio.run(rule.check(event)) is False


def test_payload_rule_does_not_match_with_id():
    rule = PayloadRule({"cmd": "prod_inc"})
    event = _FakePayloadEvent({"cmd": "prod_inc", "id": 42})
    assert asyncio.run(rule.check(event)) is False


def test_parse_event_payload_from_object():
    class Obj:
        payload = {"cmd": "prod_inc", "id": 3}

    class Event:
        object = Obj()

    assert parse_event_payload(Event()) == {"cmd": "prod_inc", "id": 3}
