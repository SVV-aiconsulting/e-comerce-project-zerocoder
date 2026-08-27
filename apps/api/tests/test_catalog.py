"""Тесты каталога REST API."""
import pytest
from rest_framework.test import APIClient

from apps.common.enums import ProductUnit
from apps.common.utils import generate_public_code
from apps.catalog.models import Product


@pytest.mark.django_db
def test_product_list_returns_only_active(product, inactive_product):
    client = APIClient()
    response = client.get("/api/products/")

    assert response.status_code == 200
    assert len(response.data) == 1
    assert response.data[0]["public_code"] == product.public_code
    assert response.data[0]["is_available"] is True


@pytest.mark.django_db
def test_product_detail_returns_active_product(product):
    client = APIClient()
    response = client.get(f"/api/products/{product.public_code}/")

    assert response.status_code == 200
    assert response.data["name"] == product.name
    assert "images" in response.data


@pytest.mark.django_db
def test_product_detail_inactive_returns_404(inactive_product):
    client = APIClient()
    response = client.get(f"/api/products/{inactive_product.public_code}/")

    assert response.status_code == 404
    assert response.data["error"]["code"] == "product_inactive"


@pytest.mark.django_db
def test_product_detail_not_found():
    client = APIClient()
    response = client.get("/api/products/UNKNOWN/")

    assert response.status_code == 404
    assert response.data["error"]["code"] == "product_not_found"


@pytest.mark.django_db
def test_meta_endpoint():
    client = APIClient()
    response = client.get("/api/meta/")

    assert response.status_code == 200
    assert "channels" in response.data
    assert "payment_methods" in response.data


@pytest.mark.django_db
def test_catalog_closed_without_token(settings, product):
    settings.ADAPTER_API_PUBLIC_CATALOG = False
    settings.ADAPTER_API_TOKENS = ["test-token"]
    client = APIClient()
    response = client.get("/api/products/")

    assert response.status_code == 401


@pytest.mark.django_db
def test_catalog_open_with_token_when_closed(settings, product):
    settings.ADAPTER_API_PUBLIC_CATALOG = False
    settings.ADAPTER_API_TOKENS = ["test-token"]
    client = APIClient()
    client.credentials(HTTP_X_ADAPTER_TOKEN="test-token")
    response = client.get("/api/products/")

    assert response.status_code == 200
    assert len(response.data) == 1
