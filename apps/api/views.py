"""Views REST API — реэкспорт для обратной совместимости."""
from apps.api.views.health import HealthCheckView
from apps.api.views.identify import IdentifyCustomerView

__all__ = ["HealthCheckView", "IdentifyCustomerView"]
