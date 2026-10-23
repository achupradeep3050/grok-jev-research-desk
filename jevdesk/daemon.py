"""
The desk daemon: polls the feeds, runs the pipeline, emits operator cards to
Telegram and executes approved orders through the configured broker.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .approval import ApprovalHub, in_dead_zone
from .assets import ASSETS, resolve_asset
from .broker import make_broker
from .config import Settings, get_settings
from .engine import ResearchEngine, build_state
from .feeds.market import MarketData
from .feeds.news import NewsHarvester
from .feeds.predictions import PredictionMarkets
from .llm import LLMClient
from .telegram import TelegramClient

logger = logging.getLogger("jevdesk.daemon")

HELP = (
    "*Grok + JEV research desk*\n"
    "`/eval gold|gbpusd|btc [deep]` — run the 5-stage pipeline (deep = Grok)\n"
    "`/status` — desk advisory, prices, scores\n"
    "`/signals` — pending cards\n"
    "`/positions` — paper / executed positions\n"
    "`/journal` — last journal rows\n"
    "`/yes|/no|/late <signal_id>` — decide a card by text"
)


def quiet_http_loggers() -> None:
    # httpx logs full request URLs at INFO, and Telegram URLs contain the bot token.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


class Desk:
    def __init__(self, settings: Optional[Settings] = None):
        self.s = settings or get_settings()
        self.s.data_dir.mkdir(parents=True, exist_ok=True)
        self.llm = LLMClient(self.s)
        self.market = MarketData()
        self.news_h = NewsHarvester(self.s.data_dir)
        self.pred = PredictionMarkets()
        self.engine = ResearchEngine(self.llm)
        self.broker = make_broker(self.s)
        self.hub = ApprovalHub(self.s, self.broker, price_getter=self.live_price)
        self.tg = TelegramClient(self.s.telegram_bot_token, api_base=self.s.telegram_api_base) if self.s.telegram_enabled else None
        self.markets: Dict[str, Dict[str, Any]] = {}
        self.news: Dict[str, Any] = {}
        self.predictions: Dict[str, Any] = {}
        self.research: Dict[str, Dict[str, Any]] = {}
        self.state: Dict[str, Any] = {}
        self._last: Dict[str, float] = {}
        self._last_card: Dict[str, float] = {}
        self._last_advisory = ""

    # ------------------------------------------------------------- data
    def live_price(self, asset_key: str) -> Optional[float]:
        snap = self.market.snapshot(ASSETS[asset_key])
        return snap.get("price") if snap.get("ok") and not snap.get("stale") else None

    def _due(self, name: str, every: float) -> bool:
        if time.time() - self._last.get(name, 0) >= every:
            self._last[name] = time.time()
            return True
        return False

    def refresh(self, force: bool = False) -> None:
        if force or self._due("market", self.s.market_poll_s):
            self.markets = self.market.snapshot_all()
            closed = self.broker.mark_to_market({k: (m.get("price") if m.get("ok") else None) for k, m in self.markets.items()})
            for c in closed:
                self.notify(f"📕 Closed `{c['signal_id']}` {c['asset']} {c['action']} by {c['reason']} @ `{c['exit']}` · P&L `${c['pnl_usd']:+.2f}`")
        if force or self._due("news", self.s.news_poll_s):
            self.news = self.news_h.poll()
        if force or self._due("pred", self.s.predictions_poll_s):
            self.predictions = self.pred.scan()
        self.research = {k: self.engine.research(a, self.news, self.markets.get(k, {}), use_llm=False)
                         for k, a in ASSETS.items()}
        self.state = build_state(self.news, self.markets, self.predictions, self.research)
        self.save_state()

    def save_state(self) -> None:
        path = self.s.data_dir / "state.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, path)

    # ------------------------------------------------------- telegram out
    def notify(self, text: str, markup: Optional[Dict[str, Any]] = None) -> None:
        if not self.tg:
            logger.info("[notify] %s", text.splitlines()[0] if text else "")
            return
        for chat in self.s.broadcast_ids:
            self.tg.send(chat, text, reply_markup=markup)

    def emit_card(self, pkg: Dict[str, Any]) -> Dict[str, Any]:
        sig = self.hub.build_signal(pkg, self.markets.get(pkg["asset"]) or self.market.snapshot(ASSETS[pkg["asset"]]))
        if sig["status"] == "PENDING":
            self.notify(self.hub.card_text(sig), self.hub.keyboard(sig["signal_id"]))
        return sig

    def auto_signals(self) -> None:
        if in_dead_zone(self.s.dead_zone_utc) or self.state.get("execution_advisory") == "HALT_OR_CLOSE":
            return
        for key in self.s.auto_assets:
            pkg = self.research.get(key)
            if not pkg:
                continue
            jev = pkg["jev"]
            if jev["direction"] not in ("BUY", "SELL"):
                continue
            if jev["score"] < self.s.signal_min_score or jev["news_support_pct"] < self.s.signal_min_news_support:
                continue
            if time.time() - self._last_card.get(key, 0) < self.s.signal_cooldown_min * 60:
                continue
            if self.s.llm_in_loop and self.llm.enabled:
                pkg = self.engine.research(ASSETS[key], self.news, self.markets.get(key, {}), use_llm=True)
                if pkg["jev"]["direction"] not in ("BUY", "SELL"):
                    continue
            self._last_card[key] = time.time()
            self.emit_card(pkg)

    def broadcast_advisory(self) -> None:
        adv = self.state.get("execution_advisory", "")
        if adv == self._last_advisory:
            return
        self._last_advisory = adv
        if adv in ("HALT_OR_CLOSE", "TIGHTEN_SL"):
            icon = "🚨" if adv == "HALT_OR_CLOSE" else "⚠️"
            self.notify(f"{icon} *Desk advisory: {adv}*\n{self.state.get('advisory_reason')}")

    # -------------------------------------------------------- telegram in
    def authorized(self, user_id: Optional[int]) -> bool:
        return user_id is not None and user_id in self.s.telegram_operator_ids

    def handle_update(self, upd: Dict[str, Any]) -> None:
        if self.tg is None:
            return
        cb = upd.get("callback_query")
        if cb:
            uid = (cb.get("from") or {}).get("id")
            if not self.authorized(uid):
                self.tg.answer_callback(cb["id"], "⛔ Only the desk operator can decide signals.")
                logger.warning("unauthorized button press from user %s", uid)
                return
            decision, _, sid = (cb.get("data") or "").partition(":")
            res = self.hub.decide(sid, decision)
            self.tg.answer_callback(cb["id"], res["status"])
            msg = cb.get("message") or {}
            if msg:
                self.tg.edit(msg["chat"]["id"], msg["message_id"], res["message"])
            return
        msg = upd.get("message") or {}
        text = (msg.get("text") or "").strip()
        if not text:
            return
        if not self.authorized((msg.get("from") or {}).get("id")):
            return  # silently ignore everyone except the operator(s)
        reply = self.command(text)
        if reply:
            self.tg.send(msg["chat"]["id"], reply, reply_to=msg.get("message_id"))

    def command(self, text: str) -> str:
        parts = text.split()
        cmd = parts[0].split("@")[0].lower()
        args = parts[1:]
        if cmd in ("/start", "/help"):
            return HELP
        if cmd == "/status":
            lines = [f"🛡️ `{self.state.get('macro_risk_level')}` · `{self.state.get('execution_advisory')}`",
                     self.state.get("advisory_reason", "")]
            for k, r in self.state.get("research", {}).items():
                m = self.state["markets"].get(k, {})
                px = m.get("price") if m.get("ok") else "unavailable"
                lines.append(f"• *{k}* `{px}` · {r['decision']} · score `{r['score']:.0f}` · news `{r['news_support_pct']:.0f}%` · trend `{r['trend']}`")
            return "\n".join(lines)
        if cmd in ("/eval", "/research"):
            asset = resolve_asset(args[0]) if args else ASSETS["GOLD"]
            if not asset:
                return "Unknown asset. Use gold, gbpusd or btc."
            deep = any(a.lower() in ("deep", "grok", "llm") for a in args[1:])
            if deep and not self.llm.enabled:
                return "No LLM configured (set XAI_API_KEY or LLM_BASE_URL). Running without `deep`."
            threading.Thread(target=self._eval_worker, args=(asset.key, deep), daemon=True).start()
            return f"🧠 Running the {'Grok' if deep else 'deterministic'} pipeline for {asset.label}…"
        if cmd == "/signals":
            p = self.hub.pending()
            return "\n".join(f"• `{s['signal_id']}` {s['asset']} {s['action']} @ `{s['entry']}` · score {s['score']:.0f}"
                             for s in p) or "No pending signals."
        if cmd == "/positions":
            pos = self.broker.open_positions()
            return "\n".join(f"• {p['asset']} {p['action']} {p['size']} @ `{p['entry']}` SL `{p['sl']}` TP `{p['tp']}`"
                             for p in pos) or "No open positions."
        if cmd == "/journal":
            rows = self.hub.journal.tail()
            return "```\n" + "\n".join(rows) + "\n```" if rows else "Journal is empty."
        if cmd in ("/yes", "/no", "/late"):
            if not args:
                return f"Usage: `{cmd} <signal_id>`"
            return self.hub.decide(args[0], cmd[1:])["message"]
        return HELP

    def _eval_worker(self, key: str, deep: bool) -> None:
        try:
            snap = self.market.snapshot(ASSETS[key])
            self.markets[key] = snap
            news = self.news or self.news_h.poll()
            pkg = self.engine.research(ASSETS[key], news, snap, use_llm=deep)
            jev = pkg["jev"]
            if jev["direction"] in ("BUY", "SELL"):
                sig = self.emit_card(pkg)
                if sig["status"] == "BLOCKED":
                    flags = "; ".join(c["note"] for c in sig["risk"]["checks"].values() if c["rating"] == "FAIL")
                    self.notify(f"🛡️ Risk desk blocked {key} {jev['direction']}: {flags}")
            else:
                self.notify(f"⚖️ *{ASSETS[key].label}* — `{jev['decision']}` (score `{jev['score']:.0f}`, engine `{pkg['engine']}`)\n{jev['explanation']}")
        except Exception as exc:
            logger.exception("eval failed")
            self.notify(f"⚠️ Evaluation for {key} failed: {exc.__class__.__name__}")

    # ------------------------------------------------------------- loop
    def run(self) -> None:
        quiet_http_loggers()
        logger.info("desk starting · data=%s · llm=%s (%s) · telegram=%s · execution=%s",
                    self.s.data_dir, self.llm.enabled, self.s.llm_model, bool(self.tg), self.broker.name)
        self.refresh(force=True)
        self.notify("⚡ *Grok + JEV research desk online*\n" + HELP)
        while True:
            try:
                self.refresh()
                self.broadcast_advisory()
                self.auto_signals()
                if self.tg:
                    for upd in self.tg.updates():
                        self.handle_update(upd)
                else:
                    time.sleep(5)
            except KeyboardInterrupt:
                logger.info("stopping")
                return
            except Exception:
                logger.exception("loop error")
                time.sleep(3)
