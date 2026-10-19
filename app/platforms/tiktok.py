"""TikTok Business API adapter (Marketing API v1.3).

Flow: upload video + cover -> campaign -> ad group per country -> ad.
IMPORTANT: written against the public docs; NOT exercised against a live account in this build.
Run first with PLATFORM_MODE=live, a tiny budget and activate=false, then inspect in TikTok Ads Manager.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from .. import geo
from ..config import Settings, get_settings
from ..models import Campaign, Product
from .base import (DeployResult, Metrics, PlatformAdapter, PlatformError, PlatformNotConfigured,
                   assets_for, copy_for)

BASE = "https://business-api.tiktok.com/open_api/v1.3"


def _age_groups(lo: int, hi: int) -> list[str]:
    bands = [("AGE_18_24", 18, 24), ("AGE_25_34", 25, 34), ("AGE_35_44", 35, 44),
             ("AGE_45_54", 45, 54), ("AGE_55_100", 55, 100)]
    return [n for n, a, b in bands if b >= lo and a <= hi] or ["AGE_18_24"]


class TikTokAdapter(PlatformAdapter):
    name = "tiktok"

    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()
        self._regions: dict[str, str] | None = None

    def configured(self) -> bool:
        return bool(self.s.tiktok_access_token and self.s.tiktok_advertiser_id and self.s.tiktok_identity_id)

    def _require(self):
        if not self.configured():
            raise PlatformNotConfigured(
                "TikTok غير مُهيّأ: اضبط TIKTOK_ACCESS_TOKEN و TIKTOK_ADVERTISER_ID و TIKTOK_IDENTITY_ID في .env")

    async def _req(self, http: httpx.AsyncClient, method: str, path: str, *, json_body=None, data=None,
                   files=None, params=None) -> dict:
        r = await http.request(method, f"{BASE}/{path.lstrip('/')}", json=json_body, data=data, files=files,
                               params=params, headers={"Access-Token": self.s.tiktok_access_token})
        try:
            body = r.json()
        except ValueError:
            raise PlatformError(f"TikTok أرجعت ردّاً غير مفهوم (HTTP {r.status_code})")
        if body.get("code") != 0:
            raise PlatformError(f"TikTok: {body.get('message', body)}")
        return body.get("data") or {}

    async def _region_id(self, http, cc: str) -> str:
        if self._regions is None:
            data = await self._req(http, "GET", "tool/region/", params={
                "advertiser_id": self.s.tiktok_advertiser_id, "placements": json.dumps(["PLACEMENT_TIKTOK"]),
                "objective_type": "TRAFFIC", "level_range": "COUNTRY_OR_AREA"})
            self._regions = {r.get("region_code") or r.get("country_code", ""): str(r["location_id"])
                             for r in data.get("region_info", []) if r.get("location_id")}
        if cc not in self._regions:
            raise PlatformError(f"TikTok لا تدعم استهداف {cc} لهذا الحساب")
        return self._regions[cc]

    async def deploy(self, campaign: Campaign, product: Product, activate: bool) -> DeployResult:
        self._require()
        adv = self.s.tiktok_advertiser_id
        op = "ENABLE" if activate else "DISABLE"
        ids: dict = {"adgroups": {}, "ads": {}, "active": activate}
        per_country_usd = campaign.daily_budget_usd / max(len(campaign.countries), 1)
        async with httpx.AsyncClient(timeout=180) as http:
            camp = await self._req(http, "POST", "campaign/create/", json_body={
                "advertiser_id": adv, "campaign_name": f"{campaign.name} [{campaign.id}]",
                "objective_type": "TRAFFIC", "budget_mode": "BUDGET_MODE_INFINITE", "operation_status": op})
            ids["campaign_id"] = camp["campaign_id"]
            start = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")

            for cc in campaign.countries:
                files, copy = assets_for(campaign, cc), copy_for(campaign, cc)
                if "video" not in files or not copy:
                    continue
                video_id = await self._upload_file(http, "video", files["video"]["path"])
                cover_id = await self._upload_file(http, "image", files["image_9x16"]["path"])
                ag = await self._req(http, "POST", "adgroup/create/", json_body={
                    "advertiser_id": adv, "campaign_id": camp["campaign_id"],
                    "adgroup_name": f"{campaign.name} - {cc}",
                    "placement_type": "PLACEMENT_TYPE_NORMAL", "placements": ["PLACEMENT_TIKTOK"],
                    "promotion_type": "WEBSITE", "location_ids": [await self._region_id(http, cc)],
                    "age_groups": _age_groups(campaign.age_min, campaign.age_max),
                    **({"gender": "GENDER_MALE" if campaign.gender == "male" else "GENDER_FEMALE"}
                       if campaign.gender in ("male", "female") else {"gender": "GENDER_UNLIMITED"}),
                    "budget_mode": "BUDGET_MODE_DAY",
                    "budget": round(geo.from_usd(per_country_usd, self.s.tiktok_account_currency), 2),
                    "schedule_type": "SCHEDULE_FROM_NOW", "schedule_start_time": start,
                    "optimization_goal": "CLICK", "billing_event": "CPC", "bid_type": "BID_TYPE_NO_BID",
                    "pacing": "PACING_MODE_SMOOTH", "operation_status": op})
                ids["adgroups"][cc] = ag["adgroup_id"]
                ad = await self._req(http, "POST", "ad/create/", json_body={
                    "advertiser_id": adv, "adgroup_id": ag["adgroup_id"],
                    "creatives": [{
                        "ad_name": f"{campaign.name} - {cc}", "ad_text": copy.get("primary_text", "")[:100],
                        "call_to_action": "SHOP_NOW", "landing_page_url": campaign.landing_url,
                        "video_id": video_id, "image_ids": [cover_id],
                        "identity_id": self.s.tiktok_identity_id, "identity_type": "CUSTOMIZED_USER",
                        "display_name": (product.brand or product.name_en)[:40]}]})
                ids["ads"][cc] = (ad.get("ad_ids") or [None])[0]
        if not ids["adgroups"]:
            raise PlatformError("لا توجد وسائط/نصوص صالحة لأي دولة")
        return DeployResult(True, ids, f"تم إنشاء الحملة على TikTok ({'نشطة' if activate else 'متوقفة للمراجعة'})")

    async def _upload_file(self, http, kind: str, path: str) -> str:
        raw = Path(path).read_bytes()
        sig = hashlib.md5(raw).hexdigest()
        adv = self.s.tiktok_advertiser_id
        if kind == "video":
            data = await self._req(http, "POST", "file/video/ad/upload/",
                                   data={"advertiser_id": adv, "upload_type": "UPLOAD_BY_FILE", "video_signature": sig},
                                   files={"video_file": (Path(path).name, raw, "video/mp4")})
            item = data[0] if isinstance(data, list) else data
            return item["video_id"]
        data = await self._req(http, "POST", "file/image/ad/upload/",
                               data={"advertiser_id": adv, "upload_type": "UPLOAD_BY_FILE", "image_signature": sig},
                               files={"image_file": (Path(path).name, raw, "image/png")})
        item = data[0] if isinstance(data, list) else data
        return item["image_id"]

    async def set_active(self, campaign: Campaign, active: bool) -> None:
        self._require()
        ids = campaign.external_ids.get("tiktok") or {}
        if not ids.get("campaign_id"):
            raise PlatformError("لا يوجد معرّف حملة TikTok")
        op = "ENABLE" if active else "DISABLE"
        async with httpx.AsyncClient(timeout=60) as http:
            await self._req(http, "POST", "campaign/status/update/", json_body={
                "advertiser_id": self.s.tiktok_advertiser_id, "campaign_ids": [ids["campaign_id"]],
                "operation_status": op})
            if ids.get("adgroups"):
                await self._req(http, "POST", "adgroup/status/update/", json_body={
                    "advertiser_id": self.s.tiktok_advertiser_id, "adgroup_ids": list(ids["adgroups"].values()),
                    "operation_status": op})

    async def set_budget(self, campaign: Campaign, daily_budget_usd: float) -> None:
        self._require()
        groups = (campaign.external_ids.get("tiktok") or {}).get("adgroups", {})
        if not groups:
            return
        per = round(geo.from_usd(daily_budget_usd / len(groups), self.s.tiktok_account_currency), 2)
        async with httpx.AsyncClient(timeout=60) as http:
            for gid in groups.values():
                await self._req(http, "POST", "adgroup/update/", json_body={
                    "advertiser_id": self.s.tiktok_advertiser_id, "adgroup_id": gid, "budget": per})

    async def fetch_metrics(self, campaign: Campaign) -> Metrics:
        self._require()
        cid = (campaign.external_ids.get("tiktok") or {}).get("campaign_id")
        if not cid:
            return Metrics()
        async with httpx.AsyncClient(timeout=60) as http:
            data = await self._req(http, "GET", "report/integrated/get/", params={
                "advertiser_id": self.s.tiktok_advertiser_id, "report_type": "BASIC",
                "data_level": "AUCTION_CAMPAIGN", "dimensions": json.dumps(["campaign_id"]),
                "metrics": json.dumps(["spend", "impressions", "clicks", "conversion"]),
                "filtering": json.dumps([{"field_name": "campaign_ids", "filter_type": "IN",
                                          "filter_value": json.dumps([cid])}]),
                "query_lifetime": "true"})
        rows = data.get("list") or []
        if not rows:
            return Metrics()
        m = rows[0].get("metrics", {})
        return Metrics(
            spend_usd=round(geo.to_usd(float(m.get("spend", 0)), self.s.tiktok_account_currency), 2),
            impressions=int(float(m.get("impressions", 0))), clicks=int(float(m.get("clicks", 0))),
            conversions=int(float(m.get("conversion", 0))))
