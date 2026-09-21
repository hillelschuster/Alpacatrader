#!/usr/bin/env python3
"""BASKET-01 Phase-1 anatomy extractor — measurement only, no strategy.

Contract: researches/PRE-REG-BASKET-01.md (frozen). Reads raw clean RTH minute
bars (+ 2025 premarket bars), builds causal top-K snapshots for A_open / A_pm /
B(T), records causal fills, ruler ladders (with ambiguity indices), halt/gap
buckets, containment materials and rule-free LS materials.

Causality rules enforced here:
  * eligibility uses only info knowable at T (no session bar counts, no later
    halts, no later fillability, no EOD outcomes);
  * A_open decision from the completed 09:30 bar -> fill 09:31 open;
  * A_pm decision pre-bell -> fill at the 09:30 first-trade open (alt bound 09:31);
  * B(T) decision from last completed bar et<=T-1 -> fill first bar open et>=T;
  * no release-rule / survivor-rule constants exist in this file.

Outputs per processed day (resumable; existing files skipped unless --force):
  factory/artifacts/basket/anatomy/YYYY-MM-DD.jsonl
  factory/artifacts/basket/bars/YYYY-MM-DD.parquet

Evidence boundary: reserved months 2026-06..08 are refused unless --allow-reserved.

Usage:
  .venv/bin/python factory/scripts/basket_anatomy.py --self-test
  .venv/bin/python factory/scripts/basket_anatomy.py --months 2025-06 --max-days 3
  .venv/bin/python factory/scripts/basket_anatomy.py --all-dev
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
PITP = DATA / "pit" / "pit_symbols.parquet"
SPLITP = DATA / "split_flags.parquet"
OUT = ROOT / "factory" / "artifacts" / "basket"

RESERVED = {"2026-06", "2026-07", "2026-08"}  # evidence boundary (PRE-REG §5)
DEV_END = "2026-05"
T_LIST = [575, 580, 585, 590, 595, 600, 615, 630, 645, 660, 690, 720]
K_TOP = 10
UP = [5, 10, 20, 30, 50, 100]
DN = [3, 5, 8, 10, 15]
TIMES = [575, 580, 585, 590, 595, 600, 615, 630, 645, 660, 690, 720]
FRESH_MIN = 15
MIN_PRICE = 1.0
GAP_BLOCK = 5  # entry gap in minutes >= this = blocked slot
RATIO_AUDIT = 2.0  # anchor-ratio >= this is listed for the split audit

_PIT: dict = {}
_SPLIT_BY_DAY: dict = {}


def add_month(month: str, k: int) -> str:
    y, m = int(month[:4]), int(month[5:])
    m = m - 1 + k
    return f"{y + m // 12:04d}-{m % 12 + 1:02d}"


def month_path(month: str) -> Path:
    if month >= "2026-03":
        return DATA / "backfill" / f"clean_ohlcv_{month}.parquet"
    return DATA / f"clean_ohlcv_{month}.parquet"


def pm_path(month: str) -> Path:
    return DATA / "backfill" / f"premarket_ohlcv_{month}.parquet"


def load_month_lazy(month: str) -> pl.LazyFrame:
    lf = pl.scan_parquet(month_path(month)).select(
        ["timestamp", "ticker", "open", "high", "low", "close", "volume"]
    )
    lf = lf.with_columns(
        pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("tset")
    )
    lf = lf.with_columns(
        (pl.col("tset").dt.hour().cast(pl.Int32) * 60 + pl.col("tset").dt.minute().cast(pl.Int32)).alias("et"),
        pl.col("tset").dt.date().alias("date"),
    )
    return lf.filter((pl.col("et") >= 570) & (pl.col("et") < 960))


def month_days(month: str) -> list:
    lf = load_month_lazy(month)
    return sorted(lf.select("date").unique().collect()["date"].to_list())


def load_pm_month(month: str) -> pl.DataFrame | None:
    p = pm_path(month)
    if not p.exists():
        return None
    df = pl.read_parquet(
        p, columns=["timestamp", "ticker", "open", "high", "low", "close", "volume"]
    )
    df = df.with_columns(
        pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("tset")
    )
    df = df.with_columns(
        (pl.col("tset").dt.hour().cast(pl.Int32) * 60 + pl.col("tset").dt.minute().cast(pl.Int32)).alias("et"),
        pl.col("tset").dt.date().alias("date"),
    )
    return df.filter(pl.col("et") <= 569)


def last_closes_of_month(month: str) -> dict:
    """Per-ticker last RTH close of the month (prev-close seed; per-ticker convention)."""
    p = month_path(month)
    if not p.exists():
        return {}
    df = pl.read_parquet(p, columns=["timestamp", "ticker", "close"])
    df = df.with_columns(
        pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("t")
    )
    df = df.with_columns(
        (pl.col("t").dt.hour().cast(pl.Int32) * 60 + pl.col("t").dt.minute().cast(pl.Int32)).alias("et"),
        pl.col("t").dt.date().alias("d"),
    )
    df = df.filter((pl.col("et") >= 570) & (pl.col("et") < 960))
    last = df.group_by("ticker").agg(
        pl.col("close").sort_by(["d", "et"]).last().alias("c")
    )
    return dict(zip(last["ticker"].to_list(), last["c"].to_list()))


def pit_elig(day: str) -> frozenset:
    if "df" not in _PIT:
        pit = pl.read_parquet(PITP, columns=["vintage", "symbol"])
        _PIT["df"] = pit
        _PIT["vintages"] = sorted(pit["vintage"].unique().to_list())
        _PIT["memo_v"] = None
        _PIT["memo_set"] = frozenset()
    i = bisect.bisect_right(_PIT["vintages"], day) - 1
    if i < 0:
        return frozenset()
    v = _PIT["vintages"][i]
    if _PIT["memo_v"] != v:
        _PIT["memo_set"] = frozenset(
            _PIT["df"].filter(pl.col("vintage") == v)["symbol"].to_list()
        )
        _PIT["memo_v"] = v
    return _PIT["memo_set"]


def split_excl(day: str) -> set:
    if not _SPLIT_BY_DAY:
        sf = pl.read_parquet(SPLITP, columns=["date", "ticker"])
        for d in sf["date"].unique().to_list():
            _SPLIT_BY_DAY[str(d)] = set()
        for d, t in zip(sf["date"].to_list(), sf["ticker"].to_list()):
            _SPLIT_BY_DAY[str(d)].add(t)
    return _SPLIT_BY_DAY.get(day, set())


# ---------------------------------------------------------------- pure helpers


def pick_fill(ets, mode: str, T: int):
    target = 570 if mode == "A_pm" else (571 if mode == "A_open" else T)
    for i, v in enumerate(ets):
        if v >= target:
            return i
    return None


def ngaps(ets, a: int, b: int) -> int:
    return int(sum(1 for i in range(a + 1, b) if ets[i] - ets[i - 1] > 1))


def ladders(ets, os_, hs, ls, cs, fi: int, fill: float):
    out = {"up": {}, "dn": {}}
    n = len(ets)
    for H in UP:
        thr = fill * (1.0 + H / 100.0)
        idx = None
        for i in range(fi, n):
            if hs[i] >= thr:
                idx = i
                break
        out["up"][str(H)] = None if idx is None else {
            "i": idx - fi,
            "et": int(ets[idx]),
            "touch": round(float(hs[idx]), 6),
            "exec": (round(float(os_[idx + 1]), 6) if idx + 1 < n else None),
        }
    for L in DN:
        thr = fill * (1.0 - L / 100.0)
        idx = None
        for i in range(fi, n):
            if ls[i] <= thr:
                idx = i
                break
        out["dn"][str(L)] = None if idx is None else {
            "i": idx - fi,
            "et": int(ets[idx]),
            "touch": round(float(ls[idx]), 6),
            "exec": (round(float(os_[idx + 1]), 6) if idx + 1 < n else None),
        }
    seg_h, seg_l = hs[fi:], ls[fi:]
    im = int(np.argmax(seg_h)) + fi
    ia = int(np.argmin(seg_l)) + fi
    return out, im, ia


def states_at(ets, cs, hs, fi: int, fill: float) -> dict:
    res = {}
    j = fi
    peak = -1.0
    for t in TIMES:
        lim = t - 1
        k = -1
        while j < len(ets) and ets[j] <= lim:
            if hs[j] > peak:
                peak = hs[j]
            k = j
            j += 1
        res[str(t)] = None if k < fi else {
            "ret": round(float(cs[k] / fill - 1), 6),
            "dd": round(float(cs[k] / peak - 1), 6),
        }
    return res


def R(x):
    return None if x is None else round(float(x), 6)


def topk(frame: pl.DataFrame, gaincol: str) -> pl.DataFrame:
    # NOTE: no split exclusion here — split_flags is future-dependent
    # (its signature uses the stock's full-day intraday behavior at decision time),
    # so it is audit/sensitivity metadata only, never a causal admission criterion.
    f = frame.sort(gaincol, descending=True).head(K_TOP)
    return f.with_row_index("rank", offset=1)


# ---------------------------------------------------------------- day pipeline


def process_day(
    day: pl.DataFrame,
    day_str: str,
    prev_map: dict,
    pm_day: pl.DataFrame | None,
    max_days_flag: bool,
) -> dict:
    elig = pit_elig(day_str)
    if not elig:
        return {}
    split_set = split_excl(day_str)

    day = day.sort(["ticker", "et"])
    bounds = day.group_by("ticker", maintain_order=True).agg(pl.len().alias("n"))
    _t = bounds["ticker"].to_list()
    _n = bounds["n"].to_list()
    _s = np.cumsum([0] + _n[:-1])
    _e = np.cumsum(_n)
    SL = {t: (int(s), int(e)) for t, s, e in zip(_t, _s, _e)}
    A_et = day["et"].to_numpy()
    A_o = day["open"].to_numpy()
    A_h = day["high"].to_numpy()
    A_l = day["low"].to_numpy()
    A_c = day["close"].to_numpy()
    el = day.filter(pl.col("ticker").is_in(list(elig)))

    tick_list = sorted(prev_map.keys())
    pc = pl.DataFrame({"ticker": tick_list, "prev_close": [float(prev_map[t]) for t in tick_list]})

    o570 = el.filter(pl.col("et") == 570).select(
        "ticker", pl.col("open").alias("open0930")
    )
    n_elig = el["ticker"].n_unique()
    n_open = o570.height

    # ---- winners (session-max leaders), broad elig universe
    w = el.group_by("ticker").agg(
        pl.col("open").sort_by("et").first().alias("o_first"),
        pl.col("high").max().alias("hi"),
        pl.col("close").sort_by("et").last().alias("cl"),
        pl.col("et").min().alias("et0"),
        pl.col("et").max().alias("etN"),
    )
    w = w.join(o570, on="ticker", how="left").join(pc, on="ticker", how="left")
    w = w.filter(pl.col("o_first") > 0)
    w = w.with_columns(
        pl.col("open0930").fill_null(pl.col("o_first")).alias("anchor"),
        (pl.col("open0930").is_null()).alias("delayed_open"),
    )
    # 2026-09-21 repair: prev_close never gates open-anchored or B admission;
    # close-anchored (EOD) leaders are their own object.
    w = w.with_columns(
        (pl.col("hi") / pl.col("anchor") - 1).alias("gain_open"),
        (pl.col("hi") / pl.col("prev_close") - 1).alias("gain_prev"),
        (pl.col("cl") / pl.col("anchor") - 1).alias("eod_open"),
        (pl.col("cl") / pl.col("prev_close") - 1).alias("eod_prev"),
    )
    w = w.filter(pl.col("anchor") >= MIN_PRICE)
    wp_prev_ok = w.filter(pl.col("prev_close") > 0)
    win_o = w.sort("gain_open", descending=True).head(K_TOP)
    win_p = wp_prev_ok.sort("gain_prev", descending=True).head(K_TOP)
    win_co = w.sort("eod_open", descending=True).head(K_TOP)
    win_cp = wp_prev_ok.sort("eod_prev", descending=True).head(K_TOP)
    winners_open = [
        {"ticker": r["ticker"], "gain_open": R(r["gain_open"]), "gain_prev": R(r["gain_prev"]),
         "eod_open": R(r["eod_open"]), "et_hi": int(r["etN"]) if False else None,
         "delayed_open": bool(r["delayed_open"]),
         "split_flag": r["ticker"] in split_set}
        for r in win_o.iter_rows(named=True)
    ]
    winners_prev = [
        {"ticker": r["ticker"], "gain_prev": R(r["gain_prev"]), "gain_open": R(r["gain_open"]),
         "split_flag": r["ticker"] in split_set}
        for r in win_p.iter_rows(named=True)
    ]
    winners_close_open = [
        {"ticker": r["ticker"], "eod_open": R(r["eod_open"]), "gain_open": R(r["gain_open"]),
         "eod_prev": R(r["eod_prev"]), "split_flag": r["ticker"] in split_set}
        for r in win_co.iter_rows(named=True)
    ]
    winners_close_prev = [
        {"ticker": r["ticker"], "eod_prev": R(r["eod_prev"]), "eod_open": R(r["eod_open"]),
         "split_flag": r["ticker"] in split_set}
        for r in win_cp.iter_rows(named=True)
    ]

    # ---- snapshot candidate frames
    snaps: list[dict] = []
    pops = ["A_open"]
    if pm_day is not None:
        pops.append("A_pm")

    def emit(pop: str, T: int, frame: pl.DataFrame, pxcol: str, alt_anchor: str | None,
             fill_mode: str | None = None):
        names = []
        for r in frame.iter_rows(named=True):
            base = {
                "ticker": r["ticker"],
                "rank": int(r["rank"]),
                "sel": R(r["sel"]),
                "sel_alt": R(r["sel_alt"]) if "sel_alt" in r else None,
                "px_decision": R(r[pxcol]),
                "open0930": R(r["open0930"]) if r.get("open0930") is not None else None,
                "prev_close": R(r["prev_close"]) if r.get("prev_close") is not None else None,
                "split_flag": r["ticker"] in split_set,
            }
            base.update(attach_path(r["ticker"], fill_mode or pop, T, base))
            names.append(base)
        snaps.append({"pop": pop, "T": T, "names": names})

    def attach_path(ticker: str, mode: str, T: int, base: dict) -> dict:
        if ticker not in SL:
            # Pre-market-only name with no RTH bars that day: known at decision time,
            # but it cannot participate. Blocked slot, never a silent exclusion.
            return {"fill": None, "blocked": True, "reason": "no_rth_bars"}
        s, e = SL[ticker]
        ets = A_et[s:e]
        os_ = A_o[s:e]
        hs = A_h[s:e]
        ls = A_l[s:e]
        cs = A_c[s:e]
        fi = pick_fill(ets, mode, T)
        if fi is None or os_[fi] <= 0:
            return {"fill": None, "blocked": True}
        fill = float(os_[fi])
        target = 570 if mode == "A_pm" else (571 if mode == "A_open" else T)
        gap = int(ets[fi] - target)
        L, im, ia = ladders(ets, os_, hs, ls, cs, fi, fill)
        out = {
            "fill": {
                "et": int(ets[fi]), "px": R(fill), "gap_min": gap,
                "blocked": gap >= GAP_BLOCK,
                "alt_px": R(os_[fi + 1]) if (mode == "A_pm" and ets[fi] == 570 and fi + 1 < len(ets) and ets[fi + 1] == 571) else None,
            },
            "pre_high": R(hs[:fi].max()) if fi > 0 else None,
            "day_high": R(hs.max()), "close": R(cs[-1]),
            "eod_ret": R(cs[-1] / fill - 1),
            "mfe": R(hs[im] / fill - 1), "mae": R(ls[ia] / fill - 1),
            "i_mfe": int(im - fi), "i_mae": int(ia - fi),
            "last_et": int(ets[-1]), "bars": int(len(ets)),
            "gap_pre": ngaps(ets, 0, fi), "gap_post": ngaps(ets, fi, len(ets)),
            "ladders": L, "state": states_at(ets, cs, hs, fi, fill),
            "ratio_anchor": R(float(base["px_decision"]) / float(base["prev_close"])) if base.get("prev_close") else None,
        }
        return out

    # A_open
    ao = o570.join(pc, on="ticker", how="inner").filter(
        (pl.col("open0930") >= MIN_PRICE) & (pl.col("prev_close") > 0)
    )
    ao = ao.with_columns((pl.col("open0930") / pl.col("prev_close") - 1).alias("sel"))
    emit("A_open", 570, topk(ao, "sel"), "open0930", None)

    # A_pm (2025 only)
    if pm_day is not None and pm_day.height > 0:
        last = pm_day.group_by("ticker").agg(
            pl.col("close").sort_by("et").last().alias("px"),
            pl.col("et").max().alias("et_pm"),
        )
        ap = last.join(pc, on="ticker", how="inner").filter(
            (pl.col("px") >= MIN_PRICE) & (pl.col("prev_close") > 0)
            & ((569 - pl.col("et_pm")) <= FRESH_MIN)
        )
        ap = ap.join(o570, on="ticker", how="left")
        ap = ap.with_columns(
            (pl.col("px") / pl.col("prev_close") - 1).alias("sel"),
            (pl.col("open0930") / pl.col("px") - 1).alias("sel_alt"),
        )
        emit("A_pm", 570, topk(ap, "sel"), "px", None)
        emit("A_pm31", 570, topk(ap, "sel"), "px", None, fill_mode="A_open")

    # B(T)
    for T in T_LIST:
        dec = el.filter(pl.col("et") <= T - 1).group_by("ticker").agg(
            pl.col("close").sort_by("et").last().alias("px"),
            pl.col("et").max().alias("et_dec"),
        )
        b = dec.join(o570, on="ticker", how="inner").join(pc, on="ticker", how="left").filter(
            (pl.col("px") >= MIN_PRICE) & (pl.col("open0930") > 0)
        )
        b = b.with_columns(
            (pl.col("px") / pl.col("open0930") - 1).alias("sel"),
            (pl.col("px") / pl.col("prev_close") - 1).alias("sel_alt"),
        )
        emit("B", T, topk(b, "sel"), "px", "prev_close")

    # ---- audit fields
    ratio2plus = []
    for s in snaps:
        for n in s["names"]:
            ra = n.get("ratio_anchor")
            if ra is not None and ra >= RATIO_AUDIT:
                ratio2plus.append({"pop": s["pop"], "T": s["T"], "ticker": n["ticker"], "ratio": ra})

    cand_tickers = sorted({n["ticker"] for s in snaps for n in s["names"]}
                          | {w["ticker"] for w in winners_open})
    bars = day.filter(pl.col("ticker").is_in(cand_tickers)).select(
        "date", "ticker", "et", "open", "high", "low", "close", "volume"
    )

    return {
        "date": day_str,
        "audit": {
            "rows": int(day.height),
            "n_elig": int(n_elig),
            "n_open0930": int(n_open),
            "missing_0930": int(n_elig - n_open),
            "split_flag_today": len(split_set),
            "split_flagged_in_basket": sorted(set(cand_tickers) & split_set),
            "ratio2plus": ratio2plus,
        },
        "winners_open": winners_open,
        "winners_prev": winners_prev,
        "winners_close_open": winners_close_open,
        "winners_close_prev": winners_close_prev,
        "snapshots": snaps,
        "_bars": bars,
    }


# ---------------------------------------------------------------- self-test


def selftest() -> None:
    assert add_month("2026-01", -1) == "2025-12"
    assert add_month("2025-12", 1) == "2026-01"
    ets = [570, 571, 572]
    assert pick_fill(ets, "A_pm", 570) == 0
    assert pick_fill(ets, "A_open", 570) == 1
    assert pick_fill([570], "A_open", 570) is None
    assert pick_fill([584, 585, 586], "B", 585) == 1
    assert ngaps([570, 571, 575, 576, 577], 0, 5) == 1
    assert ngaps([570, 571, 572], 0, 3) == 0
    fill = 1.0
    L, im, ia = ladders(
        [570, 571, 572, 573], [1.0, 1.0, 1.0, 1.0],
        [1.06, 1.20, 1.0, 1.0], [0.94, 0.95, 0.90, 0.90],
        [1.0, 1.1, 0.95, 0.95], 0, fill,
    )
    assert L["up"]["5"]["i"] == 0 and L["dn"]["5"]["i"] == 0  # same-bar AMBIGUOUS
    assert L["up"]["20"]["i"] == 1
    assert L["dn"]["10"]["i"] == 2
    assert L["up"]["100"] is None
    assert im == 1 and ia == 2
    L2, _, _ = ladders(
        [570, 571, 572], [1.0, 1.0, 1.0], [1.0, 1.0, 1.2],
        [0.9, 0.9, 0.9], [1.0, 1.0, 1.2], 1, 1.0,
    )
    assert L2["up"]["5"]["i"] == 1
    assert L2["up"]["5"]["exec"] is None  # final-bar touch is non-executable
    st = states_at([570, 580, 600], [1.0, 1.10, 1.05], [1.0, 1.20, 1.10], 0, 1.0)
    assert st["575"]["ret"] == 0.0
    assert st["580"] is None  # bar 580 not completed by 580 under et<=t-1
    assert st["585"]["ret"] == 0.1
    assert abs(st["585"]["dd"] + 0.083333) < 1e-5
    assert st["615"]["ret"] == 0.05  # last completed bar 600 consumed at 10:15
    assert st["615"]["dd"] == -0.125
    assert st["630"] is None  # no new bar after 600

    import datetime as _dt
    day_str = "2021-02-01"
    elig = sorted(pit_elig(day_str))[:3]
    assert len(elig) == 3, "pit_elig must return symbols for 2021-02-01"
    t_a, t_b, _t_c = elig
    rows = []
    for tk in elig:
        for et, o, h, l, c in ((570, 10.0, 10.5, 9.9, 10.2), (571, 10.2, 11.0, 10.1, 10.8),
                               (600, 10.8, 30.0, 10.5, 25.0)):
            rows.append((_dt.date(2021, 2, 1), tk, et, float(o), float(h), float(l),
                         float(c), 1000.0))
    day = pl.DataFrame(rows, schema=["date", "ticker", "et", "open", "high", "low",
                                     "close", "volume"], orient="row")
    pm = pl.DataFrame({"ticker": [t_a, t_b], "et": [569, 550], "close": [12.0, 6.0]})
    rec = process_day(day, day_str, {t_a: 5.0}, pm, max_days_flag=False)
    snaps = {(s["pop"], s["T"]): s for s in rec["snapshots"]}
    assert ("A_pm", 570) in snaps and ("A_pm31", 570) in snaps
    m31 = snaps[("A_pm31", 570)]["names"][0]
    assert m31["ticker"] == t_a and m31["fill"]["et"] == 571, m31
    assert snaps[("A_pm", 570)]["names"][0]["fill"]["et"] == 570
    bnames = snaps[("B", 600)]["names"]
    assert {n["ticker"] for n in bnames} == set(elig), bnames
    assert sum(1 for n in bnames if n.get("prev_close") is None) == 2, bnames
    assert len(rec["winners_close_open"]) == 3 and len(rec["winners_close_prev"]) == 1
    print("self-test OK")


# ---------------------------------------------------------------- main


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", nargs="*", default=None)
    ap.add_argument("--all-dev", action="store_true")
    ap.add_argument("--max-days", type=int, default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--allow-reserved", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)

    if args.self_test:
        selftest()
        return

    months = args.months or []
    if args.all_dev:
        found = []
        for p in DATA.glob("clean_ohlcv_*.parquet"):
            found.append(p.stem.replace("clean_ohlcv_", ""))
        for p in (DATA / "backfill").glob("clean_ohlcv_*.parquet"):
            found.append(p.stem.replace("clean_ohlcv_", ""))
        months = sorted(set(found))

    months = [m for m in sorted(set(months)) if m <= DEV_END or args.allow_reserved]
    skip_reserved = [m for m in sorted(set(args.months or [])) if m in RESERVED and not args.allow_reserved]
    if skip_reserved:
        print(f"[boundary] reserved months excluded (PRE-REG §5): {skip_reserved}")
    if not months:
        ap.error("no months selected")

    outd = Path(args.out)
    (outd / "anatomy").mkdir(parents=True, exist_ok=True)
    (outd / "bars").mkdir(parents=True, exist_ok=True)

    carry: dict = {}
    last_month = None
    for month in months:
        mpath = month_path(month)
        if not mpath.exists():
            print(f"[skip] no file for {month}")
            continue
        prev = add_month(month, -1)
        if last_month != prev or not carry:
            carry = last_closes_of_month(prev)
            if not carry:
                print(f"[seed] {month}: no prior-month closes; first day will be skipped")
        lf = load_month_lazy(month)
        pm = load_pm_month(month)
        days = month_days(month)
        if args.max_days:
            days = days[: args.max_days]
        for chunk in [days[i:i + 2] for i in range(0, len(days), 2)]:
            wdf = lf.filter(pl.col("date").is_in(chunk)).collect()
            for d in chunk:
                day_str = str(d)
                jl = outd / "anatomy" / f"{day_str}.jsonl"
                bp = outd / "bars" / f"{day_str}.parquet"
                day = wdf.filter(pl.col("date") == d).unique(
                    subset=["timestamp", "ticker"], keep="first"
                )
                if day.height == 0:
                    continue
                last = day.group_by("ticker").agg(
                    pl.col("close").sort_by("et").last().alias("c")
                )
                carry_next = dict(zip(last["ticker"].to_list(), last["c"].to_list()))
                if jl.exists() and not args.force:
                    carry = carry_next
                    continue
                if not carry:
                    print(f"[skip] {day_str}: no prev close seed (first day of a data stretch)")
                    carry = carry_next
                    continue
                pm_day = pm.filter(pl.col("date") == d) if pm is not None else None
                rec = process_day(day, day_str, carry, pm_day, args.max_days is not None)
                if not rec:
                    carry = carry_next
                    continue
                bars = rec.pop("_bars")
                tmp_j = jl.with_suffix(".jsonl.tmp")
                with open(tmp_j, "w") as fh:
                    fh.write(json.dumps(rec, separators=(",", ":"), default=str) + "\n")
                os.replace(tmp_j, jl)  # atomic: a kill can never leave a truncated day file
                tmp_b = bp.with_suffix(".parquet.tmp")
                bars.write_parquet(tmp_b)
                os.replace(tmp_b, bp)
                n_cand = sum(len(s["names"]) for s in rec["snapshots"])
                print(f"{day_str}: snaps={len(rec['snapshots'])} cands={n_cand} "
                      f"elig={rec['audit']['n_elig']} missing0930={rec['audit']['missing_0930']} "
                      f"ratio2plus={len(rec['audit']['ratio2plus'])}")
                carry = carry_next
        last_month = month
        print(f"[month done] {month}")


if __name__ == "__main__":
    main()
