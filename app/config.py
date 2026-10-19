"""Central configuration. Every value can be overridden from the environment / .env file."""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Autonomous Ad Engine"
    version: str = "2.0.0"

    # ---- storage -------------------------------------------------------------
    data_dir: Path = Path("./data")
    database_url: str = ""  # empty -> sqlite file inside data_dir

    # ---- security ------------------------------------------------------------
    admin_api_key: str = ""  # empty -> generated on first start into data/.admin_key
    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"
    allow_private_store_urls: bool = False  # SSRF guard for the store connector

    # ---- AI ------------------------------------------------------------------
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    # ---- safety rails --------------------------------------------------------
    platform_mode: Literal["dry_run", "live"] = "dry_run"
    auto_approve: bool = False          # skip the human review gate (NOT recommended)
    max_daily_budget_usd: float = 200.0  # hard cap per campaign
    max_cpc_usd: float = 1.0             # optimizer: above this CPC -> reduce budget
    min_ctr_pct: float = 0.4             # optimizer: below this CTR -> pause
    optimizer_interval_min: int = 60
    store_sync_interval_min: int = 0     # 0 = manual sync only
    scheduler_enabled: bool = True

    # ---- landing page --------------------------------------------------------
    whatsapp_number: str = ""            # digits only, with country code e.g. 9665XXXXXXXX
    default_landing_url: str = ""

    # ---- Meta (Facebook / Instagram) ----------------------------------------
    meta_access_token: str = ""
    meta_ad_account_id: str = ""         # digits only (without act_)
    meta_page_id: str = ""
    meta_instagram_actor_id: str = ""
    meta_account_currency: str = "USD"
    meta_api_version: str = "v21.0"

    # ---- TikTok --------------------------------------------------------------
    tiktok_access_token: str = ""
    tiktok_advertiser_id: str = ""
    tiktok_identity_id: str = ""
    tiktok_account_currency: str = "USD"

    # ---- creative ------------------------------------------------------------
    font_path: str = ""                  # path to a TTF with Arabic glyphs (optional)
    music_path: str = ""                 # optional background music (mp3/m4a)
    video_seconds_per_scene: int = 4

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(self.data_dir / 'ad_engine.db').resolve()}"

    @property
    def media_dir(self) -> Path:
        p = self.data_dir / "media"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def llm_enabled(self) -> bool:
        return bool(self.gemini_api_key and not self.gemini_api_key.startswith("your_"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
