"""
LLM bridge for the Grok research passes.

Talks to any OpenAI-compatible `/chat/completions` endpoint. The default is
xAI's Grok API; Ollama, LM Studio, vLLM or OpenRouter work by changing
LLM_BASE_URL / LLM_MODEL. Every caller has a deterministic fallback, so the
desk keeps working (and says so) when no model is configured or a call fails.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional

import httpx

from .config import Settings, get_settings

logger = logging.getLogger("jevdesk.llm")

JSON_SUFFIX = (
    "\n\nIMPORTANT: respond with one JSON object only. No markdown fences, "
    "no commentary before or after the JSON."
)


def parse_json_object(raw: str) -> Optional[Dict[str, Any]]:
    """Parse a JSON object out of a model reply (tolerates code fences)."""
    if not raw:
        return None
    clean = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    clean = re.sub(r"\s*```$", "", clean, flags=re.MULTILINE).strip()
    try:
        obj = json.loads(clean)
        return obj if isinstance(obj, dict) else None
    except ValueError:
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        if match:
            try:
                obj = json.loads(match.group(0))
                return obj if isinstance(obj, dict) else None
            except ValueError:
                return None
    return None


class LLMClient:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()

    @property
    def enabled(self) -> bool:
        return self.settings.llm_enabled

    def chat(self, system_prompt: str, user_prompt: str, timeout: Optional[float] = None) -> str:
        if not self.enabled:
            return ""
        s = self.settings
        headers = {"Content-Type": "application/json"}
        if s.llm_api_key:
            headers["Authorization"] = f"Bearer {s.llm_api_key}"
        payload = {
            "model": s.llm_model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        try:
            resp = httpx.post(
                f"{s.llm_base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=timeout or s.llm_timeout,
            )
            if resp.status_code != 200:
                logger.warning("LLM call failed: HTTP %s", resp.status_code)
                return ""
            data = resp.json()
            return (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
        except Exception as exc:  # network, timeout, bad JSON
            logger.warning("LLM call failed: %s", exc.__class__.__name__)
            return ""

    def chat_json(self, system_prompt: str, user_prompt: str, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        return parse_json_object(self.chat(system_prompt, user_prompt + JSON_SUFFIX, timeout))
