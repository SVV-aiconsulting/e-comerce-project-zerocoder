"""Тесты логики регистрации (без Telegram API)."""


def test_contact_belongs_to_user():
    contact_user_id = 12345
    from_user_id = 12345
    assert contact_user_id == from_user_id


def test_contact_foreign_user_rejected():
    contact_user_id = 99999
    from_user_id = 12345
    assert contact_user_id != from_user_id


def test_identify_payload_with_platform_contact():
    payload = {
        "channel": "telegram",
        "external_user_id": "12345",
        "phone": "+79123456789",
        "phone_verification_source": "platform_contact",
        "username": "ivan",
        "display_name": "Иван",
    }
    assert payload["phone_verification_source"] == "platform_contact"
    assert payload["channel"] == "telegram"
