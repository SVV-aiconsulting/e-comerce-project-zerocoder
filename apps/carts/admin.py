from django.contrib import admin

from apps.carts.models import Cart, CartItem


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    readonly_fields = ("created_at", "updated_at")
    verbose_name = "Позиция"
    verbose_name_plural = "Позиции корзины (товар и количество)"


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ("id", "customer", "channel", "external_user_id", "status", "updated_at")
    list_filter = ("status", "channel")
    search_fields = ("external_user_id", "customer__name", "customer__phone")
    inlines = [CartItemInline]
    fieldsets = (
        (
            None,
            {
                "description": (
                    "Корзины обычно создаются автоматически при работе клиента в боте или на сайте. "
                    "Обязательные поля: канал и идентификатор пользователя. Клиент может быть пустым "
                    "до регистрации по телефону."
                ),
                "fields": ("customer", "channel", "external_user_id", "status"),
            },
        ),
    )


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ("cart", "product", "quantity", "updated_at")
    fieldsets = (
        (
            None,
            {
                "description": "Обязательно: корзина, товар и количество (не меньше минимального для товара).",
                "fields": ("cart", "product", "quantity"),
            },
        ),
    )
