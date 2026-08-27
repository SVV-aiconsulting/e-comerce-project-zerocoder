"""Каталог товаров."""
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.auth import AdapterTokenAuthentication, catalog_requires_adapter_token
from apps.api.exceptions import ProductInactive, ProductNotFound
from apps.api.serializers.catalog import ProductDetailSerializer, ProductListSerializer
from apps.catalog.services import CatalogService


class ProductListView(APIView):
    """Список активных товаров."""

    def get_authenticators(self):
        if catalog_requires_adapter_token():
            return [AdapterTokenAuthentication()]
        return []

    permission_classes = []

    def get(self, request):
        products = CatalogService.get_active_products()
        serializer = ProductListSerializer(products, many=True, context={"request": request})
        return Response(serializer.data)


class ProductDetailView(APIView):
    """Карточка товара по public_code."""

    def get_authenticators(self):
        if catalog_requires_adapter_token():
            return [AdapterTokenAuthentication()]
        return []

    permission_classes = []

    def get(self, request, public_code: str):
        product = CatalogService.get_product(public_code=public_code)
        if product is None:
            raise ProductNotFound()
        if not product.is_active:
            raise ProductInactive()
        serializer = ProductDetailSerializer(product, context={"request": request})
        return Response(serializer.data)
