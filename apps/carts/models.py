from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from apps.catalog.models import Product
from apps.common.enums import CartStatus, Channel
from apps.common.models import TimeStampedModel
from apps.customers.models import Customer


class Cart(TimeStampedModel):
    """Корзина покупок пользователя канала."""

    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="carts",
        verbose_name="Клиент",
    )
    channel = models.CharField(
        max_length=16,
        choices=Channel.choices,
        verbose_name="Канал",
        help_text="Обязательно. Платформа, в которой создана корзина.",
    )
    external_user_id = models.CharField(
        max_length=128,
        verbose_name="Идентификатор пользователя",
        help_text="Обязательно. ID пользователя в канале (совпадает с привязкой клиента).",
    )
    status = models.CharField(
        max_length=16,
        choices=CartStatus.choices,
        default=CartStatus.ACTIVE,
        verbose_name="Статус",
    )

    class Meta:
        verbose_name = "Корзина"
        verbose_name_plural = "Корзины"
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "external_user_id"],
                condition=Q(status=CartStatus.ACTIVE),
                name="unique_active_cart_per_channel_user",
            ),
        ]

    def __str__(self) -> str:
        return f"Корзина {self.get_channel_display()}:{self.external_user_id} ({self.get_status_display()})"


class CartItem(TimeStampedModel):
    """Одна позиция товара в корзине."""

    cart = models.ForeignKey(
        Cart,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Корзина",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="cart_items",
        verbose_name="Товар",
    )
    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
        verbose_name="Количество",
    )

    class Meta:
        verbose_name = "Позиция корзины"
        verbose_name_plural = "Позиции корзины"
        constraints = [
            models.UniqueConstraint(
                fields=["cart", "product"],
                name="unique_product_in_cart",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product.name} × {self.quantity}"
