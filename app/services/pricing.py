from __future__ import annotations

from .. import geo
from ..models import Product


def price_for(product: Product, cc: str) -> tuple[float, str, bool]:
    """Return (amount, currency, explicit). `explicit` is False when auto-converted from base price."""
    cc = cc.upper()
    explicit = (product.prices or {}).get(cc)
    if explicit and explicit.get("price"):
        cur = explicit.get("currency") or geo.country(cc)["currency"]
        return float(explicit["price"]), cur, True
    target_cur = geo.country(cc)["currency"]
    if not product.base_price:
        return 0.0, target_cur, False
    if product.currency.upper() == target_cur:  # base price is already in the local currency
        return float(product.base_price), target_cur, True
    amt = geo.convert(product.base_price, product.currency, target_cur)
    return geo.round_price(amt, target_cur), target_cur, False


def all_allowed_prices(product: Product, countries: list[str]) -> list[float]:
    out = []
    for cc in countries:
        amt, _, _ = price_for(product, cc)
        if amt:
            out.append(amt)
    return out
