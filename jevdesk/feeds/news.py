"""
News & economic calendar.

- Economic calendar: the public ForexFactory weekly JSON (USD/GBP/EUR, High/Medium).
- Headlines: Google News RSS searches per asset + Cointelegraph RSS.
- A transparent keyword classifier tags each headline BULLISH / BEARISH / NEUTRAL.
  It is a heuristic, not a model; the Grok news pass re-reads the headlines when
  an LLM is configured.
"""

from __future__ import annotations

import json
import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger("jevdesk.news")

CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FEEDS = {
    "gold_news": "https://news.google.com/rss/search?q=gold+price+XAUUSD&hl=en-US&gl=US&ceid=US:en",
    "btc_news": "https://news.google.com/rss/search?q=Bitcoin+BTC+price&hl=en-US&gl=US&ceid=US:en",
    "gbp_news": "https://news.google.com/rss/search?q=GBPUSD+pound+sterling+Bank+of+England&hl=en-US&gl=US&ceid=US:en",
    "crypto_news": "https://cointelegraph.com/rss",
}

BULLISH_KEYWORDS = [
    "rate cut", "easing", "dovish", "inflation cooling", "soft landing", "surge", "rally",
    "breakout", "all-time high", "record high", "etf inflow", "accumulation", "safe haven demand",
    "dollar weakens", "yields drop", "central bank buying", "gold surges", "bitcoin jumps",
    "pound strengthens", "sterling climbs", "sterling rally", "boe hawkish", "uk growth",
]
BEARISH_KEYWORDS = [
    "rate hike", "hawkish", "sticky inflation", "hot cpi", "recession fears", "sell-off",
    "crash", "plunge", "dump", "etf outflow", "regulatory crackdown", "liquidation",
    "dollar strengthens", "yields surge", "tightening", "gold drops", "bitcoin slides",
    "pound weakens", "sterling drops", "sterling falls", "boe rate cut", "uk recession",
]
HIGH_IMPACT = [
    "fomc", "cpi", "ppi", "non-farm", "nonfarm", "unemployment", "interest rate decision", "gdp",
    "powell", "bailey", "lagarde", "fed chair", "pmi", "boe", "bank of england", "gilt",
]


def classify_headline(title: str) -> Tuple[str, float, bool]:
    t = title.lower()
    bull = sum(1 for k in BULLISH_KEYWORDS if k in t)
    bear = sum(1 for k in BEARISH_KEYWORDS if k in t)
    high = any(k in t for k in HIGH_IMPACT)
    if bull > bear:
        return "BULLISH", min(1.0, 0.4 + 0.2 * (bull - bear)), high
    if bear > bull:
        return "BEARISH", max(-1.0, -0.4 - 0.2 * (bear - bull)), high
    return "NEUTRAL", 0.0, high


def parse_calendar(raw: List[Dict[str, Any]], now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    events = []
    for item in raw or []:
        if item.get("country") not in ("USD", "GBP", "EUR") or item.get("impact") not in ("High", "Medium"):
            continue
        try:
            dt = datetime.fromisoformat(item.get("date", "")).astimezone(timezone.utc)
        except ValueError:
            continue
        mins = (dt - now).total_seconds() / 60.0
        events.append({
            "title": item.get("title", ""),
            "country": item.get("country"),
            "impact": item.get("impact"),
            "date": item.get("date"),
            "event_dt_utc": dt.isoformat(),
            "minutes_until": round(mins, 1),
            "is_imminent": -15 <= mins <= 30,
            "is_today": abs(mins) < 1440,
            "forecast": item.get("forecast", ""),
            "previous": item.get("previous", ""),
        })
    return events


class NewsHarvester:
    def __init__(self, cache_dir: Path, client: Optional[httpx.Client] = None):
        self.client = client or httpx.Client(
            timeout=10.0, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; jevdesk/1.0)"},
        )
        self.cache_file = Path(cache_dir) / "calendar_cache.json"
        self.raw_calendar: List[Dict[str, Any]] = []
        self.last_calendar_fetch = 0.0
        if self.cache_file.exists():
            try:
                self.raw_calendar = json.loads(self.cache_file.read_text(encoding="utf-8"))
            except ValueError:
                self.raw_calendar = []

    def fetch_calendar(self) -> List[Dict[str, Any]]:
        if not self.raw_calendar or time.time() - self.last_calendar_fetch > 900:
            try:
                r = self.client.get(CALENDAR_URL)
                if r.status_code == 200:
                    self.raw_calendar = r.json()
                    self.last_calendar_fetch = time.time()
                    self.cache_file.parent.mkdir(parents=True, exist_ok=True)
                    self.cache_file.write_text(json.dumps(self.raw_calendar), encoding="utf-8")
            except Exception as exc:
                logger.warning("calendar fetch failed: %s", exc.__class__.__name__)
        return parse_calendar(self.raw_calendar)

    def fetch_rss(self, url: str, tag: str, limit: int = 15) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        try:
            r = self.client.get(url)
            if r.status_code != 200:
                return items
            root = ET.fromstring(r.content)
            for el in root.findall(".//item")[:limit]:
                title = (el.findtext("title") or "").strip()
                if not title:
                    continue
                sentiment, score, high = classify_headline(title)
                items.append({
                    "feed": tag,
                    "title": title,
                    "link": (el.findtext("link") or "").strip(),
                    "pub_date": (el.findtext("pubDate") or "").strip(),
                    "sentiment": sentiment,
                    "score": score,
                    "is_high_impact": high,
                })
        except Exception as exc:
            logger.warning("rss %s failed: %s", tag, exc.__class__.__name__)
        return items

    def poll(self) -> Dict[str, Any]:
        calendar = self.fetch_calendar()
        bundle: Dict[str, Any] = {k: self.fetch_rss(u, k) for k, u in FEEDS.items()}
        bundle["btc_news"] = bundle["btc_news"] + bundle["crypto_news"]
        imminent = [e for e in calendar if e["is_imminent"] and e["impact"] == "High"]
        bundle.update({
            "calendar_today": [e for e in calendar if e["is_today"]],
            "imminent_high_impact": imminent,
            "has_imminent_danger": bool(imminent),
            "fetched_at": time.time(),
        })
        for key in ("gold_news", "btc_news", "gbp_news"):
            total = sum(n["score"] for n in bundle[key])
            bundle[key.replace("_news", "_news_sentiment")] = (
                "BULLISH" if total > 0.3 else "BEARISH" if total < -0.3 else "NEUTRAL"
            )
        return bundle
