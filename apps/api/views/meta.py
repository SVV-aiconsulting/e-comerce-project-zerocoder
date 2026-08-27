"""Справочники для UI адаптеров."""
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.enums import (
    Channel,
    OrderStatus,
    PaymentMethod,
    ProductUnit,
    ReceivingType,
    TimeInterval,
)


class MetaView(APIView):
    """Справочники choices для frontend-адаптеров."""

    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return Response(
            {
                "channels": self._choices(Channel.choices),
                "product_units": self._choices(ProductUnit.choices),
                "receiving_types": self._choices(ReceivingType.choices),
                "payment_methods": self._choices(PaymentMethod.choices),
                "time_intervals": self._choices(TimeInterval.choices),
                "order_statuses": self._choices(OrderStatus.choices),
            }
        )

    @staticmethod
    def _choices(choices):
        return [{"value": value, "label": label} for value, label in choices]
