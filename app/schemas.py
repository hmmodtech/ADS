from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .config import get_settings
from .geo import COUNTRIES, USD_RATES
from .platforms import SUPPORTED_PLATFORMS


class CountryPrice(BaseModel):
    price: float = Field(gt=0)
    currency: Optional[str] = None


class ProductIn(BaseModel):
    name_en: str = Field(min_length=1, max_length=200)
    name_ar: Optional[str] = Field(default=None, max_length=200)
    sku: Optional[str] = Field(default=None, max_length=80)
    brand: Optional[str] = Field(default=None, max_length=80)
    category: str = "mobile_phone"
    condition: Literal["new", "used", "refurbished"] = "new"
    description: Optional[str] = Field(default=None, max_length=2000)
    specs: dict[str, Any] = Field(default_factory=dict)
    base_price: float = Field(default=0, ge=0)
    currency: str = "SAR"
    prices: dict[str, CountryPrice] = Field(default_factory=dict)
    track_stock: bool = False
    stock: Optional[int] = Field(default=None, ge=0)
    warranty_text: Optional[str] = Field(default=None, max_length=200)
    offer_text: Optional[str] = Field(default=None, max_length=200)
    landing_url: Optional[str] = Field(default=None, max_length=500)
    active: bool = True

    @field_validator("currency")
    @classmethod
    def _cur(cls, v):
        v = v.upper()
        if v not in USD_RATES:
            raise ValueError(f"عملة غير مدعومة: {v}")
        return v

    @field_validator("prices")
    @classmethod
    def _prices(cls, v):
        bad = [k for k in v if k.upper() not in COUNTRIES]
        if bad:
            raise ValueError(f"دول غير مدعومة في الأسعار: {bad}")
        return {k.upper(): p for k, p in v.items()}

    @field_validator("landing_url")
    @classmethod
    def _url(cls, v):
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("الرابط يجب أن يبدأ بـ http:// أو https://")
        return v or None


class ProductPatch(BaseModel):
    """All fields optional - only the provided ones change."""
    model_config = {"extra": "forbid"}
    name_en: Optional[str] = Field(default=None, min_length=1, max_length=200)
    name_ar: Optional[str] = None
    sku: Optional[str] = None
    brand: Optional[str] = None
    category: Optional[str] = None
    condition: Optional[Literal["new", "used", "refurbished"]] = None
    description: Optional[str] = None
    specs: Optional[dict[str, Any]] = None
    base_price: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = None
    prices: Optional[dict[str, CountryPrice]] = None
    track_stock: Optional[bool] = None
    stock: Optional[int] = Field(default=None, ge=0)
    warranty_text: Optional[str] = None
    offer_text: Optional[str] = None
    landing_url: Optional[str] = None
    active: Optional[bool] = None


class CampaignIn(BaseModel):
    product_id: str
    name: Optional[str] = Field(default=None, max_length=120)
    platforms: list[str] = ["meta"]
    countries: list[str] = ["SA", "AE"]
    languages: list[Literal["ar", "en"]] = ["ar"]
    objective: Literal["traffic"] = "traffic"
    daily_budget_usd: float = Field(default=20.0, gt=0)
    total_budget_usd: Optional[float] = Field(default=None, gt=0)
    age_min: int = Field(default=18, ge=18, le=65)
    age_max: int = Field(default=55, ge=18, le=65)
    gender: Literal["all", "male", "female"] = "all"
    tone: Optional[str] = Field(default=None, max_length=120)
    notes: Optional[str] = Field(default=None, max_length=1000)
    creative_style: Literal["balanced", "price", "features", "lifestyle"] = "balanced"
    landing_url: Optional[str] = Field(default=None, max_length=500)
    auto_optimize: bool = True
    custom_copy: Optional[dict[str, dict[str, dict[str, Any]]]] = None

    @field_validator("platforms")
    @classmethod
    def _platforms(cls, v):
        v = list(dict.fromkeys(p.lower() for p in v))
        if not v or any(p not in SUPPORTED_PLATFORMS for p in v):
            raise ValueError(f"المنصات المدعومة: {list(SUPPORTED_PLATFORMS)}")
        return v

    @field_validator("countries")
    @classmethod
    def _countries(cls, v):
        v = list(dict.fromkeys(c.upper() for c in v))
        if not v or any(c not in COUNTRIES for c in v):
            raise ValueError(f"الدول المدعومة: {list(COUNTRIES)}")
        return v

    @field_validator("daily_budget_usd")
    @classmethod
    def _budget(cls, v):
        cap = get_settings().max_daily_budget_usd
        if v > cap:
            raise ValueError(f"الميزانية اليومية تتجاوز الحد الأقصى {cap:g}$ (MAX_DAILY_BUDGET_USD)")
        return v

    @field_validator("landing_url")
    @classmethod
    def _url(cls, v):
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("الرابط يجب أن يبدأ بـ http:// أو https://")
        return v or None

    @model_validator(mode="after")
    def _ages(self):
        if self.age_max < self.age_min:
            raise ValueError("العمر الأقصى أصغر من الأدنى")
        return self


class CopyBlock(BaseModel):
    headline: str = Field(default="", max_length=120)
    primary_text: str = Field(min_length=1, max_length=400)
    description: str = Field(default="", max_length=120)
    cta: Literal["SHOP_NOW", "ORDER_NOW", "LEARN_MORE", "CONTACT_US"] = "SHOP_NOW"
    hashtags: list[str] = []


class CopiesIn(BaseModel):
    copies: dict[str, dict[Literal["ar", "en"], CopyBlock]]


class ApproveIn(BaseModel):
    activate: bool = False


class BudgetIn(BaseModel):
    daily_budget_usd: float = Field(gt=0)

    @field_validator("daily_budget_usd")
    @classmethod
    def _cap(cls, v):
        cap = get_settings().max_daily_budget_usd
        if v > cap:
            raise ValueError(f"الحد الأقصى {cap:g}$")
        return v


class StoreConnectIn(BaseModel):
    type: Literal["custom", "woocommerce", "shopify"] = "custom"
    url: str
    api_key: Optional[str] = None
    currency: str = "SAR"

    @field_validator("url")
    @classmethod
    def _url(cls, v):
        v = v.strip().rstrip("/")
        if not v.startswith(("http://", "https://")):
            raise ValueError("الرابط يجب أن يبدأ بـ http:// أو https://")
        return v
