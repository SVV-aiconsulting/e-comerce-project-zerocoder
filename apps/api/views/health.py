"""Health-check endpoint."""
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthCheckView(APIView):
    """Простая конечная точка проверки работоспособности для мониторинга."""

    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return Response({"status": "успешно"})


class ReadinessView(APIView):
    authentication_classes = []
    permission_classes = []
    def get(self, request):
        from django.db import connection
        from django.conf import settings
        import redis
        ready = True
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            redis.Redis.from_url(settings.CELERY_BROKER_URL, socket_connect_timeout=1, socket_timeout=1).ping()
        except Exception:
            ready = False
        return Response({"ready": ready}, status=200 if ready else 503)


class HeartbeatView(APIView):
    from apps.api.auth import AdapterTokenAuthentication
    authentication_classes = [AdapterTokenAuthentication]
    permission_classes = []
    def post(self, request):
        from apps.common.tasks import heartbeat
        name = request.data.get("name")
        if name not in ("bot:telegram", "bot:vk"):
            return Response({"error": "invalid_name"}, status=400)
        heartbeat(name)
        return Response({"ok": True})
