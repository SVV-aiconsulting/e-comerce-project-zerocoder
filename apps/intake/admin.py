from django.contrib import admin

from apps.intake.models import (
    AIExtractionRun,
    Clarification,
    InboundEvent,
    OrderDraft,
    OrderDraftItem,
    OutboundMessage,
)


class ReadOnlyAuditAdmin(admin.ModelAdmin):
    """Запрещает ручное создание и удаление аудиторских записей."""

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class OrderDraftItemInline(admin.TabularInline):
    model = OrderDraftItem
    extra = 0
    can_delete = False
    readonly_fields = [field.name for field in OrderDraftItem._meta.fields]


class ClarificationInline(admin.TabularInline):
    model = Clarification
    extra = 0
    can_delete = False
    readonly_fields = [field.name for field in Clarification._meta.fields]


@admin.register(OrderDraft)
class OrderDraftAdmin(ReadOnlyAuditAdmin):
    list_display = (
        "public_id",
        "status",
        "intent",
        "channel",
        "customer",
        "revision",
        "manager_attention_required",
        "updated_at",
    )
    list_filter = ("status", "intent", "channel", "manager_attention_required")
    search_fields = (
        "public_id",
        "external_user_id",
        "conversation_key",
        "customer__phone",
        "customer__name",
    )
    readonly_fields = [field.name for field in OrderDraft._meta.fields]
    inlines = [OrderDraftItemInline, ClarificationInline]


@admin.register(InboundEvent)
class InboundEventAdmin(ReadOnlyAuditAdmin):
    list_display = (
        "public_id",
        "channel",
        "kind",
        "status",
        "external_event_id",
        "processing_attempts",
        "created_at",
    )
    list_filter = ("channel", "kind", "status")
    search_fields = (
        "public_id",
        "external_event_id",
        "external_user_id",
        "conversation_key",
    )
    readonly_fields = [field.name for field in InboundEvent._meta.fields]


@admin.register(AIExtractionRun)
class AIExtractionRunAdmin(ReadOnlyAuditAdmin):
    list_display = (
        "run_id",
        "purpose",
        "status",
        "provider",
        "model_name",
        "prompt_id",
        "prompt_version",
        "created_at",
    )
    list_filter = ("purpose", "status", "provider", "model_name")
    search_fields = (
        "run_id",
        "draft__public_id",
        "prompt_id",
        "prompt_version",
        "input_hash",
    )
    readonly_fields = [field.name for field in AIExtractionRun._meta.fields]


@admin.register(Clarification)
class ClarificationAdmin(ReadOnlyAuditAdmin):
    list_display = ("draft", "field_path", "status", "attempt_number", "asked_at")
    list_filter = ("status", "asked_at")
    search_fields = ("draft__public_id", "field_path", "question")
    readonly_fields = [field.name for field in Clarification._meta.fields]


@admin.register(OrderDraftItem)
class OrderDraftItemAdmin(ReadOnlyAuditAdmin):
    list_display = (
        "draft",
        "line_number",
        "raw_product_name",
        "requested_quantity",
        "requested_unit",
        "match_status",
        "product",
    )
    list_filter = ("match_status", "requested_unit", "resolution_source")
    search_fields = ("draft__public_id", "raw_product_name", "product__name")
    readonly_fields = [field.name for field in OrderDraftItem._meta.fields]


@admin.register(OutboundMessage)
class OutboundMessageAdmin(ReadOnlyAuditAdmin):
    list_display = (
        "id",
        "channel",
        "recipient",
        "status",
        "delivery_attempts",
        "sent_at",
        "created_at",
    )
    list_filter = ("channel", "status")
    search_fields = ("recipient", "response_id", "provider_message_id")
    readonly_fields = [field.name for field in OutboundMessage._meta.fields]
