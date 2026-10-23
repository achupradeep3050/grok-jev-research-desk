"""
Execution adapters.

- PaperBroker (default): a local JSON ledger. Positions are opened at the
  approved price and closed by the daemon when price touches the stop or the
  target. Nothing leaves your machine.
- WebhookBroker: POSTs the approved order as JSON to your own executor
  (MT5 bridge, TradingView webhook relay, broker API...) with an
  HMAC-SHA256 signature header `X-JEV-Signature`. The paper ledger still
  records every order.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from .assets import ASSETS


def _atomic_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class PaperBroker:
    name = "paper"

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "paper_ledger.json"
        self._lock = threading.Lock()
        self.ledger: Dict[str, Any] = {"positions": [], "closed": [], "next_id": 1}
        if self.path.exists():
            try:
                self.ledger = json.loads(self.path.read_text(encoding="utf-8"))
            except ValueError:
                pass

    def _save(self) -> None:
        _atomic_write(self.path, self.ledger)

    def open_positions(self) -> List[Dict[str, Any]]:
        return list(self.ledger["positions"])

    def submit(self, order: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            pid = self.ledger["next_id"]
            self.ledger["next_id"] = pid + 1
            pos = {"id": pid, "opened_at": time.time(), **order}
            self.ledger["positions"].append(pos)
            self._save()
        return {"ok": True, "broker": self.name, "trade_id": f"PAPER-{pid}"}

    def mark_to_market(self, prices: Dict[str, Optional[float]]) -> List[Dict[str, Any]]:
        """Close positions whose stop or target was touched. Returns the closed trades."""
        closed = []
        with self._lock:
            keep = []
            for p in self.ledger["positions"]:
                px = prices.get(p["asset"])
                if px is None:
                    keep.append(p)
                    continue
                buy = p["action"] == "BUY"
                hit_sl = px <= p["sl"] if buy else px >= p["sl"]
                hit_tp = px >= p["tp"] if buy else px <= p["tp"]
                if not (hit_sl or hit_tp):
                    keep.append(p)
                    continue
                exit_px = p["sl"] if hit_sl else p["tp"]
                a = ASSETS[p["asset"]]
                move = (exit_px - p["entry"]) if buy else (p["entry"] - exit_px)
                pnl = move / a.pip_size * 10.0 * p["size"] if a.is_fx else move * p["size"]
                rec = {**p, "exit": exit_px, "closed_at": time.time(), "reason": "SL" if hit_sl else "TP",
                       "pnl_usd": round(pnl, 2)}
                self.ledger["closed"].append(rec)
                closed.append(rec)
            if closed:
                self.ledger["positions"] = keep
                self._save()
        return closed


class WebhookBroker(PaperBroker):
    name = "webhook"

    def __init__(self, data_dir: Path, url: str, secret: str):
        super().__init__(data_dir)
        if not url or not secret:
            raise ValueError("EXECUTION_WEBHOOK_URL and EXECUTION_WEBHOOK_SECRET are required for webhook mode")
        self.url, self.secret = url, secret

    def submit(self, order: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps({"source": "jevdesk", "sent_at": time.time(), **order}, sort_keys=True).encode()
        sig = hmac.new(self.secret.encode(), body, hashlib.sha256).hexdigest()
        try:
            r = httpx.post(self.url, content=body, timeout=10.0,
                           headers={"Content-Type": "application/json", "X-JEV-Signature": sig})
            ok = 200 <= r.status_code < 300
        except Exception:
            ok = False
        if not ok:
            return {"ok": False, "broker": self.name, "error": "webhook did not accept the order"}
        res = super().submit(order)
        res["broker"] = self.name
        return res


def make_broker(settings) -> PaperBroker:
    if settings.execution_mode == "webhook":
        return WebhookBroker(settings.data_dir, settings.execution_webhook_url, settings.execution_webhook_secret)
    return PaperBroker(settings.data_dir)
