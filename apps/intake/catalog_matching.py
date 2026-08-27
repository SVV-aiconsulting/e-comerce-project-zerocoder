"""Детерминированное сопоставление текста LLM с реальным каталогом."""
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher

from django.conf import settings

from apps.catalog.models import Product, ProductAlias, normalize_product_text
from apps.intake.enums import ItemMatchStatus, ResolutionSource


@dataclass(frozen=True)
class ProductMatch:
    status: str
    product: Product | None
    candidates: tuple[Product, ...]
    source: str
    confidence: Decimal | None


class CatalogMatcher:
    max_candidates = 5

    @classmethod
    def match(cls, raw_product_name: str) -> ProductMatch:
        normalized = normalize_product_text(raw_product_name)
        if not normalized:
            return ProductMatch(
                status=ItemMatchStatus.INVALID,
                product=None,
                candidates=(),
                source="",
                confidence=None,
            )

        active_products = list(Product.objects.filter(is_active=True).order_by("id"))
        exact = [
            product
            for product in active_products
            if normalize_product_text(product.name) == normalized
            or product.public_code.casefold() == raw_product_name.strip().casefold()
        ]
        if len(exact) == 1:
            return cls._matched(exact[0], ResolutionSource.EXACT, Decimal("1"))
        if len(exact) > 1:
            return cls._ambiguous(exact, ResolutionSource.EXACT, Decimal("1"))

        aliases = list(
            ProductAlias.objects.filter(
                normalized_alias=normalized,
                product__is_active=True,
            )
            .select_related("product")
            .order_by("product_id")
        )
        alias_products = list({alias.product_id: alias.product for alias in aliases}.values())
        if len(alias_products) == 1:
            return cls._matched(alias_products[0], ResolutionSource.ALIAS, Decimal("1"))
        if len(alias_products) > 1:
            return cls._ambiguous(
                alias_products,
                ResolutionSource.ALIAS,
                Decimal("1"),
            )

        fuzzy_candidates = []
        aliases_by_product = {}
        for alias in ProductAlias.objects.filter(
            product__in=active_products
        ).select_related("product"):
            aliases_by_product.setdefault(alias.product_id, []).append(
                alias.normalized_alias
            )
        for product in active_products:
            variants = [normalize_product_text(product.name)] + aliases_by_product.get(
                product.pk,
                [],
            )
            score = max(cls._similarity(normalized, variant) for variant in variants)
            if score >= settings.CATALOG_MATCH_CANDIDATE_THRESHOLD:
                fuzzy_candidates.append((product, score))
        fuzzy_candidates.sort(key=lambda pair: (-pair[1], pair[0].pk))
        fuzzy_candidates = fuzzy_candidates[: cls.max_candidates]
        if not fuzzy_candidates:
            return ProductMatch(
                status=ItemMatchStatus.NOT_FOUND,
                product=None,
                candidates=(),
                source=ResolutionSource.FUZZY,
                confidence=None,
            )

        candidates = [pair[0] for pair in fuzzy_candidates]
        top_score = Decimal(str(round(fuzzy_candidates[0][1], 4)))
        second_score = (
            Decimal(str(round(fuzzy_candidates[1][1], 4)))
            if len(fuzzy_candidates) > 1
            else Decimal("0")
        )
        if (
            top_score >= Decimal(str(settings.CATALOG_MATCH_AUTO_THRESHOLD))
            and top_score - second_score >= Decimal(str(settings.CATALOG_MATCH_MIN_MARGIN))
        ):
            return cls._matched(candidates[0], ResolutionSource.FUZZY, top_score)
        return cls._ambiguous(candidates, ResolutionSource.FUZZY, top_score)

    @staticmethod
    def _similarity(left: str, right: str) -> float:
        direct = SequenceMatcher(None, left, right).ratio()
        token_sorted = SequenceMatcher(
            None,
            " ".join(sorted(left.split())),
            " ".join(sorted(right.split())),
        ).ratio()
        return max(direct, token_sorted)

    @staticmethod
    def _matched(product, source, confidence):
        return ProductMatch(
            status=ItemMatchStatus.MATCHED,
            product=product,
            candidates=(product,),
            source=source,
            confidence=confidence,
        )

    @staticmethod
    def _ambiguous(products, source, confidence):
        return ProductMatch(
            status=ItemMatchStatus.AMBIGUOUS,
            product=None,
            candidates=tuple(products),
            source=source,
            confidence=confidence,
        )
