"""Проверка structured output по фиксированным естественным заказам."""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apps.intake.ai.schemas import OrderExtraction

DEFAULT_CASES_PATH = Path(__file__).with_name("order_extraction_cases.json")


@dataclass(frozen=True)
class EvaluationResult:
    case_id: str
    passed: bool
    errors: tuple[str, ...]


def load_evaluation_cases(path: Path = DEFAULT_CASES_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_extraction(case: dict, extraction: OrderExtraction) -> EvaluationResult:
    payload = extraction.model_dump(mode="json")
    errors = []
    for field_path, expected in case.get("expected", {}).items():
        actual = _resolve_path(payload, field_path)
        if actual != expected:
            errors.append(f"{field_path}: expected {expected!r}, got {actual!r}")

    for field_path, expected_fragment in case.get("expected_contains", {}).items():
        actual = _resolve_path(payload, field_path)
        if expected_fragment.casefold() not in str(actual or "").casefold():
            errors.append(
                f"{field_path}: expected fragment {expected_fragment!r}, got {actual!r}"
            )

    serialized = json.dumps(payload, ensure_ascii=False).casefold()
    for forbidden_fragment in case.get("forbidden_contains", []):
        if forbidden_fragment.casefold() in serialized:
            errors.append(f"forbidden fragment returned: {forbidden_fragment!r}")

    return EvaluationResult(
        case_id=case["id"],
        passed=not errors,
        errors=tuple(errors),
    )


def _resolve_path(payload: Any, field_path: str):
    current = payload
    for part in field_path.split("."):
        try:
            current = current[int(part)] if isinstance(current, list) else current[part]
        except (IndexError, KeyError, TypeError, ValueError):
            return None
    return current
