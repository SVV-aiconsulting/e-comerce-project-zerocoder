from django.shortcuts import render
from django.views import View
from apps.orders.access import OrderAccessService

class PaymentReturnView(View):
    def get(self, request):
        identity = request.session.get("website_external_user_id", "")
        orders = OrderAccessService.visible(channel="website", external_user_id=identity) if identity else None
        number = request.GET.get("order")
        order = orders.filter(public_number=number).first() if orders is not None and number else (orders.order_by("-created_at").first() if orders is not None else None)
        return render(request, "payments/return.html", {"order":order})
