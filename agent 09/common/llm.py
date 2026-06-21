"""
Unified multi-provider LLM client with retry, fallback, and JSON extraction.

Design goals:
  - Single import for all LLM calls across all agents
  - Provider selected automatically from model name
  - Consistent retry/backoff behaviour
  - JSON mode handled transparently per provider
  - Fallback chain: primary model → fallback model → exception

Usage:
    from common.llm import LLMClient
    llm = LLMClient()

    # Returns parsed dict/list, retries on failure
    result = llm.json(prompt="Extract items...", model="gpt-4o")

    # Returns raw string
    text = llm.text(prompt="Summarise...", model="gemini-2.0-flash")

    # Explicit fallback: try gpt-4o first, fall back to gemini-2.0-flash
    result, model_used = llm.json_with_fallback(
        prompt=prompt,
        primary="gpt-4o",
        fallback="gemini-2.0-flash",
    )
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional, Tuple

from common.config import settings

logger = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def _strip_fences(text: str) -> str:
    m = _JSON_FENCE.search(text)
    return m.group(1).strip() if m else text.strip()


def _parse_json(text: str) -> Any:
    clean = _strip_fences(text)
    return json.loads(clean)


# ── Provider clients (lazy-initialised) ────────────────────────────────────────

class LLMClient:
    """
    Unified client for OpenAI, Anthropic, and Google Gemini.

    Clients are initialised lazily so agents that don't use a particular
    provider don't need that SDK installed.
    """

    def __init__(self):
        self._openai = None
        self._anthropic = None
        self._gemini_configured = False

    # ── Public API ──────────────────────────────────────────────────────────────

    def json(
        self,
        prompt: str,
        model: Optional[str] = None,
        system: Optional[str] = None,
        retries: Optional[int] = None,
    ) -> Any:
        """Call LLM and return a parsed JSON object. Retries on any exception."""
        model = model or settings.llm_model
        retries = retries if retries is not None else settings.llm_retries
        return self._with_retry(self._call_json, prompt, model, system, retries)

    def text(
        self,
        prompt: str,
        model: Optional[str] = None,
        system: Optional[str] = None,
        retries: Optional[int] = None,
    ) -> str:
        """Call LLM and return raw text. Retries on any exception."""
        model = model or settings.llm_model
        retries = retries if retries is not None else settings.llm_retries
        return self._with_retry(self._call_text, prompt, model, system, retries)

    def json_with_fallback(
        self,
        prompt: str,
        primary: str,
        fallback: str,
        system: Optional[str] = None,
    ) -> Tuple[Any, str]:
        """
        Try primary model, fall back to fallback model on failure.
        Returns (parsed_json, model_name_used).
        """
        try:
            result = self.json(prompt=prompt, model=primary, system=system)
            return result, primary
        except Exception as e:
            logger.warning(f"[LLM] {primary} failed: {e} — falling back to {fallback}")
        result = self.json(prompt=prompt, model=fallback, system=system)
        return result, fallback

    def text_with_fallback(
        self,
        prompt: str,
        primary: str,
        fallback: str,
        system: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Try primary model, fall back to fallback model. Returns (text, model_used)."""
        try:
            result = self.text(prompt=prompt, model=primary, system=system)
            return result, primary
        except Exception as e:
            logger.warning(f"[LLM] {primary} failed: {e} — falling back to {fallback}")
        result = self.text(prompt=prompt, model=fallback, system=system)
        return result, fallback

    # ── Retry wrapper ───────────────────────────────────────────────────────────

    def _with_retry(self, fn, prompt, model, system, retries):
        last_exc = None
        delays = settings.llm_retry_delays
        for attempt in range(retries):
            try:
                return fn(prompt, model, system)
            except Exception as e:
                last_exc = e
                delay = delays[min(attempt, len(delays) - 1)]
                logger.warning(
                    f"[LLM] {model} attempt {attempt + 1}/{retries} failed: {e}. "
                    f"Retrying in {delay}s…"
                )
                if attempt < retries - 1:
                    time.sleep(delay)
        raise last_exc

    # ── Dispatch by provider ────────────────────────────────────────────────────

    def _call_json(self, prompt: str, model: str, system: Optional[str]) -> Any:
        provider = settings.provider_for(model)
        if provider == "openai":
            return self._openai_json(prompt, model, system)
        if provider == "anthropic":
            text = self._anthropic_text(prompt, model, system)
            return _parse_json(text)
        if provider == "google":
            text = self._gemini_text(prompt, model, system)
            return _parse_json(text)
        raise ValueError(f"Unknown provider for model: {model}")

    def _call_text(self, prompt: str, model: str, system: Optional[str]) -> str:
        provider = settings.provider_for(model)
        if provider == "openai":
            return self._openai_text(prompt, model, system)
        if provider == "anthropic":
            return self._anthropic_text(prompt, model, system)
        if provider == "google":
            return self._gemini_text(prompt, model, system)
        raise ValueError(f"Unknown provider for model: {model}")

    # ── OpenAI ──────────────────────────────────────────────────────────────────

    def _ensure_openai(self):
        if self._openai is None:
            import openai
            self._openai = openai.OpenAI(api_key=settings.openai_api_key)
        return self._openai

    def _openai_json(self, prompt: str, model: str, system: Optional[str]) -> Any:
        client = self._ensure_openai()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        else:
            # OpenAI JSON mode requires "json" to appear in a message
            messages.append({"role": "system", "content": "Respond only with valid JSON."})
        messages.append({"role": "user", "content": prompt})
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)

    def _openai_text(self, prompt: str, model: str, system: Optional[str]) -> str:
        client = self._ensure_openai()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
        )
        return resp.choices[0].message.content

    # ── Anthropic ───────────────────────────────────────────────────────────────

    def _ensure_anthropic(self):
        if self._anthropic is None:
            import anthropic
            self._anthropic = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        return self._anthropic

    def _anthropic_text(self, prompt: str, model: str, system: Optional[str]) -> str:
        client = self._ensure_anthropic()
        kwargs: dict = {
            "model": model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        resp = client.messages.create(**kwargs)
        return resp.content[0].text

    # ── Google Gemini ───────────────────────────────────────────────────────────

    def _ensure_gemini(self):
        if not self._gemini_configured:
            import google.generativeai as genai
            genai.configure(api_key=settings.google_api_key)
            self._gemini_configured = True

    def _gemini_text(self, prompt: str, model: str, system: Optional[str]) -> str:
        self._ensure_gemini()
        import google.generativeai as genai
        m = genai.GenerativeModel(model)
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        resp = m.generate_content(full_prompt)
        return resp.text
