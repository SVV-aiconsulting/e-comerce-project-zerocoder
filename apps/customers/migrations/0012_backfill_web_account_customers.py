import uuid

from django.db import migrations


def create_missing_customer_cards(apps, schema_editor):
    Customer = apps.get_model("customers", "Customer")
    WebAccount = apps.get_model("customers", "WebAccount")

    for account in WebAccount.objects.filter(customer__isnull=True).iterator():
        while True:
            public_code = uuid.uuid4().hex[:8].upper()
            if not Customer.objects.filter(public_code=public_code).exists():
                break
        customer = Customer.objects.create(
            public_code=public_code,
            name=account.name or "Клиент сайта",
            phone=account.phone_login or "",
            email=account.email,
            first_source="website",
            email_verified_at=account.verified_at,
        )
        account.customer_id = customer.pk
        account.save(update_fields=["customer"])


class Migration(migrations.Migration):
    dependencies = [("customers", "0011_websessionbinding_revoked_at")]

    operations = [
        migrations.RunPython(create_missing_customer_cards, migrations.RunPython.noop),
    ]
