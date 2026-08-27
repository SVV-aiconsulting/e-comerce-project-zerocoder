import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.common.enums import Channel, CustomerSource
from apps.common.exceptions import ChannelIdentityAlreadyLinkedError
from apps.customers.models import (
    Customer,
    CustomerChannelIdentity,
    CustomerIdentityConflict,
)
from apps.customers.services import CustomerService
from apps.customers.validators import PHONE_ERROR_MESSAGE


@pytest.mark.django_db
def test_create_customer():
    customer = CustomerService.create_customer(
        name="Мария",
        phone="79123456780",
        first_source=CustomerSource.WEBSITE,
    )
    assert customer.public_code
    assert customer.phone == "79123456780"
    assert customer.status == "new"
    assert customer.phone_verified_at is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "phone",
    [
        "abcdefg",
        "91234567890",
        "912345678",
        "9123456789012",
    ],
)
def test_create_customer_invalid_phone(phone):
    with pytest.raises(ValidationError) as exc_info:
        CustomerService.create_customer(
            name="Мария",
            phone=phone,
            first_source=CustomerSource.WEBSITE,
        )
    assert PHONE_ERROR_MESSAGE in str(exc_info.value)


@pytest.mark.django_db
def test_create_customer_duplicate_phone_is_allowed(customer):
    duplicate = CustomerService.create_customer(
        name="Другой клиент",
        phone=customer.phone,
        first_source=CustomerSource.WEBSITE,
    )

    assert duplicate.pk != customer.pk
    assert Customer.objects.filter(phone=customer.phone).count() == 2


@pytest.mark.django_db
def test_create_customer_channel_identity(db):
    customer = CustomerService.create_customer(
        name="Мария",
        phone="79123456784",
        first_source=CustomerSource.TELEGRAM,
    )
    identity = CustomerService.link_channel(
        customer=customer,
        channel=Channel.TELEGRAM,
        external_user_id="999",
        username="maria",
    )
    assert identity.customer == customer
    assert identity.channel == Channel.TELEGRAM


@pytest.mark.django_db
def test_link_channel_creates_new_identity(customer):
    identity = CustomerService.link_channel(
        customer=customer,
        channel=Channel.VK,
        external_user_id="vk-user-new",
        username="ivan",
    )
    assert identity.customer == customer
    assert CustomerChannelIdentity.objects.filter(
        channel=Channel.VK,
        external_user_id="vk-user-new",
    ).count() == 1


@pytest.mark.django_db
def test_link_channel_same_customer_is_idempotent(customer):
    first = CustomerService.link_channel(
        customer=customer,
        channel=Channel.VK,
        external_user_id="vk-user-1",
        username="old_name",
    )
    second = CustomerService.link_channel(
        customer=customer,
        channel=Channel.VK,
        external_user_id="vk-user-1",
        username="new_name",
    )
    assert second.pk == first.pk
    assert CustomerChannelIdentity.objects.filter(
        channel=Channel.VK,
        external_user_id="vk-user-1",
    ).count() == 1
    second.refresh_from_db()
    assert second.username == "new_name"


@pytest.mark.django_db
def test_link_channel_other_customer_raises(customer):
    CustomerService.link_channel(
        customer=customer,
        channel=Channel.VK,
        external_user_id="vk-user-1",
    )
    other = CustomerService.create_customer(
        name="Другой клиент",
        phone="79123456781",
        first_source=CustomerSource.VK,
    )
    with pytest.raises(ChannelIdentityAlreadyLinkedError):
        CustomerService.link_channel(
            customer=other,
            channel=Channel.VK,
            external_user_id="vk-user-1",
        )


@pytest.mark.django_db
def test_create_customer_rolls_back_on_channel_conflict(db):
    customer = CustomerService.create_customer(
        name="Базовый клиент",
        phone="79123456785",
        first_source=CustomerSource.TELEGRAM,
    )
    CustomerService.link_channel(
        customer=customer,
        channel=Channel.TELEGRAM,
        external_user_id="tg-1",
    )

    with pytest.raises(ChannelIdentityAlreadyLinkedError):
        CustomerService.create_customer(
            name="Новый клиент",
            phone="79123456782",
            first_source=CustomerSource.TELEGRAM,
            channel=Channel.TELEGRAM,
            external_user_id="tg-1",
        )

    assert not Customer.objects.filter(phone="79123456782").exists()


@pytest.mark.django_db
def test_resolve_customer_registration_required_without_phone():
    result = CustomerService.resolve_or_register_customer(
        channel=Channel.TELEGRAM,
        external_user_id="tg-42",
    )
    assert result.status == "registration_required"
    assert result.customer is None
    assert result.registration_required is True


@pytest.mark.django_db
def test_resolve_customer_creates_new_with_phone():
    result = CustomerService.resolve_or_register_customer(
        channel=Channel.TELEGRAM,
        external_user_id="tg-100",
        phone="+7 (912) 345-67-80",
        username="ivan_ivanov",
        name="Иван",
    )
    assert result.status == "identified"
    assert result.is_new_customer is True
    assert result.customer is not None
    assert result.customer.phone == "79123456780"
    assert result.customer.phone_verified_at is None
    assert CustomerChannelIdentity.objects.filter(
        customer=result.customer,
        channel=Channel.TELEGRAM,
        external_user_id="tg-100",
    ).exists()


@pytest.mark.django_db
def test_resolve_customer_marks_phone_verified_when_source_trusted():
    result = CustomerService.resolve_or_register_customer(
        channel=Channel.MAX,
        external_user_id="max-100",
        phone="9991234567",
        phone_verified=True,
    )
    assert result.customer is not None
    assert result.customer.phone == "79991234567"
    assert result.customer.phone_verified_at is not None


@pytest.mark.django_db
def test_resolve_customer_creates_channel_card_and_conflict_by_same_phone(customer):
    result = CustomerService.resolve_or_register_customer(
        channel=Channel.VK,
        external_user_id="vk-200",
        phone=customer.phone,
        username="vk_name",
    )
    assert result.status == "identified"
    assert result.is_new_customer is True
    assert result.customer != customer
    assert result.channel_linked is True
    assert result.conflicts_created == 1
    assert CustomerChannelIdentity.objects.filter(
        customer=result.customer,
        channel=Channel.VK,
        external_user_id="vk-200",
    ).exists()
    assert CustomerIdentityConflict.objects.filter(
        source_customer=result.customer,
        matched_customer=customer,
        contact_value=customer.phone,
        status="pending",
    ).exists()


@pytest.mark.django_db
def test_resolve_email_customer_without_phone():
    result = CustomerService.resolve_email_customer(
        external_user_id="email:anna",
        email="Anna@Example.com",
        name="Анна",
    )

    assert result.is_new_customer is True
    assert result.customer.email == "anna@example.com"
    assert result.customer.phone == ""
    assert CustomerChannelIdentity.objects.filter(
        customer=result.customer,
        channel=Channel.EMAIL,
        external_user_id="email:anna",
    ).exists()


@pytest.mark.django_db
def test_website_customer_uses_unique_contact_match(customer):
    result = CustomerService.resolve_website_customer(
        external_user_id="web:submission",
        name="Иван из формы",
        phone=customer.phone,
        email="ivan@example.com",
    )

    assert result.is_new_customer is False
    assert result.customer == customer
    customer.refresh_from_db()
    assert customer.email == "ivan@example.com"


@pytest.mark.django_db
def test_website_ambiguous_contacts_create_non_blocking_card_and_conflicts(customer):
    email_customer = CustomerService.create_customer(
        name="Другой email-клиент",
        email="other@example.com",
        first_source=CustomerSource.EMAIL,
    )

    result = CustomerService.resolve_website_customer(
        external_user_id="web:ambiguous",
        name="Клиент формы",
        phone=customer.phone,
        email=email_customer.email,
    )

    assert result.is_new_customer is True
    assert result.customer not in {customer, email_customer}
    assert result.conflicts_created == 2
    assert CustomerIdentityConflict.objects.filter(source_customer=result.customer).count() == 2


@pytest.mark.django_db
def test_email_phone_conflict_does_not_replace_or_block_customer(customer):
    result = CustomerService.resolve_email_customer(
        external_user_id="email:conflict",
        email="mail-customer@example.com",
        phone=customer.phone,
        name="Клиент почты",
    )

    assert result.status == "identified"
    assert result.is_new_customer is True
    assert result.customer.phone == customer.phone
    assert result.conflicts_created == 1
