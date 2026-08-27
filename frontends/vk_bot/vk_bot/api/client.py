"""HTTP-клиент Storefront REST API."""
import asyncio

from typing import Any

import httpx

from vk_bot.api.errors import ApiError, BackendUnavailableError


class StorefrontApiClient:
    def __init__(
        self,
        base_url: str,
        adapter_token: str,
        timeout: float = 15.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._adapter_token = adapter_token
        self._timeout = timeout

    def _headers(self, *, with_token: bool = True) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if with_token:
            headers["X-Adapter-Token"] = self._adapter_token
        return headers

    @staticmethod
    def _parse_json_response(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise BackendUnavailableError("Некорректный ответ backend (не JSON)") from exc

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        with_token: bool = True,
    ) -> Any:
        url = f"{self._base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    method,
                    url,
                    json=json,
                    params=params,
                    headers=self._headers(with_token=with_token),
                )
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise BackendUnavailableError(str(exc)) from exc

        if response.status_code >= 400:
            self._raise_api_error(response)

        if response.status_code == 204 or not response.content:
            return None
        return self._parse_json_response(response)

    @staticmethod
    def _raise_api_error(response: httpx.Response) -> None:
        try:
            payload = response.json()
            error = payload.get("error", {})
            raise ApiError(
                code=error.get("code", "api_error"),
                message=error.get("message", "Ошибка API"),
                details=error.get("details", {}),
                status_code=response.status_code,
            )
        except ApiError:
            raise
        except Exception as exc:
            raise ApiError(
                code="api_error",
                message=f"HTTP {response.status_code}",
                status_code=response.status_code,
            ) from exc

    async def health(self) -> dict:
        return await self._request("GET", "/api/health/", with_token=False)

    async def get_meta(self) -> dict:
        return await self._request("GET", "/api/meta/", with_token=False)

    async def list_products(self) -> list:
        return await self._request("GET", "/api/products/", with_token=False)

    async def get_product(self, public_code: str) -> dict:
        return await self._request(
            "GET",
            f"/api/products/{public_code}/",
            with_token=False,
        )

    async def identify_customer(self, payload: dict) -> dict:
        url = f"{self._base_url}/api/identify-customer/"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers=self._headers(with_token=True),
                )
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise BackendUnavailableError(str(exc)) from exc

        if response.status_code == 409:
            return self._parse_json_response(response)

        if response.status_code >= 400:
            self._raise_api_error(response)

        return self._parse_json_response(response)

    async def submit_inbound_event(self, payload: dict) -> dict:
        return await self._request("POST", "/api/intake/events/", json=payload)

    async def get_inbound_event(
        self,
        event_id: str,
        *,
        channel: str,
        external_user_id: str,
    ) -> dict:
        return await self._request(
            "GET",
            f"/api/intake/events/{event_id}/",
            params={
                "channel": channel,
                "external_user_id": external_user_id,
            },
        )

    async def wait_for_inbound_event(
        self,
        event_id: str,
        *,
        channel: str,
        external_user_id: str,
        attempts: int = 20,
        interval: float = 0.75,
    ) -> dict:
        result = {}
        for attempt in range(attempts):
            result = await self.get_inbound_event(
                event_id,
                channel=channel,
                external_user_id=external_user_id,
            )
            if result.get("complete"):
                return result
            if attempt + 1 < attempts:
                await asyncio.sleep(interval)
        return result

    async def get_cart(
        self,
        *,
        channel: str,
        external_user_id: str,
        customer_id: int | None = None,
    ) -> dict:
        params: dict[str, str | int] = {
            "channel": channel,
            "external_user_id": external_user_id,
        }
        if customer_id is not None:
            params["customer_id"] = customer_id
        return await self._request("GET", "/api/cart/", params=params)

    async def set_cart_item(
        self,
        product_id: int,
        *,
        channel: str,
        external_user_id: str,
        quantity: str,
        customer_id: int,
    ) -> dict:
        return await self._request(
            "PUT",
            f"/api/cart/items/{product_id}/",
            json={
                "channel": channel,
                "external_user_id": external_user_id,
                "customer_id": customer_id,
                "quantity": quantity,
            },
        )

    async def remove_cart_item(
        self,
        product_id: int,
        *,
        channel: str,
        external_user_id: str,
        customer_id: int,
    ) -> dict:
        return await self._request(
            "DELETE",
            f"/api/cart/items/{product_id}/",
            json={
                "channel": channel,
                "external_user_id": external_user_id,
                "customer_id": customer_id,
            },
        )

    async def clear_cart(
        self,
        *,
        channel: str,
        external_user_id: str,
        customer_id: int,
    ) -> dict:
        return await self._request(
            "DELETE",
            "/api/cart/items/",
            json={
                "channel": channel,
                "external_user_id": external_user_id,
                "customer_id": customer_id,
            },
        )

    async def checkout_preview(
        self,
        *,
        channel: str,
        external_user_id: str,
        customer_id: int,
        receiving_type: str,
    ) -> dict:
        return await self._request(
            "POST",
            "/api/checkout/preview/",
            json={
                "channel": channel,
                "external_user_id": external_user_id,
                "customer_id": customer_id,
                "receiving_type": receiving_type,
            },
        )

    async def create_order(self, payload: dict) -> dict:
        return await self._request("POST", "/api/orders/", json=payload)

    async def get_order(
        self,
        public_number: str,
        *,
        channel: str,
        external_user_id: str,
    ) -> dict:
        return await self._request(
            "GET",
            f"/api/orders/{public_number}/",
            params={
                "channel": channel,
                "external_user_id": external_user_id,
            },
        )

    async def list_customer_orders(
        self,
        public_code: str,
        *,
        channel: str,
        external_user_id: str,
    ) -> list:
        return await self._request(
            "GET",
            f"/api/customers/{public_code}/orders/",
            params={
                "channel": channel,
                "external_user_id": external_user_id,
            },
        )

    async def aclose(self) -> None:
        """Совместимость с lifecycle; клиент создаётся на каждый запрос."""
