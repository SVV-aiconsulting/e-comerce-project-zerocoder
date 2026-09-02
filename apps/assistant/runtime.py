"""Runtime-конфигурация ассистента без привязки к frontend-каналу."""

from dataclasses import dataclass

from django.conf import settings

from apps.intake.exceptions import LLMConfigurationError

SUPPORTED_PROVIDERS = {"gigachat"}
SUPPORTED_PROMPT_PROFILES = {"ecommerce_sales_v1"}


@dataclass(frozen=True)
class AssistantRuntimeConfig:
    enabled: bool
    provider: str
    model: str
    prompt_profile: str

    def validate(self) -> None:
        if self.provider not in SUPPORTED_PROVIDERS:
            raise LLMConfigurationError(
                f"Неизвестный AI_ASSISTANT_PROVIDER: {self.provider}"
            )
        if self.prompt_profile not in SUPPORTED_PROMPT_PROFILES:
            raise LLMConfigurationError(
                "Неизвестный AI_ASSISTANT_PROMPT_PROFILE: "
                f"{self.prompt_profile}"
            )


def get_assistant_runtime() -> AssistantRuntimeConfig:
    config = AssistantRuntimeConfig(
        enabled=(
            settings.AI_ASSISTANT_ENABLED
            or settings.AI_ORDER_PROCESSING_ENABLED
        ),
        provider=settings.AI_ASSISTANT_PROVIDER,
        model=settings.GIGACHAT_MODEL,
        prompt_profile=settings.AI_ASSISTANT_PROMPT_PROFILE,
    )
    if config.enabled:
        config.validate()
    return config
