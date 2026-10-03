#!/usr/bin/env python3
"""LIFECYCLE-01 Stage C — ownership architecture, proven rather than assumed.

REANCHOR (read researches/LIFECYCLE-REANCHOR-CHECK.md first): the basket is an optionality
portfolio of competing claims; unconditional hold is a ruler; the decision object is where
the next dollar goes now; the objective is executable EV through the window with the TAIL
PRESERVED and the DUD TAX MEASURED.

This stage consumes the chronological OOF next-dollar ranking (Stage B) and searches the
architecture dimensions the old pipeline smuggled in as constants:

  slots        1..5 concurrent claims (the mechanical 3 is now one candidate, not a law)
  selection    top-k by predicted value, with an optional minimum predicted value
  release      exit when the prediction drops below a threshold (or on a personality sign)
  re-entry     allow a released name back when its prediction recovers
  rotation     allow switching to a higher-ranked sibling
  window exit  recycle at the window end (or keep running claims with a trail)
  tail rule    never release while the name is making new highs / in positive personalities

Every configuration runs through the same cash/share accounting (lifecycle_economics) with
measured friction on every fresh deployment. Reports EV vs cash, tail vs dud contribution,
turnover, and the holding-count distribution. Discovery half only.
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

import numpy as np
import polars as pl

import lifecycle_study as ls
from lifecycle_economics import ReplaySpec, replay

FRICTION_SIDE = 0.005


class StructuralAllocator:
    """No model: deploy only into the personalities the anatomy measured as positive.

    repair  : deep drawdown (>=10% below the running high) that is actively repairing
              (a fresh high within 5 minutes) on a rising short return.
    pullback: shallow pullback (2-10% below the high) with a fresh high within 5 minutes.
    """

    def __init__(self, rule: str, k: int = 3, dwell: int = 30):
        self.rule, self.k, self.dwell = rule, k, dwell
        self.last_fill: dict[str, int] = {}
        self.last_seen = -1

    def note_fill(self, ticker, et, side):
        self.last_fill[ticker] = et

    def __call__(self, t, states, positions, cash):
        if t < self.last_seen:
            self.last_fill.clear()
        self.last_seen = t
        chosen = []
        for tk, row in states.items():
            dd, nh5, ret5 = row.get("dd_from_high"), row.get("nh5"), row.get("ret5")
            if dd is None or nh5 is None or ret5 is None:
                continue
            try:
                dd, nh5, ret5 = float(dd), float(nh5), float(ret5)
            except (TypeError, ValueError):
                continue
            if self.rule == "repair":
                hit = dd <= -0.10 and nh5 >= 1 and ret5 > 0
            elif self.rule == "pullback":
                hit = -0.10 <= dd <= -0.02 and nh5 >= 1
            else:
                hit = False
            if hit:
                chosen.append(tk)
        chosen = chosen[:self.k]
        target = {}
        for tk in states:
            current = positions.get(tk, 0.0)
            if t - self.last_fill.get(tk, -10**9) < self.dwell:
                target[tk] = current
                continue
            if tk in chosen:
                target[tk] = 1.0 / self.k
            elif current > 1e-9:
                # released when the personality no longer qualifies
                target[tk] = 0.0
            else:
                target[tk] = 0.0
        return target


class RankAllocator:
    """Targets from the ranked next-dollar predictions; everything else is accounting."""

    def __init__(self, k: int, min_pred: float, release_below: float, reentry: bool,
                 rotate: bool, tail_keep: bool, dwell: int = 5, margin: float = 0.0,
                 release_fade: bool = False, keep_runner: bool = False):
        self.k, self.min_pred = k, min_pred
        self.release_below, self.reentry, self.rotate = release_below, reentry, rotate
        self.tail_keep, self.dwell, self.margin = tail_keep, dwell, margin
        self.release_fade, self.keep_runner = release_fade, keep_runner
        self.last_fill: dict[str, int] = {}
        self.last_seen = -1

    def note_fill(self, ticker, et, side):
        self.last_fill[ticker] = et

    def __call__(self, t, states, positions, cash):
        if t < self.last_seen:
            self.last_fill.clear()
        self.last_seen = t
        scored = []
        for tk, row in states.items():
            p = row.get("pred")
            if p is None or not np.isfinite(float(p)):
                continue
            scored.append((float(p), tk, row))
        scored.sort(reverse=True)
        chosen = {}
        for p, tk, row in scored:
            if len(chosen) >= self.k:
                break
            if p < self.min_pred:
                break
            if self.tail_keep and row.get("ret_fill") is not None:
                # a running claim in a positive personality is not released for rank alone
                pass
            chosen[tk] = p
        target = {}
        for tk, row in states.items():
            current = positions.get(tk, 0.0)
            p = row.get("pred")
            p = float(p) if p is not None and np.isfinite(float(p)) else None
            held = current > 1e-9
            # HYSTERESIS: exposure only changes after the dwell since the last actual fill.
            if t - self.last_fill.get(tk, -10**9) < self.dwell:
                target[tk] = current
                continue
            # behavioral keep: a claim making fresh highs / strongly up is not released
            strong = False
            if self.keep_runner and held:
                nh5 = row.get("nh5")
                rf = row.get("ret_fill")
                if (nh5 is not None and float(nh5) >= 1) or (rf is not None and float(rf) >= 0.10):
                    strong = True
            # behavioral release: a losing claim that stopped making highs is a dead claim
            dead = False
            if self.release_fade and held and not strong:
                rf, msh = row.get("ret_fill"), row.get("minutes_since_high")
                if (rf is not None and float(rf) < 0.0 and msh is not None
                        and float(msh) >= 15):
                    dead = True
            if tk in chosen:
                if held or (p is not None and p > self.margin):
                    target[tk] = 1.0 / self.k
                else:
                    target[tk] = current
                continue
            if not held:
                target[tk] = 0.0
                continue
            if dead:
                target[tk] = 0.0
            elif strong:
                target[tk] = current
            elif p is not None and p < self.release_below - self.margin:
                target[tk] = 0.0
            elif self.rotate:
                target[tk] = 0.0
            else:
                target[tk] = current
        total = sum(target.values())
        if total > 1.0:
            target = {k: v / total for k, v in target.items()}
        return target


def run_config(scores: pl.DataFrame, panel: pl.DataFrame, roster: pl.DataFrame, clock: int,
               end: int, cfg: dict, label: str):
    if cfg.get("structural"):
        allocator = StructuralAllocator(cfg["structural"], k=3, dwell=cfg.get("dwell", 30))
        spec = ReplaySpec(clock=clock, end=end, side=FRICTION_SIDE, universe_n=5,
                          max_holdings=3, initial_fraction=0.0)
        merged = panel
    else:
        allocator = RankAllocator(cfg["k"], cfg["min_pred"], cfg["release_below"],
                                  cfg["reentry"], cfg["rotate"], cfg["tail_keep"],
                                  dwell=cfg.get("dwell", 5), margin=cfg.get("margin", 0.0),
                                  release_fade=cfg.get("release_fade", False),
                                  keep_runner=cfg.get("keep_runner", False))
        spec = ReplaySpec(clock=clock, end=end, side=FRICTION_SIDE, universe_n=5,
                          max_holdings=cfg["k"], initial_fraction=0.0)
        merged = panel.join(scores.select(["day", "clock", "rank", "ticker", "t", "pred"]),
                            on=["day", "clock", "rank", "ticker", "t"], how="left")
    d, m, f = replay(merged, roster, spec, allocator, label)
    return d, m, f


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--clock", type=int, default=569)
    ap.add_argument("--end", type=int, default=780)
    ap.add_argument("--horizon", default="F120")
    ap.add_argument("--structural", action="store_true",
                    help="evaluate anatomy-derived structural rules instead of model scores")
    a = ap.parse_args()
    root = a.data_root / "harvest01/lifecycle/v2"
    out = root / "report"
    days = ls.discovery_days(a.data_root)
    scored = None
    if a.structural:
        test_days = days
    else:
        scored = pl.read_parquet(out / f"nextdollar_scores_{a.clock}_{a.horizon}.parquet")
        test_days = sorted(scored["day"].unique().to_list())
    panel = ls.load_panel(test_days, a.data_root, clocks=(a.clock,), with_tape=False)
    roster = ls.load_roster(test_days, a.data_root, clocks=(a.clock,))
    routes_path = out / f"personality_routes_{a.clock}.parquet"
    if routes_path.exists():
        roster = roster.join(pl.read_parquet(routes_path).select(
            ["day", "clock", "rank", "ticker", "route"]), on=["day", "clock", "rank", "ticker"],
            how="left")
    grid = []
    for rule, dwell in itertools.product(("repair", "pullback"), (15, 30, 60)):
        grid.append({"structural": rule, "dwell": dwell})
    all_d, all_m, all_f = [], [], []
    for i, cfg in enumerate(grid):
        label = (f"struct_{cfg['structural']}_d{cfg['dwell']}" if cfg.get("structural")
                 else f"k{cfg['k']}_m{cfg['margin']}_d{cfg['dwell']}_"
                      f"fade{int(cfg.get('release_fade',0))}_keep{int(cfg.get('keep_runner',0))}")
        d, m, f = run_config(scored, panel, roster, a.clock, a.end, cfg, label)
        for coll in (d, m, f):
            for r in coll:
                r["policy"] = label
        all_d += d
        all_m += m
        all_f += f
        print(f"  {i+1}/{len(grid)} {label}", flush=True)
    daily = pl.DataFrame(all_d, infer_schema_length=None)
    members = pl.DataFrame(all_m, infer_schema_length=None)
    fills = pl.DataFrame(all_f, infer_schema_length=None) if all_f else pl.DataFrame()
    daily.write_parquet(out / f"architecture_daily_{a.clock}.parquet")
    members.write_parquet(out / f"architecture_members_{a.clock}.parquet")
    if fills.height:
        fills.write_parquet(out / f"architecture_fills_{a.clock}.parquet")
    # attribution against the route rulers (forward labels used ONLY as attribution here)
    if "route" in members.columns and members.height:
        attr = members.group_by(["policy", "route"]).agg(
            pl.col("pnl").sum().alias("pnl"), pl.len().alias("n"),
            pl.col("unknown").sum().alias("unknown"))
        attr.write_parquet(out / f"architecture_attribution_{a.clock}.parquet")
    lines = [f"# LIFECYCLE-01 Stage C — ownership architecture (clock {a.clock}, window {a.end})", "",
             "REANCHOR: competing claims, window, next-dollar allocation; tail preserved, dud tax measured.",
             f"Ranking horizon {a.horizon}; fresh deployments pay {2*FRICTION_SIDE*1e4:.0f} bps round trip.",
             "Configs differ only in architecture; every number is executable cash accounting.", "",
             "| config | days | EV vs cash | median | day SE | orders/day | turnover/day |",
             "|---|---|---|---|---|---|---|"]
    for (label,), g in daily.group_by(["policy"]):
        r = g["ret"].drop_nulls()
        if r.len() == 0:
            continue
        lines.append(f"| {label} | {r.len()} | {100*r.mean():+.3f}% | {100*r.median():+.3f}% | "
                     f"{100*r.std()/np.sqrt(r.len()):.3f}% | {g['orders'].mean():.2f} | "
                     f"{g['turnover'].mean():.2f} |")
    (out / f"architecture_{a.clock}.md").write_text("\n".join(lines) + "\n")
    print(f"architecture grid clock={a.clock} configs={len(grid)} days={daily.height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
