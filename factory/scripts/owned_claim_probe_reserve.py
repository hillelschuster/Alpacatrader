#!/usr/bin/env python3
"""CONTINUOUS INITIAL-EXPOSURE FRONTIER — same original roster, two priced endpoints.

The capital-timing question is priced as a continuous frontier over the ORIGINAL causal
admission roster (never a new selection, never a future-winner gate). Each admitted slot
owns one unit of admission cash budget ``B = 1``. Two rulers deploy it:

  EARLY endpoint (a = 1)  the pre-existing CAUSAL FADE RULER keeps the ORIGINAL owned
      position from admission; the first fade onset at a minute strictly before session
      end executes at that event's own next executable open, otherwise the calendar
      terminal open does. The original entry was already paid at admission (sunk at the
      decision); only the common exit fee is charged on the owned shares.

  LATE endpoint (a = 0)   the slot budget stands in cash; the FIRST ``+5%``
      (``feature_event_profit5``) with NO STRICTLY-PRIOR owned damage
      (``feature_hist_count_damage - feature_event_damage <= 0``, the stored count is
      inclusive of the coincident event flag) strictly before 13:00 (``t < 780``) buys the
      reserved original slot budget at that event's ACTUAL next open, with the share
      quantity fixed from the CAUSAL completed bar mark
      (``0.90 * B / (mark * (1 + side))``), and exits at the 15/30/60-minute ruler open.

Both endpoints are end-cash per unit slot budget measured from the SAME admission
decision, so ``W(a) = a * W_early + (1 - a) * W_late`` is the analytic same-budget
frontier and its crossing with cash is an analytical break-even initial fraction.

Money is exact and additive. UNKNOWN is never zeroed and never resized:
  * an unaffordable gap up (actual cost beyond the reserved cash) is UNKNOWN, never resized;
  * a calendar-KNOWN insufficient horizon at the qualifier (``session_end - t < h``) stands
    in cash (the rule is causal: the session end was known when the decision was made) and
    is decided BEFORE any mark / future open / funding read, so a missing open never
    censors it;
  * any other missing qualification price / execution / ruler exit is UNKNOWN and counted;
  * ``status == "missing"`` roster members make that day's endpoint UNKNOWN (the slot's
    cashflow is not observable), exactly as the replay treats them.
Costs are 100 / 150 bps round trip (50 / 75 bps per side); N3 and N5 and every admission
clock are kept separate. Only discovery days are touched; no protected outcome, no model,
no parameter/alpha grid, no best-horizon selection. This is a DESCRIPTIVE capital-timing
diagnostic, not validated EV and not a replacement thesis: a = 0 is one endpoint, not a
claim that waiting always wins.

Outputs are staged per day (bounded memory, resumable, source/input pinned); the compact
JSON artifact is written only after every requested day has landed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path

os.environ.setdefault("POLARS_MAX_THREADS", "2")
import lifecycle_study as ls  # noqa: E402
import polars as pl  # noqa: E402

CLOCKS = (540, 560, 569, 571)
VIEWS = (3, 5)
SIDES = (0.005, 0.0075)  # 100 / 150 bps round trip
HORIZONS = (15, 30, 60)  # late-exit rulers, minutes from the qualifier minute
END = 780  # 13:00 qualifier deadline (exclusive)
QUANTITY_HEADROOM = 0.90  # fixed causal execution headroom; never resized
BUDGET = 1.0  # one admitted slot = one unit admission cash
CASH = 1.0
KIND = "DISCOVERY-CAPITAL-TIMING-FRONTIER-DIAGNOSTIC-NOT-ALPHA"

EVENT_COLS = [
    "day",
    "clock",
    "rank",
    "ticker",
    "t",
    "session_end",
    "fill_et",
    "fill_px",
    "status",
    "sell_px",
    "sell_et",
    "feature_px",
    "feature_event_profit5",
    "feature_event_damage",
    "feature_hist_count_damage",
    "feature_event_fade_ruler",
    "V15",
    "V30",
    "V60",
    "label_15_et",
    "label_30_et",
    "label_60_et",
]
ROSTER_COLS = ["day", "clock", "rank", "ticker", "status", "fill_px", "fill_et", "session_end"]
REQUIRED_FEATS = (
    "feature_event_profit5",
    "feature_event_damage",
    "feature_hist_count_damage",
    "feature_event_fade_ruler",
)

FLOAT_COLS = ["side", "w_early", "entry_gross", "entry_fee", "exit_gross", "exit_fee"] + [
    f"{p}_{h}"
    for h in HORIZONS
    for p in (
        "w_late",
        "late_cost",
        "late_buy_gross",
        "late_buy_fee",
        "late_remaining",
        "late_sell_gross",
        "late_sell_fee",
        "late_proceeds",
    )
]
BOOL_COLS = ["early_known", "late_coincident_damage"] + [f"late_known_{h}" for h in HORIZONS]
STR_COLS = ["early_status"] + [f"late_status_{h}" for h in HORIZONS]


# --------------------------------------------------------------------- pure helpers
def finite(value) -> bool:
    return value is not None and isinstance(value, (int, float)) and math.isfinite(float(value))


def bps(side: float) -> int:
    return int(round(2.0 * side * 10000.0))


def exec_ok(px, et, session_end) -> bool:
    """An execution is KNOWN only with a finite positive price and an in-session open."""
    return finite(px) and float(px) > 0 and et is not None and int(et) <= int(session_end)


def early_exit(rows: list[dict]) -> tuple[float | None, str]:
    """Existing causal fade ruler: first fade onset before session end, else terminal open.

    ``rows`` MUST be the claim's events sorted by ``t``. A fade onset whose own open is
    not executable is UNKNOWN (mirrors the replay's attempted-unknown), never silently
    replaced by a later print.
    """
    fades = [
        r
        for r in rows
        if finite(r.get("feature_event_fade_ruler"))
        and float(r["feature_event_fade_ruler"]) > 0
        and int(r["t"]) < int(r["session_end"])
    ]
    if fades:
        row = min(fades, key=lambda r: int(r["t"]))
        if exec_ok(row.get("sell_px"), row.get("sell_et"), row["session_end"]):
            return float(row["sell_px"]), "fade_ruler"
        return None, "fade_ruler_unexecutable"
    row = rows[-1]
    if exec_ok(row.get("sell_px"), row.get("sell_et"), row["session_end"]):
        return float(row["sell_px"]), "terminal"
    return None, "terminal_unexecutable"


def late_outcome(rows: list[dict], side: float, h: int) -> tuple[float | None, str, dict, bool]:
    """Reserved-budget late endpoint for one horizon ruler.

    Returns ``(wealth, status, legs, coincident_damage)``. Wealth is end cash per unit
    slot budget; ``None`` is an explicit UNKNOWN that is counted and never zeroed. Cash
    (no qualifier / rejected qualifier / causally known insufficient horizon) is KNOWN
    with wealth exactly ``BUDGET``. The producer stores the INCLUSIVE damage count, so the
    qualifier's strictly-prior damage is
    ``feature_hist_count_damage - feature_event_damage``; the coincident
    ``feature_event_damage`` flag is retained separately for context and never counts as
    prior history.
    """
    prof = [
        r
        for r in rows
        if finite(r.get("feature_event_profit5")) and float(r["feature_event_profit5"]) > 0
    ]
    if not prof:
        return CASH, "no_profit5", {}, False
    q = min(prof, key=lambda r: int(r["t"]))
    hist = q.get("feature_hist_count_damage")
    cur = q.get("feature_event_damage")
    if not finite(hist) or not finite(cur):
        return None, "damage_history_unknown", {}, False
    prior_damage = float(hist) - float(cur)
    coincident = float(cur) > 0
    if prior_damage > 0:
        return CASH, "profit5_prior_damage", {}, coincident
    if int(q["t"]) >= END:
        return CASH, "profit5_after_13", {}, coincident
    # The session end was known at the decision: a calendar-insufficient horizon stands
    # in cash WITHOUT touching the mark, the future open or the funding check.
    se, t = int(q["session_end"]), int(q["t"])
    if se - t < h:
        return CASH, "horizon_cash", {}, coincident
    mark = q.get("feature_px")
    if not finite(mark) or float(mark) <= 0:
        return None, "qualifier_mark_unknown", {}, coincident
    px_open, et_open = q.get("sell_px"), q.get("sell_et")
    if not exec_ok(px_open, et_open, se):
        return None, "qualifier_open_unknown", {}, coincident
    # Quantity is fixed from the causal completed mark; the future open only prices it.
    quantity = QUANTITY_HEADROOM * BUDGET / (float(mark) * (1.0 + side))
    buy_gross = quantity * float(px_open)
    buy_fee = buy_gross * side
    cost = buy_gross + buy_fee
    if cost > BUDGET + 1e-9:
        return None, "unaffordable_gap_unknown", {}, coincident
    value, lab_et = q.get(f"V{h}"), q.get(f"label_{h}_et")
    if not finite(value) or lab_et is None or int(lab_et) > se:
        return None, "exit_unknown", {}, coincident
    sell_gross = quantity * (float(px_open) * (1.0 + float(value)))
    sell_fee = sell_gross * side
    proceeds = sell_gross - sell_fee
    remaining = BUDGET - cost
    wealth = remaining + proceeds
    if not math.isfinite(wealth) or wealth < -1e-9:
        return None, "wealth_nonfinite", {}, coincident
    legs = {
        "late_cost": cost,
        "late_buy_gross": buy_gross,
        "late_buy_fee": buy_fee,
        "late_remaining": remaining,
        "late_sell_gross": sell_gross,
        "late_sell_fee": sell_fee,
        "late_proceeds": proceeds,
    }
    return wealth, "deployed", legs, coincident


def _stats(values: list) -> tuple[float | None, float | None, int]:
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    n = len(vals)
    if n == 0:
        return None, None, 0
    mean = math.fsum(vals) / n
    if n < 2:
        return mean, None, n
    var = math.fsum((v - mean) ** 2 for v in vals) / (n - 1)
    return mean, math.sqrt(var / n), n


def _break_even(w_early: float, w_late: float) -> float | None:
    gap = w_early - w_late
    if not math.isfinite(gap) or abs(gap) <= 1e-12:
        return None
    return (CASH - w_late) / gap


def _crossing_label(w_early: float, w_late: float) -> str:
    """Diagnostic name for where the analytic frontier ``W(a)`` meets cash.

    ``break_even_a`` stays the raw analytic root ``(CASH - W_late) / (W_early - W_late)``
    and is never clamped or optimized. Both endpoints above cash means every admissible
    convex mixture beats cash and the root lies OUTSIDE ``[0, 1]``; both below mirrors it.
    In-domain crossings and single-endpoint cash equality are named explicitly.
    """
    if w_early > CASH and w_late > CASH:
        return "both_endpoints_above_cash"
    if w_early < CASH and w_late < CASH:
        return "both_endpoints_below_cash"
    if w_early == CASH and w_late == CASH:
        return "endpoints_tied_at_cash"
    if w_early > w_late:
        if w_late < CASH < w_early:
            return "minimum_early_fraction_to_beat_cash"
        if w_late == CASH:
            return "late_endpoint_at_cash_early_above"
        if w_early == CASH:
            return "early_endpoint_at_cash_late_below"
        return "late_at_or_below_cash_early_above"
    if w_early < w_late:
        if w_early < CASH < w_late:
            return "maximum_early_fraction_to_beat_cash"
        if w_early == CASH:
            return "early_endpoint_at_cash_late_above"
        if w_late == CASH:
            return "late_endpoint_at_cash_early_below"
        return "early_at_or_below_cash_late_above"
    return "endpoints_tied_no_unique_break_even"


def _concentration(diffs: list) -> tuple[float | None, float | None]:
    absd = sorted((abs(d) for d in diffs), reverse=True)
    total = math.fsum(absd)
    if total <= 0:
        return None, None
    top1 = absd[0] / total
    k = max(1, math.ceil(0.05 * len(absd)))
    top5 = math.fsum(absd[:k]) / total
    return top1, top5


# --------------------------------------------------------------------- per-day staging
def claim_rows(day: str, roster_rows: list[dict], ev_by_key: dict) -> list[dict]:
    out = []
    for r in roster_rows:
        status = r["status"]
        ev = ev_by_key.get((int(r["clock"]), r["ticker"]), [])
        for side in SIDES:
            rec = {
                "day": day,
                "clock": int(r["clock"]),
                "rank": int(r["rank"]),
                "ticker": r["ticker"],
                "status": status,
                "side": float(side),
                "early_known": False,
                "early_status": "unset",
                "w_early": None,
                "late_coincident_damage": False,
                "entry_gross": 0.0,
                "entry_fee": 0.0,
                "exit_gross": 0.0,
                "exit_fee": 0.0,
            }
            for h in HORIZONS:
                rec[f"late_known_{h}"] = False
                rec[f"late_status_{h}"] = "unset"
                rec[f"w_late_{h}"] = None
                for p in (
                    "late_cost",
                    "late_buy_gross",
                    "late_buy_fee",
                    "late_remaining",
                    "late_sell_gross",
                    "late_sell_fee",
                    "late_proceeds",
                ):
                    rec[f"{p}_{h}"] = 0.0

            if status == "blocked":
                rec["early_known"] = True
                rec["early_status"] = "blocked_cash"
                rec["w_early"] = CASH
                for h in HORIZONS:
                    rec[f"late_known_{h}"] = True
                    rec[f"late_status_{h}"] = "blocked_cash"
                    rec[f"w_late_{h}"] = CASH
            elif status == "missing":
                # The slot's cashflow is unobservable: explicit UNKNOWN, never cash.
                rec["early_status"] = "missing_member"
                for h in HORIZONS:
                    rec[f"late_status_{h}"] = "missing_member"
            elif status == "filled":
                fill_px = r.get("fill_px")
                if not ev:
                    rec["early_status"] = "no_events"
                    for h in HORIZONS:
                        rec[f"late_status_{h}"] = "no_events"
                else:
                    rows = sorted(ev, key=lambda x: int(x["t"]))
                    px, estatus = early_exit(rows)
                    if px is None:
                        rec["early_status"] = estatus
                    elif not finite(fill_px) or float(fill_px) <= 0:
                        rec["early_status"] = "fill_px_unknown"
                    else:
                        # Entry cost is exactly the admission budget and is SUNK here.
                        qty = BUDGET / (float(fill_px) * (1.0 + side))
                        eg, ef = qty * float(fill_px), qty * float(fill_px) * side
                        xg, xf = qty * px, qty * px * side
                        if abs((eg + ef) - BUDGET) > 1e-9:
                            raise AssertionError("original entry does not fund the budget")
                        rec.update(
                            {
                                "early_known": True,
                                "early_status": estatus,
                                "w_early": xg - xf,
                                "entry_gross": eg,
                                "entry_fee": ef,
                                "exit_gross": xg,
                                "exit_fee": xf,
                            }
                        )
                    for h in HORIZONS:
                        wealth, lstatus, legs, coincident = late_outcome(rows, side, h)
                        rec[f"late_status_{h}"] = lstatus
                        if coincident:
                            rec["late_coincident_damage"] = True
                        if wealth is not None:
                            rec[f"late_known_{h}"] = True
                            rec[f"w_late_{h}"] = wealth
                            for key, value in legs.items():
                                rec[f"{key}_{h}"] = value
            else:
                rec["early_status"] = "unknown_status"
                for h in HORIZONS:
                    rec[f"late_status_{h}"] = "unknown_status"
            out.append(rec)
    return out


def process_day(day: str, data_root: Path, events_root: Path) -> pl.DataFrame:
    roster = pl.read_parquet(
        ls.v2_dir(data_root) / "roster" / f"{day}.parquet", columns=ROSTER_COLS
    ).filter(pl.col("clock").is_in(list(CLOCKS)))
    events = pl.read_parquet(events_root / "events" / f"{day}.parquet", columns=EVENT_COLS).filter(
        pl.col("clock").is_in(list(CLOCKS))
    )
    ev_by_key: dict = {}
    for row in events.sort(["clock", "ticker", "t"]).to_dicts():
        ev_by_key.setdefault((int(row["clock"]), row["ticker"]), []).append(row)
    rows = claim_rows(day, roster.to_dicts(), ev_by_key)
    if not rows:
        schema = {
            "day": pl.Utf8,
            "clock": pl.Int64,
            "rank": pl.Int64,
            "ticker": pl.Utf8,
            "status": pl.Utf8,
        }
        schema.update(dict.fromkeys(FLOAT_COLS, pl.Float64))
        schema.update(dict.fromkeys(BOOL_COLS, pl.Boolean))
        schema.update(dict.fromkeys(STR_COLS, pl.Utf8))
        return pl.DataFrame(schema=schema)
    frame = pl.DataFrame(rows, infer_schema_length=None)
    return frame.with_columns(
        [pl.col(c).cast(pl.Float64) for c in FLOAT_COLS if c in frame.columns]
        + [pl.col(c).cast(pl.Boolean) for c in BOOL_COLS if c in frame.columns]
        + [pl.col(c).cast(pl.Utf8) for c in STR_COLS if c in frame.columns]
    )


def day_key(day: str, data_root: Path, events_root: Path) -> str:
    ev = events_root / "events" / f"{day}.parquet"
    marker = events_root / "_done" / f"{day}.json"
    parts = [day, hashlib.sha256(ev.read_bytes()).hexdigest()]
    parts.append(hashlib.sha256(marker.read_bytes()).hexdigest() if marker.exists() else "-")
    return hashlib.sha256(":".join(parts).encode()).hexdigest()


# --------------------------------------------------------------------- aggregation
def _day_aggregate(view: pl.DataFrame) -> list[dict]:
    agg = [
        pl.len().alias("members"),
        (pl.col("status") == "filled").sum().alias("filled_members"),
        (pl.col("status") == "blocked").sum().alias("blocked_members"),
        (pl.col("status") == "missing").sum().alias("missing_members"),
        pl.col("early_known").all().alias("early_all"),
        pl.col("w_early").mean().alias("w_early"),
    ]
    for h in HORIZONS:
        agg += [
            pl.col(f"late_known_{h}").all().alias(f"late_all_{h}"),
            pl.col(f"w_late_{h}").mean().alias(f"w_late_{h}"),
        ]
    return view.group_by(["day", "clock", "side"]).agg(agg).to_dicts()


def _cell_support(sub: pl.DataFrame) -> dict:
    support = {
        "roster_members": sub.height,
        "filled_members": int((sub["status"] == "filled").sum()),
        "blocked_members": int((sub["status"] == "blocked").sum()),
        "missing_members": int((sub["status"] == "missing").sum()),
        "late_coincident_damage_claims": int(sub["late_coincident_damage"].fill_null(False).sum()),
        "early": sub.group_by("early_status").len().sort("early_status").to_dicts(),
    }
    for h in HORIZONS:
        support[f"late_{h}"] = (
            sub.group_by(f"late_status_{h}")
            .len()
            .sort(f"late_status_{h}")
            .rename({f"late_status_{h}": "late_status"})
            .to_dicts()
        )
    return support


def _conservation(sub: pl.DataFrame) -> tuple[dict, float]:
    # The owned budget was actually deployed only where the original slot was filled AND
    # its ruler exit is known; blocked slots are cash and carry no entry cashflow.
    deployed = pl.col("early_known") & pl.col("early_status").is_in(["fade_ruler", "terminal"])
    known = sub.filter(deployed)
    entry = float(known["entry_gross"].sum()) + float(known["entry_fee"].sum())
    exit_cash = float(known["exit_gross"].sum()) - float(known["exit_fee"].sum())
    wealth_e = float(known["w_early"].sum())
    rows = {
        "early_deployed_claims": known.height,
        "early_cash_claims": int((sub["early_status"] == "blocked_cash").sum()),
        "early_budget": known.height * BUDGET,
        "early_entry_cost": entry,
        "early_entry_gross": float(known["entry_gross"].sum()),
        "early_entry_fee_sunk": float(known["entry_fee"].sum()),
        "early_exit_gross": float(known["exit_gross"].sum()),
        "early_exit_fee": float(known["exit_fee"].sum()),
        "early_exit_cash": exit_cash,
        "early_wealth": wealth_e,
        "early_entry_residual": known.height * BUDGET - entry,
        "early_exit_residual": wealth_e - exit_cash,
    }
    worst = max(abs(rows["early_entry_residual"]), abs(rows["early_exit_residual"]))
    for h in HORIZONS:
        dep = sub.filter(pl.col(f"late_status_{h}") == "deployed")
        budget = dep.height * BUDGET
        cost = float(dep[f"late_cost_{h}"].sum())
        buy = float(dep[f"late_buy_gross_{h}"].sum()) + float(dep[f"late_buy_fee_{h}"].sum())
        remaining = float(dep[f"late_remaining_{h}"].sum())
        proceeds = float(dep[f"late_sell_gross_{h}"].sum()) - float(dep[f"late_sell_fee_{h}"].sum())
        wealth_l = float(dep[f"w_late_{h}"].sum())
        residual = max(
            abs(budget - (cost + remaining)),
            abs(cost - buy),
            abs(wealth_l - (remaining + proceeds)),
        )
        rows[f"late_{h}_deployed_claims"] = dep.height
        rows[f"late_{h}_budget"] = budget
        rows[f"late_{h}_buy_cost"] = cost
        rows[f"late_{h}_buy_gross"] = float(dep[f"late_buy_gross_{h}"].sum())
        rows[f"late_{h}_buy_fee_fresh"] = float(dep[f"late_buy_fee_{h}"].sum())
        rows[f"late_{h}_remaining_cash"] = remaining
        rows[f"late_{h}_sell_gross"] = float(dep[f"late_sell_gross_{h}"].sum())
        rows[f"late_{h}_sell_fee"] = float(dep[f"late_sell_fee_{h}"].sum())
        rows[f"late_{h}_proceeds"] = proceeds
        rows[f"late_{h}_wealth"] = wealth_l
        rows[f"late_{h}_residual"] = residual
        worst = max(worst, residual)
    return rows, worst


def build_report(staged: list[Path], provenance: dict) -> dict:
    frames = [pl.read_parquet(p) for p in staged]
    if not frames:
        raise SystemExit("no staged days were produced; refusing an empty artifact")
    allc = pl.concat(frames, how="diagonal_relaxed")
    allc = allc.with_columns(
        [pl.col(c).cast(pl.Float64) for c in FLOAT_COLS if c in allc.columns]
        + [pl.col(c).cast(pl.Boolean) for c in BOOL_COLS if c in allc.columns]
        + [pl.col(c).cast(pl.Utf8) for c in STR_COLS if c in allc.columns]
    )
    frontier, support, conservation, months = [], [], [], []
    worst_residual = 0.0
    for n in VIEWS:
        view = allc.filter(pl.col("rank") <= n)
        dayagg = _day_aggregate(view)
        for clock in CLOCKS:
            for side in SIDES:
                sub = view.filter((pl.col("clock") == clock) & (pl.col("side") == side))
                support.append(
                    {
                        "n": n,
                        "clock": clock,
                        "side": side,
                        "round_trip_bps": bps(side),
                        **_cell_support(sub),
                    }
                )
                cons, residual = _conservation(sub)
                worst_residual = max(worst_residual, residual)
                conservation.append(
                    {
                        "n": n,
                        "clock": clock,
                        "side": side,
                        "round_trip_bps": bps(side),
                        "max_abs_residual": residual,
                        **cons,
                    }
                )
                rows = [d for d in dayagg if d["clock"] == clock and abs(d["side"] - side) < 1e-12]
                for h in HORIZONS:
                    known_e = [
                        d
                        for d in rows
                        if d["members"] > 0 and d["missing_members"] == 0 and d["early_all"]
                    ]
                    known_l = [
                        d
                        for d in rows
                        if d["members"] > 0 and d["missing_members"] == 0 and d[f"late_all_{h}"]
                    ]
                    matched = [d for d in known_e if d[f"late_all_{h}"]]
                    w_e = [d["w_early"] for d in matched]
                    w_l = [d[f"w_late_{h}"] for d in matched]
                    mean_e, se_e, _ = _stats(w_e)
                    mean_l, se_l, _ = _stats(w_l)
                    diffs = [float(a) - float(b) for a, b in zip(w_e, w_l, strict=False)]
                    mean_d, se_d, n_days = _stats(diffs)
                    per_day_a = [
                        (CASH - float(b)) / (float(a) - float(b))
                        for a, b in zip(w_e, w_l, strict=False)
                        if abs(float(a) - float(b)) > 1e-12
                    ]
                    a_mean, a_se, a_n = _stats(per_day_a)
                    in_unit = (sum(1 for a in per_day_a if 0.0 <= a <= 1.0) / a_n) if a_n else None
                    top1, top5 = _concentration(diffs)
                    point_a = None
                    if mean_e is not None and mean_l is not None:
                        point_a = _break_even(mean_e, mean_l)
                    frontier.append(
                        {
                            "n": n,
                            "clock": clock,
                            "side": side,
                            "round_trip_bps": bps(side),
                            "horizon": h,
                            "matched_days": n_days,
                            "known_early_days": len(known_e),
                            "known_late_days": len(known_l),
                            "W_early": mean_e,
                            "W_early_se": se_e,
                            "W_late": mean_l,
                            "W_late_se": se_l,
                            "delta_early_minus_late": mean_d,
                            "delta_se": se_d,
                            "W_early_minus_cash_per100": (
                                None if mean_e is None else (mean_e - CASH) * 100
                            ),
                            "W_late_minus_cash_per100": (
                                None if mean_l is None else (mean_l - CASH) * 100
                            ),
                            "endpoint_direction": (
                                None
                                if mean_d is None
                                else (
                                    "early_dominates"
                                    if mean_d > 0
                                    else "late_dominates"
                                    if mean_d < 0
                                    else "tied"
                                )
                            ),
                            "break_even_a": point_a,
                            "break_even_label": (
                                None
                                if mean_e is None or mean_l is None
                                else _crossing_label(mean_e, mean_l)
                            ),
                            "break_even_a_day_mean": a_mean,
                            "break_even_a_day_se": a_se,
                            "break_even_a_days": a_n,
                            "break_even_a_in_unit_share": in_unit,
                            "share_days_early_wins": (
                                None if not diffs else sum(1 for d in diffs if d > 0) / len(diffs)
                            ),
                            "top_day_abs_share": top1,
                            "top5pct_day_abs_share": top5,
                        }
                    )
                    by_month: dict[str, list] = {}
                    for d in matched:
                        by_month.setdefault(d["day"][:7], []).append(d)
                    for month in sorted(by_month):
                        group = by_month[month]
                        me, _, _ = _stats([d["w_early"] for d in group])
                        ml, _, _ = _stats([d[f"w_late_{h}"] for d in group])
                        months.append(
                            {
                                "n": n,
                                "clock": clock,
                                "side": side,
                                "horizon": h,
                                "month": month,
                                "matched_days": len(group),
                                "W_early": me,
                                "W_late": ml,
                                "break_even_a": (
                                    None if me is None or ml is None else _break_even(me, ml)
                                ),
                            }
                        )
    if worst_residual > 1e-7:
        raise AssertionError(f"dollar conservation violated: max residual {worst_residual}")
    artifact = {
        "kind": KIND,
        "producer": "owned_claim_probe_reserve.py",
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "inputs": provenance,
        "rules": {
            "early_endpoint": (
                "existing causal fade ruler: first fade onset strictly before session end, "
                "else the calendar terminal open; original owned shares, common exit fee"
            ),
            "early_endpoint_source": (
                "events feature_event_fade_ruler (same causal release expression as the "
                "owned_claim_replay 'fade' book); a stale fade book is refused, never used"
            ),
            "late_endpoint": (
                "reserved original slot budget, deployed only on the first +5% with no "
                "strictly-prior owned damage (history minus the coincident event flag) before "
                "13:00, at the actual next open, quantity fixed from the causal completed mark "
                "(90% headroom), exit at the 15/30/60m ruler"
            ),
            "late_qualifier": (
                "feature_event_profit5 > 0 and "
                "feature_hist_count_damage - feature_event_damage <= 0 and t < 780"
            ),
            "views": list(VIEWS),
            "clocks": list(CLOCKS),
            "costs_bps_round_trip": [bps(s) for s in SIDES],
            "horizons": list(HORIZONS),
            "budget": "one admitted slot = one unit of admission cash budget",
            "entry_basis": (
                "early = sunk original admission (its entry fee was paid at the fill); late = "
                "fresh capital paying a buy fee at the qualifier open plus a sell fee at the exit"
            ),
            "unaffordable_gap": "UNKNOWN, never resized and never financed",
            "insufficient_horizon": (
                "calendar-known insufficient horizon stands in cash (causal) and is decided "
                "before any mark, future open or funding read; any other missing qualification "
                "price or ruler exit is UNKNOWN and counted"
            ),
            "frontier": "W(a) = a*W_early + (1-a)*W_late; break_even_a solves W(a) = cash",
            "break_even_domain": (
                "break_even_a is the raw analytic root, never clamped or optimized; both "
                "endpoints above (or below) cash puts it outside [0, 1], and in-domain "
                "crossings versus endpoint-cash equality are labeled explicitly"
            ),
        },
        "frontier": frontier,
        "by_month": months,
        "support": support,
        "conservation": conservation,
        "max_abs_conservation_residual": worst_residual,
        "caveats": [
            "Descriptive capital-timing diagnostic, not validated EV and not a strategy verdict.",
            "a = 0 is one endpoint measurement, never a replacement thesis; the frontier is "
            "linear by construction and no alpha grid or winning horizon is searched.",
            "Late exits 15/30/60m are rulers reported independently; no horizon is preferred.",
            "Qualification uses only past owned-path state; the admission roster is fixed and "
            "no future winner or liquidity is used as a gate.",
            "Only discovery days are read; protected outcomes and FREEZE are never touched.",
            "Open prices are modeled rulers, not quote/queue-certified fills.",
        ],
    }
    return artifact


# --------------------------------------------------------------------- CLI
def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--events-root", type=Path, default=None)
    ap.add_argument(
        "--out", type=Path, default=root / "factory/artifacts/owned_claim_probe_reserve.json"
    )
    ap.add_argument(
        "--stage", type=Path, default=root / "factory/artifacts/owned_claim_probe_reserve_days"
    )
    ap.add_argument("--limit", type=int, help="smoke: price only the first N discovery days")
    a = ap.parse_args()

    events_root = a.events_root or (ls.v2_dir(a.data_root) / "owned_claim")
    days = ls.discovery_days(a.data_root)
    manifest_path = events_root / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"owned-claim events manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text())
    if list(manifest.get("discovery_days", [])) != days:
        raise SystemExit("events manifest is not exactly the guarded discovery split")

    # Require the CORRECTED fade book: the events must come from the current producer.
    here = Path(__file__).resolve().parent
    ev_src, peer_src = here / "owned_claim_events.py", here / "owned_claim_observable_peers.py"
    want_ev, want_peer = _sha256_file(ev_src), _sha256_file(peer_src)
    src = manifest.get("source_hashes") or {}
    if src:
        stale = src.get("events_sha256") != want_ev or src.get("peer_helper_sha256") != want_peer
    else:
        stale = (
            manifest.get("source_sha256")
            != hashlib.sha256(ev_src.read_bytes() + peer_src.read_bytes()).hexdigest()
        )
    if stale:
        raise SystemExit(
            "owned-claim events are NOT built from the current corrected producer; the causal "
            "fade book is stale — rebuild owned_claim_events (+ replay) before pricing the frontier"
        )
    feats = set(manifest.get("feature_columns") or [])
    missing = [c for c in REQUIRED_FEATS if c not in feats]
    if missing:
        raise SystemExit(f"events manifest lacks the corrected rulers: {missing}")

    if a.limit:
        days = days[: a.limit]
    if not days:
        raise SystemExit("no discovery days selected")
    ls.assert_coverage(days, a.data_root, "panel")
    ls.assert_coverage(days, a.data_root, "roster")

    run_id = hashlib.sha256(
        (
            Path(__file__).read_text()
            + json.dumps(days)
            + json.dumps(
                {"clocks": CLOCKS, "views": VIEWS, "sides": SIDES, "horizons": HORIZONS, "end": END}
            )
        ).encode()
    ).hexdigest()
    a.stage.mkdir(parents=True, exist_ok=True)
    for index, day in enumerate(days):
        marker = a.stage / f"{day}.json"
        parquet = a.stage / f"{day}.claims.parquet"
        key = day_key(day, a.data_root, events_root)
        if marker.exists() and parquet.exists():
            try:
                info = json.loads(marker.read_text())
                if info.get("run_id") == run_id and info.get("input_key") == key:
                    continue
            except Exception:
                pass
        frame = process_day(day, a.data_root, events_root)
        tmp = parquet.with_suffix(".tmp.parquet")
        frame.write_parquet(tmp)
        tmp.replace(parquet)
        marker.write_text(
            json.dumps({"run_id": run_id, "day": day, "input_key": key, "claims": frame.height})
            + "\n"
        )
        if index % 25 == 0:
            print(f"{index + 1}/{len(days)} {day}", flush=True)

    staged = [
        a.stage / f"{day}.claims.parquet"
        for day in days
        if (a.stage / f"{day}.claims.parquet").exists()
    ]
    provenance = {
        "data_root": str(a.data_root),
        "events_root": str(events_root),
        "discovery_days": len(days),
        "first_day": days[0],
        "last_day": days[-1],
        "split_sha256": _sha256_file(ls.v2_dir(a.data_root) / "split.json"),
        "events_manifest_sha256": _sha256_file(manifest_path),
        "events_source_sha256": manifest.get("source_sha256"),
        "events_source_hashes": manifest.get("source_hashes"),
        "required_events_sha256": want_ev,
        "required_peer_helper_sha256": want_peer,
        "staged_day_files": len(staged),
        "resume_input_key": "sha256(events file sha256 + events _done marker sha256 + day) per day",
    }
    artifact = build_report(staged, provenance)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = a.out.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(artifact, indent=2, allow_nan=False) + "\n")
    tmp.replace(a.out)
    head = [r for r in artifact["frontier"] if r["n"] == 3 and r["round_trip_bps"] == 100]
    print("Reserve probe written", a.out, "days", len(days), "cells", len(artifact["frontier"]))
    print(
        pl.DataFrame(head)
        .select(
            [
                "clock",
                "horizon",
                "matched_days",
                "W_early",
                "W_late",
                "delta_early_minus_late",
                "break_even_a",
            ]
        )
        .sort(["clock", "horizon"])
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
