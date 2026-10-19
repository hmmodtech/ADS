"""Exercise the Gemini code path with a fake LLM (no network): parsing, rewrite loop, fallback."""
from app.llm import LLM, LLMUnavailable
from conftest import H, wait_status


def test_llm_rewrite_loop_and_fallback(client, product, monkeypatch):
    calls = {"copy": 0}

    async def fake_json(self, prompt, system=None, temperature=0.8, retries=2):
        if "استراتيجي إعلانات" in prompt:
            return {"angle": "الكاميرا أولاً", "hooks": ["h1"], "key_points": ["200MP"], "per_country": {}}
        if "مدقق سياسات" in prompt:
            return {"issues": []}
        if "كاتب إعلانات" in prompt:
            calls["copy"] += 1
            if calls["copy"] == 1:  # first draft breaks the rules on purpose
                text = "ضمان مدى الحياة على جالكسي - الأفضل في العالم بسعر 4,299 ر.س"
            else:
                assert "ملاحظات مراجعة الامتثال" in prompt, "rewrite must receive compliance feedback"
                text = "جالكسي S24 ألترا بسعر 4,299 ر.س - اطلبه الآن"
            return {"ar": {"headline": "جالكسي S24 ألترا", "primary_text": text, "description": "جديد",
                           "cta": "SHOP_NOW", "hashtags": ["Samsung"]}}
        raise LLMUnavailable("unexpected prompt")

    monkeypatch.setattr(LLM, "json", fake_json)
    monkeypatch.setattr(LLM, "available", property(lambda self: True))
    cid = client.post("/api/campaigns", headers=H, json={"product_id": product["id"], "countries": ["SA"]}).json()["id"]
    c = wait_status(client, cid, {"pending_review", "blocked", "failed"})
    assert c["status"] == "pending_review", c["status_reason"]
    assert c["llm_used"] is True and calls["copy"] == 2
    assert "ضمان" not in c["copies"]["SA"]["ar"]["primary_text"]
    assert any("إعادة كتابة" in e["message"] for e in c["events"])


def test_llm_failure_falls_back_to_templates(client, product, monkeypatch):
    async def boom(self, *a, **k):
        raise LLMUnavailable("quota")

    monkeypatch.setattr(LLM, "json", boom)
    monkeypatch.setattr(LLM, "available", property(lambda self: True))
    cid = client.post("/api/campaigns", headers=H, json={"product_id": product["id"], "countries": ["SA"]}).json()["id"]
    c = wait_status(client, cid, {"pending_review", "blocked", "failed"})
    assert c["status"] == "pending_review" and c["llm_used"] is False
    assert c["copies"]["SA"]["ar"]["primary_text"]
