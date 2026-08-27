from decimal import Decimal

import pytest

from apps.catalog.models import Product, ProductAlias
from apps.common.enums import ProductUnit
from apps.common.utils import generate_public_code
from apps.intake.catalog_matching import CatalogMatcher
from apps.intake.enums import ItemMatchStatus, ResolutionSource


def create_product(name, *, active=True):
    return Product.objects.create(
        public_code=generate_public_code(
            lambda code: Product.objects.filter(public_code=code).exists()
        ),
        name=name,
        unit=ProductUnit.PACKAGE,
        min_quantity=Decimal("1"),
        base_price=Decimal("500"),
        is_active=active,
    )


@pytest.mark.django_db
def test_catalog_matcher_normalizes_exact_name():
    product = create_product("Креветки Ёжики")

    result = CatalogMatcher.match("  креветки   ежики ")

    assert result.status == ItemMatchStatus.MATCHED
    assert result.product == product
    assert result.source == ResolutionSource.EXACT
    assert result.confidence == Decimal("1")


@pytest.mark.django_db
def test_catalog_matcher_uses_managed_alias():
    product = create_product("Креветки тигровые 500 г")
    alias = ProductAlias.objects.create(product=product, alias="тигровые креветки")

    result = CatalogMatcher.match("Тигровые креветки")

    alias.refresh_from_db()
    assert alias.normalized_alias == "тигровые креветки"
    assert result.status == ItemMatchStatus.MATCHED
    assert result.product == product
    assert result.source == ResolutionSource.ALIAS


@pytest.mark.django_db
def test_catalog_matcher_returns_ambiguous_alias_candidates():
    first = create_product("Креветки северные")
    second = create_product("Креветки аргентинские")
    ProductAlias.objects.create(product=first, alias="креветки")
    ProductAlias.objects.create(product=second, alias="креветки")

    result = CatalogMatcher.match("Креветки")

    assert result.status == ItemMatchStatus.AMBIGUOUS
    assert result.product is None
    assert {product.pk for product in result.candidates} == {first.pk, second.pk}
    assert result.source == ResolutionSource.ALIAS


@pytest.mark.django_db
def test_catalog_matcher_uses_unicode_fuzzy_search():
    product = create_product("Креветки тигровые охлажденные")

    result = CatalogMatcher.match("Креветки тигровые охлажденые")

    assert result.status == ItemMatchStatus.MATCHED
    assert result.product == product
    assert result.source == ResolutionSource.FUZZY


@pytest.mark.django_db
def test_catalog_matcher_does_not_return_inactive_product():
    create_product("Уникальный неактивный товар", active=False)

    result = CatalogMatcher.match("Уникальный неактивный товар")

    assert result.status == ItemMatchStatus.NOT_FOUND
    assert result.product is None
