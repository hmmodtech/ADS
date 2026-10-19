"""The single source of truth about what an ad is ALLOWED to claim."""
from __future__ import annotations

from .. import geo
from ..models import Campaign, Product
from ..services.pricing import price_for

CONDITION_AR = {"new": "جديد", "used": "مستعمل", "refurbished": "مجدّد"}
CONDITION_EN = {"new": "Brand new", "used": "Pre-owned", "refurbished": "Refurbished"}
LOW_STOCK_THRESHOLD = 20


def short_specs(product: Product, limit: int = 4) -> list[str]:
    out = []
    for k, v in (product.specs or {}).items():
        v = str(v).strip()
        if not v:
            continue
        out.append(v if len(v) >= 4 else f"{k} {v}")
        if len(out) >= limit:
            break
    return out


def scarcity_allowed(product: Product) -> bool:
    """Urgency/limited-stock wording is only allowed when real, tracked stock is low."""
    return bool(product.track_stock and product.stock is not None
                and 0 < product.stock <= LOW_STOCK_THRESHOLD)


def facts_for(product: Product, campaign: Campaign, cc: str) -> dict:
    amount, currency, explicit = price_for(product, cc)
    c = geo.country(cc)
    return {
        "country_code": cc,
        "country_ar": c["ar"],
        "country_en": c["en"],
        "product_name_ar": product.name_ar or product.name_en,
        "product_name_en": product.name_en,
        "brand": product.brand,
        "condition_ar": CONDITION_AR.get(product.condition, product.condition),
        "condition_en": CONDITION_EN.get(product.condition, product.condition),
        "specs": product.specs or {},
        "short_specs": short_specs(product),
        "price": amount,
        "currency": currency,
        "price_ar": geo.format_price(amount, currency, True) if amount else None,
        "price_en": geo.format_price(amount, currency, False) if amount else None,
        "price_is_explicit": explicit,
        "warranty": product.warranty_text,
        "offer": product.offer_text,
        "scarcity_allowed": scarcity_allowed(product),
        "stock": product.stock if product.track_stock else None,
        "description": product.description,
    }
