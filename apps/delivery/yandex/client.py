"""Тонкий HTTP-клиент API Яндекс Доставки по России."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from django.conf import settings
import httpx

from apps.delivery.exceptions import (
    DeliveryConfigurationError,
    YandexDeliveryAPIError,
)
from apps.delivery.models import DeliveryEnvironment

TEST_BASE_URL = "https://b2b.taxi.tst.yandex.net"
PRODUCTION_BASE_URL = "https://b2b-authproxy.taxi.yandex.net"
MONEY_RE = re.compile(r"^\s*(?P<amount>\d+(?:[.,]\d+)?)\s+(?P<currency>[A-Z]{3})\s*$")


@dataclass(frozen=True)
class YandexDeliveryConfig:
    """Конфигурация с раздельными реквизитами test и production."""

    enabled: bool
    environment: str
    token: str
    station_id: str
    timeout_seconds: float
    production_enabled: bool = False

    @classmethod
    def from_django_settings(cls) -> "YandexDeliveryConfig":
        environment = settings.YANDEX_DELIVERY_ENVIRONMENT
        if environment == DeliveryEnvironment.TEST:
            token = settings.YANDEX_DELIVERY_TEST_TOKEN
            station_id = settings.YANDEX_DELIVERY_TEST_STATION_ID
        elif environment == DeliveryEnvironment.PRODUCTION:
            token = settings.YANDEX_DELIVERY_PRODUCTION_TOKEN
            station_id = settings.YANDEX_DELIVERY_PRODUCTION_STATION_ID
        else:
            token = ""
            station_id = ""

        return cls(
            enabled=settings.YANDEX_DELIVERY_ENABLED,
            environment=environment,
            token=token,
            station_id=station_id,
            timeout_seconds=settings.YANDEX_DELIVERY_TIMEOUT_SECONDS,
            production_enabled=settings.YANDEX_DELIVERY_PRODUCTION_ENABLED,
        )

    @property
    def base_url(self) -> str:
        if self.environment == DeliveryEnvironment.TEST:
            return TEST_BASE_URL
        if self.environment == DeliveryEnvironment.PRODUCTION:
            return PRODUCTION_BASE_URL
        raise DeliveryConfigurationError(
            "YANDEX_DELIVERY_ENVIRONMENT должен быть test или production"
        )

    def validate(self, *, require_enabled: bool = True) -> None:
        if require_enabled and not self.enabled:
            raise DeliveryConfigurationError("Интеграция Яндекс Доставки выключена")
        if self.environment not in {
            DeliveryEnvironment.TEST,
            DeliveryEnvironment.PRODUCTION,
        }:
            raise DeliveryConfigurationError(
                "YANDEX_DELIVERY_ENVIRONMENT должен быть test или production"
            )
        if self.environment == DeliveryEnvironment.PRODUCTION and not self.production_enabled:
            raise DeliveryConfigurationError(
                "Коммерческий контур требует YANDEX_DELIVERY_PRODUCTION_ENABLED=True"
            )
        if not self.token:
            variable = (
                "YANDEX_DELIVERY_TEST_TOKEN"
                if self.environment == DeliveryEnvironment.TEST
                else "YANDEX_DELIVERY_PRODUCTION_TOKEN"
            )
            raise DeliveryConfigurationError(f"Не задан {variable}")
        if not self.station_id:
            variable = (
                "YANDEX_DELIVERY_TEST_STATION_ID"
                if self.environment == DeliveryEnvironment.TEST
                else "YANDEX_DELIVERY_PRODUCTION_STATION_ID"
            )
            raise DeliveryConfigurationError(f"Не задан {variable}")
        if self.timeout_seconds <= 0:
            raise DeliveryConfigurationError(
                "YANDEX_DELIVERY_TIMEOUT_SECONDS должен быть больше нуля"
            )


@dataclass(frozen=True)
class PricingResult:
    amount: Decimal
    currency: str
    delivery_days: int
    raw_payload: dict[str, Any]


def parse_money(value: str) -> tuple[Decimal, str]:
    """Разобрать формат Яндекса `225.7 RUB` без float."""

    match = MONEY_RE.fullmatch(value or "")
    if not match:
        raise YandexDeliveryAPIError("Яндекс Доставка вернула неизвестный формат цены")
    try:
        amount = Decimal(match.group("amount").replace(",", ".")).quantize(
            Decimal("0.01")
        )
    except InvalidOperation as exc:
        raise YandexDeliveryAPIError("Яндекс Доставка вернула некорректную цену") from exc
    return amount, match.group("currency")


class YandexDeliveryClient:
    """Клиент без бизнес-логики, ORM и автоматического подтверждения оффера."""

    def __init__(
        self,
        config: YandexDeliveryConfig | None = None,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.config = config or YandexDeliveryConfig.from_django_settings()
        self.http_client = http_client

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.config.validate()
        headers = {
            "Authorization": f"Bearer {self.config.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "WebMarket-Diploma/1.0",
        }
        url = f"{self.config.base_url}{path}"

        try:
            if self.http_client is not None:
                response = self.http_client.request(
                    method,
                    url,
                    json=payload,
                    params=params,
                    headers=headers,
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
                        params=params,
                        headers=headers,
                    )
        except httpx.RequestError as exc:
            raise YandexDeliveryAPIError(
                "Не удалось подключиться к API Яндекс Доставки",
                retryable=True,
            ) from exc

        try:
            response_payload = response.json()
        except ValueError as exc:
            raise YandexDeliveryAPIError(
                "Яндекс Доставка вернула ответ не в JSON",
                status_code=response.status_code,
                retryable=response.status_code >= 500,
            ) from exc

        if not isinstance(response_payload, dict):
            raise YandexDeliveryAPIError(
                "Яндекс Доставка вернула JSON неизвестной структуры",
                status_code=response.status_code,
                retryable=response.status_code >= 500,
            )

        if response.is_error:
            code = str(response_payload.get("code", ""))
            message = str(
                response_payload.get("message")
                or response_payload.get("error")
                or "Ошибка API Яндекс Доставки"
            )
            raise YandexDeliveryAPIError(
                message,
                status_code=response.status_code,
                code=code,
                response_payload=response_payload,
                retryable=response.status_code == 429 or response.status_code >= 500,
            )

        return response_payload

    def _post(
        self,
        path: str,
        *,
        payload: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request("POST", path, payload=payload, params=params)

    def _get(
        self,
        path: str,
        *,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request("GET", path, params=params)

    def calculate_price(
        self,
        payload: dict[str, Any],
        *,
        is_oversized: bool = False,
    ) -> PricingResult:
        response_payload = self._post(
            "/api/b2b/platform/pricing-calculator",
            payload=payload,
            params={"is_oversized": str(is_oversized).lower()},
        )
        amount, currency = parse_money(str(response_payload.get("pricing_total", "")))
        delivery_days = response_payload.get("delivery_days")
        if not isinstance(delivery_days, int) or delivery_days < 0:
            raise YandexDeliveryAPIError(
                "Яндекс Доставка вернула некорректный срок доставки",
                response_payload=response_payload,
            )
        return PricingResult(
            amount=amount,
            currency=currency,
            delivery_days=delivery_days,
            raw_payload=response_payload,
        )

    def create_offers(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Получить офферы; метод ничего не бронирует и не подтверждает."""

        return self._post(
            "/api/b2b/platform/offers/create",
            payload=payload,
            params={"send_unix": "false"},
        )

    def detect_location(self, location: str) -> list[dict[str, Any]]:
        response = self._post(
            "/api/b2b/platform/location/detect",
            payload={"location": location},
        )
        variants = response.get("variants")
        if not isinstance(variants, list):
            raise YandexDeliveryAPIError(
                "Яндекс Доставка вернула некорректные варианты адреса",
                response_payload=response,
            )
        return [variant for variant in variants if isinstance(variant, dict)]

    def confirm_offer(self, offer_id: str) -> str:
        response = self._post(
            "/api/b2b/platform/offers/confirm",
            payload={"offer_id": offer_id},
        )
        request_id = response.get("request_id")
        if not isinstance(request_id, str) or not request_id.strip():
            raise YandexDeliveryAPIError(
                "Яндекс Доставка не вернула ID подтверждённой заявки",
                response_payload=response,
            )
        return request_id

    def get_request_info(self, request_id: str) -> dict[str, Any]:
        return self._get(
            "/api/b2b/platform/request/info",
            params={"request_id": request_id, "slim": "true"},
        )

    def cancel_request(self, request_id: str) -> dict[str, Any]:
        return self._post(
            "/api/b2b/platform/request/cancel",
            payload={"request_id": request_id},
        )
