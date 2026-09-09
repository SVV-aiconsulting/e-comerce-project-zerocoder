from celery import shared_task
from django.utils import timezone
from apps.common.models import ServiceHeartbeat

@shared_task(name="ops.heartbeat")
def heartbeat(name):
    ServiceHeartbeat.objects.update_or_create(name=name, defaults={"last_seen_at": timezone.now()})
