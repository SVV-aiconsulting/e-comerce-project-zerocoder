from django import forms
from django.contrib import admin
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
    )
    inlines = [CustomerChannelIdentityInline]
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
                    "personal_data_consent_link",
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
