"""Per-setup analytics report built from decision-log records.

SPEC §11.17.13 item #5 asks for setup-level analytics.  The decision log
already carries entry setup and exit PnL/R fields; this module aggregates those
records without changing trading behavior.
"""

from __future__ import annotations

from collections.abc import Iterable

from src.models.schemas import DecisionRecord


class SetupStats:
    """Aggregated realized performance for one entry setup."""

    def __init__(self, setup: str) -> None:
        self.setup = setup
        self.entries = 0
        self.exits = 0
        self.wins = 0
        self.losses = 0
        self.total_pnl = 0.0
        self.total_r = 0.0
        self.gross_profit_r = 0.0
        self.gross_loss_r = 0.0
        self.best_r: float | None = None
        self.worst_r: float | None = None
        self.total_entry_risk = 0.0
        self._pnl_count = 0
        self._r_count = 0
        self._attention_sum = 0.0
        self._attention_count = 0

    def add_entry(self, record: DecisionRecord) -> None:
        self.entries += 1
        if record.entry_risk_amount is not None:
            self.total_entry_risk += record.entry_risk_amount
        if record.attention_score is not None:
            self._attention_sum += record.attention_score
            self._attention_count += 1

    def add_exit(self, record: DecisionRecord) -> None:
        self.exits += 1
        if record.exit_pnl is not None:
            self.total_pnl += record.exit_pnl
            self._pnl_count += 1
        if record.exit_pnl_r is None:
            return

        pnl_r = record.exit_pnl_r
        self.total_r += pnl_r
        self._r_count += 1
        self.best_r = pnl_r if self.best_r is None else max(self.best_r, pnl_r)
        self.worst_r = pnl_r if self.worst_r is None else min(self.worst_r, pnl_r)
        if pnl_r > 0:
            self.wins += 1
            self.gross_profit_r += pnl_r
        elif pnl_r < 0:
            self.losses += 1
            self.gross_loss_r += pnl_r

    @property
    def win_rate_pct(self) -> float:
        scored = self.wins + self.losses
        if scored == 0:
            return 0.0
        return self.wins / scored * 100.0

    @property
    def avg_pnl(self) -> float:
        if self._pnl_count == 0:
            return 0.0
        return self.total_pnl / self._pnl_count

    @property
    def avg_r(self) -> float:
        if self._r_count == 0:
            return 0.0
        return self.total_r / self._r_count

    @property
    def avg_attention(self) -> float | None:
        if self._attention_count == 0:
            return None
        return self._attention_sum / self._attention_count

    @property
    def profit_factor(self) -> float | None:
        if self.gross_loss_r == 0:
            return None
        return self.gross_profit_r / abs(self.gross_loss_r)


def summarize_by_setup(records: Iterable[DecisionRecord]) -> list[SetupStats]:
    """Group decision records by entry setup and realized exit outcome.

    Exit records emitted by the monitor path may not repeat ``entry_setup``.
    In that case, the most recent open setup for the same symbol is used.
    """

    stats_by_setup: dict[str, SetupStats] = {}
    open_setup_by_symbol: dict[str, str] = {}

    def _stats(setup: str) -> SetupStats:
        if setup not in stats_by_setup:
            stats_by_setup[setup] = SetupStats(setup)
        return stats_by_setup[setup]

    for record in records:
        if record.decision == "enter" and record.entry_setup:
            setup = record.entry_setup
            open_setup_by_symbol[record.symbol] = setup
            _stats(setup).add_entry(record)
            continue

        if record.decision not in {"exit", "trail_exit"}:
            continue

        setup = (
            record.entry_setup
            if record.entry_setup is not None
            else open_setup_by_symbol.get(record.symbol)
        )
        if setup is None:
            continue

        _stats(setup).add_exit(record)
        if record.exit_remaining_shares in (0, None):
            open_setup_by_symbol.pop(record.symbol, None)

    return sorted(stats_by_setup.values(), key=lambda row: row.setup)


def format_setup_report(rows: Iterable[SetupStats]) -> str:
    """Render setup analytics as a compact text report."""

    rows = list(rows)
    if not rows:
        return "No setup analytics available."

    header = (
        "Setup                  Entries  Exits   Win%  Total R   Avg R  "
        "Best R Worst R     PF  Risk $  Total PnL  Avg Attn"
    )
    line = "-" * len(header)
    rendered = [header, line]

    for row in rows:
        avg_attention = "-" if row.avg_attention is None else f"{row.avg_attention:.1f}"
        best_r = "-" if row.best_r is None else f"{row.best_r:.2f}"
        worst_r = "-" if row.worst_r is None else f"{row.worst_r:.2f}"
        profit_factor = "-" if row.profit_factor is None else f"{row.profit_factor:.2f}"
        rendered.append(
            f"{row.setup:<22}"
            f"{row.entries:>7}"
            f"{row.exits:>7}"
            f"{row.win_rate_pct:>7.1f}"
            f"{row.total_r:>9.2f}"
            f"{row.avg_r:>8.2f}"
            f"{best_r:>8}"
            f"{worst_r:>8}"
            f"{profit_factor:>7}"
            f"{row.total_entry_risk:>8.2f}"
            f"{row.total_pnl:>11.2f}"
            f"{avg_attention:>10}"
        )

    return "\n".join(rendered)
