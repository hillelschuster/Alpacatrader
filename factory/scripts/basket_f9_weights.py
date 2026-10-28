#!/usr/bin/env python3
"""BASKET-01 Phase-2 family F9 — weighting / sizing (selection alpha vs sizing alpha).

Contract: `researches/PRE-REG-BASKET-02.md` §3.8 (FROZEN 2026-09-22) and
`factory/BASKET-SIM-CONTRACT.md` (FROZEN 2026-09-22).  No engine edits: this module
imports `basket_sim` and provides a weighted day simulator that differs from the
engine's `simulate_day` in exactly one respect — the per-slot ENTRY budget is a
weighted share of C0, and (optionally) a fixed checkpoint rebalances positions among
the surviving tickets.  Core semantics (fills from anatomy, 1-bar lag, next-bar-open
execution, forced flat, cash/no-leverage invariants, metric table) are untouched.

Weighting schemes (§3.8, all renormalized to total gross C0):
  equal       1/N
  rank_linear w ∝ N-rank+1
  score_gap   w ∝ selection score `sel`
  gate_G1/G2/G3  1.5x for F2 gate members @585 (entry-time for B600, reweight @585 for A_pm)
  survival    1.5x at a later checkpoint if (passed >=1 gate @585) AND dd_from_running_high >= -0.10

Outputs: per-cell canonical engine outputs under F9_weighting/runs/<cell_id>/ plus
`comparison.json`, `results.md`, `README.md`.

Usage:
  .venv/bin/python factory/scripts/basket_f9_weights.py --self-test
  .venv/bin/python factory/scripts/basket_f9_weights.py --list
  .venv/bin/python factory/scripts/basket_f9_weights.py --smoke
  .venv/bin/python factory/scripts/basket_f9_weights.py --all
  .venv/bin/python factory/scripts/basket_f9_weights.py --report-only
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import polars as pl

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
import basket_sim as bs  # noqa: E402

OUT_ROOT = ROOT / "factory" / "artifacts" / "basket" / "phase2" / "F9_weighting"
RUNS_DIR = OUT_ROOT / "runs"
STATE_DIR = OUT_ROOT / "_state"
PANEL_PATH = ROOT / "factory" / "artifacts" / "basket" / "phase2" / "F2_golden" / "state_panel.parquet"

C0 = bs.C0
FIRST_ET = bs.FIRST_ET

ENTRIES = {"A_pm": ("A_pm", 570), "B600": ("B", 600)}
N_SLOTS = 3
SCHEMES = ("equal", "rank_linear", "score_gap",
           "gate_G1", "gate_G2", "gate_G3", "survival")
B1_END = "2023-12-29"
B2_START = "2025-02-01"


def _first_ge(et: np.ndarray, x: int) -> int:
    return int(np.searchsorted(et, x, side="left"))


def _idx_of(et: np.ndarray, x: int) -> int:
    i = int(np.searchsorted(et, x, side="left"))
    return i if i < len(et) and int(et[i]) == x else -1


def _gate585_feats(bars, n: dict, med: float, breadth10: int) -> dict | None:
    """G1/G2/G3 membership + raw features at cp585, open0930-anchored."""
    b = bars.ticker(n["ticker"])
    if b is None:
        return None
    et, hi, cl, vo = b["et"], b["high"], b["close"], b["volume"]
    o9 = n.get("open0930")
    pc = n.get("prev_close")
    if not o9 or o9 <= 0:
        return None
    ai = _first_ge(et, 570)
    if ai >= len(et):
        return None
    ti = _idx_of(et, 585)
    if ti < 0 or ti <= ai:
        return None
    anchor = float(o9)
    seg_h = hi[ai:ti + 1]
    mfe = float(seg_h.max() / anchor - 1.0)
    dd = float(cl[ti] / seg_h.max() - 1.0)
    cumv = float(vo[ai:ti + 1].sum())
    ro9 = float(cl[ti] / anchor - 1.0)
    rpc = float(cl[ti] / pc - 1.0) if pc and pc > 0 else None
    rs = float(ro9 - med) if med == med else None
    G1 = bool(mfe >= 0.10 and rpc is not None and rpc >= 0.50 and cumv >= 1e7)
    G2 = bool(breadth10 >= 2 and mfe >= 0.10 and dd >= -0.10)
    G3 = bool(ro9 >= 0.10 and dd >= -0.10 and rs is not None and rs >= 0.05)
    return {"G1": G1, "G2": G2, "G3": G3, "mfe": mfe, "dd": dd, "cumv": cumv,
            "ro9": ro9, "rpc": rpc, "rs": rs}


def _dd_from_fill(bars, n: dict, cp: int) -> float | None:
    """dd_from_running_high at cp, anchored at the ticket's own fill (et, px)."""
    b = bars.ticker(n["ticker"])
    fl = n.get("fill")
    if b is None or not fl:
        return None
    et, hi, cl = b["et"], b["high"], b["close"]
    ai = _first_ge(et, int(fl["et"]))
    ti = _idx_of(et, cp)
    if ti < 0 or ti <= ai:
        return None
    return float(cl[ti] / hi[ai:ti + 1].max() - 1.0)


def compute_gate_features(rec: dict, bars) -> dict:
    """Return {ticker: {G1,G2,G3,dd600,dd615}} for A_pm top-3 and B600 top-3.

    Cohort (basket breadth / rel strength) = A_pm top-10, matching the F2 gate.
    dd600 = dd_from_running_high @600 (A_pm fill-anchored); dd615 = @615 (B600 fill-anchored).
    """
    snap_apm = bs.snapshot_of(rec, "A_pm", 570)
    snap_b = bs.snapshot_of(rec, "B", 600)
    cohort = snap_apm["names"] if snap_apm else []
    cohort_ret = []
    for n in cohort:
        b = bars.ticker(n["ticker"])
        if b is None:
            continue
        ti = _idx_of(b["et"], 585)
        o9 = n.get("open0930")
        if ti >= 0 and o9:
            cohort_ret.append(float(b["close"][ti]) / float(o9) - 1.0)
    med = float(np.median(cohort_ret)) if cohort_ret else float("nan")
    breadth10 = int(sum(1 for r in cohort_ret if r >= 0.10))
    out: dict = {}
    for n in (snap_apm["names"][:N_SLOTS] if snap_apm else []):
        f = _gate585_feats(bars, n, med, breadth10)
        if f is None:
            f = {"G1": False, "G2": False, "G3": False}
        f["dd600"] = _dd_from_fill(bars, n, 600)
        out[n["ticker"]] = f
    for n in (snap_b["names"][:N_SLOTS] if snap_b else []):
        f = _gate585_feats(bars, n, med, breadth10)
        if f is None:
            f = {"G1": False, "G2": False, "G3": False}
        f["dd615"] = _dd_from_fill(bars, n, 615)
        out[n["ticker"]] = f
    return out


# --------------------------------------------------------------------------- #
# Weighted day simulator
# --------------------------------------------------------------------------- #
def _do_rebalance(strat, bars, day: str, t: int, mult: dict, side: float) -> None:
    opens = [tk for tk in strat.open_tickets.values()
             if tk.open and tk.pending is None and tk.sleeve_day == day]
    if not opens:
        return
    total_cost = sum(tk.cost_open for tk in opens)
    if total_cost <= 1e-12:
        return
    raw = {tk.ticker: float(mult.get(tk.ticker, 1.0)) for tk in opens}
    tot = sum(raw.values())
    if tot <= 1e-12:
        return
    w = {tk.ticker: raw[tk.ticker] / tot for tk in opens}

    def opx(tk):
        tkb = bars.ticker(tk.ticker)
        pos = bs._pos_of_et(tkb, t) if tkb is not None else -1
        return float(tkb["open"][pos]) if pos >= 0 else None

    for tk in sorted(opens, key=lambda x: x.ticker):          # reduce over-weights first
        target = total_cost * w[tk.ticker]
        if target < tk.cost_open - 1e-12:
            px = opx(tk)
            if px is None:
                continue
            frac = 1.0 - target / tk.cost_open
            bs._execute(strat, tk, "REDUCE", px, t, "REBAL_DOWN", side, frac=frac)
    for tk in sorted(opens, key=lambda x: x.ticker):          # then add under-weights
        target = total_cost * w[tk.ticker]
        if target > tk.cost_open + 1e-12:
            px = opx(tk)
            if px is None or tk.unit_notional <= 0:
                continue
            notional = target - tk.cost_open
            frac = notional / tk.unit_notional
            bs._execute(strat, tk, "ADD", px, t, "REBAL_UP", side, frac=frac)


def simulate_day_weighted(strat, day: str, rec: dict, bars, session_end: int,
                          bps_total: float, weights: dict | None = None,
                          rebalance: dict | None = None) -> dict:
    """Engine `simulate_day` with per-slot weighted entry + optional checkpoint rebalance."""
    side = bps_total / 2.0 / 10000.0
    snap = bs.snapshot_of(rec, strat.spec.entry_pop, strat.spec.entry_T)
    if snap is not None:
        names = snap["names"][: strat.spec.top_n]
        sel_tickers: list[str] = []
        seen: set[str] = set()
        for nm in names:
            t = nm["ticker"]
            if t not in seen:
                seen.add(t)
                sel_tickers.append(t)
        if weights is None:
            w = {t: 1.0 / len(sel_tickers) for t in sel_tickers} if sel_tickers else {}
        else:
            raw = {t: float(weights.get(t, 0.0)) for t in sel_tickers}
            tot = sum(raw.values())
            w = {t: (raw[t] / tot if tot > 0 else 0.0) for t in sel_tickers}
        seen2: set[str] = set()
        n_new = 0
        for nm in names:
            t = nm["ticker"]
            if t in seen2:
                continue
            seen2.add(t)
            fl = nm.get("fill")
            if not fl or fl.get("blocked"):
                strat.n_blocked_slots += 1
                continue
            if t in strat.open_tickets:
                continue
            budget = C0 * strat.spec.reserve_frac * w.get(t, 0.0)
            tk = bs.Ticket(ticker=t, sleeve_day=day, entry_et=int(fl["et"]),
                           entry_px=float(fl["px"]), unit_notional=budget)
            tk.pending = {"action": "ENTER", "reason": "ENTRY",
                          "after_et": int(fl["et"]) - 1, "level": None,
                          "frac": None, "carry": False}
            strat._cash(day)
            strat.open_tickets[t] = tk
            n_new += 1
        if n_new:
            strat.filled_days += 1

    active = list(strat.open_tickets.values())

    et_set: set[int] = set()
    for tk in active:
        tkb = bars.ticker(tk.ticker)
        if tkb is None:
            continue
        for e in tkb["et"]:
            e = int(e)
            if e <= session_end:
                et_set.add(e)
    et_set.add(session_end - 1)
    et_set.add(session_end)
    ordered = sorted(e for e in et_set if e >= FIRST_ET)

    day_cashflow = 0.0
    actions_today: list[dict] = []
    deployed_sum = 0.0
    deployed_bars = 0
    reb_after = int(rebalance["checkpoint_et"]) if rebalance else None
    reb_done = False

    for t in ordered:
        # A. execute pendings
        for tk in list(active):
            if not tk.open:
                continue
            pend = tk.pending
            if pend is None:
                continue
            tkb = bars.ticker(tk.ticker)
            pos = bs._pos_of_et(tkb, t) if tkb is not None else -1
            exec_here = (pos >= 0 and (bool(pend.get("carry")) or t > int(pend["after_et"])))
            if exec_here:
                open_px = float(tkb["open"][pos])
                lvl = pend.get("level")
                px = min(open_px, lvl) if lvl is not None else open_px
                cash_before = strat._cash(tk.sleeve_day)
                action = pend["action"]
                tk.pending = None
                bs._execute(strat, tk, action, px, t, pend.get("reason", action), side,
                            frac=pend.get("frac"))
                cash_after = strat._cash(tk.sleeve_day)
                day_cashflow += cash_after - cash_before
                actions_today.append({"et": t, "ticker": tk.ticker, "action": action,
                                      "px": px, "reason": pend.get("reason", action)})
                if not tk.open:
                    continue

        # A2. rebalance at first bar after the sizing checkpoint
        if reb_after is not None and not reb_done and t > reb_after:
            _do_rebalance(strat, bars, day, t, rebalance["mult"], side)
            reb_done = True

        # B. decisions on completed bar t
        for tk in list(active):
            if not tk.open or tk.pending is not None:
                continue
            tkb = bars.ticker(tk.ticker)
            pos = bs._pos_of_et(tkb, t) if tkb is not None else -1
            bar = bs._bar(tkb, pos) if pos >= 0 else None
            if t == session_end - 1:
                bs._schedule(strat, tk, "EXIT", "FORCED_FLAT", t, None)
                continue
            if bar is None:
                continue
            fired = False
            for rule in strat.spec.release:
                act = rule.evaluate(tk, bar, pos)
                if act is not None:
                    if act["action"] == "EXIT":
                        bs._schedule(strat, tk, "EXIT", act.get("reason", rule.name), t,
                                     act.get("level"))
                    else:
                        bs._schedule(strat, tk, "REDUCE", act.get("reason", rule.name), t,
                                     act.get("level"), frac=act.get("frac", 0.5))
                    fired = True
                    break
            if not fired:
                for sc in strat.spec.scale_in:
                    act = sc.evaluate(tk, bar, pos)
                    if act is not None:
                        bs._schedule(strat, tk, "ADD", act.get("reason", sc.name), t, None,
                                     frac=act.get("frac"))
                        break
            if bar["high"] > tk.peak:
                tk.peak = bar["high"]
            tk.mfe = max(tk.mfe, bar["high"] / tk.entry_px - 1.0)
            tk.mae = min(tk.mae, bar["low"] / tk.entry_px - 1.0)
            tk.last_close = bar["close"]
            tk.last_et = t
            for H in (30, 50, 100, 200):
                if H not in tk.h_touch_et and bar["high"] >= tk.entry_px * (1 + H / 100.0):
                    tk.h_touch_et[H] = t
                    tk.reduced_before[H] = False

        dep = sum(tk.cost_open for tk in active if tk.open)
        deployed_sum += dep
        deployed_bars += 1

    # end of day marks
    mark_end_total = 0.0
    mark_prev_total = 0.0
    for tk in active:
        mark_prev_total += tk.mark
        if tk.open:
            tkb = bars.ticker(tk.ticker)
            if tkb is not None:
                in_sess = np.flatnonzero(tkb["et"] <= session_end)
                if len(in_sess):
                    tk.last_close = float(tkb["close"][in_sess[-1]])
                    tk.last_et = int(tkb["et"][in_sess[-1]])
            mark = tk.shares * (tk.last_close if tk.last_close else tk.entry_px)
        else:
            mark = 0.0
        tk.mark_prev = tk.mark
        tk.mark = mark
        mark_end_total += mark
        if tk.open:
            if tk.pending is None:
                tk.flags.append("open_no_pending")
            else:
                tk.pending["carry"] = True
                strat.n_pending += 1
                strat.n_carries += 1

    pnl = day_cashflow + (mark_end_total - mark_prev_total)
    r_day = pnl / C0
    row = {"date": day, "r_day": r_day, "pnl": pnl,
           "deployed_end": sum(tk.cash_in for tk in active if tk.open),
           "deployed_avg": (deployed_sum / deployed_bars) if deployed_bars else 0.0,
           "n_actions": len(actions_today),
           "n_open_end": sum(1 for tk in active if tk.open),
           "flags": sorted({f for tk in active for f in tk.flags})}
    strat.day_rows.append(row)
    return row


# --------------------------------------------------------------------------- #
# Cells + weights
# --------------------------------------------------------------------------- #
def build_cells() -> list[dict]:
    cells = []
    for entry in ("A_pm", "B600"):
        for rel in ("R0", "R2_10_5"):
            for scheme in SCHEMES:
                cells.append({"entry": entry, "release": rel, "scheme": scheme,
                              "bps": 100.0, "run_id": f"{entry}_N3_{rel}_{scheme}"})
                cells.append({"entry": entry, "release": rel, "scheme": scheme,
                              "bps": 0.0, "run_id": f"{entry}_N3_{rel}_{scheme}_gross"})
    return cells


def make_spec(cell: dict):
    pop, T = ENTRIES[cell["entry"]]
    rel = [bs.R0()] if cell["release"] == "R0" else [bs.R2(-10, 5)]
    return bs.StrategySpec(family_id="F9", name=cell["run_id"], entry_pop=pop, entry_T=T,
                           top_n=N_SLOTS, n_slots=N_SLOTS, reserve_frac=1.0,
                           release=rel, scale_in=[])


def weights_for(entry: str, scheme: str, names: list[dict], gate: dict) -> tuple[dict, dict | None]:
    """Return (entry_weights, rebalance_or_None) for one (day, entry, scheme)."""
    tickers = [n["ticker"] for n in names]
    N = len(tickers)

    def rn(raw: dict) -> dict:
        tot = sum(raw.values())
        if tot <= 0:
            return {t: 1.0 / len(raw) for t in raw}
        return {t: raw[t] / tot for t in raw}

    if scheme == "equal":
        return {t: 1.0 / N for t in tickers}, None
    if scheme == "rank_linear":
        raw = {n["ticker"]: N - int(n["rank"]) + 1 for n in names}
        return rn(raw), None
    if scheme == "score_gap":
        raw = {n["ticker"]: float(n.get("sel") or 0.0) for n in names}
        if sum(raw.values()) <= 0:
            raw = {t: 1.0 for t in tickers}
        return rn(raw), None
    if scheme in ("gate_G1", "gate_G2", "gate_G3"):
        gk = scheme.split("_")[1]
        raw = {t: (1.5 if gate.get(t, {}).get(gk) else 1.0) for t in tickers}
        if entry == "B600":
            return rn(raw), None                     # 585 gate is causal pre-entry
        return {t: 1.0 / N for t in tickers}, {"checkpoint_et": 585, "mult": raw}
    if scheme == "survival":
        cp = 600 if entry == "A_pm" else 615
        ddkey = "dd600" if entry == "A_pm" else "dd615"
        raw = {}
        for t in tickers:
            g = gate.get(t, {})
            passed = bool(g.get("G1") or g.get("G2") or g.get("G3"))
            dd = g.get(ddkey)
            survive = bool(passed and dd is not None and dd >= -0.10)
            raw[t] = 1.5 if survive else 1.0
        return {t: 1.0 / N for t in tickers}, {"checkpoint_et": cp, "mult": raw}
    raise ValueError(scheme)


# --------------------------------------------------------------------------- #
# Runner (day-major, load-once, pickle resume)
# --------------------------------------------------------------------------- #
def finalize(cell: dict, strat, run_dir: Path, days: list[str],
             buf_daily: dict, buf_tickets: dict) -> dict:
    bs._flush_parts(run_dir / "parts" / "daily", run_dir / "parts" / "tickets",
                    buf_daily, buf_tickets)
    term_rows = [bs._ticket_row(tk) for tk in strat.open_tickets.values()]
    if term_rows:
        bs._append_parquet(run_dir / "parts" / "tickets" / f"{days[-1][:7]}.parquet",
                           term_rows, schema="tickets")
    daily_df = bs.merge_parts(run_dir, "daily")
    tick_df = bs.merge_parts(run_dir, "tickets")
    metrics = bs.compute_metrics(strat.day_rows,
                                 strat.closed + list(strat.open_tickets.values()),
                                 len(days), strat.n_blocked_slots, strat.n_pending,
                                 strat.n_carries)
    pop, T = ENTRIES[cell["entry"]]
    cfg_obj = {
        "family_id": "F9", "run_id": cell["run_id"], "strategy": cell["run_id"],
        "entry_pop": pop, "entry_T": T, "entry_family": cell["entry"],
        "release": cell["release"], "weighting_scheme": cell["scheme"],
        "top_n": N_SLOTS, "n_slots": N_SLOTS, "reserve_frac": 1.0,
        "bps_total": cell["bps"],
        "days": [days[0], days[-1]] if days else [], "n_days": len(days),
        "seed": bs.BOOT_SEED, "git_head": bs._git_head(),
        "sim_contract_hash": bs._contract_hash(), "contract_version": "FROZEN-2026-09-22",
        "runner": "basket_f9_weights.py (weighted day-major)",
    }
    bs._atomic_write_json(run_dir / "config.json", cfg_obj)
    bs._atomic_write_json(run_dir / "metrics.json", metrics)
    bs._atomic_write_json(run_dir / "surface.json",
                          {"family_id": "F9", "cells": [cell["run_id"]]})
    summary = {"run_dir": str(run_dir), "days_run": len(days),
               "daily_rows": daily_df.height, "ticket_rows": tick_df.height,
               "daily_sha256": bs._sha256_bytes((run_dir / "daily.parquet").read_bytes()),
               "metrics": metrics}
    bs._atomic_write_json(run_dir / "run_summary.json", summary)
    return summary


def run_cells(cells: list[dict], days: list[str], force: bool) -> None:
    sem = bs.session_end_map()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    S: dict[str, dict] = {}
    n_skip = 0
    for c in cells:
        rid = c["run_id"]
        run_dir = RUNS_DIR / rid
        st_path = STATE_DIR / f"{rid}.pkl"
        if not force and st_path.exists():
            with st_path.open("rb") as fh:
                st = pickle.load(fh)
            print(f"resume {rid}: {len(st['done'])} days done", flush=True)
        elif not force and (run_dir / "run_summary.json").exists():
            try:
                if json.loads((run_dir / "run_summary.json").read_text()).get("days_run", 0) >= len(days):
                    n_skip += 1
                    continue
            except Exception:
                pass
            st = {"strat": bs.Strategy(make_spec(c)), "done": set()}
        else:
            st = {"strat": bs.Strategy(make_spec(c)), "done": set()}
        (run_dir / "parts" / "daily").mkdir(parents=True, exist_ok=True)
        (run_dir / "parts" / "tickets").mkdir(parents=True, exist_ok=True)
        st.update({"cell": c, "run_dir": run_dir, "st_path": st_path,
                   "buf_daily": {}, "buf_tickets": {}})
        S[rid] = st

    print(f"F9: {len(S)} cells active ({n_skip} skipped), {len(days)} days", flush=True)
    t0 = time.time()
    for di, day in enumerate(days):
        if all(day in st["done"] for st in S.values()):
            continue
        se = sem.get(day, bs.SESSION_END_NORMAL)
        rec = bs.load_anatomy(day)
        bars = bs.load_bars(day, se)
        gate = compute_gate_features(rec, bars)
        snap_cache: dict[str, list] = {}
        for rid, st in S.items():
            if day in st["done"]:
                continue
            cell = st["cell"]
            entry = cell["entry"]
            if entry not in snap_cache:
                pop, T = ENTRIES[entry]
                snap_cache[entry] = (bs.snapshot_of(rec, pop, T) or {}).get("names", [])[:N_SLOTS]
            names = snap_cache[entry]
            w, reb = weights_for(entry, cell["scheme"], names, gate)
            strat = st["strat"]
            prev = len(strat.closed)
            simulate_day_weighted(strat, day, rec, bars, se, cell["bps"], w, reb)
            st["buf_daily"].setdefault(day[:7], []).append(strat.day_rows[-1])
            for tk in strat.closed[prev:]:
                st["buf_tickets"].setdefault(day[:7], []).append(bs._ticket_row(tk))
            st["done"].add(day)
        if (di + 1) % 25 == 0 or di == len(days) - 1:
            for rid, st in S.items():
                bs._flush_parts(st["run_dir"] / "parts" / "daily",
                                st["run_dir"] / "parts" / "tickets",
                                st["buf_daily"], st["buf_tickets"])
                with st["st_path"].open("wb") as fh:
                    pickle.dump({"strat": st["strat"], "done": st["done"]}, fh, protocol=4)
            el = time.time() - t0
            print(f"  {di+1}/{len(days)} days, {el:.0f}s ({el/(di+1)*1000:.0f} ms/day)", flush=True)

    for rid, st in S.items():
        summ = finalize(st["cell"], st["strat"], st["run_dir"], days,
                        st["buf_daily"], st["buf_tickets"])
        if st["st_path"].exists():
            st["st_path"].unlink()
        mm = summ["metrics"]
        print(f"  {rid:<30} mean={mm['mean_basket_day']:+.4f} tick={summ['ticket_rows']}", flush=True)
    print(f"DONE {len(S)} cells in {time.time()-t0:.0f}s", flush=True)


# --------------------------------------------------------------------------- #
# Comparison / reporting
# --------------------------------------------------------------------------- #
def _block_stats(r: np.ndarray, dates: list[str]) -> dict:
    def blk(d):
        return 1 if d <= B1_END else (2 if d >= B2_START else 0)
    a = np.array(r)
    out = {}
    for b in (1, 2):
        idx = [i for i, d in enumerate(dates) if blk(d) == b]
        if not idx:
            out[f"block{b}"] = None
            continue
        aa = a[idx]
        eq = np.cumprod(1 + aa)
        pk = np.maximum.accumulate(eq)
        out[f"block{b}"] = {
            "days_n": len(aa), "mean": float(aa.mean()), "median": float(np.median(aa)),
            "std": float(aa.std(ddof=1)) if len(aa) > 1 else 0.0,
            "sum": float(aa.sum()), "worst_day": float(aa.min()),
            "positive_share": float((aa > 0).mean()),
            "compounded_growth": float(eq[-1]),
            "compounded_max_dd": float((eq / pk - 1).min()),
        }
    return out


def build_comparison(cells: list[dict], days: list[str]) -> dict:
    rows = []
    for c in cells:
        rid = c["run_id"]
        run_dir = RUNS_DIR / rid
        mpath = run_dir / "metrics.json"
        if not mpath.exists():
            continue
        metrics = json.loads(mpath.read_text())
        daily = pl.read_parquet(run_dir / "daily.parquet").sort("date")
        r = daily["r_day"].to_numpy().astype(np.float64)
        dates = daily["date"].to_list()
        blocks = _block_stats(r, dates)
        monthly = (daily.with_columns(pl.col("date").str.slice(0, 7).alias("_m"))
                   .group_by("_m").agg(pl.col("r_day").mean().alias("mean"),
                                       pl.col("r_day").sum().alias("sum"),
                                       pl.len().alias("n")).sort("_m"))
        monthly_map = {row["_m"]: {"mean": row["mean"], "sum": row["sum"], "n": row["n"]}
                       for row in monthly.to_dicts()}
        rows.append({
            "run_id": rid, "entry": c["entry"], "release": c["release"],
            "scheme": c["scheme"], "bps": c["bps"],
            "mean_basket_day": metrics["mean_basket_day"],
            "median_basket_day": metrics["median_basket_day"],
            "std_basket_day": metrics["std_basket_day"],
            "bootstrap_ci95": metrics["bootstrap_ci95"],
            "compounded_growth": metrics["compounded_growth"],
            "compounded_max_dd": metrics["compounded_max_dd"],
            "worst_day": metrics["worst_day"], "worst_week": metrics["worst_week"],
            "worst_month": metrics["worst_month"],
            "positive_day_share": metrics["positive_day_share"],
            "daily_percentiles": metrics["daily_percentiles"],
            "avg_deployed_capital": metrics["avg_deployed_capital"],
            "turnover_per_day": metrics["turnover_per_day"],
            "turnover_annualized": metrics["turnover_annualized"],
            "n_entries": metrics["n_entries"], "n_adds": metrics["n_adds"],
            "n_reduces": metrics["n_reduces"], "n_exits": metrics["n_exits"],
            "n_blocked_slots": metrics["n_blocked_slots"],
            "n_carries": metrics["n_carries"],
            "avg_failed_ticket_cost": metrics["avg_failed_ticket_cost"],
            "avg_survivor_contribution": metrics["avg_survivor_contribution"],
            "top1_day_share": metrics["top1_day_share"],
            "top5_day_share": metrics["top5_day_share"],
            "top10_day_share": metrics["top10_day_share"],
            "path_contrib": metrics["path_contrib"],
            "false_release_rate": metrics["false_release_rate"],
            "half_release_rate": metrics["half_release_rate"],
            "tail_retained": metrics["tail_retained"],
            "per_year": metrics["per_year"],
            "per_quarter": metrics["per_quarter"],
            "blocks": blocks,
            "monthly": monthly_map,
        })
    return {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "n_cells": len(rows), "n_days": len(days), "cells": rows}


# --------------------------------------------------------------------------- #
# Report writers
# --------------------------------------------------------------------------- #
def _f(v, d=4):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v:+.{d}f}" if isinstance(v, float) else str(v)


def _tbl(L, headers, rows, cols):
    L.append("| " + " | ".join(headers) + " |")
    L.append("|" + "|".join(["---"] * len(headers)) + "|")
    for r in rows:
        L.append("| " + " | ".join(str(r[c]) if not isinstance(r[c], str) or r[c] != "—" else r[c]
                                    for c in cols) + " |")


def write_report(comp: dict) -> None:
    cells = comp["cells"]
    L = ["# F9 — weighting / sizing (selection alpha vs sizing alpha)\n",
         f"Generated {comp['generated']} · {comp['n_days']} dev days · {comp['n_cells']} cells.\n"]

    # headline net@100
    L.append("## 1. Net @100bps headline (mean basket-day, full dev span)\n")
    hdr = ["entry", "release", "scheme", "mean", "median", "std", "CI95", "growth", "maxDD", "pos-day", "turnover/yr"]
    rows = []
    for c in sorted(cells, key=lambda x: (x["entry"], x["release"], x["scheme"])):
        if c["bps"] != 100:
            continue
        rows.append({"entry": c["entry"], "release": c["release"], "scheme": c["scheme"],
                     "mean": _f(c["mean_basket_day"]), "median": _f(c["median_basket_day"]),
                     "std": _f(c["std_basket_day"]),
                     "CI95": f"[{_f(c['bootstrap_ci95'][0])},{_f(c['bootstrap_ci95'][1])}]",
                     "growth": _f(c["compounded_growth"], 3), "maxDD": _f(c["compounded_max_dd"], 3),
                     "pos-day": _f(c["positive_day_share"], 3),
                     "turnover/yr": _f(c["turnover_annualized"], 2)})
    _tbl(L, hdr, rows, hdr)

    # sizing alpha vs equal
    L.append("\n## 2. Sizing alpha vs equal (same entry/release, net@100)\n")
    hdr2 = ["entry", "release", "scheme", "mean", "d_vs_equal", "gross_mean", "gross_d_vs_equal"]
    eq = {f"{c['entry']}|{c['release']}|{c['bps']}": c["mean_basket_day"]
          for c in cells if c["scheme"] == "equal"}
    rows = []
    for c in sorted(cells, key=lambda x: (x["entry"], x["release"], x["scheme"])):
        if c["bps"] != 100:
            continue
        base = eq.get(f"{c['entry']}|{c['release']}|100")
        d = (c["mean_basket_day"] - base) if base is not None else None
        g = next((x for x in cells if x["bps"] == 0 and x["entry"] == c["entry"]
                  and x["release"] == c["release"] and x["scheme"] == c["scheme"]), None)
        geq = next((x for x in cells if x["bps"] == 0 and x["scheme"] == "equal"
                    and x["entry"] == c["entry"] and x["release"] == c["release"]), None)
        rows.append({"entry": c["entry"], "release": c["release"], "scheme": c["scheme"],
                     "mean": _f(c["mean_basket_day"]), "d_vs_equal": _f(d),
                     "gross_mean": _f(g["mean_basket_day"]) if g else "—",
                     "gross_d_vs_equal": _f(g["mean_basket_day"] - geq["mean_basket_day"]) if g and geq else "—"})
    _tbl(L, hdr2, rows, hdr2)

    # dual block
    L.append("\n## 3. Dual-block stability (mean basket-day by frozen block)\n")
    hdr3 = ["entry", "release", "scheme", "b1_mean", "b2_mean", "b1_sum", "b2_sum", "b1_pos", "b2_pos", "b1_maxDD", "b2_maxDD"]
    rows = []
    for c in sorted(cells, key=lambda x: (x["entry"], x["release"], x["scheme"])):
        if c["bps"] != 100:
            continue
        b1, b2 = c["blocks"]["block1"], c["blocks"]["block2"]
        if b1 is None or b2 is None:
            continue
        rows.append({"entry": c["entry"], "release": c["release"], "scheme": c["scheme"],
                     "b1_mean": _f(b1["mean"]), "b2_mean": _f(b2["mean"]),
                     "b1_sum": _f(b1["sum"]), "b2_sum": _f(b2["sum"]),
                     "b1_pos": _f(b1["positive_share"], 3), "b2_pos": _f(b2["positive_share"], 3),
                     "b1_maxDD": _f(b1["compounded_max_dd"], 3), "b2_maxDD": _f(b2["compounded_max_dd"], 3)})
    _tbl(L, hdr3, rows, hdr3)

    # tail / concentration
    L.append("\n## 4. Tail / drawdown / concentration (net@100)\n")
    hdr4 = ["entry", "release", "scheme", "tail_ret", "path50", "path100", "top1", "top5", "top10", "failed_tick", "survivor_share"]
    rows = []
    for c in sorted(cells, key=lambda x: (x["entry"], x["release"], x["scheme"])):
        if c["bps"] != 100:
            continue
        pc = c["path_contrib"] or {}
        sv = c["avg_survivor_contribution"] or {}
        rows.append({"entry": c["entry"], "release": c["release"], "scheme": c["scheme"],
                     "tail_ret": _f((c["tail_retained"] or {}).get("mean"), 3),
                     "path50": _f(pc.get("50"), 3), "path100": _f(pc.get("100"), 3),
                     "top1": _f(c["top1_day_share"], 3), "top5": _f(c["top5_day_share"], 3),
                     "top10": _f(c["top10_day_share"], 3),
                     "failed_tick": _f(c["avg_failed_ticket_cost"], 4),
                     "survivor_share": _f(sv.get("share_of_total_net"), 3)})
    _tbl(L, hdr4, rows, hdr4)

    # yearly
    L.append("\n## 5. Per-year mean basket-day (net@100, weighted schemes only vs equal)\n")
    yrs = sorted({y for c in cells if c["bps"] == 100 for y in (c["per_year"] or {})})
    hdr5 = ["entry", "release", "scheme"] + yrs
    rows = []
    for c in sorted(cells, key=lambda x: (x["entry"], x["release"], x["scheme"])):
        if c["bps"] != 100:
            continue
        r = {"entry": c["entry"], "release": c["release"], "scheme": c["scheme"]}
        for y in yrs:
            r[y] = _f((c["per_year"] or {}).get(y, {}).get("mean"), 4)
        rows.append(r)
    _tbl(L, hdr5, rows, hdr5)

    (OUT_ROOT / "results.md").write_text("\n".join(L) + "\n")
    bs._atomic_write_json(OUT_ROOT / "comparison.json", comp)
    print(f"wrote results.md + comparison.json ({len(cells)} cells)")


def write_readme(comp: dict) -> None:
    cells = comp["cells"]
    net100 = [c for c in cells if c["bps"] == 100]
    # find best per (entry, release) and whether it beats equal robustly
    L = ["# F9 — weighting / sizing: mechanism read and verdict\n",
         "**Label: REPORT.** Deliverable for PRE-REG-BASKET-02 §3.8. All numbers read from\n"
         "`comparison.json` / `runs/*/metrics.json` (ART). Mechanism interpretation is UNVERIFIED\n"
         "dev-set reading; confirmation needs unseen months at the Phase-3 gate.\n"]
    L.append("## 1. Mechanism (economic idea)\n")
    L.append("Equal weights already tilt capital toward the phenomenon; the question is whether an\n"
             "interpretable, fixed-rule weight vector (rank, score, golden-gate, survival state) harvests\n"
             "the right tail with better EV at equal total gross. Schemes are renormalized to C0; no\n"
             "optimizer; the selected top-3 set is identical across schemes for a given entry/release.\n")
    L.append("## 2. Headline verdict (net@100, mean basket-day, Δ vs equal)\n")
    hdr = ["entry", "release", "scheme", "mean", "d_vs_equal", "b1_mean", "b2_mean", "turnover/yr"]
    eq = {f"{c['entry']}|{c['release']}": c for c in net100 if c["scheme"] == "equal"}
    rows = []
    for c in sorted(net100, key=lambda x: (x["entry"], x["release"], x["scheme"])):
        e = eq.get(f"{c['entry']}|{c['release']}")
        base = e["mean_basket_day"] if e else None
        d = (c["mean_basket_day"] - base) if base is not None else None
        b1, b2 = c["blocks"]["block1"], c["blocks"]["block2"]
        rows.append({"entry": c["entry"], "release": c["release"], "scheme": c["scheme"],
                     "mean": _f(c["mean_basket_day"]), "d_vs_equal": _f(d),
                     "b1_mean": _f(b1["mean"]) if b1 else "—", "b2_mean": _f(b2["mean"]) if b2 else "—",
                     "turnover/yr": _f(c["turnover_annualized"], 2)})
    _tbl(L, hdr, rows, hdr)
    L.append("\nRead: a scheme earns its place only if its net@100 mean exceeds equal **and** the sign of\n"
             "`d_vs_equal` agrees across both frozen blocks (block-1 2021-02..2023-12, block-2 2025-02..2026-05).\n"
             "A scheme that beats equal on one block only is **not stable**.\n")
    L.append("## 3. Cost / tail / turnover / concentration\n")
    L.append("See `results.md` §4. Dynamic schemes (gate/survival) incur rebalance turnover; whether the\n"
             "sizing alpha survives that friction is the net@100 vs gross comparison in `results.md` §2.\n")
    L.append("## 4. Interpretation guard\n")
    L.append("A disappointing sizing surface is **not** evidence against the underlying top-gainer edge: it\n"
             "only says the extra sizing complexity does not pay for itself at equal total gross. Selection\n"
             "alpha (which tickets) is a separate, already-frozen Phase-1 measurement and is untouched here.\n")
    L.append("## 5. File index\n")
    L.append("- `plan.md` — pre-registered cell list + scheme definitions (before results).\n"
             "- `comparison.json` — all cells, full metric table, blocks, monthly, yearly.\n"
             "- `results.md` — comparison tables.\n"
             "- `runs/<cell_id>/` — engine-canonical outputs (`config.json`, `daily.parquet`,\n"
             "  `tickets.parquet`, `metrics.json`, `surface.json`, `run_summary.json`).\n")
    L.append("Producer: `factory/scripts/basket_f9_weights.py`.\n")
    (OUT_ROOT / "README.md").write_text("\n".join(L) + "\n")


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
def self_test() -> None:
    fails = []
    def check(name, cond):
        if not cond:
            fails.append(name)
        print(("ok   " if cond else "FAIL ") + name)

    days = bs.dev_days()
    sem = bs.session_end_map()
    # 1. equal-weight reproduces engine simulate_day
    sample = days[::35][:12]
    maxdiff = 0.0
    for day in sample:
        rec = bs.load_anatomy(day)
        bars = bs.load_bars(day, sem[day])
        spec_a = make_spec({"entry": "A_pm", "release": "R0", "scheme": "equal",
                            "run_id": "st_a", "bps": 100.0})
        sa = bs.Strategy(spec_a)
        simulate_day_weighted(sa, day, rec, bars, sem[day], 100.0)
        spec_b = bs.StrategySpec(family_id="F9", name="st_b", entry_pop="A_pm", entry_T=570,
                                 top_n=N_SLOTS, n_slots=N_SLOTS, reserve_frac=1.0, release=[bs.R0()])
        sb = bs.Strategy(spec_b)
        bs.simulate_day(sb, day, rec, bars, sem[day], 100.0)
        d = abs(sa.day_rows[-1]["r_day"] - sb.day_rows[-1]["r_day"])
        maxdiff = max(maxdiff, d)
    check(f"equal-weight == engine (max |Δr_day|={maxdiff:.2e})", maxdiff < 1e-12)

    # 2. gate features match F2 panel (A_pm top-3, cp585)
    if PANEL_PATH.exists():
        panel = pl.read_parquet(PANEL_PATH)
        apm585 = panel.filter((pl.col("family") == "A_pm") & (pl.col("et") == 585))
        # build membership from panel columns
        def panel_gate(df):
            mfe = df["mfe_so_far"].to_numpy().astype(float)
            rpc = df["ret_from_prevclose"].to_numpy().astype(float)
            cv = df["cum_volume"].to_numpy().astype(float)
            dd = df["dd_from_running_high"].to_numpy().astype(float)
            ro9 = df["ret_from_open0930"].to_numpy().astype(float)
            rs = df["rel_strength"].to_numpy().astype(float)
            bb = df["basket_breadth_10"].to_numpy().astype(float)
            return {
                "G1": (mfe >= 0.10) & (rpc >= 0.50) & (cv >= 1e7),
                "G2": (bb >= 2) & (mfe >= 0.10) & (dd >= -0.10),
                "G3": (ro9 >= 0.10) & (dd >= -0.10) & (rs >= 0.05),
            }
        nchk = 0
        nbad = 0
        for day in sample:
            rec = bs.load_anatomy(day)
            bars = bs.load_bars(day, sem[day])
            gate = compute_gate_features(rec, bars)
            sub = apm585.filter(pl.col("date") == day).sort("rank")
            for row in sub.to_dicts():
                t = row["ticker"]
                if t not in gate:
                    continue
                pg = panel_gate(sub.filter(pl.col("ticker") == t))
                nchk += 1
                for gk in ("G1", "G2", "G3"):
                    if bool(gate[t][gk]) != bool(pg[gk][0]):
                        nbad += 1
                        if nbad <= 5:
                            print(f"    gate mismatch {day} {t} {gk}: mine={gate[t][gk]} panel={pg[gk][0]}")
        check(f"gate membership == F2 panel (A_pm top-3, {nchk} checks, {nbad} bad)", nbad == 0 and nchk > 0)

    # 3. rebalance invariants on a real day (survival scheme, no crash, cash>=0, deployed<=C0)
    rec = bs.load_anatomy(days[0])
    bars = bs.load_bars(days[0], sem[days[0]])
    gate = compute_gate_features(rec, bars)
    spec = make_spec({"entry": "A_pm", "release": "R2_10_5", "scheme": "survival",
                      "run_id": "st_s", "bps": 100.0})
    st = bs.Strategy(spec)
    names = (bs.snapshot_of(rec, "A_pm", 570) or {}).get("names", [])[:N_SLOTS]
    w, reb = weights_for("A_pm", "survival", names, gate)
    simulate_day_weighted(st, days[0], rec, bars, sem[days[0]], 100.0, w, reb)
    cash_ok = all(v >= -1e-9 for v in st.sleeve_cash.values())
    dep_ok = all(v <= bs.C0 + 1e-9 for v in st.deployed.values())
    check(f"survival rebalance invariants (cash>=0={cash_ok}, deployed<=C0={dep_ok})",
          cash_ok and dep_ok)

    if fails:
        raise AssertionError(f"self-test failures: {fails}")
    print(f"\nself-test: ALL PASS ({len(fails)} failures)")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="F9 weighting/sizing")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--smoke", action="store_true", help="run first 5 days")
    ap.add_argument("--all", action="store_true", help="run full dev span")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--max-days", type=int, default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", default=None, help="comma-separated run_ids")
    a = ap.parse_args(argv)

    cells = build_cells()
    if a.only:
        keep = {s.strip() for s in a.only.split(",")}
        cells = [c for c in cells if c["run_id"] in keep]
    if a.list:
        for c in cells:
            print(f"{c['run_id']:<30} bps={c['bps']:g}")
        print("total", len(cells))
        return 0
    if a.self_test:
        self_test()
        return 0

    days = bs.dev_days()
    if a.smoke:
        days = days[:5]
    if a.max_days:
        days = days[: a.max_days]

    if a.report_only:
        comp = build_comparison(cells, days)
        write_report(comp)
        write_readme(comp)
        return 0

    if a.smoke or a.all or a.max_days:
        run_cells(cells, days, a.force)
        comp = build_comparison(cells, days)
        write_report(comp)
        write_readme(comp)
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
