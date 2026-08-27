import re
import unicodedata
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Q

from apps.common.enums import ProductUnit
from apps.common.models import TimeStampedModel


class ActiveProductManager(models.Manager):
    """Менеджер, возвращающий только активные товары."""

    def active(self):
        return self.filter(is_active=True).order_by("sort_order", "name")


class Product(TimeStampedModel):
    """Товар в каталоге магазина."""

    public_code = models.CharField(
        max_length=32,
        unique=True,
        verbose_name="Код товара",
        help_text="Создаётся автоматически при сохранении. Вручную заполнять не нужно.",
    )
    name = models.CharField(
        max_length=255,
        verbose_name="Наименование",
        help_text="Обязательно. Название товара в каталоге и заказах.",
    )
    unit = models.CharField(
        max_length=16,
        choices=ProductUnit.choices,
        default=ProductUnit.PIECE,
        verbose_name="Единица измерения",
        help_text="Обязательно. Штуки, килограммы и т.д.",
    )
    min_quantity = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        default=Decimal("1"),
        validators=[MinValueValidator(Decimal("0.001"))],
        verbose_name="Минимальное количество",
    )
    base_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Базовая цена",
        help_text="Обязательно. Цена за единицу измерения в рублях.",
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Активен",
        help_text="Снимите галочку, чтобы скрыть товар из каталога без удаления.",
    )
    description = models.TextField(
        blank=True,
        verbose_name="Описание",
        help_text="Необязательно. Текст для карточки товара.",
    )
    delivery_weight_grams = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        verbose_name="Вес для доставки, г",
        help_text=(
            "Вес брутто одной единицы товара. Для товара в килограммах укажите "
            "вес одного килограмма — 1000 г. Обязателен для расчёта Яндекс Доставки."
        ),
    )
    delivery_length_cm = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        verbose_name="Длина упаковки, см",
        help_text="Габарит одной единицы товара. Обязателен для Яндекс Доставки.",
    )
    delivery_width_cm = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        verbose_name="Ширина упаковки, см",
        help_text="Габарит одной единицы товара. Обязателен для Яндекс Доставки.",
    )
    delivery_height_cm = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        verbose_name="Высота упаковки, см",
        help_text="Габарит одной единицы товара. Обязателен для Яндекс Доставки.",
    )
    sort_order = models.PositiveIntegerField(default=0, verbose_name="Порядок сортировки")

    objects = models.Manager()
    active_objects = ActiveProductManager()

    class Meta:
        verbose_name = "Товар"
        verbose_name_plural = "Товары"
        ordering = ["sort_order", "name"]
        indexes = [
            models.Index(fields=["is_active", "sort_order"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.public_code})"

    @property
    def has_delivery_dimensions(self) -> bool:
        """Все ли весогабаритные характеристики готовы для внешнего расчёта."""

        return all(
            value is not None
            for value in (
                self.delivery_weight_grams,
                self.delivery_length_cm,
                self.delivery_width_cm,
                self.delivery_height_cm,
            )
        )


def normalize_product_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    return " ".join(re.sub(r"[^\w]+", " ", value).split())


class ProductAlias(TimeStampedModel):
    """Управляемый словарь названий товара на естественном языке."""

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="aliases",
        verbose_name="Товар",
    )
    alias = models.CharField(max_length=255, verbose_name="Синоним")
    normalized_alias = models.CharField(max_length=255, editable=False, db_index=True)

    class Meta:
        verbose_name = "Синоним товара"
        verbose_name_plural = "Синонимы товаров"
        ordering = ["alias", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "normalized_alias"],
                name="catalog_unique_product_alias",
            )
        ]

    def save(self, *args, **kwargs):
        self.normalized_alias = normalize_product_text(self.alias)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.alias} → {self.product.name}"


class ProductImage(models.Model):
    """Фотография товара, хранящаяся в медиафайлах Django."""

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="images",
        verbose_name="Товар",
    )
    image = models.ImageField(
        upload_to="products/%Y/%m/",
        verbose_name="Изображение",
    )
    alt_text = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Альтернативный текст",
        help_text="Необязательно. Описание изображения для доступности.",
    )
    is_main = models.BooleanField(
        default=False,
        verbose_name="Главное фото",
        help_text="Одно фото товара должно быть отмечено как главное.",
    )
    sort_order = models.PositiveIntegerField(default=0, verbose_name="Порядок сортировки")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        verbose_name = "Фото товара"
        verbose_name_plural = "Фото товаров"
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["product"],
                condition=Q(is_main=True),
                name="unique_main_image_per_product",
            ),
        ]

    def __str__(self) -> str:
        return f"Фото: {self.product.name}"

    def save(self, *args, **kwargs):
        # Позволяет безопасно "переключать" главное фото в админке:
        # перед сохранением текущего main-снимка снимаем флаг у остальных.
        with transaction.atomic():
            if self.is_main and self.product_id:
                (
                    ProductImage.objects.filter(product_id=self.product_id, is_main=True)
                    .exclude(pk=self.pk)
                    .update(is_main=False)
                )
            super().save(*args, **kwargs)
