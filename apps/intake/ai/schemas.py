"""Строгие схемы structured output для AI-разбора заказа."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ExtractedOrderItem(StrictSchema):
    raw_product_name: str = Field(min_length=1, max_length=255)
    quantity: float | None = Field(gt=0, le=1_000_000, multiple_of=0.001)
    unit: Literal["kg", "piece", "package"] | None
    attributes: list[str] = Field(max_length=20)
    confidence: float = Field(ge=0, le=1)


class OrderExtraction(StrictSchema):
    intent: Literal[
        "create_order",
        "modify_order",
        "cancel_order",
        "order_status",
        "product_question",
        "unknown",
    ] = Field(
        description=(
            "Intent of the latest message. When draft.items is empty, a request to "
            "order/add products is create_order and must never be modify_order. "
            "modify_order requires an existing non-empty draft being changed."
        )
    )
    items: list[ExtractedOrderItem] = Field(max_length=50)
    receiving_type: Literal["delivery", "pickup"] | None
    desired_date: date | None
    desired_time_interval: Literal[
        "10-12",
        "12-14",
        "14-16",
        "16-18",
        "18-20",
        "20-22",
    ] | None
    delivery_address: str | None = Field(max_length=1000)
    payment_method: Literal[
        "cash_on_delivery",
        "card_on_delivery",
        "card_prepayment",
    ] | None = Field(
        description=(
            "Online/by-link/current card payment is card_prepayment; card at receipt "
            "is card_on_delivery; cash at receipt or pickup is cash_on_delivery."
        )
    )
    customer_comment: str | None = Field(max_length=2000)
    confirmation: Literal["confirm", "reject", "none"] = Field(
        description=(
            "confirm only for explicit approval of the current calculated preview "
            "without simultaneous changes; reject for explicit refusal; otherwise none."
        )
    )
    missing_fields: list[str] = Field(max_length=50)
    clarification_needed: bool
    confidence: float = Field(ge=0, le=1)


def order_extraction_json_schema() -> dict:
    """JSON Schema, передаваемая GigaChat с `strict=true`."""
    return OrderExtraction.model_json_schema(mode="validation")
