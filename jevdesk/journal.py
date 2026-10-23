"""Research journal: one markdown table row per operator decision (for later review)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

HEADER = (
    "# Grok + JEV research journal\n\n"
    "Every signal card and what the operator did with it. Review each row after 30 days.\n\n"
    "| UTC | ASSET | ACTION | SCORE | NEWS % | RISK DESK | INVALIDATION | DECISION | NOTES | 30-DAY REVIEW |\n"
    "|---|---|---|---|---|---|---|---|---|---|\n"
)


def _cell(v: Any, n: int = 60) -> str:
    return str(v).replace("|", "/").replace("\n", " ")[:n]


class Journal:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "journal.md"

    def record(self, sig: Dict[str, Any], decision: str, notes: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text(HEADER, encoding="utf-8")
        row = (f"| {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} | {sig.get('asset')} | {sig.get('action')} | "
               f"{sig.get('score', 0):.0f} | {sig.get('news_support_pct', 0):.0f} | {sig.get('risk_rating', '-')} | "
               f"{sig.get('invalidation')} | {decision} | {_cell(notes)} | pending |\n")
        with self.path.open("a", encoding="utf-8") as f:
            f.write(row)

    def tail(self, n: int = 6) -> List[str]:
        if not self.path.exists():
            return []
        return [ln for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.startswith("| 2")][-n:]
