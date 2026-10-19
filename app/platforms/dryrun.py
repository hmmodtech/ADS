"""Dry-run adapter: NOTHING is sent to any ad platform.

Used in `PLATFORM_MODE=dry_run` (the default) so the full flow - generation, review, approval,
optimizer - can be exercised safely. IDs are prefixed `dryrun_` and every metric is flagged
`simulated`, so the UI never presents fake numbers as real results.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from ..db import utcnow
from ..models import Campaign, Product
from .base import DeployResult, Metrics, PlatformAdapter


class DryRunAdapter(PlatformAdapter):
    simulated = True

    def __init__(self, name: str):
        self.name = name

    def configured(self) -> bool:
        return True

    async def deploy(self, campaign: Campaign, product: Product, activate: bool) -> DeployResult:
        ids = {
            "campaign_id": f"dryrun_{self.name}_{uuid.uuid4().hex[:8]}",
            "adsets": {cc: f"dryrun_adset_{cc}" for cc in campaign.countries},
            "active": activate,
        }
        return DeployResult(True, ids, "محاكاة: لم يُرسل أي شيء إلى المنصة", simulated=True)

    async def set_active(self, campaign: Campaign, active: bool) -> None:
        ids = (campaign.external_ids or {}).get(self.name)
        if ids is not None:
            ids["active"] = active

    async def set_budget(self, campaign: Campaign, daily_budget_usd: float) -> None:
        return None

    async def fetch_metrics(self, campaign: Campaign) -> Metrics:
        """Deterministic pseudo-metrics derived from the campaign id and how long it has 'run'."""
        start = campaign.launched_at or campaign.approved_at or campaign.created_at
        hours = max((utcnow() - start).total_seconds() / 3600, 0.0)
        seed = int(hashlib.sha256(f"{campaign.id}{self.name}".encode()).hexdigest()[:8], 16)
        ctr = 0.003 + (seed % 1500) / 100000      # 0.3% .. 1.8%
        cpm = 3.0 + (seed // 7 % 400) / 100       # $3 .. $7
        spend = min(campaign.daily_budget_usd * hours / 24, campaign.total_budget_usd or 1e9)
        impressions = int(spend / cpm * 1000)
        clicks = int(impressions * ctr)
        conversions = int(clicks * 0.02)
        return Metrics(round(spend, 2), impressions, clicks, conversions, simulated=True)
