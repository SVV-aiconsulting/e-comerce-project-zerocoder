"""Идентификация клиента."""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.auth import AdapterTokenAuthentication
from apps.api.serializers.identify import (
    IdentifyCustomerRequestSerializer,
    IdentifyCustomerResponseSerializer,
)
from apps.common.exceptions import ChannelIdentityAlreadyLinkedError
from apps.customers.services import CustomerService


class IdentifyCustomerView(APIView):
    """Единая точка идентификации клиента для всех фронтендов."""

    authentication_classes = [AdapterTokenAuthentication]
    permission_classes = []

    def post(self, request):
        request_serializer = IdentifyCustomerRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        payload = request_serializer.validated_data
        phone_verification_source = payload.get("phone_verification_source", "manual_input")
        trusted_phone = payload.get("phone_verified", False) or phone_verification_source in {
            "platform_contact",
            "sms_otp",
        }

        try:
            result = CustomerService.resolve_or_register_customer(
                channel=payload["channel"],
                external_user_id=payload["external_user_id"],
                phone=payload.get("phone") or None,
                email=payload.get("email") or None,
                username=payload.get("username", ""),
                name=payload.get("display_name", ""),
                phone_verified=trusted_phone,
            )
        except ChannelIdentityAlreadyLinkedError as exc:
            response_payload = {
                "status": "conflict",
                "is_new_customer": False,
                "channel_linked": False,
                "registration_required": False,
                "next_action": str(exc),
            }
            response_serializer = IdentifyCustomerResponseSerializer(data=response_payload)
            response_serializer.is_valid(raise_exception=True)
            return Response(response_serializer.validated_data, status=status.HTTP_409_CONFLICT)

        response_payload = {
            "status": result.status,
            "is_new_customer": result.is_new_customer,
            "channel_linked": result.channel_linked,
            "registration_required": result.registration_required,
            "next_action": "request_phone" if result.registration_required else "",
            "channel": payload["channel"],
            "external_user_id": payload["external_user_id"],
        }
        if result.customer:
            response_payload["customer_id"] = result.customer.pk
            response_payload["customer_public_code"] = result.customer.public_code
            response_payload["phone"] = result.customer.phone
            response_payload["email"] = result.customer.email
            response_payload["display_name"] = result.customer.name

        response_serializer = IdentifyCustomerResponseSerializer(data=response_payload)
        response_serializer.is_valid(raise_exception=True)
        return Response(response_serializer.validated_data)
