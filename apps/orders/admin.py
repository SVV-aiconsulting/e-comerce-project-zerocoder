from django.contrib import admin, messages

from apps.common.enums import ReceivingType, StatusChangeSource
from apps.common.exceptions import DeliveryError
from apps.delivery.models import DeliveryQuoteStatus
from apps.delivery.offer_service import YandexDeliveryOfferService
from apps.orders.models import Order, OrderItem, OrderStatusHistory
from apps.orders.services import OrderService


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = (
        "product",
        "product_name_snapshot",
        "product_unit_snapshot",
        "quantity",
        "unit_price",
        "total_price",
    )


class OrderStatusHistoryInline(admin.TabularInline):
    model = OrderStatusHistory
    extra = 0
    readonly_fields = (
        "event_datetime",
        "old_status",
        "new_status",
        "source",
        "changed_by",
        "comment",
    )
    can_delete = False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    actions = ("create_yandex_delivery_offers",)
    list_display = (
        "public_number",
        "customer_name_snapshot",
        "customer_phone_snapshot",
        "customer_email_snapshot",
        "order_status",
        "payment_status",
        "channel",
        "total_amount",
        "created_at",
    )
    list_filter = ("order_status", "payment_status", "channel", "created_at")
    search_fields = (
        "public_number",
        "customer_phone_snapshot",
        "customer_email_snapshot",
        "customer_name_snapshot",
        "customer_code_snapshot",
    )
    readonly_fields = (
        "public_number",
        "customer_code_snapshot",
        "customer_name_snapshot",
        "customer_phone_snapshot",
        "customer_email_snapshot",
        "source_external_user_id_snapshot",
        "items_total",
        "discount_amount",
        "delivery_cost",
        "total_amount",
        "created_at",
        "updated_at",
    )
    inlines = [OrderItemInline, OrderStatusHistoryInline]
    fieldsets = (
        (
            None,
            {
                "description": (
                    "Заказы создаются через оформление корзины (бот или сайт). "
                    "В админке можно просматривать заказы и менять статус. "
                    "Суммы и снимки данных клиента заполняются автоматически."
                ),
                "fields": (
                    "public_number",
                    "customer",
                    "customer_code_snapshot",
                    "customer_name_snapshot",
                    "customer_phone_snapshot",
                    "customer_email_snapshot",
                    "channel",
                    "source_external_user_id_snapshot",
                    "is_new_customer",
                ),
            },
        ),
        (
            "Доставка и получение",
            {
                "fields": (
                    "receiving_type",
                    "desired_date",
                    "desired_time_interval",
                    "delivery_address",
                ),
            },
        ),
        (
            "Оплата и статусы",
            {
                "fields": (
                    "payment_method",
                    "payment_status",
                    "order_status",
                ),
            },
        ),
        (
            "Суммы",
            {
                "classes": ("collapse",),
                "fields": (
                    "items_total",
                    "discount_amount",
                    "delivery_cost",
                    "total_amount",
                ),
            },
        ),
        (
            "Комментарии",
            {
                "fields": ("customer_comment", "manager_comment"),
            },
        ),
        (
            "Служебное",
            {
                "classes": ("collapse",),
                "fields": ("created_at", "updated_at"),
            },
        ),
    )

    def save_model(self, request, obj, form, change):
        if change:
            old_order = Order.objects.get(pk=obj.pk)
            old_status = old_order.order_status
            new_status = obj.order_status
            if old_status != new_status:
                obj.order_status = old_status
            super().save_model(request, obj, form, change)
            if old_status != new_status:
                OrderService.change_status(
                    obj,
                    new_status,
                    source=StatusChangeSource.DJANGO_ADMIN,
                    changed_by=request.user,
                )
        else:
            super().save_model(request, obj, form, change)

    @admin.action(description="Получить офферы Яндекс Доставки (до 20 заказов)")
    def create_yandex_delivery_offers(self, request, queryset):
        succeeded = 0
        failed = 0
        for order in queryset.filter(receiving_type=ReceivingType.DELIVERY)[:20]:
            try:
                quotes = YandexDeliveryOfferService.create_for_order(order)
            except DeliveryError as exc:
                failed += 1
                self.message_user(
                    request,
                    f"{order.public_number}: {exc}",
                    level=messages.ERROR,
                )
                continue
            if any(quote.status == DeliveryQuoteStatus.SUCCEEDED for quote in quotes):
                succeeded += 1
            else:
                failed += 1
        self.message_user(
            request,
            f"Офферы получены: {succeeded}; ошибки: {failed}.",
            level=messages.SUCCESS if not failed else messages.WARNING,
        )


@admin.register(OrderStatusHistory)
class OrderStatusHistoryAdmin(admin.ModelAdmin):
    list_display = ("order", "old_status", "new_status", "source", "event_datetime")
    list_filter = ("source", "new_status")
    readonly_fields = (
        "order",
        "event_datetime",
        "old_status",
        "new_status",
        "source",
        "changed_by",
        "comment",
    )
