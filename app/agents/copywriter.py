"""Agent 2 - Copywriter: one dialect-correct ad copy set PER COUNTRY (and per language)."""
from __future__ import annotations

import asyncio
import json

from sqlalchemy.orm import Session

from .. import geo
from ..llm import LLMUnavailable
from ..models import Campaign, Product
from .base import BaseAgent
from .facts import facts_for

CTA_TYPES = ["SHOP_NOW", "ORDER_NOW", "LEARN_MORE", "CONTACT_US"]
LIMITS = {"headline": 40, "primary_text": 125, "description": 30}
TIKTOK_TEXT_LIMIT = 100


def _fit(parts: list[str], sep: str, limit: int) -> str:
    """Join as many parts as fit in `limit` characters (never cuts a part in half)."""
    out = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        cand = f"{out}{sep}{p}" if out else p
        if len(cand) <= limit:
            out = cand
        elif not out:
            out = p[: limit - 1].rstrip() + "…"
    return out


def _compose(items: list[tuple[str, int]], sep: str, limit: int) -> str:
    """items = [(text, priority)] in DISPLAY order. Lower priority number = more important.
    Keeps the most important parts that fit in `limit`, then renders them in display order."""
    keep: set[int] = set()
    used = 0
    for idx in sorted(range(len(items)), key=lambda i: (items[i][1], i)):
        text = items[idx][0].strip()
        if not text:
            continue
        add = len(text) + (len(sep) if keep else 0)
        if used + add <= limit:
            keep.add(idx)
            used += add
    return sep.join(items[i][0].strip() for i in sorted(keep))


def _limits(campaign: Campaign) -> dict:
    lim = dict(LIMITS)
    if "tiktok" in campaign.platforms:
        lim["primary_text"] = TIKTOK_TEXT_LIMIT
    return lim


class CopywriterAgent(BaseAgent):
    name = "copywriter"
    label = "وكيل النصوص"

    async def run(self, db: Session, campaign: Campaign, product: Product, strategy: dict,
                  feedback: list[dict] | None = None) -> dict:
        tasks = [self._country_copy(campaign, product, strategy, cc, feedback)
                 for cc in campaign.countries]
        results = await asyncio.gather(*tasks)
        copies = {}
        for cc, (data, used_llm) in zip(campaign.countries, results):
            copies[cc] = data
            if used_llm:
                campaign.llm_used = True
            else:
                self.log(db, campaign.id, f"{cc}: استُخدم القالب الآمن (بدون Gemini)", "info")
        self.log(db, campaign.id, f"تمت كتابة النصوص لـ {len(copies)} دولة", "info",
                 revised=bool(feedback))
        return copies

    # ------------------------------------------------------------------ LLM path
    async def _country_copy(self, campaign, product, strategy, cc, feedback):
        facts = facts_for(product, campaign, cc)
        if self.llm.available:
            try:
                return await self._llm_copy(campaign, strategy, facts, feedback), True
            except LLMUnavailable:
                pass  # fall through to the facts-only template
        return self._template_copy(campaign, strategy, facts), False

    async def _llm_copy(self, campaign, strategy, facts, feedback) -> dict:
        cc = facts["country_code"]
        c = geo.country(cc)
        lim = _limits(campaign)
        langs = campaign.languages or ["ar"]
        fb = ""
        if feedback:
            fb = ("\nملاحظات مراجعة الامتثال السابقة - عالجها كلها:\n"
                  + "\n".join(f"- {i['message']}" for i in feedback))
        prompt = f"""أنت كاتب إعلانات محترف (Senior Performance Copywriter) لسوق {c['ar']}.
اللهجة المطلوبة: {c['dialect']}
النبرة: {campaign.tone or 'ودّية وعصرية وموثوقة'}
الزاوية الاستراتيجية: {strategy.get('angle')}
افتتاحيات مقترحة: {strategy.get('hooks')}
ملاحظات العميل: {campaign.notes or '-'}

الحقائق المسموح بذكرها فقط (لا تخترع غيرها):
{json.dumps(facts, ensure_ascii=False, indent=1)}

قواعد صارمة:
1. لا تذكر أي ضمان إلا إذا كان "warranty" غير فارغ، ولا تذكر "مجاني" إلا إذا ورد في "offer".
2. لا تستخدم عبارات الندرة (آخر القطع، الكمية محدودة، ينفد قريباً) إلا إذا scarcity_allowed = true.
3. لا تقارن بمنتجات أو ماركات أخرى، ولا تستخدم "الأفضل في العالم" أو "رقم 1".
4. إذا ذكرت السعر استخدم price_ar (أو price_en) كما هو بالضبط.
5. الحدود: headline ≤ {lim['headline']} حرفاً، primary_text ≤ {lim['primary_text']} حرفاً، description ≤ {lim['description']} حرفاً.
6. افتتاحية تشدّ الانتباه + فائدة حقيقية + دعوة واضحة. إيموجي واحد أو اثنان كحد أقصى.
7. cta واحد من: {CTA_TYPES}.{fb}

أرجع JSON فقط بهذا الشكل (المفاتيح للغات المطلوبة فقط: {langs}):
{{"ar": {{"headline": "", "primary_text": "", "description": "", "cta": "SHOP_NOW", "hashtags": ["", ""]}},
  "en": {{...}}}}"""
        data = await self.llm.json(prompt, temperature=0.9)
        if not isinstance(data, dict):
            raise LLMUnavailable("صيغة نصوص غير صالحة")
        out = {}
        for lang in langs:
            block = data.get(lang)
            if not isinstance(block, dict) or not block.get("primary_text"):
                raise LLMUnavailable(f"نسخة {lang} ناقصة")
            out[lang] = self._normalize(block, lim)
        return out

    @staticmethod
    def _normalize(block: dict, lim: dict) -> dict:
        cta = str(block.get("cta", "SHOP_NOW")).upper()
        return {
            "headline": str(block.get("headline", ""))[: lim["headline"] + 20].strip(),
            "primary_text": str(block.get("primary_text", "")).strip(),
            "description": str(block.get("description", ""))[: lim["description"] + 20].strip(),
            "cta": cta if cta in CTA_TYPES else "SHOP_NOW",
            "hashtags": [str(h).lstrip("#").strip() for h in (block.get("hashtags") or [])][:5],
        }

    # ------------------------------------------------------------- template path
    def _template_copy(self, campaign, strategy, f) -> dict:
        lim = _limits(campaign)
        c = geo.country(f["country_code"])
        out = {}
        style = campaign.creative_style
        opener = c["openers"][sum(map(ord, campaign.id)) % len(c["openers"])]

        if "ar" in (campaign.languages or ["ar"]):
            name = f["product_name_ar"]
            specs = f["short_specs"]
            price_first = style == "price"
            items = [(f"{opener} {name}", 0)]
            if price_first and f["price_ar"]:
                items.append((f"بسعر {f['price_ar']}", 1))
            items += [(sp, 6 + i) for i, sp in enumerate(specs)]
            if not price_first and f["price_ar"]:
                items.append((f"بسعر {f['price_ar']}", 1))
            if f["offer"]:
                items.append((f["offer"], 3))
            if f["warranty"]:
                items.append((f["warranty"], 4))
            if f["scarcity_allowed"]:
                items.append(("الكمية محدودة", 5))
            items.append((c["cta"], 2))
            out["ar"] = {
                "headline": _fit([name, f["condition_ar"]], " | ", lim["headline"]),
                "primary_text": _compose(items, " • ", lim["primary_text"]),
                "description": _fit([f["offer"] or f["condition_ar"]], "", lim["description"]),
                "cta": "ORDER_NOW" if style == "price" else "SHOP_NOW",
                "hashtags": [f["brand"]] if f["brand"] else [],
            }
        if "en" in (campaign.languages or []):
            name = f["product_name_en"]
            items = [(name, 0)] + [(sp, 6 + i) for i, sp in enumerate(f["short_specs"])]
            if f["price_en"]:
                items.append((f["price_en"], 1))
            for txt, pr in ((f["offer"], 3), (f["warranty"], 4)):
                if txt and txt.isascii():
                    items.append((txt, pr))
            if f["scarcity_allowed"]:
                items.append(("Limited stock", 5))
            items.append((f"Order now in {f['country_en']}", 2))
            out["en"] = {
                "headline": _fit([name, f["condition_en"]], " | ", lim["headline"]),
                "primary_text": _compose(items, " • ", lim["primary_text"]),
                "description": _fit([f["condition_en"]], "", lim["description"]),
                "cta": "SHOP_NOW",
                "hashtags": [f["brand"]] if f["brand"] else [],
            }
        return out
