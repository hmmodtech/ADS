"""Agent 6 - Optimizer: pulls metrics, then applies explicit, auditable, budget-capped rules.

Deliberately rule-based (not LLM-driven): decisions that move money must be predictable.
Every decision is written to the audit trail with the numbers that triggered it.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import Campaign, PerformanceSnapshot, Product
from ..platforms import PlatformError, get_adapter
from ..platforms.base import Metrics
from .base import BaseAgent

MIN_IMPRESSIONS_FOR_CTR = 3000
MIN_CLICKS_FOR_CPC = 30
BUDGET_COOLDOWN_H = 24


class OptimizerAgent(BaseAgent):
    name = "optimizer"
    label = "وكيل التحسين"

    async def run(self, db: Session, campaign: Campaign, product: Product) -> list[str]:
        decisions: list[str] = []
        total = Metrics()
        for platform, ids in (campaign.external_ids or {}).items():
            try:
                m = await get_adapter(platform).fetch_metrics(campaign)
            except PlatformError as e:
                self.log(db, campaign.id, f"{platform}: تعذّر جلب الأداء - {e}", "warn")
                continue
            db.add(PerformanceSnapshot(campaign_id=campaign.id, platform=platform, spend_usd=m.spend_usd,
                                       impressions=m.impressions, clicks=m.clicks, conversions=m.conversions,
                                       simulated=m.simulated))
            total.spend_usd += m.spend_usd
            total.impressions += m.impressions
            total.clicks += m.clicks
            total.conversions += m.conversions
            total.simulated = total.simulated or m.simulated
        campaign.spent_usd = round(total.spend_usd, 2)
        campaign.impressions, campaign.clicks, campaign.conversions = total.impressions, total.clicks, total.conversions
        campaign.metrics_simulated = total.simulated
        campaign.last_optimized_at = utcnow()
        db.commit()

        if campaign.status != "live":
            return decisions

        # ---- safety rules (always on, even if auto_optimize is off)
        if product.track_stock and not product.in_stock:
            await self._pause(db, campaign, "نفد المخزون", decisions)
            return decisions
        if campaign.total_budget_usd and campaign.spent_usd >= campaign.total_budget_usd:
            await self._pause(db, campaign, f"بلغت الميزانية الكلية ({campaign.total_budget_usd:g}$)", decisions,
                              final=True)
            return decisions
        if not campaign.auto_optimize:
            return decisions

        # ---- performance rules
        s = self.settings
        if total.impressions >= MIN_IMPRESSIONS_FOR_CTR and total.ctr_pct < s.min_ctr_pct:
            await self._pause(db, campaign,
                              f"CTR ضعيف {total.ctr_pct:.2f}% (الحد {s.min_ctr_pct}%) بعد {total.impressions} ظهور",
                              decisions)
            return decisions

        if total.clicks >= MIN_CLICKS_FOR_CPC and self._can_change_budget(campaign):
            cpc = total.cpc_usd
            if cpc > s.max_cpc_usd:
                new = max(round(campaign.daily_budget_usd * 0.75, 2), 5.0)
                if new >= campaign.daily_budget_usd:  # already at the floor and still too expensive
                    await self._pause(db, campaign, f"CPC مرتفع {cpc:.2f}$ عند أدنى ميزانية", decisions)
                else:
                    await self._set_budget(db, campaign, new, f"CPC مرتفع {cpc:.2f}$ > {s.max_cpc_usd}$", decisions)
            elif total.ctr_pct >= 1.5 and cpc < 0.4 * s.max_cpc_usd and total.clicks >= 50:
                cap = min(s.max_daily_budget_usd, campaign.initial_daily_budget_usd * 2)
                new = min(round(campaign.daily_budget_usd * 1.2, 2), cap)
                if new > campaign.daily_budget_usd:
                    await self._set_budget(db, campaign, new,
                                           f"أداء ممتاز: CTR {total.ctr_pct:.2f}% وCPC {cpc:.2f}$", decisions)
        return decisions

    # ------------------------------------------------------------------ actions
    def _can_change_budget(self, c: Campaign) -> bool:
        last = (c.strategy or {}).get("last_budget_change")
        if not last:
            return True
        return utcnow() - datetime.fromisoformat(last) > timedelta(hours=BUDGET_COOLDOWN_H)

    async def _set_budget(self, db, c: Campaign, new_budget: float, why: str, decisions: list[str]):
        old = c.daily_budget_usd
        for platform in (c.external_ids or {}):
            try:
                await get_adapter(platform).set_budget(c, new_budget)
            except PlatformError as e:
                self.log(db, c.id, f"{platform}: تعذّر تعديل الميزانية - {e}", "error")
                return
        c.daily_budget_usd = new_budget
        c.strategy = {**(c.strategy or {}), "last_budget_change": utcnow().isoformat()}
        db.commit()
        msg = f"الميزانية اليومية {old:g}$ ← {new_budget:g}$ ({why})"
        decisions.append(msg)
        self.log(db, c.id, msg, "decision", old=old, new=new_budget)

    async def _pause(self, db, c: Campaign, why: str, decisions: list[str], final: bool = False):
        for platform in (c.external_ids or {}):
            try:
                await get_adapter(platform).set_active(c, False)
            except PlatformError as e:
                self.log(db, c.id, f"{platform}: تعذّر الإيقاف - {e}", "error")
        c.status = "ended" if final else "paused"
        c.status_reason = f"أوقفه وكيل التحسين: {why}"
        db.commit()
        decisions.append(f"إيقاف: {why}")
        self.log(db, c.id, f"إيقاف تلقائي: {why}", "decision")
