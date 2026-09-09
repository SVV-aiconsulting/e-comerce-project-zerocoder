from django.db import migrations


def add_white_fish_alias(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    ProductAlias = apps.get_model("catalog", "ProductAlias")
    product = Product.objects.filter(public_code="DEMO-FLOUNDER").first()
    if product:
        ProductAlias.objects.get_or_create(
            product=product,
            normalized_alias="белая рыба",
            defaults={"alias": "белая рыба"},
        )


class Migration(migrations.Migration):
    dependencies = [("catalog", "0006_add_mollusc_catalog_aliases")]

    operations = [migrations.RunPython(add_white_fish_alias, migrations.RunPython.noop)]
