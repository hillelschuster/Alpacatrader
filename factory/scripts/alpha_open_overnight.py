#!/usr/bin/env python3
"""Causal long overnight participation on names admitted by the B snapshots.

Rules frozen in contract.json of the output root (v1):
- Admission: identical predicate to the open-search panel - full-PIT SIP "B"
  snapshots at T in (575, 585, 600, 630, 660, 720), score >= 0.05, px_T >= 1,
  px fresh (px_T_et >= T-2). Watch only names already admitted; no new inputs.
- Decision: session_end - 4 (955 normal, 775 early close) using actual tape bars
  with et strictly < decision; the last completed bar must be decision-1.
- Entry: exact open of the decision minute when that minute's bar exists;
  otherwise the intent expires unfilled, cash retained, no fee.
- Exit: the open of the IMMEDIATE next trading session at et=570, taken from the
  next session's net bars; fallback is the compact universe o570 (delayed/missing
  recorded explicitly; missing => UNKNOWN, never a c_last proxy).
- Session adjacency is mechanical: leaderboard file names cross-checked against
  the canonical calendar. A signal day whose immediate next session is protected
  (2024, 2025-01, 2026-06..08) is a boundary day and is excluded from the study
  BEFORE qualification; exclusions are reported. Protected days are never read.
- Corporate actions: data/harvest01/base/splits.parquet (the Alpaca corporate
  action feed used by the basket_harvest_* family). Events with
  signal_day < ex_date <= exit_day restate the entry price onto the post-event
  basis (factor = old_rate/new_rate, the worktree convention), i.e. shares are
  multiplied by new/old instead of dropping the name afterwards. Identity is
  verified against the tape's own overnight discontinuity: after removing the
  recorded factor the residual must not itself be split-like (>=2.5x or <=0.4x).
  A discontinuity with no record, or a record that does not explain it, makes
  that trade's return UNKNOWN - never an invented number. split_flags-style
  future-intraday heuristics are NOT used as identity.
- Equal $1,000 research unit per name/day (fractional shares, no cash-in-lieu),
  max 3 positions per day ranked by causal close-to-high strength (dd_day_high
  desc, then ticker), $3,000 margin-eligible sub-book, long only, no borrow
  model, no intraday recycling (570 exits release before 955 buys). Costs are
  the charged roundtrip bps only (100/150/200); the margin/overnight-collateral
  character of the sub-book is explicit - no financing is netted into returns.
- UNKNOWN exits charge the position's full budget in the lower bound; cash days
  are never dropped from the daily series.
This is research evidence, not a validated strategy or an exchange-fill model.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import polars as pl
from alpha_open_panel import SNAPSHOTS, allowed, digest

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
CALENDAR = ROOT / "factory" / "artifacts" / "basket" / "sip" / "phase2_session_calendar.json"
SPLITS = DATA / "harvest01" / "base" / "splits.parquet"
ORDER_BUDGET = 1000.0
MAX_POSITIONS = 3
SUBBOOK = MAX_POSITIONS * ORDER_BUDGET
SPLIT_HI = 2.5  # reverse-split-like overnight discontinuity (audit_splits band)
SPLIT_LO = 0.40  # forward-split-like overnight discontinuity
BOOT_REPS = 2000
BOOT_SEED = 20261008
QUALIFICATIONS = ("qA", "qB", "qC")
COSTS = (100, 150, 200)


def split_like(x: float) -> bool:
    return not np.isfinite(x) or x <= 0 or x >= SPLIT_HI or x <= SPLIT_LO


# ---------------------------------------------------------------- frontier
def frontier() -> dict:
    """Mechanical session adjacency + protected boundary detection."""
    cal = json.loads(CALENDAR.read_text())
    cal_end = {d: int(v["session_end"]) for d, v in cal["evidence"].items()}
    lb_days = sorted(p.name[3:13] for p in (DATA / "leaderboard").glob("lb_*.parquet"))
    lb_days = [d for d in lb_days if len(d) == 10]
    lb = np.array(lb_days)
    dev_days = sorted(d for d in cal_end if allowed(d))
    dev_set = set(dev_days)
    mismatch, boundary, nxt_of = [], [], {}
    kept = []
    for d in dev_days:
        i = int(np.searchsorted(lb, d, side="right"))
        nxt = lb_days[i] if i < len(lb_days) else None
        nxt_of[d] = nxt
        if nxt is None or nxt not in dev_set:
            boundary.append(
                {
                    "day": d,
                    "next_leaderboard_day": nxt,
                    "reason": "next_session_protected_or_missing",
                }
            )
            continue
        j = int(np.searchsorted(np.array(dev_days), d, side="right"))
        if dev_days[j] != nxt:
            mismatch.append({"day": d, "calendar_next": dev_days[j], "leaderboard_next": nxt})
        kept.append(d)
    if mismatch:
        raise ValueError(f"calendar/leaderboard adjacency mismatch: {mismatch[:5]}")
    return {
        "cal_end": cal_end,
        "leaderboard_days": lb_days,
        "dev_days": kept,
        "boundary": boundary,
        "next_of": nxt_of,
    }


# ---------------------------------------------------------------- admissions
def admissions(day: str) -> tuple[dict, dict]:
    candidate = json.loads((DATA / "sip" / "candidates" / f"{day}.json").read_text())
    admit, ranks = {}, {}
    for snap in candidate["snapshots"]:
        if snap["pop"] != "B" or snap["T"] not in SNAPSHOTS:
            continue
        t = snap["T"]
        ranks[t] = {r["symbol"]: r["rank"] for r in snap["top"]}
        for r in snap["top"]:
            if r["score"] >= 0.05 and r.get(f"px_{t}", 0) >= 1 and r.get(f"px_{t}_et", -1) >= t - 2:
                admit.setdefault(r["symbol"], []).append((t, r["score"]))
    return admit, ranks


# ---------------------------------------------------------------- decision row
def decision_row(
    day: str, ticker: str, frame: pl.DataFrame, admissions: list, ranks: dict, session_end: int
) -> dict | None:
    """Features at t = session_end-4 only; identical math to panel symbol_rows."""
    t = session_end - 4
    et = frame["et"].to_numpy()
    o, h, low, c, v = (frame[x].to_numpy() for x in ("open", "high", "low", "close", "volume"))
    if len(et) == 0 or et[0] != 570:
        return None
    if not np.all(np.isfinite(np.column_stack([o, h, low, c, v]))) or np.any(c <= 0):
        raise ValueError(f"nonfinite/nonpositive tape: {day} {ticker}")
    first = min(admissions, key=lambda x: x[0])
    cumdv = np.cumsum(c * v)
    cumvol = np.cumsum(v)
    runhigh = np.maximum.accumulate(h)
    i = int(np.searchsorted(et, t)) - 1
    if i < 0 or et[i] != t - 1 or c[i] < 1:
        return None
    old = {k: max(0, int(np.searchsorted(et, t - 1 - k, side="right")) - 1) for k in (1, 3, 5, 15)}
    s5, s15 = int(np.searchsorted(et, t - 5)), int(np.searchsorted(et, t - 15))
    s10 = int(np.searchsorted(et, t - 10))
    if i < s5 or i < s15:
        return None
    dv5 = float(np.sum(c[s5 : i + 1] * v[s5 : i + 1]))
    dvprev5 = float(np.sum(c[s10:s5] * v[s10:s5]))
    diffs = np.diff(c[s15 : i + 1])
    travel = float(np.abs(diffs).sum())
    current = float(c[i])
    max15, min15 = float(h[s15 : i + 1].max()), float(low[s15 : i + 1].min())
    last_peak = int(np.flatnonzero(h[: i + 1] == runhigh[i])[-1])
    checkpoint = max(x for x in SNAPSHOTS if x <= t)
    entry_idx = int(np.searchsorted(et, t))
    filled = entry_idx < len(et) and et[entry_idx] == t
    entry = float(o[entry_idx]) if filled and o[entry_idx] > 0 else None
    row = {
        "day": day,
        "ticker": ticker,
        "t": t,
        "session_end": session_end,
        "admit_t": first[0],
        "entry_et": t if filled and entry is not None else None,
        "entry_open": entry,
        "entry_status": "filled_proxy" if entry is not None else "unfilled_expired",
        "decision_last_close": current,
        "decision_last_et": int(et[i]),
        "minute": float(t - 570),
        "admission_age": float(t - first[0]),
        "rank_snapshot": float(ranks.get(checkpoint, {}).get(ticker, 11)),
        "admit_gain": float(first[1]),
        "gain_open": current / float(o[0]) - 1,
        "log_price": float(np.log(current)),
        **{f"ret{k}": current / float(c[j]) - 1 for k, j in old.items()},
        "dd_high15": current / max15 - 1,
        "dd_day_high": current / float(runhigh[i]) - 1,
        "rebound_low5": current / float(low[s5 : i + 1].min()) - 1,
        "range5": float(h[s5 : i + 1].max() - low[s5 : i + 1].min()) / current,
        "range15": (max15 - min15) / current,
        "log_cum_dv": float(np.log1p(cumdv[i])),
        "log_dv5": float(np.log1p(dv5)),
        "dv_accel": dv5 / max(dvprev5, 1.0),
        "efficiency15": (current - float(c[s15])) / travel if travel > 0 else 0.0,
        "bars15": float(i - s15 + 1),
        "since_high": float(t - int(et[last_peak]) - 1),
        "vwap_dist": current / (float(cumdv[i]) / max(float(cumvol[i]), 1.0)) - 1,
        "cum_dv": float(cumdv[i]),
        "px": current,
    }
    return row


# ---------------------------------------------------------------- exits
class ExitBook:
    """Next-session opens: net bar at et=570 first, compact universe o570 fallback."""

    def __init__(self):
        self._tape: dict[str, dict[str, float]] = {}
        self._univ: dict[str, dict[str, tuple[float, bool]]] = {}

    def tape_open(self, day: str) -> dict[str, float]:
        if day not in self._tape:
            path = DATA / "sip" / "net" / "bars" / f"{day}.parquet"
            if not path.exists():
                self._tape[day] = {}
            else:
                df = (
                    pl.scan_parquet(path)
                    .filter(pl.col("et") == 570)
                    .select(["ticker", "open", "src"])
                    .collect()
                )
                if df.select(pl.struct("ticker").is_duplicated().any()).item():
                    raise ValueError(f"duplicate 570 bars: {day}")
                self._tape[day] = {
                    t: float(o)
                    for t, o in zip(df["ticker"].to_list(), df["open"].to_list(), strict=True)
                    if o is not None and np.isfinite(o) and o > 0
                }
        return self._tape[day]

    def universe_open(self, day: str) -> dict[str, tuple[float, bool]]:
        if day not in self._univ:
            path = DATA / "sip" / "universe" / "rth" / f"{day}.parquet"
            if not path.exists():
                self._univ[day] = {}
            else:
                df = pl.read_parquet(path).select(["symbol", "o570", "delayed_open"])
                self._univ[day] = {
                    s: (float(o), bool(d))
                    for s, o, d in zip(
                        df["symbol"].to_list(),
                        df["o570"].to_list(),
                        df["delayed_open"].to_list(),
                        strict=True,
                    )
                    if o is not None and np.isfinite(o) and o > 0
                }
        return self._univ[day]

    def resolve(self, day: str, ticker: str) -> dict:
        hit = self.tape_open(day)
        if ticker in hit:
            return {
                "exit_open": hit[ticker],
                "exit_source": "net_bar_570",
                "exit_delayed": False,
                "exit_reason": None,
            }
        u = self.universe_open(day)
        if ticker in u:
            o570, delayed = u[ticker]
            return {
                "exit_open": o570,
                "exit_source": "universe_o570",
                "exit_delayed": bool(delayed),
                "exit_reason": None,
            }
        return {
            "exit_open": None,
            "exit_source": None,
            "exit_delayed": self._univ.get(day, {}).get(ticker, (None, False))[1],
            "exit_reason": "next_session_open_missing",
        }


# ---------------------------------------------------------------- corporate actions
def load_actions() -> dict[str, list[dict]]:
    df = pl.read_parquet(
        SPLITS,
        columns=["symbol", "action_type", "new_rate", "old_rate", "ex_date", "process_date", "id"],
    )
    df = df.with_columns((pl.col("old_rate") / pl.col("new_rate")).alias("factor"))
    out: dict[str, list[dict]] = {}
    for r in df.iter_rows(named=True):
        out.setdefault(r["symbol"], []).append(
            {
                "ex_date": r["ex_date"],
                "action_type": r["action_type"],
                "new_rate": float(r["new_rate"]),
                "old_rate": float(r["old_rate"]),
                "factor": float(r["factor"]),
                "id": r["id"],
            }
        )
    return out


def action_identity(
    symbol: str, prev_day: str, exit_day: str, events: list[dict], raw_gap: float | None
) -> dict:
    """Overnight corporate action for (prev_day, exit_day]; verified against the tape gap."""
    evs = [e for e in events.get(symbol, []) if prev_day < e["ex_date"] <= exit_day]
    dedup, seen = [], set()
    for e in evs:
        key = (e["action_type"], e["new_rate"], e["old_rate"], e["ex_date"])
        if key in seen:  # same action recorded twice (e.g. SNEX 2023-11-27)
            continue
        seen.add(key)
        dedup.append(e)
    base = {
        "action_events": len(dedup),
        "action_ids": ";".join(e["id"] for e in dedup),
        "action_types": ";".join(sorted({e["action_type"] for e in dedup})),
        "split_factor": 1.0,
    }
    if not dedup:
        if raw_gap is None:
            return {
                **base,
                "action_verified": True,
                "split_factor": 1.0,
                "action_note": "no recorded action; exit price missing, discontinuity unobservable",
            }
        if split_like(raw_gap + 1.0):
            base.update(
                {
                    "action_verified": False,
                    "split_factor": None,
                    "unknown_reason": "unverified_action_identity",
                    "action_note": "overnight discontinuity without any recorded "
                    "corporate action; action identity unverified",
                }
            )
            return base
        return {
            **base,
            "action_verified": True,
            "action_note": "no recorded action; overnight move not split-like",
        }
    factor = 1.0
    for e in dedup:
        if not (np.isfinite(e["factor"]) and e["factor"] > 0):
            base.update(
                {
                    "action_verified": False,
                    "split_factor": None,
                    "unknown_reason": "unverified_action_identity",
                    "action_note": "recorded action rates invalid",
                }
            )
            return base
        factor *= e["factor"]
    if raw_gap is None:
        return {
            **base,
            "action_verified": True,
            "split_factor": round(factor, 10),
            "action_note": "action recorded; exit price missing so identity not "
            "cross-checked against the tape",
        }
    residual = (raw_gap + 1.0) / factor
    if split_like(residual):
        base.update(
            {
                "action_verified": False,
                "split_factor": round(factor, 10),
                "unknown_reason": "unverified_action_identity",
                "action_note": f"recorded factor {factor:.6g} does not explain the "
                f"observed discontinuity (residual {residual:.4g})",
            }
        )
        return base
    return {
        **base,
        "action_verified": True,
        "split_factor": round(factor, 10),
        "action_note": f"factor {factor:.6g} explains discontinuity, residual {residual:.4g}",
    }


# ---------------------------------------------------------------- qualification
def qualify(row: dict) -> dict:
    gain, dv, px = row["gain_open"], row["cum_dv"], row["px"]
    close_to_high, ret15 = row["dd_day_high"], row["ret15"]
    return {
        "qA": bool(gain >= 0.10 and dv >= 1_000_000 and px >= 1),
        "qB": bool(gain >= 0.30 and dv >= 10_000_000 and px >= 5 and close_to_high >= -0.05),
        "qC": bool(gain >= 0.10 and dv >= 10_000_000 and px >= 5 and ret15 >= 0.03),
    }


# ---------------------------------------------------------------- intents build
def build_intents(front: dict, actions: dict) -> tuple[list[dict], dict]:
    exits = ExitBook()
    intents, day_info = [], []
    for day in front["dev_days"]:
        session_end = front["cal_end"][day]
        nxt = front["next_of"][day]
        # boundary days were removed from dev_days already; keep the guard explicit
        if nxt is None or nxt not in front["cal_end"]:
            continue
        admit, ranks = admissions(day)
        tape = (
            pl.read_parquet(DATA / "sip" / "net" / "bars" / f"{day}.parquet")
            .filter(pl.col("ticker").is_in(list(admit)))
            .sort(["ticker", "et"])
        )
        if tape.select(pl.struct("ticker", "et").is_duplicated().any()).item():
            raise ValueError(f"duplicate tape bars: {day}")
        tape_tickers = set(tape["ticker"].to_list())
        missing = sorted(set(admit) - tape_tickers)
        built = 0
        for key, f in tape.partition_by("ticker", as_dict=True).items():
            ticker = key[0]
            row = decision_row(day, ticker, f, admit[ticker], ranks, session_end)
            if row is None:
                continue
            built += 1
            ex = exits.resolve(nxt, ticker)
            prev_close_day = None
            # entry-day last actual close is the pre-event basis anchor
            prev_close_day = float(f.sort("et")["close"][-1]) if f.height else None
            raw_gap = None
            if ex["exit_open"] is not None and prev_close_day and prev_close_day > 0:
                raw_gap = ex["exit_open"] / prev_close_day - 1.0
            act = action_identity(ticker, day, nxt, actions, raw_gap)
            gross_raw = gross_adj = None
            unknown_reason = act.get("unknown_reason")
            factor = act["split_factor"] if act["action_verified"] else None
            entry = row["entry_open"]
            if row["entry_status"] != "filled_proxy" or entry is None or entry <= 0:
                unknown_reason = unknown_reason or "entry_unfilled"
            elif ex["exit_open"] is None:
                unknown_reason = unknown_reason or ex["exit_reason"]
            elif act["action_verified"]:
                gross_raw = ex["exit_open"] / entry - 1.0
                gross_adj = ex["exit_open"] / (entry * factor) - 1.0 if factor else gross_raw
            else:
                unknown_reason = unknown_reason or act["unknown_reason"]
            row.update(ex)
            row.update(
                {
                    "next_day": nxt,
                    "next_session_end": front["cal_end"][nxt],
                    "prev_close_day": prev_close_day,
                    "overnight_raw_gap": raw_gap,
                    "event_day_last_et": row["decision_last_et"],
                    **{k: v for k, v in act.items() if k != "split_factor" or v is not None},
                    "gross_raw": gross_raw,
                    "gross_adj": gross_adj,
                    "gross": gross_adj,
                    "unknown_reason": unknown_reason,
                    "shares_unit": ORDER_BUDGET / entry if entry else None,
                    "shares_int": float(int(ORDER_BUDGET // entry)) if entry else None,
                }
            )
            row.update(qualify(row))
            intents.append(row)
        day_info.append(
            {
                "day": day,
                "session_end": session_end,
                "next_day": nxt,
                "admitted": len(admit),
                "decision_rows": built,
                "missing_admitted_tapes": missing,
            }
        )
    return intents, {"days": day_info}


# ---------------------------------------------------------------- replay
def positions_for_day(rows: list[dict], qual: str) -> list[dict]:
    """Selection is causal: rank qualified rows FIRST (dd_day_high desc, ticker asc),
    take max 3, then carry slots. An unfilled selected slot keeps its cash (no fee,
    no PnL) and is never substituted by a lower-ranked name."""
    qualified = [r for r in rows if r[qual]]
    qualified.sort(key=lambda r: (-r["dd_day_high"], r["ticker"]))
    return qualified[:MAX_POSITIONS]


def net_return(gross: float, cost_bps: float) -> float:
    side = cost_bps / 20_000.0
    return (1 + gross) * (1 - side) / (1 + side) - 1


def replay(intents: list[dict], days: list[str], qual: str, cost_bps: float) -> dict:
    by_day: dict[str, list[dict]] = {}
    for r in intents:
        by_day.setdefault(r["day"], []).append(r)
    daily, trades = [], []
    for day in days:
        rows = by_day.get(day, [])
        qualified = [r for r in rows if r[qual]]
        pos = positions_for_day(rows, qual)
        known_pnl, unknown, selected_unfilled = 0.0, 0, 0
        for rank, r in enumerate(pos, 1):
            rec = {
                "day": day,
                "ticker": r["ticker"],
                "t": r["t"],
                "entry_et": r["entry_et"],
                "entry_open": r["entry_open"],
                "exit_day": r["next_day"],
                "exit_et": 570,
                "exit_open": r["exit_open"],
                "exit_source": r["exit_source"],
                "quantity": r["shares_unit"],
                "quantity_int_shares": r["shares_int"],
                "gross_raw": r["gross_raw"],
                "gross_adj": r["gross_adj"],
                "split_factor": r.get("split_factor"),
                "action_verified": r["action_verified"],
                "action_note": r["action_note"],
                "cost_bps": cost_bps,
                "rank": rank,
            }
            if r["entry_status"] != "filled_proxy":
                # selected slot whose market order had no executable minute: cash kept
                selected_unfilled += 1
                rec.update({"status": "unfilled_cash", "unknown_reason": None, "net": None})
                trades.append(rec)
                continue
            if r["gross_adj"] is None:
                unknown += 1
                rec.update(
                    {"status": "unknown", "unknown_reason": r["unknown_reason"], "net": None}
                )
                trades.append(rec)
                continue
            net = net_return(r["gross_adj"], cost_bps)
            rec.update({"status": "filled", "net": net})
            known_pnl += ORDER_BUDGET * net
            trades.append(rec)
        lower = known_pnl - ORDER_BUDGET * unknown
        daily.append(
            {
                "day": day,
                "qualified_intents": len(qualified),
                "selected": len(pos),
                "rank_skipped": max(len(qualified) - len(pos), 0),
                "selected_unfilled": selected_unfilled,
                "selected_filled": len(pos) - selected_unfilled,
                "positions_open": len(pos) - selected_unfilled,
                "unknown": unknown,
                "known_fills": len(pos) - selected_unfilled - unknown,
                "known_pnl": known_pnl,
                "lower_bound_pnl": lower,
                "lower_bound_return": lower / SUBBOOK,
            }
        )
    rets = np.array([d["lower_bound_return"] for d in daily], dtype=float)
    known = np.array([t["net"] for t in trades if t["net"] is not None], dtype=float)
    rng = np.random.default_rng(BOOT_SEED)
    boots = (
        np.array([rets[rng.integers(0, len(rets), len(rets))].mean() for _ in range(BOOT_REPS)])
        if len(rets)
        else np.array([])
    )
    monthly: dict[str, list[float]] = {}
    for d in daily:
        monthly.setdefault(d["day"][:7], []).append(d["lower_bound_return"])
    wins = float(known[known > 0].sum()) if len(known) else 0.0
    losses = float(-known[known < 0].sum()) if len(known) else 0.0
    return {
        "qual": qual,
        "cost_bps": cost_bps,
        "days": len(daily),
        "qualified_intents": sum(d["qualified_intents"] for d in daily),
        "selected_slots": sum(d["selected"] for d in daily),
        "selected_filled_slots": sum(d["selected_filled"] for d in daily),
        "selected_unfilled_slots": sum(d["selected_unfilled"] for d in daily),
        "rank_skipped": sum(d["rank_skipped"] for d in daily),
        "positions_open": sum(d["positions_open"] for d in daily),
        "fills": int(known.size),
        "unknown_fills": int(sum(d["unknown"] for d in daily)),
        "traded_days": sum(1 for d in daily if d["positions_open"] > 0),
        "fill_days": sum(1 for d in daily if d["known_fills"] > 0),
        "zero_cash_days": sum(1 for d in daily if d["selected"] == 0),
        "mean_daily_lower_bound": float(rets.mean()) if len(rets) else None,
        "daily_se": float(rets.std(ddof=1) / np.sqrt(len(rets))) if len(rets) > 1 else None,
        "boot_mean_lo": float(np.percentile(boots, 2.5)) if len(boots) else None,
        "boot_mean_hi": float(np.percentile(boots, 97.5)) if len(boots) else None,
        "worst_fill": float(known.min()) if len(known) else None,
        "best_fill": float(known.max()) if len(known) else None,
        "win_rate": float((known > 0).mean()) if len(known) else None,
        "profit_factor": float(wins / losses) if losses else None,
        "months": len(monthly),
        "positive_months": int(sum(np.mean(v) > 0 for v in monthly.values())),
        "monthly_mean_lower_bound": {k: float(np.mean(v)) for k, v in monthly.items()},
        "total_lower_bound_pnl": float(rets.sum() * SUBBOOK),
        "unknown_by_reason": _reason_counts(trades),
        "daily": daily,
        "trades": trades,
    }


def _reason_counts(trades: list[dict]) -> dict:
    out: dict[str, int] = {}
    for t in trades:
        if t["status"] == "unknown":
            key = t.get("unknown_reason") or "unknown"
            out[key] = out.get(key, 0) + 1
    return out


def period_days(days: list[str], lo: str, hi: str) -> list[str]:
    return [d for d in days if lo <= d <= hi]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out", type=Path, default=Path.home() / "alpha-data" / "open-search-v1" / "overnight"
    )
    ap.add_argument("--days", nargs="+", help="smoke subset (still full adjacency rules)")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    front = frontier()
    if args.days:
        keep = set(args.days)
        front = {**front, "dev_days": [d for d in front["dev_days"] if d in keep]}
    actions = load_actions()
    intents, info = build_intents(front, actions)
    print(f"decision rows: {len(intents)} across {len(front['dev_days'])} days", flush=True)
    if not intents:
        raise SystemExit("no decision rows produced")

    periods = {
        "train": period_days(front["dev_days"], "2021-02-01", "2022-12-31"),
        "validation": period_days(front["dev_days"], "2023-01-01", "2023-12-31"),
        "confirmation": period_days(front["dev_days"], "2025-02-01", "2026-05-31"),
    }
    surface: list[dict] = []
    for qual in QUALIFICATIONS:
        for cost in COSTS:
            for period in ("train", "validation"):
                m = replay(intents, periods[period], qual, cost)
                m["period"] = period
                surface.append(m)
                mean_lb = m["mean_daily_lower_bound"]
                print(
                    f"{qual} {cost}bps {period} mean="
                    f"{mean_lb if mean_lb is not None else float('nan'):.6f} "
                    f"fills={m['fills']} fill_days={m['fill_days']}",
                    flush=True,
                )

    val_surface = [s for s in surface if s["period"] == "validation"]
    eligible = [s for s in val_surface if s["fills"] >= 100 and s["fill_days"] >= 50]
    chosen = None
    if eligible:
        best = max(
            eligible, key=lambda s: (s["mean_daily_lower_bound"], s["fills"], -s["cost_bps"])
        )
        chosen = (best["qual"], best["cost_bps"])
        print(
            f"chosen on validation: {chosen} mean={best['mean_daily_lower_bound']:.6f}", flush=True
        )
    else:
        print(
            "SELECTION BLOCKED: no configuration meets validation fills>=100 and "
            "fill days>=50; confirmation NOT run (data-blocked return)",
            flush=True,
        )

    ladder: dict[int, dict] = {}
    chosen_train = chosen_val = None
    if chosen:
        for cost in COSTS:
            ladder[cost] = replay(intents, periods["confirmation"], chosen[0], cost)
        chosen_val = [
            s for s in val_surface if s["qual"] == chosen[0] and s["cost_bps"] == chosen[1]
        ][0]
        chosen_train = [
            s
            for s in surface
            if s["period"] == "train" and s["qual"] == chosen[0] and s["cost_bps"] == chosen[1]
        ][0]

    # ---------------- outputs
    intents_frame = pl.DataFrame(intents, infer_schema_length=None)
    cols = [
        "day",
        "ticker",
        "t",
        "session_end",
        "admit_t",
        "admit_gain",
        "rank_snapshot",
        "decision_last_et",
        "gain_open",
        "px",
        "cum_dv",
        "ret15",
        "dd_day_high",
        "dd_high15",
        "bars15",
        "entry_status",
        "entry_et",
        "entry_open",
        "next_day",
        "next_session_end",
        "exit_open",
        "exit_source",
        "exit_delayed",
        "prev_close_day",
        "overnight_raw_gap",
        "action_events",
        "action_types",
        "action_ids",
        "split_factor",
        "action_verified",
        "action_note",
        "gross_raw",
        "gross_adj",
        "gross",
        "unknown_reason",
        "shares_unit",
        "shares_int",
        "qA",
        "qB",
        "qC",
        "ret1",
        "ret3",
        "ret5",
        "log_price",
        "log_dv5",
        "dv_accel",
        "efficiency15",
        "rebound_low5",
        "range5",
        "range15",
        "since_high",
        "vwap_dist",
    ]
    frame = intents_frame.select([c for c in cols if c in intents_frame.columns])
    if "exit_et" not in frame.columns:
        frame = frame.with_columns(pl.lit(570).alias("exit_et"))
    if "exit_day" not in frame.columns:
        frame = frame.with_columns(pl.col("next_day").alias("exit_day"))
    frame.write_parquet(args.out / "intents.parquet")

    contract = {
        "version": 1,
        "study": "causal long overnight participation on B-snapshot admitted names",
        "admission": (
            "full-PIT SIP B snapshots T in (575,585,600,630,660,720), "
            "score>=0.05, px>=1, fresh px"
        ),
        "decision": (
            "session_end-4 (955 normal / 775 early); "
            "features from bars strictly et<decision"
        ),
        "entry": (
            "exact decision-minute open when that bar exists; otherwise unfilled, "
            "no fee, slot not consumed"
        ),
        "exit": (
            "immediate next trading session et=570 open; net bar first, "
            "compact universe o570 fallback; missing => UNKNOWN"
        ),
        "adjacency": (
            "leaderboard file names cross-checked vs canonical calendar; boundary signal days "
            "(next session protected) are removed from the study day universe BEFORE "
            "qualification and reported"
        ),
        "corporate_actions": str(SPLITS),
        "action_rule": (
            "factor=old/new restates entry basis; identity verified against tape "
            "overnight discontinuity; unverified => UNKNOWN"
        ),
        "split_like_band": [SPLIT_LO, SPLIT_HI],
        "order": {
            "research_unit_usd": ORDER_BUDGET,
            "max_positions_per_day": MAX_POSITIONS,
            "sub_book_usd": SUBBOOK,
            "fractional_unit": True,
            "ranking": "dd_day_high desc then ticker asc",
        },
        "long_only": True,
        "no_borrow_model": True,
        "margin_note": (
            "margin-eligible research sub-book; costs are charged roundtrip bps only; "
            "no financing netted; no intraday recycling"
        ),
        "qualifications": {
            "qA": "gain_open>=0.10 and cum_dv>=1e6 and px>=1",
            "qB": "gain_open>=0.30 and cum_dv>=1e7 and px>=5 and dd_day_high>=-0.05",
            "qC": "gain_open>=0.10 and cum_dv>=1e7 and px>=5 and ret15>=0.03",
        },
        "costs_bps_round_trip": list(COSTS),
        "periods": {
            "train": "2021-02-01..2022-12-31",
            "validation": "2023-01-01..2023-12-31",
            "confirmation": (
                "2025-02-01..2026-05-31 (previously explored market periods, "
                "NOT pristine holdout)"
            ),
        },
        "selection": (
            "validation daily mean lower-bound, fills>=100 and fill days>=50; "
            "frozen before confirmation; selection ranks qualified rows first "
            "(dd_day_high desc, ticker asc) and never uses entry-fill status "
            "to substitute names"
        ),
        "confirmation_ladder": (
            "chosen qualification reported at all three roundtrip costs 100/150/200 bps"
        ),
        "unknown_convention": (
            "UNKNOWN exits charge the full position budget in the lower bound; "
            "zero-cash days are never dropped"
        ),
        "bootstrap": {
            "reps": BOOT_REPS,
            "seed": BOOT_SEED,
            "unit": "day",
            "percentile": [2.5, 97.5],
        },
        "trade_audit_columns": [
            "day",
            "ticker",
            "entry_et",
            "entry_open",
            "exit_day",
            "exit_et",
            "exit_open",
            "quantity",
            "net",
            "status",
        ],
    }
    (args.out / "contract.json").write_text(json.dumps(contract, indent=1) + "\n")

    if chosen:
        audit_cols = [
            "day",
            "ticker",
            "entry_et",
            "entry_open",
            "exit_day",
            "exit_et",
            "exit_open",
            "quantity",
            "net",
            "status",
        ]
        for cost, m in ladder.items():
            trades = pl.DataFrame(m["trades"], infer_schema_length=None)
            if trades.height:
                extra = [c for c in trades.columns if c not in audit_cols]
                trades = trades.select(audit_cols + extra)
                suffix = "" if cost == chosen[1] else f"_{cost}bps"
                trades.write_parquet(args.out / f"trades{suffix}.parquet")
                trades.write_csv(args.out / f"trades{suffix}.csv")
            pl.DataFrame(m["daily"], infer_schema_length=None).write_csv(
                args.out / f"daily_confirmation_{m['qual']}_{cost}bps.csv"
            )
        pl.DataFrame(ladder[chosen[1]]["daily"], infer_schema_length=None).write_csv(
            args.out / f"daily_{chosen[0]}_{chosen[1]}bps.csv"
        )
        for pname, m in (("train", chosen_train), ("validation", chosen_val)):
            pl.DataFrame(m["daily"], infer_schema_length=None).write_csv(
                args.out / f"daily_{pname}_{m['qual']}_{m['cost_bps']}bps.csv"
            )

    def csv_strip(m: dict) -> dict:
        d = {k: v for k, v in m.items() if k not in ("daily", "trades")}
        for k in ("monthly_mean_lower_bound", "unknown_by_reason"):
            if isinstance(d.get(k), dict):
                d[k] = json.dumps(d[k])
        return d

    pl.DataFrame([csv_strip(s) for s in surface]).write_csv(args.out / "surface.csv")

    def strip(m: dict) -> dict:
        d = {k: v for k, v in m.items() if k not in ("daily", "trades")}
        if isinstance(d.get("monthly_mean_lower_bound"), dict):
            d["monthly_mean_lower_bound"] = json.dumps(d["monthly_mean_lower_bound"])
        return d

    summary = {
        "run": "alpha_open_overnight v1",
        "sources": {
            "calendar": str(CALENDAR),
            "calendar_sha256": digest(CALENDAR),
            "splits": str(SPLITS),
            "splits_sha256": digest(SPLITS),
            "script_sha256": digest(Path(__file__)),
        },
        "boundary_excluded_days": front["boundary"],
        "period_days": {k: len(v) for k, v in periods.items()},
        "decision_rows": len(intents),
        "day_info": info["days"],
        "selection": {
            "rule": "validation daily mean lower-bound; fills>=100 and fill days>=50",
            "chosen": None
            if chosen is None
            else {"qualification": chosen[0], "cost_bps": chosen[1]},
            "frozen_before_confirmation": True,
        },
        "train": strip(chosen_train) if chosen_train else None,
        "validation": strip(chosen_val) if chosen_val else None,
        "confirmation": strip(ladder[chosen[1]]) if chosen else None,
        "confirmation_ladder": {str(c): strip(m) for c, m in ladder.items()},
        "unknown_reasons": ladder[chosen[1]]["unknown_by_reason"] if chosen else None,
        "surface": [strip(s) for s in surface],
        "unverified_action_identities": [
            r for r in intents if r.get("unknown_reason") == "unverified_action_identity"
        ],
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1, default=str) + "\n")
    print(
        json.dumps(
            {
                "chosen": chosen,
                "validation_mean": chosen_val["mean_daily_lower_bound"] if chosen_val else None,
                "validation_fills": chosen_val["fills"] if chosen_val else None,
                "confirmation_mean": ladder[chosen[1]]["mean_daily_lower_bound"]
                if chosen
                else None,
                "confirmation_fills": ladder[chosen[1]]["fills"] if chosen else None,
                "confirmation_unknown": ladder[chosen[1]]["unknown_fills"] if chosen else None,
                "confirmation_worst_fill": ladder[chosen[1]]["worst_fill"] if chosen else None,
                "confirmation_ladder": {
                    str(c): {
                        "mean": m["mean_daily_lower_bound"],
                        "fills": m["fills"],
                        "unknown": m["unknown_fills"],
                        "worst_fill": m["worst_fill"],
                    }
                    for c, m in ladder.items()
                },
                "boundary_excluded": len(front["boundary"]),
            },
            indent=1,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
