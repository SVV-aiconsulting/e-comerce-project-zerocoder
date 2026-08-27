import json

from apps.intake.ai.schemas import OrderExtraction
from apps.intake.evaluation.runner import evaluate_extraction, load_evaluation_cases


def build_extraction(**overrides):
    payload = {
        "intent": "create_order",
        "items": [
            {
                "raw_product_name": "Лосось",
                "quantity": 2,
                "unit": "kg",
                "attributes": [],
                "confidence": 0.95,
            }
        ],
        "receiving_type": None,
        "desired_date": None,
        "desired_time_interval": None,
        "delivery_address": None,
        "payment_method": None,
        "customer_comment": None,
        "confirmation": "none",
        "missing_fields": [],
        "clarification_needed": False,
        "confidence": 0.9,
    }
    payload.update(overrides)
    return OrderExtraction.model_validate_json(json.dumps(payload), strict=True)


def test_evaluation_case_collection_covers_required_edges():
    case_ids = {case["id"] for case in load_evaluation_cases()}

    assert len(case_ids) >= 6
    assert "modify_existing_order" in case_ids
    assert "explicit_confirmation" in case_ids
    assert "unknown_product_preserved" in case_ids
    assert "prompt_injection_is_data" in case_ids


def test_evaluate_extraction_checks_exact_contains_and_forbidden_values():
    case = {
        "id": "unit",
        "expected": {"intent": "create_order", "items.0.quantity": 2.0},
        "expected_contains": {"items.0.raw_product_name": "лосос"},
        "forbidden_contains": ["секрет"],
    }

    assert evaluate_extraction(case, build_extraction()).passed is True

    failed = evaluate_extraction(
        case,
        build_extraction(customer_comment="Секрет"),
    )
    assert failed.passed is False
    assert any("forbidden" in error for error in failed.errors)
