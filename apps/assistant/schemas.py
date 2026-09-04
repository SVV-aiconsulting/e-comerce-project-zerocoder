"""Строгие аргументы backend-инструментов AI-ассистента."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SearchProductsArgs(StrictToolArgs):
    query: str = Field(min_length=1, max_length=255)
    limit: int = Field(default=5, ge=1, le=10)


class EmptyArgs(StrictToolArgs):
    pass


class SetCartItemArgs(StrictToolArgs):
    product_code: str = Field(min_length=1, max_length=32)
    quantity: float = Field(gt=0, le=9_999_999)


class RemoveCartItemArgs(StrictToolArgs):
    product_code: str = Field(min_length=1, max_length=32)


class ConfigureCheckoutArgs(StrictToolArgs):
    receiving_type: Literal["delivery", "pickup"] | None = None
    delivery_address: str | None = Field(default=None, max_length=1000)
    payment_method: Literal[
        "cash_on_delivery", "card_on_delivery", "card_prepayment"
    ] | None = None
    contact_phone: str | None = Field(default=None, max_length=64)
    contact_email: str | None = Field(default=None, max_length=320)
    customer_comment: str | None = Field(default=None, max_length=2000)


class ListOrdersArgs(StrictToolArgs):
    limit: int = Field(default=5, ge=1, le=10)


class RepeatOrderArgs(StrictToolArgs):
    order_number: str | None = Field(default=None, max_length=32)


class PaymentLinkArgs(StrictToolArgs):
    order_number: str = Field(min_length=1, max_length=32)


class ConfirmOrderArgs(StrictToolArgs):
    preview_revision: int = Field(ge=1)
    confirmation: Literal["confirmed"]
