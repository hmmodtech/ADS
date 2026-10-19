"""Agent 1 - Strategist: turns a product + brief into an angle, hooks and per-country focus."""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from .. import geo
from ..llm import LLMUnavailable
from ..models import Campaign, Product
from .base import BaseAgent
from .facts import short_specs

ANGLES = {
    "price": "التركيز على السعر والقيمة مقابل المال",
    "features": "التركيز على المواصفات والأداء (الكاميرا، البطارية، الأداء)",
    "lifestyle": "التركيز على أسلوب الحياة والتجربة اليومية",
    "balanced": "توازن بين المواصفات والسعر مع دعوة واضحة للطلب",
}


class StrategistAgent(BaseAgent):
    name = "strategist"
    label = "وكيل الاستراتيجية"

    async def run(self, db: Session, campaign: Campaign, product: Product) -> dict:
        strategy = None
        if self.llm.available:
            try:
                strategy = await self._llm_strategy(campaign, product)
                campaign.llm_used = True
            except LLMUnavailable as e:
                self.log(db, campaign.id, f"تعذّر Gemini، سيتم استخدام استراتيجية القوالب: {e}", "warn")
        if strategy is None:
            strategy = self._fallback(campaign, product)
        self.log(db, campaign.id, f"الزاوية التسويقية: {strategy['angle']}", "decision",
                 hooks=strategy.get("hooks"))
        return strategy

    async def _llm_strategy(self, campaign: Campaign, product: Product) -> dict:
        brief = {
            "product": {"name_ar": product.name_ar, "name_en": product.name_en, "brand": product.brand,
                        "condition": product.condition, "specs": product.specs,
                        "description": product.description},
            "countries": {c: geo.country(c)["ar"] for c in campaign.countries},
            "platforms": campaign.platforms,
            "style": campaign.creative_style,
            "tone": campaign.tone,
            "audience": {"age": [campaign.age_min, campaign.age_max], "gender": campaign.gender},
            "extra_notes": campaign.notes,
        }
        prompt = (
            "أنت استراتيجي إعلانات رقمية متخصص في سوق الخليج لهواتف الجوال.\n"
            "اقرأ الملخص التالي وأرجع JSON فقط بهذا الشكل:\n"
            '{"angle": "جملة واحدة", "hooks": ["3 افتتاحيات قصيرة"], '
            '"key_points": ["حتى 4 نقاط مأخوذة حرفياً من المواصفات المعطاة فقط"], '
            '"per_country": {"SA": "تركيز مقترح في جملة"}}\n'
            "ممنوع اختراع أي ميزة أو رقم أو عرض غير موجود في الملخص.\n\n"
            f"الملخص:\n{json.dumps(brief, ensure_ascii=False)}"
        )
        data = await self.llm.json(prompt, temperature=0.7)
        if not isinstance(data, dict) or "angle" not in data:
            raise LLMUnavailable("صيغة استراتيجية غير صالحة")
        data.setdefault("hooks", [])
        data.setdefault("key_points", short_specs(product))
        data.setdefault("per_country", {})
        return data

    def _fallback(self, campaign: Campaign, product: Product) -> dict:
        pts = short_specs(product)
        return {
            "angle": ANGLES.get(campaign.creative_style, ANGLES["balanced"]),
            "hooks": [f"{product.display_name} الآن", "جديدنا لك", "لا يفوتك"],
            "key_points": pts,
            "per_country": {c: f"لهجة {geo.country(c)['ar']} المحلية" for c in campaign.countries},
            "mode": "template",
        }
