from django.core.management import call_command

import pytest

from apps.catalog.models import Product, ProductAlias
from apps.intake.catalog_matching import CatalogMatcher
from apps.intake.enums import ItemMatchStatus


@pytest.mark.django_db
def test_load_demo_data_creates_seafood_catalog_and_aliases_idempotently():
    call_command("load_demo_data")
    call_command("load_demo_data")

    assert Product.objects.filter(public_code__startswith="DEMO-").count() == 15
    assert Product.objects.get(public_code="DEMO-SALMON").aliases.filter(alias="сёмга").exists()
    assert Product.objects.get(public_code="DEMO-COD").aliases.filter(alias="рыба").exists()
    assert Product.objects.get(public_code="DEMO-FLOUNDER").aliases.filter(alias="рыба").exists()
    assert ProductAlias.objects.filter(alias="рыба").count() >= 3
    assert CatalogMatcher.match("рыба").status == ItemMatchStatus.AMBIGUOUS
