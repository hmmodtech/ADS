import os
import tempfile
import time

_tmp = tempfile.mkdtemp(prefix="adengine_test_")
os.environ.update({
    "DATA_DIR": _tmp, "ADMIN_API_KEY": "testkey", "SCHEDULER_ENABLED": "false", "GEMINI_API_KEY": "",
    "PLATFORM_MODE": "dry_run", "WHATSAPP_NUMBER": "966500000000", "DATABASE_URL": "",
    "MAX_DAILY_BUDGET_USD": "200", "VIDEO_SECONDS_PER_SCENE": "2",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

H = {"X-API-Key": "testkey"}


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def product(client):
    r = client.post("/api/products", headers=H, json={
        "name_en": "Galaxy S24 Ultra", "name_ar": "جالكسي S24 ألترا", "brand": "Samsung",
        "specs": {"الشاشة": "6.8 إنش Dynamic AMOLED", "التخزين": "256GB", "الكاميرا": "كاميرا 200 ميجابكسل"},
        "base_price": 4299, "currency": "SAR",
        "prices": {"AE": {"price": 4199, "currency": "AED"}},
    })
    assert r.status_code == 201, r.text
    return r.json()


def wait_status(client, cid, targets, timeout=90):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = client.get(f"/api/campaigns/{cid}", headers=H).json()
        if last["status"] in targets and not last["busy"]:
            return last
        time.sleep(0.5)
    raise AssertionError(f"timeout; last status={last and last['status']} reason={last and last['status_reason']}")
