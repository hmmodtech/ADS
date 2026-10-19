"""Meta (Facebook + Instagram) Marketing API adapter.

Flow: upload media -> campaign (PAUSED unless activate) -> one ad set per country -> creative -> ad.
IMPORTANT: written against the public Marketing API docs; it has NOT been exercised against a live
account in this build. First run: PLATFORM_MODE=live, small budget, activate=false, inspect in Ads Manager.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .. import geo
from ..config import Settings, get_settings
from ..models import Campaign, Product
from .base import (DeployResult, Metrics, PlatformAdapter, PlatformError, PlatformNotConfigured,
                   assets_for, copy_for)

CONVERSION_ACTIONS = ("offsite_conversion.fb_pixel_purchase", "onsite_conversion.messaging_conversation_started_7d",
                      "offsite_conversion.fb_pixel_lead", "lead")


class MetaAdapter(PlatformAdapter):
    name = "meta"

    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()
        self.base = f"https://graph.facebook.com/{self.s.meta_api_version}"

    def configured(self) -> bool:
        return bool(self.s.meta_access_token and self.s.meta_ad_account_id and self.s.meta_page_id)

    def _require(self):
        if not self.configured():
            raise PlatformNotConfigured(
                "Meta غير مُهيّأ: اضبط META_ACCESS_TOKEN و META_AD_ACCOUNT_ID و META_PAGE_ID في ملف .env")

    @property
    def act(self) -> str:
        return f"act_{self.s.meta_ad_account_id.replace('act_', '')}"

    # ------------------------------------------------------------- http helpers
    async def _req(self, http: httpx.AsyncClient, method: str, path: str, *, data=None, files=None,
                   params=None) -> dict:
        params = dict(params or {})
        params["access_token"] = self.s.meta_access_token
        r = await http.request(method, f"{self.base}/{path.lstrip('/')}", data=data, files=files, params=params)
        try:
            body = r.json()
        except ValueError:
            raise PlatformError(f"Meta أرجعت ردّاً غير مفهوم (HTTP {r.status_code})")
        if r.status_code >= 400 or "error" in body:
            err = body.get("error", {})
            msg = err.get("error_user_msg") or err.get("message") or str(body)[:300]
            raise PlatformError(f"Meta: {msg}")
        return body

    @staticmethod
    def _minor(amount_usd: float, currency: str) -> int:
        return int(round(geo.from_usd(amount_usd, currency) * 100))

    # ------------------------------------------------------------------ deploy
    async def deploy(self, campaign: Campaign, product: Product, activate: bool) -> DeployResult:
        self._require()
        ids: dict = {"adsets": {}, "ads": {}, "creatives": {}, "active": activate}
        status = "ACTIVE" if activate else "PAUSED"
        per_country_usd = campaign.daily_budget_usd / max(len(campaign.countries), 1)
        async with httpx.AsyncClient(timeout=180) as http:
            camp = await self._req(http, "POST", f"{self.act}/campaigns", data={
                "name": f"{campaign.name} [{campaign.id}]",
                "objective": "OUTCOME_TRAFFIC",
                "status": status,
                "special_ad_categories": json.dumps([]),
                "is_adset_budget_sharing_enabled": "false",
            })
            ids["campaign_id"] = camp["id"]

            for cc in campaign.countries:
                files = assets_for(campaign, cc)
                copy = copy_for(campaign, cc)
                if not copy or "image_1x1" not in files:
                    continue
                image_hash = await self._upload_image(http, files["image_1x1"]["path"])
                video_id = None
                if "video" in files:
                    video_id = await self._upload_video(http, files["video"]["path"], f"{campaign.name}-{cc}")

                targeting = {
                    "geo_locations": {"countries": [cc]},
                    "age_min": campaign.age_min, "age_max": min(campaign.age_max, 65),
                    "publisher_platforms": ["facebook", "instagram"],
                    "targeting_automation": {"advantage_audience": 0},
                }
                if campaign.gender in ("male", "female"):
                    targeting["genders"] = [1 if campaign.gender == "male" else 2]
                adset = await self._req(http, "POST", f"{self.act}/adsets", data={
                    "name": f"{campaign.name} - {cc}",
                    "campaign_id": camp["id"],
                    "daily_budget": self._minor(per_country_usd, self.s.meta_account_currency),
                    "billing_event": "IMPRESSIONS",
                    "optimization_goal": "LINK_CLICKS",
                    "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
                    "destination_type": "WEBSITE",
                    "targeting": json.dumps(targeting),
                    "start_time": datetime.now(timezone.utc).isoformat(),
                    "status": "ACTIVE",  # the CAMPAIGN status is the master switch
                })
                ids["adsets"][cc] = adset["id"]

                cta = {"type": copy.get("cta", "SHOP_NOW"), "value": {"link": campaign.landing_url}}
                actor = {"page_id": self.s.meta_page_id}
                if self.s.meta_instagram_actor_id:
                    actor["instagram_actor_id"] = self.s.meta_instagram_actor_id
                if video_id:
                    spec = {**actor, "video_data": {
                        "video_id": video_id, "image_hash": image_hash,
                        "message": copy.get("primary_text", ""), "title": copy.get("headline", ""),
                        "link_description": copy.get("description", ""), "call_to_action": cta}}
                else:
                    spec = {**actor, "link_data": {
                        "link": campaign.landing_url, "image_hash": image_hash,
                        "message": copy.get("primary_text", ""), "name": copy.get("headline", ""),
                        "description": copy.get("description", ""), "call_to_action": cta}}
                creative = await self._req(http, "POST", f"{self.act}/adcreatives", data={
                    "name": f"{campaign.name} - {cc} creative", "object_story_spec": json.dumps(spec)})
                ids["creatives"][cc] = creative["id"]
                ad = await self._req(http, "POST", f"{self.act}/ads", data={
                    "name": f"{campaign.name} - {cc} ad", "adset_id": adset["id"],
                    "creative": json.dumps({"creative_id": creative["id"]}), "status": "ACTIVE"})
                ids["ads"][cc] = ad["id"]
        if not ids["adsets"]:
            raise PlatformError("لا توجد وسائط/نصوص صالحة لأي دولة")
        return DeployResult(True, ids, f"تم إنشاء الحملة على Meta ({'نشطة' if activate else 'متوقفة للمراجعة'})")

    async def _upload_image(self, http, path: str) -> str:
        with open(path, "rb") as f:
            body = await self._req(http, "POST", f"{self.act}/adimages", files={"filename": (Path(path).name, f)})
        return next(iter(body["images"].values()))["hash"]

    async def _upload_video(self, http, path: str, title: str) -> str:
        with open(path, "rb") as f:
            body = await self._req(http, "POST", f"{self.act}/advideos", data={"title": title},
                                   files={"source": (Path(path).name, f, "video/mp4")})
        vid = body["id"]
        for _ in range(40):  # wait until Meta finished processing (max ~2 min)
            st = await self._req(http, "GET", vid, params={"fields": "status"})
            if st.get("status", {}).get("video_status") == "ready":
                return vid
            await asyncio.sleep(3)
        return vid

    # -------------------------------------------------------- lifecycle / stats
    async def set_active(self, campaign: Campaign, active: bool) -> None:
        self._require()
        cid = (campaign.external_ids.get("meta") or {}).get("campaign_id")
        if not cid:
            raise PlatformError("لا يوجد معرّف حملة Meta")
        async with httpx.AsyncClient(timeout=60) as http:
            await self._req(http, "POST", cid, data={"status": "ACTIVE" if active else "PAUSED"})

    async def set_budget(self, campaign: Campaign, daily_budget_usd: float) -> None:
        self._require()
        adsets = (campaign.external_ids.get("meta") or {}).get("adsets", {})
        if not adsets:
            return
        per = daily_budget_usd / len(adsets)
        async with httpx.AsyncClient(timeout=60) as http:
            for adset_id in adsets.values():
                await self._req(http, "POST", adset_id,
                                data={"daily_budget": self._minor(per, self.s.meta_account_currency)})

    async def fetch_metrics(self, campaign: Campaign) -> Metrics:
        self._require()
        cid = (campaign.external_ids.get("meta") or {}).get("campaign_id")
        if not cid:
            return Metrics()
        async with httpx.AsyncClient(timeout=60) as http:
            body = await self._req(http, "GET", f"{cid}/insights",
                                   params={"fields": "spend,impressions,clicks,actions", "date_preset": "maximum"})
        rows = body.get("data") or []
        if not rows:
            return Metrics()
        r = rows[0]
        conv = sum(int(float(a["value"])) for a in r.get("actions", []) if a.get("action_type") in CONVERSION_ACTIONS)
        return Metrics(
            spend_usd=round(geo.to_usd(float(r.get("spend", 0)), self.s.meta_account_currency), 2),
            impressions=int(r.get("impressions", 0)), clicks=int(r.get("clicks", 0)), conversions=conv)
