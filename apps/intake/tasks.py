"""Celery-задачи над durable-событиями из PostgreSQL."""
import random
import uuid
from dataclasses import dataclass
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.intake.enums import InboundEventStatus
from apps.intake.exceptions import PermanentIntakeError
from apps.intake.models import InboundEvent
from apps.intake.processors import InboundEventProcessor
from apps.intake.services import InboundEventService

FINAL_STATUSES = (
    InboundEventStatus.PROCESSED,
    InboundEventStatus.IGNORED,
    InboundEventStatus.FAILED,
)


@dataclass(frozen=True)
class EventClaim:
    token: uuid.UUID | None
    attempts: int
    reason: str = "claimed"


def _claim_event(event_id: int) -> EventClaim:
    now = timezone.now()
    stale_before = now - timedelta(seconds=settings.INTAKE_EVENT_LEASE_SECONDS)
    with transaction.atomic():
        event = InboundEvent.objects.select_for_update().filter(pk=event_id).first()
        if event is None:
            return EventClaim(token=None, attempts=0, reason="missing")
        if event.status in FINAL_STATUSES:
            return EventClaim(
                token=None,
                attempts=event.processing_attempts,
                reason=event.status,
            )
        if (
            event.status == InboundEventStatus.RETRY_SCHEDULED
            and event.next_retry_at
            and event.next_retry_at > now
        ):
            return EventClaim(
                token=None,
                attempts=event.processing_attempts,
                reason="retry_not_due",
            )
        if (
            event.status == InboundEventStatus.PROCESSING
            and event.started_at
            and event.started_at > stale_before
        ):
            return EventClaim(
                token=None,
                attempts=event.processing_attempts,
                reason="already_processing",
            )

        if event.processing_attempts >= settings.INTAKE_MAX_PROCESSING_ATTEMPTS:
            event.status = InboundEventStatus.FAILED
            event.last_error = "Исчерпаны попытки восстановления обработки"
            event.processing_token = None
            event.save(update_fields=["status", "last_error", "processing_token", "updated_at"])
            return EventClaim(token=None, attempts=event.processing_attempts, reason="failed")
        token = uuid.uuid4()
        event.status = InboundEventStatus.PROCESSING
        event.processing_token = token
        event.processing_attempts += 1
        event.started_at = now
        event.processed_at = None
        event.next_retry_at = None
        event.save(
            update_fields=[
                "status",
                "processing_token",
                "processing_attempts",
                "started_at",
                "processed_at",
                "next_retry_at",
                "updated_at",
            ]
        )
        return EventClaim(token=token, attempts=event.processing_attempts)


def _finish_event(event_id: int, token: uuid.UUID, status: str) -> bool:
    with transaction.atomic():
        event = InboundEvent.objects.select_for_update().filter(pk=event_id).first()
        if event is None or event.processing_token != token:
            return False
        event.status = status
        event.processing_token = None
        event.processed_at = timezone.now()
        event.next_retry_at = None
        event.last_error = ""
        event.save(
            update_fields=[
                "status",
                "processing_token",
                "processed_at",
                "next_retry_at",
                "last_error",
                "updated_at",
            ]
        )
        return True


def _record_error(
    event_id: int,
    token: uuid.UUID,
    exc: Exception,
    *,
    retry_at=None,
) -> bool:
    with transaction.atomic():
        event = InboundEvent.objects.select_for_update().filter(pk=event_id).first()
        if event is None or event.processing_token != token:
            return False
        event.status = (
            InboundEventStatus.RETRY_SCHEDULED if retry_at else InboundEventStatus.FAILED
        )
        event.processing_token = None
        event.next_retry_at = retry_at
        event.processed_at = None if retry_at else timezone.now()
        event.last_error = f"{type(exc).__name__}: {str(exc)}"[:2000]
        event.save(
            update_fields=[
                "status",
                "processing_token",
                "next_retry_at",
                "processed_at",
                "last_error",
                "updated_at",
            ]
        )
        return True


def _retry_countdown(attempts: int) -> int:
    exponential = settings.INTAKE_RETRY_BASE_SECONDS * (2 ** max(attempts - 1, 0))
    capped = min(exponential, settings.INTAKE_RETRY_MAX_SECONDS)
    jitter_limit = max(1, min(capped // 4, 30))
    return capped + random.randint(0, jitter_limit)


@shared_task(
    bind=True,
    name="intake.process_inbound_event",
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_inbound_event(self, event_id: int):
    """Идемпотентно обработать одно входящее событие."""
    from apps.intake.leases import customer_mutex
    event = InboundEvent.objects.filter(pk=event_id).first()
    if event is None:
        return {"event_id": event_id, "status": "missing"}
    with customer_mutex(event.channel, event.external_user_id) as acquired:
        if not acquired:
            return {"event_id": event_id, "status": "customer_busy"}
        return _process_claimed(self, event_id)


def _process_claimed(self, event_id):
    from apps.intake.leases import claim_context, LeaseLost
    claim = _claim_event(event_id)
    if claim.token is None:
        return {"event_id": event_id, "status": claim.reason}

    context_token = claim_context.set((event_id, claim.token))
    try:
        outcome = InboundEventProcessor.process(event_id)
    except LeaseLost:
        return {"event_id": event_id, "status": "lease_lost"}
    except PermanentIntakeError as exc:
        _record_error(event_id, claim.token, exc)
        raise
    except Exception as exc:
        if claim.attempts >= settings.INTAKE_MAX_PROCESSING_ATTEMPTS:
            _record_error(event_id, claim.token, exc)
            raise

        countdown = _retry_countdown(claim.attempts)
        retry_at = timezone.now() + timedelta(seconds=countdown)
        _record_error(event_id, claim.token, exc, retry_at=retry_at)
        raise self.retry(exc=exc, countdown=countdown)

    finally:
        claim_context.reset(context_token)

    if not _finish_event(event_id, claim.token, outcome.status):
        return {"event_id": event_id, "status": "lease_lost"}
    return {
        "event_id": event_id,
        "status": outcome.status,
        "draft_id": outcome.draft_id,
    }


@shared_task(name="intake.dispatch_pending_events")
def dispatch_pending_events():
    """Переопубликовать события из PostgreSQL после сбоя брокера/worker."""
    now = timezone.now()
    due = (
        Q(status=InboundEventStatus.PROCESSING, started_at__lte=now-timedelta(seconds=settings.INTAKE_EVENT_LEASE_SECONDS))
        | Q(status=InboundEventStatus.RECEIVED)
        | Q(status=InboundEventStatus.QUEUED, next_retry_at__isnull=True)
        | Q(status=InboundEventStatus.QUEUED, next_retry_at__lte=now)
        | Q(status=InboundEventStatus.RETRY_SCHEDULED, next_retry_at__lte=now)
    )
    with transaction.atomic():
        events = list(
            InboundEvent.objects.select_for_update(skip_locked=True)
            .filter(due)
            .order_by("created_at")[: settings.INTAKE_DISPATCH_BATCH_SIZE]
        )
        event_ids = [event.pk for event in events]
        InboundEvent.objects.filter(pk__in=event_ids).update(
            status=InboundEventStatus.QUEUED,
            next_retry_at=now+timedelta(seconds=30),
            processing_token=None,
        )

    published = sum(InboundEventService.publish(event_id) for event_id in event_ids)
    return {"selected": len(event_ids), "published": published}


@shared_task(name="intake.poll_yandex_mail")
def poll_yandex_mail():
    if not settings.EMAIL_CHANNEL_ENABLED:
        return {"status": "disabled"}
    from apps.intake.channels.yandex_mail import YandexIMAPAdapter

    return YandexIMAPAdapter.poll()


@shared_task(name="intake.dispatch_email_responses")
def dispatch_email_responses():
    if not settings.EMAIL_CHANNEL_ENABLED:
        return {"status": "disabled"}
    from apps.intake.channels.yandex_mail import dispatch_email_outbounds

    return dispatch_email_outbounds()
