"""Optimizer rules, tested by feeding controlled metrics through the dry-run adapter."""
import asyncio

import pytest

from app.db import SessionLocal
from app.models import Campaign, Product
from app.platforms.base import Metrics
from app.platforms.dryrun import DryRunAdapter
from app.agents.optimizer import OptimizerAgent
from conftest import H, wait_status


@pytest.fixture
def live_campaign(client, product):
    cid = client.post("/api/campaigns", headers=H, json={
        "product_id": product["id"], "countries": ["SA"], "daily_budget_usd": 40, "total_budget_usd": 500}).json()["id"]
    wait_status(client, cid, {"pending_review"})
    assert client.post(f"/api/campaigns/{cid}/approve", headers=H, json={"activate": True}).json()["status"] == "live"
    return cid


def run_opt(cid, monkeypatch, **m):
    async def fake(self, campaign):
        return Metrics(simulated=True, **m)
    monkeypatch.setattr(DryRunAdapter, "fetch_metrics", fake)
    db = SessionLocal()
    try:
        c = db.get(Campaign, cid)
        decisions = asyncio.run(OptimizerAgent().run(db, c, c.product))
        db.refresh(c)
        return c, decisions
    finally:
        db.close()


def test_low_ctr_pauses(live_campaign, monkeypatch):
    c, d = run_opt(live_campaign, monkeypatch, spend_usd=20, impressions=10000, clicks=10)
    assert c.status == "paused" and "CTR" in c.status_reason and d


def test_high_cpc_reduces_budget(live_campaign, monkeypatch):
    c, d = run_opt(live_campaign, monkeypatch, spend_usd=90, impressions=9000, clicks=60)  # ctr .67%, cpc 1.5$
    assert c.status == "live" and c.daily_budget_usd == 30.0


def test_great_performance_scales_within_cap(live_campaign, monkeypatch):
    c, d = run_opt(live_campaign, monkeypatch, spend_usd=20, impressions=6000, clicks=120)  # ctr 2%, cpc .17$
    assert c.daily_budget_usd == 48.0 and c.daily_budget_usd <= 2 * c.initial_daily_budget_usd


def test_total_budget_ends_campaign(live_campaign, monkeypatch):
    c, d = run_opt(live_campaign, monkeypatch, spend_usd=500, impressions=100000, clicks=1000)
    assert c.status == "ended"


def test_budget_cooldown(live_campaign, monkeypatch):
    c, _ = run_opt(live_campaign, monkeypatch, spend_usd=90, impressions=9000, clicks=60)
    assert c.daily_budget_usd == 30.0
    c, d = run_opt(live_campaign, monkeypatch, spend_usd=90, impressions=9000, clicks=60)
    assert c.daily_budget_usd == 30.0 and not d  # 24h cooldown: no second cut


def test_stock_out_pauses_even_without_auto_optimize(client, live_campaign, monkeypatch):
    db = SessionLocal()
    c = db.get(Campaign, live_campaign)
    c.auto_optimize = False
    p = c.product
    p.track_stock, p.stock = True, 0
    db.commit()
    db.close()
    c, d = run_opt(live_campaign, monkeypatch, spend_usd=1, impressions=100, clicks=1)
    assert c.status == "paused" and "المخزون" in c.status_reason
