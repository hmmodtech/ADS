from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, utcnow


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("p_"))
    sku: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    external_id: Mapped[Optional[str]] = mapped_column(String, unique=True, nullable=True)
    source: Mapped[str] = mapped_column(String, default="manual")  # manual | store

    name_ar: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    name_en: Mapped[str] = mapped_column(String, nullable=False)
    brand: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    category: Mapped[str] = mapped_column(String, default="mobile_phone")
    condition: Mapped[str] = mapped_column(String, default="new")  # new | used | refurbished
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    specs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    images: Mapped[list[str]] = mapped_column(JSON, default=list)

    base_price: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String, default="SAR")
    # explicit per-country prices: {"SA": {"price": 3499, "currency": "SAR"}, ...}
    prices: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # Stock is OPTIONAL. With track_stock=False the engine never blocks on stock.
    track_stock: Mapped[bool] = mapped_column(Boolean, default=False)
    stock: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Facts the copywriter is ALLOWED to claim. Anything not here is flagged by compliance.
    warranty_text: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    offer_text: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # e.g. "توصيل مجاني"
    landing_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    campaigns: Mapped[list["Campaign"]] = relationship(back_populates="product")

    @property
    def display_name(self) -> str:
        return self.name_ar or self.name_en

    @property
    def in_stock(self) -> bool:
        if not self.track_stock:
            return True
        return (self.stock or 0) > 0


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("c_"))
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    name: Mapped[str] = mapped_column(String)
    # draft | generating | blocked | pending_review | approved | deploying | live | paused | failed | ended
    status: Mapped[str] = mapped_column(String, default="draft")
    status_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    objective: Mapped[str] = mapped_column(String, default="traffic")
    platforms: Mapped[list[str]] = mapped_column(JSON, default=list)
    countries: Mapped[list[str]] = mapped_column(JSON, default=list)
    languages: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["ar"])
    age_min: Mapped[int] = mapped_column(Integer, default=18)
    age_max: Mapped[int] = mapped_column(Integer, default=55)
    gender: Mapped[str] = mapped_column(String, default="all")  # all | male | female
    tone: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    creative_style: Mapped[str] = mapped_column(String, default="balanced")
    landing_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    daily_budget_usd: Mapped[float] = mapped_column(Float, default=20.0)
    initial_daily_budget_usd: Mapped[float] = mapped_column(Float, default=20.0)
    total_budget_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    auto_optimize: Mapped[bool] = mapped_column(Boolean, default=True)

    strategy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # copies[country][lang] = {headline, primary_text, description, cta, hashtags}
    copies: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    custom_copy: Mapped[bool] = mapped_column(Boolean, default=False)
    # assets: [{kind: image|video, country, ratio, path, url}]
    assets: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    compliance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    llm_used: Mapped[bool] = mapped_column(Boolean, default=False)

    # per platform: {"meta": {..ids.., "simulated": bool}}
    external_ids: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    spent_usd: Mapped[float] = mapped_column(Float, default=0.0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    conversions: Mapped[int] = mapped_column(Integer, default=0)
    metrics_simulated: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    launched_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_optimized_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    product: Mapped[Product] = relationship(back_populates="campaigns")
    events: Mapped[list["AgentEvent"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan", order_by="AgentEvent.id"
    )
    snapshots: Mapped[list["PerformanceSnapshot"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )


class AgentEvent(Base):
    """Audit trail: every agent action / decision is stored here."""

    __tablename__ = "agent_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    agent: Mapped[str] = mapped_column(String)
    level: Mapped[str] = mapped_column(String, default="info")  # info | warn | error | decision
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    campaign: Mapped[Campaign] = relationship(back_populates="events")


class PerformanceSnapshot(Base):
    __tablename__ = "performance_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    platform: Mapped[str] = mapped_column(String)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    spend_usd: Mapped[float] = mapped_column(Float, default=0.0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    conversions: Mapped[int] = mapped_column(Integer, default=0)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)

    campaign: Mapped[Campaign] = relationship(back_populates="snapshots")


class Setting(Base):
    """Small key/value store (used for the optional store connection)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
