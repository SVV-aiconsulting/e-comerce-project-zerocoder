"""Общий контракт LLM-провайдера."""
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class StructuredCompletion:
    raw_content: str
    model_name: str
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class FunctionCall:
    name: str
    arguments: dict[str, Any]
    state_id: str = ""


@dataclass(frozen=True)
class ToolCompletion:
    content: str
    model_name: str
    function_call: FunctionCall | None = None
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

    def generate_with_tools(
        self,
        *,
        system_prompt: str,
        messages: list[dict],
        functions: list[dict],
    ) -> ToolCompletion: ...
