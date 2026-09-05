import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views import View

from apps.common.enums import Channel
from apps.privacy.models import ConsentMethod, ConsentStatus, IdentityType, PersonalDataDocumentType, PersonalDataDocumentVersion
from apps.privacy.services import ConsentService


def document_view(request, document_type, version=None):
    documents = ConsentService.current_documents()
    current = documents.policy if document_type == PersonalDataDocumentType.POLICY else documents.consent
    document = current if version is None else get_object_or_404(PersonalDataDocumentVersion, document_type=document_type, version=version)
    return render(request, "privacy/document.html", {"document": document})


class WebsiteConsentView(View):
    def post(self, request):
        try:
            payload = request.POST if request.POST else json.loads(request.body or b"{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return JsonResponse({"error": {"message": "Некорректный формат запроса."}}, status=400)
        accepted = bool(payload.get("accepted"))
        identity = request.session.get("website_external_user_id")
        if not identity:
            return JsonResponse({"error": {"message": "Сессия сайта не найдена."}}, status=400)
        event = ConsentService.record(
            channel=Channel.WEBSITE,
            identity_type=IdentityType.WEBSITE_SESSION_ID,
            identity_value=identity,
            source="website_consent_endpoint",
            status=ConsentStatus.GRANTED if accepted else ConsentStatus.DECLINED,
            expression_method=ConsentMethod.WEBSITE_CHECKBOX,
            evidence={"csrf_protected": True},
        )
        return JsonResponse({"status": event.status, "event_id": str(event.public_id)})


class WebsiteWithdrawalView(View):
    def get(self, request):
        return render(request, "privacy/withdrawal.html")

    def post(self, request):
        identity = request.session.get("website_external_user_id")
        if not identity:
            return JsonResponse({"error": {"message": "Сессия сайта не найдена."}}, status=400)
        ConsentService.withdraw(
            channel=Channel.WEBSITE,
            identity_type=IdentityType.WEBSITE_SESSION_ID,
            identity_value=identity,
            source="website_withdrawal_form",
            expression_method=ConsentMethod.WEBSITE_WITHDRAWAL,
        )
        return render(request, "privacy/withdrawal.html", {"withdrawn": True})
