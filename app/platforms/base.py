from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..models import Campaign, Product


class PlatformError(Exception):
    """The platform API rejected a call (message is safe to show to the user)."""


class PlatformNotConfigured(PlatformError):
    pass


@dataclass
class DeployResult:
    ok: bool
    ids: dict[str, Any] = field(default_factory=dict)
    detail: str = ""
    simulated: bool = False


@dataclass
class Metrics:
    spend_usd: float = 0.0
    impressions: int = 0
    clicks: int = 0
    conversions: int = 0
    simulated: bool = False

    @property
    def ctr_pct(self) -> float:
        return (self.clicks / self.impressions * 100) if self.impressions else 0.0

    @property
    def cpc_usd(self) -> float:
        return (self.spend_usd / self.clicks) if self.clicks else 0.0


def assets_for(campaign: Campaign, cc: str) -> dict[str, dict]:
    """Pick this country's creative files: image_1x1, image_9x16, video."""
    out: dict[str, dict] = {}
    for a in campaign.assets or []:
        if a.get("country") != cc:
            continue
        if a["kind"] == "image":
            out[f"image_{a['ratio'].replace(':', 'x')}"] = a
        elif a["kind"] == "video":
            out["video"] = a
    return out


def copy_for(campaign: Campaign, cc: str, lang: str | None = None) -> dict:
    langs = (campaign.copies or {}).get(cc) or {}
    lang = lang or (campaign.languages or ["ar"])[0]
    return langs.get(lang) or next(iter(langs.values()), {})


class PlatformAdapter(ABC):
    name = "base"
    simulated = False

    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    async def deploy(self, campaign: Campaign, product: Product, activate: bool) -> DeployResult: ...

    @abstractmethod
    async def set_active(self, campaign: Campaign, active: bool) -> None: ...

    @abstractmethod
    async def set_budget(self, campaign: Campaign, daily_budget_usd: float) -> None: ...

    @abstractmethod
    async def fetch_metrics(self, campaign: Campaign) -> Metrics: ...
