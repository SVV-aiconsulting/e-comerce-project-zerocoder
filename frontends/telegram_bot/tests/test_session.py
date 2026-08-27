import pytest

from bot.services.session import apply_identify_response, is_identified


@pytest.mark.asyncio
async def test_session_apply_identify():
    session = {"external_user_id": "1"}
    response = {
        "customer_id": 42,
        "customer_public_code": "CL-TEST",
        "is_new_customer": True,
        "display_name": "Иван",
    }
    apply_identify_response(session, response)
    assert session["customer_id"] == 42
    assert session["customer_public_code"] == "CL-TEST"
    assert is_identified(session)
