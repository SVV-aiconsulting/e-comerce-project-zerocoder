from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import path

from apps.privacy.models import PersonalDataConsentEvent, PersonalDataDocumentVersion


@admin.register(PersonalDataDocumentVersion)
class PersonalDataDocumentVersionAdmin(admin.ModelAdmin):
    list_display = ("document_type", "version", "effective_from", "content_hash", "public_path")
    readonly_fields = ("public_id", "content_hash", "created_at")

    def has_change_permission(self, request, obj=None):
        return False if obj else super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PersonalDataConsentEvent)
class PersonalDataConsentEventAdmin(admin.ModelAdmin):
    list_display = ("occurred_at", "status", "channel", "identity_type", "identification_level", "identity_value", "customer", "expression_method")
    list_filter = ("status", "channel", "identity_type", "identification_level", "expression_method")
    search_fields = ("identity_value", "customer__phone", "customer__email")
    readonly_fields = tuple(field.name for field in PersonalDataConsentEvent._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        return [
            path(
                "extract/<uuid:public_id>/",
                self.admin_site.admin_view(self.registry_extract),
                name="privacy-consent-registry-extract",
            )
        ] + super().get_urls()

    def registry_extract(self, request, public_id):
        event = PersonalDataConsentEvent.objects.select_related(
            "customer", "consent_document", "policy_document", "previous_event"
        ).get(public_id=public_id)
        return TemplateResponse(
            request,
            "admin/privacy/consent_registry_extract.html",
            {**self.admin_site.each_context(request), "event": event},
        )
