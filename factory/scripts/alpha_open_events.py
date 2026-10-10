#!/usr/bin/env python3
"""Genuinely sparse event-alpha families on the independent open-anchored SIP panel.

Three long-only event families, no threshold grid, shared causal liquidity:
  1. broadtape: broad tape thrust at a single liquidity-qualified state.
  2. pullback_reclaim: a qualified dry pullback, entered at the first later
     reclaim state within 30 clock minutes.
  3. squeeze_release: a qualified compressed range, entered at the first later
     volume release within 30 clock minutes.
A mechanical "first liquidity-qualified state per ticker/day" baseline is replayed
for context only and is never selection-eligible.

All rows are causal panel states (features use bars stamped <t); entries are
minute-open proxies. Qualifications without an entry, and days with no signal,
remain reported cash. Selection uses validation (2023) at 100bps only, is frozen
before confirmation, and is never retuned afterwards.
This is a discovery experiment, not an as-of-fills claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import polars as pl
from alpha_open_sim import CONTEXT_FEATURES, causal_liquidity, load_panel, period, replay

HORIZONS = (15, 60, 390)
PULLBACK_WINDOW_MIN = 30
ANATOMY_DROP = {"liq", "family"}
FAILED_SAMPLE = 200


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def first_state(df: pl.DataFrame) -> pl.DataFrame:
    """First panel state per (day, ticker); input must already be condition-filtered."""
    return (
        df.sort(["day", "ticker", "t"], nulls_last=True)
        .group_by(["day", "ticker"], maintain_order=True)
        .agg(pl.all().first())
    )


def bootstrap_mean_ci(values, draws: int = 20_000, seed: int = 20261008) -> dict | None:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size < 2:
        return None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, arr.size, size=(draws, arr.size))
    means = arr[idx].mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return {
        "samples": draws,
        "mean": float(arr.mean()),
        "ci95_low": float(lo),
        "ci95_high": float(hi),
    }


FAMILY_SPECS = {
    "broadtape": {
        "qual": (
            ((pl.col("peer_positive3") - (pl.col("ret3") > 0).cast(pl.Int64)) >= 3)
            & (pl.col("ret3") >= 0.03)
            & (pl.col("dd_high15") >= -0.01)
            & (pl.col("dv_accel") >= 2.0)
        ),
        "entry": None,
        "score": pl.col("ret3"),
        "qual_text": (
            "liq & other_positive_peers3>=3 & ret3>=0.03 & dd_high15>=-0.01 & dv_accel>=2"
        ),
        "entry_text": "same state; one first qualified event per ticker/day",
    },
    "pullback_reclaim": {
        "qual": (
            (pl.col("gain_open") >= 0.10)
            & (pl.col("ret15") >= 0.05)
            & (pl.col("ret3") < 0)
            & (pl.col("range5") <= 0.5 * pl.col("range15"))
        ),
        "entry": (pl.col("ret1") >= 0.005) & (pl.col("ret3") > 0),
        "score": pl.col("ret1"),
        "qual_text": "liq & gain_open>=0.10 & ret15>=0.05 & ret3<0 & range5<=0.5*range15",
        "entry_text": (
            "first later state within 30 clock minutes with ret1>=0.005 & ret3>0; "
            "one qualification/attempt per ticker/day"
        ),
    },
    "squeeze_release": {
        "qual": (
            (pl.col("gain_open") >= 0.10)
            & (pl.col("ret5").abs() <= 0.01)
            & (pl.col("range5") <= 0.5 * pl.col("range15"))
            & (pl.col("dd_day_high") >= -0.10)
        ),
        "entry": (pl.col("ret3") >= 0.02) & (pl.col("dv_accel") >= 2.0),
        "score": pl.col("ret3"),
        "qual_text": (
            "liq & gain_open>=0.10 & abs(ret5)<=0.01 & range5<=0.5*range15 & "
            "dd_day_high>=-0.10"
        ),
        "entry_text": (
            "first later state within 30 clock minutes with ret3>=0.02 & dv_accel>=2; "
            "one qualification/attempt per ticker/day"
        ),
    },
    "baseline_first_liq": {
        "qual": pl.col("liq"),
        "entry": None,
        "score": pl.lit(0.0),
        "qual_text": "causal liquidity only",
        "entry_text": "same state; context only, never selection-eligible",
    },
}


def build_family(frame: pl.DataFrame, name: str) -> dict:
    spec = FAMILY_SPECS[name]
    liq = causal_liquidity(frame).alias("liq")
    fliq = frame.with_columns(liq).with_columns(pl.lit(name).alias("family"))
    qual = first_state(fliq.filter(spec["qual"] & pl.col("liq")).sort("day", "ticker", "t"))
    if spec["entry"] is None:
        entries = qual
        failed = qual.clear()
        window = None
    else:
        window = fliq.join(
            qual.select(["day", "ticker", pl.col("t").alias("qual_t")]),
            on=["day", "ticker"],
            how="inner",
        ).filter(
            (pl.col("t") > pl.col("qual_t"))
            & (pl.col("t") <= pl.col("qual_t") + PULLBACK_WINDOW_MIN)
        )
        entries = first_state(window.filter(spec["entry"]))
        entered = entries.select("day", "ticker").unique()
        failed = qual.join(entered, on=["day", "ticker"], how="anti")
    signals = (entries if spec["entry"] is not None else qual).with_columns(
        spec["score"].alias("score")
    )
    return {
        "name": name,
        "signals": signals.sort("day", "t", "ticker"),
        "qual": qual,
        "entries": entries,
        "failed": failed,
        "window": window,
        "score_expr": spec["score"],
    }


def anatomy_of(fam: dict) -> pl.DataFrame:
    rows = []
    for role, df in (("qualification", fam["qual"]), ("entry", fam["entries"])):
        if not df.height:
            continue
        keep = [
            c
            for c in (["day", "ticker", "t", "qual_t"] + list(CONTEXT_FEATURES))
            if c in df.columns
        ]
        keep += [c for c in df.columns if c not in keep and c not in ANATOMY_DROP]
        rows.append(
            df.select(keep).with_columns(
                pl.lit(fam["name"]).alias("family"), pl.lit(role).alias("role")
            )
        )
    return pl.concat(rows, how="diagonal_relaxed") if rows else None


def period_diag(fam: dict, days: list[str]) -> dict:
    day_list = sorted(days)
    qual = fam["qual"].filter(pl.col("day").is_in(day_list))
    entries = fam["entries"].filter(pl.col("day").is_in(day_list))
    filled = entries.filter(pl.col("entry_status") == "filled_proxy").height
    diag = {
        "days_in_period": len(day_list),
        "qualifications": qual.height,
        "qualifications_unique_ticker_days": qual.select("day", "ticker").unique().height,
        "entries": entries.height,
        "entries_filled_proxy": filled,
        "entries_unfilled_expired": entries.height - filled,
        "entry_conversion": round(entries.height / qual.height, 6) if qual.height else None,
        "fill_rate_of_entries": round(filled / entries.height, 6) if entries.height else None,
        "signals_per_day": round(entries.height / len(day_list), 4) if day_list else None,
    }
    if fam["failed"].height:
        failed = fam["failed"].filter(pl.col("day").is_in(day_list))
        diag["failed_qualifications"] = failed.height
        if failed.height:
            diag["failed_qualification_features"] = (
                failed.select(
                    [
                        "day",
                        "ticker",
                        "t",
                        "gain_open",
                        "ret15",
                        "ret3",
                        "ret5",
                        "range5",
                        "range15",
                        "dd_day_high",
                        "dv_accel",
                        "log_cum_dv",
                        "peer_positive3",
                        "peer_breadth10",
                        "vwap_dist",
                    ]
                )
                .head(FAILED_SAMPLE)
                .to_dicts()
            )
            cols = [
                c
                for c in ("gain_open", "ret15", "ret3", "range5", "range15", "dv_accel")
                if c in failed.columns
            ]
            if cols:
                diag["failed_qualification_feature_medians"] = {
                    c: float(failed[c].median()) for c in cols
                }
    if fam["window"] is not None:
        w = fam["window"].filter(pl.col("day").is_in(day_list))
        diag["window_states_examined"] = w.height
        if w.height:
            diag["window_passed_entry"] = w.filter(FAMILY_SPECS[fam["name"]]["entry"]).height
    return diag


def strip(metrics: dict) -> dict:
    return {k: v for k, v in metrics.items() if k != "daily"}


def main() -> None:
    started = time.time()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--panel-root", type=Path, default=Path.home() / "alpha-data" / "open-search-v1"
    )
    ap.add_argument(
        "--out", type=Path, default=Path.home() / "alpha-data" / "open-search-v1" / "events"
    )
    ap.add_argument("--bootstrap-draws", type=int, default=20_000)
    ap.add_argument("--min-fills", type=int, default=100)
    ap.add_argument("--min-traded-days", type=int, default=50)
    args = ap.parse_args()

    frame, files = load_panel(args.panel_root)
    day_sets = {"train": [], "validation": [], "confirmation": []}
    for p in files:
        day_sets[period(p)].append(p)
    args.out.mkdir(parents=True, exist_ok=True)

    families = {name: build_family(frame, name) for name in FAMILY_SPECS}
    for fam in families.values():
        print(
            f"built {fam['name']}: signals={fam['signals'].height} "
            f"quals={fam['qual'].height} failed={fam['failed'].height}",
            flush=True,
        )

    anatomy = [a for fam in families.values() if (a := anatomy_of(fam)) is not None]
    (
        pl.concat(anatomy, how="diagonal_relaxed")
        .sort("family", "role", "day", "t", "ticker")
        .write_parquet(args.out / "event_anatomies.parquet")
    )

    sig_frames = [
        fam["signals"].with_columns(pl.lit(fam["name"]).alias("family"))
        for fam in families.values()
        if fam["signals"].height
    ]
    signals_out = pl.concat(sig_frames, how="diagonal_relaxed").sort("day", "t", "ticker")
    signals_out.write_parquet(args.out / "signals.parquet")

    diagnostics = {
        name: {p: period_diag(fam, day_sets[p]) for p in ("train", "validation", "confirmation")}
        for name, fam in families.items()
    }
    family_defs = {
        k: {"qualification": v["qual_text"], "entry": v["entry_text"]}
        for k, v in FAMILY_SPECS.items()
    }
    (args.out / "family_definitions.json").write_text(json.dumps(family_defs, indent=2) + "\n")
    (args.out / "diagnostics.json").write_text(
        json.dumps(diagnostics, indent=1, default=str) + "\n"
    )

    def run(fam: dict, horizon: int, days: list[str], cost_bps: float):
        return replay(fam["signals"], sorted(days), horizon, cost_bps)

    # ---- Validation surface: all nine alternatives at 100bps (frozen before confirmation).
    validation, train_runs = {}, {}
    for fam in families.values():
        if fam["name"] == "baseline_first_liq":
            continue
        for h in HORIZONS:
            m_tr, _ = run(fam, h, day_sets["train"], 100.0)
            m_val, t_val = run(fam, h, day_sets["validation"], 100.0)
            boot = bootstrap_mean_ci(
                [r["lower_bound_return"] for r in m_val["daily"]], args.bootstrap_draws
            )
            key = f"{fam['name']}_h{h}"
            validation[key] = {
                "family": fam["name"],
                "horizon": h,
                "cost_bps": 100,
                "period": "validation",
                "metrics": strip(m_val),
                "daily_lower_bound_bootstrap": boot,
            }
            train_runs[key] = {
                "family": fam["name"],
                "horizon": h,
                "cost_bps": 100,
                "period": "train",
                "metrics": strip(m_tr),
            }
            (args.out / f"trades_validation_{fam['name']}_h{h}_100bps.json").write_text(
                json.dumps(t_val, indent=1, default=str) + "\n"
            )

    baseline_validation = {}
    for h in HORIZONS:
        m_val, _ = run(families["baseline_first_liq"], h, day_sets["validation"], 100.0)
        baseline_validation[f"baseline_first_liq_h{h}"] = {
            "family": "baseline_first_liq",
            "horizon": h,
            "cost_bps": 100,
            "period": "validation",
            "context_only": True,
            "metrics": strip(m_val),
            "daily_lower_bound_bootstrap": bootstrap_mean_ci(
                [r["lower_bound_return"] for r in m_val["daily"]], args.bootstrap_draws
            ),
        }

    eligible = {
        k: v
        for k, v in validation.items()
        if (v["metrics"]["fills"] or 0) >= args.min_fills
        and (v["metrics"]["traded_days"] or 0) >= args.min_traded_days
    }
    pool = eligible if eligible else validation
    best_key = max(pool, key=lambda k: pool[k]["metrics"]["mean_daily_lower_bound"])
    best_lb = pool[best_key]["metrics"]["mean_daily_lower_bound"]
    no_edge = (not eligible) or best_lb <= 0

    candidate = {
        "status": "FROZEN_DIAGNOSTIC_NO_EDGE" if no_edge else "FROZEN_CANDIDATE",
        "selected_alternative": best_key,
        "selection_rule": (
            f"max validation(2023) mean daily lower-bound return at 100bps, "
            f"subject to fills>={args.min_fills} and traded_days>={args.min_traded_days}; "
            f"context baseline never eligible"
        ),
        "eligible_alternatives": sorted(eligible),
        "validation_metrics": validation[best_key]["metrics"],
        "validation_bootstrap": validation[best_key]["daily_lower_bound_bootstrap"],
        "no_edge_promotion": no_edge,
        "frozen_at_epoch": time.time(),
        "panel_root": str(args.panel_root),
    }
    (args.out / "candidate.json").write_text(json.dumps(candidate, indent=1) + "\n")

    chosen_family = validation[best_key]["family"]
    (
        signals_out.filter(pl.col("family") == chosen_family).write_parquet(
            args.out / "chosen_signals.parquet"
        )
    )

    # ---- Confirmation: chosen alternative only, 100/150/200bps, never retuned.
    chosen = families[validation[best_key]["family"]]
    horizon = validation[best_key]["horizon"]
    confirmation = {}
    for cost in (100.0, 150.0, 200.0):
        m, trades = run(chosen, horizon, day_sets["confirmation"], cost)
        confirmation[f"confirmation_{int(cost)}bps"] = {
            "alternative": best_key,
            "cost_bps": cost,
            "period": "confirmation",
            "metrics": strip(m),
            "daily_lower_bound_bootstrap": bootstrap_mean_ci(
                [r["lower_bound_return"] for r in m["daily"]], args.bootstrap_draws
            ),
        }
        (args.out / f"trades_confirmation_{best_key}_{int(cost)}bps.json").write_text(
            json.dumps(trades, indent=1, default=str) + "\n"
        )

    summary = {
        "script": Path(__file__).name,
        "script_sha256": digest(Path(__file__)),
        "alpha_open_sim_sha256": digest(Path(__file__).with_name("alpha_open_sim.py")),
        "panel_root": str(args.panel_root),
        "panel_days": len(files),
        "panel_rows": frame.height,
        "panel_days_by_period": {k: len(v) for k, v in day_sets.items()},
        "contract_sha256": digest(args.panel_root / "contract.json"),
        "runtime_s": round(time.time() - started, 1),
        "selection": candidate,
        "baseline_validation_context": baseline_validation,
        "train_validation_surface": {"train": train_runs, "validation_100bps": validation},
        "confirmation": confirmation,
        "diagnostics": diagnostics,
        "family_definitions": family_defs,
        "artifacts": sorted(p.name for p in args.out.glob("*") if p.is_file()),
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1, default=str) + "\n")

    print(
        f"panel: {len(files)} days, {frame.height} rows "
        f"(train={len(day_sets['train'])}, val={len(day_sets['validation'])}, "
        f"conf={len(day_sets['confirmation'])})"
    )
    for k, v in validation.items():
        m = v["metrics"]
        print(
            f"  val {k:34s} fills={m['fills']:5d} days={m['traded_days']:4d} "
            f"lb={m['mean_daily_lower_bound']:+.6f} win={(m['known_win_rate'] or 0):.3f} "
            f"unk={m['unknown_fills']:3d}"
        )
    print(
        f"  selected {best_key}: val lb={best_lb:+.6f}, "
        f"bootstrap95=[{validation[best_key]['daily_lower_bound_bootstrap']['ci95_low']:+.6f},"
        f"{validation[best_key]['daily_lower_bound_bootstrap']['ci95_high']:+.6f}] "
        f"edge={'no' if no_edge else 'candidate'}"
    )
    for cost in (100.0, 150.0, 200.0):
        m = confirmation[f"confirmation_{int(cost)}bps"]["metrics"]
        print(
            f"  confirm {int(cost):3d}bps fills={m['fills']:5d} days={m['traded_days']:4d} "
            f"lb={m['mean_daily_lower_bound']:+.6f} unk={m['unknown_fills']:3d} "
            f"win={(m['known_win_rate'] or 0):.3f}"
        )
    print(f"artifacts -> {args.out}")


if __name__ == "__main__":
    main()
