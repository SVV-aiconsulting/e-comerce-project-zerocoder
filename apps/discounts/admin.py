from django.contrib import admin

from apps.discounts.models import DiscountRule


@admin.register(DiscountRule)
class DiscountRuleAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "is_active",
        "priority",
        "discount_percent",
        "discount_amount",
        "free_delivery",
        "min_order_amount",
    )
    list_filter = ("is_active", "free_delivery")
    ordering = ("priority", "name")
    fieldsets = (
        (
            None,
            {
                "description": (
                    "Обязательные поля: название и приоритет. "
                    "Укажите хотя бы один вид скидки: процент, сумма или бесплатная доставка."
                ),
                "fields": (
                    "name",
                    "is_active",
                    "priority",
                    "discount_percent",
                    "discount_amount",
                    "free_delivery",
                ),
            },
        ),
        (
            "Условия применения",
            {
                "description": "Необязательно. Ограничения по сумме заказа, истории клиента и периоду действия.",
                "fields": (
                    "min_order_amount",
                    "min_customer_orders",
                    "date_start",
                    "date_end",
                    "comment",
                ),
            },
        ),
    )
