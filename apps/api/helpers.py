"""Вспомогательные функции для REST API."""
from apps.api.exceptions import CustomerContextMismatch, CustomerIdentityRequired, CustomerNotFound
from apps.carts.models import Cart
from apps.carts.services import CartService
from apps.customers.models import Customer, CustomerChannelIdentity
from apps.customers.selectors import get_customer_by_id, get_customer_by_public_code
from apps.customers.services import CustomerService


def get_customer_or_raise(customer_id: int) -> Customer:
    customer = get_customer_by_id(customer_id)
    if customer is None:
        raise CustomerNotFound()
    return customer


def get_customer_by_code_or_raise(public_code: str) -> Customer:
    customer = get_customer_by_public_code(public_code)
    if customer is None:
        raise CustomerNotFound()
    return customer


def resolve_customer_context(
    *,
    channel: str,
    external_user_id: str,
    customer_id: int | None = None,
) -> Customer | None:
    """Проверить и вернуть клиента для channel context.

    Если customer_id не передан — anonymous cart (None).
    Если передан — сверить с CustomerChannelIdentity и identity lookup.
    """
    if customer_id is None:
        return None

    customer = get_customer_by_id(customer_id)
    if customer is None:
        raise CustomerNotFound()

    identity_customer = CustomerService.find_by_channel_identity(channel, external_user_id)
    if identity_customer is not None and identity_customer.pk != customer.pk:
        raise CustomerContextMismatch()

    has_identity = CustomerChannelIdentity.objects.filter(
        customer=customer,
        channel=channel,
        external_user_id=external_user_id,
    ).exists()
    if not has_identity:
        raise CustomerContextMismatch()

    return customer


def resolve_customer_from_identity(*, channel: str, external_user_id: str) -> Customer:
    """Найти клиента по channel identity (обязателен для чтения заказов)."""
    customer = CustomerService.find_by_channel_identity(channel, external_user_id)
    if customer is None:
        raise CustomerIdentityRequired()
    return customer


def get_active_cart(
    *,
    channel: str,
    external_user_id: str,
    customer: Customer | None = None,
) -> Cart:
    cart = CartService.get_or_create_active_cart(
        channel=channel,
        external_user_id=external_user_id,
        customer=customer,
    )
    cart.prefetched_items = list(CartService.get_contents(cart))
    return cart
