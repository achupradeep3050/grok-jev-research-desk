"""
Command line:

  python -m jevdesk run                 start the desk daemon
  python -m jevdesk eval gold [--llm]   one research pass, printed as JSON summary
  python -m jevdesk doctor              check config, data feeds, LLM and Telegram
  python -m jevdesk selftest            offline checks of the safety logic
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
import time
from pathlib import Path

from . import __version__
from .config import get_settings


def _mask(v: str) -> str:
    return "(not set)" if not v else f"set ({len(v)} chars)"


def cmd_run(_: argparse.Namespace) -> int:
    from .daemon import Desk
    Desk().run()
    return 0


def cmd_eval(a: argparse.Namespace) -> int:
    from .assets import resolve_asset
    from .daemon import quiet_http_loggers
    from .engine import ResearchEngine
    from .feeds.market import MarketData
    from .feeds.news import NewsHarvester
    from .llm import LLMClient

    quiet_http_loggers()
    s = get_settings()
    asset = resolve_asset(a.asset)
    if not asset:
        print("unknown asset (use gold, gbpusd or btc)")
        return 2
    llm = LLMClient(s)
    if a.llm and not llm.enabled:
        print("--llm requested but no LLM is configured (XAI_API_KEY / LLM_BASE_URL)")
        return 2
    snap = MarketData().snapshot(asset)
    news = NewsHarvester(s.data_dir).poll()
    pkg = ResearchEngine(llm).research(asset, news, snap, use_llm=a.llm)
    out = {
        "asset": pkg["label"], "engine": pkg["engine"], "elapsed_s": pkg["elapsed_s"],
        "price": snap.get("price"), "data_ok": snap.get("ok"), "stale": snap.get("stale"),
        "macro": {k: pkg["macro"].get(k) for k in ("macro_bias", "macro_support_score", "engine")},
        "news": {k: pkg["news"].get(k) for k in ("news_stance", "news_support_pct", "confidence",
                                                  "injections_blocked", "engine")},
        "chart": {k: pkg["chart"].get(k) for k in ("technical_bias", "chart_support_score", "what_numbers_show", "engine")},
        "jev": {k: pkg["jev"].get(k) for k in ("score", "weights", "decision", "explanation", "invalidation_condition", "engine")},
        "trend": pkg["trend"],
    }
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_doctor(_: argparse.Namespace) -> int:
    import httpx

    from .assets import ASSETS
    from .daemon import quiet_http_loggers
    from .feeds.market import MarketData
    from .feeds.news import NewsHarvester
    from .feeds.predictions import PredictionMarkets
    from .llm import LLMClient
    from .telegram import TelegramClient

    quiet_http_loggers()
    s = get_settings()
    ok = True
    print(f"jevdesk {__version__} · python {sys.version.split()[0]}")
    print(f"data dir          : {s.data_dir.resolve()}")
    print(f"risk per trade    : ${s.risk_per_trade_usd:.2f} · dead zone UTC {s.dead_zone_utc} · execution {s.execution_mode}")
    print(f"LLM               : {s.llm_base_url} · model {s.llm_model} · key {_mask(s.llm_api_key)}")
    print(f"Telegram token    : {_mask(s.telegram_bot_token)} · operators {len(s.telegram_operator_ids)}")
    print("-- data feeds")
    md = MarketData()
    for k, a in ASSETS.items():
        snap = md.snapshot(a)
        state = "OK" if snap.get("ok") else f"FAIL ({snap.get('error')})"
        if snap.get("stale"):
            state += " (stale: market closed?)"
        print(f"  {k:<7} {a.symbol:<9} {state:<28} price={snap.get('price')}")
        ok &= bool(snap.get("ok"))
    news = NewsHarvester(Path(tempfile.mkdtemp())).poll()
    print(f"  news    calendar events today={len(news['calendar_today'])} gold={len(news['gold_news'])} "
          f"gbp={len(news['gbp_news'])} btc={len(news['btc_news'])}")
    print(f"  polymarket {'OK' if PredictionMarkets().scan().get('ok') else 'FAIL'}")
    print("-- LLM")
    llm = LLMClient(s)
    if llm.enabled:
        t0 = time.time()
        res = llm.chat_json("You are a health check.", 'Reply with {"ok": true}.', timeout=60)
        good = bool(res and res.get("ok") is True)
        print(f"  {'OK' if good else 'FAIL'} ({time.time() - t0:.1f}s)")
        ok &= good
    else:
        print("  not configured — the desk runs its deterministic engine only")
    print("-- Telegram")
    if s.telegram_bot_token:
        me = TelegramClient(s.telegram_bot_token, api_base=s.telegram_api_base).get_me()
        print(f"  {'OK @' + me['username'] if me else 'FAIL (token rejected?)'}")
        ok &= bool(me)
        if not s.telegram_operator_ids:
            print("  WARNING: TELEGRAM_OPERATOR_IDS is empty — the bot will not start")
            ok = False
    else:
        print("  not configured — cards are logged to the console only")
    try:
        httpx.get("https://api.github.com", timeout=5)
    except Exception:
        print("WARNING: no internet connectivity")
    print("RESULT:", "HEALTHY" if ok else "PROBLEMS FOUND")
    return 0 if ok else 1


def cmd_selftest(_: argparse.Namespace) -> int:
    from .selftest import run
    return run()


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="jevdesk", description="Grok + JEV multi-agent research desk")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("run", help="start the desk daemon").set_defaults(fn=cmd_run)
    e = sub.add_parser("eval", help="one research pass")
    e.add_argument("asset", nargs="?", default="gold")
    e.add_argument("--llm", action="store_true", help="use Grok / your LLM for every pass")
    e.set_defaults(fn=cmd_eval)
    sub.add_parser("doctor", help="check configuration and connectivity").set_defaults(fn=cmd_doctor)
    sub.add_parser("selftest", help="offline safety-logic checks").set_defaults(fn=cmd_selftest)
    a = p.parse_args(argv)
    if not getattr(a, "fn", None):
        p.print_help()
        return 0
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
