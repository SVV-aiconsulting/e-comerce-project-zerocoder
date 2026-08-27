"""Форматирование данных для отображения в VK."""
from datetime import datetime
from decimal import Decimal


def format_price(value: str | Decimal | float) -> str:
    amount = Decimal(str(value)).quantize(Decimal("0.01"))
    return f"{amount} ₽"


def format_quantity(value: str | Decimal | float) -> str:
    qty = Decimal(str(value)).normalize()
    text = format(qty, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def format_datetime(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return value


def truncate_text(text: str, limit: int = 500) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def format_product_card(product: dict) -> str:
    return (
        f"{product['name']}\n\n"
        f"{truncate_text(product.get('description') or '')}\n\n"
        f"Цена: {format_price(product['base_price'])}\n"
        f"Единица: {product['unit_label']}\n"
        f"Мин. количество: {format_quantity(product['min_quantity'])}"
    )


def format_cart_item_line(item: dict) -> str:
    product = item["product"]
    unit = product.get("unit_label", "шт")
    unit_price = format_price(product["base_price"])
    qty = format_quantity(item["quantity"])
    return f"{product['name']}\nКоличество: {qty} · {unit_price} / {unit}"


def format_cart_footer(cart: dict) -> str:
    return f"Итого: {format_price(cart['items_total'])}"


def format_checkout_preview(preview: dict) -> str:
    free = " (бесплатно)" if preview.get("free_delivery") else ""
    return (
        "Превью заказа\n\n"
        f"Товары: {format_price(preview['items_total'])}\n"
        f"Скидка: {format_price(preview['discount_amount'])}\n"
        f"Доставка: {format_price(preview['delivery_cost'])}{free}\n"
        f"Итого: {format_price(preview['total_amount'])}"
    )


def format_order_created(order: dict) -> str:
    return (
        "Заказ принят!\n\n"
        f"Номер: {order['public_number']}\n"
        f"Статус: {order['order_status_label']}\n"
        f"Сумма: {format_price(order['total_amount'])}"
    )


def format_orders_list_item(order: dict) -> str:
    return (
        f"• {order['public_number']} — {format_price(order['total_amount'])}\n"
        f"  {format_datetime(order['created_at'])} — {order['order_status_label']}"
    )


def format_order_detail(order: dict) -> str:
    items_lines = []
    for item in order.get("items") or []:
        items_lines.append(
            f"• {item['product_name_snapshot']}: "
            f"{format_quantity(item['quantity'])} × {format_price(item['unit_price'])} "
            f"= {format_price(item['total_price'])}"
        )

    text = (
        f"Заказ {order['public_number']}\n\n"
        f"Статус: {order['order_status_label']}\n"
        f"Оплата: {order['payment_method_label']}\n"
        f"Получение: {order['receiving_type_label']}\n"
        f"Дата: {format_datetime(order['created_at'])}\n"
        f"Сумма: {format_price(order['total_amount'])}\n"
    )
    if order.get("delivery_address"):
        text += f"Адрес: {order['delivery_address']}\n"
    if items_lines:
        text += "\nПозиции:\n" + "\n".join(items_lines)
    return text
