from decimal import Decimal

import httpx
import pytest

from apps.delivery.exceptions import DeliveryConfigurationError, YandexDeliveryAPIError
from apps.delivery.models import DeliveryEnvironment
from apps.delivery.yandex.client import (
    TEST_BASE_URL,
    YandexDeliveryClient,
    YandexDeliveryConfig,
    parse_money,
)


def config(**overrides) -> YandexDeliveryConfig:
    values = {
        "enabled": True,
        "environment": DeliveryEnvironment.TEST,
        "token": "test-token",
        "station_id": "test-station",
        "timeout_seconds": 5.0,
    }
    values.update(overrides)
    return YandexDeliveryConfig(**values)


def test_parse_money_uses_decimal():
    assert parse_money("225.7 RUB") == (Decimal("225.70"), "RUB")


def test_production_requires_explicit_safety_switch():
    production = config(
        environment=DeliveryEnvironment.PRODUCTION,
        production_enabled=False,
    )

    with pytest.raises(DeliveryConfigurationError, match="PRODUCTION_ENABLED"):
        production.validate()


def test_calculate_price_uses_locked_test_host_and_bearer_header():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            200,
            json={"pricing_total": "225.7 RUB", "delivery_days": 7},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = YandexDeliveryClient(
            config(),
            http_client=http_client,
        ).calculate_price({"source": {"platform_station_id": "test-station"}})

    request = captured["request"]
    assert str(request.url).startswith(
        f"{TEST_BASE_URL}/api/b2b/platform/pricing-calculator"
    )
    assert request.headers["Authorization"] == "Bearer test-token"
    assert request.url.params["is_oversized"] == "false"
    assert result.amount == Decimal("225.70")
    assert result.delivery_days == 7


def test_api_error_keeps_structured_details_without_token_in_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": "unauthorized", "message": "Bad token"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = YandexDeliveryClient(config(token="secret-token"), http_client=http_client)
        with pytest.raises(YandexDeliveryAPIError) as exc_info:
            client.calculate_price({})

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "unauthorized"
    assert "secret-token" not in str(exc_info.value)


def test_confirm_info_and_cancel_use_documented_methods():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/offers/confirm"):
            return httpx.Response(200, json={"request_id": "request-1"})
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"request_id": "request-1", "state": {"status": "CREATED"}},
            )
        return httpx.Response(
            200,
            json={"status": "SUCCESS", "description": "Заказ отменён"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = YandexDeliveryClient(config(), http_client=http_client)
        assert client.confirm_offer("offer-1") == "request-1"
        assert client.get_request_info("request-1")["state"]["status"] == "CREATED"
        assert client.cancel_request("request-1")["status"] == "SUCCESS"

    assert requests[0].method == "POST"
    assert requests[0].url.path.endswith("/offers/confirm")
    assert requests[1].method == "GET"
    assert requests[1].url.params["request_id"] == "request-1"
    assert requests[2].method == "POST"
    assert requests[2].url.path.endswith("/request/cancel")


def test_detect_location_returns_only_object_variants():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "variants": [
                    {"geo_id": 213, "address": "Москва"},
                    "invalid",
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        variants = YandexDeliveryClient(
            config(),
            http_client=http_client,
        ).detect_location("Москва")

    assert variants == [{"geo_id": 213, "address": "Москва"}]
