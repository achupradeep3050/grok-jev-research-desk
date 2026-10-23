"""
Market data: candles, indicators and futures positioning from free public APIs.

- Gold and Bitcoin: Binance USD-M futures (XAUUSDT, BTCUSDT) — klines, 24h
  ticker, open interest, top-trader long/short and taker buy/sell ratios.
- GBPUSD: Yahoo Finance 5-minute chart.

If a feed fails, the asset is reported as unavailable (`ok: False`). The desk
never substitutes a made-up price.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from ..assets import ASSETS, Asset

logger = logging.getLogger("jevdesk.market")

BINANCE = "https://fapi.binance.com"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"
UA = {"User-Agent": "Mozilla/5.0 (compatible; jevdesk/1.0; +https://github.com/achupradeep3050/grok-jev-research-desk)"}


def ema(values: List[float], span: int) -> Optional[float]:
    if not values:
        return None
    k = 2.0 / (span + 1.0)
    out = values[0]
    for v in values[1:]:
        out = v * k + out * (1 - k)
    return out


def atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Optional[float]:
    trs = [
        max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        for i in range(1, len(closes))
    ]
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def chart_metrics(candles: List[Dict[str, float]], decimals: int, range_bars: int = 30) -> Dict[str, Any]:
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    price = closes[-1]
    hi = max(highs[-range_bars:])
    lo = min(lows[-range_bars:])
    r = lambda x: None if x is None else round(float(x), decimals)  # noqa: E731
    return {
        "price": r(price),
        "ema_9": r(ema(closes, 9)),
        "ema_21": r(ema(closes, 21)),
        "ema_50": r(ema(closes, 50)),
        "ema_200": r(ema(closes, 200)) if len(closes) >= 200 else None,
        "atr": r(atr(highs, lows, closes)),
        "range_high": r(hi),
        "range_low": r(lo),
        "range_midline": r((hi + lo) / 2.0),
        "range_bars": range_bars,
        "bars": len(candles),
        "last_bar_epoch": candles[-1].get("t"),
    }


class MarketData:
    def __init__(self, client: Optional[httpx.Client] = None):
        self.client = client or httpx.Client(timeout=10.0, headers=UA, follow_redirects=True)

    # ---- raw fetchers -------------------------------------------------
    def binance_klines(self, symbol: str, interval: str, limit: int = 250) -> List[Dict[str, float]]:
        r = self.client.get(f"{BINANCE}/fapi/v1/klines", params={"symbol": symbol, "interval": interval, "limit": limit})
        r.raise_for_status()
        return [
            {"t": k[0] / 1000.0, "open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4])}
            for k in r.json()
        ]

    def yahoo_candles(self, symbol: str, interval: str = "5m", rng: str = "5d") -> List[Dict[str, float]]:
        r = self.client.get(YAHOO + symbol, params={"interval": interval, "range": rng})
        r.raise_for_status()
        res = r.json()["chart"]["result"][0]
        ts = res.get("timestamp") or []
        q = res["indicators"]["quote"][0]
        out = []
        for i, t in enumerate(ts):
            o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
            if None in (o, h, l, c):
                continue
            out.append({"t": float(t), "open": o, "high": h, "low": l, "close": c})
        return out[-250:]

    def binance_positioning(self, symbol: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "change_24h_pct": None, "open_interest": None,
            "top_trader_long_pct": None, "top_trader_short_pct": None, "top_trader_ratio": None,
            "taker_buy_ratio": None,
        }
        try:
            t = self.client.get(f"{BINANCE}/fapi/v1/ticker/24hr", params={"symbol": symbol})
            if t.status_code == 200:
                out["change_24h_pct"] = round(float(t.json().get("priceChangePercent", 0.0)), 2)
            oi = self.client.get(f"{BINANCE}/fapi/v1/openInterest", params={"symbol": symbol})
            if oi.status_code == 200:
                out["open_interest"] = float(oi.json().get("openInterest", 0.0))
            ls = self.client.get(f"{BINANCE}/futures/data/topLongShortPositionRatio",
                                 params={"symbol": symbol, "period": "1h", "limit": 1})
            if ls.status_code == 200 and ls.json():
                item = ls.json()[0]
                out["top_trader_long_pct"] = round(float(item["longAccount"]) * 100, 1)
                out["top_trader_short_pct"] = round(float(item["shortAccount"]) * 100, 1)
                out["top_trader_ratio"] = round(float(item["longShortRatio"]), 2)
            tk = self.client.get(f"{BINANCE}/futures/data/takerlongshortRatio",
                                 params={"symbol": symbol, "period": "1h", "limit": 1})
            if tk.status_code == 200 and tk.json():
                out["taker_buy_ratio"] = round(float(tk.json()[0]["buySellRatio"]), 2)
        except Exception as exc:
            logger.warning("positioning %s: %s", symbol, exc.__class__.__name__)
        return out

    # ---- per-asset snapshot -------------------------------------------
    def snapshot(self, asset: Asset) -> Dict[str, Any]:
        snap: Dict[str, Any] = {"asset": asset.key, "label": asset.label, "symbol": asset.symbol,
                                "timeframe": asset.timeframe, "ok": False, "fetched_at": time.time()}
        try:
            if asset.source == "binance_futures":
                candles = self.binance_klines(asset.symbol, asset.timeframe)
            else:
                candles = self.yahoo_candles(asset.symbol, asset.timeframe)
            if len(candles) < 60:
                snap["error"] = f"only {len(candles)} candles"
                return snap
            snap.update(chart_metrics(candles, asset.decimals))
            age = time.time() - float(snap.get("last_bar_epoch") or 0)
            snap["stale"] = age > 3 * 3600  # e.g. FX weekend close
            if asset.source == "binance_futures":
                snap.update(self.binance_positioning(asset.symbol))
            else:
                first = candles[-min(len(candles), 288)]["open"]  # ~24h of 5m bars
                snap["change_24h_pct"] = round((snap["price"] - first) / first * 100, 2)
            snap["positioning_bias"] = positioning_bias(snap)
            snap["ok"] = True
        except Exception as exc:
            snap["error"] = exc.__class__.__name__
            logger.warning("market snapshot %s failed: %s", asset.key, exc)
        return snap

    def snapshot_all(self) -> Dict[str, Dict[str, Any]]:
        return {key: self.snapshot(a) for key, a in ASSETS.items()}


def positioning_bias(m: Dict[str, Any]) -> str:
    """Classify Binance top-trader / taker positioning. NEUTRAL when unavailable."""
    long_pct = m.get("top_trader_long_pct")
    taker = m.get("taker_buy_ratio")
    if long_pct is None:
        return "UNAVAILABLE"
    if long_pct >= 60.0 and (taker or 1.0) >= 1.0:
        return "STRONG_BULLISH"
    if long_pct >= 55.0:
        return "BULLISH"
    if long_pct <= 40.0 or (taker is not None and taker <= 0.8):
        return "STRONG_BEARISH"
    if long_pct <= 45.0:
        return "BEARISH"
    return "BALANCED"
