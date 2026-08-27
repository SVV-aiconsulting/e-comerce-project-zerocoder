from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.common.enums import Channel, CustomerSource, CustomerStatus
from apps.common.models import TimeStampedModel
from apps.customers.validators import validate_phone


class Customer(TimeStampedModel):
    """Клиент магазина со сводной статистикой по заказам."""

    public_code = models.CharField(
        max_length=32,
        unique=True,
        verbose_name="Код клиента",
        help_text="Создаётся автоматически при сохранении. Вручную заполнять не нужно.",
    )
    name = models.CharField(
        max_length=255,
        verbose_name="Имя",
        help_text="Обязательно. Имя клиента для заказов и CRM.",
    )
    phone = models.CharField(
        max_length=11,
        blank=True,
        default="",
        db_index=True,
        validators=[validate_phone],
        verbose_name="Телефон",
        help_text=(
            "Необязательно для email-клиента. Формат хранения: 79991234567. "
            "Можно ввести +7, 8… или 10 цифр с 9 — система приведёт к единому виду."
        ),
    )
    email = models.EmailField(
        max_length=320,
        blank=True,
        default="",
        db_index=True,
        verbose_name="Email",
        help_text="Необязательно. Хранится в нормализованном нижнем регистре.",
    )
    first_source = models.CharField(
        max_length=16,
        choices=CustomerSource.choices,
        verbose_name="Первый источник",
        help_text="Обязательно. При ручном создании в админке выберите «Менеджер».",
    )
    first_order_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Первый заказ",
    )
    last_order_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Последний заказ",
    )
    orders_count = models.PositiveIntegerField(default=0, verbose_name="Количество заказов")
    total_orders_sum = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        verbose_name="Сумма заказов",
    )
    status = models.CharField(
        max_length=16,
        choices=CustomerStatus.choices,
        default=CustomerStatus.NEW,
        verbose_name="Статус",
        help_text="По умолчанию «Новый». Меняется автоматически после первого заказа.",
    )
    marketing_consent = models.BooleanField(
        default=False,
        verbose_name="Согласие на маркетинг",
    )
    personal_data_consent = models.BooleanField(
        default=False,
        verbose_name="Согласие на обработку ПД",
    )
    personal_data_consent_link = models.URLField(
        blank=True,
        verbose_name="Ссылка на согласие",
    )
    phone_verified_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Телефон подтверждён",
        help_text="Заполняется автоматически при подтверждении через бота или API. Вручную — только при проверке менеджером.",
    )
    email_verified_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Email подтверждён",
        help_text="Заполняется после подтверждения владения почтовым адресом.",
    )
    manager_comment = models.TextField(
        blank=True,
        verbose_name="Комментарий менеджера",
        help_text="Необязательно. Внутренние заметки, не видны клиенту.",
    )

    class Meta:
        verbose_name = "Клиент"
        verbose_name_plural = "Клиенты"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(phone="") | ~Q(email=""),
                name="customers_customer_has_contact",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.public_code})"


class CustomerChannelIdentity(TimeStampedModel):
    """Связь клиента с идентификатором пользователя во внешнем канале."""

    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="channel_identities",
        verbose_name="Клиент",
    )
    channel = models.CharField(
        max_length=16,
        choices=Channel.choices,
        verbose_name="Канал",
        help_text="Обязательно. Платформа: Telegram, ВКонтакте, MAX, сайт или email.",
    )
    external_user_id = models.CharField(
        max_length=128,
        verbose_name="Идентификатор пользователя",
        help_text="Обязательно. ID пользователя в выбранном канале (например, telegram user id).",
    )
    username = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Имя пользователя",
        help_text="Необязательно. @username или ник в канале.",
    )

    class Meta:
        verbose_name = "Канал клиента"
        verbose_name_plural = "Каналы клиентов"
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "external_user_id"],
                name="unique_channel_external_user",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.customer.name} — {self.get_channel_display()}:{self.external_user_id}"


class IdentityConflictStatus(models.TextChoices):
    PENDING = "pending", "Ожидает решения"
    MERGED = "merged", "Карточки объединены"
    KEPT_SEPARATE = "kept_separate", "Оставлены раздельно"
    IGNORED = "ignored", "Проигнорирован"


class ContactType(models.TextChoices):
    PHONE = "phone", "Телефон"
    EMAIL = "email", "Email"


class CustomerIdentityConflict(TimeStampedModel):
    """Неблокирующее совпадение контактов у разных карточек CRM."""

    source_customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="identity_conflicts",
        verbose_name="Текущая карточка",
    )
    matched_customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="matched_identity_conflicts",
        verbose_name="Совпавшая карточка",
    )
    contact_type = models.CharField(
        max_length=16,
        choices=ContactType.choices,
        verbose_name="Тип контакта",
    )
    contact_value = models.CharField(
        max_length=320,
        db_index=True,
        verbose_name="Нормализованный контакт",
    )
    source_channel = models.CharField(
        max_length=16,
        choices=Channel.choices,
        verbose_name="Канал обнаружения",
    )
    source_external_user_id = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="ID пользователя/обращения",
    )
    status = models.CharField(
        max_length=24,
        choices=IdentityConflictStatus.choices,
        default=IdentityConflictStatus.PENDING,
        verbose_name="Статус",
    )
    resolution_comment = models.TextField(blank=True, verbose_name="Решение менеджера")
    resolved_at = models.DateTimeField(null=True, blank=True, verbose_name="Решён")
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_customer_identity_conflicts",
        verbose_name="Решил",
    )

    class Meta:
        verbose_name = "Конфликт идентификации"
        verbose_name_plural = "Конфликты идентификации"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(source_customer=models.F("matched_customer")),
                name="customers_identity_conflict_different_customers",
            ),
            models.UniqueConstraint(
                fields=[
                    "source_customer",
                    "matched_customer",
                    "contact_type",
                    "contact_value",
                    "source_channel",
                ],
                name="customers_unique_identity_conflict",
            ),
        ]
        indexes = [
            models.Index(
                fields=["status", "created_at"],
                name="customers_conflict_status_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.get_contact_type_display()} {self.contact_value}: "
            f"{self.source_customer} ↔ {self.matched_customer}"
        )
