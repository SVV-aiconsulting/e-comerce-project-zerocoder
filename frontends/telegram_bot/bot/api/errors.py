"""Ошибки HTTP-клиента REST API."""


class ApiError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        details: dict | None = None,
        status_code: int = 400,
    ) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        self.status_code = status_code
        super().__init__(message)


class BackendUnavailableError(Exception):
    """Backend недоступен (сеть, timeout)."""

    def __init__(self, message: str = "Backend недоступен") -> None:
        self.message = message
        super().__init__(message)
