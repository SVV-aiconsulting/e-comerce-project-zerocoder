"""Periodic status sync; dispatching shipments remains a manager action."""
from datetime import timedelta
from celery import shared_task
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from apps.delivery.models import Shipment
from apps.delivery.shipment_service import YandexShipmentService
from apps.common.exceptions import DeliveryError

@shared_task(name="delivery.sync_shipments")
def sync_shipments():
    now = timezone.now()
    with transaction.atomic():
        rows = list(Shipment.objects.select_for_update(skip_locked=True).exclude(external_request_id="")
            .exclude(status__in=["delivered", "cancelled", "returned"])
            .filter(Q(next_sync_at__isnull=True) | Q(next_sync_at__lte=now)).order_by("next_sync_at", "pk")[:20])
        Shipment.objects.filter(pk__in=[r.pk for r in rows]).update(next_sync_at=now+timedelta(minutes=5))
    success = failed = 0
    for row in rows:
        try:
            YandexShipmentService.sync(row)
        except DeliveryError:
            failed += 1
            attempts = row.sync_failures+1
            Shipment.objects.filter(pk=row.pk).update(sync_failures=attempts,
                next_sync_at=timezone.now()+timedelta(seconds=min(300*2**min(attempts, 6), 3600)))
        else:
            success += 1
            Shipment.objects.filter(pk=row.pk).update(sync_failures=0)
    return {"synced":success, "failed":failed}
