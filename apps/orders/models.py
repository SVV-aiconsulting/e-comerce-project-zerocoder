from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.catalog.models import Product
from apps.common.enums import (
    Channel,
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    ReceivingType,
    StatusChangeSource,
    TimeInterval,
)
from apps.common.models import TimeStampedModel
from apps.customers.models import Customer


class Order(TimeStampedModel):
    """Заказ клиента с зафиксированными снимками цен."""

    public_number = models.CharField(
        max_length=32,
        unique=True,
        verbose_name="Номер заказа",
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="orders",
        verbose_name="Клиент",
    )
    customer_code_snapshot = models.CharField(
        max_length=32,
        verbose_name="Код клиента (снимок)",
    )
    customer_name_snapshot = models.CharField(
        max_length=255,
        verbose_name="Имя клиента (снимок)",
    )
    customer_phone_snapshot = models.CharField(
        max_length=11,
        blank=True,
        verbose_name="Телефон клиента (снимок)",
    )
    customer_email_snapshot = models.EmailField(
        max_length=320,
        blank=True,
        verbose_name="Email клиента (снимок)",
    )
    channel = models.CharField(
        max_length=16,
        choices=Channel.choices,
        verbose_name="Канал",
    )
    source_external_user_id_snapshot = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="ID пользователя/обращения в канале (снимок)",
    )
    is_new_customer = models.BooleanField(default=False, verbose_name="Новый клиент")
    receiving_type = models.CharField(
        max_length=16,
        choices=ReceivingType.choices,
        verbose_name="Способ получения",
    )
    desired_date = models.DateField(null=True, blank=True, verbose_name="Желаемая дата")
    desired_time_interval = models.CharField(
        max_length=8,
        choices=TimeInterval.choices,
        blank=True,
        verbose_name="Интервал времени",
    )
    delivery_address = models.TextField(blank=True, verbose_name="Адрес доставки")
    customer_comment = models.TextField(blank=True, verbose_name="Комментарий клиента")
    manager_comment = models.TextField(blank=True, verbose_name="Комментарий менеджера")
    items_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Сумма товаров",
    )
    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Скидка",
    )
    delivery_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Доставка",
    )
    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Итого",
    )
    order_status = models.CharField(
        max_length=16,
        choices=OrderStatus.choices,
        default=OrderStatus.NEW,
        verbose_name="Статус заказа",
    )
    payment_status = models.CharField(
        max_length=16,
        choices=PaymentStatus.choices,
        default=PaymentStatus.UNPAID,
        verbose_name="Статус оплаты",
    )
    payment_method = models.CharField(
        max_length=32,
        choices=PaymentMethod.choices,
        verbose_name="Способ оплаты",
    )

    class Meta:
        verbose_name = "Заказ"
        verbose_name_plural = "Заказы"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Заказ {self.public_number}"


class OrderItem(models.Model):
    """Позиция заказа со снимком данных о товаре."""

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Заказ",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="order_items",
        verbose_name="Товар",
    )
    product_name_snapshot = models.CharField(
        max_length=255,
        verbose_name="Название товара (снимок)",
    )
    product_unit_snapshot = models.CharField(
        max_length=16,
        verbose_name="Единица измерения (снимок)",
    )
    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
        verbose_name="Количество",
    )
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Цена за единицу",
    )
    total_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Сумма позиции",
    )

    class Meta:
        verbose_name = "Позиция заказа"
        verbose_name_plural = "Позиции заказа"

    def __str__(self) -> str:
        return f"{self.product_name_snapshot} × {self.quantity}"


class OrderStatusHistory(models.Model):
    """История изменений статуса заказа."""

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="status_history",
        verbose_name="Заказ",
    )
    event_datetime = models.DateTimeField(auto_now_add=True, verbose_name="Дата и время")
    old_status = models.CharField(
        max_length=16,
        choices=OrderStatus.choices,
        blank=True,
        verbose_name="Старый статус",
    )
    new_status = models.CharField(
        max_length=16,
        choices=OrderStatus.choices,
        verbose_name="Новый статус",
    )
    source = models.CharField(
        max_length=16,
        choices=StatusChangeSource.choices,
        verbose_name="Источник",
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Изменил",
    )
    comment = models.TextField(blank=True, verbose_name="Комментарий")

    class Meta:
        verbose_name = "История статуса"
        verbose_name_plural = "История статусов"
        ordering = ["-event_datetime"]

    def __str__(self) -> str:
        old = self.get_old_status_display() if self.old_status else "—"
        return f"{self.order.public_number}: {old} → {self.get_new_status_display()}"
