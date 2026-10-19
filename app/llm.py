"""Thin async wrapper around Gemini that always returns parsed JSON.

If no API key is configured, `available` is False and agents use their deterministic,
facts-only fallbacks (so the whole pipeline still works offline / in manual mode).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from .config import Settings, get_settings

log = logging.getLogger("llm")


class LLMUnavailable(Exception):
    pass


def _parse_json(text: str) -> Any:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", text, flags=re.S)
        if not m:
            raise
        return json.loads(m.group(1))


class LLM:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._client = None

    @property
    def available(self) -> bool:
        return self.settings.llm_enabled

    def _get_client(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.settings.gemini_api_key)
        return self._client

    def _call(self, prompt: str, system: str | None, temperature: float) -> str:
        from google.genai import types

        resp = self._get_client().models.generate_content(
            model=self.settings.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                temperature=temperature,
            ),
        )
        return resp.text or ""

    async def json(self, prompt: str, system: str | None = None, temperature: float = 0.8,
                   retries: int = 2) -> Any:
        if not self.available:
            raise LLMUnavailable("GEMINI_API_KEY غير مضبوط")
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                text = await asyncio.to_thread(self._call, prompt, system, temperature)
                return _parse_json(text)
            except Exception as e:  # network, quota, malformed JSON ...
                last = e
                log.warning("LLM attempt %s failed: %s", attempt + 1, e)
                await asyncio.sleep(1.5 * (attempt + 1))
        raise LLMUnavailable(f"فشل استدعاء Gemini: {last}")


_llm: LLM | None = None


def get_llm() -> LLM:
    global _llm
    if _llm is None:
        _llm = LLM()
    return _llm
