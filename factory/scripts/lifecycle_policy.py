#!/usr/bin/env python3
# SUPERSEDED 2026-10-02 (owner reanchor): this module drives an "EV table / LightGBM ->
# hold-vs-release" policy with a mechanical 3-slot cap. That framing is cancelled. The
# replay/accounting primitives live in lifecycle_economics.py and survive; this file is
# kept as evidence only and must not be run to select, freeze, or validate anything.
# Read researches/LIFECYCLE-REANCHOR.md first.
"""LIFECYCLE-01 action economics: RELEASE / RETAIN / RE-ENTER / CASH, never a session hold.

The thesis is a WINDOW of strength: the strategy is a policy over when to release dead claims,
when to retain (or re-enter) a name the window is still paying, and when to sit in cash.
Unconditional "hold" appears ONLY as a benchmark ruler, never as a strategy.

An interpretable state-value table and chronological tree forecasts share exactly the
same cash/position replay. The table measures next-leg value in drawdown/recovery,
high-cadence, short momentum and relative-volume coordinates. No winner-per-day rule.
Discovery chooses among retaining optionality, keeping reserves and cash-first entry.
The validation command reads a frozen spec and never fits or changes anything.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import bisect

import numpy as np
import polars as pl

import lifecycle_study as ls
from lifecycle_economics import ReplaySpec, replay
from lifecycle_models import TARGETS, score as model_score
from lifecycle_results import report, summary

STATE_COORDS = ("dd_from_high", "minutes_since_high", "recovery_from_low", "ret5", "rel_dvol5")


def digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def cell_frame(df: pl.DataFrame, cuts: dict) -> pl.DataFrame:
    keys = []
    for column, values in cuts.items():
        x = pl.col(column)
        ex = pl.when(~x.is_finite() | x.is_null()).then(pl.lit("missing"))
        for i, boundary in enumerate(values):
            ex = ex.when(x <= boundary).then(pl.lit(str(i)))
        ex = ex.otherwise(pl.lit(str(len(values))))
        keys.append(ex)
    return df.with_columns(pl.concat_str(keys, separator="|").alias("state_cell"))


def fit_state_table(df: pl.DataFrame) -> dict:
    d = df.filter(pl.col("filled_asof") & (pl.col("rank") <= 3))
    cuts = {}
    for col in STATE_COORDS:
        values = d[col].filter(d[col].is_finite())
        if values.len():
            cuts[col] = sorted(set(float(values.quantile(q)) for q in (1/3, 2/3)))
    cells = cell_frame(d, cuts)
    table = cells.select("state_cell").unique()
    for target in TARGETS:
        s = cells.filter(pl.col(target).is_finite()).group_by(["state_cell", "day"]).agg(
            pl.col(target).mean().alias("day_value"))
        g = s.group_by("state_cell").agg(
            pl.col("day_value").mean().alias(f"mean_{target}"),
            pl.col("day_value").std().alias(f"std_{target}"), pl.len().alias(f"days_{target}"))
        table = table.join(g, on="state_cell", how="left")
    return {"coords": list(cuts), "cuts": cuts, "table": table.to_dicts(),
            "weighting": "equal day within each state cell; minute overlap not independent",
            "minimum_days": 30, "definition": "gross executable hold/sell-1 over5/15/30m",
            "clock_scope": sorted(d["clock"].unique().to_list())}


def state_score(df: pl.DataFrame, config: dict) -> pl.DataFrame:
    d = cell_frame(df, config["cuts"])
    tab = pl.DataFrame(config["table"], infer_schema_length=None)
    d = d.join(tab, on="state_cell", how="left")
    for target in TARGETS:
        mean, std, count = f"mean_{target}", f"std_{target}", f"days_{target}"
        # One calendar-day standard error is an uncertainty deduction, not a claim of alpha.
        val = pl.col(mean) - pl.col(std).fill_null(0) / pl.col(count).sqrt()
        d = d.with_columns(pl.when(pl.col(count) >= config["minimum_days"])
                           .then(val).otherwise(0.0).alias(f"ev_{target}"))
    return d


class Allocator:
    def __init__(self, style: str, threshold: float, minimum_dwell: int):
        self.style, self.threshold, self.minimum_dwell = style, threshold, minimum_dwell
        self.last_change: dict[str, int] = {}
        self.last_seen = -1

    def note_fill(self, ticker: str, et: int, side: str):
        """Dwell starts when exposure actually changes, never on unfilled intent."""
        self.last_change[ticker] = et

    def __call__(self, t, states, positions, cash):
        if t < self.last_seen:
            self.last_change.clear()
        self.last_seen = t
        target = {}
        for ticker, row in states.items():
            current = positions.get(ticker, 0.0)
            if not row.get("filled_asof"):
                target[ticker] = current
                continue
            values = [row.get(f"ev_{h}") for h in TARGETS]
            valid = [float(v) for v in values if v is not None and np.isfinite(v)]
            if not valid:
                target[ticker] = current
                continue
            opportunity = max(valid)
            exhausted = max(valid) < 0
            if t - self.last_change.get(ticker, -100000) < self.minimum_dwell:
                target[ticker] = current
                continue
            if self.style == "retain":
                # Conditional retention while the window still pays; release when it stops.
                # Selling does not automatically imply a new entry elsewhere.
                desired = 0.0 if exhausted else current
            else:
                if opportunity > self.threshold:
                    desired = min(2/3, max(1/3, opportunity / (3*self.threshold)))
                elif exhausted:
                    desired = 0.0
                else:
                    desired = current
            target[ticker] = desired
        return target


def baseline(panel, roster, clock, end, side):
    d, m, f = replay(panel, roster, ReplaySpec(clock=clock, end=end, side=side),
                     label="benchmark_unconditional_hold")
    c, cm, cf = replay(panel, roster, ReplaySpec(clock=clock, end=end, side=side, initial_fraction=0), label="cash")
    return d+c, m+cm, f+cf


def oof_state(df: pl.DataFrame, days: list[str]) -> tuple[pl.DataFrame, dict]:
    out = []
    for start, stop in ((200,311), (311,422), (422,533)):
        config = fit_state_table(df.filter(pl.col("day").is_in(days[:start])))
        out.append(state_score(df.filter(pl.col("day").is_in(days[start:stop])), config))
    return pl.concat(out, how="diagonal_relaxed"), fit_state_table(df)


def discover(root: Path, data_root: Path, clock: int, end: int):
    split = ls.load_split(data_root)
    days = split["discovery_days"]
    # Behavior map is a prerequisite, not an after-the-fact story for a fitted model.
    anatomy = root / "report" / f"anatomy_pullback_discovery_{clock}.parquet"
    if not anatomy.exists():
        raise SystemExit(f"behavior anatomy must precede action selection: {anatomy}")
    evidence = root / "report" / "anatomy_evidence_discovery.json"
    if not evidence.exists():
        raise SystemExit(f"behavior anatomy provenance missing: {evidence}")
    prov = json.loads(evidence.read_text()).get("provenance", {})
    if not prov.get("rule_first_eligible"):
        raise SystemExit("action selection requires a FULL discovery-half anatomy run, "
                         "not a --limit smoke (rule_first_eligible=false)")
    frame = ls.load_panel(days, data_root, clocks=(clock,))
    roster = ls.load_roster(days, data_root, clocks=(clock,))
    test_days = days[200:]
    state_oof, state_config = oof_state(frame, days)
    # Store the final transparent map, but use prefix-fitted maps for discovery replay.
    out = root / "policies" / str(clock)
    out.mkdir(parents=True, exist_ok=True)
    (out / "state_map.json").write_text(json.dumps(state_config, indent=2, allow_nan=False)+"\n")
    oof_model = pl.read_parquet(root / "models" / "oof.parquet").filter(pl.col("clock") == clock)
    model_config = json.loads((root / "models" / "model_config.json").read_text())
    base = frame.filter(pl.col("day").is_in(test_days))
    ros_test = roster.filter(pl.col("day").is_in(test_days))
    scored = {"state": state_oof}
    keys = ["day", "clock", "rank", "ticker", "t"]
    for family in ("price", "full"):
        pred = oof_model.filter(pl.col("family") == family).select(keys+[f"pred_{h}" for h in TARGETS])
        x = base.join(pred, on=keys, how="left")
        # Honest OOF policy scores: predictions from prefix models, no future-fold
        # outcome calibration. The same raw expected-value units are frozen for test.
        x = x.with_columns([pl.col(f"pred_{h}").alias(f"ev_{h}") for h in TARGETS])
        scored[family] = x
    all_d, all_m, all_f = baseline(base, ros_test, clock, end, .005)
    hd, hm, hf = replay(base, ros_test, ReplaySpec(clock=clock, end=end, side=.005,
                       universe_n=5, max_holdings=3), label="benchmark_unconditional_hold5cap3")
    all_d += hd
    all_m += hm
    all_f += hf
    candidates = []
    day_rets: dict[str, pl.DataFrame] = {}
    for family, data in scored.items():
        for universe_n in (3, 5):
            for style, initial in (("retain", 1.0), ("reserve", 1/3), ("cash_first", 0.0)):
                label = f"{family}_{style}_n{universe_n}"
                allocator = Allocator(style, threshold=.01, minimum_dwell=5)
                spec = ReplaySpec(clock=clock, end=end, side=.005, initial_fraction=initial,
                                  universe_n=universe_n, max_holdings=3)
                d, m, f = replay(data, ros_test, spec, allocator, label)
                all_d += d
                all_m += m
                all_f += f
                day_rets[label] = pl.DataFrame(d).select(["day", "ret"])
                candidates.append({"family": family, "style": style, "label": label,
                                   "universe_n": universe_n,
                                   "initial_fraction": initial, "threshold": .01,
                                   "minimum_dwell": 5, "mean": None, "days": 0})
    report(all_d, all_m, all_f, out, "discovery_oof")
    # Fair comparison: every candidate is scored on the SAME days where all are known.
    common = None
    for label, frame in day_rets.items():
        known = set(frame.filter(pl.col("ret").is_finite())["day"].to_list())
        common = known if common is None else (common & known)
    common = common or set()
    for c in candidates:
        frame = day_rets[c["label"]].filter(pl.col("day").is_in(sorted(common)))
        values = frame["ret"].drop_nulls()
        c["mean"] = float(values.mean()) if values.len() else None
        c["days"] = values.len()
    (out / "candidate_days.parquet").write_parquet(
        pl.concat([f.with_columns(pl.lit(label).alias("policy")) for label, f in day_rets.items()]))
    known = [c for c in candidates if c["days"] >= 300 and c["mean"] is not None]
    winner = max(known, key=lambda c: c["mean"]) if known else None
    cash_best = winner is None or winner["mean"] <= 0
    cfg = {"clock": clock, "end": end, "universe_n": 3, "max_holdings": 3,
           "main_friction_side": .005, "stress_sides": [0.005,0.0075],
           "candidate_count": len(candidates), "candidates": candidates,
           "selected": {"label": "cash", "initial_fraction": 0.0} if cash_best else winner,
           "best_risky": winner, "state_map_sha256": digest(out / "state_map.json"),
           "model_config_sha256": digest(root / "models" / "model_config.json"),
           "basis": "333 expanding chronological out-of-fold discovery days; cash included",
           "caution": "action specifications chosen on discovery; OOF predictions alone are not validation",
           "coverage": "tape models are matched-coverage crosschecks, not universal promoted policies"}
    (out / "action_spec.json").write_text(json.dumps(cfg, indent=2, allow_nan=False)+"\n")
    return cfg


def validate(root: Path, data_root: Path, spec_path: Path):
    from lifecycle_freeze import verify as verify_freeze
    freeze = verify_freeze(root)
    if freeze["action_specs"].get(str(spec_path)) != digest(spec_path):
        raise ValueError("action pipeline changed after freeze")
    config = json.loads(spec_path.read_text())
    clock, end = config["clock"], config["end"]
    days = ls.validation_days(data_root, allow=True)
    df = ls.load_panel(days, data_root, clocks=(clock,))
    ros = ls.load_roster(days, data_root, clocks=(clock,))
    model_path = root / "models" / "model_config.json"
    if digest(model_path) != freeze["model_config_sha256"]:
        raise ValueError("frozen models/config changed")
    models = json.loads(model_path.read_text())
    state_path = spec_path.parent / "state_map.json"
    if digest(state_path) != config["state_map_sha256"]:
        raise ValueError("frozen interpretable map changed")
    state_cfg = json.loads(state_path.read_text())
    out_d, out_m, out_f = [], [], []
    for side in config["stress_sides"]:
        d,m,f = baseline(df, ros, clock, end, side)
        hd, hm, hf = replay(df, ros, ReplaySpec(clock=clock, end=end, side=side,
                           universe_n=5, max_holdings=3), label="benchmark_unconditional_hold5cap3")
        d += hd
        m += hm
        f += hf
        label_suffix = f"_{round(2*side*10000)}bps"
        for collection in (d,m,f):
            for r in collection:
                r["policy"] += label_suffix
        out_d += d;out_m += m;out_f += f
        # All declared candidates are run unchanged; never pick a winner from test.
        for c in config["candidates"]:
            if c["family"] == "state":
                scored = state_score(df, state_cfg)
            else:
                scored = model_score(df, models, c["family"])
                scored = scored.with_columns([pl.col(f"pred_{h}").alias(f"ev_{h}") for h in TARGETS])
            allocator = Allocator(c["style"], c["threshold"], c["minimum_dwell"])
            spec = ReplaySpec(clock=clock, end=end, side=side, initial_fraction=c["initial_fraction"],
                              universe_n=c["universe_n"], max_holdings=3)
            d,m,f = replay(scored, ros, spec, allocator, c["label"]+label_suffix)
            out_d += d;out_m += m;out_f += f
    return report(out_d,out_m,out_f,spec_path.parent,"validation_frozen")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=("discover","validate"), required=True)
    ap.add_argument("--data-root",type=Path,default=Path("/home/hillel/projects/Alpacatrader/data"))
    ap.add_argument("--clock",type=int,default=560)
    ap.add_argument("--end",type=int,required=True)
    a = ap.parse_args()
    root = a.data_root / "harvest01/lifecycle/v2"
    if a.stage == "discover":
        cfg = discover(root,a.data_root,a.clock,a.end)
        print(json.dumps({"selected":cfg["selected"],"best_risky":cfg["best_risky"]},indent=2))
    else:
        validate(root,a.data_root,root/"policies"/str(a.clock)/"action_spec.json")
        print("frozen chronological policy evaluation complete; no refitting")


if __name__ == "__main__":
    main()
