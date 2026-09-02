from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.auth import AdapterTokenAuthentication
from apps.api.exceptions import OrderAccessDenied, OrderNotFound
from apps.api.helpers import resolve_customer_from_identity
from apps.api.serializers.common import ChannelIdentitySerializer
from apps.api.serializers.payments import PaymentSerializer
from apps.orders.models import Order
from apps.payments.services import PaymentService, YooKassaWebhookService


class CreatePaymentView(APIView):
    authentication_classes = [AdapterTokenAuthentication]
    permission_classes = []

    def post(self, request, public_number: str):
        identity = ChannelIdentitySerializer(data=request.data)
        identity.is_valid(raise_exception=True)
        order = Order.objects.filter(public_number=public_number).first()
        if order is None:
            raise OrderNotFound()
        customer = resolve_customer_from_identity(**identity.validated_data)
        if customer.pk != order.customer_id:
            raise OrderAccessDenied()
        payment = PaymentService.ensure_payment_link(order)
        return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)


class YooKassaWebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @csrf_exempt
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def post(self, request):
        # Gunicorn закрыт Docker-сетью, поэтому nginx является доверенным proxy и
        # перезаписывает X-Real-IP фактическим адресом отправителя webhook.
        remote_ip = (
            request.META.get("HTTP_X_REAL_IP")
            or (request.META.get("HTTP_X_FORWARDED_FOR", "").split(",", 1)[0].strip())
            or request.META.get("REMOTE_ADDR")
        )
        event = YooKassaWebhookService.process(
            request.data,
            remote_ip=remote_ip,
        )
        # ЮKassa ожидает 200, повторное уведомление погасит уникальный fingerprint.
        return Response({"accepted": True, "event_id": event.pk}, status=status.HTTP_200_OK)
