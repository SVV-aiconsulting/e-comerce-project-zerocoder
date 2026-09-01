from django.core.management import call_command

import pytest

from apps.catalog.models import Product, ProductAlias, ProductImage
from apps.intake.catalog_matching import CatalogMatcher
from apps.intake.enums import ItemMatchStatus


@pytest.mark.django_db
def test_load_demo_data_creates_seafood_catalog_and_aliases_idempotently():
    call_command("load_demo_data")
    Product.objects.filter(public_code="DEMO-SALMON").update(delivery_height_cm=None)
    call_command("load_demo_data")

    assert Product.objects.filter(public_code__startswith="DEMO-").count() == 15
    assert not Product.objects.filter(
        public_code__startswith="DEMO-",
        delivery_weight_grams__isnull=True,
    ).exists()
    assert not Product.objects.filter(
        public_code__startswith="DEMO-",
        delivery_length_cm__isnull=True,
    ).exists()
    assert not Product.objects.filter(
        public_code__startswith="DEMO-",
        delivery_width_cm__isnull=True,
    ).exists()
    assert not Product.objects.filter(
        public_code__startswith="DEMO-",
        delivery_height_cm__isnull=True,
    ).exists()
    assert Product.objects.get(public_code="DEMO-SALMON").aliases.filter(alias="сёмга").exists()
    assert Product.objects.get(public_code="DEMO-COD").aliases.filter(alias="рыба").exists()
    assert Product.objects.get(public_code="DEMO-FLOUNDER").aliases.filter(alias="рыба").exists()
    assert ProductAlias.objects.filter(alias="рыба").count() >= 3
    assert CatalogMatcher.match("рыба").status == ItemMatchStatus.AMBIGUOUS
    salmon = Product.objects.get(public_code="DEMO-SALMON")
    assert ProductImage.objects.filter(product=salmon, is_main=True).exists()
    assert ProductImage.objects.filter(product=salmon).count() == 1
