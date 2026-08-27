from django.contrib import admin, messages

from apps.delivery.exceptions import DeliveryError
from apps.delivery.models import (
    DeliveryQuote,
    DeliveryRule,
    DeliverySyncEvent,
    Shipment,
)
from apps.delivery.shipment_service import YandexShipmentService


@admin.register(DeliveryRule)
class DeliveryRuleAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "is_active",
        "delivery_cost",
        "free_delivery_from",
        "min_order_amount",
        "delivery_zone",
    )
    list_filter = ("is_active",)
    search_fields = ("name", "delivery_zone")
    fieldsets = (
        (
            None,
            {
                "description": (
                    "Обязательные поля: название и стоимость доставки. "
                    "Остальные параметры уточняют условия применения правила."
                ),
                "fields": (
                    "name",
                    "is_active",
                    "delivery_cost",
                    "free_delivery_from",
                    "min_order_amount",
                    "delivery_zone",
                    "comment",
                ),
            },
        ),
    )


@admin.register(DeliveryQuote)
class DeliveryQuoteAdmin(admin.ModelAdmin):
    actions = ("confirm_yandex_offers",)
    list_display = (
        "id",
        "provider",
        "environment",
        "kind",
        "status",
        "amount",
        "currency",
        "delivery_days",
        "expires_at",
        "created_at",
    )
    list_filter = ("provider", "environment", "kind", "status", "last_mile_policy")
    search_fields = (
        "external_offer_id",
        "operator_request_id",
        "destination_address",
        "order__public_number",
        "order_draft__public_id",
    )
    raw_id_fields = ("order", "order_draft")
    readonly_fields = (
        "request_fingerprint",
        "request_payload",
        "response_payload",
        "created_at",
        "updated_at",
    )

    @admin.action(description="Подтвердить выбранные офферы Яндекса (до 20)")
    def confirm_yandex_offers(self, request, queryset):
        succeeded = 0
        failed = 0
        for quote in queryset.select_related("order")[:20]:
            try:
                YandexShipmentService.confirm_quote(quote)
                succeeded += 1
            except DeliveryError as exc:
                failed += 1
                self.message_user(request, str(exc), level=messages.ERROR)
        self.message_user(
            request,
            f"Подтверждено: {succeeded}; ошибки: {failed}.",
            level=messages.SUCCESS if not failed else messages.WARNING,
        )


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    actions = ("sync_yandex_shipments", "cancel_yandex_shipments")
    list_display = (
        "order",
        "provider",
        "environment",
        "status",
        "external_status",
        "amount",
        "last_synced_at",
    )
    list_filter = ("provider", "environment", "status", "external_status")
    search_fields = ("order__public_number", "external_request_id", "tracking_url")
    raw_id_fields = ("order", "quote")
    readonly_fields = (
        "creation_payload",
        "provider_payload",
        "created_at",
        "updated_at",
    )

    @admin.action(description="Обновить статусы из Яндекс Доставки (до 20)")
    def sync_yandex_shipments(self, request, queryset):
        self._run_action(request, queryset, YandexShipmentService.sync, "Обновлено")

    @admin.action(description="Отменить доставки в Яндексе (до 20)")
    def cancel_yandex_shipments(self, request, queryset):
        self._run_action(request, queryset, YandexShipmentService.cancel, "Отменено")

    def _run_action(self, request, queryset, operation, success_label):
        succeeded = 0
        failed = 0
        for shipment in queryset[:20]:
            try:
                operation(shipment)
                succeeded += 1
            except DeliveryError as exc:
                failed += 1
                self.message_user(request, str(exc), level=messages.ERROR)
        self.message_user(
            request,
            f"{success_label}: {succeeded}; ошибки: {failed}.",
            level=messages.SUCCESS if not failed else messages.WARNING,
        )


@admin.register(DeliverySyncEvent)
class DeliverySyncEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "operation", "succeeded", "http_status", "shipment", "quote")
    list_filter = ("operation", "succeeded", "http_status")
    search_fields = ("error_code", "error_message", "shipment__order__public_number")
    raw_id_fields = ("shipment", "quote")
    readonly_fields = (
        "shipment",
        "quote",
        "operation",
        "succeeded",
        "http_status",
        "request_payload",
        "response_payload",
        "error_code",
        "error_message",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
