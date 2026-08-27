"""Безопасная live-проверка расчёта в тестовом контуре Яндекс Доставки."""

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from apps.common.enums import PaymentMethod
from apps.delivery.exceptions import DeliveryError
from apps.delivery.models import DeliveryEnvironment, LastMilePolicy
from apps.delivery.quote_service import DeliveryPackage, build_pricing_payload
from apps.delivery.yandex.client import YandexDeliveryClient


class Command(BaseCommand):
    help = (
        "Выполняет только предварительный расчёт стоимости в test-контуре. "
        "Заявка и доставка не создаются."
    )

    def handle(self, *args, **options):
        client = YandexDeliveryClient()
        if client.config.environment != DeliveryEnvironment.TEST:
            raise CommandError(
                "Команда намеренно работает только при YANDEX_DELIVERY_ENVIRONMENT=test"
            )

        payload = build_pricing_payload(
            station_id=client.config.station_id,
            destination_station_id="01946f4f013c7337874ec2fb848a58a4",
            package=DeliveryPackage(
                weight_gross=100,
                length_cm=10,
                width_cm=10,
                height_cm=10,
            ),
            items_total=Decimal("100.00"),
            payment_method=PaymentMethod.CARD_PREPAYMENT,
            last_mile_policy=LastMilePolicy.SELF_PICKUP,
        )
        try:
            result = client.calculate_price(payload)
        except DeliveryError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                "Яндекс Доставка test: OK; "
                f"стоимость={result.amount} {result.currency}; "
                f"срок={result.delivery_days} дн."
            )
        )
        self.stdout.write("Заявка, оффер и доставка не создавались.")
