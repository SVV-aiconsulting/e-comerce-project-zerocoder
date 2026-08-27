from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.common.models import TimeStampedModel


class DiscountRule(TimeStampedModel):
    """Правило скидки, применяемое по приоритету."""

    name = models.CharField(
        max_length=255,
        verbose_name="Название",
        help_text="Обязательно. Понятное название правила для менеджеров.",
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Активно",
        help_text="Только активные правила участвуют в расчёте скидок.",
    )
    priority = models.PositiveIntegerField(
        default=100,
        verbose_name="Приоритет",
        help_text="Обязательно. Меньшее число — выше приоритет при нескольких подходящих правилах.",
    )
    min_order_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Минимальная сумма заказа",
    )
    min_customer_orders = models.PositiveIntegerField(
        default=0,
        verbose_name="Мин. количество заказов клиента",
    )
    discount_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        verbose_name="Скидка, %",
        help_text="Укажите процент или фиксированную сумму (или оба — применяется по логике сервиса).",
    )
    discount_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Скидка, сумма",
    )
    free_delivery = models.BooleanField(
        default=False,
        verbose_name="Бесплатная доставка",
        help_text="Если включено, правило даёт бесплатную доставку при выполнении условий.",
    )
    date_start = models.DateField(
        null=True,
        blank=True,
        verbose_name="Дата начала",
        help_text="Необязательно. Правило действует с этой даты включительно.",
    )
    date_end = models.DateField(
        null=True,
        blank=True,
        verbose_name="Дата окончания",
        help_text="Необязательно. Правило действует до этой даты включительно.",
    )
    comment = models.TextField(blank=True, verbose_name="Комментарий")

    class Meta:
        verbose_name = "Правило скидки"
        verbose_name_plural = "Правила скидок"
        ordering = ["priority", "name"]

    def __str__(self) -> str:
        return self.name
