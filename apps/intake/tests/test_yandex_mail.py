from email.message import EmailMessage
import imaplib
from unittest.mock import MagicMock

import pytest

from apps.common.enums import Channel
from apps.intake.channels.yandex_mail import (
    YandexIMAPAdapter,
    check_yandex_connections,
    dispatch_email_outbounds,
    parse_email,
)
from apps.intake.enums import InboundEventStatus, OrderDraftStatus, OutboundMessageStatus
from apps.intake.exceptions import EmailProviderError
from apps.intake.models import Clarification, InboundEvent, OutboundMessage
from apps.intake.services import InboundEventService, OrderDraftService


def raw_email(
    body="Хочу две упаковки креветок. Телефон +7 999 123-45-67",
    *,
    content_subtype="plain",
):
    message = EmailMessage()
    message["From"] = "Анна Покупатель <anna@example.com>"
    message["To"] = "orders@example.ru"
    message["Subject"] = "Заказ креветок"
    message["Message-ID"] = "<customer-message-1@example.com>"
    message.set_content(body, subtype=content_subtype)
    return message.as_bytes()


class FakeIMAP:
    instances = []
    message_bytes = raw_email()

    def __init__(self, *_args, **_kwargs):
        self.calls = []
        self.__class__.instances.append(self)

    def login(self, username, password):
        self.calls.append(("login", username, password))
        return "OK", []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", [b"1"]

    def uid(self, command, *args):
        self.calls.append(("uid", command, args))
        if command == "search":
            return "OK", [b"101"]
        if command == "fetch":
            return "OK", [(b"101 (BODY[] {100})", self.message_bytes), b")"]
        if command == "store":
            return "OK", [b"101"]
        raise AssertionError(command)

    def logout(self):
        self.calls.append(("logout",))
        return "BYE", []


@pytest.fixture
def email_settings(settings):
    settings.EMAIL_CHANNEL_ENABLED = True
    settings.YANDEX_EMAIL_ADDRESS = "orders@example.ru"
    settings.YANDEX_EMAIL_APP_PASSWORD = "fixture-app-password"
    settings.YANDEX_IMAP_HOST = "imap.yandex.test"
    settings.YANDEX_IMAP_PORT = 993
    settings.YANDEX_IMAP_FOLDER = "INBOX"
    settings.YANDEX_SMTP_HOST = "smtp.yandex.test"
    settings.YANDEX_SMTP_PORT = 465
    settings.EMAIL_POLL_BATCH_SIZE = 50
    settings.EMAIL_MAX_MESSAGE_BYTES = 1024 * 1024
    settings.EMAIL_OUTBOUND_LEASE_SECONDS = 300
    settings.EMAIL_OUTBOUND_MAX_ATTEMPTS = 3
    settings.EMAIL_NETWORK_TIMEOUT_SECONDS = 1
    return settings


def test_parse_html_email_and_remove_quoted_reply():
    parsed = parse_email(
        raw_email(
            "<p>Нужен один лосось.</p><blockquote>старый заказ</blockquote>",
            content_subtype="html",
        )
    )

    assert "Нужен один лосось" in parsed.body
    assert "старый заказ" not in parsed.body
    assert parsed.from_address == "anna@example.com"


def test_connection_check_is_read_only(monkeypatch, email_settings):
    FakeIMAP.instances.clear()

    class FakeSMTP:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def login(self, *_args):
            return None

        def noop(self):
            return 250, b"OK"

    monkeypatch.setattr(
        "apps.intake.channels.yandex_mail.imaplib.IMAP4_SSL",
        FakeIMAP,
    )
    monkeypatch.setattr(
        "apps.intake.channels.yandex_mail.smtplib.SMTP_SSL",
        FakeSMTP,
    )

    assert check_yandex_connections() == {"imap": True, "smtp": True}
    select_calls = [
        call
        for instance in FakeIMAP.instances
        for call in instance.calls
        if call[0] == "select"
    ]
    assert select_calls == [("select", "INBOX", True)]
    assert not any(
        call[0] == "uid" for instance in FakeIMAP.instances for call in instance.calls
    )


def test_connection_check_explains_imap_auth_failure(monkeypatch, email_settings):
    class RejectingIMAP(FakeIMAP):
        def login(self, *_args):
            raise imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] invalid credentials")

    monkeypatch.setattr(
        "apps.intake.channels.yandex_mail.imaplib.IMAP4_SSL",
        RejectingIMAP,
    )

    with pytest.raises(EmailProviderError, match="IMAP-авторизацию"):
        check_yandex_connections()


@pytest.mark.django_db(transaction=True)
def test_imap_poll_registers_email_once_and_marks_seen(
    monkeypatch,
    email_settings,
):
    FakeIMAP.instances.clear()
    FakeIMAP.message_bytes = raw_email()
    monkeypatch.setattr(
        "apps.intake.channels.yandex_mail.imaplib.IMAP4_SSL",
        FakeIMAP,
    )
    monkeypatch.setattr(InboundEventService, "publish", lambda _event_id: True)

    first = YandexIMAPAdapter.poll()
    second = YandexIMAPAdapter.poll()

    assert first == {"selected": 1, "created": 1, "duplicates": 0, "skipped": 0}
    assert second == {"selected": 1, "created": 0, "duplicates": 1, "skipped": 0}
    assert InboundEvent.objects.count() == 1
    event = InboundEvent.objects.select_related("customer").get()
    assert event.channel == Channel.EMAIL
    assert event.status == InboundEventStatus.QUEUED
    assert event.customer.phone == "79991234567"
    assert event.customer.email == "anna@example.com"
    assert event.external_user_id.startswith("email:")
    assert "anna@example.com" not in event.external_user_id
    assert event.raw_payload["message_id"] == "<customer-message-1@example.com>"
    store_calls = [
        call
        for instance in FakeIMAP.instances
        for call in instance.calls
        if call[:2] == ("uid", "store")
    ]
    assert len(store_calls) == 2


@pytest.mark.django_db(transaction=True)
def test_imap_poll_creates_email_customer_without_phone(
    monkeypatch,
    email_settings,
):
    FakeIMAP.instances.clear()
    FakeIMAP.message_bytes = raw_email("Хочу одну упаковку креветок")
    monkeypatch.setattr(
        "apps.intake.channels.yandex_mail.imaplib.IMAP4_SSL",
        FakeIMAP,
    )
    monkeypatch.setattr(InboundEventService, "publish", lambda _event_id: True)

    result = YandexIMAPAdapter.poll()

    assert result["created"] == 1
    event = InboundEvent.objects.select_related("customer").get()
    assert event.customer is not None
    assert event.customer.email == "anna@example.com"
    assert event.customer.phone == ""


@pytest.mark.django_db
def test_email_response_is_sent_once_with_thread_headers(
    monkeypatch,
    email_settings,
    customer,
):
    event = InboundEventService.register(
        channel=Channel.EMAIL,
        external_event_id="email-outbound-1",
        external_user_id="email:fixture",
        conversation_key="email:fixture",
        customer=customer,
        raw_text="Хочу рыбу",
        raw_payload={
            "from_address": "anna@example.com",
            "subject": "Заказ рыбы",
            "message_id": "<inbound-1@example.com>",
            "references": "<older@example.com>",
        },
    ).event
    draft, _ = OrderDraftService.get_or_create_active(
        channel=Channel.EMAIL,
        external_user_id=event.external_user_id,
        conversation_key=event.conversation_key,
        customer=customer,
    )
    draft.status = OrderDraftStatus.NEEDS_CLARIFICATION
    draft.missing_fields = ["items.0.product"]
    draft.save(update_fields=["status", "missing_fields", "updated_at"])
    event.draft = draft
    event.status = InboundEventStatus.PROCESSED
    event.save(update_fields=["draft", "status", "updated_at"])
    Clarification.objects.create(
        draft=draft,
        field_path="items.0.product",
        question="Какую именно рыбу вы хотите?",
        trigger_event=event,
    )
    sent_messages = []

    class FakeSMTP:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def login(self, *_args):
            return None

        def send_message(self, message):
            sent_messages.append(message)

    monkeypatch.setattr(
        "apps.intake.channels.yandex_mail.smtplib.SMTP_SSL",
        FakeSMTP,
    )

    first = dispatch_email_outbounds()
    second = dispatch_email_outbounds()

    outbound = OutboundMessage.objects.get()
    assert first["prepared"] == 1
    assert first["sent"] == 1
    assert second["prepared"] == 0
    assert second["sent"] == 0
    assert outbound.status == OutboundMessageStatus.SENT
    assert outbound.delivery_attempts == 1
    assert len(sent_messages) == 1
    assert sent_messages[0]["To"] == "anna@example.com"
    assert sent_messages[0]["In-Reply-To"] == "<inbound-1@example.com>"
    assert "<older@example.com>" in sent_messages[0]["References"]
    assert "<inbound-1@example.com>" in sent_messages[0]["References"]


@pytest.mark.django_db
def test_smtp_failure_is_scheduled_without_secret_in_error(
    monkeypatch,
    email_settings,
    customer,
):
    event = InboundEvent.objects.create(
        channel=Channel.EMAIL,
        external_event_id="email-outbound-failure",
        external_user_id="email:failure",
        conversation_key="email:failure",
        customer=customer,
        raw_text="test",
        raw_payload={"from_address": "anna@example.com"},
        status=InboundEventStatus.FAILED,
    )

    class FailingSMTP:
        def __init__(self, *_args, **_kwargs):
            raise OSError("network unavailable")

    monkeypatch.setattr(
        "apps.intake.channels.yandex_mail.smtplib.SMTP_SSL",
        FailingSMTP,
    )

    result = dispatch_email_outbounds()

    outbound = OutboundMessage.objects.get(event=event)
    assert result["retried"] == 1
    assert outbound.status == OutboundMessageStatus.RETRY_SCHEDULED
    assert outbound.next_retry_at is not None
    assert email_settings.YANDEX_EMAIL_APP_PASSWORD not in outbound.last_error
