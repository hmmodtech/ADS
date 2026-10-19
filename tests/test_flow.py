import io
import os

import pytest
from PIL import Image

from conftest import H, wait_status


def make_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (600, 900), (30, 144, 255, 255)).save(buf, "PNG")
    return buf.getvalue()


def test_auth_required(client):
    assert client.get("/health").status_code == 200
    assert client.get("/api/products").status_code == 401
    assert client.get("/api/products", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/api/products", headers=H).status_code == 200


def test_product_image_upload_and_validation(client, product):
    pid = product["id"]
    r = client.post(f"/api/products/{pid}/images", headers=H, files={"file": ("a.png", make_png(), "image/png")})
    assert r.status_code == 200 and r.json()["images"][0].startswith(f"/uploads/{pid}/")
    bad = client.post(f"/api/products/{pid}/images", headers=H, files={"file": ("a.png", b"not an image", "image/png")})
    assert bad.status_code == 415
    assert client.get(r.json()["images"][0]).status_code == 200


def test_full_manual_pipeline_without_store(client, product):
    """The core promise: ads work with NO store connected."""
    client.post(f"/api/products/{product['id']}/images", headers=H, files={"file": ("a.png", make_png(), "image/png")})
    r = client.post("/api/campaigns", headers=H, json={
        "product_id": product["id"], "platforms": ["meta", "tiktok"], "countries": ["SA", "AE", "KW"],
        "languages": ["ar", "en"], "daily_budget_usd": 30, "creative_style": "price"})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    c = wait_status(client, cid, {"pending_review", "blocked", "failed"})
    assert c["status"] == "pending_review", c["status_reason"]
    assert c["compliance"]["passed"] is True
    assert c["landing_url"].startswith("https://wa.me/966500000000")

    # per-country dialect copy, both languages, correct per-country prices
    assert set(c["copies"]) == {"SA", "AE", "KW"}
    assert "4,299 ر.س" in c["copies"]["SA"]["ar"]["primary_text"]
    assert "4,199 د.إ" in c["copies"]["AE"]["ar"]["primary_text"]
    assert "ر.س" not in c["copies"]["AE"]["ar"]["primary_text"]
    assert c["copies"]["KW"]["en"]["primary_text"]
    assert not c["llm_used"]

    # real media files exist: 2 images + 1 video per country
    kinds = [(a["country"], a["kind"], a["ratio"]) for a in c["assets"]]
    assert len(kinds) == 9
    for a in c["assets"]:
        resp = client.get(a["url"])
        assert resp.status_code == 200 and len(resp.content) > 2000
        if a["kind"] == "video":
            assert resp.headers["content-type"] == "video/mp4"

    # approving does NOT spend: created paused, clearly flagged simulated
    ap = client.post(f"/api/campaigns/{cid}/approve", headers=H, json={"activate": False})
    assert ap.status_code == 200, ap.text
    assert ap.json()["status"] == "paused" and ap.json()["simulated_deploy"] is True
    assert ap.json()["external_ids"]["meta"]["campaign_id"].startswith("dryrun_")
    # second approve is idempotent (no duplicate platform campaigns)
    first_id = ap.json()["external_ids"]["meta"]["campaign_id"]
    assert client.post(f"/api/campaigns/{cid}/resume", headers=H).json()["status"] == "live"
    detail = client.post(f"/api/campaigns/{cid}/refresh", headers=H).json()
    assert detail["metrics_simulated"] is True
    assert detail["external_ids"]["meta"]["campaign_id"] == first_id
    assert client.post(f"/api/campaigns/{cid}/pause", headers=H).json()["status"] == "paused"

    z = client.get(f"/api/campaigns/{cid}/export", headers=H)
    assert z.status_code == 200 and z.content[:2] == b"PK"


def test_compliance_blocks_and_manual_fix(client, product):
    bad = {"SA": {"ar": {"headline": "جالكسي", "primary_text": "ضمان مدى الحياة وأفضل من آيفون بسعر 999 ر.س! ينفد قريباً",
                         "description": "", "cta": "SHOP_NOW", "hashtags": []}}}
    r = client.post("/api/campaigns", headers=H, json={
        "product_id": product["id"], "countries": ["SA"], "custom_copy": bad})
    cid = r.json()["id"]
    c = wait_status(client, cid, {"blocked", "pending_review", "failed"})
    assert c["status"] == "blocked"
    codes = {i["code"] for i in c["compliance"]["issues"]}
    assert {"unverified_warranty", "competitor_brand", "price_mismatch", "fake_scarcity"} <= codes
    # a blocked campaign cannot be approved
    assert client.post(f"/api/campaigns/{cid}/approve", headers=H, json={}).status_code == 409
    # fix by hand -> re-checked -> ready
    good = {"copies": {"SA": {"ar": {"headline": "جالكسي S24 ألترا", "primary_text": "جالكسي S24 ألترا بسعر 4,299 ر.س • اطلبه الآن",
                                     "description": "جديد", "cta": "SHOP_NOW", "hashtags": []}}}}
    assert client.put(f"/api/campaigns/{cid}/copies", headers=H, json=good).status_code == 200
    c = wait_status(client, cid, {"pending_review", "blocked", "failed"})
    assert c["status"] == "pending_review", c["status_reason"]


def test_out_of_stock_is_blocked_only_when_tracked(client):
    mk = lambda **kw: client.post("/api/products", headers=H, json={"name_en": "Phone X", "base_price": 1000, **kw}).json()
    tracked = mk(track_stock=True, stock=0)
    untracked = mk(track_stock=False, stock=0)
    c1 = client.post("/api/campaigns", headers=H, json={"product_id": tracked["id"], "countries": ["SA"]}).json()
    c2 = client.post("/api/campaigns", headers=H, json={"product_id": untracked["id"], "countries": ["SA"]}).json()
    assert wait_status(client, c1["id"], {"blocked", "pending_review", "failed"})["status"] == "blocked"
    assert wait_status(client, c2["id"], {"blocked", "pending_review", "failed"})["status"] == "pending_review"


def test_budget_cap_and_validation(client, product):
    base = {"product_id": product["id"]}
    assert client.post("/api/campaigns", headers=H, json={**base, "daily_budget_usd": 5000}).status_code == 422
    assert client.post("/api/campaigns", headers=H, json={**base, "countries": ["EG"]}).status_code == 422
    assert client.post("/api/campaigns", headers=H, json={**base, "platforms": ["myspace"]}).status_code == 422
    assert client.post("/api/campaigns", headers=H, json={**base, "age_min": 40, "age_max": 20}).status_code == 422


def test_store_connector_blocks_internal_urls(client):
    r = client.post("/api/store/connect", headers=H, json={"type": "custom", "url": "http://127.0.0.1:8000"})
    assert r.status_code == 400
    r = client.post("/api/store/connect", headers=H, json={"type": "custom", "url": "http://169.254.169.254/latest"})
    assert r.status_code == 400
    assert client.get("/api/store", headers=H).json()["connected"] is False


def test_dashboard(client):
    d = client.get("/api/dashboard", headers=H).json()
    assert d["system"]["platform_mode"] == "dry_run" and d["system"]["llm"] is False
    assert d["products"] >= 1
