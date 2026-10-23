"""
Instrument definitions: where each asset's data comes from and how its
bracket (stop / target) and position size are expressed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class Asset:
    key: str                 # GOLD | BTC | GBPUSD
    label: str               # human label shown on cards
    source: str              # binance_futures | yahoo
    symbol: str              # feed symbol
    timeframe: str           # candle timeframe used by the chart researcher
    sl_distance: float       # default stop distance in price units
    tp_distance: float       # default target distance in price units
    news_key: str            # key in the news bundle
    is_fx: bool = False      # FX: size in lots (pip value $10 / lot); else units
    pip_size: float = 0.0001
    decimals: int = 2


ASSETS: Dict[str, Asset] = {
    "GOLD": Asset(
        key="GOLD", label="XAUUSD (Gold)", source="binance_futures", symbol="XAUUSDT",
        timeframe="3m", sl_distance=4.0, tp_distance=8.0, news_key="gold_news", decimals=2,
    ),
    "BTC": Asset(
        key="BTC", label="BTCUSD (Bitcoin)", source="binance_futures", symbol="BTCUSDT",
        timeframe="15m", sl_distance=400.0, tp_distance=800.0, news_key="btc_news", decimals=1,
    ),
    "GBPUSD": Asset(
        key="GBPUSD", label="GBPUSD (Pound Sterling)", source="yahoo", symbol="GBPUSD=X",
        timeframe="5m", sl_distance=0.0015, tp_distance=0.0030, news_key="gbp_news",
        is_fx=True, pip_size=0.0001, decimals=5,
    ),
}

ALIASES = {
    "XAU": "GOLD", "XAUUSD": "GOLD", "GOLD": "GOLD",
    "BTC": "BTC", "BTCUSD": "BTC", "BTCUSDT": "BTC", "BITCOIN": "BTC",
    "GBP": "GBPUSD", "GBPUSD": "GBPUSD", "CABLE": "GBPUSD",
}


def resolve_asset(name: str) -> Optional[Asset]:
    key = ALIASES.get((name or "").strip().upper())
    return ASSETS.get(key) if key else None


def position_size(asset: Asset, entry: float, stop: float, risk_usd: float) -> float:
    """Fixed-dollar-risk sizing: the stop being hit loses exactly `risk_usd`.

    FX: lots = risk / (stop_pips * $10 per pip per standard lot), 0.01 minimum step.
    Others: units = risk / |entry - stop|  (1 unit = 1 oz / 1 coin), 3 decimals.
    """
    dist = abs(entry - stop)
    if dist <= 0:
        raise ValueError("stop must differ from entry")
    if asset.is_fx:
        pips = dist / asset.pip_size
        return max(0.01, round(risk_usd / (pips * 10.0), 2))
    return max(0.001, round(risk_usd / dist, 3))


def risk_of(asset: Asset, entry: float, stop: float, size: float) -> float:
    """Dollar loss if the stop is hit for a given size (inverse of position_size)."""
    dist = abs(entry - stop)
    if asset.is_fx:
        return round(dist / asset.pip_size * 10.0 * size, 2)
    return round(dist * size, 2)
