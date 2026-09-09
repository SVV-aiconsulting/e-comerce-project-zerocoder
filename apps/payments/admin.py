import json
import uuid

from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

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
        "receipt_registration_status",
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
        "receipt_registration_status",
        "receipt_registration_error",
        "provider_payload",
        "last_error",
        "paid_notification_sent_at",
        "paid_notification_attempts",
        "paid_notification_error",
        "created_at",
        "updated_at",
        "partial_refund_link",
    )

    def get_urls(self):
        return [
            path(
                "<int:payment_id>/partial-refund/",
                self.admin_site.admin_view(self.partial_refund_view),
                name="payments_payment_partial_refund",
            )
        ] + super().get_urls()

    @admin.display(description="Частичный возврат")
    def partial_refund_link(self, obj):
        if not obj or not obj.pk:
            return "Сначала сохраните платёж"
        url = reverse("admin:payments_payment_partial_refund", args=[obj.pk])
        return format_html('<a class="button" href="{}">Выбрать позиции возврата</a>', url)

    def partial_refund_view(self, request, payment_id):
        payment = self.get_queryset(request).select_related("order").filter(pk=payment_id).first()
        if payment is None:
            from django.http import Http404

            raise Http404
        operation_id = request.POST.get("operation_id") or str(uuid.uuid4())
        lines_text = request.POST.get("lines", "")
        if request.method == "POST":
            try:
                lines = json.loads(lines_text)
                if not isinstance(lines, dict):
                    raise ValueError
                PaymentService.create_refund(
                    payment,
                    lines=lines,
                    operation_id=operation_id,
                    reason=(request.POST.get("reason") or "Частичный возврат из Django Admin"),
                )
            except (ValueError, json.JSONDecodeError, PaymentError) as exc:
                self.message_user(
                    request,
                    str(exc) if str(exc) else "Позиции должны быть JSON-объектом.",
                    level=messages.ERROR,
                )
            else:
                self.message_user(request, "Возврат создан или синхронизирован.", messages.SUCCESS)
                return HttpResponseRedirect(reverse("admin:payments_payment_change", args=[payment.pk]))
        original_items = (payment.receipt_data or {}).get("items", [])
        context = {
            **self.admin_site.each_context(request),
            "title": f"Частичный возврат: {payment.order.public_number}",
            "payment": payment,
            "receipt_items": list(enumerate(original_items)),
            "operation_id": operation_id,
            "lines": lines_text,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/payments/payment/partial_refund.html", context)

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
