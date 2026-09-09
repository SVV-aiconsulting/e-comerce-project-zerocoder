from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.html import format_html
from django.utils import timezone

from apps.common.enums import CustomerSource
from apps.common.utils import generate_public_code
from apps.customers.models import (
    Customer,
    CustomerChannelIdentity,
    CustomerIdentityConflict,
    IdentityConflictStatus,
)
from apps.customers.validators import normalize_email, normalize_phone
from apps.customers.services import CustomerService
from apps.privacy.models import ConsentStatus, PersonalDataConsentEvent


class CustomerAdminForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = "__all__"

    def clean_phone(self):
        phone = self.cleaned_data["phone"]
        return normalize_phone(phone) if phone else ""

    def clean_email(self):
        email = self.cleaned_data["email"]
        return normalize_email(email) if email else ""

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("phone") and not cleaned_data.get("email"):
            raise forms.ValidationError("Укажите телефон или email клиента.")
        return cleaned_data


class CustomerChannelIdentityInline(admin.TabularInline):
    model = CustomerChannelIdentity
    extra = 0
    fields = ("channel", "external_user_id", "username", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")
    verbose_name = "Привязка к каналу"
    verbose_name_plural = (
        "Привязки к каналам (необязательно при ручном создании; "
        "нужны для входа клиента через бота или email)"
    )


class HasChannelFilter(admin.SimpleListFilter):
    """Фильтр клиентов по наличию привязки к конкретному каналу."""

    title = "Канал"
    parameter_name = "has_channel"

    def lookups(self, request, model_admin):
        return (
            ("telegram", "Telegram"),
            ("vk", "ВКонтакте"),
            ("max", "MAX"),
            ("website", "Сайт"),
            ("email", "Email"),
        )

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(channel_identities__channel=self.value()).distinct()
        return queryset


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    form = CustomerAdminForm
    list_display = (
        "name",
        "public_code",
        "phone",
        "email",
        "phone_verified_at",
        "channels_display",
        "status",
        "first_source",
        "orders_count",
        "total_orders_sum",
        "last_order_at",
    )
    list_filter = ("status", "first_source", HasChannelFilter)
    search_fields = (
        "name",
        "phone",
        "email",
        "public_code",
        "channel_identities__external_user_id",
        "channel_identities__username",
    )
    readonly_fields = (
        "public_code",
        "phone_verified_at",
        "email_verified_at",
        "orders_count",
        "total_orders_sum",
        "first_order_at",
        "last_order_at",
        "created_at",
        "updated_at",
        "personal_data_consent_registry_key",
        "consent_date",
        "consent_registry_link",
        "consent_registry_extract",
    )
    inlines = [CustomerChannelIdentityInline]
    actions = ("anonymize_and_delete_customers",)
    list_select_related = ()
    fieldsets = (
        (
            None,
            {
                "description": (
                    "Обязательные поля: имя, первый источник и хотя бы один контакт. "
                    "Код клиента создаётся автоматически при сохранении."
                ),
                "fields": (
                    "name",
                    "phone",
                    "email",
                    "first_source",
                    "public_code",
                    "status",
                ),
            },
        ),
        (
            "Согласия и комментарии",
            {
                "description": "Необязательно. Заполняйте при получении согласий от клиента.",
                "fields": (
                    "marketing_consent",
                    "personal_data_consent",
                    "personal_data_consent_registry_key",
                    "consent_date",
                    "consent_registry_link",
                    "consent_registry_extract",
                    "manager_comment",
                ),
            },
        ),
        (
            "Статистика",
            {
                "classes": ("collapse",),
                "description": "Заполняется автоматически по заказам клиента.",
                "fields": (
                    "orders_count",
                    "total_orders_sum",
                    "first_order_at",
                    "last_order_at",
                    "phone_verified_at",
                    "email_verified_at",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )

    def get_changeform_initial_data(self, request):
        return {"first_source": CustomerSource.MANAGER}

    def save_model(self, request, obj, form, change):
        if not obj.public_code:
            obj.public_code = generate_public_code(
                lambda code: Customer.objects.filter(public_code=code).exists()
            )
        super().save_model(request, obj, form, change)

    @admin.display(description="Каналы")
    def channels_display(self, obj: Customer) -> str:
        channels = {
            identity.get_channel_display()
            for identity in obj.channel_identities.all()
        }
        return ", ".join(sorted(channels)) if channels else "—"

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("channel_identities")


    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Удалить карточки и обезличить связанные заказы")
    def anonymize_and_delete_customers(self, request, queryset):
        if request.POST.get("confirm_anonymize") != "yes":
            return TemplateResponse(
                request,
                "admin/customers/confirm_anonymize.html",
                {
                    **self.admin_site.each_context(request),
                    "title": "Удаление карточек и обезличивание заказов",
                    "customers": queryset,
                    "action_checkbox_name": ACTION_CHECKBOX_NAME,
                    "action_name": "anonymize_and_delete_customers",
                },
            )
        customers = list(queryset)
        for customer in customers:
            CustomerService.anonymize_orders_and_delete(customer=customer)
        self.message_user(
            request,
            f"Удалено карточек: {len(customers)}. Заказы сохранены и обезличены.",
            level=messages.WARNING,
        )

    @admin.display(description="Дата согласия")
    def consent_date(self, obj):
        event = self._current_consent_event(obj)
        return event.occurred_at if event else "—"

    @admin.display(description="Запись реестра")
    def consent_registry_link(self, obj):
        event = self._current_consent_event(obj)
        if not event:
            return "—"
        url = reverse("admin:privacy_personaldataconsentevent_change", args=[event.pk])
        return format_html('<a href="{}">{}</a>', url, event.public_id)

    @admin.display(description="Выписка из реестра")
    def consent_registry_extract(self, obj):
        event = self._current_consent_event(obj)
        if not event:
            return "—"
        url = reverse("admin:privacy-consent-registry-extract", args=[event.public_id])
        return format_html('<a class="button" href="{}" target="_blank">Сформировать выписку</a>', url)

    @staticmethod
    def _current_consent_event(obj):
        if obj.personal_data_consent_registry_key:
            event = PersonalDataConsentEvent.objects.filter(public_id=obj.personal_data_consent_registry_key).first()
            if event:
                return event
        return obj.personal_data_consent_events.filter(status=ConsentStatus.GRANTED).order_by("-occurred_at", "-id").first()


@admin.register(CustomerChannelIdentity)
class CustomerChannelIdentityAdmin(admin.ModelAdmin):
    list_display = (
        "customer",
        "channel",
        "external_user_id",
        "username",
        "created_at",
        "updated_at",
    )
    list_filter = ("channel",)
    search_fields = ("external_user_id", "username", "customer__name")
    autocomplete_fields = ("customer",)
    fieldsets = (
        (
            None,
            {
                "description": (
                    "Обязательно: клиент, канал и идентификатор пользователя в этом канале. "
                    "Один клиент может иметь несколько идентификаторов одного канала."
                ),
                "fields": ("customer", "channel", "external_user_id", "username"),
            },
        ),
    )


@admin.register(CustomerIdentityConflict)
class CustomerIdentityConflictAdmin(admin.ModelAdmin):
    list_display = (
        "contact_type",
        "contact_value",
        "source_customer",
        "matched_customer",
        "source_channel",
        "status",
        "created_at",
    )
    list_filter = ("status", "contact_type", "source_channel", "created_at")
    search_fields = (
        "contact_value",
        "source_customer__name",
        "source_customer__public_code",
        "matched_customer__name",
        "matched_customer__public_code",
    )
    autocomplete_fields = ("source_customer", "matched_customer")
    readonly_fields = (
        "source_customer",
        "matched_customer",
        "contact_type",
        "contact_value",
        "source_channel",
        "source_external_user_id",
        "created_at",
        "updated_at",
        "resolved_at",
        "resolved_by",
    )
    fields = (
        "source_customer",
        "matched_customer",
        "contact_type",
        "contact_value",
        "source_channel",
        "source_external_user_id",
        "status",
        "resolution_comment",
        "resolved_at",
        "resolved_by",
        "created_at",
        "updated_at",
    )

    def save_model(self, request, obj, form, change):
        if obj.status != IdentityConflictStatus.PENDING:
            obj.resolved_at = obj.resolved_at or timezone.now()
            obj.resolved_by = obj.resolved_by or request.user
        else:
            obj.resolved_at = None
            obj.resolved_by = None
        super().save_model(request, obj, form, change)


from apps.customers.models import WebAccount, OrderAccessGrant, HistoryLinkRequest

@admin.register(WebAccount)
class WebAccountAdmin(admin.ModelAdmin):
    list_display = ("email", "name", "verified_at")
    readonly_fields = ("email", "user", "verified_at", "phone_login", "basket_user_id")
    def has_add_permission(self, request):
        return False

@admin.register(OrderAccessGrant)
class OrderAccessGrantAdmin(admin.ModelAdmin):
    list_display = ("order", "account", "reason", "granted_by", "created_at")
    readonly_fields = ("order", "account", "reason", "granted_by", "created_at", "updated_at")
    def has_add_permission(self, request):
        return False

@admin.register(HistoryLinkRequest)
class HistoryLinkRequestAdmin(admin.ModelAdmin):
    list_display = ("account", "order", "status", "reason")
    readonly_fields = ("account", "order", "status")
    actions = ("approve_links", "reject_links")

    @admin.action(description="Подтвердить связи с указанной причиной")
    def approve_links(self, request, queryset):
        from django.db import transaction
        for candidate in queryset.filter(status="pending"):
            with transaction.atomic():
                row = HistoryLinkRequest.objects.select_for_update().get(pk=candidate.pk)
                if not row.reason.strip():
                    self.message_user(request, "Сначала укажите причину подтверждения.", level=messages.ERROR)
                    continue
                from apps.orders.models import Order
                Order.objects.select_for_update().get(pk=row.order_id)
                grant, _ = OrderAccessGrant.objects.get_or_create(order=row.order,
                    defaults={"account": row.account, "reason": row.reason, "granted_by": request.user})
                if grant.account_id != row.account_id:
                    self.message_user(request, "Заказ уже связан с другим аккаунтом.", level=messages.ERROR)
                    continue
                row.status = "approved"
                row.save(update_fields=["status", "updated_at"])

    @admin.action(description="Отклонить связи")
    def reject_links(self, request, queryset):
        missing_reason = queryset.filter(status="pending", reason="").exists()
        if missing_reason:
            self.message_user(
                request,
                "Сначала укажите причину отклонения для каждой связи.",
                level=messages.ERROR,
            )
            return
        queryset.filter(status="pending").update(status="rejected")
