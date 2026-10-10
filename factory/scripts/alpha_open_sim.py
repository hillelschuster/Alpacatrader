"""Shared account replay for independent open-anchored alpha searches.

Inputs are selected causal signals, not outcome-filtered rows. Equal $1,000 order
budgets, $3,000 research sub-book, no leverage, one attempt per ticker/day; same-
clock sells precede new buys. Cash reuse assumes an eligible margin account and
is not a claim that a $3,000 US cash account may day-trade freely. UNKNOWN exits
remain explicit; portfolio lower bounds charge total loss, never a cash zero.
"""

from __future__ import annotations

import heapq
from pathlib import Path

import numpy as np
import polars as pl
from alpha_open_panel import allowed

CONTEXT_FEATURES = ("peer_positive3", "peer_gain_mean", "peer_breadth10", "peer_heat")


def load_panel(root: Path) -> tuple[pl.DataFrame, list[str]]:
    files = sorted((root / "days").glob("????-??-??.parquet"))
    if any(not allowed(p.stem) for p in files):
        raise ValueError("protected input file in panel root")
    frame = pl.scan_parquet(files).collect()
    # Cross-sectional context uses only observable watchlist states, never labels.
    frame = frame.with_columns(
        (pl.col("ret3") > 0).sum().over(["day", "t"]).alias("peer_positive3"),
        pl.col("gain_open").mean().over(["day", "t"]).alias("peer_gain_mean"),
        (pl.col("gain_open") >= 0.10).sum().over(["day", "t"]).alias("peer_breadth10"),
        pl.col("range5").median().over(["day", "t"]).alias("peer_heat"),
    )
    return frame, [p.stem for p in files]


def period(day: str) -> str:
    if day < "2023-01-01":
        return "train"
    if day < "2024-01-01":
        return "validation"
    return "confirmation"


def causal_liquidity(frame: pl.DataFrame) -> pl.Expr:
    return (pl.col("log_cum_dv") >= np.log1p(1_000_000)) & (pl.col("bars15") >= 12)


def replay(
    signals: pl.DataFrame,
    days: list[str],
    horizon: int,
    cost_bps: float,
    max_positions: int = 3,
    order_budget: float = 1000.0,
) -> tuple[dict, list[dict]]:
    """Long-only, next-open proxy. Score affects simultaneous picks, not fill availability."""
    if "score" not in signals.columns:
        signals = signals.with_columns(pl.lit(0.0).alias("score"))
    signals = signals.sort(["day", "t", "score", "ticker"], descending=[False, False, True, False])
    by_day = signals.partition_by("day", as_dict=True)
    trades, daily, total_attempts, cash_skips = [], [], 0, 0
    side = cost_bps / 20_000
    initial = max_positions * order_budget
    for day in days:
        cash, queue, attempted, active, known_pnl = initial, [], set(), set(), 0.0
        unknown = 0
        sequence = 0
        rows = by_day.get((day,))
        if rows is not None:
            for r in rows.iter_rows(named=True):
                t = r["t"]
                while queue and queue[0][0] <= t:
                    _, _, symbol, proceeds = heapq.heappop(queue)
                    active.remove(symbol)
                    cash += proceeds
                symbol = r["ticker"]
                if symbol in attempted or symbol in active:
                    continue
                if len(active) >= max_positions or cash + 1e-8 < order_budget:
                    cash_skips += 1
                    continue
                attempted.add(symbol)
                total_attempts += 1
                if r["entry_status"] == "unfilled_expired":
                    # Reserve for the attempted minute, then expire without fees.
                    cash -= order_budget
                    active.add(symbol)
                    heapq.heappush(queue, (t + 1, sequence, symbol, order_budget))
                    sequence += 1
                    continue
                gross, exit_et = r[f"gross_{horizon}"], r[f"exit_et_{horizon}"]
                cash -= order_budget
                active.add(symbol)
                if gross is None:
                    unknown += 1
                    net, status, proceeds = None, "unknown_pending", 0.0
                    release_t = r["session_end"] + 1
                else:
                    net = (1 + gross) * (1 - side) / (1 + side) - 1
                    status, proceeds = "known_open_proxy", order_budget * (1 + net)
                    release_t = exit_et
                    known_pnl += order_budget * net
                heapq.heappush(queue, (release_t, sequence, symbol, proceeds))
                sequence += 1
                trades.append(
                    {
                        "day": day,
                        "ticker": symbol,
                        "t": t,
                        "entry_et": r["entry_et"],
                        "entry_open": r["entry_open"],
                        "exit_et": exit_et,
                        "horizon": horizon,
                        "gross": gross,
                        "net": net,
                        "cost_bps": cost_bps,
                        "order_budget": order_budget,
                        "score": r["score"],
                        "status": status,
                    }
                )
        lower_pnl = known_pnl - order_budget * unknown
        daily.append(
            {
                "day": day,
                "known_pnl": known_pnl,
                "unknown": unknown,
                "lower_bound_pnl": lower_pnl,
                "lower_bound_return": lower_pnl / initial,
            }
        )
    known = np.array([r["net"] for r in trades if r["net"] is not None], dtype=float)
    returns = np.array([r["lower_bound_return"] for r in daily], dtype=float)
    monthly = {}
    for r in daily:
        monthly.setdefault(r["day"][:7], []).append(r["lower_bound_return"])
    wins = known[known > 0].sum()
    losses = -known[known < 0].sum()
    unknown_count = sum(r["unknown"] for r in daily)
    metrics = {
        "days": len(days),
        "attempts": total_attempts,
        "fills": len(trades),
        "known_fills": len(known),
        "unknown_fills": unknown_count,
        "cash_or_slot_skips": cash_skips,
        "cost_bps": cost_bps,
        "horizon": horizon,
        "mean_net_known_fill": float(known.mean()) if len(known) else None,
        "mean_daily_lower_bound": float(returns.mean()) if len(returns) else None,
        "daily_se": float(returns.std(ddof=1) / np.sqrt(len(returns)))
        if len(returns) > 1
        else None,
        "known_win_rate": float((known > 0).mean()) if len(known) else None,
        "known_profit_factor": float(wins / losses) if losses else None,
        "worst_known_fill": float(known.min()) if len(known) else None,
        "positive_months_lower_bound": int(sum(np.mean(v) > 0 for v in monthly.values())),
        "months": len(monthly),
        "monthly_mean_lower_bound": {k: float(np.mean(v)) for k, v in monthly.items()},
        "traded_days": len({r["day"] for r in trades}),
        "execution_proxy_only": True,
        "daily": daily,
    }
    return metrics, trades
