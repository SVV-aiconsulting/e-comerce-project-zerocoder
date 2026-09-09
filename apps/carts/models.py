from decimal import Decimal
import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from apps.catalog.models import Product
from apps.common.enums import CartStatus, Channel, PaymentMethod, ReceivingType
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
    receiving_type = models.CharField(
        max_length=16,
        choices=ReceivingType.choices,
        blank=True,
        verbose_name="Способ получения текущего оформления",
    )
    delivery_address = models.TextField(
        blank=True,
        verbose_name="Адрес текущего оформления",
    )
    payment_method = models.CharField(
        max_length=32,
        choices=PaymentMethod.choices,
        blank=True,
        verbose_name="Способ оплаты текущего оформления",
    )
    customer_comment = models.TextField(
        blank=True,
        verbose_name="Комментарий текущего оформления",
    )
    contact_phone = models.CharField(max_length=11, blank=True)
    contact_email = models.EmailField(max_length=320, blank=True)
    revision = models.PositiveBigIntegerField(default=1, editable=False)
    desired_date = models.DateField(null=True, blank=True)
    desired_time_interval = models.CharField(max_length=8, blank=True)

    CHECKOUT_FIELDS = (
        "receiving_type", "delivery_address", "payment_method", "customer_comment",
        "contact_phone", "contact_email", "desired_date", "desired_time_interval",
    )

    def save(self, *args, **kwargs):
        # Includes Admin/state endpoints; queryset.update must not mutate checkout.
        from django.db import transaction
        if not self.pk:
            return super().save(*args, **kwargs)
        with transaction.atomic():
            old = type(self).objects.select_for_update().get(pk=self.pk)
            fields = kwargs.get("update_fields")
            changed = any(
                (fields is None or field in fields) and getattr(old, field) != getattr(self, field)
                for field in self.CHECKOUT_FIELDS
            )
            self.revision = old.revision + int(changed)
            if fields is not None and changed:
                kwargs["update_fields"] = [*fields, "revision"]
            return super().save(*args, **kwargs)

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

    def save(self, *args, **kwargs):
        from django.db import transaction
        from django.db.models import F
        with transaction.atomic():
            Cart.objects.select_for_update().get(pk=self.cart_id)
            old = type(self).objects.filter(pk=self.pk).first() if self.pk else None
            changed = old is None or old.quantity != self.quantity or old.product_id != self.product_id
            result = super().save(*args, **kwargs)
            if changed:
                Cart.objects.filter(pk=self.cart_id).update(revision=F("revision") + 1)
            return result


from django.db.models.signals import post_delete
from django.dispatch import receiver


@receiver(post_delete, sender=CartItem)
def item_removed(sender, instance, **kwargs):
    from django.db.models import F
    Cart.objects.filter(pk=instance.cart_id).update(revision=F("revision") + 1)


class CheckoutPreview(models.Model):
    """Immutable terms; only the resulting order may be attached afterwards."""
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    cart = models.ForeignKey(Cart, on_delete=models.PROTECT, related_name="previews")
    cart_revision = models.PositiveBigIntegerField()
    customer = models.ForeignKey("customers.Customer", null=True, on_delete=models.PROTECT)
    snapshot = models.JSONField()
    quote = models.ForeignKey("delivery.DeliveryQuote", null=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    order = models.OneToOneField("orders.Order", null=True, blank=True, on_delete=models.PROTECT)

    def save(self, *args, **kwargs):
        if self.pk:
            old = type(self).objects.only(
                "cart_id", "cart_revision", "customer_id", "snapshot", "quote_id",
                "created_at", "expires_at", "order_id"
            ).get(pk=self.pk)
            immutable_changed = any(
                getattr(old, field) != getattr(self, field)
                for field in (
                    "cart_id", "cart_revision", "customer_id", "snapshot", "quote_id",
                    "created_at", "expires_at",
                )
            )
            if immutable_changed or (old.order_id and old.order_id != self.order_id):
                raise ValidationError("Условия CheckoutPreview неизменяемы")
        return super().save(*args, **kwargs)
