from django.contrib import admin
from django.db.models import Count
from django.urls import reverse
from django.utils.html import format_html

from apps.intake.models import AssistantMessage, OrderDraft, OrderDraftItem


class ReadOnlyAuditAdmin(admin.ModelAdmin):
    """Protect records produced by the order-processing pipeline."""

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ConversationTypeFilter(admin.SimpleListFilter):
    title = "Тип записи"
    parameter_name = "record_type"

    def lookups(self, request, model_admin):
        return (("order", "Оформленный заказ"), ("draft", "Черновик"))

    def queryset(self, request, queryset):
        if self.value() == "order":
            return queryset.filter(converted_order__isnull=False)
        if self.value() == "draft":
            return queryset.filter(converted_order__isnull=True)
        return queryset


class OrderDraftItemInline(admin.TabularInline):
    model = OrderDraftItem
    extra = 0
    can_delete = False
    verbose_name = "Позиция"
    verbose_name_plural = "Состав заказа или черновика"
    readonly_fields = [field.name for field in OrderDraftItem._meta.fields]


@admin.register(OrderDraft)
class AssistantConversationAdmin(ReadOnlyAuditAdmin):
    """One manager-facing view for AI orders, drafts and their dialogue."""

    change_form_template = "admin/intake/orderdraft/change_form.html"
    list_display = (
        "record_kind",
        "record_reference",
        "client_name",
        "channel",
        "display_status",
        "item_count",
        "display_total",
        "manager_attention_required",
        "updated_at",
    )
    list_filter = (
        ConversationTypeFilter,
        "status",
        "channel",
        "manager_attention_required",
        "updated_at",
    )
    search_fields = (
        "public_id",
        "converted_order__public_number",
        "external_user_id",
        "conversation_key",
        "customer__phone",
        "customer__email",
        "customer__name",
        "converted_order__customer_phone_snapshot",
        "converted_order__customer_email_snapshot",
        "converted_order__customer_name_snapshot",
    )
    readonly_fields = [field.name for field in OrderDraft._meta.fields]
    inlines = [OrderDraftItemInline]
    fieldsets = (
        ("Заказ или черновик", {"fields": ("public_id", "order_link", "status", "intent", "manager_attention_required", "escalation_reason")}),
        ("Клиент и канал", {"fields": ("customer", "channel", "external_user_id", "conversation_key", "contact_phone", "contact_email")}),
        ("Получение и оплата", {"fields": ("receiving_type", "delivery_address", "desired_date", "desired_time_interval", "payment_method", "customer_comment")}),
        ("Расчёт", {"fields": ("items_total", "discount_amount", "delivery_cost", "total_amount", "priced_at", "checkout_preview")}),
        ("Служебное", {"classes": ("collapse",), "fields": ("cart", "synced_cart_revision", "revision", "previewed_revision", "confirmed_revision", "confirmed_at", "missing_fields", "created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("customer", "converted_order")
            .annotate(admin_item_count=Count("items", distinct=True))
        )

    @admin.display(description="Тип", ordering="converted_order")
    def record_kind(self, obj):
        return "Заказ" if obj.converted_order_id else "Черновик"

    @admin.display(description="Номер / черновик")
    def record_reference(self, obj):
        if obj.converted_order_id:
            url = reverse("admin:orders_order_change", args=[obj.converted_order_id])
            return format_html('<a href="{}">{}</a>', url, obj.converted_order.public_number)
        return f"Черновик {str(obj.public_id)[:8]}"

    @admin.display(description="Клиент", ordering="customer__name")
    def client_name(self, obj):
        if obj.converted_order_id:
            return obj.converted_order.customer_name_snapshot or "Не указан"
        if obj.customer_id:
            return obj.customer.name
        return obj.contact_phone or obj.contact_email or "Не идентифицирован"

    @admin.display(description="Статус", ordering="status")
    def display_status(self, obj):
        if obj.converted_order_id:
            return f"Оформлен · {obj.converted_order.get_order_status_display()}"
        return obj.get_status_display()

    @admin.display(description="Позиций", ordering="admin_item_count")
    def item_count(self, obj):
        return obj.admin_item_count

    @admin.display(description="Сумма")
    def display_total(self, obj):
        total = obj.converted_order.total_amount if obj.converted_order_id else obj.total_amount
        return f"{total:.2f} ₽" if total is not None else "—"

    @admin.display(description="Оформленный заказ")
    def order_link(self, obj):
        if not obj or not obj.converted_order_id:
            return "Заказ ещё не оформлен"
        url = reverse("admin:orders_order_change", args=[obj.converted_order_id])
        return format_html('<a href="{}">Открыть заказ {}</a>', url, obj.converted_order.public_number)

    def get_readonly_fields(self, request, obj=None):
        fields = list(self.readonly_fields)
        fields.insert(1, "order_link")
        return fields

    def change_view(self, request, object_id, form_url="", extra_context=None):
        obj = self.get_object(request, object_id)
        messages = []
        if obj is not None:
            messages = list(
                AssistantMessage.objects.filter(event__draft=obj)
                .select_related("event")
                .order_by("created_at", "id")
            )
        extra_context = {**(extra_context or {}), "conversation_messages": messages}
        return super().change_view(request, object_id, form_url, extra_context)
