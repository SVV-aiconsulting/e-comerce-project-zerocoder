from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.catalog.models import Product, ProductImage
from apps.common.enums import ProductUnit
from apps.common.utils import generate_public_code


@pytest.mark.django_db
def test_create_product():
    product = Product.objects.create(
        public_code=generate_public_code(
            lambda code: Product.objects.filter(public_code=code).exists()
        ),
        name="Яблоки",
        unit=ProductUnit.KG,
        min_quantity=Decimal("0.5"),
        base_price=Decimal("150.00"),
        is_active=True,
        description="Свежие яблоки",
    )
    assert product.pk is not None
    assert product.is_active is True
    assert str(product) == f"Яблоки ({product.public_code})"


@pytest.mark.django_db
def test_only_one_main_image_per_product(product):
    first = ProductImage.objects.create(
        product=product,
        image=SimpleUploadedFile("test.jpg", b"filecontent", content_type="image/jpeg"),
        is_main=True,
    )
    second = ProductImage.objects.create(
        product=product,
        image=SimpleUploadedFile("test2.jpg", b"filecontent2", content_type="image/jpeg"),
        is_main=True,
    )

    first.refresh_from_db()
    second.refresh_from_db()

    assert first.is_main is False
    assert second.is_main is True
    assert ProductImage.objects.filter(product=product, is_main=True).count() == 1
