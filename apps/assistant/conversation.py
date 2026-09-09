"""Persist conversational references while keeping business assertions in cards."""
import re
from apps.intake.models import (
    ConversationMemory,
    AssistantToolCall,
    InboundEvent,
    OrderDraft,
)

def safe_narration(content):
    # Free-form questions may guide a choice, but prices, claims of actions and
    # product assertions must come from the server card. Fail closed to a question.
    content = (content or "").strip()
    if len(content) > 500 or re.search(r"[\d₽]|https?://|www\.", content, re.I):
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", content)
    questions = [s for s in sentences if s.endswith("?") and not re.search(
        r"(?:оформлен|оплачен|создан|добавил|стоит|стоимость|скидк|гарант|свеж|полез|безопас|бесплат)", s, re.I)]
    return " ".join(questions[:1])


def remember(event, content, response_type):
    memory, _ = ConversationMemory.objects.get_or_create(channel=event.channel,
        external_user_id=event.external_user_id, conversation_key=event.conversation_key)
    questions = re.findall(r"[^.!?\n]+\?", content)
    if questions:
        memory.last_question = questions[-1].strip()
    call = AssistantToolCall.objects.filter(turn__event=event, status="succeeded",
        tool_name__in=["search_products", "compare_products", "recommend_products"]).order_by("-call_index").first()
    if call:
        memory.options = [{"code":p["code"], "name":p["name"]} for p in call.result.get("products", [])]
    changed = AssistantToolCall.objects.filter(turn__event=event, status="succeeded", tool_name="set_cart_item").order_by("-call_index").first()
    if changed:
        memory.selected_product_code = changed.arguments.get("product_code", "")
    draft_id = InboundEvent.objects.filter(pk=event.pk).values_list(
        "draft_id", flat=True
    ).first()
    draft = OrderDraft.objects.filter(pk=draft_id).first()
    if draft:
        memory.expected_fields = draft.missing_fields
    memory.save()
