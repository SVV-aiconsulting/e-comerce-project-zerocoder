import hashlib
import uuid

from django.core.exceptions import ValidationError
from django.db import models

from apps.common.enums import Channel


class PersonalDataDocumentType(models.TextChoices):
    POLICY = "privacy_policy", "Политика обработки персональных данных"
    CONSENT = "personal_data_consent", "Согласие на обработку персональных данных"


class ConsentStatus(models.TextChoices):
    GRANTED = "granted", "Дано"
    DECLINED = "declined", "Отказ"
    WITHDRAWN = "withdrawn", "Отозвано"
    SUPERSEDED = "superseded", "Заменено новой версией"


class ConsentMethod(models.TextChoices):
    WEBSITE_CHECKBOX = "website_checkbox", "Чекбокс сайта"
    BOT_BUTTON = "bot_button", "Кнопка бота"
    WEBSITE_WITHDRAWAL = "website_withdrawal", "Форма отзыва на сайте"
    BOT_COMMAND = "bot_command", "Команда бота"


class IdentityLevel(models.TextChoices):
    PLATFORM_ACCOUNT = "platform_account", "ID аккаунта платформы"
    WEBSITE_SESSION = "website_session", "ID сессии сайта"


class IdentityType(models.TextChoices):
    TELEGRAM_USER_ID = "telegram_user_id", "Telegram user ID"
    VK_USER_ID = "vk_user_id", "VK user ID"
    MAX_USER_ID = "max_user_id", "MAX user ID"
    WEBSITE_SESSION_ID = "website_session_id", "ID сессии сайта"


class PersonalDataDocumentVersion(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    document_type = models.CharField(max_length=32, choices=PersonalDataDocumentType.choices)
    version = models.CharField(max_length=32)
    effective_from = models.DateField()
    content = models.TextField()
    content_hash = models.CharField(max_length=64, editable=False)
    public_path = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Версия документа ПДн"
        verbose_name_plural = "Версии документов ПДн"
        constraints = [models.UniqueConstraint(fields=["document_type", "version"], name="privacy_unique_document_version")]
        ordering = ["document_type", "-effective_from", "-id"]

    def save(self, *args, **kwargs):
        content_hash = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if self.pk:
            original = type(self).objects.get(pk=self.pk)
            if any(getattr(self, field) != getattr(original, field) for field in ("document_type", "version", "effective_from", "content", "content_hash", "public_path")):
                raise ValidationError("Опубликованная версия документа неизменяема. Создайте новую версию.")
        self.content_hash = content_hash
        super().save(*args, **kwargs)


class PersonalDataConsentEvent(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    customer = models.ForeignKey("customers.Customer", null=True, blank=True, on_delete=models.SET_NULL, related_name="personal_data_consent_events")
    identity_type = models.CharField(max_length=32, choices=IdentityType.choices)
    identity_value = models.CharField(max_length=255)
    identification_level = models.CharField(max_length=32, choices=IdentityLevel.choices)
    channel = models.CharField(max_length=16, choices=Channel.choices)
    source = models.CharField(max_length=64)
    purpose = models.CharField(max_length=255, default="Обработка персональных данных для работы сервиса и исполнения заказа")
    status = models.CharField(max_length=16, choices=ConsentStatus.choices)
    occurred_at = models.DateTimeField()
    consent_document = models.ForeignKey(PersonalDataDocumentVersion, on_delete=models.PROTECT, related_name="consent_events")
    policy_document = models.ForeignKey(PersonalDataDocumentVersion, on_delete=models.PROTECT, related_name="policy_events")
    expression_method = models.CharField(max_length=32, choices=ConsentMethod.choices)
    evidence = models.JSONField(default=dict, blank=True)
    previous_event = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="next_events")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Событие согласия ПДн"
        verbose_name_plural = "Реестр согласий ПДн"
        indexes = [models.Index(fields=["channel", "identity_value", "-occurred_at"])]
        ordering = ["-occurred_at", "-id"]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Записи реестра согласий неизменяемы; создайте новое событие.")
        super().save(*args, **kwargs)
