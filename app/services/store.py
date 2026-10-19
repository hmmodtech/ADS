"""OPTIONAL store connector. The engine works fully without it; connect it last.

Supported: `custom` (a JSON API you control), `woocommerce`, `shopify`.
Products synced from a store are marked source="store" and get stock tracking turned on, so the
engine blocks/pauses ads for out-of-stock items automatically.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import Product, Setting
from .netguard import UnsafeURL, assert_public_url

log = logging.getLogger("store")
KEY = "store"
TIMEOUT = httpx.Timeout(30.0)


class StoreError(Exception):
    pass


def get_config(db: Session) -> dict | None:
    row = db.get(Setting, KEY)
    return row.value if row else None


def save_config(db: Session, cfg: dict) -> None:
    row = db.get(Setting, KEY)
    if row:
        row.value = cfg
    else:
        db.add(Setting(key=KEY, value=cfg))
    db.commit()


def clear_config(db: Session) -> None:
    row = db.get(Setting, KEY)
    if row:
        db.delete(row)
        db.commit()


def public_view(cfg: dict | None) -> dict:
    if not cfg:
        return {"connected": False}
    return {"connected": True, "type": cfg["type"], "url": cfg["url"], "has_key": bool(cfg.get("api_key")),
            "last_sync": cfg.get("last_sync"), "last_result": cfg.get("last_result")}


# ------------------------------------------------------------------ http layer
async def _get(client: httpx.AsyncClient, url: str, **kw) -> Any:
    try:
        assert_public_url(url)
        r = await client.get(url, **kw)
    except UnsafeURL as e:
        raise StoreError(str(e)) from e
    except httpx.HTTPError as e:
        raise StoreError(f"تعذّر الاتصال بالمتجر: {e}") from e
    if r.status_code in (401, 403):
        raise StoreError("المتجر رفض المفتاح (401/403)")
    if r.status_code >= 400:
        raise StoreError(f"المتجر أرجع HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError as e:
        raise StoreError("ردّ المتجر ليس JSON") from e


def _strip_html(s: str | None) -> str | None:
    return re.sub(r"<[^>]+>", " ", s).strip() if s else s


# ----------------------------------------------------------------- per-platform
async def _fetch_custom(client, cfg) -> list[dict]:
    headers = {"X-API-Key": cfg["api_key"]} if cfg.get("api_key") else {}
    data = await _get(client, f"{cfg['url']}/products", headers=headers, params={"limit": 200})
    items = data if isinstance(data, list) else data.get("products") or data.get("data") or []
    return [{
        "external_id": str(i.get("id") or i.get("sku")),
        "name_en": i.get("name_en") or i.get("name") or "Product",
        "name_ar": i.get("name_ar"), "brand": i.get("brand"),
        "price": float(i.get("price") or 0), "currency": i.get("currency") or "SAR",
        "stock": int(i["stock"]) if i.get("stock") is not None else None,
        "specs": i.get("specs") or {}, "images": [u for u in (i.get("images") or []) if isinstance(u, str)],
        "category": i.get("category"), "description": i.get("description"), "landing_url": i.get("url"),
    } for i in items if i.get("id") or i.get("sku")]


async def _fetch_woo(client, cfg) -> list[dict]:
    ck, _, cs = (cfg.get("api_key") or ":").partition(":")
    out: list[dict] = []
    for page in range(1, 6):
        items = await _get(client, f"{cfg['url']}/wp-json/wc/v3/products", auth=(ck, cs),
                           params={"per_page": 100, "page": page, "status": "publish"})
        for i in items:
            out.append({
                "external_id": str(i["id"]), "name_en": i.get("name") or "Product", "name_ar": None,
                "brand": None, "price": float(i.get("price") or 0), "currency": cfg.get("currency", "SAR"),
                "stock": i.get("stock_quantity") if i.get("manage_stock") else (None if i.get("stock_status") == "instock" else 0),
                "specs": {a["name"]: ", ".join(a.get("options", [])) for a in i.get("attributes", [])},
                "images": [im["src"] for im in i.get("images", []) if im.get("src")],
                "category": (i.get("categories") or [{}])[0].get("name"),
                "description": _strip_html(i.get("short_description")), "landing_url": i.get("permalink"),
            })
        if len(items) < 100:
            break
    return out


async def _fetch_shopify(client, cfg) -> list[dict]:
    data = await _get(client, f"{cfg['url']}/admin/api/2024-10/products.json", params={"limit": 250},
                      headers={"X-Shopify-Access-Token": cfg.get("api_key", "")})
    out = []
    for i in data.get("products", []):
        variants = i.get("variants") or [{}]
        qty = [v.get("inventory_quantity") for v in variants if v.get("inventory_quantity") is not None]
        out.append({
            "external_id": str(i["id"]), "name_en": i.get("title") or "Product", "name_ar": None,
            "brand": i.get("vendor"), "price": float(variants[0].get("price") or 0),
            "currency": cfg.get("currency", "SAR"), "stock": sum(qty) if qty else None, "specs": {},
            "images": [im["src"] for im in i.get("images", []) if im.get("src")],
            "category": i.get("product_type"), "description": _strip_html(i.get("body_html")),
            "landing_url": f"{cfg['url']}/products/{i.get('handle')}",
        })
    return out


FETCHERS = {"custom": _fetch_custom, "woocommerce": _fetch_woo, "shopify": _fetch_shopify}


async def fetch_products(cfg: dict) -> list[dict]:
    fn = FETCHERS.get(cfg["type"])
    if not fn:
        raise StoreError(f"نوع متجر غير مدعوم: {cfg['type']}")
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
        return await fn(client, cfg)


async def test_connection(cfg: dict) -> int:
    return len(await fetch_products(cfg))


async def sync(db: Session) -> dict:
    cfg = get_config(db)
    if not cfg:
        raise StoreError("لا يوجد متجر مرتبط")
    items = await fetch_products(cfg)
    added = updated = 0
    for it in items:
        p = db.query(Product).filter(Product.external_id == it["external_id"]).first()
        if p:
            updated += 1
        else:
            p = Product(external_id=it["external_id"], source="store", name_en=it["name_en"])
            db.add(p)
            added += 1
        p.name_en = it["name_en"]
        p.name_ar = p.name_ar or it.get("name_ar")
        p.brand = it.get("brand") or p.brand
        p.base_price, p.currency = it["price"], it["currency"]
        p.stock, p.track_stock = it["stock"], it["stock"] is not None
        p.specs = p.specs or it["specs"]
        p.images = it["images"] or p.images or []
        p.category = it.get("category") or p.category
        p.description = p.description or it.get("description")
        p.landing_url = it.get("landing_url") or p.landing_url
        p.synced_at = utcnow()
    result = {"added": added, "updated": updated, "total": len(items)}
    cfg = {**cfg, "last_sync": utcnow().isoformat(), "last_result": result}
    save_config(db, cfg)
    db.commit()
    return result
