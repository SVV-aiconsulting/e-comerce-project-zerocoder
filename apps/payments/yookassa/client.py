"""Тонкий JSON-клиент ЮKassa без ORM и бизнес-правил."""
from dataclasses import dataclass
from typing import Any

from django.conf import settings
import httpx

from apps.payments.exceptions import PaymentConfigurationError, YooKassaAPIError
from apps.payments.models import PaymentEnvironment

BASE_URL = "https://api.yookassa.ru/v3"


@dataclass(frozen=True)
class YooKassaConfig:
    enabled: bool
    environment: str
    shop_id: str
    secret_key: str
    return_url: str
    timeout_seconds: float
    production_enabled: bool = False

    @classmethod
    def from_django_settings(cls) -> "YooKassaConfig":
        environment = settings.YOOKASSA_ENVIRONMENT
        if environment == PaymentEnvironment.TEST:
            shop_id = settings.YOOKASSA_TEST_SHOP_ID
            secret_key = settings.YOOKASSA_TEST_SECRET_KEY
        elif environment == PaymentEnvironment.PRODUCTION:
            shop_id = settings.YOOKASSA_PRODUCTION_SHOP_ID
            secret_key = settings.YOOKASSA_PRODUCTION_SECRET_KEY
        else:
            shop_id = ""
            secret_key = ""
        return cls(
            enabled=settings.YOOKASSA_ENABLED,
            environment=environment,
            shop_id=shop_id,
            secret_key=secret_key,
            return_url=settings.YOOKASSA_RETURN_URL,
            timeout_seconds=settings.YOOKASSA_TIMEOUT_SECONDS,
            production_enabled=settings.YOOKASSA_PRODUCTION_ENABLED,
        )

    def validate(self, *, require_enabled: bool = True) -> None:
        if require_enabled and not self.enabled:
            raise PaymentConfigurationError("Интеграция ЮKassa выключена")
        if self.environment not in PaymentEnvironment.values:
            raise PaymentConfigurationError(
                "YOOKASSA_ENVIRONMENT должен быть test или production"
            )
        if self.environment == PaymentEnvironment.PRODUCTION and not self.production_enabled:
            raise PaymentConfigurationError(
                "Коммерческий магазин требует YOOKASSA_PRODUCTION_ENABLED=True"
            )
        if not self.shop_id:
            variable = (
                "YOOKASSA_TEST_SHOP_ID"
                if self.environment == PaymentEnvironment.TEST
                else "YOOKASSA_PRODUCTION_SHOP_ID"
            )
            raise PaymentConfigurationError(f"Не задан {variable}")
        if not self.secret_key:
            variable = (
                "YOOKASSA_TEST_SECRET_KEY"
                if self.environment == PaymentEnvironment.TEST
                else "YOOKASSA_PRODUCTION_SECRET_KEY"
            )
            raise PaymentConfigurationError(f"Не задан {variable}")
        if not self.return_url.startswith(("http://", "https://")):
            raise PaymentConfigurationError(
                "YOOKASSA_RETURN_URL должен быть абсолютным http(s)-адресом"
            )
        if (
            self.environment == PaymentEnvironment.PRODUCTION
            and not self.return_url.startswith("https://")
        ):
            raise PaymentConfigurationError(
                "В production YOOKASSA_RETURN_URL должен использовать HTTPS"
            )
        if self.timeout_seconds <= 0:
            raise PaymentConfigurationError(
                "YOOKASSA_TIMEOUT_SECONDS должен быть больше нуля"
            )


class YooKassaClient:
    """Клиент API v3 с обязательным ключом идемпотентности для POST."""

    def __init__(
        self,
        config: YooKassaConfig | None = None,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.config = config or YooKassaConfig.from_django_settings()
        self.http_client = http_client

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        idempotence_key: str | None = None,
    ) -> dict[str, Any]:
        self.config.validate()
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if method in {"POST", "DELETE"}:
            if not idempotence_key:
                raise PaymentConfigurationError(
                    "Изменяющий запрос ЮKassa требует Idempotence-Key"
                )
            headers["Idempotence-Key"] = idempotence_key
        url = f"{BASE_URL}{path}"
        try:
            if self.http_client is not None:
                response = self.http_client.request(
                    method,
                    url,
                    json=payload,
                    headers=headers,
                    auth=(self.config.shop_id, self.config.secret_key),
                )
            else:
                with httpx.Client(
                    timeout=self.config.timeout_seconds,
                    follow_redirects=False,
                ) as client:
                    response = client.request(
                        method,
                        url,
                        json=payload,
                        headers=headers,
                        auth=(self.config.shop_id, self.config.secret_key),
                    )
        except httpx.RequestError as exc:
            raise YooKassaAPIError(
                "Не удалось подключиться к API ЮKassa", retryable=True
            ) from exc

        try:
            response_payload = response.json()
        except ValueError as exc:
            raise YooKassaAPIError(
                "ЮKassa вернула ответ не в JSON",
                status_code=response.status_code,
                retryable=response.status_code >= 500,
            ) from exc
        if not isinstance(response_payload, dict):
            raise YooKassaAPIError(
                "ЮKassa вернула JSON неизвестной структуры",
                status_code=response.status_code,
                retryable=response.status_code >= 500,
            )
        if response.is_error:
            description = str(
                response_payload.get("description")
                or response_payload.get("message")
                or "Ошибка API ЮKassa"
            )
            raise YooKassaAPIError(
                description,
                status_code=response.status_code,
                code=str(response_payload.get("code", "")),
                response_payload=response_payload,
                retryable=response.status_code == 429 or response.status_code >= 500,
            )
        return response_payload

    def create_payment(
        self, payload: dict[str, Any], *, idempotence_key: str
    ) -> dict[str, Any]:
        return self._request(
            "POST", "/payments", payload=payload, idempotence_key=idempotence_key
        )

    def get_payment(self, external_id: str) -> dict[str, Any]:
        return self._request("GET", f"/payments/{external_id}")

    def list_payments(self, *, limit: int = 1) -> dict[str, Any]:
        """Read-only проверка реквизитов; не создаёт оплату или счёт."""

        if not 1 <= limit <= 100:
            raise PaymentConfigurationError("limit должен быть от 1 до 100")
        return self._request("GET", f"/payments?limit={limit}")

    def cancel_payment(self, external_id: str, *, idempotence_key: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/payments/{external_id}/cancel",
            payload={},
            idempotence_key=idempotence_key,
        )

    def create_refund(
        self, payload: dict[str, Any], *, idempotence_key: str
    ) -> dict[str, Any]:
        return self._request(
            "POST", "/refunds", payload=payload, idempotence_key=idempotence_key
        )
