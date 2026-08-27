"""Доменные ошибки подготовки AI-заказа."""


class IntakeError(Exception):
    """Базовая ошибка приложения intake."""


class DraftNotReadyError(IntakeError):
    """Черновик не готов к расчёту или подтверждению."""


class DraftStateError(IntakeError):
    """Операция недоступна в текущем состоянии черновика."""


class PermanentIntakeError(IntakeError):
    """Ошибка входящего события, которую повторная попытка не исправит."""


class LLMProviderError(IntakeError):
    """Временная ошибка LLM-провайдера или сети."""


class LLMConfigurationError(PermanentIntakeError):
    """Отсутствует или некорректна обязательная конфигурация LLM."""


class LLMResponseValidationError(IntakeError):
    """Ответ LLM не является допустимым structured output."""

    def __init__(self, message, *, run=None):
        super().__init__(message)
        self.run = run


class EmailConfigurationError(PermanentIntakeError):
    """Email-канал включён без обязательных безопасных настроек."""


class EmailProviderError(IntakeError):
    """Временная ошибка IMAP/SMTP провайдера."""
