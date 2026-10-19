"""API-key authentication. All /api routes require the key; the dashboard prompts for it."""
import os
import secrets
from functools import lru_cache

from fastapi import Header, HTTPException, status

from .config import get_settings


@lru_cache
def get_admin_key() -> str:
    s = get_settings()
    if s.admin_api_key:
        return s.admin_api_key
    s.data_dir.mkdir(parents=True, exist_ok=True)
    key_file = s.data_dir / ".admin_key"
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_urlsafe(32)
    key_file.write_text(key)
    try:
        os.chmod(key_file, 0o600)
    except OSError:
        pass
    return key


def require_api_key(x_api_key: str = Header(default="")) -> None:
    if not x_api_key or not secrets.compare_digest(x_api_key, get_admin_key()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "مفتاح API غير صالح")
