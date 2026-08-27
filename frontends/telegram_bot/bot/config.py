"""Настройки Telegram-бота из переменных окружения."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: str
    backend_api_base_url: str = "http://web:8000"
    adapter_api_token: str
    telegram_bot_use_polling: bool = True
    telegram_bot_log_level: str = "INFO"
    telegram_proxy: str | None = None
    http_timeout_seconds: float = 15.0
    telegram_ai_poll_attempts: int = 20
    telegram_ai_poll_interval_seconds: float = 0.75


def get_settings() -> Settings:
    return Settings()
