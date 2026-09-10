"""Бизнес-логика клиентов."""

from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.common.enums import CartStatus, Channel, CustomerSource, CustomerStatus
from apps.common.exceptions import ChannelIdentityAlreadyLinkedError
from apps.common.utils import generate_public_code
from apps.customers.models import (
    ContactType,
    Customer,
    CustomerChannelIdentity,
    CustomerIdentityConflict,
    IdentityConflictStatus,
)
from apps.customers.validators import normalize_email, normalize_phone, validate_phone


@dataclass
class CustomerIdentificationResult:
    """Результат идентификации клиента при входе из канала."""

    customer: Customer | None
    status: str
    is_new_customer: bool = False
    channel_linked: bool = False
    registration_required: bool = False
    conflicts_created: int = 0


class CustomerService:
    """Сервис управления клиентами."""

    @staticmethod
    @transaction.atomic
    def anonymize_orders_and_delete(*, customer: Customer) -> None:
        """Удаляет карточку CRM, сохраняя неидентифицирующую историю заказов.

        Операцию намеренно вызывает только сотрудник через административный
        интерфейс. Отзыв согласия сам по себе лишь ограничивает новые операции:
        основания хранения уже оформленного заказа проверяет менеджер.
        """
        from apps.orders.models import Order
        from apps.carts.models import CheckoutPreview
        from apps.privacy.models import ConsentMethod, IdentityType, PersonalDataConsentEvent
        from apps.privacy.services import ConsentService

        # A platform account can keep talking to a bot after its CRM card has
        # been erased. The consent registry is immutable, therefore we append
        # a withdrawal event for every currently linked channel identity
        # instead of deleting its audit trail. A subsequent /start then asks
        # for a new explicit consent before registration or checkout.
        identity_types = {
            Channel.TELEGRAM: IdentityType.TELEGRAM_USER_ID,
            Channel.VK: IdentityType.VK_USER_ID,
            Channel.MAX: IdentityType.MAX_USER_ID,
            Channel.WEBSITE: IdentityType.WEBSITE_SESSION_ID,
        }
        consent_identities = {
            (identity.channel, identity.external_user_id): identity_types.get(identity.channel)
            for identity in customer.channel_identities.all()
        }
        # A website customer is usually connected to a consent event through
        # the browser session, not a CustomerChannelIdentity. Include every
        # consent identity already associated with the card as well.
        for event in PersonalDataConsentEvent.objects.filter(customer=customer):
            consent_identities.setdefault(
                (event.channel, event.identity_value), event.identity_type
            )

        for (channel, identity_value), identity_type in consent_identities.items():
            if identity_type and ConsentService.has_current_consent(
                channel=channel,
                identity_value=identity_value,
            ):
                ConsentService.withdraw(
                    channel=channel,
                    identity_type=identity_type,
                    identity_value=identity_value,
                    source="customer_card_erased",
                    expression_method=ConsentMethod.BOT_COMMAND,
                    customer=None,
                )

        # A channel ID remains stable after a CRM card is erased. Active carts
        # and assistant drafts are keyed by that ID, so merely nulling their
        # customer FK would let the next registration resume an old address,
        # payment method or cart. Abandon these transient records and erase
        # their content before deleting the customer.
        from apps.carts.models import Cart, CheckoutPreview
        from apps.carts.services import CartService
        from apps.intake.enums import ACTIVE_DRAFT_STATUSES, OrderDraftStatus
        from apps.intake.models import AssistantMessage, ConversationMemory, InboundEvent, OrderDraft

        identity_scope = Q()
        for channel, identity_value in consent_identities:
            identity_scope |= Q(channel=channel, external_user_id=identity_value)

        carts = list(
            Cart.objects.select_for_update()
            .filter(Q(customer=customer) | identity_scope, status=CartStatus.ACTIVE)
        )
        for cart in carts:
            CartService.clear(cart)
            Cart.objects.filter(pk=cart.pk).update(
                customer=None,
                status=CartStatus.ABANDONED,
            )

        active_drafts = OrderDraft.objects.select_for_update().filter(
            Q(customer=customer) | identity_scope,
            status__in=ACTIVE_DRAFT_STATUSES,
        )
        for draft in active_drafts:
            draft.items.all().delete()
        active_drafts.update(
            customer=None,
            cart=None,
            checkout_preview=None,
            status=OrderDraftStatus.CANCELLED,
            receiving_type="",
            delivery_address="",
            payment_method="",
            contact_phone="",
            contact_email="",
            customer_comment="",
            desired_date=None,
            desired_time_interval="",
            missing_fields=[],
            manager_attention_required=False,
            escalation_reason="",
            updated_at=timezone.now(),
        )
        # A preview without an order is only a short-lived checkout offer; it
        # must not retain erased address/contact snapshots.
        OrderDraft.objects.filter(
            checkout_preview__cart__in=carts,
            checkout_preview__order__isnull=True,
        ).update(checkout_preview=None, updated_at=timezone.now())
        CheckoutPreview.objects.filter(cart__in=carts, order__isnull=True).delete()
        ConversationMemory.objects.filter(identity_scope).delete()
        AssistantMessage.objects.filter(
            event__in=InboundEvent.objects.filter(identity_scope)
        ).delete()

        # Account bindings are identifiers of browser sessions. Mark them
        # revoked as part of erasure so a previously authenticated browser
        # cannot continue under the removed customer context.
        from apps.customers.models import WebSessionBinding
        WebSessionBinding.objects.filter(account__customer=customer).update(
            revoked_at=timezone.now()
        )

        Order.objects.filter(customer=customer).update(
            customer=None,
            customer_deleted=True,
            customer_code_snapshot="",
            customer_name_snapshot="Клиент удалён",
            customer_phone_snapshot="",
            customer_email_snapshot="",
            source_external_user_id_snapshot="",
            delivery_address="",
            customer_comment="",
        )
        CustomerIdentityConflict.objects.filter(
            Q(source_customer=customer) | Q(matched_customer=customer)
        ).delete()
        # A preview is a technical, short-lived checkout snapshot. It can
        # refer to a customer even after its resulting order has been
        # anonymized, so clear that protected link before removing the card.
        CheckoutPreview.objects.filter(customer=customer).update(customer=None)
        customer.delete()

    @staticmethod
    def find_by_channel_identity(channel: str, external_user_id: str) -> Customer | None:
        identity = CustomerChannelIdentity.objects.filter(
            channel=channel,
            external_user_id=external_user_id,
        ).select_related("customer").first()
        return identity.customer if identity else None

    @staticmethod
    @transaction.atomic
    def create_customer(
        *,
        name: str,
        phone: str = "",
        email: str = "",
        first_source: str,
        channel: str | None = None,
        external_user_id: str | None = None,
        username: str = "",
        phone_verified: bool = False,
        email_verified: bool = False,
    ) -> Customer:
        phone = normalize_phone(phone) if phone else ""
        email = normalize_email(email) if email else ""
        if not phone and not email:
            raise ValidationError("У клиента должен быть указан телефон или email")
        if phone:
            validate_phone(phone)
        public_code = generate_public_code(
            lambda code: Customer.objects.filter(public_code=code).exists()
        )
        customer = Customer.objects.create(
            public_code=public_code,
            name=name,
            phone=phone,
            email=email,
            first_source=first_source,
            status=CustomerStatus.NEW,
            phone_verified_at=timezone.now() if phone_verified else None,
            email_verified_at=timezone.now() if email_verified else None,
        )
        if channel and external_user_id:
            CustomerService.link_channel(
                customer=customer,
                channel=channel,
                external_user_id=external_user_id,
                username=username,
            )
        return customer

    @staticmethod
    def record_contact_conflicts(
        *,
        customer: Customer,
        channel: str,
        external_user_id: str = "",
        phone: str = "",
        email: str = "",
    ) -> int:
        """Зафиксировать совпадения контактов, не блокируя клиента и заказ."""
        contacts = []
        if phone:
            normalized_phone = normalize_phone(phone)
            contacts.append(
                (ContactType.PHONE, normalized_phone, Q(phone=normalized_phone))
            )
        if email:
            normalized_email = normalize_email(email)
            contacts.append(
                (ContactType.EMAIL, normalized_email, Q(email=normalized_email))
            )

        created_count = 0
        for contact_type, contact_value, lookup in contacts:
            matched_customers = Customer.objects.filter(lookup).exclude(pk=customer.pk)
            for matched_customer in matched_customers:
                _, created = CustomerIdentityConflict.objects.get_or_create(
                    source_customer=customer,
                    matched_customer=matched_customer,
                    contact_type=contact_type,
                    contact_value=contact_value,
                    source_channel=channel,
                    defaults={
                        "source_external_user_id": external_user_id,
                        "status": IdentityConflictStatus.PENDING,
                    },
                )
                created_count += int(created)
        return created_count

    @staticmethod
    def update_customer_contacts(
        *,
        customer: Customer,
        channel: str,
        external_user_id: str = "",
        phone: str = "",
        email: str = "",
        phone_verified: bool = False,
        email_verified: bool = False,
    ) -> int:
        """Дополнить пустые основные контакты и записать неблокирующие конфликты."""
        normalized_phone = normalize_phone(phone) if phone else ""
        normalized_email = normalize_email(email) if email else ""
        update_fields = []
        if normalized_phone and not customer.phone:
            customer.phone = normalized_phone
            update_fields.append("phone")
        if normalized_email and not customer.email:
            customer.email = normalized_email
            update_fields.append("email")
        if (
            phone_verified
            and normalized_phone == customer.phone
            and not customer.phone_verified_at
        ):
            customer.phone_verified_at = timezone.now()
            update_fields.append("phone_verified_at")
        if (
            email_verified
            and normalized_email == customer.email
            and not customer.email_verified_at
        ):
            customer.email_verified_at = timezone.now()
            update_fields.append("email_verified_at")
        if update_fields:
            customer.save(update_fields=[*update_fields, "updated_at"])
        return CustomerService.record_contact_conflicts(
            customer=customer,
            channel=channel,
            external_user_id=external_user_id,
            phone=normalized_phone,
            email=normalized_email,
        )

    @staticmethod
    def link_channel(
        *,
        customer: Customer,
        channel: str,
        external_user_id: str,
        username: str = "",
    ) -> CustomerChannelIdentity:
        identity = CustomerChannelIdentity.objects.filter(
            channel=channel,
            external_user_id=external_user_id,
        ).first()
        if identity:
            if identity.customer_id != customer.pk:
                raise ChannelIdentityAlreadyLinkedError(
                    f"Идентификатор {channel}:{external_user_id} уже привязан к другому клиенту"
                )
            if identity.username != username:
                identity.username = username
                identity.save(update_fields=["username", "updated_at"])
            return identity

        try:
            # Отдельный savepoint, чтобы локально обработать IntegrityError
            # даже при вызове внутри внешнего transaction.atomic().
            with transaction.atomic():
                return CustomerChannelIdentity.objects.create(
                    customer=customer,
                    channel=channel,
                    external_user_id=external_user_id,
                    username=username,
                )
        except IntegrityError:
            identity = CustomerChannelIdentity.objects.filter(
                channel=channel,
                external_user_id=external_user_id,
            ).first()
            if identity and identity.customer_id == customer.pk:
                return identity
            raise ChannelIdentityAlreadyLinkedError(
                f"Идентификатор {channel}:{external_user_id} уже привязан к другому клиенту"
            )

    @staticmethod
    @transaction.atomic
    def resolve_or_register_customer(
        *,
        channel: str,
        external_user_id: str,
        phone: str | None = None,
        email: str | None = None,
        username: str = "",
        name: str = "",
        phone_verified: bool = False,
        email_verified: bool = False,
    ) -> CustomerIdentificationResult:
        """Channel-first идентификация для ботов и других ID-каналов.

        Контакты не перепривязывают новый channel ID к чужой карточке. Совпадения
        сохраняются как конфликты и не мешают оформлению заказа.
        """
        customer = CustomerService.find_by_channel_identity(channel, external_user_id)
        if customer:
            if username:
                CustomerService.link_channel(
                    customer=customer,
                    channel=channel,
                    external_user_id=external_user_id,
                    username=username,
                )
            conflicts = CustomerService.update_customer_contacts(
                customer=customer,
                channel=channel,
                external_user_id=external_user_id,
                phone=phone or "",
                email=email or "",
                phone_verified=phone_verified,
                email_verified=email_verified,
            )
            return CustomerIdentificationResult(
                customer=customer,
                status="identified",
                is_new_customer=False,
                channel_linked=False,
                conflicts_created=conflicts,
            )

        if not phone and not email:
            return CustomerIdentificationResult(
                customer=None,
                status="registration_required",
                registration_required=True,
            )

        normalized_phone = normalize_phone(phone) if phone else ""
        normalized_email = normalize_email(email) if email else ""
        customer_name = name.strip() or "Покупатель"
        first_source = CustomerService.resolve_source_from_channel(channel)
        try:
            customer = CustomerService.create_customer(
                name=customer_name,
                phone=normalized_phone,
                email=normalized_email,
                first_source=first_source,
                channel=channel,
                external_user_id=external_user_id,
                username=username,
                phone_verified=phone_verified,
                email_verified=email_verified,
            )
            conflicts = CustomerService.record_contact_conflicts(
                customer=customer,
                channel=channel,
                external_user_id=external_user_id,
                phone=normalized_phone,
                email=normalized_email,
            )
            return CustomerIdentificationResult(
                customer=customer,
                status="identified",
                is_new_customer=True,
                channel_linked=True,
                conflicts_created=conflicts,
            )
        except (IntegrityError, ChannelIdentityAlreadyLinkedError):
            pass

        # Fallback: конкурентный запрос уже мог создать/привязать клиента.
        customer = CustomerService.find_by_channel_identity(channel, external_user_id)
        if not customer:
            raise ChannelIdentityAlreadyLinkedError(
                "Не удалось завершить привязку из-за конкурентного обновления"
            )
        return CustomerIdentificationResult(
            customer=customer,
            status="identified",
            is_new_customer=False,
            channel_linked=True,
        )

    @staticmethod
    @transaction.atomic
    def resolve_website_customer(
        *,
        name: str,
        phone: str = "",
        email: str = "",
        external_user_id: str = "",
    ) -> CustomerIdentificationResult:
        """Найти web-клиента по контактам; UUID формы не является CRM identity."""
        normalized_phone = normalize_phone(phone) if phone else ""
        normalized_email = normalize_email(email) if email else ""
        if not normalized_phone and not normalized_email:
            raise ValidationError("Укажите телефон или email")

        contact_filter = Q()
        if normalized_phone:
            contact_filter |= Q(phone=normalized_phone)
        if normalized_email:
            contact_filter |= Q(email=normalized_email)
        candidates = Customer.objects.filter(contact_filter).distinct()

        exact = Customer.objects.none()
        if normalized_phone and normalized_email:
            exact = candidates.filter(phone=normalized_phone, email=normalized_email)
        exact_ids = list(exact.values_list("pk", flat=True)[:2])
        candidate_ids = list(candidates.values_list("pk", flat=True)[:2])

        customer = None
        if len(exact_ids) == 1:
            customer = Customer.objects.get(pk=exact_ids[0])
        elif not exact_ids and len(candidate_ids) == 1:
            customer = Customer.objects.get(pk=candidate_ids[0])

        if customer is None:
            customer = CustomerService.create_customer(
                name=name.strip() or "Покупатель",
                phone=normalized_phone,
                email=normalized_email,
                first_source=CustomerSource.WEBSITE,
            )
            conflicts = CustomerService.record_contact_conflicts(
                customer=customer,
                channel=Channel.WEBSITE,
                external_user_id=external_user_id,
                phone=normalized_phone,
                email=normalized_email,
            )
            return CustomerIdentificationResult(
                customer=customer,
                status="identified",
                is_new_customer=True,
                conflicts_created=conflicts,
            )

        # Unverified guest input belongs to the order, never overwrites a CRM identity.
        conflicts = CustomerService.record_contact_conflicts(customer=customer,
            channel=Channel.WEBSITE, external_user_id=external_user_id,
            phone=normalized_phone, email=normalized_email)
        return CustomerIdentificationResult(
            customer=customer,
            status="identified",
            is_new_customer=False,
            conflicts_created=conflicts,
        )

    @staticmethod
    @transaction.atomic
    def resolve_email_customer(
        *,
        external_user_id: str,
        email: str,
        name: str = "",
        phone: str = "",
    ) -> CustomerIdentificationResult:
        """Идентифицировать отправителя по email и не блокировать совпадение телефона."""
        normalized_email = normalize_email(email)
        normalized_phone = normalize_phone(phone) if phone else ""
        customer = CustomerService.find_by_channel_identity(Channel.EMAIL, external_user_id)
        if customer is not None:
            conflicts = CustomerService.update_customer_contacts(
                customer=customer,
                channel=Channel.EMAIL,
                external_user_id=external_user_id,
                phone=normalized_phone,
                email=normalized_email,
            )
            return CustomerIdentificationResult(
                customer=customer,
                status="identified",
                conflicts_created=conflicts,
            )

        email_candidates = list(Customer.objects.filter(email=normalized_email)[:2])
        if len(email_candidates) == 1:
            customer = email_candidates[0]
            CustomerService.link_channel(
                customer=customer,
                channel=Channel.EMAIL,
                external_user_id=external_user_id,
                username=normalized_email,
            )
            conflicts = CustomerService.update_customer_contacts(
                customer=customer,
                channel=Channel.EMAIL,
                external_user_id=external_user_id,
                phone=normalized_phone,
                email=normalized_email,
            )
            return CustomerIdentificationResult(
                customer=customer,
                status="identified",
                channel_linked=True,
                conflicts_created=conflicts,
            )

        try:
            customer = CustomerService.create_customer(
                name=name.strip() or normalized_email.split("@", 1)[0],
                phone=normalized_phone,
                email=normalized_email,
                first_source=CustomerSource.EMAIL,
                channel=Channel.EMAIL,
                external_user_id=external_user_id,
                username=normalized_email,
            )
        except ChannelIdentityAlreadyLinkedError:
            customer = CustomerService.find_by_channel_identity(
                Channel.EMAIL,
                external_user_id,
            )
            if customer is None:
                raise
        conflicts = CustomerService.record_contact_conflicts(
            customer=customer,
            channel=Channel.EMAIL,
            external_user_id=external_user_id,
            phone=normalized_phone,
            email=normalized_email,
        )
        return CustomerIdentificationResult(
            customer=customer,
            status="identified",
            is_new_customer=True,
            channel_linked=True,
            conflicts_created=conflicts,
        )

    @staticmethod
    def update_stats_after_order(customer: Customer, order_total: Decimal) -> None:
        now = timezone.now()
        update_fields = {
            "orders_count": F("orders_count") + 1,
            "total_orders_sum": F("total_orders_sum") + order_total,
            "last_order_at": now,
        }
        if customer.orders_count == 0:
            update_fields["first_order_at"] = now
        if customer.status == CustomerStatus.NEW:
            update_fields["status"] = CustomerStatus.ACTIVE

        Customer.objects.filter(pk=customer.pk).update(**update_fields)
        customer.refresh_from_db()

    @staticmethod
    def resolve_source_from_channel(channel: str) -> str:
        """Сопоставить канал корзины с источником клиента/заказа, где применимо."""
        mapping = {
            Channel.TELEGRAM: CustomerSource.TELEGRAM,
            Channel.VK: CustomerSource.VK,
            Channel.MAX: CustomerSource.MAX,
            Channel.WEBSITE: CustomerSource.WEBSITE,
            Channel.EMAIL: CustomerSource.EMAIL,
        }
        return mapping.get(channel, CustomerSource.WEBSITE)
