"""Конфигурация URL-маршрутов для WebMarket."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.intake.storefront import (
    WebsiteAssistantEventView,
    WebsiteAssistantHistoryView,
    WebsiteAssistantMessageView,
    WebsiteAssistantConversationView,
    WebsiteCartClearView,
    WebsiteCartItemView,
    WebsiteCartView,
    WebsiteCheckoutPreviewView,
    WebsiteCreateOrderView,
)
from apps.intake.web_views import NaturalOrderStatusView, NaturalOrderView
from apps.payments.web_views import PaymentReturnView
from apps.dashboard.views import manager_dashboard
from apps.privacy.models import PersonalDataDocumentType
from apps.privacy.views import WebsiteConsentView, WebsiteWithdrawalView, document_view

urlpatterns = [
    path("", NaturalOrderView.as_view(), name="natural-order"),
    path("privacy-policy/", lambda request: document_view(request, PersonalDataDocumentType.POLICY), name="privacy-policy"),
    path("privacy-policy/v/<str:version>/", lambda request, version: document_view(request, PersonalDataDocumentType.POLICY, version), name="privacy-policy-version"),
    path("personal-data-consent/", lambda request: document_view(request, PersonalDataDocumentType.CONSENT), name="personal-data-consent"),
    path("personal-data-consent/v/<str:version>/", lambda request, version: document_view(request, PersonalDataDocumentType.CONSENT, version), name="personal-data-consent-version"),
    path("personal-data-consent/actions/", WebsiteConsentView.as_view(), name="website-consent"),
    path("personal-data-withdrawal/", WebsiteWithdrawalView.as_view(), name="website-withdrawal"),
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
        "store/assistant/messages/",
        WebsiteAssistantMessageView.as_view(),
        name="website-assistant-message",
    ),
    path(
        "store/assistant/events/<uuid:event_id>/",
        WebsiteAssistantEventView.as_view(),
        name="website-assistant-event",
    ),
    path(
        "store/assistant/history/",
        WebsiteAssistantHistoryView.as_view(),
        name="website-assistant-history",
    ),
    path(
        "store/assistant/conversations/",
        WebsiteAssistantConversationView.as_view(),
        name="website-assistant-conversation",
    ),
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
