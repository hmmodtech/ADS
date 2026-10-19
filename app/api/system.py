from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import geo
from ..config import get_settings
from ..db import get_db
from ..models import Campaign, Product
from ..platforms import SUPPORTED_PLATFORMS, platform_status
from ..services import store

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/meta")
def meta():
    s = get_settings()
    return {
        "countries": {k: {"ar": v["ar"], "en": v["en"], "currency": v["currency"]} for k, v in geo.COUNTRIES.items()},
        "currencies": list(geo.USD_RATES),
        "platforms": list(SUPPORTED_PLATFORMS),
        "max_daily_budget_usd": s.max_daily_budget_usd,
        "styles": {"balanced": "متوازن", "price": "التركيز على السعر", "features": "التركيز على المواصفات",
                   "lifestyle": "أسلوب الحياة"},
    }


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    s = get_settings()
    by_status = dict(db.query(Campaign.status, func.count()).group_by(Campaign.status).all())
    tot = db.query(func.coalesce(func.sum(Campaign.spent_usd), 0), func.coalesce(func.sum(Campaign.impressions), 0),
                   func.coalesce(func.sum(Campaign.clicks), 0), func.coalesce(func.sum(Campaign.conversions), 0)).one()
    live = db.query(Campaign).filter(Campaign.status == "live").all()
    pending = db.query(Campaign).filter(Campaign.status == "pending_review").count()
    ps = platform_status()
    return {
        "campaigns_by_status": by_status,
        "products": db.query(Product).count(),
        "totals": {"spend_usd": round(tot[0], 2), "impressions": int(tot[1]), "clicks": int(tot[2]),
                   "conversions": int(tot[3]),
                   "ctr_pct": round(tot[2] / tot[1] * 100, 2) if tot[1] else 0},
        "live_daily_budget_usd": round(sum(c.daily_budget_usd for c in live), 2),
        "pending_review": pending,
        "system": {
            "platform_mode": s.platform_mode, "llm": s.llm_enabled, "model": s.gemini_model if s.llm_enabled else None,
            "meta_configured": ps["meta"], "tiktok_configured": ps["tiktok"],
            "whatsapp_set": bool(s.whatsapp_number), "auto_approve": s.auto_approve,
            "store": store.public_view(store.get_config(db)),
            "optimizer_interval_min": s.optimizer_interval_min,
        },
    }
