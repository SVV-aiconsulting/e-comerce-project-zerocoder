from decimal import Decimal
import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.catalog.models import Product
from apps.common.enums import Channel, PaymentMethod, ProductUnit, ReceivingType, TimeInterval
from apps.common.models import TimeStampedModel
from apps.customers.models import Customer
from apps.intake.enums import (
    ACTIVE_DRAFT_STATUSES,
    AIRunPurpose,
    AIRunStatus,
    ClarificationStatus,
    InboundEventKind,
    InboundEventStatus,
    ItemMatchStatus,
    OrderDraftStatus,
    OrderIntent,
    OutboundMessageStatus,
    ResolutionSource,
)


class OrderDraft(TimeStampedModel):
    """Проверяемый черновик заказа, формируемый в диалоге с клиентом."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_drafts",
        verbose_name="Клиент",
    )
    channel = models.CharField(max_length=16, choices=Channel.choices, verbose_name="Канал")
    external_user_id = models.CharField(max_length=255, verbose_name="ID пользователя")
    conversation_key = models.CharField(max_length=255, verbose_name="Ключ диалога")
    intent = models.CharField(
        max_length=32,
        choices=OrderIntent.choices,
        default=OrderIntent.UNKNOWN,
        verbose_name="Намерение",
    )
    status = models.CharField(
        max_length=32,
        choices=OrderDraftStatus.choices,
        default=OrderDraftStatus.COLLECTING,
        verbose_name="Статус",
    )
    receiving_type = models.CharField(
        max_length=16,
        choices=ReceivingType.choices,
        blank=True,
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
    payment_method = models.CharField(
        max_length=32,
        choices=PaymentMethod.choices,
        blank=True,
        verbose_name="Способ оплаты",
    )
    contact_phone = models.CharField(
        max_length=11,
        blank=True,
        verbose_name="Телефон для текущего заказа",
    )
    contact_email = models.EmailField(
        max_length=320,
        blank=True,
        verbose_name="Email для текущего заказа",
    )
    customer_comment = models.TextField(blank=True, verbose_name="Комментарий клиента")
    missing_fields = models.JSONField(default=list, blank=True, verbose_name="Недостающие поля")
    manager_attention_required = models.BooleanField(
        default=False,
        verbose_name="Требуется менеджер",
    )
    escalation_reason = models.TextField(blank=True, verbose_name="Причина передачи")
    revision = models.PositiveIntegerField(default=1, verbose_name="Версия черновика")
    previewed_revision = models.PositiveIntegerField(null=True, blank=True)
    confirmed_revision = models.PositiveIntegerField(null=True, blank=True)
    items_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    delivery_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    priced_at = models.DateTimeField(null=True, blank=True, verbose_name="Рассчитан")
    confirmed_at = models.DateTimeField(null=True, blank=True, verbose_name="Подтверждён")
    converted_order = models.OneToOneField(
        "orders.Order",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="source_draft",
        verbose_name="Созданный заказ",
    )

    class Meta:
        verbose_name = "Черновик AI-заказа"
        verbose_name_plural = "Черновики AI-заказов"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "conversation_key"],
                condition=Q(status__in=ACTIVE_DRAFT_STATUSES),
                name="intake_unique_active_draft_per_conversation",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "updated_at"], name="intake_draft_status_idx"),
            models.Index(
                fields=["channel", "conversation_key", "created_at"],
                name="intake_draft_conversation_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"Черновик {self.public_id} ({self.get_status_display()})"


class InboundEvent(TimeStampedModel):
    """Идемпотентное входящее событие от frontend-адаптера."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    channel = models.CharField(max_length=16, choices=Channel.choices, verbose_name="Канал")
    external_event_id = models.CharField(max_length=255, verbose_name="ID события в канале")
    external_user_id = models.CharField(max_length=255, verbose_name="ID пользователя")
    conversation_key = models.CharField(max_length=255, verbose_name="Ключ диалога")
    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inbound_events",
        verbose_name="Клиент",
    )
    draft = models.ForeignKey(
        OrderDraft,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
        verbose_name="Черновик",
    )
    kind = models.CharField(
        max_length=24,
        choices=InboundEventKind.choices,
        default=InboundEventKind.MESSAGE,
        verbose_name="Тип события",
    )
    raw_text = models.TextField(blank=True, verbose_name="Исходный текст")
    raw_payload = models.JSONField(default=dict, blank=True, verbose_name="Payload")
    payload_schema_version = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        verbose_name="Версия payload",
    )
    status = models.CharField(
        max_length=24,
        choices=InboundEventStatus.choices,
        default=InboundEventStatus.RECEIVED,
        verbose_name="Статус",
    )
    processing_attempts = models.PositiveSmallIntegerField(default=0, verbose_name="Попытки")
    processing_token = models.UUIDField(
        null=True,
        blank=True,
        editable=False,
        verbose_name="Токен обработки",
    )
    next_retry_at = models.DateTimeField(null=True, blank=True, verbose_name="Следующая попытка")
    started_at = models.DateTimeField(null=True, blank=True, verbose_name="Начало обработки")
    processed_at = models.DateTimeField(null=True, blank=True, verbose_name="Завершение")
    last_error = models.TextField(blank=True, verbose_name="Последняя ошибка")

    class Meta:
        verbose_name = "Входящее событие"
        verbose_name_plural = "Входящие события"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "external_event_id"],
                name="intake_unique_channel_event",
            ),
        ]
        indexes = [
            models.Index(
                fields=["status", "next_retry_at"],
                name="intake_event_queue_idx",
            ),
            models.Index(
                fields=["channel", "conversation_key", "created_at"],
                name="intake_event_conversation_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_channel_display()}:{self.external_event_id}"


class OrderDraftItem(TimeStampedModel):
    """Позиция черновика до окончательного сопоставления с каталогом."""

    draft = models.ForeignKey(
        OrderDraft,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Черновик",
    )
    line_number = models.PositiveIntegerField(verbose_name="Номер позиции")
    raw_product_name = models.CharField(max_length=255, verbose_name="Название из запроса")
    requested_quantity = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.001"))],
        verbose_name="Количество",
    )
    requested_unit = models.CharField(
        max_length=16,
        choices=ProductUnit.choices,
        blank=True,
        verbose_name="Единица",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="draft_items",
        verbose_name="Товар",
    )
    match_status = models.CharField(
        max_length=16,
        choices=ItemMatchStatus.choices,
        default=ItemMatchStatus.UNRESOLVED,
        verbose_name="Сопоставление",
    )
    candidate_product_ids = models.JSONField(default=list, blank=True)
    resolution_source = models.CharField(
        max_length=16,
        choices=ResolutionSource.choices,
        blank=True,
        verbose_name="Источник сопоставления",
    )
    resolution_confidence = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("1"))],
        verbose_name="Уверенность",
    )
    validation_errors = models.JSONField(default=list, blank=True)

    class Meta:
        verbose_name = "Позиция черновика"
        verbose_name_plural = "Позиции черновика"
        ordering = ["line_number", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["draft", "line_number"],
                name="intake_unique_draft_line",
            ),
            models.CheckConstraint(
                condition=Q(requested_quantity__isnull=True) | Q(requested_quantity__gt=0),
                name="intake_draft_item_positive_quantity",
            ),
            models.CheckConstraint(
                condition=~Q(match_status=ItemMatchStatus.MATCHED) | Q(product__isnull=False),
                name="intake_matched_item_has_product",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.line_number}. {self.raw_product_name}"


class AIExtractionRun(TimeStampedModel):
    """Аудит одного обращения к LLM без хранения секретов провайдера."""

    run_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    event = models.ForeignKey(
        InboundEvent,
        on_delete=models.PROTECT,
        related_name="ai_runs",
        verbose_name="Событие",
    )
    draft = models.ForeignKey(
        OrderDraft,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_runs",
        verbose_name="Черновик",
    )
    purpose = models.CharField(max_length=24, choices=AIRunPurpose.choices)
    status = models.CharField(
        max_length=24,
        choices=AIRunStatus.choices,
        default=AIRunStatus.PENDING,
    )
    provider = models.CharField(max_length=64, blank=True)
    model_name = models.CharField(max_length=128, blank=True)
    prompt_id = models.CharField(max_length=64)
    prompt_version = models.CharField(max_length=32)
    input_hash = models.CharField(max_length=64)
    raw_response = models.TextField(blank=True)
    structured_output = models.JSONField(default=dict, blank=True)
    validation_errors = models.JSONField(default=list, blank=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    input_tokens = models.PositiveIntegerField(null=True, blank=True)
    output_tokens = models.PositiveIntegerField(null=True, blank=True)
    estimated_cost = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Запуск AI-извлечения"
        verbose_name_plural = "Запуски AI-извлечения"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"], name="intake_ai_run_status_idx"),
            models.Index(fields=["prompt_id", "prompt_version"], name="intake_ai_prompt_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.prompt_id}:{self.prompt_version} ({self.get_status_display()})"


class Clarification(TimeStampedModel):
    """Один вопрос клиенту о конкретном поле черновика."""

    draft = models.ForeignKey(
        OrderDraft,
        on_delete=models.CASCADE,
        related_name="clarifications",
        verbose_name="Черновик",
    )
    field_path = models.CharField(max_length=255, verbose_name="Путь поля")
    question = models.TextField(verbose_name="Вопрос")
    status = models.CharField(
        max_length=16,
        choices=ClarificationStatus.choices,
        default=ClarificationStatus.PENDING,
    )
    trigger_event = models.ForeignKey(
        InboundEvent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="triggered_clarifications",
    )
    answered_by_event = models.ForeignKey(
        InboundEvent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="answered_clarifications",
    )
    answer_text = models.TextField(blank=True)
    attempt_number = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
    )
    asked_at = models.DateTimeField(default=timezone.now)
    answered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Уточнение"
        verbose_name_plural = "Уточнения"
        ordering = ["-asked_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["draft", "field_path"],
                condition=Q(status=ClarificationStatus.PENDING),
                name="intake_unique_pending_clarification",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.field_path}: {self.get_status_display()}"


class OutboundMessage(TimeStampedModel):
    """Durable-ответ клиенту; в MVP используется асинхронным email-каналом."""

    event = models.OneToOneField(
        InboundEvent,
        on_delete=models.PROTECT,
        related_name="outbound_message",
        verbose_name="Входящее событие",
    )
    channel = models.CharField(max_length=16, choices=Channel.choices)
    recipient = models.CharField(max_length=320)
    response_id = models.CharField(max_length=255)
    subject = models.CharField(max_length=998)
    body = models.TextField()
    headers = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=24,
        choices=OutboundMessageStatus.choices,
        default=OutboundMessageStatus.PENDING,
    )
    delivery_attempts = models.PositiveSmallIntegerField(default=0)
    processing_token = models.UUIDField(null=True, blank=True, editable=False)
    started_at = models.DateTimeField(null=True, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    provider_message_id = models.CharField(max_length=255, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        verbose_name = "Исходящее сообщение"
        verbose_name_plural = "Исходящие сообщения"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "recipient", "response_id"],
                name="intake_unique_outbound_response",
            )
        ]
        indexes = [
            models.Index(
                fields=["status", "next_retry_at"],
                name="intake_outbound_queue_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.channel}:{self.recipient} ({self.get_status_display()})"
