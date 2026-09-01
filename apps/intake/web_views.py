"""Browser-safe веб-канал без публикации adapter token в JavaScript."""

from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView

from apps.api.serializers.catalog import ProductListSerializer
from apps.catalog.services import CatalogService
from apps.common.enums import Channel
from apps.intake.models import InboundEvent
from apps.intake.responses import InboundEventResponseService
from apps.intake.storefront import get_or_create_website_user_id


@method_decorator(ensure_csrf_cookie, name="dispatch")
class NaturalOrderView(TemplateView):
    template_name = "intake/natural_order_form.html"

    def get_context_data(self, **kwargs):
        get_or_create_website_user_id(self.request)
        context = super().get_context_data(**kwargs)
        products = CatalogService.get_active_products()
        serializer = ProductListSerializer(
            products, many=True, context={"request": self.request}
        )
        catalog = [dict(item) for item in serializer.data]
        context["catalog"] = catalog
        return context


class NaturalOrderStatusView(TemplateView):
    template_name = "intake/natural_order_status.html"

    def _event(self):
        external_user_id = self.request.session.get("website_external_user_id")
        if not external_user_id:
            raise Http404
        return get_object_or_404(
            InboundEvent.objects.select_related("draft__converted_order"),
            public_id=self.kwargs["event_id"],
            channel=Channel.WEBSITE,
            external_user_id=external_user_id,
        )

    def get(self, request, *args, **kwargs):
        event = self._event()
        payload = InboundEventResponseService.present(event)
        if request.GET.get("format") == "json":
            return JsonResponse(payload)
        self.extra_context = {"event_payload": payload}
        return super().get(request, *args, **kwargs)
