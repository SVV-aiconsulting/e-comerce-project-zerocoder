"""Загрузка демонстрационных данных для витрины на VPS."""
from decimal import Decimal

from django.core.management.base import BaseCommand

from apps.catalog.models import Product
from apps.common.enums import ProductUnit
from apps.delivery.models import DeliveryRule
from apps.discounts.models import DiscountRule


class Command(BaseCommand):
    help = "Создать демонстрационные товары, правило доставки и скидки (идемпотентно)"

    def handle(self, *args, **options):
        products_data = [
            {
                "public_code": "DEMO-URCHIN",
                "name": "Морской еж (свежий)",
                "description": "Свежий морской еж. Идеален для дегустации и заказа через бота.",
                "unit": ProductUnit.PIECE,
                "min_quantity": Decimal("1"),
                "base_price": Decimal("1500.00"),
                "sort_order": 1,
            },
            {
                "public_code": "DEMO-URCHIN-CLEAN",
                "name": "Очищенный морской еж",
                "description": "Готовый к употреблению продукт без лишней подготовки.",
                "unit": ProductUnit.PIECE,
                "min_quantity": Decimal("1"),
                "base_price": Decimal("2200.00"),
                "sort_order": 2,
            },
            {
                "public_code": "DEMO-SET",
                "name": "Дегустационный набор",
                "description": "Набор из нескольких позиций для знакомства с ассортиментом.",
                "unit": ProductUnit.PACKAGE,
                "min_quantity": Decimal("1"),
                "base_price": Decimal("3500.00"),
                "sort_order": 3,
            },
        ]

        for data in products_data:
            product, created = Product.objects.get_or_create(
                public_code=data["public_code"],
                defaults={
                    "name": data["name"],
                    "description": data["description"],
                    "unit": data["unit"],
                    "min_quantity": data["min_quantity"],
                    "base_price": data["base_price"],
                    "sort_order": data["sort_order"],
                    "is_active": True,
                },
            )
            action = "Создан" if created else "Уже есть"
            self.stdout.write(f"{action}: товар «{product.name}» ({product.public_code})")

        delivery_rule, created = DeliveryRule.objects.get_or_create(
            name="Демо: стандартная доставка",
            defaults={
                "is_active": True,
                "delivery_cost": Decimal("300.00"),
                "free_delivery_from": Decimal("5000.00"),
                "min_order_amount": Decimal("0"),
                "delivery_zone": "Город",
                "comment": "Демонстрационное правило доставки",
            },
        )
        self.stdout.write(
            f"{'Создано' if created else 'Уже есть'}: правило доставки «{delivery_rule.name}»"
        )

        discount_rule, created = DiscountRule.objects.get_or_create(
            name="Демо: скидка 5% от 3000 ₽",
            defaults={
                "is_active": True,
                "priority": 100,
                "min_order_amount": Decimal("3000.00"),
                "discount_percent": Decimal("5.00"),
                "comment": "Демонстрационное правило скидки",
            },
        )
        self.stdout.write(
            f"{'Создано' if created else 'Уже есть'}: правило скидки «{discount_rule.name}»"
        )

        self.stdout.write(self.style.SUCCESS("Демонстрационные данные готовы."))
