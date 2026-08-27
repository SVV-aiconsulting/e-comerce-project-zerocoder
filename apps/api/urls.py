from django.urls import path

from apps.api.views.cart import CartClearView, CartItemView, CartView
from apps.api.views.catalog import ProductDetailView, ProductListView
from apps.api.views.checkout import CheckoutPreviewView
from apps.api.views.health import HealthCheckView
from apps.api.views.identify import IdentifyCustomerView
from apps.api.views.intake import InboundEventDetailView, InboundEventView
from apps.api.views.meta import MetaView
from apps.api.views.orders import CreateOrderView, CustomerOrdersView, OrderDetailView
from apps.api.views.payments import CreatePaymentView, YooKassaWebhookView

urlpatterns = [
    path("health/", HealthCheckView.as_view(), name="health-check"),
    path("meta/", MetaView.as_view(), name="meta"),
    path("products/", ProductListView.as_view(), name="product-list"),
    path("products/<str:public_code>/", ProductDetailView.as_view(), name="product-detail"),
    path("identify-customer/", IdentifyCustomerView.as_view(), name="identify-customer"),
    path("intake/events/", InboundEventView.as_view(), name="intake-event"),
    path(
        "intake/events/<uuid:event_id>/",
        InboundEventDetailView.as_view(),
        name="intake-event-detail",
    ),
    path("cart/", CartView.as_view(), name="cart"),
    path("cart/items/", CartClearView.as_view(), name="cart-clear"),
    path("cart/items/<int:product_id>/", CartItemView.as_view(), name="cart-item"),
    path("checkout/preview/", CheckoutPreviewView.as_view(), name="checkout-preview"),
    path("orders/", CreateOrderView.as_view(), name="order-create"),
    path("orders/<str:public_number>/", OrderDetailView.as_view(), name="order-detail"),
    path(
        "orders/<str:public_number>/payments/",
        CreatePaymentView.as_view(),
        name="order-payment-create",
    ),
    path("webhooks/payments/yookassa/", YooKassaWebhookView.as_view(), name="yookassa-webhook"),
    path(
        "customers/<str:public_code>/orders/",
        CustomerOrdersView.as_view(),
        name="customer-orders",
    ),
]
