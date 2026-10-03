#!/usr/bin/env python3
"""LIFECYCLE-01 economic replay: fixed original claims, cash, partials and delayed opens.

Observations are completed-bar states. Decisions submit fixed share sells or cash-budget
buys to the first subsequent observed open; cash from an unfilled sale is not spendable.
Unknown terminal liquidation remains unknown. This module selects no rule or threshold.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
import math

import polars as pl


@dataclass(frozen=True)
class ReplaySpec:
    clock: int
    universe_n: int = 3
    initial_fraction: float = 1.0
    max_holdings: int = 3
    end: int = 780
    side: float = 0.005
    notional: float = 10000.0
    participation_limit: float | None = None


def finite(x) -> bool:
    return x is not None and math.isfinite(float(x))


def replay(panel: pl.DataFrame, roster: pl.DataFrame, spec: ReplaySpec,
           allocator: Callable[[int, dict, dict, float], dict] | None = None,
           label: str = "hold") -> tuple[list[dict], list[dict], list[dict]]:
    """allocator receives t, past-only states, current position marks, actual free cash.

    Returned weights are target ORIGINAL-capital allocations, never future net worth.
    Prices used to execute queued orders are not passed to the allocator. Limits can
    use only completed historical volume, never the execution minute's future volume.
    """
    daily, members, fills = [], [], []
    eligible = (pl.col("clock") == spec.clock) & (pl.col("rank") <= spec.universe_n)
    rosters = {key[0]: value.sort("rank") for key, value in
               roster.filter(eligible).partition_by("day", as_dict=True).items()}
    panels = {key[0]: value.sort(["t", "rank"]) for key, value in
              panel.filter(eligible).partition_by("day", as_dict=True).items()}
    for day in sorted(rosters):
        ros = rosters[day]
        pp = panels.get(day)
        if pp is None:
            raise ValueError(f"missing replay path for roster day {day}")
        se = min(int(ros["session_end"][0]), spec.end)
        rm = {r["ticker"]: r for r in ros.iter_rows(named=True)}
        # Censor only claims this policy can actually own: initial slots (top max_holdings).
        missing_claims = {tk for tk, r in rm.items()
                          if r.get("status") == "missing" and r.get("rank", 99) <= spec.max_holdings}
        if allocator is None and spec.initial_fraction == 0:
            missing_claims.clear()  # unconditional cash requires no market observations
        by_t: dict[int, dict] = {}
        for row in pp.iter_rows(named=True):
            by_t.setdefault(int(row["t"]), {})[row["ticker"]] = row
        cash = 1.0
        shares = {tk: 0.0 for tk in rm}
        flow = {tk: 0.0 for tk in rm}
        marks = {tk: float(r["decision_px"]) for tk, r in rm.items()
                 if finite(r.get("decision_px")) and r["decision_px"] > 0}
        pending: dict[str, tuple] = {}
        latest: dict[str, dict] = {}
        trades, traded, total_fees, max_part = 0, 0.0, 0.0, 0.0
        censored = []

        def execute(t: int):
            nonlocal cash, trades, traded, total_fees, max_part
            # Existing cash only; process simultaneously executable sales before buys.
            for tk in sorted(list(pending), key=lambda k: pending[k][0] == "buy"):
                kind, et, px, amount, decision, volume = pending[tk]
                if et > t:
                    continue
                if not finite(px) or px <= 0:
                    continue
                if kind == "sell":
                    q = min(amount, shares[tk])
                    gross = q * px
                    fee = gross * spec.side
                    cash += gross - fee
                    flow[tk] += gross - fee
                    shares[tk] -= q
                else:
                    budget = min(amount, cash)
                    q = budget / (px * (1 + spec.side))
                    gross = q * px
                    fee = gross * spec.side
                    cash -= budget
                    flow[tk] -= budget
                    shares[tk] += q
                if q > 1e-12:
                    part = q * spec.notional / volume if finite(volume) and volume > 0 else None
                    if part is not None:
                        max_part = max(max_part, part)
                    trades += 1
                    traded += gross
                    total_fees += fee
                    fills.append({"day": day, "clock": spec.clock, "policy": label,
                                  "ticker": tk, "rank": rm[tk]["rank"], "decision_et": decision,
                                  "exec_et": et, "side": kind, "px": px,
                                  "shares_per_capital": q, "gross_fraction": gross,
                                  "fee_fraction": fee, "participation": part,
                                  "delay_min": et - decision})
                marks[tk] = px
                if allocator is not None and hasattr(allocator, "note_fill"):
                    allocator.note_fill(tk, et, kind)
                del pending[tk]

        # Initial claim buys use fixed original slots; unavailable slots remain cash.
        initial = sorted(rm, key=lambda k: rm[k]["rank"])[:spec.max_holdings]
        slot_budget = spec.initial_fraction / min(spec.universe_n, spec.max_holdings)
        for tk in initial:
            r = rm[tk]
            if slot_budget <= 0 or r.get("status") != "filled" or not finite(r.get("fill_px")):
                continue
            et = int(r["fill_et"])
            if et > se:
                continue
            pending[tk] = ("buy", et, float(r["fill_px"]), slot_budget, spec.clock,
                           r.get("fill_volume"))
        for t in range(spec.clock, se + 1):
            execute(t)
            latest.update(by_t.get(t, {}))
            for tk, row in by_t.get(t, {}).items():
                if finite(row.get("px")) and row["px"] > 0:
                    marks[tk] = float(row["px"])
            if t == se:
                # Cancel unfilled entries; submit endpoint liquidations at later actual
                # opens within this session. A deadline is not an invented fill.
                pending = {k: v for k, v in pending.items() if v[0] == "sell"}
                for tk, q in shares.items():
                    if q <= 1e-12 or tk in pending:
                        continue
                    row = by_t.get(t, {}).get(tk)
                    et = row.get("sell_et") if row is not None else None
                    px = row.get("sell_px") if row is not None else None
                    if (not finite(px) or et is None
                            or et > int(ros["session_end"][0])):
                        censored.append(tk)
                        continue
                    pending[tk] = ("sell", int(et), float(px), q, t,
                                   row.get("sell_volume"))
                for final_t in sorted({p[1] for p in pending.values()}):
                    execute(final_t)
                break
            if allocator is None or t <= spec.clock:
                continue
            # Explicit allowlist: never send labels or the current/future fill to policy.
            observed = {tk: {k: v for k, v in row.items()
                             if not (k.startswith(("V", "fmfe", "fmae", "R"))
                                     or k in {"mfe_day", "mae_day", "mfe_close", "mae_close",
                                              "t_peak", "sell_px", "sell_et", "sell_volume",
                                              "exit_px", "exit_et", "state_cls"})}
                        for tk, row in latest.items() if row["t"] == t}
            position = {tk: q * marks.get(tk, 0.0) for tk, q in shares.items()}
            target = allocator(t, observed, position, cash)
            desired = {tk: max(0.0, float(target.get(tk, 0))) for tk in rm}
            selected = sorted((k for k in desired if desired[k] > 0),
                              key=lambda k: (-desired[k], rm[k]["rank"]))[:spec.max_holdings]
            desired = {k: desired[k] if k in selected else 0.0 for k in rm}
            desired = {k: min(v, 1.0) for k, v in desired.items()}
            # Quantities/budgets decided from already known marks, not future opens.
            for tk in sorted(rm, key=lambda k: desired[k] > position.get(k, 0)):
                if tk in pending:
                    continue
                mark = marks.get(tk)
                row = by_t.get(t, {}).get(tk)
                if not finite(mark) or mark <= 0 or row is None:
                    continue
                delta = desired[tk] - position.get(tk, 0.0)
                if abs(delta) < 0.05:
                    continue
                px, et = row.get("sell_px"), row.get("sell_et")
                if not finite(px) or et is None or et > se:
                    continue
                if delta < 0:
                    quantity = min(shares[tk], -delta / mark)
                    pending[tk] = ("sell", int(et), float(px), quantity, t,
                                   row.get("sell_volume"))
                else:
                    occupied = sum(q > 1e-12 for q in shares.values()) + sum(
                        p[0] == "buy" and p[3] > 0 and shares[k] <= 1e-12
                        for k, p in pending.items())
                    if shares[tk] <= 1e-12 and occupied >= spec.max_holdings:
                        continue
                    reserved = sum(p[3] for p in pending.values() if p[0] == "buy")
                    budget = min(delta, max(0.0, cash - reserved))
                    if spec.participation_limit is not None:
                        past_volume = row.get("v5")
                        if not finite(past_volume) or past_volume <= 0:
                            continue
                        budget = min(budget, spec.participation_limit * past_volume / 5
                                     * mark / spec.notional)
                    if budget > 1e-6:
                        pending[tk] = ("buy", int(et), float(px), budget, t,
                                       row.get("sell_volume"))
            execute(t)
        unknown = bool(missing_claims or censored or any(q > 1e-10 for q in shares.values()))
        ret = None if unknown else cash - 1
        if cash < -1e-9:
            raise AssertionError(f"negative cash {day}: {cash}")
        daily.append({"day": day, "clock": spec.clock, "policy": label,
                      "ret": ret, "cash": cash, "unknown": unknown,
                      "unknown_tickers": sorted(missing_claims | set(censored)), "orders": trades,
                      "turnover": traded, "fees": total_fees,
                      "max_participation": max_part, "end": se})
        for tk, r in rm.items():
            unresolved = shares[tk] > 1e-10 or tk in missing_claims
            members.append({"day": day, "clock": spec.clock, "policy": label,
                            "ticker": tk, "rank": r["rank"],
                            "pnl": None if unresolved else flow[tk],
                            "unknown": unresolved, "entry_status": r.get("status"),
                            "mfe_day": r.get("mfe_day"), "mae_day": r.get("mae_day")})
        if not unknown and abs(sum(flow.values()) - ret) > 1e-8:
            raise AssertionError(f"cashflow conservation {day}")
    return daily, members, fills
