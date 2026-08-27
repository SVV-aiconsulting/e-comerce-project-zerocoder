"""Сессия пользователя VK-бота."""
from typing import Any


def empty_session(external_user_id: str, username: str = "", display_name: str = "") -> dict:
    return {
        "external_user_id": external_user_id,
        "username": username,
        "display_name": display_name,
        "customer_id": None,
        "customer_public_code": None,
        "is_new_customer": False,
        "catalog_page": 0,
        "product_quantities": {},
        "product_min_quantities": {},
        "product_codes": {},
        "selected_product_id": None,
        "selected_product_code": None,
        "selected_min_quantity": None,
        "selected_quantity": None,
        "receiving_type": None,
        "delivery_address": "",
        "payment_method": None,
        "customer_comment": "",
        "checkout_preview": None,
        "cart_ui": None,
    }


def is_identified(session: dict) -> bool:
    return session.get("customer_id") is not None


def apply_identify_response(session: dict, response: dict) -> dict:
    session["customer_id"] = response.get("customer_id")
    session["customer_public_code"] = response.get("customer_public_code")
    session["is_new_customer"] = response.get("is_new_customer", False)
    if response.get("display_name"):
        session["display_name"] = response["display_name"]
    if response.get("external_user_id"):
        session["external_user_id"] = response["external_user_id"]
    return session


def sync_catalog_quantities(session: dict, products: list) -> dict:
    quantities = dict(session.get("product_quantities") or {})
    min_quantities = dict(session.get("product_min_quantities") or {})
    product_codes = dict(session.get("product_codes") or {})

    for product in products:
        product_id = str(product["id"])
        min_qty = str(product["min_quantity"])
        min_quantities[product_id] = min_qty
        product_codes[product_id] = product["public_code"]
        if product_id not in quantities:
            quantities[product_id] = min_qty

    session["product_quantities"] = quantities
    session["product_min_quantities"] = min_quantities
    session["product_codes"] = product_codes
    return session


def update_session_fields(session: dict, **kwargs: Any) -> dict:
    session.update(kwargs)
    return session
