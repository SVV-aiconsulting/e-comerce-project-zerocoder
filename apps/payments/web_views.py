from django.http import HttpResponse
from django.views import View


class PaymentReturnView(View):
    """Возврат клиента в магазин; факт оплаты подтверждает только сервер."""

    def get(self, request):
        return HttpResponse(
            "Оплата обрабатывается. Статус заказа обновится после подтверждения платёжным сервисом.",
            content_type="text/plain; charset=utf-8",
        )
