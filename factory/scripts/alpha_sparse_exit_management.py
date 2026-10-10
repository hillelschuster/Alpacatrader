#!/usr/bin/env python3
"""Paired quote-triggered exit management on the frozen sparse h60 model trades.

Three fixed, paired policies are evaluated on the ORIGINAL fixed entries and
quantities (immutable validation 61 / late 143 trades of the exploratory
sparse extension). Entries, timestamps, tickers and the $1,000 unit size are
never re-selected; nothing here refits a model or re-runs the account engine.

Policies (identical entry leg for all three; only the exit differs):
  hold60m  the original 60-minute control priced at the actual NBBO touch:
           buy ASK at entry clock +250ms, sell BID as of the original 60m
           target clock +250ms.
  stop10   quote-triggered 10% adverse exit.
  stop15   quote-triggered 15% adverse exit.

Stop mechanics (both stop policies): protection is active 250ms after the entry
fill (fill-ack allowance). The FIRST subsequent REGULAR quote at/after activation
whose BID prints at/below entry_ask*(1-L) sends a market exit arriving 250ms
later; the exit prices at the BID as of arrival, or at the FIRST subsequent
valid regular quote when arrival lands in a halt/no-fresh-quote gap. A stop is
never marked filled at its level, no quote high/low/MFE target is used, and no
favorable quote time is ever chosen. If no stop triggers before the original 60m
target, holding is capped to the original 60m exit, priced exactly like the
control. Stop-through gaps can still lose more than the limit; they are
quantified, not hidden.

Quote treatment follows the shared frontier service (alpha_sparse_quote_service):
R-only as-of prints, 2s primary staleness, fee-funded integer quantity, L1
support required on both legs, residual-cost ladder 0/10/25bps charged per side.
Quotes that are missing, non-firm, stale, crossed or L1-shallow stay UNKNOWN
(never cash, never a quiet zero): the covered subset is not the whole portfolio,
and display depth is an L1-only measurement, not a fillability verdict. A budget
that cannot fund even one integer share is a KNOWN no-order instead: no order is
sent, the cash stays unfilled and the day contributes 0 USD - it is never
charged the UNKNOWN full-unit loss and never counted as a fill.

Selection uses ONLY the 2023 validation calendar daily lower bound (all calendar
days, unknowns charged a full unit loss) at the primary actual-touch +25bps
residual, and is frozen before any late-block outcome is computed. The late
block (2025-02..2026-05) was already explored as the base, so everything late is
reported exploratory, never pristine. Annual EV figures are simple 252-day
annualizations of observed rates, never compounded.

Operational assumption (disclosed, not hidden): every order is priced at a 250ms
arrival latency (entry, stop activation ack and market exit alike) for
like-for-like pairing across the three policies. Minute-bar feature publication
latency is unmeasured, so the frontier additionally assesses 1s/2s arrivals;
this module stays at 250ms and never re-selects on late outcomes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import polars as pl
from alpha_open_learned import (
    BOOT_N,
    BOOT_SEED,
    PANEL_ROOT,
    bootstrap_daily,
    monthly_accounting,
)
from alpha_open_panel import ROOT, allowed
from alpha_open_sim import period
from alpha_quote_audit import clock_us
from alpha_sparse_quote_service import (
    load_day_quotes,
    quote_at,
    regular_mask,
    supported_round_trip,
)

# ----- fixed configuration (no HPO, no grid search). ---------------------------
STATUS = "DISCOVERY-NOT-VALIDATED"
HORIZON = 60
RESIDUALS = (0.0, 10.0, 25.0)  # bps round-trip residual ladder
PRIMARY_RESIDUAL = 25.0  # selection touch: actual +25bps
LATENCY_MS = 250  # order arrival latency
LATENCY_US = 250_000
FILL_ACK_US = 250_000  # stop protection activation delay
MAX_AGE_S = 2.0  # primary as-of staleness
ORDER_BUDGET = 1000.0  # $1,000 per unit, as the frozen replay
MAX_POSITIONS = 3  # $3,000 research sub-book, no leverage
POLICIES = ("hold60m", "stop10", "stop15")
STOP_FRAC = {"stop10": 0.10, "stop15": 0.15}
POLICY_LABELS = {
    "hold60m": (
        "original 60m control at actual NBBO touch "
        "(entry ASK@+250ms, exit BID@target+250ms)"
    ),
    "stop10": (
        "quote-triggered 10% adverse exit "
        "(first regular BID <= entry_ask*0.90 after activation)"
    ),
    "stop15": (
        "quote-triggered 15% adverse exit "
        "(first regular BID <= entry_ask*0.85 after activation)"
    ),
}
TRADING_DAYS_PER_YEAR = 252
FLOAT_TOL = 1e-12
DATA_ROOT = ROOT / "data"
BASE_POLICY = "base_proxy"  # stored next-open proxy reference


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, indent=2, default=str) + "\n")


def side_of(residual_bps: float) -> float:
    """Per-side decimal half of the declared round-trip residual."""
    return float(residual_bps) / 20_000.0


def proxy_net(gross: float, residual_bps: float) -> float:
    """Original next-open proxy net for one residual rung (unchanged replay math)."""
    side = side_of(residual_bps)
    return (1.0 + gross) * (1.0 - side) / (1.0 + side) - 1.0


# ----- block calendars ----------------------------------------------------------
def block_calendar(panel: Path, block: str) -> list[str]:
    """All calendar days of one research block (zero-return days included)."""
    stems = sorted(p.stem for p in (panel / "days").glob("????-??-??.parquet"))
    return [d for d in stems if allowed(d) and period(d) == block]


# ----- frozen trade inputs -------------------------------------------------------
def load_block(trades_path: Path, signals_path: Path) -> list[dict]:
    """Frozen trades joined to their signal session_end (no re-selection)."""
    if not trades_path.exists():
        raise SystemExit(f"[exit-mgmt] trades file missing: {trades_path}")
    if not signals_path.exists():
        raise SystemExit(f"[exit-mgmt] signals file missing: {signals_path}")
    trades = pl.read_parquet(trades_path).to_dicts()
    sigs = pl.read_parquet(signals_path).select("day", "ticker", "t", "session_end").to_dicts()
    ends: dict[tuple[str, str], int] = {}
    for r in sigs:
        key = (r["day"], r["ticker"])
        if key in ends and ends[key] != r["session_end"]:
            raise SystemExit(f"[exit-mgmt] ambiguous session_end for {key}")
        ends[key] = r["session_end"]
    for r in trades:
        key = (r["day"], r["ticker"])
        if key not in ends:
            raise SystemExit(f"[exit-mgmt] no session_end for trade {key}")
        r["session_end"] = int(ends[key])
    return trades


# ----- raw quote scans (R-only rule shared via regular_mask) ---------------------
def _quote_arrays(frame: pl.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(ts_us, bid, valid-and-regular mask) for one symbol's sorted frame."""
    marked = regular_mask(frame)
    stamps = marked["ts_utc"].cast(pl.Int64).to_numpy()
    bid = marked["bid_price"].to_numpy()
    ask = marked["ask_price"].to_numpy()
    ok = (
        marked["regular"].to_numpy()
        & np.isfinite(bid)
        & np.isfinite(ask)
        & (bid > 0)
        & (ask > 0)
        & (bid <= ask)
    )
    return stamps, bid.astype(float), ok


def first_bid_trigger(
    frame: pl.DataFrame, from_us: int, before_us: int, level: float
) -> tuple[int | None, float | None]:
    """First VALID REGULAR quote with BID <= level in [from_us, before_us)."""
    if frame.is_empty():
        return None, None
    stamps, bid, ok = _quote_arrays(frame)
    lo = int(np.searchsorted(stamps, from_us, side="left"))
    hi = int(np.searchsorted(stamps, before_us, side="left"))
    for i in range(lo, hi):
        if ok[i] and bid[i] <= level:
            return int(stamps[i]), float(bid[i])
    return None, None


def resting_exit_quote(
    frame: pl.DataFrame, arrival_us: int, end_us: int, max_age_s: float = MAX_AGE_S
) -> tuple[dict | None, str, int, float | None]:
    """BID as of arrival; a halt/no-fresh-quote gap waits for the FIRST subsequent
    valid regular quote at/after arrival and at/before end_us (never a later,
    more favorable print)."""
    q, status = quote_at(frame, arrival_us, max_age_s)
    if q is not None:
        return q, status, arrival_us, 0.0
    stamps, _bid, ok = _quote_arrays(frame)
    i = int(np.searchsorted(stamps, arrival_us, side="left"))
    while i < len(stamps) and stamps[i] <= end_us:
        if ok[i]:
            rested, _ = quote_at(frame, int(stamps[i]), max_age_s)
            if rested is not None:
                return (
                    rested,
                    "rested_until_regular_quote",
                    int(stamps[i]),
                    (float(stamps[i]) - arrival_us) / 1_000_000.0,
                )
        i += 1
    return None, "no_valid_regular_quote_before_end", arrival_us, None


# ----- one fixed trade under every policy ---------------------------------------
def evaluate_trade(t: dict, frame: pl.DataFrame, residuals: tuple[float, ...] = RESIDUALS) -> dict:
    day, ticker = t["day"], t["ticker"]
    entry_min, exit_min = int(t["entry_et"]), int(t["exit_et"])
    entry_target = clock_us(day, entry_min, LATENCY_MS)  # entry ASK clock
    activation_us = entry_target + FILL_ACK_US  # protection active
    target_us = clock_us(day, exit_min, LATENCY_MS)  # original 60m exit
    end_us = clock_us(day, int(t["session_end"]), 0)
    tags = [int(r) for r in residuals]
    rec: dict = {
        "day": day,
        "ticker": ticker,
        "entry_et": entry_min,
        "exit_et": exit_min,
        "session_end": int(t["session_end"]),
        "order_budget": ORDER_BUDGET,
        "entry_open_proxy": t["entry_open"],
        "exit_open_proxy": t.get("exit_open_proxy"),
        "base_gross": t["gross"],
        "base_status": t["status"],
        "base_net_100": t["net"],
        "entry_target_us": entry_target,
        "activation_us": activation_us,
        "target_exit_us": target_us,
        "session_end_us": end_us,
    }
    for r, tag in zip(residuals, tags, strict=True):
        rec[f"base_net_{tag}"] = proxy_net(t["gross"], r) if t["gross"] is not None else None
    entry_q, estat = quote_at(frame, entry_target, MAX_AGE_S)
    if entry_q is None:
        for p in POLICIES:
            rec[f"{p}_entry_status"] = f"unknown_quote_entry:{estat}"
            for tag in tags:
                rec[f"{p}_net_{tag}"] = None
                rec[f"{p}_unknown_{tag}"] = f"unknown_quote_entry:{estat}"
        return rec
    entry_ask = float(entry_q["ask"])
    rec.update(
        {
            "entry_ask": entry_ask,
            "entry_ask_shares": entry_q["ask_shares"],
            "entry_age_s": entry_q["age_s"],
            "entry_spread_bps": entry_q["spread_bps"],
        }
    )
    for p in POLICIES:
        level = None if p == "hold60m" else entry_ask * (1.0 - STOP_FRAC[p])
        if p == "hold60m":
            trig_us, trig_bid, arrival = None, None, target_us
        else:
            trig_us, trig_bid = first_bid_trigger(frame, activation_us, target_us, level)
            arrival = target_us if trig_us is None else trig_us + LATENCY_US
        rec.update(
            {
                f"{p}_entry_status": "quoted",
                f"{p}_stop_level": level,
                f"{p}_trigger_us": trig_us,
                f"{p}_trigger_bid": trig_bid,
                f"{p}_triggered": trig_us is not None,
                f"{p}_exit_arrival_us": arrival,
            }
        )
        exit_q, xstat, exit_us, delay_s = resting_exit_quote(frame, arrival, end_us)
        rec.update(
            {f"{p}_exit_status": xstat, f"{p}_exit_us": exit_us, f"{p}_exit_delay_s": delay_s}
        )
        if exit_q is None:
            for tag in tags:
                rec[f"{p}_net_{tag}"] = None
                rec[f"{p}_unknown_{tag}"] = f"unknown_exit_quote:{xstat}"
            continue
        rec.update(
            {
                f"{p}_exit_bid": exit_q["bid"],
                f"{p}_exit_bid_shares": exit_q["bid_shares"],
                f"{p}_exit_age_s": exit_q["age_s"],
                f"{p}_exit_spread_bps": exit_q["spread_bps"],
                f"{p}_exit_vs_level_bps": (
                    None if level is None else (exit_q["bid"] / level - 1.0) * 10_000.0
                ),
            }
        )
        for r, tag in zip(residuals, tags, strict=True):
            rt = supported_round_trip(entry_q, exit_q, ORDER_BUDGET, r)
            rec[f"{p}_q_{tag}"] = rt["quantity"]
            rec[f"{p}_entry_price_{tag}"] = rt["entry_price"]
            rec[f"{p}_exit_price_{tag}"] = rt["exit_price"]
            rec[f"{p}_net_{tag}"] = rt["net"] if rt["supported"] else None
            rec[f"{p}_unknown_{tag}"] = rt["unknown"]
            rec[f"{p}_rt_status_{tag}"] = rt["status"]
            rec[f"{p}_no_order_{tag}"] = bool(rt["unfilled_known_cash"])
    return rec


def evaluate_block(trades: list[dict], data: Path, supplemental: Path | None) -> list[dict]:
    """Day-resident quote reads: one day's streams held at a time, never a corpus."""
    by_day: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_day[t["day"]].append(t)
    records: list[dict] = []
    for day in sorted(by_day):
        tickers = {t["ticker"] for t in by_day[day]}
        frame = load_day_quotes(data, day, tickers, supplemental)
        groups = frame.partition_by("symbol", as_dict=True) if frame.height else {}
        for t in by_day[day]:
            one = groups.get((t["ticker"],))
            if one is None:
                one = frame.filter(pl.col("symbol") == t["ticker"])
            records.append(evaluate_trade(t, one))
        del frame, groups
    return records


# ----- portfolio metrics over the fixed book --------------------------------------
def book_view(
    records: list[dict],
    net_key: str,
    calendar: list[str],
    unknown_key: str | None = None,
    pricing: str = "actual_nbbo_touch",
    cost_bps: float = PRIMARY_RESIDUAL,
    no_order_key: str | None = None,
) -> dict:
    """Replay-compatible metrics for one fixed policy rung (no slot-engine rerun).

    A record whose ``net_key`` is None is UNKNOWN (no fresh/valid quote, L1
    shortfall, ...) and is charged a full unit loss in the lower bound. A record
    flagged KNOWN no-order by ``no_order_key`` is different: its budget cannot
    fund even one integer share, no order was ever sent and the cash simply
    stays unfilled, so it contributes 0 to the day and is never charged the
    UNKNOWN full-unit loss and never counted as a fill.
    """
    initial = MAX_POSITIONS * ORDER_BUDGET
    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_day[r["day"]].append(r)
    daily: list[dict] = []
    known: list[float] = []
    causes: Counter = Counter()
    n_no_order = 0
    for day in calendar:
        known_pnl, unknown = 0.0, 0
        for r in by_day.get(day, ()):
            net = r[net_key]
            if net is None:
                if no_order_key is not None and r.get(no_order_key):
                    # Known no-order: cash retained, 0 contribution, NOT UNKNOWN.
                    n_no_order += 1
                    continue
                unknown += 1
                if unknown_key is not None:
                    causes[str(r.get(unknown_key))] += 1
            else:
                known_pnl += ORDER_BUDGET * net
                known.append(net)
        lb = known_pnl - ORDER_BUDGET * unknown
        daily.append(
            {
                "day": day,
                "known_pnl": known_pnl,
                "unknown": unknown,
                "lower_bound_pnl": lb,
                "lower_bound_return": lb / initial,
            }
        )
    returns = np.array([r["lower_bound_return"] for r in daily], dtype=float)
    k = np.array(known, dtype=float)
    monthly: dict[str, list[float]] = defaultdict(list)
    for r in daily:
        monthly[r["day"][:7]].append(r["lower_bound_return"])
    wins = float(k[k > 0].sum()) if k.size else 0.0
    losses = float(-k[k < 0].sum()) if k.size else 0.0
    view = {
        "days": len(calendar),
        "attempts": len(records),
        "fills": len(records),
        "known_fills": len(known),
        "unknown_fills": int(sum(r["unknown"] for r in daily)),
        "known_no_order_fills": n_no_order,
        "cash_or_slot_skips": 0,
        "cost_bps": cost_bps,
        "horizon": HORIZON,
        "pricing": pricing,
        "mean_net_known_fill": float(k.mean()) if k.size else None,
        "mean_daily_lower_bound": float(returns.mean()) if returns.size else None,
        "daily_se": (
            float(returns.std(ddof=1) / np.sqrt(returns.size)) if returns.size > 1 else None
        ),
        "known_win_rate": float((k > 0).mean()) if k.size else None,
        "known_profit_factor": (float(wins / losses) if losses else None),
        "worst_known_fill": float(k.min()) if k.size else None,
        "positive_months_lower_bound": int(sum(np.mean(v) > 0 for v in monthly.values())),
        "months": len(monthly),
        "monthly_mean_lower_bound": {m: float(np.mean(v)) for m, v in sorted(monthly.items())},
        "traded_days": len({r["day"] for r in records}),
        "execution_proxy_only": False,
        "quote_touch_not_fill_guaranteed": True,
        "unknown_causes": dict(causes),
        "known_no_order_note": (
            "a budget below one integer share sends no order; the "
            "unfilled cash contributes 0 USD (cash retained), so it "
            "is never charged the UNKNOWN full-unit loss and never "
            "counted as a known fill"
        ),
    }
    view["daily"] = daily
    view["bootstrap_daily"] = bootstrap_daily(list(returns), n=BOOT_N, seed=BOOT_SEED)
    view["monthly"] = monthly_accounting(
        view, [{"day": r["day"], "net": r[net_key]} for r in records]
    )
    return view


def metrics_view(view: dict) -> dict:
    """Stored metrics without the bulky per-day list."""
    return {k: v for k, v in view.items() if k != "daily"}


# ----- annualization (simple rates, never compounded) ------------------------------
def annual_bounds(view: dict) -> dict:
    """252-day simple annualization; covered subset is never the whole portfolio."""
    days, known = view["days"], view["known_fills"]
    fills_per_year = (known / days * TRADING_DAYS_PER_YEAR) if days else None
    covered_ev = (
        view["mean_net_known_fill"] * fills_per_year * ORDER_BUDGET
        if fills_per_year is not None and view["mean_net_known_fill"] is not None
        else None
    )
    daily_lb = view["mean_daily_lower_bound"]
    known_pnl_per_day = sum(r["known_pnl"] for r in view["daily"]) / days if days else 0.0
    return {
        "calendar_days": days,
        "observed_known_fills_per_year": fills_per_year,
        "covered_mean_net_per_fill": view["mean_net_known_fill"],
        "covered_ev_usd_per_year": covered_ev,
        "covered_subset_is_not_whole_portfolio": True,
        "whole_daily_lower_bound": daily_lb,
        "whole_lb_usd_per_year": (
            daily_lb * TRADING_DAYS_PER_YEAR * MAX_POSITIONS * ORDER_BUDGET
            if daily_lb is not None
            else None
        ),
        "whole_lb_charges_unknown_full_loss": True,
        "known_only_usd_per_year_no_unknown_charge": known_pnl_per_day * TRADING_DAYS_PER_YEAR,
        "known_no_order_fills": view.get("known_no_order_fills", 0),
        "known_no_order_note": (
            "a known no-order sends no order and keeps the cash; it "
            "is excluded from known fills and contributes 0 USD, "
            "never a UNKNOWN full-loss charge"
        ),
        "annualization": "simple 252-day rate, never compounded; no capacity scaling assumed",
    }


# ----- paired comparisons -----------------------------------------------------------
def paired_summary(records: list[dict], policy: str, control: str, residual: float) -> dict:
    """Per-trade paired change of `policy` against the paired `control`."""
    tag = int(residual)
    deltas: list[float] = []
    classes: Counter = Counter()
    for r in records:
        a, b = r[f"{policy}_net_{tag}"], r[f"{control}_net_{tag}"]
        if a is None and b is None:
            classes["unknown_both"] += 1
        elif a is None:
            classes["unknown_lost"] += 1
        elif b is None:
            classes["unknown_gained"] += 1
        else:
            d = a - b
            deltas.append(d)
            if abs(d) <= FLOAT_TOL:
                classes["unchanged"] += 1
            elif d > 0:
                classes["improved"] += 1
            else:
                classes["worsened"] += 1
    d = np.array(deltas, dtype=float)

    def q(p: float):
        return float(np.percentile(d, p)) if d.size else None

    return {
        "policy": policy,
        "paired_control": control,
        "residual_bps": residual,
        "fills": len(records),
        "paired_known_pairs": int(d.size),
        "paired_classes": dict(classes),
        "delta_mean": float(d.mean()) if d.size else None,
        "delta_median": q(50),
        "delta_p05": q(5),
        "delta_p95": q(95),
        "delta_min": float(d.min()) if d.size else None,
        "delta_max": float(d.max()) if d.size else None,
        "delta_sum_usd_per_1000_units": float(d.sum()) * ORDER_BUDGET if d.size else None,
    }


def stop_quality(records: list[dict], policy: str, control: str, residual: float) -> dict:
    """Tail, gap and win/loss transition diagnostics (descriptive, never vetoes)."""
    tag = int(residual)
    triggered = [r for r in records if r.get(f"{policy}_triggered")]
    through = [
        r
        for r in triggered
        if r.get(f"{policy}_exit_vs_level_bps") is not None
        and r[f"{policy}_exit_vs_level_bps"] < 0.0
    ]
    win_to_loss = [
        r
        for r in records
        if (r.get(f"{control}_net_{tag}") or 0) > 0
        and (r.get(f"{policy}_net_{tag}") is not None)
        and r[f"{policy}_net_{tag}"] < 0
    ]
    pairs = [
        (r[f"{control}_net_{tag}"], r[f"{policy}_net_{tag}"])
        for r in records
        if r[f"{control}_net_{tag}"] is not None and r[f"{policy}_net_{tag}"] is not None
    ]
    worst = sorted(pairs, key=lambda x: x[0])[:5]
    best = sorted(pairs, key=lambda x: x[0], reverse=True)[:5]
    through_nets = [
        r[f"{policy}_net_{tag}"] for r in through if r[f"{policy}_net_{tag}"] is not None
    ]
    return {
        "policy": policy,
        "paired_control": control,
        "residual_bps": residual,
        "stop_triggers": len(triggered),
        "stop_through_gap_trades": len(through),
        "stop_through_gap_unknown_net_trades": len(through) - len(through_nets),
        "stop_through_gap_mean_bps": (
            float(np.mean([r[f"{policy}_exit_vs_level_bps"] for r in through])) if through else None
        ),
        "stop_through_gap_worst_bps": (
            float(np.min([r[f"{policy}_exit_vs_level_bps"] for r in through])) if through else None
        ),
        "stop_through_worst_net": (float(np.min(through_nets)) if through_nets else None),
        "win_to_loss_transitions": len(win_to_loss),
        "control_worst5_control_net": [round(c, 6) for c, _ in worst],
        "control_worst5_policy_net": [round(p, 6) for _, p in worst],
        "control_best5_control_net": [round(c, 6) for c, _ in best],
        "control_best5_policy_net": [round(p, 6) for _, p in best],
        "not_used_as_a_gate": True,
    }


def frequency_report(records: list[dict]) -> dict:
    """Entry frequency is frozen across policies: same trades, days, minutes."""
    fills = len(records)
    days = {(r["day"], r["ticker"]) for r in records}
    minutes = {(r["day"], r["ticker"], r["entry_et"]) for r in records}
    return {
        "fills": fills,
        "unique_ticker_days": len(days),
        "unique_entry_minutes": len(minutes),
        "identical_entry_set_across_policies": len(days) == fills == len(minutes),
    }


# ----- selection ---------------------------------------------------------------------
def select_policy(val_views: dict[str, dict[str, dict]], primary: float) -> dict:
    """Max validation calendar daily lower bound at the primary residual only."""
    tag = str(int(primary))
    scores = {
        p: (val_views[p][tag]["mean_daily_lower_bound"] if tag in val_views[p] else None)
        for p in POLICIES
    }
    best = None
    for p in POLICIES:  # fixed order: deterministic ties
        s = scores[p]
        if s is not None and (best is None or s > scores[best]):
            best = p
    return {
        "objective": (
            "max 2023 validation calendar mean_daily_lower_bound over ALL "
            "calendar days at the primary actual-touch +25bps residual"
        ),
        "primary_residual_bps": primary,
        "scores": scores,
        "chosen": best,
        "tie_break": "fixed policy order hold60m < stop10 < stop15",
        "late_outcomes_used_in_selection": False,
    }


# ----- CLI ----------------------------------------------------------------------------
def run(args: argparse.Namespace) -> None:
    t0 = time.time()
    val_trades_path = args.val_trades
    late_trades_path = args.late_trades
    val_signals = args.val_signals or val_trades_path.with_name("signals_validation.parquet")
    late_signals = args.late_signals or late_trades_path.with_name("signals_confirmation.parquet")
    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    val_trades = load_block(val_trades_path, val_signals)
    late_trades = load_block(late_trades_path, late_signals)
    val_calendar = block_calendar(args.panel, "validation")
    late_calendar = block_calendar(args.panel, "confirmation")
    print(
        f"[input] val trades={len(val_trades)} calendar={len(val_calendar)}d | "
        f"late trades={len(late_trades)} calendar={len(late_calendar)}d",
        flush=True,
    )

    # 1) validation surface: every policy at every residual rung.
    val_records = evaluate_block(val_trades, args.data, args.supplemental_quotes)
    val_views: dict[str, dict[str, dict]] = {}
    for p in POLICIES:
        val_views[p] = {}
        for r in RESIDUALS:
            view = book_view(
                val_records,
                f"{p}_net_{int(r)}",
                val_calendar,
                unknown_key=f"{p}_unknown_{int(r)}",
                cost_bps=r,
                no_order_key=f"{p}_no_order_{int(r)}",
            )
            val_views[p][str(int(r))] = view
            print(
                f"[val] {p:<8} +{int(r):>3}bps mdlb={view['mean_daily_lower_bound']:+.6f} "
                f"n={view['known_fills']} unknown={view['unknown_fills']} "
                f"days={view['traded_days']}",
                flush=True,
            )
    base_val_views = {}
    for r in sorted(set(RESIDUALS) | {100.0}):
        tag = int(r)
        view = book_view(
            val_records,
            f"base_net_{tag}",
            val_calendar,
            pricing="original_next_open_proxy",
            cost_bps=r,
        )
        base_val_views[str(tag)] = view

    # 2) freeze the choice BEFORE any late-block outcome is computed.
    selection = select_policy(val_views, PRIMARY_RESIDUAL)
    chosen = selection["chosen"]
    if chosen is None:
        raise SystemExit("[exit-mgmt] no policy produced a validation daily lower bound")
    print(
        f"[select] chosen={chosen} by validation calendar mdlb "
        f"{selection['scores'][chosen]:+.6f} @+{int(PRIMARY_RESIDUAL)}bps "
        f"(frozen before late outcomes)",
        flush=True,
    )
    freeze = {
        "study": "alpha_sparse_exit_management",
        "status": STATUS,
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "frozen_before_late_outcomes": True,
        "arrival_latency_assumption": {
            "latency_ms": LATENCY_MS,
            "applies_to": "entry ASK, stop activation ack, market exit arrival",
            "minute_bar_feature_publication_latency": "unmeasured",
            "frontier_alternatives_assessed": [250, 1000, 2000],
            "like_for_like_policy_pairing": True,
            "selection_on_late_outcomes": False,
        },
        "selection": selection,
        "input_sha256": {
            "val_trades": sha256_file(val_trades_path),
            "val_signals": sha256_file(val_signals),
            "late_trades": sha256_file(late_trades_path),
            "late_signals": sha256_file(late_signals),
            "quote_service": sha256_file(
                Path(__file__).resolve().parent / "alpha_sparse_quote_service.py"
            ),
            "producer": sha256_file(Path(__file__)),
        },
        "calendar": {"validation_days": len(val_calendar), "late_days": len(late_calendar)},
    }
    write_json(out / "selection_freeze.json", freeze)

    # 3) late block: control always; the chosen stop only (no late look-elsewhere).
    late_records = evaluate_block(late_trades, args.data, args.supplemental_quotes)
    late_views: dict[str, dict[str, dict]] = {BASE_POLICY: {}, "hold60m": {}}
    if chosen != "hold60m":
        late_views[chosen] = {}
    for p in late_views:
        if p == BASE_POLICY:
            for r in sorted(set(RESIDUALS) | {100.0}):
                view = book_view(
                    late_records,
                    f"base_net_{int(r)}",
                    late_calendar,
                    unknown_key=None,
                    pricing="original_next_open_proxy",
                    cost_bps=r,
                )
                late_views[p][str(int(r))] = view
        else:
            for r in RESIDUALS:
                view = book_view(
                    late_records,
                    f"{p}_net_{int(r)}",
                    late_calendar,
                    unknown_key=f"{p}_unknown_{int(r)}",
                    cost_bps=r,
                    no_order_key=f"{p}_no_order_{int(r)}",
                )
                late_views[p][str(int(r))] = view
                print(
                    f"[late] {p:<8} +{int(r):>3}bps mdlb={view['mean_daily_lower_bound']:+.6f} "
                    f"n={view['known_fills']} unknown={view['unknown_fills']} "
                    f"days={view['traded_days']}",
                    flush=True,
                )

    # 4) original-surface reproduction check (base 100bps replay math, fixed calendar).
    original = None
    if args.original_results is not None and args.original_results.exists():
        original = json.loads(args.original_results.read_text())
    repro = {}
    if original:
        ov = original.get("validation") or {}
        oc = (original.get("confirmation_by_cost") or {}).get("100") or {}
        for tag, ref in (("validation", ov), ("late_100", oc)):
            mine = base_val_views["100"] if tag == "validation" else late_views[BASE_POLICY]["100"]
            repro[tag] = {
                "original_mean_daily_lower_bound": ref.get("mean_daily_lower_bound"),
                "reproduced_mean_daily_lower_bound": mine["mean_daily_lower_bound"],
                "match": (
                    ref.get("mean_daily_lower_bound") is not None
                    and mine["mean_daily_lower_bound"] is not None
                    and abs(ref["mean_daily_lower_bound"] - mine["mean_daily_lower_bound"]) <= 1e-9
                ),
                "original_days": ref.get("days"),
                "reproduced_days": mine["days"],
                "original_known_fills": ref.get("known_fills"),
                "reproduced_known_fills": mine["known_fills"],
            }

    # 5) paired comparisons (validation surface for every policy; late for chosen).
    paired_val = [
        paired_summary(val_records, p, "hold60m", r)
        for p in POLICIES
        if p != "hold60m"
        for r in RESIDUALS
    ]
    stop_val = [
        stop_quality(val_records, p, "hold60m", r)
        for p in POLICIES
        if p != "hold60m"
        for r in RESIDUALS
    ]
    paired_late, stop_late = [], []
    if chosen != "hold60m":
        paired_late = [paired_summary(late_records, chosen, "hold60m", r) for r in RESIDUALS]
        stop_late = [stop_quality(late_records, chosen, "hold60m", r) for r in RESIDUALS]

    # 6) annual EV bounds (simple, uncompounded; covered != whole).
    annual = {"validation": {}, "late": {}, "policy_chosen": chosen}
    for p in POLICIES:
        annual["validation"][p] = {
            str(int(r)): annual_bounds(val_views[p][str(int(r))]) for r in RESIDUALS
        }
    for p in late_views:
        annual["late"][p] = {
            str(int(r)): annual_bounds(late_views[p][str(int(r))])
            for r in (RESIDUALS if p != BASE_POLICY else sorted(set(RESIDUALS) | {100.0}))
        }

    frequency = {
        "validation": frequency_report(val_records),
        "late": frequency_report(late_records),
    }

    results = {
        "contract": freeze,
        "policies": POLICY_LABELS,
        "validation_surface": {
            p: {str(int(r)): metrics_view(val_views[p][str(int(r))]) for r in RESIDUALS}
            for p in POLICIES
        },
        "validation_base_proxy": {tag: metrics_view(v) for tag, v in base_val_views.items()},
        "base_reproduction": repro,
        "selection": selection,
        "late_surface": {
            p: {tag: metrics_view(v) for tag, v in per_r.items()} for p, per_r in late_views.items()
        },
        "paired": {"validation": paired_val, "late": paired_late},
        "stop_quality": {"validation": stop_val, "late": stop_late},
        "annual_ev": annual,
        "frequency_frozen": frequency,
        "decision": (
            f"{STATUS}: chosen={chosen} on 2023 validation calendar mdlb "
            f"{selection['scores'][chosen]:+.6f}@+{int(PRIMARY_RESIDUAL)}bps; late is the "
            "already-explored block, exploratory only, not pristine; quote-covered "
            "subsets are not whole-portfolio estimates"
        ),
        "status": STATUS,
        "artifacts": {
            "selection_freeze": str(out / "selection_freeze.json"),
            "trades_val_paired": str(out / "trades_val_paired.parquet"),
            "trades_late_paired": str(out / "trades_late_paired.parquet"),
            "annual_ev": str(out / "annual_EV.json"),
            "producer_snapshot": str(out / "producer_snapshot.py"),
            "results": str(out / "results.json"),
        },
        "runtime_s": round(time.time() - t0, 1),
    }
    val_frame = pl.DataFrame(val_records)
    val_frame.write_parquet(out / "trades_val_paired.parquet")
    late_frame = pl.DataFrame(late_records)
    late_frame.write_parquet(out / "trades_late_paired.parquet")
    write_json(out / "annual_EV.json", annual)
    shutil.copy2(Path(__file__), out / "producer_snapshot.py")
    write_json(out / "results.json", results)
    print(f"[decision] {results['decision']}", flush=True)
    print(f"[done] results -> {out / 'results.json'} ({results['runtime_s']}s)", flush=True)


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--val-trades",
        type=Path,
        default=PANEL_ROOT / "learned_sparse_extension" / "trades_validation.parquet",
    )
    p.add_argument(
        "--late-trades",
        type=Path,
        default=PANEL_ROOT / "learned_sparse_extension" / "trades_confirmation_100.parquet",
    )
    p.add_argument("--val-signals", type=Path, default=None)
    p.add_argument("--late-signals", type=Path, default=None)
    p.add_argument(
        "--original-results",
        type=Path,
        default=PANEL_ROOT / "learned_sparse_extension" / "results.json",
    )
    p.add_argument(
        "--data",
        type=Path,
        default=DATA_ROOT,
        help="panel data root containing sip/net/quotes (default: %(default)s)",
    )
    p.add_argument(
        "--supplemental-quotes",
        type=Path,
        default=None,
        help="optional fetch-cache root of <day>.parquet extras (default: none)",
    )
    p.add_argument(
        "--panel",
        type=Path,
        default=PANEL_ROOT,
        help="panel root with days/ for block calendars (default: %(default)s)",
    )
    p.add_argument("--out", type=Path, default=PANEL_ROOT / "sparse_exit_management")
    run(p.parse_args())


if __name__ == "__main__":
    main()
