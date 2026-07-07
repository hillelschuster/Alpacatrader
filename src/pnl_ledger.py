"""
Lightweight P&L ledger persistence for session realized P&L.

Mirrors the ``PositionStore`` JSON pattern — no DB, no scheduler,
no broad refactor.  Used by ``TradingApp`` for startup restore,
shutdown save, and periodic checkpoint (30–60s).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class PnLLedger:
    """Session P&L ledger with optional JSON checkpointing.

    Fields:
      - ``session_realized_pnl``: float — cumulative realized P&L
        across the day (resets only on session boundary).
      - ``per_symbol_pnl``: dict[str, float] — per-symbol realized P&L
        accumulator for per-symbol loss cap enforcement.
      - ``weekly_realized_pnl`` and ``consecutive_losses``: lightweight
        risk-throttle state for roadmap #11.
    """

    def __init__(
        self,
        session_realized_pnl: float = 0.0,
        per_symbol_pnl: Optional[dict[str, float]] = None,
        weekly_realized_pnl: float = 0.0,
        consecutive_losses: int = 0,
        week_id: str = "",
    ) -> None:
        self.session_realized_pnl = session_realized_pnl
        self.per_symbol_pnl = per_symbol_pnl or {}
        self.weekly_realized_pnl = weekly_realized_pnl
        self.consecutive_losses = consecutive_losses
        self.week_id = week_id

    def to_dict(self) -> dict:
        return {
            "session_realized_pnl": self.session_realized_pnl,
            "per_symbol_pnl": self.per_symbol_pnl,
            "weekly_realized_pnl": self.weekly_realized_pnl,
            "consecutive_losses": self.consecutive_losses,
            "week_id": self.week_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PnLLedger":
        return cls(
            session_realized_pnl=float(data.get("session_realized_pnl", 0.0)),
            per_symbol_pnl={
                k: float(v) for k, v in data.get("per_symbol_pnl", {}).items()
            },
            weekly_realized_pnl=float(data.get("weekly_realized_pnl", 0.0)),
            consecutive_losses=int(data.get("consecutive_losses", 0)),
            week_id=str(data.get("week_id", "")),
        )

    def save_to_disk(self, path: str | Path) -> None:
        """Persist ledger to JSON file.  Creates parent dirs if needed."""
        p = Path(path) if isinstance(path, str) else path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load_from_disk(cls, path: str | Path) -> "PnLLedger":
        """Restore ledger from JSON file.  Returns empty if file missing."""
        p = Path(path) if isinstance(path, str) else path
        if not p.exists():
            return cls()
        data = json.loads(p.read_text())
        return cls.from_dict(data)
