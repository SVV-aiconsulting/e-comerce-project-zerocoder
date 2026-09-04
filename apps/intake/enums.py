"""Состояния единого входящего события и черновика заказа."""
from django.db import models


class InboundEventKind(models.TextChoices):
    MESSAGE = "message", "Сообщение"
    CALLBACK = "callback", "Действие интерфейса"
    FORM_SUBMISSION = "form_submission", "Отправка формы"


class InboundEventStatus(models.TextChoices):
    RECEIVED = "received", "Получено"
    QUEUED = "queued", "В очереди"
    PROCESSING = "processing", "Обрабатывается"
    RETRY_SCHEDULED = "retry_scheduled", "Ожидает повтора"
    PROCESSED = "processed", "Обработано"
    FAILED = "failed", "Ошибка"
    IGNORED = "ignored", "Пропущено"


class OrderIntent(models.TextChoices):
    CREATE_ORDER = "create_order", "Создать заказ"
    MODIFY_ORDER = "modify_order", "Изменить заказ"
    CANCEL_ORDER = "cancel_order", "Отменить заказ"
    ORDER_STATUS = "order_status", "Узнать статус заказа"
    PRODUCT_QUESTION = "product_question", "Вопрос о товаре"
    UNKNOWN = "unknown", "Не определено"


class OrderDraftStatus(models.TextChoices):
    COLLECTING = "collecting", "Сбор данных"
    NEEDS_CLARIFICATION = "needs_clarification", "Требуется уточнение"
    READY_FOR_PREVIEW = "ready_for_preview", "Готов к расчёту"
    AWAITING_CONFIRMATION = "awaiting_confirmation", "Ожидает подтверждения"
    CONFIRMED = "confirmed", "Подтверждён"
    CONVERTED = "converted", "Преобразован в заказ"
    CANCELLED = "cancelled", "Отменён"
    ESCALATED = "escalated", "Передан менеджеру"
    FAILED = "failed", "Ошибка"


ACTIVE_DRAFT_STATUSES = (
    OrderDraftStatus.COLLECTING,
    OrderDraftStatus.NEEDS_CLARIFICATION,
    OrderDraftStatus.READY_FOR_PREVIEW,
    OrderDraftStatus.AWAITING_CONFIRMATION,
    OrderDraftStatus.CONFIRMED,
)


class ItemMatchStatus(models.TextChoices):
    UNRESOLVED = "unresolved", "Не сопоставлен"
    MATCHED = "matched", "Сопоставлен"
    AMBIGUOUS = "ambiguous", "Несколько вариантов"
    NOT_FOUND = "not_found", "Не найден"
    INVALID = "invalid", "Некорректен"


class ResolutionSource(models.TextChoices):
    EXACT = "exact", "Точное совпадение"
    ALIAS = "alias", "Синоним"
    TRIGRAM = "trigram", "Нечёткий поиск"
    FUZZY = "fuzzy", "Unicode fuzzy-поиск"
    SEMANTIC = "semantic", "Семантический поиск"
    CUSTOMER = "customer", "Уточнено клиентом"
    MANAGER = "manager", "Уточнено менеджером"


class AIRunPurpose(models.TextChoices):
    EXTRACTION = "extraction", "Извлечение заказа"
    CLARIFICATION = "clarification", "Формирование уточнения"
    DISAMBIGUATION = "disambiguation", "Разрешение неоднозначности"
    REPAIR = "repair", "Исправление структуры"


class AIRunStatus(models.TextChoices):
    PENDING = "pending", "Ожидает"
    SUCCEEDED = "succeeded", "Успешно"
    SCHEMA_INVALID = "schema_invalid", "Ошибка схемы"
    PROVIDER_ERROR = "provider_error", "Ошибка провайдера"
    SKIPPED = "skipped", "Пропущено"


class ClarificationStatus(models.TextChoices):
    PENDING = "pending", "Ожидает ответа"
    ANSWERED = "answered", "Получен ответ"
    SKIPPED = "skipped", "Пропущено"
    CANCELLED = "cancelled", "Отменено"


class OutboundMessageStatus(models.TextChoices):
    PENDING = "pending", "Ожидает отправки"
    SENDING = "sending", "Отправляется"
    RETRY_SCHEDULED = "retry_scheduled", "Ожидает повтора"
    SENT = "sent", "Отправлено"
    FAILED = "failed", "Ошибка"


class AssistantMessageRole(models.TextChoices):
    USER = "user", "Клиент"
    ASSISTANT = "assistant", "Ассистент"


class AssistantTurnStatus(models.TextChoices):
    RUNNING = "running", "Выполняется"
    SUCCEEDED = "succeeded", "Успешно"
    FAILED = "failed", "Ошибка"
    TOOL_LIMIT = "tool_limit", "Лимит инструментов"


class AssistantToolCallStatus(models.TextChoices):
    RUNNING = "running", "Выполняется"
    SUCCEEDED = "succeeded", "Успешно"
    FAILED = "failed", "Ошибка"
    REJECTED = "rejected", "Отклонено"
