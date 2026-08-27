"""Общий контракт LLM-провайдера."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class StructuredCompletion:
    raw_content: str
    model_name: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class StructuredOutputProvider(Protocol):
    provider_name: str
    model: str

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict,
    ) -> StructuredCompletion: ...
