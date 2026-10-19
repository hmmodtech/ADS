"""Agent 5 - Deployer: pushes an APPROVED campaign to the ad platforms (idempotent, per platform)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import Campaign, Product
from ..platforms import PlatformError, get_adapter
from .base import BaseAgent


class DeployerAgent(BaseAgent):
    name = "deployer"
    label = "وكيل النشر"

    async def run(self, db: Session, campaign: Campaign, product: Product, activate: bool) -> dict:
        results: dict[str, dict] = {}
        ext = dict(campaign.external_ids or {})
        for platform in campaign.platforms:
            if ext.get(platform, {}).get("campaign_id"):
                results[platform] = {"ok": True, "skipped": True, "detail": "موجودة مسبقاً على المنصة"}
                self.log(db, campaign.id, f"{platform}: الحملة منشورة مسبقاً - تخطّي لتفادي التكرار", "info")
                continue
            try:
                adapter = get_adapter(platform)
                res = await adapter.deploy(campaign, product, activate)
                ext[platform] = {**res.ids, "simulated": res.simulated}
                results[platform] = {"ok": res.ok, "detail": res.detail, "simulated": res.simulated}
                self.log(db, campaign.id, f"{platform}: {res.detail}", "decision", ids=res.ids)
            except PlatformError as e:
                results[platform] = {"ok": False, "detail": str(e)}
                self.log(db, campaign.id, f"{platform}: فشل النشر - {e}", "error")
            except Exception as e:  # network or unexpected
                results[platform] = {"ok": False, "detail": f"خطأ غير متوقع: {e}"}
                self.log(db, campaign.id, f"{platform}: خطأ غير متوقع - {e}", "error")
            campaign.external_ids = dict(ext)  # reassign so SQLAlchemy persists the JSON change
            db.commit()

        ok = [p for p, r in results.items() if r["ok"]]
        if not ok:
            campaign.status = "failed"
            campaign.status_reason = "; ".join(f"{p}: {r['detail']}" for p, r in results.items())
        else:
            campaign.status = "live" if activate else "paused"
            campaign.status_reason = None if activate else "أُنشئت متوقفة - اضغط «تشغيل» عند الجاهزية"
            if activate and not campaign.launched_at:
                campaign.launched_at = utcnow()
            failed = [p for p in results if not results[p]["ok"]]
            if failed:
                campaign.status_reason = "نجحت جزئياً؛ فشلت: " + ", ".join(failed)
        db.commit()
        return results
