from django.db import migrations


MOLLUSC_PRODUCT_CODES = (
    "DEMO-SCALLOP",
    "DEMO-MUSSELS",
    "DEMO-SQUID",
    "DEMO-OCTOPUS",
)
MOLLUSC_ALIASES = ("моллюски", "молюски", "малюски")


def add_mollusc_aliases(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    ProductAlias = apps.get_model("catalog", "ProductAlias")
    for product in Product.objects.filter(public_code__in=MOLLUSC_PRODUCT_CODES):
        for alias in MOLLUSC_ALIASES:
            ProductAlias.objects.get_or_create(
                product=product,
                normalized_alias=alias,
                defaults={"alias": alias},
            )


class Migration(migrations.Migration):
    dependencies = [("catalog", "0005_product_delivery_height_cm_and_more")]

    operations = [
        migrations.RunPython(add_mollusc_aliases, migrations.RunPython.noop),
    ]
