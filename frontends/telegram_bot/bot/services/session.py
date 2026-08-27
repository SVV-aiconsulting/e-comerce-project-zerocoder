"""Сессия пользователя в FSM storage."""
from typing import Any

from aiogram.fsm.context import FSMContext

SESSION_KEY = "session"


def _empty_session(external_user_id: str, username: str = "", display_name: str = "") -> dict:
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


async def get_session(state: FSMContext, external_user_id: str) -> dict:
    data = await state.get_data()
    session = data.get(SESSION_KEY)
    if not session:
        session = _empty_session(external_user_id)
        await state.update_data({SESSION_KEY: session})
        return session
    if session.get("external_user_id") != external_user_id:
        # Защита от callback.message.from_user (бот): не теряем customer_id.
        if session.get("customer_id") is not None:
            session["external_user_id"] = external_user_id
            await state.update_data({SESSION_KEY: session})
            return session
        session = _empty_session(external_user_id)
        await state.update_data({SESSION_KEY: session})
    return session


async def save_session(state: FSMContext, session: dict) -> None:
    await state.update_data({SESSION_KEY: session})


async def update_session(state: FSMContext, **kwargs: Any) -> dict:
    data = await state.get_data()
    session = data.get(SESSION_KEY, {})
    session.update(kwargs)
    await state.update_data({SESSION_KEY: session})
    return session


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
