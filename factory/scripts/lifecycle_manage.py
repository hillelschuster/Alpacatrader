#!/usr/bin/env python3
"""LIFECYCLE-01 Stage F — own early, release the dead, ride the runners.

REANCHOR: the strategy is NOT holding. It is (1) get aboard the race early — the basket is
the optionality portfolio — (2) aggressively stop financing dead claims, (3) preserve and
reinforce genuine runners, (4) sit in cash otherwise. Entry is paid ONCE at the entry clock;
every release pays one side; the window end is the forced-flat endpoint.

Release signals are personality states (all causal), never route labels:
  terminal_fade : below entry AND away from the high AND losing over 5 min (a dead claim)
  trail         : giveback >= Z% from the running high after having been up >= Y%
  either/both   : combinations

Reports, per configuration: EV vs the unconditional-hold benchmark and vs cash, the tail
contribution (monsters kept vs released), the dud tax avoided, turnover and holding counts.
Discovery half only.
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


class ManageAllocator:
    """Hold the entered claims; release on the personality signal; never re-enter."""

    def __init__(self, rule: str, dwell: int = 10, fade_msh: int = 15, fade_dd: float = 0.05,
                 trail: float = 0.20, trail_min_gain: float = 0.10):
        self.rule, self.dwell = rule, dwell
        self.fade_msh, self.fade_dd = fade_msh, fade_dd
        self.trail, self.trail_min_gain = trail, trail_min_gain
        self.last_fill: dict[str, int] = {}
        self.last_seen = -1

    def note_fill(self, ticker, et, side):
        self.last_fill[ticker] = et

    def __call__(self, t, states, positions, cash):
        if t < self.last_seen:
            self.last_fill.clear()
        self.last_seen = t
        target = {}
        for tk, row in states.items():
            current = positions.get(tk, 0.0)
            if current <= 1e-9:
                target[tk] = 0.0
                continue
            if t - self.last_fill.get(tk, -10**9) < self.dwell:
                target[tk] = current
                continue
            try:
                rf = float(row.get("ret_fill"))
                dd = float(row.get("dd_from_high"))
                msh = float(row.get("minutes_since_high"))
                r5 = float(row.get("ret5"))
                pg = float(row.get("peak_gain"))
            except (TypeError, ValueError):
                target[tk] = current
                continue
            dead = (rf < 0.0 and msh >= self.fade_msh and dd <= -self.fade_dd and r5 < 0.0)
            trail_hit = (pg >= self.trail_min_gain and dd <= -self.trail)
            release = False
            if self.rule == "fade":
                release = dead
            elif self.rule == "trail":
                release = trail_hit
            elif self.rule == "both":
                release = dead or trail_hit
            elif self.rule == "both_soft":
                release = dead and trail_hit
            target[tk] = 0.0 if release else current
        return target


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--clocks", default="540,560,569,571")
    ap.add_argument("--end", type=int, default=780)
    a = ap.parse_args()
    root = a.data_root / "harvest01/lifecycle/v2"
    out = root / "report"
    days = ls.discovery_days(a.data_root)
    all_d, all_m, all_f = [], [], []
    configs = []
    for rule, k, fade_msh in itertools.product(("hold", "fade", "trail", "both"), (3, 5), (15, 30)):
        if rule == "hold" and fade_msh != 15:
            continue
        configs.append({"rule": rule, "k": k, "fade_msh": fade_msh})
    for clock in (int(x) for x in a.clocks.split(",")):
        panel = ls.load_panel(days, a.data_root, clocks=(clock,))
        roster = ls.load_roster(days, a.data_root, clocks=(clock,))
        routes_path = out / f"personality_routes_{clock}.parquet"
        if routes_path.exists():
            roster = roster.join(pl.read_parquet(routes_path).select(
                ["day", "clock", "rank", "ticker", "route", "mfe", "captured"]),
                on=["day", "clock", "rank", "ticker"], how="left")
        for cfg in configs:
            label = f"{cfg['rule']}_k{cfg['k']}_msh{cfg['fade_msh']}"
            alloc = ManageAllocator(cfg["rule"], dwell=10, fade_msh=cfg["fade_msh"])
            spec = ReplaySpec(clock=clock, end=a.end, side=FRICTION_SIDE,
                              universe_n=cfg["k"], max_holdings=cfg["k"],
                              initial_fraction=1.0)
            d, m, f = replay(panel, roster, spec, alloc, label)
            for coll in (d, m, f):
                for r in coll:
                    r["clock"] = clock
                    r["policy"] = label
            all_d += d
            all_m += m
            all_f += f
            print(f"  clock {clock} {label}", flush=True)
    daily = pl.DataFrame(all_d, infer_schema_length=None)
    members = pl.DataFrame(all_m, infer_schema_length=None)
    fills = pl.DataFrame(all_f, infer_schema_length=None) if all_f else pl.DataFrame()
    daily.write_parquet(out / "manage_daily.parquet")
    members.write_parquet(out / "manage_members.parquet")
    if fills.height:
        fills.write_parquet(out / "manage_fills.parquet")
    lines = ["# LIFECYCLE-01 Stage F — own early, release the dead, ride the runners", "",
             "REANCHOR: entry paid once; releases pay one side; hold is only the benchmark.",
             "fade = below entry + away from high + losing 5 m; trail = giveback from peak;",
             "both = either. Window end forces flat.", "",
             "| clock | policy | days | EV | median | day SE | orders/day | turnover/day |",
             "|---|---|---|---|---|---|---|---|"]
    for (clock, policy), g in daily.group_by(["clock", "policy"]):
        r = g["ret"].drop_nulls()
        if r.len() == 0:
            continue
        lines.append(f"| {clock} | {policy} | {r.len()} | {100*r.mean():+.3f}% | "
                     f"{100*r.median():+.3f}% | {100*r.std()/np.sqrt(r.len()):.3f}% | "
                     f"{g['orders'].mean():.2f} | {g['turnover'].mean():.2f} |")
    (out / "manage.md").write_text("\n".join(lines) + "\n")
    # tail preservation / dud tax attribution where routes are known
    if "route" in members.columns:
        att = (members.filter(pl.col("pnl").is_finite())
               .with_columns(pl.when(pl.col("route").is_in(["monster", "second_leg_runner",
                                                            "sustained_runner"]))
                             .then(pl.lit("money_routes")).otherwise(pl.lit("tax_routes")).alias("side"))
               .group_by(["clock", "policy", "side"]).agg(
                   pl.col("pnl").sum().alias("pnl"), pl.len().alias("n")))
        att.write_parquet(out / "manage_attribution.parquet")
    print(f"manage grid done rows={daily.height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
