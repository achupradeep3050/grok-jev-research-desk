"""
Approval hub: turns a JEV research package into an operator signal card and
executes the operator's YES / NO / LATE decision.

Late Reconfirmation Protocol (for an operator who saw the card late):
  1. Invalidation guard  — refused if price has crossed the invalidation level
                           (half-way from entry to stop).
  2. Runaway guard       — refused if price already covered RUNAWAY_FRACTION of the
                           way to target, or the remaining reward:risk < MIN_LATE_RR.
  3. Resize              — otherwise re-sized at the current price so a stop-out
                           still loses exactly RISK_PER_TRADE_USD.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .assets import ASSETS, position_size, risk_of
from .config import Settings
from .jev_desk import JEVRiskDesk
from .journal import Journal
from .trend import compare_trend


def in_dead_zone(zone, now: Optional[datetime] = None) -> bool:
    if not zone:
        return False
    now = now or datetime.now(timezone.utc)
    h = now.hour + now.minute / 60.0
    start, end = zone
    return start <= h < end if start <= end else (h >= start or h < end)


def _fmt(asset_key: str, v: Optional[float]) -> str:
    if v is None:
        return "n/a"
    a = ASSETS[asset_key]
    return f"{v:.{a.decimals}f}" if a.is_fx else f"${v:,.{a.decimals}f}"


class ApprovalHub:
    def __init__(self, settings: Settings, broker, journal: Optional[Journal] = None,
                 price_getter: Optional[Callable[[str], Optional[float]]] = None):
        self.s = settings
        self.broker = broker
        self.journal = journal or Journal(settings.data_dir)
        self.price_getter = price_getter
        self.risk_desk = JEVRiskDesk()
        self.path = Path(settings.data_dir) / "signals.json"
        self._lock = threading.Lock()
        self.signals: Dict[str, Dict[str, Any]] = {}
        if self.path.exists():
            try:
                self.signals = json.loads(self.path.read_text(encoding="utf-8"))
            except ValueError:
                self.signals = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.signals, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def pending(self) -> List[Dict[str, Any]]:
        return [s for s in self.signals.values() if s["status"] == "PENDING"]

    # ------------------------------------------------------------------ build
    def build_signal(self, pkg: Dict[str, Any], market: Dict[str, Any]) -> Dict[str, Any]:
        """Create a signal from a research package. Returns the signal (status PENDING or BLOCKED)."""
        a = ASSETS[pkg["asset"]]
        action = pkg["jev"]["direction"]
        entry = market.get("price")
        sid = f"SIG-{a.key}-{int(time.time())}"
        sig: Dict[str, Any] = {
            "signal_id": sid, "asset": a.key, "label": a.label, "action": action, "timeframe": a.timeframe,
            "created_at": time.time(), "score": pkg["jev"]["score"],
            "news_support_pct": pkg["jev"]["news_support_pct"], "engine": pkg["engine"],
            "supporting": pkg["jev"].get("supporting_evidence", [])[:3],
            "opposing": pkg["jev"].get("opposing_evidence", [])[:3],
            "explanation": pkg["jev"].get("explanation", ""),
        }
        if action in ("BUY", "SELL") and entry is not None:
            d = 1 if action == "BUY" else -1
            sl = round(entry - d * a.sl_distance, a.decimals)
            tp = round(entry + d * a.tp_distance, a.decimals)
            inv = round(entry - d * a.sl_distance / 2.0, a.decimals)
            size = position_size(a, entry, sl, self.s.risk_per_trade_usd)
            rr = round(a.tp_distance / a.sl_distance, 2)
            sig.update(entry=entry, sl=sl, tp=tp, invalidation=inv, size=size, rr=rr,
                       risk_usd=risk_of(a, entry, sl, size))
        else:
            sig.update(entry=entry, sl=None, tp=None, invalidation=None, size=0, rr=None, risk_usd=0.0)

        risk = self.risk_desk.check(
            direction=action, risk_usd=sig["risk_usd"], configured_risk_usd=self.s.risk_per_trade_usd,
            open_positions=len(self.broker.open_positions()), in_dead_zone=in_dead_zone(self.s.dead_zone_utc),
            data_ok=bool(market.get("ok")), stale=bool(market.get("stale")),
            invalidation=sig["invalidation"], rr=sig["rr"], min_rr=self.s.min_late_rr,
        )
        sig["risk"] = risk
        sig["risk_rating"] = risk["overall_risk_rating"]
        sig["trend"] = compare_trend(action, market)
        sig["status"] = "PENDING" if risk["overall_risk_rating"] != "FAIL" else "BLOCKED"
        with self._lock:
            self.signals[sid] = sig
            self._save()
        return sig

    def card_text(self, sig: Dict[str, Any]) -> str:
        k = sig["asset"]
        side = "🟢 BUY / LONG" if sig["action"] == "BUY" else "🔴 SELL / SHORT"
        size = f"{sig['size']} lots" if ASSETS[k].is_fx else f"{sig['size']} units"
        pros = "\n".join(f"  • {p}" for p in sig["supporting"]) or "  • —"
        cons = "\n".join(f"  • {c}" for c in sig["opposing"]) or "  • —"
        flags = [f"{n}: {c['note']}" for n, c in sig["risk"]["checks"].items() if c["rating"] != "PASS"]
        return (
            f"🚨 *{sig['label']}* — research signal\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 *Action*: `{side}` ({sig['timeframe']})\n"
            f"📊 *News support*: `{sig['news_support_pct']:.0f}%`  ⚖️ *JEV score*: `{sig['score']:.0f}/100`\n"
            f"🛡️ *Risk desk*: `{sig['risk_rating']}`  🧠 engine: `{sig['engine']}`\n\n"
            f"📈 *Order (fixed ${self.s.risk_per_trade_usd:.0f} risk)*\n"
            f"• Entry `{_fmt(k, sig['entry'])}`  SL `{_fmt(k, sig['sl'])}`  TP `{_fmt(k, sig['tp'])}` ({sig['rr']}R)\n"
            f"• Size `{size}` → stop-out = `${sig['risk_usd']:.2f}`\n"
            f"• Invalidation `{_fmt(k, sig['invalidation'])}`\n\n"
            f"🧭 *Trend check*: {sig['trend']['verdict']}\n\n"
            f"✅ *For*:\n{pros}\n⚠️ *Against*:\n{cons}\n"
            + (f"\n🚩 *Risk flags*: " + "; ".join(flags) + "\n" if flags else "")
            + f"━━━━━━━━━━━━━━━━━━━━\n`{sig['signal_id']}` · simulated research, not financial advice"
        )

    @staticmethod
    def keyboard(sig_id: str) -> Dict[str, Any]:
        return {"inline_keyboard": [
            [{"text": "✅ YES — take it", "callback_data": f"yes:{sig_id}"},
             {"text": "❌ NO — skip", "callback_data": f"no:{sig_id}"}],
            [{"text": "⏳ LATE — reconfirm first", "callback_data": f"late:{sig_id}"}],
        ]}

    # --------------------------------------------------------------- decide
    def decide(self, sig_id: str, decision: str, current_price: Optional[float] = None) -> Dict[str, Any]:
        with self._lock:
            sig = self.signals.get(sig_id)
            if not sig:
                return {"ok": False, "status": "NOT_FOUND", "message": f"Signal `{sig_id}` not found."}
            if sig["status"] != "PENDING":
                return {"ok": False, "status": sig["status"],
                        "message": f"Signal `{sig_id}` is already {sig['status']} — nothing done."}
            decision = decision.lower()
            if decision in ("no", "skip"):
                return self._close(sig, "SKIPPED", "Operator skipped.", ok=True,
                                   message=f"❌ Skipped `{sig_id}`. No order placed.")
            if decision not in ("yes", "late"):
                return {"ok": False, "status": "BAD_DECISION", "message": f"Unknown decision `{decision}`."}
            if in_dead_zone(self.s.dead_zone_utc):
                return self._close(sig, "DEAD_ZONE", "Decision arrived inside the dead zone.",
                                   message="🌙 Dead zone — the desk stays flat. Order refused.")

            age_min = (time.time() - sig["created_at"]) / 60.0
            if decision == "yes" and age_min > self.s.signal_max_age_min:
                return {"ok": False, "status": "STALE",
                        "message": f"⚠️ Card is {age_min:.0f} min old. Press ⏳ LATE so the price is re-checked first."}

            px = current_price if current_price is not None else (self.price_getter(sig["asset"]) if self.price_getter else None)
            if px is None:
                return {"ok": False, "status": "NO_PRICE", "message": "⚠️ No live price available — order refused."}
            return self._reconfirm_and_execute(sig, float(px), full=(decision == "late"))

    def _reconfirm_and_execute(self, sig: Dict[str, Any], px: float, full: bool) -> Dict[str, Any]:
        a = ASSETS[sig["asset"]]
        buy = sig["action"] == "BUY"
        inv, sl, tp, entry = sig["invalidation"], sig["sl"], sig["tp"], sig["entry"]
        if (buy and px <= inv) or (not buy and px >= inv):
            return self._close(sig, "INVALIDATED", f"Price {px} crossed invalidation {inv}.",
                               message=f"🛑 Invalidated: price `{px}` crossed `{inv}`. Do not chase — order refused.")
        reward = (tp - px) if buy else (px - tp)
        risk = (px - sl) if buy else (sl - px)
        rr_left = round(reward / risk, 2) if risk > 0 else 0.0
        travelled = ((px - entry) if buy else (entry - px)) / a.tp_distance
        if full and (travelled >= self.s.runaway_fraction or rr_left < self.s.min_late_rr):
            return self._close(sig, "RUNAWAY", f"Moved {travelled:.0%} to target; {rr_left}R left.",
                               message=(f"🏃 Runaway: price already covered {travelled:.0%} of the move; "
                                        f"{rr_left}R left (min {self.s.min_late_rr}R). Order refused."))
        size = position_size(a, px, sl, self.s.risk_per_trade_usd)
        order = {"signal_id": sig["signal_id"], "asset": sig["asset"], "action": sig["action"],
                 "entry": px, "sl": sl, "tp": tp, "size": size,
                 "risk_usd": risk_of(a, px, sl, size)}
        res = self.broker.submit(order)
        if not res.get("ok"):
            return {"ok": False, "status": "BROKER_ERROR", "message": f"⚠️ Broker refused: {res.get('error')}"}
        sig.update(fill_price=px, size=size, rr=rr_left, trade_id=res["trade_id"])
        label = "LATE_EXECUTED" if full else "EXECUTED"
        return self._close(sig, label, f"{res['broker']} {res['trade_id']} @ {px}", ok=True, message=(
            f"🚀 *{label.replace('_', ' ')}* `{sig['signal_id']}`\n"
            f"{sig['action']} {sig['label']} @ `{px}` · size `{size}` · stop-out `${order['risk_usd']:.2f}`\n"
            f"SL `{sl}` · TP `{tp}` · {rr_left}R · ref `{res['trade_id']}` ({res['broker']})"))

    def _close(self, sig: Dict[str, Any], status: str, notes: str, ok: bool = False, message: str = "") -> Dict[str, Any]:
        sig["status"] = status
        sig["resolved_at"] = time.time()
        self._save()
        self.journal.record(sig, status, notes)
        return {"ok": ok, "status": status, "message": message or notes}
