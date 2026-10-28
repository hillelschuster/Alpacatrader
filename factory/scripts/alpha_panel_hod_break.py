#!/usr/bin/env python3
"""Causal HIGH-OF-DAY-BREAK long lane on the open-anchored top-gainer panel (no model).

The scalp surface (factory/artifacts/overnight/scalp_surface.json) found the only
positive median-net setup family of the night -- HOD-break long with a 60-minute hold
(med net +270 bps at 25 bps) -- but on a PRE-SCREENED runner sample (75 sampled days,
735 hand-picked top name-days). This lane asks the causal question on the repo's own
open-anchored panel (alpha_open_panel admission: full-PIT SIP B snapshots, top-10,
>=5% gain, $1 floor, fresh price): EVERY panel day, EVERY admitted name, no hand-picked
days or names.

Signal (pre-registered, no fitting): at a panel decision minute t (5-minute clock; the
state's last completed bar is exactly t-1, so all inputs are strictly et<t) the state's
price (close of bar t-1, the panel producer's own ``current``) makes a new high above
the previous N minutes' high -- close[t-1] > max(high over the printed minute bars in
minutes t-1-N..t-2, a TIME-based window like the panel's own range15) -- with volume in
the last bar > k x the trailing 15-minute median volume (median over printed bars in
minutes t-16..t-2, excluding the signal bar; >=5 printed bars required). N in
{15,30,60}, k in {1.5,2.0} ONLY. Both comparisons are strict.

Entry: next bar open = minute t open (1-bar lag; the panel's own entry proxy). No print
at t => unfilled_expired attempt (reserve-and-release one minute later, fee-free, still
counts as a funded attempt). Exits: fixed 60-minute hold (primary), 30-minute (recomputed
from the strict actual minute opens with the panel producer's own convention) and the
close variant (panel h390 label = first actual open at/after min(t+390, session_end)).
An exit minute that never prints stays UNKNOWN (never cash, never interpolated) and is
charged a full $1,000 unit at the day's end.

Book (shared account conventions): $3,000 research reserve = 3 x $1,000 tickets, one
position per ticker, one attempt per ticker per day, at most 3 funded attempts per day,
flat 15-minute cooldown after the ACTUAL exit (inert under one attempt per ticker per
day, implemented per the lane convention), fees on BOTH legs, no leverage, same-clock
exits precede buys. Every panel session stays in the denominator.

Cost rungs: 25/50/100 bps TOTAL round-trip modeled friction on the minute-open proxy
prices (a comparison set, never a hurdle; NOT a broker fee claim). net =
(1+gross)*(1-s)/(1+s)-1 with s = cost_bps/20_000.

Selection is pre-registered BEFORE any late-block outcome is read: argmax 2023
validation $/panel session at 25 bps on the primary 60-minute exit over the six (N,k)
cells; tie-break (- $/session, - known fills, cell key). The phase split enforces it:
``--phase dev-val`` never opens a late-day file, writes the artifact with the frozen
pre-registration, and ``--phase late`` refuses to run unless the artifact on disk
carries the frozen pre-registration and its sha256 is unchanged.

Novelty vs the scalp surface: different sample (causal PIT panel, 1,003 days vs 75
sampled days), different breakout window (previous N minutes vs all prior highs),
different volume rule (k x trailing-15m median vs 2x trailing-20m), 5-minute decision
clock vs any minute, funded book with slots/daily caps vs equal-weight $3,000 per
observation, and flat proxy cost rungs vs quote-backed spread + fees. This is a
mechanism test on the right variables, not a reproduction of the biased sample.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

from alpha_open_learned import bootstrap_daily
from alpha_open_panel import ROOT, allowed
from alpha_sparse_daily import horizon_label

PANEL_ROOT = Path("/home/hillel/alpha-data/open-search-v1")
PANEL_DAYS = PANEL_ROOT / "days"
BARS_ROOT = ROOT / "data" / "sip" / "net" / "bars"
ARTIFACT = ROOT / "factory" / "artifacts" / "overnight" / "panel_hod_break.json"
TMP_ROOT = Path("/tmp/panel_hod_break")

ET = ZoneInfo("America/New_York")

# ----- pre-registered grid ----------------------------------------------------
N_GRID = (15, 30, 60)
K_GRID = (1.5, 2.0)
EXITS = (
    ("h30", 30, "recomputed_from_strict_actual_minute_opens"),
    ("h60", 60, "panel_stored_label"),
    ("close", 390, "panel_stored_label"),
)
PRIMARY_EXIT = "h60"
COST_SCENARIOS_BPS = (25.0, 50.0, 100.0)
SELECT_COST_BPS = 25.0
MIN_TRAILING_BARS = 5  # data-availability floor for the 15-minute volume median

# ----- book (shared lane conventions) -----------------------------------------
MAX_SLOTS = 3
ORDER_BUDGET = 1000.0
RESERVE_USD = MAX_SLOTS * ORDER_BUDGET
COOLDOWN_MIN = 15  # flat minutes after the ACTUAL exit
MAX_ATTEMPTS_PER_TICKER_PER_DAY = 1
MAX_ATTEMPTS_PER_DAY = 3

BLOCKS = {
    "dev": ("2021-05-01", "2022-12-31", 421),
    "validation": ("2023-01-01", "2023-12-31", 250),
    "late": ("2025-02-01", "2026-05-31", 332),
}
SEALED_PREFIXES = ("2024", "2025-01", "2026-06", "2026-07", "2026-08")

PANEL_COLUMNS = [
    "day",
    "ticker",
    "t",
    "entry_et",
    "entry_open",
    "entry_status",
    "session_end",
    "gross_60",
    "exit_et_60",
    "exit_status_60",
    "gross_390",
    "exit_et_390",
    "exit_status_390",
]
SIGNAL_COLUMNS = [
    "day",
    "ticker",
    "t",
    "entry_et",
    "entry_open",
    "entry_status",
    "session_end",
    "cell",
    "exit",
    "gross",
    "exit_et",
    "exit_status",
]
SIGNAL_DTYPES = {
    "day": pl.String,
    "ticker": pl.String,
    "t": pl.Int64,
    "entry_et": pl.Int64,
    "entry_open": pl.Float64,
    "entry_status": pl.String,
    "session_end": pl.Int64,
    "cell": pl.String,
    "exit": pl.String,
    "gross": pl.Float64,
    "exit_et": pl.Int64,
    "exit_status": pl.String,
}


def cell_key(n: int, k: float) -> str:
    return f"N{n}_k{str(k).replace('.', 'p')}"


def now_et() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _default(obj):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    raise TypeError(f"not serializable: {type(obj)}")


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=1, default=_default) + "\n")
    tmp.replace(path)


# ----- blocks ------------------------------------------------------------------
def block_days(days: list[str] | None = None) -> dict[str, list[str]]:
    """Panel sessions of one research block, straight from the panel root on disk."""
    files = sorted(p.stem for p in PANEL_DAYS.glob("????-??-??.parquet") if allowed(p.stem))
    keep = set(days) if days else None
    out: dict[str, list[str]] = {}
    for name, (lo, hi, expected) in BLOCKS.items():
        sel = [d for d in files if lo <= d <= hi and (keep is None or d in keep)]
        if any(d.startswith(SEALED_PREFIXES) for d in sel):
            raise SystemExit(f"sealed day selected for block {name}")
        if keep is None and len(sel) != expected:
            raise SystemExit(f"block {name}: expected {expected} panel sessions, got {len(sel)}")
        out[name] = sel
    return out


def calendar_span_days(days: list[str]) -> int:
    from datetime import date

    return (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days + 1


# ----- causal signal construction (one day resident) ---------------------------
def day_signal_rows(day: str, coverage: dict) -> list[dict]:
    """Every (cell, exit) signal row of one session; strictly past-only inputs.

    The panel day file supplies the admission universe, the decision clock t, the
    minute-t entry proxy and the stored h60/close exit labels. The SIP minute tape
    (the panel producer's own source) supplies the highs/volumes/close the breakout
    and volume-spike predicates need plus the recomputed h30 exit label.
    """
    panel = pl.read_parquet(PANEL_DAYS / f"{day}.parquet").select(PANEL_COLUMNS)
    missing_cols = [c for c in PANEL_COLUMNS if c not in panel.columns]
    if missing_cols:
        raise SystemExit(f"panel {day} lacks columns {missing_cols}")
    if not panel.height:
        coverage["days_no_panel_rows"].append(day)
        return []
    tickers = sorted(panel["ticker"].unique().to_list())
    tape = (
        pl.scan_parquet(BARS_ROOT / f"{day}.parquet")
        .filter(pl.col("ticker").is_in(tickers))
        .select("ticker", "et", "open", "high", "low", "close", "volume")
        .collect()
        .sort(["ticker", "et"])
    )
    if not tape.height:
        coverage["days_missing_tape"].append(day)
        return []
    parts = tape.partition_by("ticker", as_dict=True)
    present = {key[0] for key in parts}
    for tk in tickers:
        if tk not in present:
            coverage["missing_admitted_tapes"].setdefault(day, []).append(tk)
    rows: list[dict] = []
    for key, part in parts.items():
        tk = key[0]
        sub = panel.filter(pl.col("ticker") == tk)
        if not sub.height:
            continue
        et = part["et"].to_numpy().astype(np.int64)
        high = part["high"].to_numpy().astype(np.float64)
        close = part["close"].to_numpy().astype(np.float64)
        vol = part["volume"].to_numpy().astype(np.float64)
        opens = part["open"].to_numpy().astype(np.float64)
        for r in sub.iter_rows(named=True):
            t = int(r["t"])
            i = int(np.searchsorted(et, t, side="left")) - 1
            if i < 0 or int(et[i]) != t - 1:
                coverage["state_bar_violations"] += 1
                continue
            coverage["states_evaluated"] += 1
            c = float(close[i])
            # time-based previous-N-minute high (minutes t-1-N..t-2), strict close break
            breaks = {}
            for n in N_GRID:
                s = int(np.searchsorted(et, t - 1 - n, side="left"))
                breaks[n] = s < i and c > float(high[s:i].max())
            # trailing 15-minute median volume (minutes t-16..t-2, signal bar excluded)
            sv = int(np.searchsorted(et, t - 16, side="left"))
            window = vol[sv:i]
            if len(window) < MIN_TRAILING_BARS:
                coverage["thin_trailing_volume"] += 1
                continue
            med = float(np.median(window))
            fired = []
            for n in N_GRID:
                if not breaks[n]:
                    continue
                for k in K_GRID:
                    if float(vol[i]) > k * med:
                        fired.append((n, k))
            if not fired:
                continue
            filled = r["entry_status"] == "filled_proxy"
            for n, k in fired:
                for exit_name, horizon, _src in EXITS:
                    if horizon == 30:
                        gross, exit_et, status = horizon_label(
                            et,
                            opens,
                            t,
                            r["entry_open"],
                            filled,
                            30,
                            int(r["session_end"]),
                        )
                    elif horizon == 60:
                        gross, exit_et, status = (
                            r["gross_60"],
                            r["exit_et_60"],
                            r["exit_status_60"],
                        )
                    else:
                        gross, exit_et, status = (
                            r["gross_390"],
                            r["exit_et_390"],
                            r["exit_status_390"],
                        )
                    rows.append(
                        {
                            "day": day,
                            "ticker": tk,
                            "t": t,
                            "entry_et": r["entry_et"],
                            "entry_open": r["entry_open"],
                            "entry_status": r["entry_status"],
                            "session_end": int(r["session_end"]),
                            "cell": cell_key(n, k),
                            "exit": exit_name,
                            "gross": None if gross is None else float(gross),
                            "exit_et": None if exit_et is None else int(exit_et),
                            "exit_status": status,
                        }
                    )
    return rows


def build_block_signals(block: str, days: list[str]) -> tuple[Path, dict]:
    """Streaming per-day signal construction; one day of panel+tape resident."""
    if not days:
        raise SystemExit(f"no days for block {block}")
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    out = TMP_ROOT / f"signals_{block}.parquet"
    if out.exists():
        out.unlink()
    coverage = {
        "block": block,
        "days": len(days),
        "states_evaluated": 0,
        "state_bar_violations": 0,
        "thin_trailing_volume": 0,
        "days_no_panel_rows": [],
        "days_missing_tape": [],
        "missing_admitted_tapes": {},
        "signal_rows": 0,
        "rows_by_cell": {},
    }
    t0 = time.monotonic()
    frames: list[pl.DataFrame] = []
    for n, day in enumerate(days, 1):
        rows = day_signal_rows(day, coverage)
        if rows:
            frames.append(pl.DataFrame(rows, schema=SIGNAL_DTYPES))
            coverage["signal_rows"] += len(rows)
            for key, cnt in Counter((r["cell"], r["exit"]) for r in rows).items():
                coverage["rows_by_cell"][f"{key[0]}|{key[1]}"] = (
                    coverage["rows_by_cell"].get(f"{key[0]}|{key[1]}", 0) + cnt
                )
        if n % 100 == 0 or n == len(days):
            print(
                f"[signals:{block}] {n}/{len(days)} days, "
                f"{coverage['signal_rows']} rows (last {day})",
                flush=True,
            )
    if frames:
        pl.concat(frames, how="vertical").write_parquet(out)
    else:
        pl.DataFrame(schema=SIGNAL_DTYPES).write_parquet(out)
    coverage["runtime_s"] = round(time.monotonic() - t0, 1)
    coverage["signals_parquet"] = str(out)
    return out, coverage


# ----- funded-reserve replay (shared conventions + the lane's daily cap) --------
def replay_cell(
    signals: pl.DataFrame,
    days: list[str],
    cost_bps: float,
    *,
    cooldown_min: int = COOLDOWN_MIN,
    max_attempts_per_ticker_per_day: int = MAX_ATTEMPTS_PER_TICKER_PER_DAY,
    max_attempts_per_day: int = MAX_ATTEMPTS_PER_DAY,
) -> tuple[dict, list[dict]]:
    """One (cell, exit) signal set replayed on one block under the shared book.

    Long-only, next-open proxy entry, fees on BOTH legs, same-clock exits precede buys,
    one position per ticker, one attempt per ticker per day, at most
    ``max_attempts_per_day`` funded attempts per day, flat cooldown after the ACTUAL
    exit. An UNKNOWN exit keeps its slot and blocks its ticker for the rest of the
    session and is charged a full unit at the day's end; an unfilled attempt minute
    reserves and releases its ticket (fee-free) and still counts as an attempt. Every
    session is replayed, so no-signal days stay in the denominator as cash days. The
    daily lower bound is the RESEARCH normalization on the fixed reserve, not a
    self-financing CAGR.
    """
    if not signals.height:
        signals = pl.DataFrame(schema=SIGNAL_DTYPES)
    ordered = signals.sort(["day", "t", "ticker"])
    by_day = ordered.partition_by("day", as_dict=True)
    side = cost_bps / 20_000.0
    trades: list[dict] = []
    daily: list[dict] = []
    skips: Counter = Counter()
    equity = RESERVE_USD
    for day in days:
        rows = by_day.get((day,))
        cash, active, blocked, attempted = RESERVE_USD, set(), {}, set()
        queue: list[tuple] = []
        seq = 0
        known_pnl = 0.0
        unknown = 0
        fills = 0
        attempts = 0
        day_attempts = 0
        if rows is not None:
            for r in rows.iter_rows(named=True):
                t = int(r["t"])
                while queue and queue[0][0] <= t:
                    _, _, sym, proceeds = heapq.heappop(queue)
                    active.discard(sym)
                    cash += proceeds
                tk = r["ticker"]
                if tk in attempted or tk in active:
                    skips["attempted_or_active"] += 1
                    continue
                if int(blocked.get(tk, -1)) > t:
                    skips["cooldown"] += 1
                    continue
                if day_attempts >= max_attempts_per_day:
                    skips["daily_attempt_cap"] += 1
                    continue
                if len(active) >= MAX_SLOTS or cash + 1e-8 < ORDER_BUDGET:
                    skips["cash_or_slot"] += 1
                    continue
                if max_attempts_per_ticker_per_day <= 1:
                    attempted.add(tk)
                day_attempts += 1
                attempts += 1
                cash -= ORDER_BUDGET
                active.add(tk)
                if r["entry_status"] == "unfilled_expired":
                    heapq.heappush(queue, (t + 1, seq, tk, ORDER_BUDGET))
                    seq += 1
                    continue
                gross = r["gross"]
                if gross is None:
                    unknown += 1
                    net, status, proceeds = None, "unknown_pending", 0.0
                    release = int(r["session_end"]) + 1
                    exit_et = None
                else:
                    net = (1.0 + float(gross)) * (1.0 - side) / (1.0 + side) - 1.0
                    status, proceeds = "known_open_proxy", ORDER_BUDGET * (1.0 + net)
                    known_pnl += ORDER_BUDGET * net
                    release = int(r["exit_et"])
                    exit_et = int(r["exit_et"])
                blocked[tk] = release + cooldown_min
                heapq.heappush(queue, (release, seq, tk, proceeds))
                seq += 1
                fills += 1
                trades.append(
                    {
                        "day": day,
                        "ticker": tk,
                        "t": t,
                        "entry_et": r["entry_et"],
                        "entry_open": r["entry_open"],
                        "exit_et": exit_et,
                        "gross": None if gross is None else float(gross),
                        "net": net,
                        "status": status,
                        "cost_bps": cost_bps,
                    }
                )
        lower_pnl = known_pnl - ORDER_BUDGET * unknown
        equity += lower_pnl
        daily.append(
            {
                "day": day,
                "known_pnl": known_pnl,
                "unknown": unknown,
                "fills": fills,
                "attempts": attempts,
                "lower_bound_pnl": lower_pnl,
                "lower_bound_return": lower_pnl / RESERVE_USD,
                "carry_equity": equity,
            }
        )
    known = np.array([tr["net"] for tr in trades if tr["net"] is not None], dtype=float)
    gross_known = np.array([tr["gross"] for tr in trades if tr["net"] is not None], dtype=float)
    pnl = np.array([d["lower_bound_pnl"] for d in daily], dtype=float)
    returns = pnl / RESERVE_USD
    monthly: dict[str, list[float]] = {}
    for d in daily:
        monthly.setdefault(d["day"][:7], []).append(d["lower_bound_return"])
    wins = float(known[known > 0].sum()) if len(known) else 0.0
    losses = float(-known[known < 0].sum()) if len(known) else 0.0
    per_session = float(pnl.mean()) if len(pnl) else None
    drop1 = drop3 = None
    if len(pnl) > 1:
        srt = np.sort(pnl)[::-1]
        drop1 = float((pnl.sum() - srt[0]) / (len(pnl) - 1))
        if len(pnl) > 3:
            drop3 = float((pnl.sum() - srt[:3].sum()) / (len(pnl) - 3))
    metrics = {
        "cost_bps": cost_bps,
        "days": len(days),
        "attempts": sum(d["attempts"] for d in daily),
        "fills": len(trades),
        "known_fills": len(known),
        "unknown_fills": sum(d["unknown"] for d in daily),
        "skip_reasons": {k: int(v) for k, v in sorted(skips.items())},
        "known_win_rate": float((known > 0).mean()) if len(known) else None,
        "mean_net_known_fill": float(known.mean()) if len(known) else None,
        "median_net_known_fill": float(np.median(known)) if len(known) else None,
        "median_gross_known_fill": float(np.median(gross_known)) if len(known) else None,
        "worst_known_fill": float(known.min()) if len(known) else None,
        "known_profit_factor": (wins / losses) if losses else None,
        "total_lower_bound_pnl": float(pnl.sum()) if len(pnl) else 0.0,
        "mean_daily_lower_bound": float(returns.mean()) if len(returns) else None,
        "daily_se": (float(returns.std(ddof=1) / np.sqrt(len(returns))) if len(returns) > 1 else None),
        "dollars_per_session": per_session,
        "drop_best_1_dollars_per_session": drop1,
        "drop_top_3_dollars_per_session": drop3,
        "positive_months_lower_bound": int(sum(np.mean(v) > 0 for v in monthly.values())),
        "months": len(monthly),
        "monthly_mean_lower_bound": {k: float(np.mean(v)) for k, v in sorted(monthly.items())},
        "traded_days": len({tr["day"] for tr in trades}),
        "carry_equity_end": daily[-1]["carry_equity"] if daily else RESERVE_USD,
        "daily": daily,
    }
    return metrics, trades


def cell_stats(metrics: dict, trades: list[dict], n_signals: int, days: list[str]) -> dict:
    span = calendar_span_days(days)
    holds = [
        int(tr["exit_et"]) - int(tr["entry_et"])
        for tr in trades
        if tr["exit_et"] is not None and tr["entry_et"] is not None
    ]
    return {
        "signals": int(n_signals),
        "funded_intents": int(metrics["attempts"]),
        "fills": int(metrics["fills"]),
        "known_fills": int(metrics["known_fills"]),
        "unknown_fills": int(metrics["unknown_fills"]),
        "traded_days": int(metrics["traded_days"]),
        "skip_reasons": metrics["skip_reasons"],
        "dollars_per_session": metrics["dollars_per_session"],
        "dollars_per_calendar_day_span": (
            metrics["total_lower_bound_pnl"] / span if span else None
        ),
        "total_lower_bound_pnl": metrics["total_lower_bound_pnl"],
        "median_net_fill_bps": (
            metrics["median_net_known_fill"] * 1e4 if metrics["median_net_known_fill"] is not None else None
        ),
        "median_gross_fill_bps": (
            metrics["median_gross_known_fill"] * 1e4
            if metrics["median_gross_known_fill"] is not None
            else None
        ),
        "known_win_rate": metrics["known_win_rate"],
        "mean_net_known_fill_bps": (
            metrics["mean_net_known_fill"] * 1e4 if metrics["mean_net_known_fill"] is not None else None
        ),
        "months_positive": metrics["positive_months_lower_bound"],
        "months": metrics["months"],
        "drop_best_1_dollars_per_session": metrics["drop_best_1_dollars_per_session"],
        "drop_top_3_dollars_per_session": metrics["drop_top_3_dollars_per_session"],
        "mean_hold_min": float(np.mean(holds)) if holds else None,
        "median_hold_min": float(np.median(holds)) if holds else None,
    }


CELLS = [(n, k) for n in N_GRID for k in K_GRID]


def grid_block(signals: pl.DataFrame, days: list[str], block: str) -> tuple[dict, dict, dict]:
    """Full (cell x exit x cost) grid of one block plus the chosen-cell detail."""
    grid: dict[str, dict] = {}
    detail: dict[str, dict] = {}
    daily_returns: dict[str, dict] = {}
    for n, k in CELLS:
        key = cell_key(n, k)
        grid[key] = {}
        for exit_name, _horizon, _src in EXITS:
            sel = signals.filter((pl.col("cell") == key) & (pl.col("exit") == exit_name))
            n_signals = sel.height
            grid[key][exit_name] = {}
            for cost in COST_SCENARIOS_BPS:
                metrics, trades = replay_cell(sel, days, cost)
                grid[key][exit_name][str(int(cost))] = cell_stats(metrics, trades, n_signals, days)
                if exit_name == PRIMARY_EXIT:
                    detail.setdefault(key, {})[str(int(cost))] = {
                        "dollars_per_session": metrics["dollars_per_session"],
                        "mean_daily_lower_bound": metrics["mean_daily_lower_bound"],
                        "daily_se": metrics["daily_se"],
                        "known_fills": metrics["known_fills"],
                        "unknown_fills": metrics["unknown_fills"],
                        "traded_days": metrics["traded_days"],
                    }
                    daily_returns.setdefault(key, {})[str(int(cost))] = [
                        d["lower_bound_return"] for d in metrics["daily"]
                    ]
        print(
            f"[grid:{block}] {key} done (h60@25 "
            f"{grid[key][PRIMARY_EXIT]['25']['dollars_per_session']})",
            flush=True,
        )
    return grid, detail, daily_returns


def method_block() -> dict:
    return {
        "study": "alpha_panel_hod_break",
        "purpose": (
            "Does a causal high-of-day-break long entry work on the CAUSAL PIT "
            "top-gainer panel (every panel day, every admitted name, no hand-picked "
            "days or names)? The scalp surface's +270bps median-net h60 finding came "
            "from a pre-screened runner sample; this lane re-tests the mechanism on "
            "the panel with the repo's own admission."
        ),
        "blocks": {
            "dev": "2021-05..2022-12, 421 panel sessions (context only; never selected on)",
            "validation": "2023, 250 panel sessions (the pre-registered selection block)",
            "late": "2025-02..2026-05, 332 panel sessions (previously explored; read only "
            "after the frozen pre-registration)",
            "sealed_never_read": list(SEALED_PREFIXES),
        },
        "panel": {
            "root": str(PANEL_DAYS),
            "contract": "alpha_open_panel.py CONTRACT version 1 (admission: full-PIT SIP B "
            "snapshot top10, score>=0.05, px>=1, fresh px; 5-minute decision clock; OHLCV "
            "strictly et<t; entry at minute-t open; unresolved exits stay UNKNOWN)",
            "candidate_states": "every panel state of every admitted name (no liquidity gate, "
            "no model score, no quality filter beyond the signal predicate itself)",
        },
        "signal": {
            "decision_clock": "the panel's own 5-minute clock; the state's last completed bar "
            "is exactly t-1, so every input is strictly et<t (1-bar causal lag)",
            "breakout": "state price (close of bar t-1, the panel producer's own 'current') > "
            "max(high over the PRINTED minute bars in minutes t-1-N..t-2), a time-based "
            "window; strict inequality; empty window => no signal",
            "volume": "volume of the last bar (t-1) > k x median(volume over the printed bars "
            "in minutes t-16..t-2, the signal bar excluded); >=5 printed bars required "
            "(data-availability floor, not a quality gate)",
            "grid": {"N": list(N_GRID), "k": list(K_GRID)},
            "no_other_conditions": "no day-high requirement, no liquidity gate, no model, "
            "no fit; the six cells are fixed in code before any outcome was read",
        },
        "execution": {
            "entry": "next bar open = minute-t open (the panel's entry proxy); no print at "
            "minute t => unfilled_expired attempt: reserve-and-release one minute later, "
            "fee-free, still a funded attempt",
            "exits": {
                "h60": "fixed 60-minute hold (PRIMARY): first actual minute open at/after "
                "min(entry+60, session_end), panel-stored label",
                "h30": "recomputed here from the strict actual minute opens with the panel "
                "producer's own convention (alpha_sparse_daily.horizon_label)",
                "close": "hold to the session end (panel h390 label = first actual open "
                "at/after min(entry+390, session_end))",
            },
            "unknown": "an exit minute that never prints stays UNKNOWN (never cash, never "
            "interpolated): the slot and the ticker are held for the rest of the session "
            "and the day is charged a full $1,000 unit (lower bound)",
        },
        "book": {
            "reserve_usd": RESERVE_USD,
            "slots": MAX_SLOTS,
            "ticket_usd": ORDER_BUDGET,
            "one_position_per_ticker": True,
            "one_attempt_per_ticker_per_day": True,
            "max_attempts_per_day": MAX_ATTEMPTS_PER_DAY,
            "cooldown_min_after_actual_exit": COOLDOWN_MIN,
            "cooldown_note": "inert under one attempt per ticker per day; implemented per the "
            "shared lane convention and reported in skip_reasons",
            "fees": "both legs; net = (1+gross)*(1-s)/(1+s)-1 with s = cost_bps/20_000",
            "leverage": False,
            "denominators": "every panel session of the block stays in the denominator "
            "(421/250/332); $/calendar-day-span divides the block's total by the inclusive "
            "first..last session span",
        },
        "costs": {
            "rungs_bps": [float(c) for c in COST_SCENARIOS_BPS],
            "nature": "TOTAL round-trip modeled friction on the minute-open proxy prices: a "
            "COMPARISON SET, never a hurdle; NOT a broker-fee claim (a primary US broker's "
            "regular schedule is typically well under 1bp on a $1,000 ticket)",
            "context": "the program's measured quote-backed median round-trip spread on these "
            "names is 77.5bps (scalp_surface structural costs), so the 25bps rung is "
            "optimistic and the 100bps rung is the honest read for spread-inclusive friction",
        },
        "selection_pre_registration": {
            "objective": "2023 validation $/panel session at the 25bps rung on the PRIMARY "
            "60-minute exit",
            "grid": "the six (N,k) cells only; h30/close exits are reported as shape and are "
            "not selection axes (h60 is the primary exit)",
            "tie_break": "(- $/session, - known fills, cell key)",
            "frozen_before": "any late-block outcome is computed; enforced by the two-phase "
            "runner (phase dev-val never opens a late-day file; phase late refuses to run "
            "unless the artifact carries the frozen pre-registration with an unchanged sha256)",
        },
        "sensitivities": {
            "drop_best_1": "$/session recomputed after removing the single best session",
            "drop_top_3": "$/session recomputed after removing the three best sessions",
        },
        "novelty_vs_scalp_surface": {
            "sample": "causal PIT top-gainer panel, 1,003 sessions vs 75 sampled days / 735 "
            "pre-screened top name-days",
            "breakout_window": "previous N minutes (15/30/60) vs all prior highs (full-day)",
            "volume_rule": "k x trailing-15-minute median vs 2x trailing-20-minute median",
            "clock": "panel 5-minute decision clock vs any minute (first occurrence)",
            "fills": "minute-t open proxy, strict actual opens, UNKNOWN charged vs next-minute "
            "open with same-minute print requirement",
            "costs": "flat 25/50/100bps proxy rungs vs quote-backed spread + fees",
            "book": "funded $3,000 reserve with slots and a 3-attempts/day cap vs equal-weight "
            "$3,000 notional per observation",
        },
    }


# ----- phases ------------------------------------------------------------------
def run_phase_dev_val(args: argparse.Namespace) -> None:
    blocks = block_days(args.days)
    dev, val = blocks["dev"], blocks["validation"]
    if any(d >= "2025-02-01" for d in dev + val):
        raise SystemExit("dev/val phase touched a late day")
    if any(d.startswith(SEALED_PREFIXES) for d in dev + val):
        raise SystemExit("dev/val phase touched a sealed day")
    payload: dict = {
        "artifact": "panel_hod_break",
        "producer": "factory/scripts/alpha_panel_hod_break.py (phase dev-val)",
        "created_et": now_et(),
        "status": "DISCOVERY-NOT-VALIDATED",
        "method": method_block(),
    }
    grid_out: dict[str, dict] = {}
    detail_out: dict[str, dict] = {}
    daily_out: dict[str, dict] = {}
    coverage_out: dict[str, dict] = {}
    for block, days in (("dev", dev), ("validation", val)):
        sig_path, coverage = build_block_signals(block, days)
        coverage_out[block] = {k: v for k, v in coverage.items() if k != "rows_by_cell"}
        signals = pl.read_parquet(sig_path)
        grid, detail, daily_returns = grid_block(signals, days, block)
        del signals
        grid_out[block] = grid
        detail_out[block] = detail
        daily_out[block] = daily_returns
    payload["coverage"] = coverage_out
    payload["grids"] = {b: grid_out[b] for b in ("dev", "validation")}
    payload["detail_primary_exit"] = {b: detail_out[b] for b in ("dev", "validation")}

    # ----- pre-registered selection (validation only, 25bps, primary h60 exit) -----
    ranked = sorted(
        CELLS,
        key=lambda ck: (
            -(detail_out["validation"][cell_key(*ck)]["25"]["dollars_per_session"] or 0.0),
            -detail_out["validation"][cell_key(*ck)]["25"]["known_fills"],
            cell_key(*ck),
        ),
    )
    pick = ranked[0]
    key = cell_key(*pick)
    payload["pre_registration"] = {
        "registered_at_et": now_et(),
        "registered_by": "alpha_panel_hod_break lane (phase dev-val)",
        "rule": "argmax 2023 validation $/panel session at the 25bps rung on the primary "
        "60-minute exit over the six (N,k) cells; tie-break (-$/session, -known fills, cell key)",
        "selectable_cells": [cell_key(n, k) for n, k in CELLS],
        "exits_not_selectable": [e[0] for e in EXITS if e[0] != PRIMARY_EXIT],
        "selection_cost_bps": SELECT_COST_BPS,
        "selection_exit": PRIMARY_EXIT,
        "selection_block": "validation",
        "validation_ranked_dollars_per_session": {
            cell_key(*ck): detail_out["validation"][cell_key(*ck)]["25"]["dollars_per_session"]
            for ck in ranked
        },
        "validation_ranked_known_fills": {
            cell_key(*ck): detail_out["validation"][cell_key(*ck)]["25"]["known_fills"]
            for ck in ranked
        },
        "frozen_choice": {"cell": key, "N": pick[0], "k": pick[1]},
        "frozen_choice_validation_dollars_per_session": detail_out["validation"][key]["25"][
            "dollars_per_session"
        ],
        "status_at_freeze": "FROZEN: the late block has NOT been read; no late-day panel, tape "
        "or outcome exists in this process at freeze time",
        "late_block": None,
        "enforcement": "phase late refuses to run unless this pre_registration block exists and "
        "the artifact sha256 recorded at freeze is unchanged",
    }
    pre = payload["pre_registration"]
    # chosen-cell daily returns (both blocks, all rungs) for the bootstrap / drop-day audit
    payload["chosen_cell_daily_returns"] = {
        b: {c: daily_out[b][key][c] for c in ("25", "50", "100")}
        for b in ("dev", "validation")
    }
    write_json_atomic(ARTIFACT, payload)
    freeze_sha = hashlib.sha256(
        json.dumps(
            {k: v for k, v in pre.items() if k != "artifact_sha256_at_freeze"},
            sort_keys=True,
            default=_default,
        ).encode()
    ).hexdigest()
    pre["artifact_sha256_at_freeze"] = freeze_sha
    write_json_atomic(ARTIFACT, payload)
    print(
        f"[freeze] validation @25bps choice {key} "
        f"({payload['pre_registration']['frozen_choice_validation_dollars_per_session']:+.2f} "
        f"$/session); artifact -> {ARTIFACT}",
        flush=True,
    )


def run_phase_late(args: argparse.Namespace) -> None:
    if not ARTIFACT.exists():
        raise SystemExit(f"late phase requires the frozen artifact at {ARTIFACT}")
    payload = json.loads(ARTIFACT.read_text())
    pre = payload.get("pre_registration")
    if not pre or "frozen_choice" not in pre:
        raise SystemExit("late phase refused: no frozen pre_registration in the artifact")
    recorded = pre.get("artifact_sha256_at_freeze")
    if not recorded:
        raise SystemExit("late phase refused: pre_registration lacks the freeze sha256")
    projected = {k: v for k, v in pre.items() if k != "artifact_sha256_at_freeze"}
    actual = hashlib.sha256(json.dumps(projected, sort_keys=True).encode()).hexdigest()
    if actual != recorded:
        raise SystemExit(
            "late phase refused: the frozen pre-registration changed since the freeze "
            f"(recorded {recorded}, found {actual})"
        )
    choice = pre["frozen_choice"]
    key = choice["cell"]
    blocks = block_days(args.days)
    late = blocks["late"]
    if any(not ("2025-02-01" <= d <= "2026-05-31") for d in late):
        raise SystemExit("late phase selected a non-late day")
    late_read_started_et = now_et()

    sig_path, coverage = build_block_signals("late", late)
    payload = json.loads(ARTIFACT.read_text())
    payload["late_read_started_et"] = late_read_started_et
    payload["coverage"]["late"] = {k: v for k, v in coverage.items() if k != "rows_by_cell"}
    signals = pl.read_parquet(sig_path)
    grid, detail, daily_returns = grid_block(signals, late, "late")
    del signals
    payload["grids"]["late"] = grid
    payload["detail_primary_exit"]["late"] = detail
    payload["chosen_cell_daily_returns"]["late"] = {
        c: daily_returns[key][c] for c in ("25", "50", "100")
    }

    # chosen-cell late table (the pre-registered late read) + bootstrap on the daily returns
    chosen_late: dict[str, dict] = {}
    for exit_name, _h, _s in EXITS:
        chosen_late[exit_name] = grid[key][exit_name]
    boot = {}
    for cost in COST_SCENARIOS_BPS:
        rets = daily_returns[key][str(int(cost))]
        boot[str(int(cost))] = bootstrap_daily(rets)
    payload["chosen_cell_late"] = {
        "cell": key,
        "note": "the pre-registered late read: the frozen cell only; the exits are reported "
        "beside each other (h60 primary); the full late grid below is POST-HOC",
        "bootstrap_daily": boot,
        "by_exit": chosen_late,
    }

    # full late grid = post-hoc appendix
    payload["late_appendix_posthoc"] = {
        "post_hoc": True,
        "warning": "POST-HOC APPENDIX: every late cell below is reported for completeness only; "
        "no late cell was eligible for selection and no late number fed the frozen choice",
        "grid": grid,
    }

    # ----- the answer -----
    def cell_usd(block: str, exit_name: str, cost: str) -> float | None:
        return payload["grids"][block][key][exit_name][cost]["dollars_per_session"]

    answer = {
        "chosen_cell": key,
        "frozen_choice_N": choice["N"],
        "frozen_choice_k": choice["k"],
        "validation_dollars_per_session": {
            c: {e: cell_usd("validation", e, c) for e, _h, _s in EXITS}
            for c in ("25", "50", "100")
        },
        "late_dollars_per_session": {
            c: {e: cell_usd("late", e, c) for e, _h, _s in EXITS} for c in ("25", "50", "100")
        },
        "dev_dollars_per_session_h60": {
            c: cell_usd("dev", PRIMARY_EXIT, c) for c in ("25", "50", "100")
        },
    }
    v25, v100 = cell_usd("validation", PRIMARY_EXIT, "25"), cell_usd(
        "validation", PRIMARY_EXIT, "100"
    )
    l25, l100 = cell_usd("late", PRIMARY_EXIT, "25"), cell_usd("late", PRIMARY_EXIT, "100")
    answer["validation_positive_at_25bps"] = bool(v25 is not None and v25 > 0)
    answer["validation_positive_at_100bps"] = bool(v100 is not None and v100 > 0)
    answer["late_positive_at_25bps"] = bool(l25 is not None and l25 > 0)
    answer["late_positive_at_100bps"] = bool(l100 is not None and l100 > 0)
    answer["positive_on_both_blocks_25bps"] = bool(
        answer["validation_positive_at_25bps"] and answer["late_positive_at_25bps"]
    )
    answer["positive_on_both_blocks_100bps"] = bool(
        answer["validation_positive_at_100bps"] and answer["late_positive_at_100bps"]
    )
    answer["survives_100bps"] = answer["positive_on_both_blocks_100bps"]
    answer["text"] = (
        f"{key} (primary 60-minute exit): validation {v25:+.2f} $/session @25bps, "
        f"{v100:+.2f} @100bps; late {l25:+.2f} $/session @25bps, {l100:+.2f} @100bps on "
        f"the previously explored 2025-02..2026-05 block; positive on BOTH blocks at 25bps: "
        f"{answer['positive_on_both_blocks_25bps']}; at 100bps: "
        f"{answer['positive_on_both_blocks_100bps']}. DISCOVERY-NOT-VALIDATED."
    )
    payload["answer"] = answer
    pre = payload["pre_registration"]
    pre["late_read_started_et"] = payload["late_read_started_et"]
    pre["late_block"] = (
        "2025-02..2026-05, 332 panel sessions, previously explored (discovery, not "
        "pristine); read exactly once, only after the freeze"
    )
    payload["producer"] = "factory/scripts/alpha_panel_hod_break.py (phase dev-val + late)"
    payload["completed_et"] = now_et()
    write_json_atomic(ARTIFACT, payload)
    print(f"[done] late block appended -> {ARTIFACT}", flush=True)
    # throwaway intermediates: the durable artifact is the only output
    for p in TMP_ROOT.glob("signals_*.parquet"):
        p.unlink()
    if TMP_ROOT.exists() and not any(TMP_ROOT.iterdir()):
        TMP_ROOT.rmdir()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--phase", choices=("dev-val", "late"), required=True)
    p.add_argument("--days", nargs="+", help="restrict panel sessions (smoke tests only)")
    args = p.parse_args()
    if args.phase == "dev-val":
        run_phase_dev_val(args)
    else:
        run_phase_late(args)


if __name__ == "__main__":
    main()
