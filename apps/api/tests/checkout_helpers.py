import json
from unittest.mock import patch

def confirmed_post(client, path, data=None, **kwargs):
    payload = json.loads(data) if isinstance(data, str) else dict(data or {})
    if "preview_id" not in payload:
        preview_payload = dict(payload)
        if payload.get("customer_email"):
            preview_payload["contact_email"] = payload["customer_email"]
        if path == "/api/orders/":
            if payload.get("delivery_quote_id"):
                from apps.delivery.models import DeliveryQuote
                quote = DeliveryQuote.objects.get(pk=payload["delivery_quote_id"])
                with patch("apps.delivery.quote_service.YandexDeliveryQuoteService.quote_cart", return_value=quote):
                    preview = client.post("/api/checkout/preview/", preview_payload, format="json")
            else:
                preview = client.post("/api/checkout/preview/", preview_payload, format="json")
            assert preview.status_code == 200, preview.data
            payload["preview_id"] = str(preview.data["preview_id"])
        else:
            preview = client.post("/store/checkout/preview/", json.dumps(preview_payload), content_type="application/json")
            assert preview.status_code == 200, preview.content
            payload["preview_id"] = preview.json()["preview_id"]
    return client.post(path, json.dumps(payload) if path.startswith("/store/") else payload, **kwargs)
