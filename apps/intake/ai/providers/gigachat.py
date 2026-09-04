"""Минимальный REST-адаптер GigaChat без LangChain и доступа к ORM."""
import ssl
import threading
import time
import uuid
from functools import lru_cache
from pathlib import Path

import httpx
from django.conf import settings

from apps.intake.ai.providers.base import FunctionCall, StructuredCompletion, ToolCompletion
from apps.intake.exceptions import LLMConfigurationError, LLMProviderError

ALLOWED_SCOPES = {
    "GIGACHAT_API_PERS",
    "GIGACHAT_API_B2B",
    "GIGACHAT_API_CORP",
}


class GigaChatProvider:
    provider_name = "gigachat"

    def __init__(
        self,
        *,
        credentials: str,
        scope: str,
        model: str,
        base_url: str,
        auth_url: str,
        ca_bundle: str = "",
        verify_ssl: bool = True,
        timeout: float = 30.0,
        max_tokens: int = 1600,
        temperature: float = 0.1,
    ):
        self.credentials = credentials.strip()
        self.scope = scope
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.auth_url = auth_url
        self.ca_bundle = ca_bundle.strip()
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._access_token = ""
        self._access_token_expires_at = 0.0
        self._token_lock = threading.Lock()

    @classmethod
    def from_settings(cls):
        return cls(
            credentials=settings.GIGACHAT_CREDENTIALS,
            scope=settings.GIGACHAT_SCOPE,
            model=settings.GIGACHAT_MODEL,
            base_url=settings.GIGACHAT_BASE_URL,
            auth_url=settings.GIGACHAT_AUTH_URL,
            ca_bundle=settings.GIGACHAT_CA_BUNDLE,
            verify_ssl=settings.GIGACHAT_VERIFY_SSL,
            timeout=settings.GIGACHAT_TIMEOUT_SECONDS,
            max_tokens=settings.GIGACHAT_MAX_TOKENS,
            temperature=settings.GIGACHAT_TEMPERATURE,
        )

    def _validate_configuration(self) -> None:
        if not self.credentials:
            raise LLMConfigurationError("Не задан GIGACHAT_CREDENTIALS")
        if self.scope not in ALLOWED_SCOPES:
            raise LLMConfigurationError("Некорректный GIGACHAT_SCOPE")
        if not self.model:
            raise LLMConfigurationError("Не задан GIGACHAT_MODEL")
        if not self.verify_ssl:
            raise LLMConfigurationError(
                "Отключение проверки TLS для GigaChat запрещено конфигурацией MVP"
            )

    def _post(self, url: str, **kwargs) -> httpx.Response:
        try:
            with httpx.Client(verify=self._ssl_verifier(), timeout=self.timeout) as client:
                return client.post(url, **kwargs)
        except httpx.HTTPError as exc:
            raise LLMProviderError(f"Сетевая ошибка GigaChat: {type(exc).__name__}") from exc

    def _ssl_verifier(self):
        if not self.ca_bundle:
            return True
        bundle_path = Path(self.ca_bundle)
        if not bundle_path.is_file():
            raise LLMConfigurationError("Файл GIGACHAT_CA_BUNDLE не найден")
        try:
            context = ssl.create_default_context()
            context.load_verify_locations(cafile=bundle_path)
        except (OSError, ssl.SSLError) as exc:
            raise LLMConfigurationError(
                "Не удалось загрузить сертификат GIGACHAT_CA_BUNDLE"
            ) from exc
        return context

    def _fetch_access_token(self) -> tuple[str, float]:
        authorization = self.credentials
        if not authorization.lower().startswith("basic "):
            authorization = f"Basic {authorization}"
        response = self._post(
            self.auth_url,
            headers={
                "Accept": "application/json",
                "Authorization": authorization,
                "RqUID": str(uuid.uuid4()),
            },
            data={"scope": self.scope},
        )
        if response.status_code >= 400:
            raise LLMProviderError(
                f"GigaChat OAuth вернул HTTP {response.status_code}"
            )
        try:
            payload = response.json()
            access_token = str(payload["access_token"])
            expires_at = float(payload["expires_at"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMProviderError("GigaChat OAuth вернул некорректный ответ") from exc
        if expires_at > 10_000_000_000:
            expires_at /= 1000
        return access_token, expires_at

    def _get_access_token(self) -> str:
        self._validate_configuration()
        with self._token_lock:
            if self._access_token and self._access_token_expires_at > time.time() + 60:
                return self._access_token
            token, expires_at = self._fetch_access_token()
            self._access_token = token
            self._access_token_expires_at = expires_at
            return token

    def _chat(self, access_token: str, payload: dict) -> httpx.Response:
        return self._post(
            f"{self.base_url}/chat/completions",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict,
    ) -> StructuredCompletion:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {
                "type": "json_schema",
                "schema": json_schema,
                "strict": True,
            },
        }
        response = self._chat(self._get_access_token(), payload)
        if response.status_code == 401:
            with self._token_lock:
                self._access_token = ""
                self._access_token_expires_at = 0
            response = self._chat(self._get_access_token(), payload)
        if response.status_code >= 400:
            raise LLMProviderError(
                f"GigaChat chat/completions вернул HTTP {response.status_code}"
            )

        try:
            body = response.json()
            raw_content = body["choices"][0]["message"]["content"]
            usage = body.get("usage", {})
            model_name = body.get("model") or self.model
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise LLMProviderError("GigaChat вернул некорректную структуру ответа") from exc
        if not isinstance(raw_content, str) or not raw_content.strip():
            raise LLMProviderError("GigaChat вернул пустой structured output")

        return StructuredCompletion(
            raw_content=raw_content,
            model_name=str(model_name),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )

    def generate_with_tools(
        self,
        *,
        system_prompt: str,
        messages: list[dict],
        functions: list[dict],
    ) -> ToolCompletion:
        """Один шаг нативного GigaChat function-calling без исполнения функций."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                *messages,
            ],
            "functions": functions,
            "function_call": "auto",
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        response = self._chat(self._get_access_token(), payload)
        if response.status_code == 401:
            with self._token_lock:
                self._access_token = ""
                self._access_token_expires_at = 0
            response = self._chat(self._get_access_token(), payload)
        if response.status_code >= 400:
            raise LLMProviderError(
                f"GigaChat chat/completions вернул HTTP {response.status_code}"
            )
        try:
            body = response.json()
            message = body["choices"][0]["message"]
            usage = body.get("usage", {})
            model_name = str(body.get("model") or self.model)
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise LLMProviderError("GigaChat вернул некорректную структуру ответа") from exc

        function_call = None
        raw_call = message.get("function_call")
        if raw_call is not None:
            try:
                name = str(raw_call["name"]).strip()
                arguments = raw_call.get("arguments", {})
                if isinstance(arguments, str):
                    import json

                    arguments = json.loads(arguments)
                if not name or not isinstance(arguments, dict):
                    raise ValueError
            except (KeyError, TypeError, ValueError) as exc:
                raise LLMProviderError("GigaChat вернул некорректный function_call") from exc
            function_call = FunctionCall(
                name=name,
                arguments=arguments,
                state_id=str(message.get("functions_state_id") or ""),
            )
        content = message.get("content") or ""
        if not function_call and not str(content).strip():
            raise LLMProviderError("GigaChat вернул пустой ответ")
        return ToolCompletion(
            content=str(content),
            model_name=model_name,
            function_call=function_call,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )


@lru_cache(maxsize=1)
def get_gigachat_provider() -> GigaChatProvider:
    """Один OAuth token cache на процесс Celery worker."""
    return GigaChatProvider.from_settings()
