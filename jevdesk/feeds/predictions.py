"""
Prediction-markets radar: the highest-volume active Polymarket events that
touch macro (Fed, rates, inflation, recession, tariffs) or crypto.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("jevdesk.predictions")

GAMMA = "https://gamma-api.polymarket.com/events"
MACRO_KEYS = ["fed", "rate", "inflation", "cpi", "recession", "tariff", "treasury", "election", "war", "ceasefire"]
CRYPTO_KEYS = ["bitcoin", "btc", "ethereum", "crypto", "solana", "tether", "etf"]


def _yes_prob(prices: Any) -> Optional[float]:
    try:
        parsed = json.loads(prices) if isinstance(prices, str) else prices
        return round(float(parsed[0]) * 100, 1) if parsed else None
    except (ValueError, TypeError, IndexError):
        return None


class PredictionMarkets:
    def __init__(self, client: Optional[httpx.Client] = None):
        self.client = client or httpx.Client(timeout=10.0, headers={"User-Agent": "jevdesk/1.0"})

    def scan(self, limit: int = 40) -> Dict[str, Any]:
        events: List[Dict[str, Any]] = []
        try:
            r = self.client.get(GAMMA, params={"limit": limit, "active": "true", "closed": "false",
                                               "order": "volume24hr", "ascending": "false"})
            if r.status_code == 200:
                events = r.json()
        except Exception as exc:
            logger.warning("polymarket failed: %s", exc.__class__.__name__)
        macro, crypto = [], []
        for ev in events:
            text = (ev.get("title", "") + " " + ev.get("slug", "")).lower()
            is_macro = any(k in text for k in MACRO_KEYS)
            is_crypto = any(k in text for k in CRYPTO_KEYS)
            if not (is_macro or is_crypto):
                continue
            item = {
                "title": ev.get("title", ""),
                "volume_24h_usd": round(float(ev.get("volume24hr") or 0.0)),
                "markets": [
                    {"question": m.get("question", ""), "yes_prob_pct": _yes_prob(m.get("outcomePrices"))}
                    for m in (ev.get("markets") or [])[:3]
                ],
            }
            (macro if is_macro else crypto).append(item)
        return {"macro_markets": macro[:5], "crypto_markets": crypto[:5], "ok": bool(events)}
