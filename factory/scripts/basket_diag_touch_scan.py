"""Diagnostic producer (worktree-local research tool, 2026-09-24 C1 cycle).

Reproduces the touch-excursion scans quoted in
`factory/artifacts/basket/phase2/DIAGNOSTICS_20260924/`:

    python factory/scripts/basket_diag_touch_scan.py fade
    python factory/scripts/basket_diag_touch_scan.py mixture
    python factory/scripts/basket_diag_touch_scan.py halt-time
    python factory/scripts/basket_diag_touch_scan.py limit
    python factory/scripts/basket_diag_touch_scan.py identifiability
    python factory/scripts/basket_diag_touch_scan.py afternoon
    python factory/scripts/basket_diag_touch_scan.py nontouch
    python factory/scripts/basket_diag_touch_scan.py shape

Conventions (identical to the artifacts these regenerate): A_pm anatomy fills, top-3 by canonical
rank, candidate bars, RTH only; a "touch" is the first bar whose high reaches entry*(1+30%) on a
completed bar; the post-touch statistics use that bar's close and the session's last close.  No
simulation is run: these are descriptive path scans.
"""
import json
import sys
from pathlib import Path

import numpy as np
import polars as pl

try:
    from factory.scripts import basket_sim as sim
except ImportError:                                        # direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from factory.scripts import basket_sim as sim

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "factory/artifacts/basket/phase2/DIAGNOSTICS_20260924"
LEVEL = 30.0
POPS = (("A_pm", "A_pm", 570), ("A_open", "A_open", 570), ("B600", "B", 600))


def fills(day: str, bars, family: str, pop: str, T: int, top: int = 3):
    rec = sim.load_anatomy(day)
    snap = sim.snapshot_of(rec, pop, T)
    if snap is None:
        return []
    out = []
    for nm in snap["names"][:top]:
        fl = nm.get("fill")
        if not fl or fl.get("blocked"):
            continue
        tkb = bars.ticker(nm["ticker"])
        if tkb is not None:
            out.append((nm, fl, tkb))
    return out


def _window(ets, fl, session_end):
    entry_et = int(fl["et"])
    first = next((i for i, e in enumerate(ets) if int(e) >= entry_et), None)
    last = max((i for i, e in enumerate(ets) if int(e) <= session_end), default=None)
    if first is None or last is None or last <= first + 5:
        return None
    return first, last


def scan_touches(families=POPS, top=3):
    """One row per first +30% touch with the causal and outcome fields used by the scans."""
    days = sim.dev_days()
    sem = sim.session_end_map()
    rows = []
    for day in days:
        bars = sim.load_bars(day, sem[day])
        for fam, pop, T in families:
            for nm, fl, tkb in fills(day, bars, fam, pop, T, top):
                ets, hi, cl, vo = tkb["et"], tkb["high"], tkb["close"], tkb["volume"]
                win = _window(ets, fl, sem[day])
                if win is None:
                    continue
                first, last = win
                entry = float(fl["px"])
                trig = entry * (1 + LEVEL / 100.0)
                hit = next((i for i in range(first, last + 1)
                            if float(hi[i]) >= trig - 1e-12), None)
                if hit is None:
                    continue
                touch_close = float(cl[hit])
                post_hi = hi[hit + 1:last + 1]
                post_max = float(post_hi.max()) if len(post_hi) else touch_close
                gaps = np.diff(ets[first:hit + 1])
                run_high = float(hi[first:hit + 1].max())
                cl5 = float(cl[max(first, hit - 4)])
                vol_mean = float(vo[first:hit + 1].mean()) if hit > first else float(vo[hit])
                rows.append({
                    "day": day, "family": fam, "ticker": nm["ticker"],
                    "rank": int(nm.get("rank") or 0),
                    "touch_et": int(ets[hit]),
                    "touch_close": touch_close,
                    "ret_to_touch_close": touch_close / entry - 1.0,
                    "post_touch_max_ret": post_max / touch_close - 1.0,
                    "touch_to_eod": float(cl[last]) / touch_close - 1.0,
                    "entry_to_eod": float(cl[last]) / entry - 1.0,
                    "halts_so_far": int((gaps > 1).sum()),
                    "minutes_since_fill": int(ets[hit]) - int(fl["et"]),
                    "drawdown_from_runhigh": touch_close / run_high - 1.0,
                    "velocity_5": touch_close / cl5 - 1.0,
                    "vol_ratio": (float(vo[hit]) / vol_mean) if vol_mean > 0 else None,
                })
    return pl.DataFrame(rows)


def cmd_fade(frame: pl.DataFrame) -> dict:
    out = {"n": frame.height, "days": int(frame["day"].n_unique()), "levels": {}}
    for level in (30, 50, 100):
        sub = frame.filter(pl.col("post_touch_max_ret") >= 0)  # placeholder, replaced below
        del sub
    for level in (30,):
        sub = frame
        out["levels"][str(level)] = {
            "n": int(sub.height),
            "touch_to_eod_mean": float(sub["touch_to_eod"].mean()),
            "touch_to_eod_median": float(sub["touch_to_eod"].median()),
            "positive_share": float((sub["touch_to_eod"] > 0).mean()),
            "entry_to_eod_mean": float(sub["entry_to_eod"].mean()),
        }
    return out


def cmd_mixture(frame: pl.DataFrame) -> dict:
    cont = frame.filter(pl.col("post_touch_max_ret") >= 0.10)
    fade = frame.filter(pl.col("post_touch_max_ret") < 0.10)
    return {
        "n": frame.height,
        "continued_share": float(cont.height / frame.height) if frame.height else None,
        "continued": {"n": cont.height, "touch_to_eod_mean": float(cont["touch_to_eod"].mean())},
        "faded": {"n": fade.height, "touch_to_eod_mean": float(fade["touch_to_eod"].mean())},
        "unconditional_touch_to_eod_mean": float(frame["touch_to_eod"].mean()),
    }


def cmd_halt_time(frame: pl.DataFrame) -> dict:
    def hb(h):
        return "0-1" if h <= 1 else ("2-3" if h <= 3 else ("4-5" if h <= 5 else "6+"))
    def tb(t):
        return "<10:00" if t < 600 else ("10:00-11:00" if t < 660 else
                                         ("11:00-13:00" if t < 780 else "13:00+"))
    frame = frame.with_columns(
        pl.col("halts_so_far").map_elements(hb, return_dtype=pl.Utf8).alias("halt_bucket"),
        pl.col("touch_et").map_elements(tb, return_dtype=pl.Utf8).alias("time_bucket"))
    def agg(col, key):
        return {b: {"n": int(frame.filter(pl.col(col) == b).height),
                    "touch_to_eod_mean": float(frame.filter(pl.col(col) == b)["touch_to_eod"].mean())}
                for b in key if frame.filter(pl.col(col) == b).height}
    return {"n": frame.height,
            "by_halts": agg("halt_bucket", ("0-1", "2-3", "4-5", "6+")),
            "by_touch_time": agg("time_bucket", ("<10:00", "10:00-11:00", "11:00-13:00", "13:00+"))}


def cmd_identifiability(frame: pl.DataFrame) -> dict:
    cont = frame.filter(pl.col("post_touch_max_ret") >= 0.10)
    fade = frame.filter(pl.col("post_touch_max_ret") < 0.10)
    feats = ("ret_to_touch_close", "drawdown_from_runhigh", "velocity_5", "vol_ratio",
             "halts_so_far", "minutes_since_fill", "rank")
    result = {}
    for name in feats:
        c = cont[name].drop_nulls().to_numpy()
        d = fade[name].drop_nulls().to_numpy()
        if len(c) < 10 or len(d) < 10:
            continue
        allv = np.concatenate([c, d])
        order = allv.argsort().argsort() + 1
        auc = (order[:len(c)].sum() - len(c) * (len(c) + 1) / 2) / (len(c) * len(d))
        result[name] = {"cont_mean": float(c.mean()), "fade_mean": float(d.mean()), "auc": float(auc)}
    return {"n": frame.height, "continuers": cont.height, "features": result}


def cmd_limit(top: int = 3) -> dict:
    days = sim.dev_days()
    sem = sim.session_end_map()
    rows = []
    for day in days:
        bars = sim.load_bars(day, sem[day])
        for nm, fl, tkb in fills(day, bars, "A_pm", "A_pm", 570, top):
            ets, lo, op, cl = tkb["et"], tkb["low"], tkb["open"], tkb["close"]
            win = _window(ets, fl, sem[day])
            if win is None:
                continue
            first, last = win
            entry = float(fl["px"])
            row = {"day": day, "ret_from_open": float(cl[last]) / entry - 1.0}
            window = [i for i in range(first, last + 1) if int(ets[i]) <= 630]
            for pct in (2, 5, 10):
                trig = entry * (1 - pct / 100.0)
                hit = next((i for i in window if float(lo[i]) <= trig + 1e-12), None)
                row[f"ret_{pct}"] = (float(cl[last]) / min(float(op[hit]), trig) - 1.0
                                     if hit is not None else None)
            rows.append(row)
    frame = pl.DataFrame(rows)
    out = {"n": frame.height, "baseline": float(frame["ret_from_open"].mean()), "limits": {}}
    for pct in (2, 5, 10):
        filled = frame.filter(pl.col(f"ret_{pct}").is_not_null())
        out["limits"][f"-{pct}pct"] = {
            "fill_rate": float(filled.height / frame.height),
            "filled_entry_to_eod_mean": float(filled[f"ret_{pct}"].mean()),
            "unfilled_entry_to_eod_mean": float(
                frame.filter(pl.col(f"ret_{pct}").is_null())["ret_from_open"].mean()),
        }
    return out


def cmd_afternoon() -> dict:
    frame = scan_touches(POPS)
    late = frame.filter(pl.col("touch_et") >= 660)
    vals = late["touch_to_eod"].to_numpy()
    order = np.sort(vals)[::-1]
    return {"n": int(late.height), "days": int(late["day"].n_unique()),
            "mean": float(vals.mean()), "median": float(np.median(vals)),
            "positive_share": float((vals > 0).mean()),
            "top5_share_of_sum": float(order[:5].sum() / vals.sum()) if vals.sum() else None,
            "by_family": {fam: {"n": int(late.filter(pl.col("family") == fam).height),
                                "mean": float(late.filter(pl.col("family") == fam)["touch_to_eod"].mean())}
                          for fam in ("A_pm", "A_open", "B600")}}


def scan_nontouchers(families=(("A_pm", "A_pm", 570),), top=3):
    """One row per fill that never reaches +30%, with its early and full-session outcome.

    This is the majority of the sleeve and, per the harvest decomposition, where the loss
    lives; the row carries the causal early state (10:00 return) and the realized outcome.
    """
    days = sim.dev_days()
    sem = sim.session_end_map()
    rows = []
    for day in days:
        bars = sim.load_bars(day, sem[day])
        for fam, pop, T in families:
            for nm, fl, tkb in fills(day, bars, fam, pop, T, top):
                ets, hi, lo, cl = tkb["et"], tkb["high"], tkb["low"], tkb["close"]
                win = _window(ets, fl, sem[day])
                if win is None:
                    continue
                first, last = win
                entry = float(fl["px"])
                hit30 = next((i for i in range(first, last + 1)
                              if float(hi[i]) >= entry * 1.30 - 1e-12), None)
                if hit30 is not None:
                    continue
                i600 = next((i for i in range(first, last + 1) if int(ets[i]) >= 600), None)
                if i600 is None:
                    continue
                rows.append({
                    "day": day, "family": fam, "ticker": nm["ticker"],
                    "rank": int(nm.get("rank") or 0),
                    "early_ret": float(cl[i600]) / entry - 1.0,
                    "ret_eod": float(cl[last]) / entry - 1.0,
                    "mfe": float(hi[first:last + 1].max()) / entry - 1.0,
                    "mae": float(lo[first:last + 1].min()) / entry - 1.0,
                    "touch10": bool(float(hi[first:last + 1].max()) >= entry * 1.10),
                    "halted": bool((np.diff(ets[first:last + 1]) > 1).any()),
                })
    return pl.DataFrame(rows)


def cmd_nontouch() -> dict:
    frame = scan_nontouchers()
    buckets = ((">=+10%", 0.10, 1e9), ("0..+10%", 0.0, 0.10),
               ("-10..0%", -0.10, 0.0), ("<-10%", -1e9, -0.10))
    out = {"n": frame.height, "days": int(frame["day"].n_unique()),
           "mean_ret_eod": float(frame["ret_eod"].mean()),
           "median_ret_eod": float(frame["ret_eod"].median()),
           "positive_share": float((frame["ret_eod"] > 0).mean()),
           "aggregate_ret": float(frame["ret_eod"].sum()),
           "touch10_share": float(frame["touch10"].mean()),
           "by_early_ret": {}}
    for label, lo, hi in buckets:
        sub = frame.filter((pl.col("early_ret") >= lo) & (pl.col("early_ret") < hi))
        if not sub.height:
            continue
        out["by_early_ret"][label] = {
            "n": int(sub.height),
            "share": float(sub.height / frame.height),
            "early_ret_mean": float(sub["early_ret"].mean()),
            "ret_eod_mean": float(sub["ret_eod"].mean()),
            "ret_eod_median": float(sub["ret_eod"].median()),
            "positive_share": float((sub["ret_eod"] > 0).mean()),
            "mfe_mean": float(sub["mfe"].mean()),
            "mae_mean": float(sub["mae"].mean()),
            "touch10_share": float(sub["touch10"].mean()),
            "halt_share": float(sub["halted"].mean()),
            "aggregate_ret": float(sub["ret_eod"].sum()),
            # exit at the 10:00 close versus holding to the session close, per ticket, no
            # friction: `cut_minus_hold_1000` > 0 means cutting at 10:00 beats holding.
            "cut_minus_hold_1000": float(sub["early_ret"].mean() - sub["ret_eod"].mean()),
            "cut_wins_share": float((sub["early_ret"] > sub["ret_eod"]).mean()),
        }
    return out


CHECKPOINTS = (600, 630, 660, 690, 720, 780, 840, 900, 960)


def scan_shape(families=(("A_pm", "A_pm", 570),), top=3):
    """One row per fill with its return at fixed intraday checkpoints (a ruler, not a policy)."""
    days = sim.dev_days()
    sem = sim.session_end_map()
    rows = []
    for day in days:
        bars = sim.load_bars(day, sem[day])
        for fam, pop, T in families:
            for nm, fl, tkb in fills(day, bars, fam, pop, T, top):
                ets, hi, cl = tkb["et"], tkb["high"], tkb["close"]
                win = _window(ets, fl, sem[day])
                if win is None:
                    continue
                first, last = win
                entry = float(fl["px"])
                hit30 = next((i for i in range(first, last + 1)
                              if float(hi[i]) >= entry * 1.30 - 1e-12), None)
                row = {"day": day, "family": fam, "ticker": nm["ticker"],
                       "cohort": "touch30" if hit30 is not None else "nontouch",
                       "ret_eod": float(cl[last]) / entry - 1.0}
                for cp in CHECKPOINTS:
                    idx = next((i for i in range(first, last + 1) if int(ets[i]) >= cp), None)
                    row[f"ret_{cp}"] = (float(cl[idx]) / entry - 1.0) if idx is not None else None
                rows.append(row)
    return pl.DataFrame(rows)


def _cp_mean(sub: pl.DataFrame, cp: int):
    col = sub.filter(pl.col(f"ret_{cp}").is_not_null())
    return float(col[f"ret_{cp}"].mean()) if col.height else None


def cmd_shape() -> dict:
    frame = scan_shape()
    out = {"n": frame.height, "checkpoints": {}, "by_cohort": {}}
    for cp in CHECKPOINTS:
        col = f"ret_{cp}"
        sub = frame.filter(pl.col(col).is_not_null())
        out["checkpoints"][str(cp)] = ({
            "n": int(sub.height),
            "mean": float(sub[col].mean()),
            "median": float(sub[col].median()),
            "positive_share": float((sub[col] > 0).mean()),
        } if sub.height else {"n": 0, "mean": None, "median": None, "positive_share": None})
    for cohort in ("touch30", "nontouch"):
        sub = frame.filter(pl.col("cohort") == cohort)
        out["by_cohort"][cohort] = {
            "n": int(sub.height),
            "eod_mean": float(sub["ret_eod"].mean()),
            "path": {str(cp): _cp_mean(sub, cp) for cp in CHECKPOINTS},
        }
    # early-state split at 10:00, held fixed through the day
    up = frame.filter(pl.col("ret_600") >= 0)
    dn = frame.filter(pl.col("ret_600") < 0)
    out["by_early_state"] = {
        "early_up": {"n": int(up.height), "eod_mean": float(up["ret_eod"].mean()),
                     "path": {str(cp): _cp_mean(up, cp) for cp in CHECKPOINTS}},
        "early_down": {"n": int(dn.height), "eod_mean": float(dn["ret_eod"].mean()),
                       "path": {str(cp): _cp_mean(dn, cp) for cp in CHECKPOINTS}},
    }
    # false-cut accounting: inside the early-down cohort, what does the recovering minority
    # contribute (i.e. what would a blanket cut at 10:00 destroy)?
    false_cut = {}
    for state, sub in (("early_up", up), ("early_down", dn)):
        for cohort in ("touch30", "nontouch", "all"):
            s = sub if cohort == "all" else sub.filter(pl.col("cohort") == cohort)
            if not s.height:
                continue
            false_cut[f"{state}|{cohort}"] = {
                "n": int(s.height),
                "ret_600_mean": _cp_mean(s, 600),
                "ret_eod_mean": float(s["ret_eod"].mean()),
                "ret_eod_median": float(s["ret_eod"].median()),
                "aggregate_eod": float(s["ret_eod"].sum()),
                "positive_share": float((s["ret_eod"] > 0).mean()),
            }
    out["false_cut_accounting"] = false_cut
    return out


def main(argv=None):
    argv = argv or sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2
    command = argv[0]
    # optional family filter: `mixture A_pm` restricts the scan to one entry family
    family_filter = argv[1] if len(argv) > 1 else None
    if command == "limit":
        result = cmd_limit()
        name = "entry_limit_probe.json"
    elif command == "afternoon":
        result = cmd_afternoon()
        name = "afternoon_touch_continuation.json"
    elif command == "nontouch":
        result = cmd_nontouch()
        name = "nontouch_majority.json"
    elif command == "shape":
        result = cmd_shape()
        name = "intraday_shape.json"
    else:
        families = POPS if family_filter is None else tuple(
            entry for entry in POPS if entry[0] == family_filter)
        if not families:
            print(f"unknown family {family_filter!r}; expected one of {[e[0] for e in POPS]}")
            return 2
        frame = scan_touches(families)
        if command == "fade":
            result = cmd_fade(frame)
            name = "touch_fade_paths.json"
        elif command == "mixture":
            result = cmd_mixture(frame)
            name = "touch_mixture.json"
        elif command == "halt-time":
            result = cmd_halt_time(frame)
            name = "touch_halt_time_splits.json"
        elif command == "identifiability":
            result = cmd_identifiability(frame)
            name = "continuation_identifiability.json"
        else:
            print(__doc__)
            return 2
    if family_filter:
        name = name.replace(".json", f"_{family_filter}.json")
    result["producer"] = "factory/scripts/basket_diag_touch_scan.py"
    result["scope"] = f"entry families: {family_filter or [e[0] for e in POPS]}"
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(result, indent=1, sort_keys=True, default=str))
    print(json.dumps({"command": command, "out": str(OUT / name)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
