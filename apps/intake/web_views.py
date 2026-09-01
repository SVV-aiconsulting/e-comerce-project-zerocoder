"""Browser-safe веб-канал без публикации adapter token в JavaScript."""
import uuid

from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import FormView, TemplateView

from apps.api.serializers.catalog import ProductListSerializer
from apps.catalog.services import CatalogService
from apps.common.enums import Channel
from apps.customers.models import Customer
from apps.customers.services import CustomerService
from apps.intake.forms import NaturalOrderForm
from apps.intake.models import InboundEvent
from apps.intake.responses import InboundEventResponseService
from apps.intake.services import InboundEventService
from apps.intake.storefront import SESSION_CUSTOMER_KEY, get_or_create_website_user_id


@method_decorator(ensure_csrf_cookie, name="dispatch")
class NaturalOrderView(FormView):
    template_name = "intake/natural_order_form.html"
    form_class = NaturalOrderForm

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

    def get_initial(self):
        initial = super().get_initial()
        initial["submission_id"] = uuid.uuid4()
        customer_id = self.request.session.get(SESSION_CUSTOMER_KEY)
        customer = Customer.objects.filter(pk=customer_id).first()
        if customer is not None:
            initial.update(name=customer.name, phone=customer.phone, email=customer.email)
        return initial

    def form_valid(self, form):
        external_user_id = get_or_create_website_user_id(self.request)
        identity = CustomerService.resolve_website_customer(
            external_user_id=external_user_id,
            phone=form.cleaned_data["phone"],
            email=form.cleaned_data["email"],
            name=form.cleaned_data["name"],
        )

        customer = identity.customer
        if customer is None:
            form.add_error("phone", "Не удалось идентифицировать клиента.")
            return self.form_invalid(form)
        if not customer.personal_data_consent:
            customer.personal_data_consent = True
            customer.save(update_fields=["personal_data_consent", "updated_at"])

        registration = InboundEventService.register(
            channel=Channel.WEBSITE,
            external_event_id=str(form.cleaned_data["submission_id"]),
            external_user_id=external_user_id,
            conversation_key=external_user_id,
            customer=customer,
            raw_text=form.cleaned_data["message"],
            raw_payload={
                "source": "website_natural_order_form",
                "contact_phone": form.cleaned_data["phone"],
                "contact_email": form.cleaned_data["email"],
            },
        )
        InboundEventService.enqueue(registration.event)
        self.request.session[SESSION_CUSTOMER_KEY] = customer.pk
        return redirect(
            reverse("natural-order-status", kwargs={"event_id": registration.event.public_id})
        )


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
