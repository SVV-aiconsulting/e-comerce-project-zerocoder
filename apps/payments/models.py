"""Финансовые сущности WebMarket: платежи, webhooks и возвраты."""
import uuid

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from apps.common.models import TimeStampedModel


class PaymentProvider(models.TextChoices):
    YOOKASSA = "yookassa", "ЮKassa"


class PaymentEnvironment(models.TextChoices):
    TEST = "test", "Тестовый магазин"
    PRODUCTION = "production", "Коммерческий магазин"


class PaymentState(models.TextChoices):
    PENDING = "pending", "Ожидает подтверждения"
    WAITING_FOR_CAPTURE = "waiting_for_capture", "Ожидает списания"
    SUCCEEDED = "succeeded", "Оплачен"
    CANCELED = "canceled", "Отменён"
    FAILED = "failed", "Ошибка создания"


class RefundState(models.TextChoices):
    PENDING = "pending", "Обрабатывается"
    SUCCEEDED = "succeeded", "Возвращён"
    CANCELED = "canceled", "Отменён"
    FAILED = "failed", "Ошибка"


class Payment(TimeStampedModel):
    """Одна попытка онлайн-оплаты заказа без хранения секретов провайдера."""

    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.PROTECT,
        related_name="payments",
        verbose_name="Заказ",
    )
    provider = models.CharField(
        max_length=32,
        choices=PaymentProvider.choices,
        default=PaymentProvider.YOOKASSA,
        verbose_name="Провайдер",
    )
    environment = models.CharField(
        max_length=16,
        choices=PaymentEnvironment.choices,
        default=PaymentEnvironment.TEST,
        verbose_name="Контур",
    )
    state = models.CharField(
        max_length=24,
        choices=PaymentState.choices,
        default=PaymentState.PENDING,
        verbose_name="Состояние",
    )
    idempotence_key = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        verbose_name="Ключ идемпотентности",
    )
    external_id = models.CharField(
        max_length=128,
        blank=True,
        db_index=True,
        verbose_name="ID платежа ЮKassa",
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
        verbose_name="Сумма",
    )
    currency = models.CharField(max_length=3, default="RUB", verbose_name="Валюта")
    description = models.CharField(max_length=256, verbose_name="Описание")
    confirmation_url = models.URLField(
        max_length=2000,
        blank=True,
        verbose_name="Ссылка на оплату",
    )
    expires_at = models.DateTimeField(null=True, blank=True, verbose_name="Действует до")
    paid_at = models.DateTimeField(null=True, blank=True, verbose_name="Оплачен")
    cancellation_code = models.CharField(max_length=128, blank=True)
    cancellation_description = models.TextField(blank=True)
    receipt_data = models.JSONField(default=dict, blank=True, verbose_name="Данные чека")
    receipt_registration_status = models.CharField(
        max_length=32,
        blank=True,
        verbose_name="Статус регистрации чека",
    )
    receipt_registration_error = models.TextField(
        blank=True,
        verbose_name="Ошибка регистрации чека",
    )
    provider_payload = models.JSONField(default=dict, blank=True, verbose_name="Ответ ЮKassa")
    last_error = models.TextField(blank=True, verbose_name="Последняя ошибка")
    paid_notification_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Уведомление об оплате отправлено",
    )
    paid_notification_attempts = models.PositiveSmallIntegerField(default=0)
    paid_notification_error = models.TextField(blank=True)

    class Meta:
        verbose_name = "Платёж"
        verbose_name_plural = "Платежи"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "environment", "external_id"],
                condition=~Q(external_id=""),
                name="payments_unique_external_payment",
            ),
        ]
        indexes = [
            models.Index(
                fields=["order", "state", "created_at"],
                name="payments_order_state_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.order.public_number}: {self.get_state_display()}"


class PaymentWebhookEvent(TimeStampedModel):
    """Неизменяемый журнал входящих уведомлений, включая повторы и ошибки."""

    payment = models.ForeignKey(
        Payment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="webhook_events",
        verbose_name="Платёж",
    )
    provider = models.CharField(max_length=32, choices=PaymentProvider.choices)
    event_type = models.CharField(max_length=128, blank=True)
    fingerprint = models.CharField(max_length=64, unique=True, verbose_name="Отпечаток")
    remote_ip = models.GenericIPAddressField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    verified = models.BooleanField(default=False, verbose_name="Проверено у провайдера")
    processed = models.BooleanField(default=False, verbose_name="Обработано")
    processing_error = models.TextField(blank=True)

    class Meta:
        verbose_name = "Webhook платежа"
        verbose_name_plural = "Webhook-события платежей"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.provider}: {self.event_type or 'unknown'}"


class Refund(TimeStampedModel):
    """Полный или частичный возврат, инициированный после успешной оплаты."""

    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        related_name="refunds",
        verbose_name="Платёж",
    )
    state = models.CharField(
        max_length=24,
        choices=RefundState.choices,
        default=RefundState.PENDING,
        verbose_name="Состояние",
    )
    idempotence_key = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        verbose_name="Ключ идемпотентности",
    )
    external_id = models.CharField(max_length=128, blank=True, db_index=True)
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    currency = models.CharField(max_length=3, default="RUB")
    reason = models.CharField(max_length=256, blank=True)
    receipt_data = models.JSONField(default=dict, blank=True)
    provider_payload = models.JSONField(default=dict, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        verbose_name = "Возврат"
        verbose_name_plural = "Возвраты"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["payment", "external_id"],
                condition=~Q(external_id=""),
                name="payments_unique_external_refund",
            ),
        ]

    def __str__(self) -> str:
        return f"Возврат {self.payment.order.public_number}: {self.amount} {self.currency}"
