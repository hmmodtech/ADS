from ..config import get_settings
from .base import PlatformAdapter, PlatformError, PlatformNotConfigured
from .dryrun import DryRunAdapter
from .meta import MetaAdapter
from .tiktok import TikTokAdapter

SUPPORTED_PLATFORMS = ("meta", "tiktok")


def get_adapter(name: str) -> PlatformAdapter:
    if name not in SUPPORTED_PLATFORMS:
        raise PlatformError(f"منصة غير مدعومة: {name}")
    if get_settings().platform_mode == "dry_run":
        return DryRunAdapter(name)
    return MetaAdapter() if name == "meta" else TikTokAdapter()


def platform_status() -> dict:
    s = get_settings()
    return {
        "mode": s.platform_mode,
        "meta": MetaAdapter().configured(),
        "tiktok": TikTokAdapter().configured(),
    }


__all__ = ["get_adapter", "platform_status", "PlatformAdapter", "PlatformError", "PlatformNotConfigured",
           "SUPPORTED_PLATFORMS"]
