import httpx
import pytest

from bot.api.client import StorefrontApiClient
from bot.api.errors import ApiError, BackendUnavailableError

BASE_URL = "http://test-api"
TOKEN = "test-token"


@pytest.fixture
def client():
    return StorefrontApiClient(BASE_URL, TOKEN)


@pytest.mark.asyncio
async def test_health(client, respx_mock):
    respx_mock.get(f"{BASE_URL}/api/health/").respond(json={"status": "успешно"})
    result = await client.health()
    assert result["status"] == "успешно"


@pytest.mark.asyncio
async def test_identify_registration_required(client, respx_mock):
    route = respx_mock.post(f"{BASE_URL}/api/identify-customer/").respond(
        json={
            "status": "registration_required",
            "registration_required": True,
            "next_action": "request_phone",
        }
    )
    result = await client.identify_customer(
        {"channel": "telegram", "external_user_id": "123"}
    )
    assert result["status"] == "registration_required"
    assert route.calls[0].request.headers["X-Adapter-Token"] == TOKEN


@pytest.mark.asyncio
async def test_identify_identified(client, respx_mock):
    respx_mock.post(f"{BASE_URL}/api/identify-customer/").respond(
        json={
            "status": "identified",
            "customer_id": 1,
            "customer_public_code": "CL-ABC",
            "is_new_customer": True,
        }
    )
    result = await client.identify_customer(
        {
            "channel": "telegram",
            "external_user_id": "123",
            "phone": "+79123456789",
            "phone_verification_source": "platform_contact",
        }
    )
    assert result["customer_id"] == 1


@pytest.mark.asyncio
async def test_identify_conflict_returns_body(client, respx_mock):
    respx_mock.post(f"{BASE_URL}/api/identify-customer/").respond(
        status_code=409,
        json={"status": "conflict", "next_action": "conflict message"},
    )
    result = await client.identify_customer(
        {"channel": "telegram", "external_user_id": "123", "phone": "79991234567"}
    )
    assert result["status"] == "conflict"


@pytest.mark.asyncio
async def test_submit_and_read_inbound_event(client, respx_mock):
    submit = respx_mock.post(f"{BASE_URL}/api/intake/events/").respond(
        status_code=202,
        json={"event_id": "event-uuid", "status": "queued"},
    )
    detail = respx_mock.get(f"{BASE_URL}/api/intake/events/event-uuid/").respond(
        json={
            "event_id": "event-uuid",
            "status": "processed",
            "complete": True,
            "response": {"type": "clarification", "message": "Уточните товар"},
        }
    )

    created = await client.submit_inbound_event(
        {
            "channel": "telegram",
            "external_event_id": "1:2",
            "external_user_id": "123",
            "conversation_key": "1",
            "raw_text": "две упаковки креветок",
        }
    )
    result = await client.get_inbound_event(
        created["event_id"],
        channel="telegram",
        external_user_id="123",
    )

    assert result["complete"] is True
    assert submit.calls[0].request.headers["X-Adapter-Token"] == TOKEN
    assert detail.calls[0].request.url.params["external_user_id"] == "123"


@pytest.mark.asyncio
async def test_identify_phone_validation_error(client, respx_mock):
    respx_mock.post(f"{BASE_URL}/api/identify-customer/").respond(
        status_code=400,
        json={
            "error": {
                "code": "validation_error",
                "message": "Ошибка валидации данных",
                "details": {"phone": ["Телефон должен быть указан в формате 79991234567"]},
            }
        },
    )
    with pytest.raises(ApiError) as exc_info:
        await client.identify_customer(
            {"channel": "telegram", "external_user_id": "123", "phone": "bad"}
        )
    assert exc_info.value.code == "validation_error"
    assert "phone" in exc_info.value.details


@pytest.mark.asyncio
async def test_list_products(client, respx_mock):
    respx_mock.get(f"{BASE_URL}/api/products/").respond(json=[{"id": 1, "name": "Товар"}])
    products = await client.list_products()
    assert len(products) == 1


@pytest.mark.asyncio
async def test_set_cart_item(client, respx_mock):
    route = respx_mock.put(f"{BASE_URL}/api/cart/items/1/").respond(
        json={"items_total": "200.00", "items": []}
    )
    result = await client.set_cart_item(
        1,
        channel="telegram",
        external_user_id="123",
        customer_id=1,
        quantity="2.000",
    )
    assert result["items_total"] == "200.00"
    import json

    body = json.loads(route.calls[0].request.content)
    assert body["customer_id"] == 1


@pytest.mark.asyncio
async def test_checkout_preview(client, respx_mock):
    respx_mock.post(f"{BASE_URL}/api/checkout/preview/").respond(
        json={
            "items_total": "200.00",
            "discount_amount": "0.00",
            "delivery_cost": "300.00",
            "total_amount": "500.00",
            "free_delivery": False,
        }
    )
    preview = await client.checkout_preview(
        channel="telegram",
        external_user_id="123",
        customer_id=1,
        receiving_type="delivery",
    )
    assert preview["total_amount"] == "500.00"


@pytest.mark.asyncio
async def test_create_order(client, respx_mock):
    respx_mock.post(f"{BASE_URL}/api/orders/").respond(
        status_code=201,
        json={"public_number": "WM-001", "total_amount": "500.00"},
    )
    order = await client.create_order(
        {
            "channel": "telegram",
            "external_user_id": "123",
            "customer_id": 1,
            "receiving_type": "delivery",
            "payment_method": "cash_on_delivery",
        }
    )
    assert order["public_number"] == "WM-001"


@pytest.mark.asyncio
async def test_list_customer_orders(client, respx_mock):
    respx_mock.get(f"{BASE_URL}/api/customers/CL-ABC/orders/").respond(
        json=[{"public_number": "WM-001", "total_amount": "500.00"}]
    )
    orders = await client.list_customer_orders(
        "CL-ABC",
        channel="telegram",
        external_user_id="123",
    )
    assert orders[0]["public_number"] == "WM-001"


@pytest.mark.asyncio
async def test_customer_context_mismatch(client, respx_mock):
    respx_mock.get(f"{BASE_URL}/api/cart/").respond(
        status_code=409,
        json={
            "error": {
                "code": "customer_context_mismatch",
                "message": "customer_id не соответствует",
                "details": {},
            }
        },
    )
    with pytest.raises(ApiError) as exc_info:
        await client.get_cart(channel="telegram", external_user_id="123", customer_id=99)
    assert exc_info.value.code == "customer_context_mismatch"


@pytest.mark.asyncio
async def test_backend_unavailable(client, respx_mock):
    respx_mock.get(f"{BASE_URL}/api/health/").mock(side_effect=httpx.ConnectError("failed"))

    with pytest.raises(BackendUnavailableError):
        await client.health()


@pytest.mark.asyncio
async def test_invalid_json_response(client, respx_mock):
    respx_mock.get(f"{BASE_URL}/api/health/").respond(
        status_code=200,
        content=b"<html>not json</html>",
        headers={"Content-Type": "text/html"},
    )

    with pytest.raises(BackendUnavailableError):
        await client.health()
