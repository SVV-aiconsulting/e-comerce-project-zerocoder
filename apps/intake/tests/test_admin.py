import pytest
from django.contrib import admin
from django.contrib.auth.models import User
from django.test import Client

from apps.common.enums import Channel
from apps.intake.enums import AssistantMessageRole
from apps.intake.models import (
    AIExtractionRun,
    AssistantMessage,
    AssistantToolCall,
    AssistantTurn,
    Clarification,
    InboundEvent,
    OrderDraft,
    OrderDraftItem,
    OutboundMessage,
)


@pytest.mark.django_db
def test_ai_admin_has_one_section_and_chronological_dialogue():
    draft = OrderDraft.objects.create(
        channel=Channel.WEBSITE,
        external_user_id="web:admin-test",
        conversation_key="admin-test",
    )
    first = InboundEvent.objects.create(
        channel=Channel.WEBSITE,
        external_event_id="admin-test-1",
        external_user_id="web:admin-test",
        conversation_key="admin-test",
        raw_text="Покажите белую рыбу",
        draft=draft,
    )
    AssistantMessage.objects.create(
        event=first,
        conversation_key=draft.conversation_key,
        role=AssistantMessageRole.USER,
        content="Покажите белую рыбу",
    )
    AssistantMessage.objects.create(
        event=first,
        conversation_key=draft.conversation_key,
        role=AssistantMessageRole.ASSISTANT,
        content="Есть треска и камбала.",
    )
    user = User.objects.create_superuser(
        username="admin-ai", email="admin-ai@example.com", password="secret"
    )
    client = Client()
    client.force_login(user)

    response = client.get(f"/admin/intake/orderdraft/{draft.pk}/change/")

    assert response.status_code == 200
    content = response.content.decode()
    assert "История диалога" in content
    assert content.index("Покажите белую рыбу") < content.index("Есть треска и камбала.")
    assert "Заказ ещё не оформлен" in content
    registered_intake_models = {
        model
        for model in admin.site._registry
        if model._meta.app_label == "intake"
    }
    assert registered_intake_models == {OrderDraft}
    assert not {
        AIExtractionRun,
        AssistantMessage,
        AssistantToolCall,
        AssistantTurn,
        Clarification,
        InboundEvent,
        OrderDraftItem,
        OutboundMessage,
    } & registered_intake_models


def test_order_admin_does_not_show_deleted_customer_column():
    from apps.orders.admin import OrderAdmin
    from apps.orders.models import Order

    model_admin = OrderAdmin(Order, admin.site)

    assert "customer_deleted" not in model_admin.list_display
