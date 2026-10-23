"""
Runtime configuration.

Everything comes from environment variables (optionally loaded from a `.env`
file). Nothing personal or secret is ever hard-coded in this package.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple


def load_dotenv(path: Optional[Path] = None) -> Optional[Path]:
    """Minimal `.env` loader (KEY=VALUE lines). Existing env vars win."""
    candidates = [path] if path else [
        Path(os.environ.get("JEVDESK_ENV_FILE", "")) if os.environ.get("JEVDESK_ENV_FILE") else None,
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ]
    for p in candidates:
        if p and p.is_file():
            for raw in p.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                if key.startswith("export "):
                    key = key[len("export "):].strip()
                val = val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                    val = val[1:-1]
                os.environ.setdefault(key, val)
            return p
    return None


def _int_list(raw: str) -> List[int]:
    out: List[int] = []
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if part:
            try:
                out.append(int(part))
            except ValueError:
                pass
    return out


def _bool(raw: Optional[str], default: bool) -> bool:
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _dead_zone(raw: str) -> Optional[Tuple[float, float]]:
    """'20-24' -> (20.0, 24.0); '' or 'off' -> None."""
    raw = (raw or "").strip().lower()
    if not raw or raw in ("off", "none", "0"):
        return None
    try:
        a, b = raw.split("-", 1)
        return float(a), float(b)
    except ValueError:
        return None


@dataclass
class Settings:
    # LLM (any OpenAI-compatible endpoint; xAI Grok by default)
    llm_api_key: str = ""
    llm_base_url: str = "https://api.x.ai/v1"
    llm_model: str = "grok-4.20-non-reasoning"
    llm_timeout: float = 45.0
    llm_in_loop: bool = False

    # Telegram
    telegram_bot_token: str = ""
    telegram_api_base: str = "https://api.telegram.org"
    telegram_operator_ids: List[int] = field(default_factory=list)
    telegram_broadcast_chat_ids: List[int] = field(default_factory=list)

    # Risk & signal policy
    risk_per_trade_usd: float = 100.0
    dead_zone_utc: Optional[Tuple[float, float]] = (20.0, 24.0)
    min_late_rr: float = 1.2
    runaway_fraction: float = 0.55
    signal_min_score: float = 68.0
    signal_min_news_support: float = 65.0
    signal_cooldown_min: float = 45.0
    signal_max_age_min: float = 25.0
    auto_assets: List[str] = field(default_factory=lambda: ["GOLD", "GBPUSD"])

    # Execution
    execution_mode: str = "paper"          # paper | webhook
    execution_webhook_url: str = ""
    execution_webhook_secret: str = ""

    # Loop cadence (seconds)
    market_poll_s: float = 30.0
    news_poll_s: float = 60.0
    predictions_poll_s: float = 120.0

    data_dir: Path = Path("data")

    @property
    def llm_enabled(self) -> bool:
        # Local servers (Ollama, LM Studio, a proxy) often need no key.
        local = any(h in self.llm_base_url for h in ("localhost", "127.0.0.1", "0.0.0.0"))
        return bool(self.llm_api_key) or local

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token) and bool(self.telegram_operator_ids)

    @property
    def broadcast_ids(self) -> List[int]:
        return self.telegram_broadcast_chat_ids or list(self.telegram_operator_ids)


def get_settings(reload_env: bool = True) -> Settings:
    if reload_env:
        load_dotenv()
    env = os.environ.get
    data_dir = Path(env("JEVDESK_DATA_DIR", "data")).expanduser()
    return Settings(
        llm_api_key=env("XAI_API_KEY", "") or env("LLM_API_KEY", ""),
        llm_base_url=(env("LLM_BASE_URL", "https://api.x.ai/v1") or "https://api.x.ai/v1").rstrip("/"),
        llm_model=env("LLM_MODEL", "grok-4.20-non-reasoning") or "grok-4.20-non-reasoning",
        llm_timeout=_float("LLM_TIMEOUT_S", 45.0),
        llm_in_loop=_bool(env("LLM_IN_LOOP"), False),
        telegram_bot_token=env("TELEGRAM_BOT_TOKEN", ""),
        telegram_api_base=env("TELEGRAM_API_BASE", "https://api.telegram.org") or "https://api.telegram.org",
        telegram_operator_ids=_int_list(env("TELEGRAM_OPERATOR_IDS", "")),
        telegram_broadcast_chat_ids=_int_list(env("TELEGRAM_BROADCAST_CHAT_IDS", "")),
        risk_per_trade_usd=_float("RISK_PER_TRADE_USD", 100.0),
        dead_zone_utc=_dead_zone(env("DEAD_ZONE_UTC", "20-24")),
        min_late_rr=_float("MIN_LATE_RR", 1.2),
        runaway_fraction=_float("RUNAWAY_FRACTION", 0.55),
        signal_min_score=_float("SIGNAL_MIN_SCORE", 68.0),
        signal_min_news_support=_float("SIGNAL_MIN_NEWS_SUPPORT", 65.0),
        signal_cooldown_min=_float("SIGNAL_COOLDOWN_MIN", 45.0),
        signal_max_age_min=_float("SIGNAL_MAX_AGE_MIN", 25.0),
        auto_assets=[a.strip().upper() for a in env("AUTO_ASSETS", "GOLD,GBPUSD").split(",") if a.strip()],
        execution_mode=(env("EXECUTION_MODE", "paper") or "paper").lower(),
        execution_webhook_url=env("EXECUTION_WEBHOOK_URL", ""),
        execution_webhook_secret=env("EXECUTION_WEBHOOK_SECRET", ""),
        market_poll_s=_float("MARKET_POLL_S", 30.0),
        news_poll_s=_float("NEWS_POLL_S", 60.0),
        predictions_poll_s=_float("PREDICTIONS_POLL_S", 120.0),
        data_dir=data_dir,
    )
