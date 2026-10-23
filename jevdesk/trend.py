"""
Trend comparator — compares the JEV thesis with a simple, explicit trend model:

- Trend anchor : price vs EMA 50
- Momentum     : EMA 9 vs EMA 21
- Equilibrium  : price vs the recent range midline

Result: FULL_CONFLUENCE, PULLBACK, DIVERGENT or WAIT. Advisory only.
"""

from __future__ import annotations

from typing import Any, Dict


def compare_trend(action: str, m: Dict[str, Any]) -> Dict[str, Any]:
    price = m.get("price")
    if price is None:
        return {"state": "UNAVAILABLE", "verdict": "No market data."}
    e9, e21, e50 = m.get("ema_9") or price, m.get("ema_21") or price, m.get("ema_50") or price
    mid = m.get("range_midline") or price
    trend = "BULLISH" if price >= e50 else "BEARISH"
    momentum = "BULLISH" if e9 >= e21 else "BEARISH"
    action = (action or "").upper()
    want = "BULLISH" if action == "BUY" else "BEARISH" if action == "SELL" else None

    if want is None:
        state, verdict = "WAIT", "No directional action proposed."
    elif trend == want and momentum == want:
        state, verdict = "FULL_CONFLUENCE", f"{action} agrees with trend (EMA 50) and momentum (EMA 9/21)."
    elif trend == want:
        state, verdict = "PULLBACK", f"Trend supports {action}, but momentum is pulling back."
    else:
        state, verdict = "DIVERGENT", f"{action} is counter-trend: price is on the wrong side of EMA 50."
    return {
        "state": state,
        "trend": trend,
        "momentum": momentum,
        "midline_position": "ABOVE" if price >= mid else "BELOW",
        "verdict": verdict,
        "summary": f"EMA50 {e50} | EMA9/21 {e9}/{e21} | midline {mid} | {state}",
    }
