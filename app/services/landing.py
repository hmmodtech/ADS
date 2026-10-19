from __future__ import annotations

from urllib.parse import quote

from ..config import get_settings
from ..models import Campaign, Product


def resolve_landing(product: Product, campaign: Campaign | None = None, explicit: str | None = None) -> str | None:
    """Landing page priority: explicit > product URL > DEFAULT_LANDING_URL > WhatsApp click-to-chat link.

    The WhatsApp fallback is what makes the engine usable BEFORE any store exists.
    """
    s = get_settings()
    for candidate in (explicit, campaign.landing_url if campaign else None, product.landing_url, s.default_landing_url):
        if candidate:
            return candidate
    number = "".join(ch for ch in s.whatsapp_number if ch.isdigit())
    if number:
        msg = quote(f"مرحباً، أرغب بالاستفسار عن {product.display_name}")
        return f"https://wa.me/{number}?text={msg}"
    return None
