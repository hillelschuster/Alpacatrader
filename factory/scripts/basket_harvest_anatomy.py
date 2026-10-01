#!/usr/bin/env python3
"""HARVEST01 — anatomy of winners vs failures among the ACTUAL basket members.

Measurement only, dev data only, glob-based and rerunnable as harvest days land.

Inputs (one file per day, whatever exists):
  harvest01/sim/fills/<day>.parquet      lane variant=primary, clock in {560, 600}
  harvest01/base/bars/<day>.parquet      minute bars (ticker, et, o/h/l/c, volume)
  harvest01/base/selected/<day>.parquet  variant=primary rows carry rank_known /
                                         rank_unfiltered for clock 600 / 660 / 720

Classes by post-fill MFE (mfe_adj from the fills lane):
  giant  mfe_adj >= 1.00
  runner 0.30 <= mfe_adj < 1.00
  mid    0.10 <= mfe_adj < 0.30
  dud    mfe_adj < 0.10
Blocked slots are cash and are reported as their own count, never folded into a class.
Every path statistic says whether it is a share over all members or only over the
subset that reached the state (touch / damage); never-touched members stay visible.

Outputs:
  harvest01/report/anatomy_summary.parquet  one row per filled member, path metrics
  harvest01/report/anatomy.md               class counts, time-to-MFE, damage tolerance,
                                            recovery cadence, high cadence, volume decay,
                                            rank decay at 600/660/720

Usage:
    .venv/bin/python factory/scripts/basket_harvest_anatomy.py [--data-root ...]
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_pm_snapshots as bps  # noqa: E402

ENTRY_CLOCKS = (560, 600)
RANK_CLOCKS = (600, 660, 720)
TOUCHES = (0.10, 0.30)
DAMAGE = 0.10          # -10% vs fill
CLASS_ORDER = ("giant", "runner", "mid", "dud")
BAR_COLS = ["ticker", "et", "open", "high", "low", "close", "volume"]
FILL_COLS = ["day", "variant", "clock", "rank", "ticker", "status", "fill_et", "fill_px",
             "mfe_adj", "mae_adj", "mfe_et"]
SEL_COLS = ["day", "clock", "variant", "ticker", "rank", "rank_known", "rank_unfiltered", "gain_adj"]


def block_of(day: str) -> str:
    if "2021-02-01" <= day <= "2023-12-31":
        return "B1"
    if "2025-02-01" <= day <= "2026-05-29":
        return "B2"
    return "other"


def is_dev_day(day: str) -> bool:
    """Dev evidence only: sealed 2024 / 2025-01 and reserved 2026-06..08 are excluded."""
    if day.startswith("2024-") or day.startswith("2025-01"):
        return False
    if day >= "2026-06-01":
        return False
    return True


def classify(mfe: float | None) -> str:
    if mfe is None or not math.isfinite(mfe):
        return "unknown"
    if mfe >= 1.0:
        return "giant"
    if mfe >= 0.3:
        return "runner"
    if mfe >= 0.1:
        return "mid"
    return "dud"


# --------------------------------------------------------------------------- metrics


def member_anatomy(sub: pl.DataFrame, fill_et: int, fill_px: float) -> dict:
    """Path metrics from one member's minute bars; None where the tape is short."""
    out: dict = {}
    if sub is None or sub.height == 0 or fill_px is None or fill_px <= 0:
        return out
    et = sub["et"].to_numpy()
    high = sub["high"].to_numpy().astype(float)
    low = sub["low"].to_numpy().astype(float)
    close = sub["close"].to_numpy().astype(float)
    vol = sub["volume"].to_numpy().astype(float)
    i0 = int(np.searchsorted(et, fill_et, side="left"))
    if i0 >= et.size:
        return out
    n_post = int(et.size - i0)
    out["n_bars_post"] = n_post
    out["eod_et"] = int(et[-1])
    out["eod_ret"] = float(close[-1] / fill_px - 1)

    # MFE (bars lens) and time-to-MFE
    rel = int(np.argmax(high[i0:]))
    m_abs = i0 + rel
    out["mfe_bar_adj"] = float(high[m_abs] / fill_px - 1)
    out["mfe_bar_et"] = int(et[m_abs])
    out["time_to_mfe_min"] = int(et[m_abs] - fill_et)

    # max adverse excursion up to (and including) the MFE bar
    pre_lo = low[i0:m_abs + 1]
    out["pre_mfe_mae_pct"] = (float(pre_lo.min() / fill_px - 1)
                              if pre_lo.size else None)

    # new low after first +10 / +30 touch; None when never touched or touch is the
    # final observed bar (censored, not a "no")
    for H in TOUCHES:
        key = f"nl_after_{int(H * 100)}"
        hit = np.flatnonzero(high[i0:] >= fill_px * (1 + H))
        if hit.size == 0:
            out[key] = None
            continue
        t = i0 + int(hit[0])
        after = low[t + 1:]
        prior_min = low[i0:t + 1].min()
        out[key] = (bool(after.min() < prior_min) if after.size else None)

    # cadence of running highs over the first 2h after fill
    j = int(np.searchsorted(et, fill_et + 120, side="right"))
    win_h = high[i0:j]
    if win_h.size:
        run = np.maximum.accumulate(win_h)
        is_new = np.empty(win_h.size, dtype=bool)
        is_new[0] = True
        is_new[1:] = run[1:] > run[:-1]
        ts = et[i0:j][is_new].astype(float)
        out["n_new_highs_2h"] = int(ts.size)
        if ts.size >= 2:
            gaps = np.diff(ts)
            out["hi_interval_med"] = float(np.median(gaps))
            out["hi_interval_q1"] = float(np.quantile(gaps, 0.25))
            out["hi_interval_q3"] = float(np.quantile(gaps, 0.75))
        else:
            out["hi_interval_med"] = None
            out["hi_interval_q1"] = None
            out["hi_interval_q3"] = None

    # volume acceleration at the deepest pre-MFE damage bar (5-bar / prior-20-bar)
    d = i0 + int(np.argmin(low[i0:m_abs + 1]))
    out["damage_et"] = int(et[d])
    out["damage_depth_pct"] = float(low[d] / fill_px - 1)
    if d - 24 >= 0:
        win5 = vol[d - 4:d + 1]
        prior20 = vol[d - 24:d - 4]
        if win5.size == 5 and prior20.size == 20 and prior20.mean() > 0:
            out["vol_accel_5v20"] = float(win5.sum() / (prior20.mean() * 5))
        else:
            out["vol_accel_5v20"] = None
    else:
        out["vol_accel_5v20"] = None

    # recovery: close back at/above fill after first -10% damage; bars from damage bar
    dmg = np.flatnonzero(low[i0:] <= fill_px * (1 - DAMAGE))
    if dmg.size == 0:
        out["ever_damaged_10"] = False
        out["reclaimed_10"] = None
        out["bars_to_reclaim"] = None
    else:
        d0 = i0 + int(dmg[0])
        out["ever_damaged_10"] = True
        back = np.flatnonzero(close[d0:] >= fill_px)
        if back.size:
            out["reclaimed_10"] = True
            out["bars_to_reclaim"] = int(et[d0 + int(back[0])] - et[d0])
        else:
            out["reclaimed_10"] = False
            out["bars_to_reclaim"] = None
    return out


# --------------------------------------------------------------------------- loading


def load_bars(path: Path) -> dict[str, pl.DataFrame]:
    if not path.exists():
        return {}
    df = pl.read_parquet(path, columns=BAR_COLS)
    return {k[0]: v.sort("et") for k, v in df.partition_by("ticker", as_dict=True).items()}


def load_ranks(path: Path) -> dict[tuple[int, str], dict]:
    """(clock, ticker) -> rank row for variant=primary at RANK_CLOCKS."""
    if not path.exists():
        return {}
    df = (pl.read_parquet(path, columns=SEL_COLS)
          .filter((pl.col("variant") == "primary") & pl.col("clock").is_in(list(RANK_CLOCKS))))
    return {(int(r["clock"]), r["ticker"]): r for r in df.iter_rows(named=True)}


def build_members(data_root: Path) -> tuple[list[dict], dict]:
    sim = data_root / "harvest01" / "sim" / "fills"
    base = data_root / "harvest01" / "base"
    files = sorted(sim.glob("*.parquet"))
    if not files:
        return [], {"days": [], "blocked": 0, "no_bars": 0, "filled": 0, "bad_files": 0}
    days_files = 0
    frames = []
    for p in files:  # a day being rewritten by the harvest pass is skipped, not fatal
        try:
            frames.append(pl.read_parquet(p, columns=FILL_COLS))
        except Exception:
            days_files += 1
    if not frames:
        return [], {"days": [], "blocked": 0, "no_bars": 0, "filled": 0, "bad_files": days_files}
    fills = pl.concat(frames).filter(
        (pl.col("variant") == "primary") & pl.col("clock").is_in(list(ENTRY_CLOCKS))
        & pl.col("day").map_elements(is_dev_day, return_dtype=pl.Boolean))
    days = sorted(fills["day"].unique().to_list())
    by_day = fills.partition_by("day", as_dict=True)
    members: list[dict] = []
    meta = {"days": days, "blocked": 0, "no_bars": 0, "filled": 0, "bad_files": days_files}
    for day in days:
        sub = by_day[(day,)].sort(["clock", "rank"])
        try:
            bars = load_bars(base / "bars" / f"{day}.parquet")
        except Exception:
            bars, meta["bad_files"] = {}, meta["bad_files"] + 1
        try:
            ranks = load_ranks(base / "selected" / f"{day}.parquet")
        except Exception:
            ranks = {}
        for r in sub.iter_rows(named=True):
            rec = {
                "day": day, "block": block_of(day), "variant": r["variant"],
                "entry_clock": int(r["clock"]), "rank": int(r["rank"]),
                "ticker": r["ticker"], "status": r["status"],
                "fill_et": r["fill_et"], "fill_px": r["fill_px"],
                "mfe_adj": r["mfe_adj"], "mae_adj_fills": r["mae_adj"],
                "mfe_et_fills": r["mfe_et"], "class": classify(r["mfe_adj"]),
            }
            if r["status"] == "blocked":
                meta["blocked"] += 1
            elif r["status"] == "filled":
                meta["filled"] += 1
                b = bars.get(r["ticker"])
                if b is None:
                    meta["no_bars"] += 1
                else:
                    rec.update(member_anatomy(b, int(r["fill_et"]), r["fill_px"]))
                for c in RANK_CLOCKS:
                    rr = ranks.get((c, r["ticker"]))
                    rec[f"rank{c}_present"] = rr is not None
                    rec[f"rank{c}_known"] = None if rr is None else rr["rank_known"]
                    rec[f"rank{c}_unfiltered"] = None if rr is None else rr["rank_unfiltered"]
                    rec[f"rank{c}_gain"] = None if rr is None else rr["gain_adj"]
            members.append(rec)
    return members, meta


# --------------------------------------------------------------------------- stats


def _flt(vals):
    return [float(v) for v in vals if v is not None and isinstance(v, (int, float, np.floating))
            and math.isfinite(float(v))]


def q(vals, p):
    v = _flt(vals)
    if not v:
        return None
    return float(np.quantile(v, p))


def share(vals):
    v = [b for b in vals if b is not None]
    if not v:
        return None, 0
    return float(sum(1 for b in v if b) / len(v)), len(v)


def fnum(x, nd=2, pct=False):
    if x is None:
        return ""
    if pct:
        return f"{x * 100:.{nd}f}"
    return f"{x:.{nd}f}"


def fshare(x):
    return "" if x is None else f"{x:.3f}"


def table(lines, header, rows):
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))
    for r in rows:
        lines.append("| " + " | ".join(str(c) for c in r) + " |")
    lines.append("")


def group(members, cls=None, clock=None):
    out = members
    if cls is not None:
        out = [m for m in out if m["class"] == cls]
    if clock is not None:
        out = [m for m in out if m["entry_clock"] == clock]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()
    data_root = bps.resolve_data_root(args.data_root)
    rep = data_root / "harvest01" / "report"
    rep.mkdir(parents=True, exist_ok=True)

    members, meta = build_members(data_root)
    if not members:
        print("no fills yet; nothing to do")
        (rep / "anatomy.md").write_text(
            "# HARVEST01 — basket member anatomy\n\n_no fills lane files present yet._\n")
        return 0

    # ---- summary parquet: one row per member (blocked rows included, path cols null)
    schema = {
        "day": pl.Utf8, "block": pl.Utf8, "variant": pl.Utf8, "entry_clock": pl.Int64,
        "rank": pl.Int64, "ticker": pl.Utf8, "status": pl.Utf8, "class": pl.Utf8,
        "fill_et": pl.Int64, "fill_px": pl.Float64, "mfe_adj": pl.Float64,
        "mae_adj_fills": pl.Float64, "mfe_et_fills": pl.Int64,
        "n_bars_post": pl.Int64, "mfe_bar_adj": pl.Float64, "mfe_bar_et": pl.Int64,
        "time_to_mfe_min": pl.Int64, "pre_mfe_mae_pct": pl.Float64,
        "nl_after_10": pl.Boolean, "nl_after_30": pl.Boolean,
        "n_new_highs_2h": pl.Int64, "hi_interval_med": pl.Float64,
        "hi_interval_q1": pl.Float64, "hi_interval_q3": pl.Float64,
        "damage_et": pl.Int64, "damage_depth_pct": pl.Float64,
        "vol_accel_5v20": pl.Float64, "ever_damaged_10": pl.Boolean,
        "reclaimed_10": pl.Boolean, "bars_to_reclaim": pl.Int64,
        "eod_et": pl.Int64, "eod_ret": pl.Float64,
        "rank600_present": pl.Boolean, "rank600_known": pl.Int64,
        "rank600_unfiltered": pl.Int64, "rank600_gain": pl.Float64,
        "rank660_present": pl.Boolean, "rank660_known": pl.Int64,
        "rank660_unfiltered": pl.Int64, "rank660_gain": pl.Float64,
        "rank720_present": pl.Boolean, "rank720_known": pl.Int64,
        "rank720_unfiltered": pl.Int64, "rank720_gain": pl.Float64,
    }
    cols = {k: [m.get(k) for m in members] for k in schema}
    pl.DataFrame(cols, schema=schema).sort(["day", "entry_clock", "rank"]).write_parquet(
        rep / "anatomy_summary.parquet")

    days = meta["days"]
    filled = [m for m in members if m["status"] == "filled"]
    blocked = [m for m in members if m["status"] == "blocked"]
    n_bars_missing = sum(1 for m in filled if m.get("n_bars_post") is None)
    lines: list[str] = []
    lines.append("# HARVEST01 — basket member anatomy: winners vs failures (dev only)\n")
    lines.append(f"days covered: {len(days)} ({days[0]}..{days[-1]}); "
                 f"members: {len(members)} (filled {len(filled)}, blocked/cash {len(blocked)})")
    if n_bars_missing:
        lines.append(f"filled members without bars: {n_bars_missing} "
                     f"(their path stats are UNKNOWN, never zero)")
    if meta.get("bad_files"):
        lines.append(f"unreadable files skipped (partial writes): {meta['bad_files']}")
    lines.append("")
    blocks = sorted({m["block"] for m in members})
    lines.append("blocks present: " + ", ".join(
        f"{b} (days {sorted({m['day'] for m in members if m['block'] == b})[0]}.."
        f"{sorted({m['day'] for m in members if m['block'] == b})[-1]}, "
        f"n={sum(1 for m in members if m['block'] == b)})" for b in blocks) + "\n")
    lines.append("Classes are `mfe_adj` from the fills lane (>={:.2f} giant, "
                 "{:.2f}-{:.2f} runner, {:.2f}-{:.2f} mid, <{:.2f} dud). "
                 "Blocked slots are cash; they never enter a class.\n".format(
                     1.0, 0.3, 1.0, 0.1, 0.3, 0.1))

    # ---- 1. class counts -------------------------------------------------------
    lines.append("## 1. Class counts (filled members; blocked shown separately)\n")
    header = ["entry clock"] + [c for c in CLASS_ORDER] + ["filled n", "blocked/cash"]
    rows = []
    for clock in list(ENTRY_CLOCKS) + [None]:
        g = group(filled, clock=clock)
        label = "pooled" if clock is None else str(clock)
        rows.append([label] + [sum(1 for m in g if m["class"] == c) for c in CLASS_ORDER]
                    + [len(g), sum(1 for m in blocked if clock is None or m["entry_clock"] == clock)])
    table(lines, header, rows)

    # ---- 2. time-to-MFE --------------------------------------------------------
    lines.append("## 2. Time-to-MFE — minutes from fill to the max-high bar (et diff)\n")
    table(lines, ["class", "n", "median", "q1", "q3"],
          [[c, len(_flt([m.get("time_to_mfe_min") for m in group(filled, cls=c)])),
            fnum(q([m.get("time_to_mfe_min") for m in group(filled, cls=c)], .5), 1),
            fnum(q([m.get("time_to_mfe_min") for m in group(filled, cls=c)], .25), 1),
            fnum(q([m.get("time_to_mfe_min") for m in group(filled, cls=c)], .75), 1)]
           for c in CLASS_ORDER] +
          [["all", len(_flt([m.get("time_to_mfe_min") for m in filled])),
            fnum(q([m.get("time_to_mfe_min") for m in filled], .5), 1),
            fnum(q([m.get("time_to_mfe_min") for m in filled], .25), 1),
            fnum(q([m.get("time_to_mfe_min") for m in filled], .75), 1)]])

    # ---- 3. damage tolerance ---------------------------------------------------
    lines.append("## 3. Damage tolerance — pre-MFE max adverse excursion vs fill\n")
    lines.append("`pre-MFE MAE` is the worst low between fill and the MFE bar. "
                 "`new low after +10/+30` is a share over members that actually touched "
                 "the level (touch n in parens); untouched members are shown, not dropped.\n")
    rows = []
    for c in CLASS_ORDER:
        g = group(filled, cls=c)
        s10, n10 = share([m.get("nl_after_10") for m in g])
        s30, n30 = share([m.get("nl_after_30") for m in g])
        rows.append([c, len(_flt([m.get("pre_mfe_mae_pct") for m in g])),
                     fnum(q([m.get("pre_mfe_mae_pct") for m in g], .5), 2, pct=True),
                     fnum(q([m.get("pre_mfe_mae_pct") for m in g], .25), 2, pct=True),
                     fnum(q([m.get("pre_mfe_mae_pct") for m in g], .75), 2, pct=True),
                     f"{fshare(s10)} (n={n10})", f"{fshare(s30)} (n={n30})"])
    table(lines, ["class", "n", "med pre-MFE MAE %", "q1 %", "q3 %",
                  "new low after +10", "new low after +30"], rows)

    # ---- 4. recovery cadence ---------------------------------------------------
    lines.append("## 4. Recovery cadence — close back at/above fill after first -10% damage\n")
    lines.append("`damaged` share is over all members; `reclaimed` share and bars are over "
                 "damaged members only; `bars` counts minute bars from the first damage bar "
                 "to the first close back >= fill.\n")
    rows = []
    for c in CLASS_ORDER:
        g = group(filled, cls=c)
        damaged = [m for m in g if m.get("ever_damaged_10") is True]
        s_dmg, n_dmg = share([m.get("ever_damaged_10") for m in g])
        s_rec, n_rec = share([m.get("reclaimed_10") for m in damaged])
        rows.append([c, f"{fshare(s_dmg)} (n={n_dmg})", n_rec,
                     fshare(s_rec),
                     fnum(q([m.get("bars_to_reclaim") for m in damaged], .5), 1),
                     fnum(q([m.get("bars_to_reclaim") for m in damaged], .25), 1),
                     fnum(q([m.get("bars_to_reclaim") for m in damaged], .75), 1),
                     len([m for m in damaged if m.get("reclaimed_10") is False]),
                     fnum(q([m.get("eod_ret") for m in g], .5), 2, pct=True)])
    table(lines, ["class", "damaged -10% share", "damaged n", "reclaimed share",
                  "bars med", "bars q1", "bars q3", "never reclaimed", "med EOD ret %"], rows)

    # ---- 5. high cadence -------------------------------------------------------
    lines.append("## 5. Running-high cadence over the first 2h after fill\n")
    lines.append("A running high is a bar whose high beats every high since fill; "
                 "interval = minutes between consecutive running highs (fills with <2 "
                 "running highs have no cadence).\n")
    rows = []
    for c in CLASS_ORDER:
        g = group(filled, cls=c)
        rows.append([c, len(_flt([m.get("n_new_highs_2h") for m in g])),
                     fnum(q([m.get("n_new_highs_2h") for m in g], .5), 1),
                     fnum(q([m.get("n_new_highs_2h") for m in g], .25), 1),
                     fnum(q([m.get("n_new_highs_2h") for m in g], .75), 1),
                     len(_flt([m.get("hi_interval_med") for m in g])),
                     fnum(q([m.get("hi_interval_med") for m in g], .5), 1),
                     fnum(q([m.get("hi_interval_q1") for m in g], .5), 1),
                     fnum(q([m.get("hi_interval_q3") for m in g], .5), 1)])
    table(lines, ["class", "n", "med new highs", "q1", "q3",
                  "n with >=2 highs", "med interval", "med of q1s", "med of q3s"], rows)

    # ---- 6. volume decay -------------------------------------------------------
    lines.append("## 6. Volume at the deepest pre-MFE damage — 5-bar / prior-20-bar ratio\n")
    lines.append("Ratio >1 means the damage bar sat on an expanding tape; <1 means the "
                 "damage came on fading volume. Members whose damage bar had <24 bars of "
                 "history are excluded (n shown).\n")
    table(lines, ["class", "n", "median ratio", "q1", "q3"],
          [[c, len(_flt([m.get("vol_accel_5v20") for m in group(filled, cls=c)])),
            fnum(q([m.get("vol_accel_5v20") for m in group(filled, cls=c)], .5), 2),
            fnum(q([m.get("vol_accel_5v20") for m in group(filled, cls=c)], .25), 2),
            fnum(q([m.get("vol_accel_5v20") for m in group(filled, cls=c)], .75), 2)]
           for c in CLASS_ORDER] +
          [["all", len(_flt([m.get("vol_accel_5v20") for m in filled])),
            fnum(q([m.get("vol_accel_5v20") for m in filled], .5), 2),
            fnum(q([m.get("vol_accel_5v20") for m in filled], .25), 2),
            fnum(q([m.get("vol_accel_5v20") for m in filled], .75), 2)]])

    # ---- 7. rank behavior ------------------------------------------------------
    lines.append("## 7. Rank behavior after entry — variant=primary selected rows at 600/660/720\n")
    lines.append("`present` means the ticker still sits in that clock's emitted top-4; an "
                 "absent ticker has no stored rank (rank_known/rank_unfiltered are only "
                 "serialised for emitted rows). `rank_known`/`rank_unfiltered` medians are "
                 "over present members; `known<=4` is a share over present members.\n")
    for c in ("giant", "dud"):
        g = group(filled, cls=c)
        rows = []
        for clk in RANK_CLOCKS:
            key = f"rank{clk}"
            present = [m for m in g if m.get(f"{key}_present")]
            known = [m.get(f"{key}_known") for m in present]
            unf = [m.get(f"{key}_unfiltered") for m in present]
            k4 = share([k is not None and k <= 4 for k in known])
            rows.append([clk, len(g), len(present), fshare(len(present) / len(g)) if g else "",
                         fnum(q(known, .5), 1), fnum(q(known, .25), 1), fnum(q(known, .75), 1),
                         fnum(q(unf, .5), 1), f"{fshare(k4[0])} (n={k4[1]})"])
        lines.append(f"### {c} (n={len(g)})\n")
        table(lines, ["rank clock", "members", "present", "present share", "med rank_known",
                      "q1", "q3", "med rank_unfiltered", "known<=4"], rows)

    # ---- 8. failures visible ---------------------------------------------------
    lines.append("## 8. Failures kept in view\n")
    duds = group(filled, cls="dud")
    lines.append(f"- duds (mfe_adj < 0.10): {len(duds)}/{len(filled)} filled members "
                 f"({len(duds)/max(1,len(filled)):.3f}); giants: "
                 f"{len(group(filled, cls='giant'))}.\n")
    lines.append(f"- blocked slots booked as cash: {len(blocked)} of {len(members)} "
                 f"basket slots ({len(blocked)/max(1,len(members)):.3f}).\n")
    never10 = sum(1 for m in filled if m.get("nl_after_10") is None)
    never30 = sum(1 for m in filled if m.get("nl_after_30") is None)
    lines.append(f"- no post-touch observation of +10% after fill (never touched, or "
                 f"touch on the final bar): {never10}/{len(filled)}; same for +30%: "
                 f"{never30}/{len(filled)}.\n")
    no_dmg = sum(1 for m in filled if m.get("ever_damaged_10") is False)
    lines.append(f"- never damaged -10% after fill: {no_dmg}/{len(filled)}.\n")
    worst = sorted([m for m in filled if m.get("eod_ret") is not None],
                   key=lambda m: m["eod_ret"])[:5]
    if worst:
        lines.append("- worst EOD close from fill (member level): " + "; ".join(
            f"{m['day']} {m['ticker']}@{m['entry_clock']} {m['eod_ret']*100:.1f}% "
            f"(mfe {m['mfe_adj']*100:.1f}%)" for m in worst) + ".\n")

    (rep / "anatomy.md").write_text("\n".join(lines) + "\n")
    print(f"anatomy: {len(members)} members over {len(days)} days; "
          f"classes " + ", ".join(f"{c}={len(group(filled, cls=c))}" for c in CLASS_ORDER)
          + f"; blocked={len(blocked)}; wrote {rep/'anatomy.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
