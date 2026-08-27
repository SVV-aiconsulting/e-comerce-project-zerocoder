"""Единая точка приёма сообщений от всех каналов продаж."""
from rest_framework import status
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.auth import AdapterTokenAuthentication
from apps.api.helpers import resolve_customer_context
from apps.api.serializers.intake import (
    InboundEventLookupSerializer,
    InboundEventRequestSerializer,
)
from apps.customers.services import CustomerService
from apps.intake.models import InboundEvent
from apps.intake.responses import InboundEventResponseService
from apps.intake.services import InboundEventService


class InboundEventView(APIView):
    authentication_classes = [AdapterTokenAuthentication]
    permission_classes = []

    def post(self, request):
        serializer = InboundEventRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = dict(serializer.validated_data)
        customer_id = payload.pop("customer_id", None)

        if customer_id is not None:
            customer = resolve_customer_context(
                channel=payload["channel"],
                external_user_id=payload["external_user_id"],
                customer_id=customer_id,
            )
        else:
            customer = CustomerService.find_by_channel_identity(
                payload["channel"],
                payload["external_user_id"],
            )

        result = InboundEventService.register(customer=customer, **payload)
        enqueued = InboundEventService.enqueue(result.event)
        result.event.refresh_from_db(fields=["public_id", "status"])

        response_status = (
            status.HTTP_202_ACCEPTED if result.created else status.HTTP_200_OK
        )
        return Response(
            {
                "event_id": str(result.event.public_id),
                "status": result.event.status,
                "duplicate": not result.created,
                "enqueued": enqueued,
            },
            status=response_status,
        )


class InboundEventDetailView(APIView):
    """Безопасный polling результата ранее принятого события."""

    authentication_classes = [AdapterTokenAuthentication]
    permission_classes = []

    def get(self, request, event_id):
        lookup = InboundEventLookupSerializer(data=request.query_params)
        lookup.is_valid(raise_exception=True)
        event = get_object_or_404(
            InboundEvent.objects.select_related("draft__converted_order"),
            public_id=event_id,
            channel=lookup.validated_data["channel"],
            external_user_id=lookup.validated_data["external_user_id"],
        )
        return Response(InboundEventResponseService.present(event))
