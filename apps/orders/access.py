"""Order authorization independent of contact matching and LLM arguments."""
from django.db.models import Q
from apps.orders.models import Order
from apps.customers.models import WebSessionBinding, OrderAccessGrant, CustomerChannelIdentity

class OrderAccessService:
    @staticmethod
    def visible(*, channel, external_user_id):
        if channel == "website":
            scope = Q(channel="website", source_external_user_id_snapshot=external_user_id)
            binding = WebSessionBinding.objects.filter(
                website_user_id=external_user_id,
                revoked_at__isnull=True,
            ).first()
            if binding:
                scope |= Q(access_grant__account_id=binding.account_id)
            return Order.objects.filter(scope).distinct()
        identity = CustomerChannelIdentity.objects.filter(channel=channel,
            external_user_id=external_user_id).first()
        if not identity:
            return Order.objects.none()
        return Order.objects.filter(
            customer_id=identity.customer_id,
            channel=channel,
        )

    @staticmethod
    def bind_created(order):
        if order.channel == "website":
            binding = WebSessionBinding.objects.filter(
                website_user_id=order.source_external_user_id_snapshot,
                revoked_at__isnull=True,
            ).first()
            if binding:
                OrderAccessGrant.objects.get_or_create(order=order,
                    defaults={"account_id": binding.account_id, "reason": "authenticated_checkout"})
