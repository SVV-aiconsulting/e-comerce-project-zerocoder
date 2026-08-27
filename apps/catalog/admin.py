from django.contrib import admin
from django.utils.html import format_html

from apps.catalog.models import Product, ProductAlias, ProductImage
from apps.common.utils import generate_public_code


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1
    fields = ("image_preview", "image", "alt_text", "is_main", "sort_order")
    readonly_fields = ("image_preview",)
    verbose_name = "Фото товара"
    verbose_name_plural = "Фотографии (необязательно; загрузите хотя бы одно для отображения в каталоге)"

    @admin.display(description="Превью")
    def image_preview(self, obj: ProductImage) -> str:
        if obj.pk and obj.image:
            return format_html(
                '<a href="{}" target="_blank" rel="noopener noreferrer">'
                '<img src="{}" style="max-height: 72px; border-radius: 6px;" />'
                '</a>',
                obj.image.url,
                obj.image.url,
            )
        return "—"


class ProductAliasInline(admin.TabularInline):
    model = ProductAlias
    extra = 1
    fields = ("alias",)
    verbose_name = "Синоним"
    verbose_name_plural = "Синонимы для AI-поиска"


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "public_code",
        "base_price",
        "unit",
        "delivery_dimensions_ready",
        "is_active",
        "sort_order",
    )
    list_filter = ("is_active", "unit")
    search_fields = ("name", "public_code")
    ordering = ("sort_order", "name")
    inlines = [ProductImageInline, ProductAliasInline]
    readonly_fields = ("public_code",)
    fieldsets = (
        (
            None,
            {
                "description": (
                    "Обязательные поля: наименование, единица измерения, базовая цена. "
                    "Код товара создаётся автоматически."
                ),
                "fields": (
                    "name",
                    "public_code",
                    "unit",
                    "min_quantity",
                    "base_price",
                    "is_active",
                    "sort_order",
                ),
            },
        ),
        (
            "Описание",
            {
                "fields": ("description",),
            },
        ),
        (
            "Яндекс Доставка",
            {
                "description": (
                    "Вес и габариты одной единицы товара. Пока поля не заполнены, "
                    "внешний расчёт доставки для заказа с этим товаром невозможен."
                ),
                "fields": (
                    "delivery_weight_grams",
                    "delivery_length_cm",
                    "delivery_width_cm",
                    "delivery_height_cm",
                ),
            },
        ),
    )

    @admin.display(boolean=True, description="Габариты готовы")
    def delivery_dimensions_ready(self, obj: Product) -> bool:
        return obj.has_delivery_dimensions

    def save_model(self, request, obj, form, change):
        if not obj.public_code:
            obj.public_code = generate_public_code(
                lambda code: Product.objects.filter(public_code=code).exists()
            )
        super().save_model(request, obj, form, change)


@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = ("image_preview", "product", "is_main", "sort_order", "created_at")
    list_filter = ("is_main",)
    readonly_fields = ("image_preview", "created_at")
    fieldsets = (
        (
            None,
            {
                "description": "Обязательно: товар и файл изображения.",
                "fields": ("product", "image_preview", "image", "alt_text", "is_main", "sort_order", "created_at"),
            },
        ),
    )

    @admin.display(description="Превью")
    def image_preview(self, obj: ProductImage) -> str:
        if obj.pk and obj.image:
            return format_html(
                '<a href="{}" target="_blank" rel="noopener noreferrer">'
                '<img src="{}" style="max-height: 72px; border-radius: 6px;" />'
                '</a>',
                obj.image.url,
                obj.image.url,
            )
        return "—"


@admin.register(ProductAlias)
class ProductAliasAdmin(admin.ModelAdmin):
    list_display = ("alias", "product", "normalized_alias", "updated_at")
    search_fields = ("alias", "normalized_alias", "product__name", "product__public_code")
    list_select_related = ("product",)
    readonly_fields = ("normalized_alias", "created_at", "updated_at")
