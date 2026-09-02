"""Аудируемый вызов GigaChat и проверка structured output."""
import hashlib
import json
import time

from django.utils import timezone
from pydantic import ValidationError

from apps.intake.ai.normalization import normalize_extraction
from apps.intake.ai.prompts import (
    build_order_extraction_prompt,
    build_order_repair_prompt,
)
from apps.intake.ai.providers.gigachat import get_gigachat_provider
from apps.intake.ai.schemas import OrderExtraction, order_extraction_json_schema
from apps.intake.enums import AIRunPurpose, AIRunStatus
from apps.intake.exceptions import (
    LLMConfigurationError,
    LLMProviderError,
    LLMResponseValidationError,
)
from apps.intake.models import AIExtractionRun


class AIExtractionService:
    provider_name = "gigachat"

    @classmethod
    def extract(
        cls,
        event,
        draft,
        *,
        provider=None,
        provider_name="gigachat",
        prompt_profile=None,
        current_datetime=None,
    ):
        if provider_name != "gigachat":
            raise LLMConfigurationError(f"Провайдер {provider_name} пока не подключён")
        provider = provider or get_gigachat_provider()
        prompt = build_order_extraction_prompt(
            event,
            draft,
            profile=prompt_profile,
            current_datetime=current_datetime,
        )
        return cls._execute(
            event,
            draft,
            prompt,
            AIRunPurpose.EXTRACTION,
            provider,
        )

    @classmethod
    def extract_with_repair(
        cls,
        event,
        draft,
        *,
        provider=None,
        provider_name="gigachat",
        prompt_profile=None,
        current_datetime=None,
    ):
        """Извлечь заказ и один раз исправить только структуру ответа."""
        if provider_name != "gigachat":
            raise LLMConfigurationError(f"Провайдер {provider_name} пока не подключён")
        provider = provider or get_gigachat_provider()
        try:
            return cls.extract(
                event,
                draft,
                provider=provider,
                provider_name=provider_name,
                prompt_profile=prompt_profile,
                current_datetime=current_datetime,
            )
        except LLMResponseValidationError as exc:
            if exc.run is None:
                raise
            prompt = build_order_repair_prompt(
                event,
                draft,
                exc.run.raw_response,
                profile=prompt_profile,
                current_datetime=current_datetime,
            )
            return cls._execute(
                event,
                draft,
                prompt,
                AIRunPurpose.REPAIR,
                provider,
            )

    @classmethod
    def _execute(cls, event, draft, prompt, purpose, provider):
        schema = order_extraction_json_schema()
        fingerprint = json.dumps(
            {
                "system": prompt.system,
                "user": prompt.user,
                "schema": schema,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        run = AIExtractionRun.objects.create(
            event=event,
            draft=draft,
            purpose=purpose,
            provider=cls.provider_name,
            model_name=provider.model,
            prompt_id=prompt.prompt_id,
            prompt_version=prompt.version,
            input_hash=hashlib.sha256(fingerprint.encode()).hexdigest(),
        )
        started = time.monotonic()

        try:
            completion = provider.generate_structured(
                system_prompt=prompt.system,
                user_prompt=prompt.user,
                json_schema=schema,
            )
        except (LLMConfigurationError, LLMProviderError) as exc:
            cls._finish_error(run, AIRunStatus.PROVIDER_ERROR, exc, started)
            raise

        run.raw_response = completion.raw_content
        run.model_name = completion.model_name
        run.input_tokens = completion.input_tokens
        run.output_tokens = completion.output_tokens
        try:
            extraction = OrderExtraction.model_validate_json(
                completion.raw_content,
                strict=True,
            )
        except (ValidationError, ValueError) as exc:
            cls._finish_error(
                run,
                AIRunStatus.SCHEMA_INVALID,
                exc,
                started,
                raw_response=completion.raw_content,
            )
            raise LLMResponseValidationError(
                "Ответ GigaChat не прошёл JSON Schema",
                run=run,
            ) from exc

        extraction = normalize_extraction(event, draft, extraction)

        run.status = AIRunStatus.SUCCEEDED
        run.structured_output = extraction.model_dump(mode="json")
        run.latency_ms = max(0, round((time.monotonic() - started) * 1000))
        run.completed_at = timezone.now()
        run.save(
            update_fields=[
                "status",
                "raw_response",
                "structured_output",
                "model_name",
                "input_tokens",
                "output_tokens",
                "latency_ms",
                "completed_at",
                "updated_at",
            ]
        )
        return extraction, run

    @staticmethod
    def _finish_error(run, status, exc, started, *, raw_response=""):
        run.status = status
        if raw_response:
            run.raw_response = raw_response
        if isinstance(exc, ValidationError):
            errors = exc.errors(include_input=False, include_url=False)
        else:
            errors = [{"type": type(exc).__name__, "message": str(exc)[:500]}]
        run.validation_errors = errors
        run.latency_ms = max(0, round((time.monotonic() - started) * 1000))
        run.completed_at = timezone.now()
        run.save(
            update_fields=[
                "status",
                "raw_response",
                "validation_errors",
                "latency_ms",
                "completed_at",
                "updated_at",
            ]
        )
