"""
Research engine: runs the five-stage Grok + JEV pipeline for one asset and
builds the desk-wide state (macro risk level, execution advisory, positioning).
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from .assets import ASSETS, Asset
from .jev_desk import JEVPortfolioManager
from .llm import LLMClient
from .research_desk import ChartResearcher, MacroResearcher, NewsResearcher
from .trend import compare_trend


class ResearchEngine:
    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm
        self.macro = MacroResearcher(llm)
        self.news = NewsResearcher(llm)
        self.chart = ChartResearcher(llm)
        self.jev = JEVPortfolioManager(llm)

    def research(self, asset: Asset, news: Dict[str, Any], market: Dict[str, Any], use_llm: bool = False) -> Dict[str, Any]:
        t0 = time.time()
        label = f"{asset.label} [{asset.symbol}]"
        macro = self.macro.analyze(label, news.get("calendar_today", []), use_llm)
        news_r = self.news.analyze(label, news.get(asset.news_key, []), use_llm)
        chart = self.chart.analyze(label, market, use_llm)
        jev = self.jev.evaluate(label, macro, news_r, chart, stale=bool(market.get("stale")), use_llm=use_llm)
        engines = {macro.get("engine"), news_r.get("engine"), chart.get("engine"), jev.get("engine")}
        return {
            "asset": asset.key,
            "label": asset.label,
            "timestamp": time.time(),
            "elapsed_s": round(time.time() - t0, 2),
            "engine": "grok" if engines == {"grok"} else ("mixed" if "grok" in engines else "deterministic"),
            "macro": macro,
            "news": news_r,
            "chart": chart,
            "jev": jev,
            "trend": compare_trend(jev.get("direction", "WAIT"), market),
            "price": market.get("price"),
        }


def desk_advisory(news: Dict[str, Any]) -> Dict[str, str]:
    """Macro risk level and execution advisory from the economic calendar."""
    imminent = news.get("imminent_high_impact", [])
    if imminent:
        e = min(imminent, key=lambda x: abs(x.get("minutes_until", 999)))
        m = e.get("minutes_until", 999)
        if -5 <= m <= 10:
            return {"risk_level": "CRITICAL", "advisory": "HALT_OR_CLOSE",
                    "reason": f"High-impact release now: {e['title']} ({e['country']}). No new entries."}
        if 10 < m <= 25:
            return {"risk_level": "HIGH", "advisory": "TIGHTEN_SL",
                    "reason": f"{e['title']} ({e['country']}) in {m:.0f} min. Protect open trades; no new swings."}
        return {"risk_level": "MEDIUM", "advisory": "CAUTION",
                "reason": f"{e['title']} ({e['country']}) within 30 min. Reduced conviction."}
    return {"risk_level": "ALL_CLEAR", "advisory": "PLAN_ENTRIES", "reason": "No high-impact release inside 30 minutes."}


def build_state(news: Dict[str, Any], markets: Dict[str, Dict[str, Any]], predictions: Dict[str, Any],
                research: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    adv = desk_advisory(news)
    return {
        "timestamp": time.time(),
        "macro_risk_level": adv["risk_level"],
        "execution_advisory": adv["advisory"],
        "advisory_reason": adv["reason"],
        "markets": {k: {f: v.get(f) for f in ("ok", "stale", "price", "change_24h_pct", "ema_9", "ema_21", "ema_50",
                                              "atr", "range_high", "range_low", "range_midline",
                                              "top_trader_long_pct", "taker_buy_ratio", "open_interest",
                                              "positioning_bias")}
                    for k, v in markets.items()},
        "news_sentiment": {k: news.get(f"{k}_news_sentiment") for k in ("gold", "btc", "gbp")},
        "calendar": {"imminent_high_impact": news.get("imminent_high_impact", []),
                     "events_today": len(news.get("calendar_today", []))},
        "prediction_markets": predictions,
        "research": {k: {f: r.get(f) for f in ("engine", "price")} | {
                         "score": r["jev"]["score"], "decision": r["jev"]["decision"],
                         "direction": r["jev"]["direction"], "news_support_pct": r["jev"]["news_support_pct"],
                         "explanation": r["jev"]["explanation"], "trend": r["trend"]["state"]}
                     for k, r in research.items()},
    }


__all__ = ["ResearchEngine", "build_state", "desk_advisory", "ASSETS"]
