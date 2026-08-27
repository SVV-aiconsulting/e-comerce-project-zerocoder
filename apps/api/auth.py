"""Аутентификация доверенных frontend-адаптеров."""
from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied


class AdapterTokenAuthentication(BaseAuthentication):
    """Проверка заголовка X-Adapter-Token для защищённых endpoints."""

    def authenticate(self, request):
        tokens = set(getattr(settings, "ADAPTER_API_TOKENS", []))
        if not tokens:
            raise PermissionDenied("Сервис API не настроен для внешнего доступа")

        token = request.headers.get("X-Adapter-Token", "")
        if not token or token not in tokens:
            raise AuthenticationFailed("Недействительный токен адаптера")

        return (None, token)


def catalog_requires_adapter_token() -> bool:
    """Каталог закрыт токеном, если ADAPTER_API_PUBLIC_CATALOG=False."""
    return not getattr(settings, "ADAPTER_API_PUBLIC_CATALOG", True)
