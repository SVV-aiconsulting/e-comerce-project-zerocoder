import json

import httpx
import pytest

from apps.payments.exceptions import PaymentConfigurationError, YooKassaAPIError
from apps.payments.models import PaymentEnvironment
from apps.payments.yookassa.client import YooKassaClient, YooKassaConfig


def config(**overrides):
    values = {
        "enabled": True,
        "environment": PaymentEnvironment.TEST,
        "shop_id": "test-shop",
        "secret_key": "test-secret",
        "return_url": "http://localhost:8000/payment/return/",
        "timeout_seconds": 5,
    }
    values.update(overrides)
    return YooKassaConfig(**values)


def test_create_payment_uses_basic_auth_and_idempotence_key():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.yookassa.ru/v3/payments"
        assert request.headers["Idempotence-Key"] == "same-request"
        assert request.headers["Authorization"].startswith("Basic ")
        assert json.loads(request.content)["confirmation"]["type"] == "redirect"
        return httpx.Response(200, json={"id": "pay-1", "status": "pending"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = YooKassaClient(config(), http_client=http_client)
        result = client.create_payment(
            {"confirmation": {"type": "redirect"}},
            idempotence_key="same-request",
        )
    assert result["id"] == "pay-1"


def test_post_without_idempotence_key_is_refused_locally():
    client = YooKassaClient(config())
    with pytest.raises(PaymentConfigurationError, match="Idempotence-Key"):
        client._request("POST", "/payments", payload={})


def test_client_does_not_expose_secret_in_provider_error():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": "invalid_credentials", "description": "Denied"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = YooKassaClient(config(secret_key="super-secret"), http_client=http_client)
        with pytest.raises(YooKassaAPIError) as error:
            client.get_payment("pay-1")
    assert "super-secret" not in str(error.value)
    assert error.value.code == "invalid_credentials"


def test_production_requires_explicit_protection_flag():
    with pytest.raises(PaymentConfigurationError, match="PRODUCTION_ENABLED"):
        config(environment=PaymentEnvironment.PRODUCTION).validate()
