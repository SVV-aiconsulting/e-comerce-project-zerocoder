from urllib.parse import urlsplit

from django.conf import settings
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.auth import AdapterTokenAuthentication
from apps.api.serializers.privacy import ConsentActionSerializer, ConsentIdentitySerializer
from apps.customers.services import CustomerService
from apps.privacy.models import ConsentMethod, ConsentStatus, IdentityType
from apps.privacy.services import ConsentService


class ConsentStatusView(APIView):
    authentication_classes = [AdapterTokenAuthentication]
    permission_classes = []

    @staticmethod
    def identity_type(channel):
        return {
            "telegram": IdentityType.TELEGRAM_USER_ID,
            "vk": IdentityType.VK_USER_ID,
            "max": IdentityType.MAX_USER_ID,
        }[channel]

    @staticmethod
    def public_url(request, path):
        configured = settings.PUBLIC_SITE_URL.strip().rstrip("/")
        if not configured:
            payment_return = urlsplit(settings.YOOKASSA_RETURN_URL)
            if payment_return.scheme and payment_return.netloc:
                configured = f"{payment_return.scheme}://{payment_return.netloc}"
        return f"{configured}{path}" if configured else request.build_absolute_uri(path)

    def get(self, request):
        serializer = ConsentIdentitySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        documents = ConsentService.current_documents()
        granted = ConsentService.has_current_consent(
            channel=data["channel"], identity_value=data["external_user_id"]
        )
        return Response(
            {
                "granted": granted,
                "policy_url": self.public_url(request, "/privacy-policy/"),
                "consent_url": self.public_url(request, "/personal-data-consent/"),
                "policy_version": documents.policy.version,
                "consent_version": documents.consent.version,
            }
        )

    def post(self, request):
        serializer = ConsentActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        customer = CustomerService.find_by_channel_identity(
            data["channel"], data["external_user_id"]
        )
        if data["action"] == ConsentStatus.WITHDRAWN:
            event = ConsentService.withdraw(
                channel=data["channel"],
                identity_type=self.identity_type(data["channel"]),
                identity_value=data["external_user_id"],
                source=data["source"],
                expression_method=ConsentMethod.BOT_COMMAND,
                customer=customer,
            )
        else:
            event = ConsentService.record(
                channel=data["channel"],
                identity_type=self.identity_type(data["channel"]),
                identity_value=data["external_user_id"],
                source=data["source"],
                status=data["action"],
                expression_method=ConsentMethod.BOT_BUTTON,
                customer=customer,
                evidence={"adapter_authenticated": True},
            )
            # A consent granted after account erasure starts a fresh dialogue.
            # This second guard removes any orphan cart/draft left by releases
            # that predated the erasure cleanup.
            previous = event.previous_event
            if (
                event.status == ConsentStatus.GRANTED
                and previous is not None
                and previous.status == ConsentStatus.WITHDRAWN
                and previous.source == "customer_card_erased"
            ):
                CustomerService.erase_transient_channel_state(
                    channel=data["channel"],
                    external_user_id=data["external_user_id"],
                )
            if customer is not None and event.status == ConsentStatus.GRANTED:
                customer.personal_data_consent = True
                customer.save(update_fields=["personal_data_consent", "updated_at"])
        return Response({"status": event.status, "event_id": str(event.public_id)})
