from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from apps.common.models import TimeStampedModel


class DeliveryProvider(models.TextChoices):
    YANDEX_RUSSIA = "yandex_russia", "Яндекс Доставка по России"
    LOCAL_RULE = "local_rule", "Локальное правило"


class DeliveryEnvironment(models.TextChoices):
    TEST = "test", "Тестовый контур"
    PRODUCTION = "production", "Коммерческий контур"
    LOCAL = "local", "Локальный режим"


class DeliveryQuoteKind(models.TextChoices):
    PRELIMINARY = "preliminary", "Предварительная оценка"
    OFFER = "offer", "Оффер для бронирования"
    FALLBACK = "fallback", "Локальный fallback"


class DeliveryQuoteStatus(models.TextChoices):
    SUCCEEDED = "succeeded", "Рассчитан"
    FAILED = "failed", "Ошибка"
    EXPIRED = "expired", "Истёк"
    SELECTED = "selected", "Выбран"


class LastMilePolicy(models.TextChoices):
    TIME_INTERVAL = "time_interval", "Курьер до двери"
    SELF_PICKUP = "self_pickup", "Пункт выдачи"


class ShipmentStatus(models.TextChoices):
    DRAFT = "draft", "Черновик"
    CONFIRMING = "confirming", "Подтверждается"
    CONFIRMED = "confirmed", "Подтверждено"
    IN_TRANSIT = "in_transit", "В пути"
    DELIVERED = "delivered", "Доставлено"
    CANCELLING = "cancelling", "Отменяется"
    CANCELLED = "cancelled", "Отменено"
    FAILED = "failed", "Ошибка"


class DeliveryOperation(models.TextChoices):
    PRICING = "pricing", "Расчёт"
    OFFERS_CREATE = "offers_create", "Создание офферов"
    OFFER_CONFIRM = "offer_confirm", "Подтверждение оффера"
    INFO = "info", "Получение статуса"
    CANCEL = "cancel", "Отмена"
    POLL = "poll", "Синхронизация"


class DeliveryRule(TimeStampedModel):
    """Правило расчёта стоимости доставки."""

    name = models.CharField(
        max_length=255,
        verbose_name="Название",
        help_text="Обязательно. Понятное название правила для менеджеров.",
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Активно",
        help_text="Только активные правила участвуют в расчёте доставки.",
    )
    delivery_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Стоимость доставки",
        help_text="Обязательно. Базовая стоимость доставки в рублях.",
    )
    free_delivery_from = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Бесплатная доставка от суммы",
        help_text="Необязательно. При сумме заказа от этой величины доставка бесплатна.",
    )
    min_order_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Минимальная сумма заказа",
    )
    delivery_zone = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Зона доставки",
        help_text="Необязательно. Район или город, к которому применяется правило.",
    )
    comment = models.TextField(
        blank=True,
        verbose_name="Комментарий",
        help_text="Необязательно. Внутренние пояснения для менеджеров.",
    )

    class Meta:
        verbose_name = "Правило доставки"
        verbose_name_plural = "Правила доставки"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.name


class DeliveryQuote(TimeStampedModel):
    """Снимок расчёта или оффера внешней службы доставки."""

    cart = models.ForeignKey(
        "carts.Cart",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="delivery_quotes",
        verbose_name="Корзина",
    )

    order_draft = models.ForeignKey(
        "intake.OrderDraft",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="delivery_quotes",
        verbose_name="Черновик заказа",
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="delivery_quotes",
        verbose_name="Заказ",
    )
    provider = models.CharField(
        max_length=32,
        choices=DeliveryProvider.choices,
        default=DeliveryProvider.YANDEX_RUSSIA,
        verbose_name="Провайдер",
    )
    environment = models.CharField(
        max_length=16,
        choices=DeliveryEnvironment.choices,
        default=DeliveryEnvironment.TEST,
        verbose_name="Контур",
    )
    kind = models.CharField(
        max_length=16,
        choices=DeliveryQuoteKind.choices,
        default=DeliveryQuoteKind.PRELIMINARY,
        verbose_name="Тип расчёта",
    )
    status = models.CharField(
        max_length=16,
        choices=DeliveryQuoteStatus.choices,
        default=DeliveryQuoteStatus.SUCCEEDED,
        verbose_name="Статус",
    )
    request_fingerprint = models.CharField(
        max_length=64,
        db_index=True,
        verbose_name="Отпечаток запроса",
    )
    operator_request_id = models.CharField(
        max_length=128,
        blank=True,
        verbose_name="Внутренний ID запроса",
    )
    external_offer_id = models.CharField(
        max_length=128,
        blank=True,
        db_index=True,
        verbose_name="ID оффера Яндекса",
    )
    last_mile_policy = models.CharField(
        max_length=16,
        choices=LastMilePolicy.choices,
        default=LastMilePolicy.TIME_INTERVAL,
        verbose_name="Последняя миля",
    )
    destination_address = models.TextField(verbose_name="Адрес (снимок)")
    package_snapshot = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Вес и габариты (снимок)",
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Стоимость доставки",
    )
    currency = models.CharField(max_length=3, default="RUB", verbose_name="Валюта")
    delivery_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Расчётный срок, дней",
    )
    expires_at = models.DateTimeField(null=True, blank=True, verbose_name="Действует до")
    delivery_from = models.DateTimeField(null=True, blank=True, verbose_name="Доставка с")
    delivery_to = models.DateTimeField(null=True, blank=True, verbose_name="Доставка до")
    pickup_from = models.DateTimeField(null=True, blank=True, verbose_name="Забор с")
    pickup_to = models.DateTimeField(null=True, blank=True, verbose_name="Забор до")
    request_payload = models.JSONField(default=dict, blank=True, verbose_name="Запрос")
    response_payload = models.JSONField(default=dict, blank=True, verbose_name="Ответ")
    error_code = models.CharField(max_length=128, blank=True, verbose_name="Код ошибки")
    error_message = models.TextField(blank=True, verbose_name="Ошибка")

    class Meta:
        verbose_name = "Расчёт доставки"
        verbose_name_plural = "Расчёты доставки"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "environment", "external_offer_id"],
                condition=~Q(external_offer_id=""),
                name="delivery_unique_external_offer",
            ),
        ]
        indexes = [
            models.Index(
                fields=["provider", "environment", "status", "created_at"],
                name="delivery_quote_lookup_idx",
            ),
        ]

    def __str__(self) -> str:
        target = self.order or self.order_draft or "без заказа"
        return f"{self.get_provider_display()}: {target} — {self.get_status_display()}"


class Shipment(TimeStampedModel):
    """Забронированная доставка, связанная с финальным заказом CRM."""

    order = models.OneToOneField(
        "orders.Order",
        on_delete=models.PROTECT,
        related_name="shipment",
        verbose_name="Заказ",
    )
    quote = models.ForeignKey(
        DeliveryQuote,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shipments",
        verbose_name="Выбранный расчёт",
    )
    provider = models.CharField(
        max_length=32,
        choices=DeliveryProvider.choices,
        default=DeliveryProvider.YANDEX_RUSSIA,
        verbose_name="Провайдер",
    )
    environment = models.CharField(
        max_length=16,
        choices=DeliveryEnvironment.choices,
        default=DeliveryEnvironment.TEST,
        verbose_name="Контур",
    )
    status = models.CharField(
        max_length=16,
        choices=ShipmentStatus.choices,
        default=ShipmentStatus.DRAFT,
        verbose_name="Статус",
    )
    external_request_id = models.CharField(
        max_length=128,
        blank=True,
        db_index=True,
        verbose_name="ID заявки Яндекса",
    )
    external_status = models.CharField(max_length=128, blank=True, verbose_name="Статус Яндекса")
    tracking_url = models.URLField(max_length=1000, blank=True, verbose_name="Трекинг")
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Стоимость доставки",
    )
    currency = models.CharField(max_length=3, default="RUB", verbose_name="Валюта")
    delivery_from = models.DateTimeField(null=True, blank=True, verbose_name="Доставка с")
    delivery_to = models.DateTimeField(null=True, blank=True, verbose_name="Доставка до")
    last_synced_at = models.DateTimeField(null=True, blank=True, verbose_name="Синхронизировано")
    last_error = models.TextField(blank=True, verbose_name="Последняя ошибка")
    creation_payload = models.JSONField(default=dict, blank=True, verbose_name="Payload создания")
    provider_payload = models.JSONField(default=dict, blank=True, verbose_name="Последний ответ")

    class Meta:
        verbose_name = "Доставка заказа"
        verbose_name_plural = "Доставки заказов"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "environment", "external_request_id"],
                condition=~Q(external_request_id=""),
                name="delivery_unique_external_request",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.order.public_number}: {self.get_status_display()}"


class DeliverySyncEvent(TimeStampedModel):
    """Аудит вызовов API доставки без токенов и других секретов."""

    shipment = models.ForeignKey(
        Shipment,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="sync_events",
        verbose_name="Доставка",
    )
    quote = models.ForeignKey(
        DeliveryQuote,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="sync_events",
        verbose_name="Расчёт",
    )
    operation = models.CharField(max_length=24, choices=DeliveryOperation.choices)
    succeeded = models.BooleanField(default=False, verbose_name="Успешно")
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=128, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        verbose_name = "Событие синхронизации доставки"
        verbose_name_plural = "События синхронизации доставки"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(shipment__isnull=False) | Q(quote__isnull=False),
                name="delivery_sync_event_has_target",
            ),
        ]

    def __str__(self) -> str:
        result = "OK" if self.succeeded else "ERROR"
        return f"{self.get_operation_display()}: {result}"
