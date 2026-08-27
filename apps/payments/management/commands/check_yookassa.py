"""Безопасно проверить доступ к тестовому магазину ЮKassa."""
from django.core.management.base import BaseCommand, CommandError

from apps.payments.exceptions import PaymentError
from apps.payments.models import PaymentEnvironment
from apps.payments.yookassa.client import YooKassaClient


class Command(BaseCommand):
    help = "Read-only проверка ЮKassa через GET /payments?limit=1"

    def handle(self, *args, **options):
        client = YooKassaClient()
        if client.config.environment != PaymentEnvironment.TEST:
            raise CommandError(
                "Команда разрешена только для YOOKASSA_ENVIRONMENT=test"
            )
        try:
            response = client.list_payments(limit=1)
        except PaymentError as exc:
            raise CommandError(str(exc)) from exc
        items = response.get("items")
        count = len(items) if isinstance(items, list) else 0
        self.stdout.write(self.style.SUCCESS("ЮKassa test API: OK"))
        self.stdout.write(f"Получен read-only ответ, платежей в выборке: {count}.")
        self.stdout.write("Оплаты, счета, чеки и возвраты не создавались.")
