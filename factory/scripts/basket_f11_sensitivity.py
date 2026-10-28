#!/usr/bin/env python
"""F11 sensitivity — friction ladder, execution overlay, and capacity curves.

Reuses the canonical 100-bps F1 cells (identity-checked) and runs only the missing
friction levels {0, 50, 150, 200} through the canonical engine in a single day-major
pass (single process, incremental/resume).  It then computes the explicit symmetric
execution overlay from the pilot and bounds deployable notional via
participation-of-daily-dollar-volume.

Measurement only.  No engine edit, no git, no new deps, no holdout access.
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from pathlib import Path

import polars as pl

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))

import basket_sim as bs  # noqa: E402
import basket_f1_multi as multi  # noqa: E402

F1_ROOT = ROOT / "factory" / "artifacts" / "basket" / "phase2" / "F1_primitive" / "runs"
F11_EXEC = ROOT / "factory" / "artifacts" / "basket" / "phase2" / "F11_exec"
PARTICIPATIONS = (0.01, 0.02, 0.05)
MISSING_BPS = (0, 50, 150, 200)
ALL_BPS = (0, 50, 100, 150, 200)

# (run_id, family, entry-key, exit-key, release name, pop, T)
CELLS = [
    ("A_pm_N3_R2_10_5", "f1_A_pm", "A_pm", "R2_10_5", "R2(-10,5)", "A_pm", 570),
    ("A_pm31_N3_R2_10_5", "f1_A_pm31", "A_pm31", "R2_10_5", "R2(-10,5)", "A_pm31", 570),
    ("B600_N3_R2_10_5", "f1_B600", "B600", "R2_10_5", "R2(-10,5)", "B", 600),
    ("A_pm_N3_R0", "f1_A_pm", "A_pm", "R0", "R0", "A_pm", 570),
]


def daily_capacity(rth_dollar_volumes, slots: int, participation: float) -> float:
    """Equal-slot total-notional capacity = N * min_i(p * dv_i). Empty -> 0."""
    vols = [v for v in rth_dollar_volumes if v is not None and v > 0]
    if not vols:
        return 0.0
    return float(slots) * float(participation) * min(vols)


def _friction_cells() -> list[dict]:
    cells: list[dict] = []
    for run_id, _fam, entry, exit_key, _rel, _pop, _T in CELLS:
        for bps in MISSING_BPS:
            cells.append(dict(run_id=f"{run_id}_bps{int(bps)}", entry=entry, exit=exit_key,
                              n=3, bps=float(bps), tier="f11"))
    return cells


def run_friction(cells: list[dict], out_root: Path, state_dir: Path,
                 max_days: int | None) -> None:
    """Single day-major pass over all friction cells (canonical engine, resume-safe)."""
    days = bs.dev_days()
    if max_days:
        days = days[:max_days]
    sem = bs.session_end_map()
    state_dir.mkdir(parents=True, exist_ok=True)

    S: dict[str, dict] = {}
    for c in cells:
        rid = c["run_id"]
        run_dir = out_root / f"f1_{c['entry']}" / rid
        st_path = state_dir / f"{rid}.pkl"
        if st_path.exists():
            st = pickle.loads(st_path.read_bytes())
            print(f"resume {rid}: {len(st['done'])} days done", flush=True)
        else:
            st = {"strat": bs.Strategy(multi.make_spec(c)), "done": set(), "wall": 0.0}
        (run_dir / "parts" / "daily").mkdir(parents=True, exist_ok=True)
        (run_dir / "parts" / "tickets").mkdir(parents=True, exist_ok=True)
        st.update({"cell": c, "run_dir": run_dir, "state_path": st_path,
                   "buf_daily": {}, "buf_tickets": {}, "started": time.time()})
        S[rid] = st

    t0 = time.time()
    for i, day in enumerate(days):
        if all(day in st["done"] for st in S.values()):
            continue
        se = sem.get(day, bs.SESSION_END_NORMAL)
        rec = bs.load_anatomy(day)
        bars = bs.load_bars(day, se)
        m = day[:7]
        for rid, st in S.items():
            if day in st["done"]:
                continue
            strat = st["strat"]
            prev = len(strat.closed)
            row = bs.simulate_day(strat, day, rec, bars, se, st["cell"]["bps"])
            st["buf_daily"].setdefault(m, []).append(row)
            for tk in strat.closed[prev:]:
                st["buf_tickets"].setdefault(m, []).append(bs._ticket_row(tk))
            st["done"].add(day)
        if (i + 1) % 25 == 0 or day == days[-1]:
            for st in S.values():
                bs._flush_parts(st["run_dir"] / "parts" / "daily",
                                st["run_dir"] / "parts" / "tickets",
                                st["buf_daily"], st["buf_tickets"])
                st["wall"] = time.time() - st["started"]
                st["state_path"].write_bytes(pickle.dumps(
                    {"strat": st["strat"], "done": st["done"], "wall": st["wall"]},
                    protocol=4))
            print(f"  {i+1}/{len(days)} days, {time.time()-t0:.0f}s", flush=True)

    for rid, st in S.items():
        st["wall"] = time.time() - st["started"]
        summ = multi.finalize(st["cell"], st, st["strat"], st["run_dir"], days,
                              out_root, st["state_path"])
        mm = summ["metrics"]
        print(f"  {rid:<28} mean={mm['mean_basket_day']:+.4f} "
              f"med={mm['median_basket_day']:+.4f} tick={summ['ticket_rows']}", flush=True)
    print(f"friction pass done: {len(cells)} cells in {time.time()-t0:.0f}s", flush=True)


def _overlay(pilot_entries: Path) -> dict:
    df = pl.read_parquet(pilot_entries)
    sub = df.filter(~pl.col("blocked") & pl.col("spread_bps").is_not_null()
                    & pl.col("next_print_slippage_bps").is_not_null())
    if sub.height == 0:
        return {"n_entries": 0, "one_way_adder_bps": None, "round_trip_overlay_bps": None,
                "note": "no pilot entries with both spread and next print"}
    half = float(sub["half_spread_bps"].median())
    adv = max(0.0, float(sub["next_print_slippage_bps"].median()))
    one_way = half + adv
    return {"n_entries": int(sub.height), "median_half_spread_bps": half,
            "median_adverse_next_print_slippage_bps": adv,
            "one_way_adder_bps": one_way, "round_trip_overlay_bps": 2.0 * one_way}


def _read_metrics(run_dir: Path) -> dict:
    return json.loads((run_dir / "metrics.json").read_text())


def _read_config(run_dir: Path) -> dict:
    return json.loads((run_dir / "config.json").read_text())


def build_friction_curve(pilot_entries: Path, runs_root: Path, out: Path) -> dict:
    contract_hash = bs._contract_hash()
    git_head = bs._git_head()
    curve: dict = {"evidence_label": "RUN", "contract_hash": contract_hash,
                   "git_head": git_head, "architectures": {},
                   "overlay": _overlay(pilot_entries),
                   "overlay_note": ("symmetric, exit-side symmetry assumed; spread and "
                                    "next-print restricted to entries with both measures")}
    for run_id, family, _entry, _exit, release_name, _pop, _T in CELLS:
        points = {}
        for bps in ALL_BPS:
            if bps == 100:
                run_dir = F1_ROOT / family / run_id
                source = "reused_F1_100bps"
            else:
                run_dir = runs_root / family / f"{run_id}_bps{int(bps)}"
                source = "F11_run"
            cfg = _read_config(run_dir)
            identity_ok = (cfg.get("top_n") == 3 and cfg.get("n_slots") == 3
                           and cfg.get("n_days") == 1066
                           and cfg.get("sim_contract_hash") == contract_hash
                           and cfg.get("git_head") == git_head
                           and cfg.get("release") == [release_name])
            m = _read_metrics(run_dir)
            points[str(int(bps))] = {
                "mean_basket_day": m["mean_basket_day"],
                "median_basket_day": m["median_basket_day"],
                "compounded_growth": m["compounded_growth"],
                "positive_day_share": m["positive_day_share"],
                "source": source,
                "config_identity_ok": identity_ok,
            }
        base = points["0"]["mean_basket_day"]
        curve["architectures"][run_id] = {
            "bps": points,
            "friction_decay": {
                "bps0_to_200_mean_drop": base - points["200"]["mean_basket_day"],
                "bps0_to_100_mean_drop": base - points["100"]["mean_basket_day"],
            },
        }
    (out / "friction_curve.json").write_text(json.dumps(curve, indent=1))
    return curve


def _entry_dvs(cell_run_id: str, family: str, bar_cache: dict,
               sem: dict[str, int]) -> dict[str, list[float]]:
    tk = pl.read_parquet(F1_ROOT / family / cell_run_id / "tickets.parquet",
                         columns=["ticker", "sleeve_day"])
    per_day: dict[str, list[float]] = {}
    for day, ticker in zip(tk["sleeve_day"].to_list(), tk["ticker"].to_list()):
        if day not in bar_cache:
            df = pl.read_parquet(bs.BARS_DIR / f"{day}.parquet",
                                 columns=["ticker", "et", "close", "volume"])
            df = df.filter(pl.col("et") <= sem.get(day, bs.SESSION_END_NORMAL))
            bar_cache[day] = df
        tb = bar_cache[day].filter(pl.col("ticker") == ticker)
        dv = float((tb["close"] * tb["volume"]).sum()) if tb.height else 0.0
        per_day.setdefault(day, []).append(dv)
    return per_day


def _capacity_stats(per_day: dict[str, list[float]], p: float, n_slots: int) -> dict:
    import numpy as np
    caps = [daily_capacity(dvs, n_slots, p) for dvs in per_day.values() if dvs]
    a = np.asarray(caps, dtype=np.float64)
    return {"n_basket_days": int(len(a)), "mean": float(a.mean()) if len(a) else 0.0,
            **{f"p{q}": float(np.percentile(a, q)) for q in (10, 25, 50, 75, 90)}}


def _pilot_capacity(pilot_entries: Path, dv_field: str) -> dict:
    df = pl.read_parquet(pilot_entries).filter(~pl.col("blocked"))
    out = {}
    for run_id, *_ in CELLS:
        sub = df.filter(pl.col("architecture") == run_id)
        per_day: dict[str, list[float]] = {}
        for day, dv in zip(sub["day"].to_list(), sub[dv_field].to_list()):
            per_day.setdefault(day, []).append(float(dv))
        out[run_id] = {f"p{int(p*100)}": _capacity_stats(per_day, p, 3)
                       for p in PARTICIPATIONS}
    return out


def build_capacity_curve(pilot_entries: Path, out: Path) -> dict:
    sem = bs.session_end_map()
    bar_cache: dict[str, pl.DataFrame] = {}
    result: dict = {"evidence_label": "REPORT",
                    "method": "equal-slot capacity = N * min_i(p * daily dollar volume); "
                              "blocked/empty basket = 0; retrospective proxy, not a market-impact model",
                    "full_span_bar_proxy": {}, "pilot_raw_trade": {}, "pilot_bar_proxy": {}}
    for run_id, family, *_ in CELLS:
        per_day = _entry_dvs(run_id, family, bar_cache, sem)
        result["full_span_bar_proxy"][run_id] = {
            f"p{int(p*100)}": _capacity_stats(per_day, p, 3) for p in PARTICIPATIONS}
    result["pilot_raw_trade"] = _pilot_capacity(pilot_entries, "raw_rth_dv")
    result["pilot_bar_proxy"] = _pilot_capacity(pilot_entries, "bar_dv_proxy")
    (out / "capacity_curve.json").write_text(json.dumps(result, indent=1))
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(F11_EXEC))
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--friction-only", action="store_true")
    ap.add_argument("--capacity-only", action="store_true")
    ap.add_argument("--max-days", type=int, default=None)
    a = ap.parse_args(argv)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pilot_entries = out / "pilot_entries.parquet"
    root = out / "smoke" / "runs" if a.smoke else out / "runs"
    state = out / "smoke" / "_state" if a.smoke else out / "_state"
    max_days = 5 if a.smoke else a.max_days

    if a.friction_only or not a.capacity_only:
        run_friction(_friction_cells(), root, state, max_days)
    if a.capacity_only:
        build_capacity_curve(pilot_entries, out)
        return 0
    if not a.smoke:
        build_friction_curve(pilot_entries, root, out)
        build_capacity_curve(pilot_entries, out)
    print("F11 sensitivity done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
