from django.contrib import admin

from apps.dashboard.models import AnalyticsDashboard
from apps.dashboard.views import manager_dashboard


@admin.register(AnalyticsDashboard)
class AnalyticsDashboardAdmin(admin.ModelAdmin):
    """Expose the existing live dashboard as its own Django Admin section."""

    def changelist_view(self, request, extra_context=None):
        return manager_dashboard(request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_staff

    def get_model_perms(self, request):
        return {"view": self.has_view_permission(request)}
