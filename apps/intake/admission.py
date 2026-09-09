"""Database-backed limits, shared by all frontend adapters."""
from datetime import timedelta
from django.utils import timezone
from apps.customers.models import AuthGuard, WebSessionBinding
from apps.intake.models import InboundEvent
from apps.common.exceptions import IntakeRateLimited
from apps.customers.web_accounts import digest

def check_admission(channel, external_user_id):
    identity = f"{channel}:{external_user_id}"
    binding = WebSessionBinding.objects.filter(
        website_user_id=external_user_id,
        revoked_at__isnull=True,
    ).first() if channel == "website" else None
    if binding:
        identity = f"account:{binding.account_id}"
        ids = WebSessionBinding.objects.filter(account_id=binding.account_id).values_list("website_user_id", flat=True)
        rows = InboundEvent.objects.filter(channel=channel, external_user_id__in=ids)
    else:
        rows = InboundEvent.objects.filter(channel=channel, external_user_id=external_user_id)
    key = "ai:" + digest(identity)
    AuthGuard.objects.get_or_create(key=key)
    AuthGuard.objects.select_for_update().get(key=key)
    now = timezone.now()
    if rows.filter(created_at__gte=now-timedelta(days=1)).count() >= 100:
        raise IntakeRateLimited(3600)
    if rows.filter(created_at__gte=now-timedelta(minutes=1)).count() >= 10:
        raise IntakeRateLimited(60)
    if rows.filter(status__in=["received", "queued", "processing", "retry_scheduled"]).exists():
        raise IntakeRateLimited(3)
