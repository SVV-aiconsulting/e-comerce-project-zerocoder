"""Настройки VK-бота из переменных окружения."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    vk_bot_token: str
    vk_group_id: int | None = None
    backend_api_base_url: str = "http://web:8000"
    product_media_base_url: str = ""
    adapter_api_token: str
    vk_bot_use_longpoll: bool = True
    vk_bot_log_level: str = "INFO"
    http_timeout_seconds: float = 15.0
    vk_ai_poll_attempts: int = 20
    vk_ai_poll_interval_seconds: float = 0.75


def get_settings() -> Settings:
    return Settings()
