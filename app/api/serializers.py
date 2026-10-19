from __future__ import annotations

from ..models import Campaign, Product


def product_out(p: Product) -> dict:
    return {
        "id": p.id, "sku": p.sku, "source": p.source, "external_id": p.external_id,
        "name_ar": p.name_ar, "name_en": p.name_en, "brand": p.brand, "category": p.category,
        "condition": p.condition, "description": p.description, "specs": p.specs or {},
        "images": p.images or [], "base_price": p.base_price, "currency": p.currency,
        "prices": p.prices or {}, "track_stock": p.track_stock, "stock": p.stock,
        "warranty_text": p.warranty_text, "offer_text": p.offer_text, "landing_url": p.landing_url,
        "active": p.active, "created_at": p.created_at.isoformat() if p.created_at else None,
        "synced_at": p.synced_at.isoformat() if p.synced_at else None,
    }


def campaign_out(c: Campaign, detail: bool = False, busy: bool = False) -> dict:
    out = {
        "id": c.id, "name": c.name, "status": c.status, "status_reason": c.status_reason, "busy": busy,
        "product_id": c.product_id, "product_name": c.product.display_name if c.product else None,
        "platforms": c.platforms, "countries": c.countries, "languages": c.languages,
        "daily_budget_usd": c.daily_budget_usd, "total_budget_usd": c.total_budget_usd,
        "auto_optimize": c.auto_optimize, "creative_style": c.creative_style,
        "spent_usd": c.spent_usd, "impressions": c.impressions, "clicks": c.clicks,
        "conversions": c.conversions, "metrics_simulated": c.metrics_simulated,
        "ctr_pct": round(c.clicks / c.impressions * 100, 2) if c.impressions else 0,
        "llm_used": c.llm_used, "landing_url": c.landing_url,
        "compliance": {k: v for k, v in (c.compliance or {}).items() if k != "issues"} or None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "launched_at": c.launched_at.isoformat() if c.launched_at else None,
        "simulated_deploy": any(v.get("simulated") for v in (c.external_ids or {}).values()),
        "assets": [{"kind": a["kind"], "country": a.get("country"), "ratio": a.get("ratio"), "url": a.get("url")}
                   for a in (c.assets or [])],
    }
    if detail:
        out.update({
            "age_min": c.age_min, "age_max": c.age_max, "gender": c.gender, "tone": c.tone, "notes": c.notes,
            "strategy": c.strategy, "copies": c.copies, "custom_copy": c.custom_copy,
            "compliance": c.compliance, "external_ids": c.external_ids,
            "events": [{"ts": e.ts.isoformat(), "agent": e.agent, "level": e.level, "message": e.message}
                       for e in c.events][-150:],
            "snapshots": [{"ts": s.ts.isoformat(), "platform": s.platform, "spend_usd": s.spend_usd,
                           "impressions": s.impressions, "clicks": s.clicks, "simulated": s.simulated}
                          for s in c.snapshots][-60:],
        })
    return out
