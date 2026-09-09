"""Per-customer PostgreSQL mutex and fencing for a claimed inbound event."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
from django.db import connection, transaction
from apps.intake.models import InboundEvent

claim_context = ContextVar("intake_claim", default=None)
class LeaseLost(Exception):
    pass

@contextmanager
def customer_mutex(channel, external_user_id):
    key = int.from_bytes(hashlib.sha256(f"{channel}:{external_user_id}".encode()).digest()[:8], "big", signed=True)
    locked = True
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
            locked = cursor.fetchone()[0]
    try:
        yield locked
    finally:
        if locked and connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [key])

@contextmanager
def fenced_write():
    claim = claim_context.get()
    if claim is None:
        yield
        return
    with transaction.atomic():
        if not InboundEvent.objects.select_for_update().filter(pk=claim[0],
                processing_token=claim[1], status="processing").exists():
            raise LeaseLost("Обработка передана другому worker")
        yield


def check_lease():
    with fenced_write():
        pass
