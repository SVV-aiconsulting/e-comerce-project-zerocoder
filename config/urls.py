"""Конфигурация URL-маршрутов для WebMarket."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.intake.storefront import (
    WebsiteCartClearView,
    WebsiteCartItemView,
    WebsiteCartView,
    WebsiteCheckoutPreviewView,
    WebsiteCreateOrderView,
)
from apps.intake.web_views import NaturalOrderStatusView, NaturalOrderView
from apps.payments.web_views import PaymentReturnView
from apps.dashboard.views import manager_dashboard

urlpatterns = [
    path("", NaturalOrderView.as_view(), name="natural-order"),
    path("store/cart/", WebsiteCartView.as_view(), name="website-cart"),
    path(
        "store/cart/items/<int:product_id>/",
        WebsiteCartItemView.as_view(),
        name="website-cart-item",
    ),
    path("store/cart/items/", WebsiteCartClearView.as_view(), name="website-cart-clear"),
    path(
        "store/checkout/preview/",
        WebsiteCheckoutPreviewView.as_view(),
        name="website-checkout-preview",
    ),
    path("store/orders/", WebsiteCreateOrderView.as_view(), name="website-order-create"),
    path(
        "order-assistant/<uuid:event_id>/",
        NaturalOrderStatusView.as_view(),
        name="natural-order-status",
    ),
    path("payment/return/", PaymentReturnView.as_view(), name="payment-return"),
    path("manager/dashboard/", manager_dashboard, name="manager-dashboard"),
    path("admin/", admin.site.urls),
    path("api/", include("apps.api.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
