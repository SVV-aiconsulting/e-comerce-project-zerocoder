"""Яндекс Почта: безопасный IMAP-приём и durable SMTP-ответы."""
from __future__ import annotations

import hashlib
import hmac
import imaplib
import re
import smtplib
import ssl
import uuid
from dataclasses import dataclass
from datetime import timedelta
from email import policy
from email.errors import MessageError
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr
from html.parser import HTMLParser

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.common.enums import Channel
from apps.customers.services import CustomerService
from apps.customers.validators import normalize_phone, validate_phone
from apps.intake.enums import InboundEventStatus, OutboundMessageStatus
from apps.intake.exceptions import EmailConfigurationError, EmailProviderError
from apps.intake.models import InboundEvent, OutboundMessage
from apps.intake.responses import InboundEventResponseService
from apps.intake.services import InboundEventService

FINAL_EVENT_STATUSES = (
    InboundEventStatus.PROCESSED,
    InboundEventStatus.IGNORED,
    InboundEventStatus.FAILED,
)
PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:\+?7|8)(?:[\s().-]*\d){10}(?!\d)",
    re.IGNORECASE,
)
QUOTED_REPLY_MARKERS = (
    "-----original message-----",
    "-----исходное сообщение-----",
    "от:",
    "from:",
)


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.ignored_depth = 0

    def handle_starttag(self, tag, _attrs):
        if tag.casefold() in {"blockquote", "script", "style"}:
            self.ignored_depth += 1

    def handle_endtag(self, tag):
        if tag.casefold() in {"blockquote", "script", "style"} and self.ignored_depth:
            self.ignored_depth -= 1

    def handle_data(self, data):
        if not self.ignored_depth and data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        return "\n".join(self.parts)


@dataclass(frozen=True)
class ParsedEmail:
    from_address: str
    from_name: str
    subject: str
    body: str
    message_id: str
    in_reply_to: str
    references: str
    auto_submitted: bool


@dataclass(frozen=True)
class OutboundClaim:
    message_id: int | None
    token: uuid.UUID | None


def _require_email_configuration() -> None:
    if not settings.YANDEX_EMAIL_ADDRESS.strip():
        raise EmailConfigurationError("Не задан YANDEX_EMAIL_ADDRESS")
    if not settings.YANDEX_EMAIL_APP_PASSWORD.strip():
        raise EmailConfigurationError("Не задан YANDEX_EMAIL_APP_PASSWORD")
    try:
        validate_email(settings.YANDEX_EMAIL_ADDRESS.strip())
    except ValidationError as exc:
        raise EmailConfigurationError("YANDEX_EMAIL_ADDRESS некорректен") from exc
    if not settings.YANDEX_IMAP_HOST.strip() or not settings.YANDEX_SMTP_HOST.strip():
        raise EmailConfigurationError("Не заданы хосты Яндекс IMAP/SMTP")


def _safe_header(value, max_length: int) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())[
        :max_length
    ]


def _strip_quoted_reply(text: str) -> str:
    kept = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = line.strip()
        lowered = stripped.casefold()
        if stripped.startswith(">"):
            continue
        if any(lowered.startswith(marker) for marker in QUOTED_REPLY_MARKERS):
            break
        if stripped == "--":
            break
        kept.append(line)
    return "\n".join(kept).strip()


def _extract_body(message) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, UnicodeError):
        payload = part.get_payload(decode=True) or b""
        content = payload.decode("utf-8", errors="replace")
    if part.get_content_type() == "text/html":
        extractor = _HTMLTextExtractor()
        extractor.feed(str(content))
        content = extractor.text()
    return _strip_quoted_reply(str(content))[:20_000]


def parse_email(raw_message: bytes) -> ParsedEmail:
    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    from_name, from_address = parseaddr(str(message.get("From", "")))
    from_address = from_address.strip().casefold()
    validate_email(from_address)
    local_part = from_address.split("@", 1)[0]
    precedence = str(message.get("Precedence", "")).casefold()
    auto_submitted = (
        str(message.get("Auto-Submitted", "no")).casefold() != "no"
        or precedence in {"bulk", "junk", "list"}
        or local_part in {"mailer-daemon", "postmaster", "no-reply", "noreply"}
    )
    return ParsedEmail(
        from_address=from_address,
        from_name=_safe_header(from_name, 255),
        subject=_safe_header(message.get("Subject", ""), 900),
        body=_extract_body(message),
        message_id=_safe_header(message.get("Message-ID", ""), 998),
        in_reply_to=_safe_header(message.get("In-Reply-To", ""), 998),
        references=_safe_header(message.get("References", ""), 2000),
        auto_submitted=auto_submitted,
    )


def _email_identity(address: str) -> str:
    digest = hmac.new(
        settings.SECRET_KEY.encode(),
        address.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"email:{digest[:48]}"


def _external_event_id(parsed: ParsedEmail, raw_message: bytes) -> str:
    identity = (
        f"{parsed.from_address}\0{parsed.message_id}".encode()
        if parsed.message_id
        else raw_message
    )
    return f"message:{hashlib.sha256(identity).hexdigest()}"


def _extract_phone(text: str) -> str | None:
    for match in PHONE_PATTERN.finditer(text):
        try:
            phone = normalize_phone(match.group())
            validate_phone(phone)
            return phone
        except ValidationError:
            continue
    return None


def _resolve_customer(parsed: ParsedEmail, external_user_id: str):
    phone = _extract_phone(parsed.body)
    result = CustomerService.resolve_email_customer(
        external_user_id=external_user_id,
        email=parsed.from_address,
        phone=phone or "",
        name=parsed.from_name or parsed.from_address.split("@", 1)[0],
    )
    return result.customer


class YandexIMAPAdapter:
    @classmethod
    def poll(cls) -> dict:
        _require_email_configuration()
        counters = {"selected": 0, "created": 0, "duplicates": 0, "skipped": 0}
        mailbox = None
        try:
            mailbox = imaplib.IMAP4_SSL(
                settings.YANDEX_IMAP_HOST,
                settings.YANDEX_IMAP_PORT,
                ssl_context=ssl.create_default_context(),
                timeout=settings.EMAIL_NETWORK_TIMEOUT_SECONDS,
            )
            mailbox.login(
                settings.YANDEX_EMAIL_ADDRESS,
                settings.YANDEX_EMAIL_APP_PASSWORD,
            )
            status, _ = mailbox.select(settings.YANDEX_IMAP_FOLDER, readonly=False)
            if status != "OK":
                raise EmailProviderError("Яндекс IMAP не открыл выбранную папку")
            status, data = mailbox.uid("search", None, "UNSEEN")
            if status != "OK":
                raise EmailProviderError("Яндекс IMAP не выполнил поиск писем")
            uids = (data[0] or b"").split()[: settings.EMAIL_POLL_BATCH_SIZE]
            counters["selected"] = len(uids)
            for uid in uids:
                outcome = cls._process_uid(mailbox, uid)
                counters[outcome] += 1
            return counters
        except (imaplib.IMAP4.error, OSError, ssl.SSLError) as exc:
            raise EmailProviderError("Ошибка подключения к Яндекс IMAP") from exc
        finally:
            if mailbox is not None:
                try:
                    mailbox.logout()
                except (imaplib.IMAP4.error, OSError):
                    pass

    @classmethod
    def _process_uid(cls, mailbox, uid: bytes) -> str:
        status, data = mailbox.uid("fetch", uid, "(BODY.PEEK[])")
        if status != "OK":
            raise EmailProviderError("Яндекс IMAP не получил письмо")
        raw_message = next(
            (
                part[1]
                for part in data
                if isinstance(part, tuple)
                and len(part) > 1
                and isinstance(part[1], bytes)
            ),
            b"",
        )
        if not raw_message or len(raw_message) > settings.EMAIL_MAX_MESSAGE_BYTES:
            cls._mark_seen(mailbox, uid)
            return "skipped"
        try:
            parsed = parse_email(raw_message)
        except (MessageError, UnicodeError, ValueError, ValidationError):
            cls._mark_seen(mailbox, uid)
            return "skipped"
        if (
            parsed.auto_submitted
            or parsed.from_address == settings.YANDEX_EMAIL_ADDRESS.casefold()
            or not parsed.body
        ):
            cls._mark_seen(mailbox, uid)
            return "skipped"

        external_user_id = _email_identity(parsed.from_address)
        customer = _resolve_customer(parsed, external_user_id)
        raw_text = f"Тема: {parsed.subject}\n\n{parsed.body}" if parsed.subject else parsed.body
        registration = InboundEventService.register(
            channel=Channel.EMAIL,
            external_event_id=_external_event_id(parsed, raw_message),
            external_user_id=external_user_id,
            conversation_key=external_user_id,
            customer=customer,
            raw_text=raw_text,
            raw_payload={
                "source": "yandex_imap",
                "imap_uid": uid.decode(errors="replace")[:64],
                "from_address": parsed.from_address,
                "from_name": parsed.from_name,
                "contact_email": parsed.from_address,
                "contact_phone": _extract_phone(parsed.body) or "",
                "subject": parsed.subject,
                "message_id": parsed.message_id,
                "in_reply_to": parsed.in_reply_to,
                "references": parsed.references,
            },
        )
        InboundEventService.enqueue(registration.event)
        cls._mark_seen(mailbox, uid)
        return "created" if registration.created else "duplicates"

    @staticmethod
    def _mark_seen(mailbox, uid: bytes) -> None:
        status, _ = mailbox.uid("store", uid, "+FLAGS.SILENT", "(\\Seen)")
        if status != "OK":
            raise EmailProviderError("Яндекс IMAP не отметил письмо прочитанным")


def prepare_email_outbounds(batch_size: int | None = None) -> int:
    batch_size = batch_size or settings.EMAIL_POLL_BATCH_SIZE
    events = list(
        InboundEvent.objects.select_related("draft__converted_order")
        .filter(
            channel=Channel.EMAIL,
            status__in=FINAL_EVENT_STATUSES,
            outbound_message__isnull=True,
        )
        .order_by("processed_at", "id")[:batch_size]
    )
    prepared = 0
    for event in events:
        response = InboundEventResponseService.present(event).get("response")
        recipient = str(event.raw_payload.get("from_address", "")).strip().casefold()
        if not response or not recipient:
            continue
        try:
            validate_email(recipient)
        except ValidationError:
            continue
        original_subject = _safe_header(event.raw_payload.get("subject", ""), 900)
        subject = original_subject if original_subject.casefold().startswith("re:") else (
            f"Re: {original_subject}" if original_subject else "WebMarket: ваш заказ"
        )
        original_message_id = _safe_header(event.raw_payload.get("message_id", ""), 998)
        references = _safe_header(event.raw_payload.get("references", ""), 1800)
        if original_message_id and original_message_id not in references:
            references = f"{references} {original_message_id}".strip()
        digest = hashlib.sha256(response["id"].encode()).hexdigest()[:32]
        domain = settings.YANDEX_EMAIL_ADDRESS.rsplit("@", 1)[-1]
        _, created = OutboundMessage.objects.get_or_create(
            event=event,
            defaults={
                "channel": Channel.EMAIL,
                "recipient": recipient,
                "response_id": response["id"],
                "subject": subject,
                "body": response["message"],
                "headers": {
                    "in_reply_to": original_message_id,
                    "references": references,
                },
                "provider_message_id": f"<webmarket-{digest}@{domain}>",
            },
        )
        prepared += int(created)
    return prepared


def _claim_outbound() -> OutboundClaim:
    now = timezone.now()
    stale_before = now - timedelta(seconds=settings.EMAIL_OUTBOUND_LEASE_SECONDS)
    due = (
        Q(status=OutboundMessageStatus.PENDING)
        | Q(
            status=OutboundMessageStatus.RETRY_SCHEDULED,
            next_retry_at__lte=now,
        )
        | Q(status=OutboundMessageStatus.SENDING, started_at__lt=stale_before)
    )
    with transaction.atomic():
        outbound = (
            OutboundMessage.objects.select_for_update(skip_locked=True)
            .filter(channel=Channel.EMAIL)
            .filter(due)
            .order_by("created_at", "id")
            .first()
        )
        if outbound is None:
            return OutboundClaim(message_id=None, token=None)
        token = uuid.uuid4()
        outbound.status = OutboundMessageStatus.SENDING
        outbound.processing_token = token
        outbound.started_at = now
        outbound.next_retry_at = None
        outbound.delivery_attempts += 1
        outbound.save(
            update_fields=[
                "status",
                "processing_token",
                "started_at",
                "next_retry_at",
                "delivery_attempts",
                "updated_at",
            ]
        )
        return OutboundClaim(message_id=outbound.pk, token=token)


def _send_outbound(outbound: OutboundMessage) -> None:
    _require_email_configuration()
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = settings.YANDEX_EMAIL_ADDRESS
    message["To"] = outbound.recipient
    message["Subject"] = outbound.subject
    message["Message-ID"] = outbound.provider_message_id
    if outbound.headers.get("in_reply_to"):
        message["In-Reply-To"] = outbound.headers["in_reply_to"]
    if outbound.headers.get("references"):
        message["References"] = outbound.headers["references"]
    message.set_content(outbound.body)
    try:
        with smtplib.SMTP_SSL(
            settings.YANDEX_SMTP_HOST,
            settings.YANDEX_SMTP_PORT,
            context=ssl.create_default_context(),
            timeout=settings.EMAIL_NETWORK_TIMEOUT_SECONDS,
        ) as smtp:
            smtp.login(
                settings.YANDEX_EMAIL_ADDRESS,
                settings.YANDEX_EMAIL_APP_PASSWORD,
            )
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        raise EmailProviderError("Ошибка отправки через Яндекс SMTP") from exc


def _finish_outbound(claim: OutboundClaim, *, error: Exception | None = None) -> str:
    with transaction.atomic():
        outbound = OutboundMessage.objects.select_for_update().get(pk=claim.message_id)
        if outbound.processing_token != claim.token:
            return "lease_lost"
        outbound.processing_token = None
        if error is None:
            outbound.status = OutboundMessageStatus.SENT
            outbound.sent_at = timezone.now()
            outbound.last_error = ""
            outcome = "sent"
        elif outbound.delivery_attempts >= settings.EMAIL_OUTBOUND_MAX_ATTEMPTS:
            outbound.status = OutboundMessageStatus.FAILED
            outbound.last_error = f"{type(error).__name__}: {str(error)}"[:2000]
            outcome = "failed"
        else:
            delay = min(30 * (2 ** (outbound.delivery_attempts - 1)), 1800)
            outbound.status = OutboundMessageStatus.RETRY_SCHEDULED
            outbound.next_retry_at = timezone.now() + timedelta(seconds=delay)
            outbound.last_error = f"{type(error).__name__}: {str(error)}"[:2000]
            outcome = "retried"
        outbound.save(
            update_fields=[
                "status",
                "processing_token",
                "sent_at",
                "next_retry_at",
                "last_error",
                "updated_at",
            ]
        )
        return outcome


def dispatch_email_outbounds() -> dict:
    _require_email_configuration()
    counters = {
        "prepared": prepare_email_outbounds(),
        "sent": 0,
        "retried": 0,
        "failed": 0,
        "lease_lost": 0,
    }
    for _ in range(settings.EMAIL_POLL_BATCH_SIZE):
        claim = _claim_outbound()
        if claim.message_id is None:
            break
        outbound = OutboundMessage.objects.get(pk=claim.message_id)
        try:
            _send_outbound(outbound)
        except EmailProviderError as exc:
            outcome = _finish_outbound(claim, error=exc)
        else:
            outcome = _finish_outbound(claim)
        counters[outcome] += 1
    return counters


def check_yandex_connections() -> dict:
    """Проверить TLS и авторизацию, не читая и не изменяя письма."""
    _require_email_configuration()
    result = {"imap": False, "smtp": False}
    try:
        with imaplib.IMAP4_SSL(
            settings.YANDEX_IMAP_HOST,
            settings.YANDEX_IMAP_PORT,
            ssl_context=ssl.create_default_context(),
            timeout=settings.EMAIL_NETWORK_TIMEOUT_SECONDS,
        ) as mailbox:
            mailbox.login(
                settings.YANDEX_EMAIL_ADDRESS,
                settings.YANDEX_EMAIL_APP_PASSWORD,
            )
            status, _ = mailbox.select(settings.YANDEX_IMAP_FOLDER, readonly=True)
            if status != "OK":
                raise EmailProviderError("Яндекс IMAP не открыл папку в read-only режиме")
            result["imap"] = True
    except imaplib.IMAP4.error as exc:
        if "AUTHENTICATIONFAILED" in str(exc).upper():
            raise EmailProviderError(
                "Яндекс отклонил IMAP-авторизацию: проверьте полный адрес ящика, "
                "пароль приложения и включение IMAP"
            ) from exc
        raise EmailProviderError("Ошибка проверки Яндекс IMAP") from exc
    except (OSError, ssl.SSLError) as exc:
        raise EmailProviderError("Ошибка подключения к Яндекс IMAP") from exc

    try:
        with smtplib.SMTP_SSL(
            settings.YANDEX_SMTP_HOST,
            settings.YANDEX_SMTP_PORT,
            context=ssl.create_default_context(),
            timeout=settings.EMAIL_NETWORK_TIMEOUT_SECONDS,
        ) as smtp:
            smtp.login(
                settings.YANDEX_EMAIL_ADDRESS,
                settings.YANDEX_EMAIL_APP_PASSWORD,
            )
            smtp.noop()
            result["smtp"] = True
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailProviderError(
            "Яндекс отклонил SMTP-авторизацию: проверьте полный адрес ящика "
            "и пароль приложения"
        ) from exc
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        raise EmailProviderError("Ошибка подключения к Яндекс SMTP") from exc
    return result
