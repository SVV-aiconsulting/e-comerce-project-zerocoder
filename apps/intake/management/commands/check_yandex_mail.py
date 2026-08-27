"""Read-only проверка IMAP/SMTP конфигурации Яндекс Почты."""
from django.core.management.base import BaseCommand, CommandError

from apps.intake.channels.yandex_mail import check_yandex_connections
from apps.intake.exceptions import EmailConfigurationError, EmailProviderError


class Command(BaseCommand):
    help = "Check Yandex IMAP/SMTP TLS and authentication without reading messages."

    def handle(self, *args, **options):
        try:
            result = check_yandex_connections()
        except (EmailConfigurationError, EmailProviderError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"IMAP: {'OK' if result['imap'] else 'FAIL'}"))
        self.stdout.write(self.style.SUCCESS(f"SMTP: {'OK' if result['smtp'] else 'FAIL'}"))
        self.stdout.write("Письма не читались и не изменялись.")
