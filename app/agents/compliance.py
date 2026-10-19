"""Agent 4 - Compliance & QA gate. Deterministic rules first, optional LLM second opinion.

A campaign with any `critical` issue can NEVER be approved/deployed until it is fixed.
"""
from __future__ import annotations

import json
import os
import re

from sqlalchemy.orm import Session

from ..llm import LLMUnavailable
from ..models import Campaign, Product
from ..services.pricing import all_allowed_prices
from ..services.events import add_event  # noqa: F401
from .base import BaseAgent
from .facts import scarcity_allowed

AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٬٫", "0123456789,.")

# claim -> (regex, explanation)
SUPERLATIVES = re.compile(
    r"(الأفضل في العالم|رقم\s*1|رقم\s*واحد|الأول عالمياً|#1|best in the world|world'?s best|number\s*one|no\.?\s*1)",
    re.I)
WARRANTY_WORDS = re.compile(r"(ضمان|كفالة|warranty|guarantee|guaranteed)", re.I)
FREE_WORDS = re.compile(r"(مجاني|مجانا|مجانًا|بلاش|free\b)", re.I)
SCARCITY_WORDS = re.compile(
    r"(آخر القطع|الكمية محدودة|كمية محدودة|ينفد|قبل نفاد|نفدت|limited stock|last chance|while stocks? last|selling fast|almost gone)",
    re.I)
PROHIBITED = re.compile(
    r"(ربح مضمون|اربح المال|100\s*%\s*مضمون|معجزة|يشفي|شفاء|risk[- ]free|guaranteed profit|miracle|cure)", re.I)
COMPETITOR_BRANDS = {
    "apple": ["apple", "iphone", "ايفون", "آيفون", "ابل", "آبل"],
    "samsung": ["samsung", "galaxy", "سامسونج", "سامسونغ", "جالكسي", "غالاكسي"],
    "xiaomi": ["xiaomi", "redmi", "poco", "شاومي", "ريدمي"],
    "huawei": ["huawei", "هواوي"],
    "oppo": ["oppo", "اوبو", "أوبو"],
    "vivo": ["vivo", "فيفو"],
    "realme": ["realme", "ريلمي"],
    "honor": ["honor", "هونر", "أونر"],
    "google": ["pixel", "بيكسل"],
    "oneplus": ["oneplus", "ونبلس", "وان بلس"],
    "infinix": ["infinix", "انفنكس", "إنفينكس"],
    "tecno": ["tecno", "تكنو"],
    "nothing": ["nothing phone"],
}
COMPARATIVE = re.compile(r"(أفضل من|يتفوق على|أقوى من|بدل|better than|beats|vs\.?|instead of)", re.I)
CUR = r"(?:SAR|AED|KWD|QAR|BHD|OMR|JOD|USD|ر\.?\s?س|ريال|د\.?\s?إ|درهم|د\.?\s?ك|د\.?\s?ق|ر\.?\s?ق|د\.?\s?ب|ر\.?\s?ع|دينار|\$)"
PRICE_RE = [re.compile(rf"(\d[\d,]*(?:\.\d+)?)\s*{CUR}", re.I), re.compile(rf"{CUR}\s*(\d[\d,]*(?:\.\d+)?)", re.I)]
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


def _brand_hits(text: str, own: set[str]) -> list[str]:
    low = text.lower()
    hits = []
    for brand, words in COMPETITOR_BRANDS.items():
        if brand in own:
            continue
        if any(w.lower() in low for w in words):
            hits.append(brand)
    return hits


class ComplianceAgent(BaseAgent):
    name = "compliance"
    label = "وكيل الامتثال والجودة"

    async def run(self, db: Session, campaign: Campaign, product: Product) -> dict:
        issues: list[dict] = []
        self._check_copies(campaign, product, issues)
        self._check_assets(campaign, issues)
        self._check_basics(campaign, product, issues)

        if self.llm.available and campaign.copies:
            try:
                issues += await self._llm_review(campaign, product)
            except LLMUnavailable as e:
                issues.append({"severity": "info", "code": "llm_review_skipped",
                               "message": f"تعذّرت المراجعة الثانية بالذكاء الاصطناعي: {e}"})

        critical = [i for i in issues if i["severity"] == "critical"]
        result = {"passed": not critical, "critical": len(critical),
                  "warnings": len([i for i in issues if i["severity"] == "warning"]), "issues": issues}
        self.log(db, campaign.id,
                 "اجتاز فحص الامتثال" if result["passed"] else f"فشل الفحص: {len(critical)} مشكلة حرجة",
                 "decision" if result["passed"] else "warn", critical=len(critical))
        return result

    # ------------------------------------------------------------------ checks
    def _check_copies(self, campaign: Campaign, product: Product, issues: list[dict]):
        allowed_prices = all_allowed_prices(product, campaign.countries)
        own = {w.lower() for w in re.split(r"\W+", f"{product.brand or ''} {product.name_en} {product.name_ar or ''}") if w}
        own_brands = {b for b, words in COMPETITOR_BRANDS.items()
                      if any(w.lower() in " ".join(own) for w in words) or b in own}
        for cc, langs in (campaign.copies or {}).items():
            for lang, c in (langs or {}).items():
                text = " ".join(str(c.get(k, "")) for k in ("headline", "primary_text", "description"))
                text_n = text.translate(AR_DIGITS)

                def add(sev, code, msg):
                    issues.append({"severity": sev, "code": code, "message": msg, "country": cc, "lang": lang})

                if not c.get("primary_text"):
                    add("critical", "empty_text", "النص الرئيسي فارغ")
                if PROHIBITED.search(text):
                    add("critical", "prohibited_claim", "يحتوي على ادعاء محظور (ربح مضمون/علاج/معجزة...)")
                if SUPERLATIVES.search(text):
                    add("critical", "superlative", "ادعاء تفوّق مطلق غير مُثبت (الأفضل في العالم / رقم 1)")
                if WARRANTY_WORDS.search(text) and not product.warranty_text:
                    add("critical", "unverified_warranty",
                        "ذُكر ضمان/كفالة ولا يوجد نص ضمان مسجّل في المنتج")
                if FREE_WORDS.search(text) and not (product.offer_text and FREE_WORDS.search(product.offer_text)):
                    add("critical", "unverified_free", "ذُكر «مجاني» ولا يوجد عرض مجاني مسجّل في المنتج")
                if SCARCITY_WORDS.search(text) and not scarcity_allowed(product):
                    add("critical", "fake_scarcity",
                        "عبارة ندرة/نفاد دون مخزون حقيقي منخفض مُتتبَّع (تضليل)")
                hits = _brand_hits(text, own_brands)
                if hits:
                    add("critical" if COMPARATIVE.search(text) else "warning", "competitor_brand",
                        f"ذُكرت ماركات أخرى: {', '.join(hits)}" + (" ضمن مقارنة" if COMPARATIVE.search(text) else ""))
                # price must match a known price
                for rx in PRICE_RE:
                    for m in rx.finditer(text_n):
                        try:
                            val = float(m.group(1).replace(",", ""))
                        except ValueError:
                            continue
                        if not any(abs(val - p) < 0.51 for p in allowed_prices):
                            add("critical", "price_mismatch", f"السعر {val:g} في النص لا يطابق أسعار المنتج")
                # platform limits
                if len(c.get("headline", "")) > 40:
                    add("warning", "headline_long", "العنوان أطول من 40 حرفاً وقد يُقتطع")
                limit = 100 if "tiktok" in campaign.platforms else 125
                if len(c.get("primary_text", "")) > limit:
                    add("critical" if "tiktok" in campaign.platforms else "warning", "text_long",
                        f"النص الرئيسي أطول من {limit} حرفاً")
                if len(EMOJI_RE.findall(text)) > 6:
                    add("warning", "emoji_overuse", "إيموجي كثير قد يخفّض جودة الإعلان")
                if re.search(r"[A-Z]{12,}", text):
                    add("warning", "shouting", "كلمات بأحرف كبيرة كثيرة")

    def _check_assets(self, campaign: Campaign, issues: list[dict]):
        if not campaign.assets:
            return
        for a in campaign.assets:
            if not os.path.exists(a.get("path", "")):
                issues.append({"severity": "critical", "code": "asset_missing",
                               "message": f"ملف الوسائط غير موجود: {a.get('kind')} {a.get('country')}"})
        if not any(a["kind"] == "image" for a in campaign.assets):
            issues.append({"severity": "critical", "code": "no_image", "message": "لا توجد صورة إعلانية"})

    def _check_basics(self, campaign: Campaign, product: Product, issues: list[dict]):
        if not product.active:
            issues.append({"severity": "critical", "code": "product_inactive", "message": "المنتج غير نشط"})
        if product.track_stock and not product.in_stock:
            issues.append({"severity": "critical", "code": "out_of_stock", "message": "المنتج نفد من المخزون"})
        if not campaign.landing_url:
            issues.append({"severity": "critical", "code": "no_landing",
                           "message": "لا يوجد رابط وجهة (ضع رابطاً أو رقم واتساب في الإعدادات)"})
        elif not campaign.landing_url.startswith(("https://", "http://")):
            issues.append({"severity": "critical", "code": "bad_landing", "message": "رابط الوجهة غير صالح"})
        for cc in campaign.countries:
            from ..services.pricing import price_for
            amt, _, explicit = price_for(product, cc)
            if not amt:
                issues.append({"severity": "warning", "code": "no_price", "country": cc,
                               "message": f"لا يوجد سعر لـ {cc}؛ لن يُذكر سعر في الإعلان"})
            elif not explicit:
                issues.append({"severity": "warning", "code": "converted_price", "country": cc,
                               "message": f"سعر {cc} محوَّل تلقائياً من السعر الأساسي؛ حدّد سعراً صريحاً للدقة"})

    async def _llm_review(self, campaign: Campaign, product: Product) -> list[dict]:
        prompt = (
            "أنت مدقق سياسات إعلانات (Meta/TikTok) لسوق الخليج. راجع النصوص التالية وأرجع JSON فقط:\n"
            '{"issues": [{"country": "SA", "lang": "ar", "severity": "critical|warning", "message": "..."}]}\n'
            "اعتبر المشكلة critical فقط إذا كانت تنتهك سياسة إعلانات بوضوح: ادعاء كاذب/مضلل، "
            "محتوى حساس، مقارنة مباشرة بعلامة منافسة، وعد غير مثبت، أو ما يخالف الذوق العام الخليجي.\n"
            "الحقائق الصحيحة الوحيدة: "
            + json.dumps({"name": product.display_name, "specs": product.specs, "warranty": product.warranty_text,
                          "offer": product.offer_text}, ensure_ascii=False)
            + "\nالنصوص:\n" + json.dumps(campaign.copies, ensure_ascii=False)
        )
        data = await self.llm.json(prompt, temperature=0.1)
        out = []
        for i in (data.get("issues") or [])[:10] if isinstance(data, dict) else []:
            sev = i.get("severity", "warning")
            out.append({"severity": sev if sev in ("critical", "warning") else "warning",
                        "code": "llm_review", "message": f"[مراجعة AI] {i.get('message', '')}",
                        "country": i.get("country"), "lang": i.get("lang")})
        return out
