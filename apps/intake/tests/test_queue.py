from datetime import timedelta

import pytest
from celery.exceptions import Retry
from django.test import override_settings
from django.utils import timezone

from apps.common.enums import Channel
from apps.intake.enums import InboundEventStatus
from apps.intake.models import OrderDraft
from apps.intake.processors import InboundEventProcessor
from apps.intake.services import InboundEventService
from apps.intake.tasks import dispatch_pending_events, process_inbound_event


def register_event(*, event_id="event-1", text="Добавьте две упаковки креветок"):
    return InboundEventService.register(
        channel=Channel.TELEGRAM,
        external_event_id=event_id,
        external_user_id="user-1",
        conversation_key="chat-1",
        raw_text=text,
    ).event


@pytest.mark.django_db(transaction=True)
def test_enqueue_publishes_after_commit_only_once(monkeypatch):
    event = register_event()
    calls = []
    monkeypatch.setattr(
        process_inbound_event,
        "apply_async",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert InboundEventService.enqueue(event) is True
    assert InboundEventService.enqueue(event) is False

    event.refresh_from_db()
    assert event.status == InboundEventStatus.QUEUED
    assert calls == [
        ((), {"args": [event.pk], "queue": "intake", "retry": False})
    ]


@pytest.mark.django_db
def test_process_event_creates_one_draft_and_is_idempotent():
    event = register_event()

    first = process_inbound_event.run(event.pk)
    second = process_inbound_event.run(event.pk)

    event.refresh_from_db()
    assert first["status"] == InboundEventStatus.PROCESSED
    assert second["status"] == InboundEventStatus.PROCESSED
    assert event.status == InboundEventStatus.PROCESSED
    assert event.processing_attempts == 1
    assert event.processing_token is None
    assert event.draft_id == first["draft_id"]
    assert OrderDraft.objects.count() == 1


@pytest.mark.django_db
def test_blank_message_is_ignored_without_draft():
    event = register_event(text="   ")

    result = process_inbound_event.run(event.pk)

    event.refresh_from_db()
    assert result["status"] == InboundEventStatus.IGNORED
    assert event.status == InboundEventStatus.IGNORED
    assert event.processed_at is not None
    assert OrderDraft.objects.count() == 0


@pytest.mark.django_db
def test_recent_processing_lease_prevents_parallel_processing():
    event = register_event()
    event.status = InboundEventStatus.PROCESSING
    event.started_at = timezone.now()
    event.processing_attempts = 1
    event.save(update_fields=["status", "started_at", "processing_attempts", "updated_at"])

    result = process_inbound_event.run(event.pk)

    assert result["status"] == "already_processing"
    assert OrderDraft.objects.count() == 0


@pytest.mark.django_db
def test_transient_error_is_scheduled_with_backoff(monkeypatch):
    event = register_event()

    def fail(_event_id):
        raise ConnectionError("temporary provider outage")

    monkeypatch.setattr(InboundEventProcessor, "process", fail)
    monkeypatch.setattr("apps.intake.tasks._retry_countdown", lambda _attempts: 10)
    monkeypatch.setattr(
        process_inbound_event,
        "retry",
        lambda **kwargs: Retry(exc=kwargs["exc"], when=kwargs["countdown"]),
    )

    with pytest.raises(Retry):
        process_inbound_event.run(event.pk)

    event.refresh_from_db()
    assert event.status == InboundEventStatus.RETRY_SCHEDULED
    assert event.processing_attempts == 1
    assert event.processing_token is None
    assert event.next_retry_at is not None
    assert "ConnectionError" in event.last_error


@pytest.mark.django_db
@override_settings(INTAKE_MAX_PROCESSING_ATTEMPTS=1)
def test_event_fails_after_attempt_limit(monkeypatch):
    event = register_event()

    def fail(_event_id):
        raise ValueError("invalid event")

    monkeypatch.setattr(InboundEventProcessor, "process", fail)

    with pytest.raises(ValueError, match="invalid event"):
        process_inbound_event.run(event.pk)

    event.refresh_from_db()
    assert event.status == InboundEventStatus.FAILED
    assert event.processing_attempts == 1
    assert event.processed_at is not None


@pytest.mark.django_db
def test_dispatcher_selects_only_due_events(monkeypatch):
    due = register_event(event_id="due")
    future = register_event(event_id="future")
    future.status = InboundEventStatus.RETRY_SCHEDULED
    future.next_retry_at = timezone.now() + timedelta(minutes=5)
    future.save(update_fields=["status", "next_retry_at", "updated_at"])
    published = []
    monkeypatch.setattr(
        InboundEventService,
        "publish",
        lambda event_id: published.append(event_id) or True,
    )

    result = dispatch_pending_events.run()

    due.refresh_from_db()
    future.refresh_from_db()
    assert result == {"selected": 1, "published": 1}
    assert published == [due.pk]
    assert due.status == InboundEventStatus.QUEUED
    assert future.status == InboundEventStatus.RETRY_SCHEDULED


@pytest.mark.django_db
def test_broker_failure_keeps_event_for_redispatch(monkeypatch):
    event = register_event()
    event.status = InboundEventStatus.QUEUED
    event.save(update_fields=["status", "updated_at"])

    def fail_publish(*_args, **_kwargs):
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(process_inbound_event, "apply_async", fail_publish)

    assert InboundEventService.publish(event.pk) is False

    event.refresh_from_db()
    assert event.status == InboundEventStatus.QUEUED
    assert event.next_retry_at is not None
    assert "ConnectionError" in event.last_error
