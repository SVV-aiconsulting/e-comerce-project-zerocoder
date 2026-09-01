"""Загрузка демонстрационных данных для витрины на VPS."""
from decimal import Decimal

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand

from apps.catalog.models import Product, ProductAlias, ProductImage, normalize_product_text
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
                "delivery_weight_grams": 300,
                "delivery_length_cm": 12,
                "delivery_width_cm": 12,
                "delivery_height_cm": 12,
            },
            {
                "public_code": "DEMO-URCHIN-CLEAN",
                "name": "Очищенный морской еж",
                "description": "Готовый к употреблению продукт без лишней подготовки.",
                "unit": ProductUnit.PIECE,
                "min_quantity": Decimal("1"),
                "base_price": Decimal("2200.00"),
                "sort_order": 2,
                "delivery_weight_grams": 150,
                "delivery_length_cm": 12,
                "delivery_width_cm": 12,
                "delivery_height_cm": 5,
            },
            {
                "public_code": "DEMO-SET",
                "name": "Дегустационный набор",
                "description": "Набор из нескольких позиций для знакомства с ассортиментом.",
                "unit": ProductUnit.PACKAGE,
                "min_quantity": Decimal("1"),
                "base_price": Decimal("3500.00"),
                "sort_order": 3,
                "delivery_weight_grams": 1000,
                "delivery_length_cm": 30,
                "delivery_width_cm": 20,
                "delivery_height_cm": 15,
            },
            {
                "public_code": "DEMO-SALMON",
                "name": "Лосось",
                "description": "Свежий лосось для запекания, стейков и тартаров.",
                "unit": ProductUnit.KG,
                "min_quantity": Decimal("0.5"),
                "base_price": Decimal("1800.00"),
                "sort_order": 4,
                "delivery_weight_grams": 1000,
                "delivery_length_cm": 35,
                "delivery_width_cm": 25,
                "delivery_height_cm": 7,
                "aliases": ["сёмга", "семга", "рыба", "красная рыба"],
            },
            {
                "public_code": "DEMO-COD",
                "name": "Треска",
                "description": "Охлаждённое филе трески без кожи.",
                "unit": ProductUnit.KG,
                "min_quantity": Decimal("0.5"),
                "base_price": Decimal("950.00"),
                "sort_order": 5,
                "delivery_weight_grams": 1000,
                "delivery_length_cm": 35,
                "delivery_width_cm": 25,
                "delivery_height_cm": 7,
                "aliases": ["рыба", "белая рыба", "филе трески"],
            },
            {
                "public_code": "DEMO-SHRIMP",
                "name": "Креветки тигровые",
                "description": "Крупные тигровые креветки в заморозке.",
                "unit": ProductUnit.PACKAGE,
                "min_quantity": Decimal("1"),
                "base_price": Decimal("1250.00"),
                "sort_order": 6,
                "delivery_weight_grams": 1000,
                "delivery_length_cm": 25,
                "delivery_width_cm": 18,
                "delivery_height_cm": 6,
                "aliases": ["креветки", "тигровые креветки", "креветка"],
            },
            {
                "public_code": "DEMO-SCALLOP",
                "name": "Гребешок морской",
                "description": "Нежный морской гребешок для быстрой обжарки.",
                "unit": ProductUnit.PACKAGE,
                "min_quantity": Decimal("1"),
                "base_price": Decimal("1900.00"),
                "sort_order": 7,
                "delivery_weight_grams": 500,
                "delivery_length_cm": 20,
                "delivery_width_cm": 15,
                "delivery_height_cm": 5,
                "aliases": ["гребешок", "морской гребешок"],
            },
            {
                "public_code": "DEMO-MUSSELS",
                "name": "Мидии в створках",
                "description": "Замороженные мидии в створках для пасты и супов.",
                "unit": ProductUnit.PACKAGE,
                "min_quantity": Decimal("1"),
                "base_price": Decimal("690.00"),
                "sort_order": 8,
                "delivery_weight_grams": 1000,
                "delivery_length_cm": 30,
                "delivery_width_cm": 20,
                "delivery_height_cm": 10,
                "aliases": ["мидии", "мидия"],
            },
            {
                "public_code": "DEMO-SQUID",
                "name": "Кальмар очищенный",
                "description": "Очищенные тушки кальмара быстрой заморозки.",
                "unit": ProductUnit.PACKAGE,
                "min_quantity": Decimal("1"),
                "base_price": Decimal("780.00"),
                "sort_order": 9,
                "delivery_weight_grams": 1000,
                "delivery_length_cm": 25,
                "delivery_width_cm": 18,
                "delivery_height_cm": 6,
                "aliases": ["кальмар", "кальмары"],
            },
            {
                "public_code": "DEMO-TROUT",
                "name": "Форель",
                "description": "Свежая форель для запекания целиком или на стейки.",
                "unit": ProductUnit.KG,
                "min_quantity": Decimal("0.5"),
                "base_price": Decimal("1450.00"),
                "sort_order": 10,
                "delivery_weight_grams": 1000,
                "delivery_length_cm": 40,
                "delivery_width_cm": 25,
                "delivery_height_cm": 8,
                "aliases": ["форель", "рыба", "красная рыба"],
            },
            {
                "public_code": "DEMO-FLOUNDER",
                "name": "Камбала",
                "description": "Свежая камбала для жарки и запекания.",
                "unit": ProductUnit.KG,
                "min_quantity": Decimal("0.5"),
                "base_price": Decimal("890.00"),
                "sort_order": 11,
                "delivery_weight_grams": 1000,
                "delivery_length_cm": 40,
                "delivery_width_cm": 30,
                "delivery_height_cm": 6,
                "aliases": ["камбала", "рыба"],
            },
            {
                "public_code": "DEMO-CAVIAR",
                "name": "Икра лососёвая",
                "description": "Зернистая красная икра в стеклянной банке.",
                "unit": ProductUnit.PACKAGE,
                "min_quantity": Decimal("1"),
                "base_price": Decimal("3200.00"),
                "sort_order": 12,
                "delivery_weight_grams": 250,
                "delivery_length_cm": 12,
                "delivery_width_cm": 12,
                "delivery_height_cm": 8,
                "aliases": ["икра", "красная икра", "лососёвая икра"],
            },
            {
                "public_code": "DEMO-CRAB",
                "name": "Краб камчатский",
                "description": "Варёно-мороженое мясо камчатского краба.",
                "unit": ProductUnit.PACKAGE,
                "min_quantity": Decimal("1"),
                "base_price": Decimal("4500.00"),
                "sort_order": 13,
                "delivery_weight_grams": 1000,
                "delivery_length_cm": 35,
                "delivery_width_cm": 25,
                "delivery_height_cm": 10,
                "aliases": ["краб", "крабовое мясо", "камчатский краб"],
            },
            {
                "public_code": "DEMO-OCTOPUS",
                "name": "Осьминог",
                "description": "Мини-осьминоги для гриля и средиземноморских блюд.",
                "unit": ProductUnit.PACKAGE,
                "min_quantity": Decimal("1"),
                "base_price": Decimal("1750.00"),
                "sort_order": 14,
                "delivery_weight_grams": 1000,
                "delivery_length_cm": 25,
                "delivery_width_cm": 18,
                "delivery_height_cm": 6,
                "aliases": ["осьминог", "осьминоги"],
            },
            {
                "public_code": "DEMO-TUNA",
                "name": "Тунец",
                "description": "Стейк тунца быстрой заморозки.",
                "unit": ProductUnit.KG,
                "min_quantity": Decimal("0.5"),
                "base_price": Decimal("2100.00"),
                "sort_order": 15,
                "delivery_weight_grams": 1000,
                "delivery_length_cm": 30,
                "delivery_width_cm": 20,
                "delivery_height_cm": 6,
                "aliases": ["тунец", "рыба"],
            },
        ]

        delivery_fields = (
            "delivery_weight_grams",
            "delivery_length_cm",
            "delivery_width_cm",
            "delivery_height_cm",
        )
        for data in products_data:
            delivery_defaults = {field: data[field] for field in delivery_fields}
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
                    **delivery_defaults,
                },
            )
            changed_delivery_fields = [
                field
                for field, value in delivery_defaults.items()
                if getattr(product, field) != value
            ]
            if changed_delivery_fields:
                for field in changed_delivery_fields:
                    setattr(product, field, delivery_defaults[field])
                product.save(update_fields=changed_delivery_fields)
            for alias in data.get("aliases", []):
                ProductAlias.objects.get_or_create(
                    product=product,
                    normalized_alias=normalize_product_text(alias),
                    defaults={"alias": alias},
                )
            self._attach_catalog_image(product)
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

    def _attach_catalog_image(self, product: Product) -> None:
        catalog_dir = (
            settings.BASE_DIR / "frontends" / "website" / "static" / "website" / "catalog"
        )
        filenames = [f"{product.public_code}.jpg"]
        if product.public_code == "DEMO-URCHIN":
            filenames.append("DEMO-URCHIN-OPEN.jpg")
        for index, filename in enumerate(filenames, start=1):
            source = catalog_dir / filename
            if not source.is_file():
                continue
            is_main = index == 1
            if is_main and product.images.filter(is_main=True).exists():
                continue
            if not is_main and product.images.filter(alt_text=product.name).count() >= 2:
                continue
            with source.open("rb") as handle:
                ProductImage.objects.create(
                    product=product,
                    image=File(handle, name=filename),
                    alt_text=product.name,
                    is_main=is_main,
                    sort_order=index,
                )
