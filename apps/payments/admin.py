from django.contrib import admin, messages

from apps.payments.exceptions import PaymentError
from apps.payments.models import Payment, PaymentWebhookEvent, Refund
from apps.payments.services import PaymentService


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    actions = (
        "create_payment_links",
        "sync_payments",
        "cancel_payments",
        "create_full_refunds",
    )
    list_display = (
        "order",
        "provider",
        "environment",
        "state",
        "amount",
        "currency",
        "external_id",
        "created_at",
    )
    list_filter = ("provider", "environment", "state", "created_at")
    search_fields = ("order__public_number", "external_id")
    raw_id_fields = ("order",)
    readonly_fields = (
        "idempotence_key",
        "external_id",
        "confirmation_url",
        "expires_at",
        "paid_at",
        "receipt_data",
        "provider_payload",
        "last_error",
        "paid_notification_sent_at",
        "paid_notification_attempts",
        "paid_notification_error",
        "created_at",
        "updated_at",
    )

    @admin.action(description="Создать/повторить ссылки ЮKassa (до 20)")
    def create_payment_links(self, request, queryset):
        self._run(request, queryset, lambda item: PaymentService.ensure_payment_link(item.order))

    @admin.action(description="Синхронизировать платежи ЮKassa (до 20)")
    def sync_payments(self, request, queryset):
        self._run(request, queryset, PaymentService.sync_payment)

    @admin.action(description="Отменить ожидающие платежи ЮKassa (до 20)")
    def cancel_payments(self, request, queryset):
        self._run(request, queryset, PaymentService.cancel_payment)

    @admin.action(description="Создать полный возврат ЮKassa (до 20)")
    def create_full_refunds(self, request, queryset):
        self._run(
            request,
            queryset,
            lambda item: PaymentService.create_refund(
                item,
                amount=item.amount,
                reason="Полный возврат из Django Admin",
            ),
        )

    def _run(self, request, queryset, operation):
        succeeded = failed = 0
        for payment in queryset.select_related("order")[:20]:
            try:
                operation(payment)
                succeeded += 1
            except PaymentError as exc:
                failed += 1
                self.message_user(request, str(exc), level=messages.ERROR)
        self.message_user(
            request,
            f"Успешно: {succeeded}; ошибок: {failed}.",
            level=messages.SUCCESS if not failed else messages.WARNING,
        )


@admin.register(PaymentWebhookEvent)
class PaymentWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "provider", "event_type", "payment", "verified", "processed")
    list_filter = ("provider", "event_type", "verified", "processed")
    search_fields = ("fingerprint", "payment__external_id", "payment__order__public_number")
    raw_id_fields = ("payment",)
    readonly_fields = (
        "payment",
        "provider",
        "event_type",
        "fingerprint",
        "remote_ip",
        "payload",
        "verified",
        "processed",
        "processing_error",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = ("payment", "state", "amount", "currency", "external_id", "created_at")
    list_filter = ("state", "currency", "created_at")
    search_fields = ("payment__external_id", "payment__order__public_number", "external_id")
    raw_id_fields = ("payment",)
    readonly_fields = (
        "idempotence_key",
        "external_id",
        "receipt_data",
        "provider_payload",
        "last_error",
        "created_at",
        "updated_at",
    )
