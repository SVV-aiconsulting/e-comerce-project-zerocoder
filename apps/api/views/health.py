"""Health-check endpoint."""
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthCheckView(APIView):
    """Простая конечная точка проверки работоспособности для мониторинга."""

    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return Response({"status": "успешно"})
