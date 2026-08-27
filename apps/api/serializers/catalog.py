"""Сериализаторы каталога."""
from rest_framework import serializers

from apps.catalog.models import Product, ProductImage


class ProductImageSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = ProductImage
        fields = ("url", "alt_text", "is_main", "sort_order")

    def get_url(self, obj: ProductImage) -> str:
        request = self.context.get("request")
        if not obj.image:
            return ""
        url = obj.image.url
        if request:
            return request.build_absolute_uri(url)
        return url


class ProductListSerializer(serializers.ModelSerializer):
    unit_label = serializers.CharField(source="get_unit_display", read_only=True)
    main_image_url = serializers.SerializerMethodField()
    is_available = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            "id",
            "public_code",
            "name",
            "description",
            "unit",
            "unit_label",
            "min_quantity",
            "base_price",
            "main_image_url",
            "sort_order",
            "is_available",
        )

    def get_main_image_url(self, obj: Product) -> str:
        main_image = next((img for img in obj.images.all() if img.is_main), None)
        if main_image is None:
            images = list(obj.images.all())
            main_image = images[0] if images else None
        if main_image is None or not main_image.image:
            return ""
        request = self.context.get("request")
        url = main_image.image.url
        if request:
            return request.build_absolute_uri(url)
        return url

    def get_is_available(self, obj: Product) -> bool:
        return obj.is_active


class ProductDetailSerializer(ProductListSerializer):
    images = ProductImageSerializer(many=True, read_only=True)

    class Meta(ProductListSerializer.Meta):
        fields = ProductListSerializer.Meta.fields + ("images",)
