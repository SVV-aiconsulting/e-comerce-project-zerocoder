"""Email-code buyer accounts. A contact match is never an authorization grant."""
import hashlib
import json
import secrets
import uuid
from datetime import timedelta
from django.conf import settings
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.hashers import make_password, check_password
from django.core.mail import EmailMessage, get_connection
from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views import View
from apps.customers.models import (WebAccount, WebSessionBinding, OrderAccessGrant,
    HistoryLinkRequest, LoginCode, AuthGuard, Customer, CustomerIdentityConflict)
from apps.customers.validators import normalize_email, normalize_phone
from apps.orders.models import Order
from apps.orders.access import OrderAccessService


def account_for(request):
    if not request.user.is_authenticated:
        return None
    return WebAccount.objects.filter(user=request.user).first()


def body(request):
    try:
        data = json.loads(request.body or b"{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, UnicodeError):
        return {}


def digest(value):
    import hmac
    return hmac.new(settings.SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()


def send_login_code(email, code):
    if settings.EMAIL_BACKEND != "django.core.mail.backends.smtp.EmailBackend":
        connection = get_connection()
    else:
        connection = get_connection(host=settings.YANDEX_SMTP_HOST,
            port=settings.YANDEX_SMTP_PORT, username=settings.YANDEX_EMAIL_ADDRESS,
            password=settings.YANDEX_EMAIL_APP_PASSWORD, use_ssl=True, timeout=10)
    EmailMessage("Код входа WebMarket", f"Ваш код: {code}. Он действует 10 минут. Не сообщайте его другим.",
        settings.YANDEX_EMAIL_ADDRESS or settings.DEFAULT_FROM_EMAIL, [email], connection=connection).send(fail_silently=False)


@transaction.atomic
def link_history(account):
    orders = Order.objects.filter(customer_email_snapshot__iexact=account.email)
    customers = list(orders.exclude(customer_id=None).values_list("customer_id", flat=True).distinct()[:2])
    for order in orders.iterator():
        conflict = bool(
            order.customer_id
            and CustomerIdentityConflict.objects.filter(
                Q(source_customer_id=order.customer_id)
                | Q(matched_customer_id=order.customer_id),
                contact_type="email",
                contact_value__iexact=account.email,
                status="pending",
            ).exists()
        )
        ambiguous = (
            len(customers) > 1
            or conflict
            or OrderAccessGrant.objects.filter(order=order)
            .exclude(account=account)
            .exists()
        )
        if ambiguous:
            HistoryLinkRequest.objects.get_or_create(account=account, order=order)
        else:
            OrderAccessGrant.objects.get_or_create(order=order,
                defaults={"account": account, "reason": "verified_email_snapshot"})


class CodeRequestView(View):
    def post(self, request):
        payload = body(request)
        identifier = str(payload.get("identifier", "")).strip()[:320]
        purpose = str(payload.get("purpose", "login"))
        changing_account = account_for(request) if purpose == "email_change" else None
        if purpose == "email_change" and changing_account is None:
            return JsonResponse({"error": {"message": "Войдите в кабинет."}}, status=401)
        if purpose not in {"login", "email_change"}:
            return JsonResponse({"error": {"message": "Неизвестная операция."}}, status=400)
        email = ""
        if "@" in identifier:
            try:
                email = normalize_email(identifier)
                from django.core.validators import validate_email
                validate_email(email)
            except ValidationError:
                return JsonResponse({"error": {"message": "Введите корректный email или телефон."}}, status=400)
        elif purpose == "login":
            try:
                phone = normalize_phone(identifier)
            except ValidationError:
                phone = ""
            account = WebAccount.objects.filter(phone_login=phone).first() if phone else None
            email = account.email if account else ""
        if not request.session.session_key:
            request.session.create()
        now = timezone.now()
        target = digest(email or identifier)
        ip = digest(request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR", ""))
        with transaction.atomic():
            for key in sorted(["target:" + target, "ip:" + ip]):
                AuthGuard.objects.get_or_create(key=key)
                AuthGuard.objects.select_for_update().get(key=key)
            recent = LoginCode.objects.filter(target_hash=target, created_at__gte=now-timedelta(hours=1))
            if (recent.count() >= 5 or recent.filter(created_at__gte=now-timedelta(minutes=1)).exists()
                    or LoginCode.objects.filter(ip_hash=ip, created_at__gte=now-timedelta(hours=1)).count() >= 30):
                response = JsonResponse({"error": {"code": "rate_limited", "message": "Слишком много запросов. Попробуйте позже."}}, status=429)
                response["Retry-After"] = "60"
                return response
            LoginCode.objects.filter(session_key=request.session.session_key, target_hash=target, consumed_at=None).update(consumed_at=now)
            code = f"{secrets.randbelow(1000000):06d}"
            challenge = LoginCode.objects.create(session_key=request.session.session_key,
                email=email, target_hash=target, ip_hash=ip, code_hash=make_password(code),
                expires_at=now+timedelta(minutes=10), purpose=purpose,
                account=changing_account)
        if email:
            try:
                send_login_code(email, code)
            except Exception:
                # Never expose SMTP secrets or distinguish account existence.
                pass
            else:
                LoginCode.objects.filter(pk=challenge.pk).update(delivered=True)
        return JsonResponse({"challenge_id": str(challenge.public_id),
            "message": "Если адрес доступен, код отправлен. Если письмо не пришло, можно продолжить заказ без входа."}, status=202)


class CodeVerifyView(View):
    def post(self, request):
        payload = body(request)
        try:
            challenge_id = uuid.UUID(str(payload.get("challenge_id", "")))
        except ValueError:
            challenge_id = None
        code = str(payload.get("code", ""))
        account = None
        with transaction.atomic():
            row = LoginCode.objects.select_for_update().filter(public_id=challenge_id,
                session_key=request.session.session_key).first()
            if row and row.delivered and not row.consumed_at and row.expires_at > timezone.now() and row.attempts < 5:
                row.attempts += 1
                if len(code) == 6 and code.isascii() and code.isdigit() and check_password(code, row.code_hash):
                    row.consumed_at = timezone.now()
                    AuthGuard.objects.get_or_create(key="account:" + digest(row.email))
                    AuthGuard.objects.select_for_update().get(key="account:" + digest(row.email))
                    if row.purpose == "email_change":
                        current = account_for(request)
                        if current and row.account_id == current.pk:
                            account = WebAccount.objects.select_for_update().get(pk=current.pk)
                            if WebAccount.objects.filter(email=row.email).exclude(pk=account.pk).exists():
                                account = None
                            else:
                                account.email = row.email
                                account.verified_at = timezone.now()
                                account.save(update_fields=["email", "verified_at"])
                                account.user.email = row.email
                                account.user.save(update_fields=["email"])
                    else:
                        account = WebAccount.objects.filter(email=row.email).first()
                        if account is None:
                            user = get_user_model().objects.create_user(
                                username="buyer_" + uuid.uuid4().hex,
                                email=row.email,
                                password=None,
                            )
                            account = WebAccount.objects.create(
                                user=user,
                                email=row.email,
                                verified_at=timezone.now(),
                            )
                        elif account.verified_at is None:
                            account.verified_at = timezone.now()
                            account.save(update_fields=["verified_at"])
                row.save(update_fields=["attempts", "consumed_at"])
        if account is None:
            return JsonResponse({"error": {"message": "Код неверен или истёк. Запросите новый."}}, status=400)
        if row.purpose == "email_change":
            link_history(account)
            return JsonResponse({"authenticated": True, "email_changed": True})

        from apps.intake.storefront import get_or_create_website_user_id
        guest_id = get_or_create_website_user_id(request)
        previous = account_for(request)
        if previous and previous.pk != account.pk:
            WebSessionBinding.objects.filter(website_user_id=guest_id).update(
                revoked_at=timezone.now()
            )
            guest_id = "web:" + str(uuid.uuid4())
        login(request, account.user, backend="django.contrib.auth.backends.ModelBackend")
        request.session["website_external_user_id"] = guest_id
        # A separate per-browser binding allows revocation on logout.
        WebSessionBinding.objects.update_or_create(
            website_user_id=guest_id,
            defaults={"account": account, "revoked_at": None},
        )
        from apps.carts.models import Cart
        saved = Cart.objects.filter(channel="website", external_user_id=account.basket_user_id, status="active").first() if account.basket_user_id else None
        guest = Cart.objects.filter(channel="website", external_user_id=guest_id, status="active").first()
        conflict = bool(saved and saved.external_user_id != guest_id and saved.items.exists() and guest and guest.items.exists())
        if saved and saved.external_user_id != guest_id and saved.items.exists():
            request.session["account_saved_cart"] = saved.pk
            request.session["account_saved_cart_owner"] = saved.external_user_id
            if not conflict:
                self.choose_cart(request, account, "account")
        if not conflict:
            account.basket_user_id = guest_id
            account.save(update_fields=["basket_user_id"])
        link_history(account)
        return JsonResponse({"authenticated": True, "cart_choice_required": conflict})

    @staticmethod
    @transaction.atomic
    def choose_cart(request, account, choice):
        from apps.carts.models import Cart
        from apps.carts.services import CartService
        from apps.intake.storefront import get_or_create_website_user_id
        saved = Cart.objects.select_for_update().filter(pk=request.session.get("account_saved_cart"),
            external_user_id=request.session.get("account_saved_cart_owner"), channel="website", status="active").first()
        guest = CartService.get_or_create_active_cart(channel="website", external_user_id=get_or_create_website_user_id(request))
        if choice == "account" and saved:
            CartService.clear(guest)
            for item in saved.items.select_related("product"):
                CartService.set_item_quantity(guest, item.product, item.quantity)
            for field in Cart.CHECKOUT_FIELDS:
                setattr(guest, field, getattr(saved, field))
            guest.save(update_fields=[*Cart.CHECKOUT_FIELDS, "updated_at"])
        account.basket_user_id = guest.external_user_id
        account.save(update_fields=["basket_user_id"])
        request.session.pop("account_saved_cart", None)
        request.session.pop("account_saved_cart_owner", None)


class AccountView(View):
    def get(self, request):
        account = account_for(request)
        if not account:
            return JsonResponse({"authenticated": False})
        return JsonResponse({"authenticated": True, "email": account.email,
            "name": account.name, "phone_login": account.phone_login or "",
            "history_pending": HistoryLinkRequest.objects.filter(account=account, status="pending").count(),
            "cart_choice_required": bool(request.session.get("account_saved_cart"))})

    def patch(self, request):
        account = account_for(request)
        if not account:
            return JsonResponse({"error": {"message": "Войдите в кабинет."}}, status=401)
        payload = body(request)
        try:
            if "phone_login" in payload:
                account.phone_login = normalize_phone(str(payload["phone_login"])) if payload["phone_login"] else None
            if "name" in payload:
                account.name = str(payload["name"]).strip()[:255]
            with transaction.atomic():
                account.save(update_fields=["phone_login", "name"])
        except (ValidationError, IntegrityError):
            return JsonResponse({"error": {"message": "Этот телефон недоступен как логин или введён неверно."}}, status=400)
        if payload.get("cart_choice") in ("guest", "account"):
            CodeVerifyView.choose_cart(request, account, payload["cart_choice"])
        return self.get(request)


class LogoutView(View):
    def post(self, request):
        identity = request.session.get("website_external_user_id")
        if identity:
            WebSessionBinding.objects.filter(website_user_id=identity).update(
                revoked_at=timezone.now()
            )
        logout(request)
        request.session["website_external_user_id"] = "web:" + str(uuid.uuid4())
        request.session.pop("website_customer_id", None)
        request.session.pop("website_assistant_identity_conversation_id", None)
        request.session.pop("website_assistant_pending_identity", None)
        request.session.modified = True
        return JsonResponse({"authenticated": False})


class AccountOrdersView(View):
    def get(self, request, number=None):
        from apps.intake.storefront import get_or_create_website_user_id
        from apps.api.serializers.orders import OrderSerializer, OrderListSerializer
        orders = OrderAccessService.visible(channel="website", external_user_id=get_or_create_website_user_id(request)).order_by("-created_at")
        if number:
            order = orders.filter(public_number=number).first()
            if order is None:
                return JsonResponse({"error": {"message": "Заказ не найден."}}, status=404)
            return JsonResponse(OrderSerializer(order).data)
        return JsonResponse({"orders": OrderListSerializer(orders[:100], many=True).data})


def account_page(request):
    return render(request, "customers/account.html")
