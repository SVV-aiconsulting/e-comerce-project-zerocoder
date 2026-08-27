import pytest

from vk_bot.services.session import apply_identify_response, empty_session, is_identified
from vk_bot.utils import basic_phone_check, normalize_phone_input, reset_sessions_for_tests


@pytest.fixture(autouse=True)
def clear_sessions():
    reset_sessions_for_tests()
    yield
    reset_sessions_for_tests()


def test_identify_session_apply():
    session = empty_session("42")
    assert not is_identified(session)
    session = apply_identify_response(
        session,
        {
            "customer_id": 7,
            "customer_public_code": "CL-VK",
            "is_new_customer": True,
        },
    )
    assert is_identified(session)
    assert session["customer_public_code"] == "CL-VK"


def test_phone_normalization():
    assert normalize_phone_input("+7 (999) 123-45-67") == "79991234567"
    assert basic_phone_check("79991234567")


def test_identify_payload_channel():
    from vk_bot.utils import channel

    assert channel() == "vk"
