"""
JEV desk — passes 4-5 of the pipeline.

4. JEV portfolio manager : combines the three Grok reports into a 0-100 score,
   a news-support %, supporting vs opposing evidence and a SIMULATED decision.
5. JEV risk desk        : PASS / FLAG / FAIL checks — fixed-dollar risk,
   concentration, dead zone, data quality, invalidation, reward:risk.

Scoring (deterministic engine), 25 points each:
  macro        = macro_support_score x 0.25
  news         = news_support_pct    x 0.25   (support for the proposed side)
  chart        = chart alignment     x 0.25   (bullish score for BUY, 100 - score for SELL)
  data_quality = 25 minus deductions (conflicting news, low confidence, macro risks, stale data)
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .llm import LLMClient
from .research_desk import GUARDRAILS, _num

DECISIONS = ("SIMULATED BUY", "SIMULATED SELL", "SIMULATED WAIT")


class JEVPortfolioManager:
    SYSTEM_PROMPT = f"""You are JEV, a portfolio-manager agent on an AI research desk. {GUARDRAILS}
Every decision you return is SIMULATED; a human makes every final decision.

Task: read the macro, news and chart reports. Return strict JSON:
{{
  "score": 0-100,
  "weights": {{"macro": 0-25, "news": 0-25, "chart": 0-25, "data_quality": 0-25}},
  "decision": "SIMULATED BUY | SIMULATED SELL | SIMULATED WAIT",
  "direction": "BUY | SELL | WAIT",
  "news_support_pct": 0-100,
  "explanation": "three sentences maximum",
  "supporting_evidence": ["..."],
  "opposing_evidence": ["..."],
  "missing_information": "what would most change your confidence",
  "invalidation_condition": "exact price level or event that proves the thesis wrong",
  "conditions_to_change_decision": "specific checkable condition"
}}
Rule: if the reports conflict or data quality is low, the correct answer is SIMULATED WAIT."""

    def __init__(self, llm: Optional[LLMClient] = None, buy_sell_threshold: float = 65.0):
        self.llm = llm
        self.threshold = buy_sell_threshold

    def evaluate(self, asset_label: str, macro: Dict[str, Any], news: Dict[str, Any],
                 chart: Dict[str, Any], stale: bool = False, use_llm: bool = False) -> Dict[str, Any]:
        if use_llm and self.llm and self.llm.enabled:
            prompt = (f"Target asset: {asset_label}\n\n--- MACRO REPORT ---\n{json.dumps(macro, indent=1)}"
                      f"\n\n--- NEWS REPORT ---\n{json.dumps(news, indent=1)}"
                      f"\n\n--- CHART REPORT ---\n{json.dumps(chart, indent=1)}"
                      + ("\n\nNOTE: the market data is STALE (market closed or feed delayed)." if stale else ""))
            res = self.llm.chat_json(self.SYSTEM_PROMPT, prompt)
            if res and res.get("decision") in DECISIONS:
                res["score"] = _num(res.get("score"), 50.0)
                res["news_support_pct"] = _num(res.get("news_support_pct"), 50.0)
                if res.get("direction") not in ("BUY", "SELL", "WAIT"):
                    res["direction"] = res["decision"].split()[-1]
                if res["direction"] != "WAIT" and (res["score"] < self.threshold or stale or not chart.get("data_ok", True)):
                    res.update(decision="SIMULATED WAIT", direction="WAIT",
                               explanation=(res.get("explanation", "") + " [Desk rule: below threshold or bad data -> WAIT]").strip())
                res["engine"] = "grok"
                return res
        return self._deterministic(macro, news, chart, stale)

    def _deterministic(self, macro: Dict[str, Any], news: Dict[str, Any], chart: Dict[str, Any], stale: bool) -> Dict[str, Any]:
        stance = news.get("news_stance", "NEUTRAL")
        tech = chart.get("technical_bias", "NEUTRAL")
        side = "BUY" if stance == "BULLISH" else "SELL" if stance == "BEARISH" else None

        chart_raw = float(chart.get("chart_support_score", 50.0))
        chart_align = chart_raw if side != "SELL" else 100.0 - chart_raw
        macro_pts = _num(macro.get("macro_support_score"), 50.0) * 0.25
        news_pts = _num(news.get("news_support_pct"), 50.0) * 0.25
        chart_pts = chart_align * 0.25

        dq = 25.0
        if news.get("conflicts"):
            dq -= 8.0
        if float(news.get("confidence", 1.0)) < 0.7:
            dq -= 5.0
        if len(macro.get("major_risks", [])) > 1:
            dq -= 4.0
        if stale or not chart.get("data_ok", True):
            dq -= 15.0
        dq = max(0.0, dq)
        score = round(macro_pts + news_pts + chart_pts + dq, 1)

        pros: List[str] = []
        cons: List[str] = []
        for f in news.get("facts", []):
            (cons if "headwind" in f.lower() else pros).append(f)
        if side == "SELL":
            pros, cons = cons, pros
        pros += [f"Macro: {o}" for o in macro.get("major_opportunities", [])]
        cons += [f"Macro risk: {r}" for r in macro.get("major_risks", [])]
        shows = chart.get("what_numbers_show", "")
        aligned = (side == "BUY" and tech == "BULLISH") or (side == "SELL" and tech == "BEARISH")
        if shows:
            (pros if aligned else cons).append(f"Technical: {shows}")

        if stale or not chart.get("data_ok", True):
            direction, why = "WAIT", "Market data is stale or unavailable; no decision on bad data."
        elif side is None:
            direction, why = "WAIT", f"News stance is {stance}; there is no directional catalyst."
        elif (side == "BUY" and tech == "BEARISH") or (side == "SELL" and tech == "BULLISH"):
            direction, why = "WAIT", f"News says {side} but the chart structure is {tech}; the reports conflict."
        elif score < self.threshold:
            direction, why = "WAIT", f"Score {score}/100 is below the {self.threshold:.0f} conviction threshold."
        else:
            direction = side
            why = (f"{stance.title()} news ({news.get('news_support_pct', 50):.0f}% support) agrees with a "
                   f"{tech.lower()} chart; score {score}/100.")

        return {
            "score": score,
            "weights": {"macro": round(macro_pts, 1), "news": round(news_pts, 1),
                        "chart": round(chart_pts, 1), "data_quality": round(dq, 1)},
            "decision": f"SIMULATED {direction}",
            "direction": direction,
            "candidate_side": side,
            "news_support_pct": float(news.get("news_support_pct", 50.0)),
            "explanation": why,
            "supporting_evidence": pros[:4],
            "opposing_evidence": cons[:4],
            "missing_information": "Live order-book depth and whether the news is already priced in.",
            "invalidation_condition": chart.get("what_would_change_picture", "Break of the recent range."),
            "conditions_to_change_decision": "Re-score if price crosses the invalidation level or a headline is retracted.",
            "engine": "deterministic",
        }


class JEVRiskDesk:
    """Deterministic gatekeeper. Its verdict is never delegated to an LLM."""

    def check(self, *, direction: str, risk_usd: float, configured_risk_usd: float, open_positions: int,
              in_dead_zone: bool, data_ok: bool, stale: bool, invalidation: Optional[float],
              rr: Optional[float], min_rr: float = 1.2, max_open: int = 1) -> Dict[str, Any]:
        def item(ok: bool, note: str, fail: bool = True) -> Dict[str, str]:
            return {"rating": "PASS" if ok else ("FAIL" if fail else "FLAG"), "note": note}

        checks = {
            "direction": item(direction in ("BUY", "SELL"), f"JEV direction is {direction}."),
            "fixed_risk": item(abs(risk_usd - configured_risk_usd) <= max(1.0, 0.02 * configured_risk_usd),
                               f"Stop-out loses ${risk_usd:.2f} (law: ${configured_risk_usd:.2f})."),
            "concentration": item(open_positions < max_open, f"{open_positions} open position(s), max {max_open}.", fail=False),
            "dead_zone": item(not in_dead_zone, "Inside the dead zone — the desk stays flat." if in_dead_zone else "Outside the dead zone."),
            "data_quality": item(data_ok and not stale, "Market data fresh." if data_ok and not stale else "Market data stale/unavailable."),
            "invalidation": item(invalidation is not None, f"Invalidation level: {invalidation}."),
            "reward_risk": item(rr is not None and rr >= min_rr, f"Reward:risk {rr}R (min {min_rr}R)."),
        }
        ratings = [c["rating"] for c in checks.values()]
        overall = "FAIL" if "FAIL" in ratings else "FLAG" if "FLAG" in ratings else "PASS"
        return {"checks": checks, "overall_risk_rating": overall}
