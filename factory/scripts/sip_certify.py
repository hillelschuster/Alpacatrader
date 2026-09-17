#!/usr/bin/env python3
"""SIP certification layer (BASKET-01 data upgrade) — compare SIP-derived facts with the
existing HF/clean-based anatomy, day by day.

This script does NOT modify the thesis, populations, rulers, or any parameter. It answers
the certification questions (user directive §3/§6/§7):
  A selection   — would SIP-derived prices have changed basket membership/rank?
  B path        — do MFE/MAE/time-to-high agree cell-by-cell?
  C first-passage — does subminute SIP sequencing resolve the minute-bar AMBIGUOUS cases,
                     and do any stored orderings actually flip?
  D execution   — prevailing bid/ask at entry, spread, sell-at-bid accessibility at touches,
                     realized breach prints vs min(open, level), halt/reopen gaps
  E tail        — do stored extreme prints survive SIP inspection (BRP-type checks)

Layers kept separate: raw SIP events -> SIP-derived bars -> market state -> assumed fills
(stored). All comparisons are read-only; nothing is redefined.

Outputs (compact; intended for commit):
  factory/artifacts/basket/sip/certification_<day>.json
  factory/artifacts/basket/sip/certification_panel.json      (--summary)

Usage:
  .venv/bin/python factory/scripts/sip_certify.py --self-test
  .venv/bin/python factory/scripts/sip_certify.py --day 2021-02-01
  .venv/bin/python factory/scripts/sip_certify.py --all
  .venv/bin/python factory/scripts/sip_certify.py --summary
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sip_bars as sb  # noqa: E402

SIP = ROOT / "data" / "sip"
ANAT = ROOT / "factory" / "artifacts" / "basket" / "anatomy"
OUT = ROOT / "factory" / "artifacts" / "basket" / "sip"
UP = [5, 10, 20, 30, 50, 100]
DN = [3, 5, 8, 10, 15]
T_LIST = [575, 580, 585, 590, 595, 600, 615, 630, 645, 660, 690, 720]


def R(x):
    return None if x is None else round(float(x), 6)


# ---------------------------------------------------------------- pure helpers


def first_ge(ts: np.ndarray, t):
    if len(ts) == 0:
        return None
    i = int(np.searchsorted(ts, t, side="left"))
    return i if i < len(ts) else None


def quote_at(ts_q: np.ndarray, q: dict, t):
    """Prevailing quote at t: last quote with ts <= t (searchsorted right - 1)."""
    if len(ts_q) == 0:
        return None
    i = int(np.searchsorted(ts_q, t, side="right")) - 1
    if i < 0:
        return None
    return {k: q[k][i] for k in ("bid_price", "bid_size", "ask_price", "ask_size")}


def first_touch_ts(ts: np.ndarray, px: np.ndarray, thr: float, side: str):
    """First trade ts with price >= thr (up) or <= thr (dn)."""
    if len(ts) == 0:
        return None
    if side == "up":
        idx = np.flatnonzero(px >= thr)
    else:
        idx = np.flatnonzero(px <= thr)
    return float(ts[idx[0]]) if len(idx) else None


def resolve_order(up_ts, dn_ts):
    """Subminute ordering from trade timestamps: 'up' | 'dn' | 'amb' | 'up_only' | 'dn_only' | 'neither'."""
    if up_ts is None and dn_ts is None:
        return "neither"
    if dn_ts is None:
        return "up_only"
    if up_ts is None:
        return "dn_only"
    if up_ts < dn_ts:
        return "up"
    if dn_ts < up_ts:
        return "dn"
    return "amb"


def bar_path_stats(sym_bars: pl.DataFrame, fill_et: int, fill_px: float):
    """From SIP-derived bars (sorted by et_min): mfe/mae/i_mfe/i_mae + ladder first touches."""
    b = sym_bars.filter(pl.col("et_min") >= fill_et)
    if b.height == 0:
        return None
    h = b["h"].to_numpy(); lo = b["l"].to_numpy()
    ets = b["et_min"].to_numpy()
    im = int(np.argmax(h)); ia = int(np.argmin(lo))
    out = {"mfe": R(h[im] / fill_px - 1), "mae": R(lo[ia] / fill_px - 1),
           "i_mfe": int(im), "i_mae": int(ia),
           "et_mfe": int(ets[im]), "et_mae": int(ets[ia]), "bars": int(b.height)}
    for H in UP:
        thr = fill_px * (1 + H / 100.0)
        idx = int(np.flatnonzero(h >= thr)[0]) if (h >= thr).any() else None
        out[f"up{H}"] = None if idx is None else {"i": idx, "et": int(ets[idx]), "touch": R(h[idx])}
    for L in DN:
        thr = fill_px * (1 - L / 100.0)
        idx = int(np.flatnonzero(lo <= thr)[0]) if (lo <= thr).any() else None
        out[f"dn{L}"] = None if idx is None else {"i": idx, "et": int(ets[idx]), "touch": R(lo[idx])}
    return out


def order_from_stats(st, H, L):
    up = st[f"up{H}"]; dn = st[f"dn{L}"]
    if up is None and dn is None:
        return "neither"
    if dn is None:
        return "up_only"
    if up is None:
        return "dn_only"
    if up["i"] < dn["i"]:
        return "up"
    if dn["i"] < up["i"]:
        return "dn"
    return "amb"


# ---------------------------------------------------------------- day pipeline


def load_day(day: str):
    tp = SIP / "trades" / f"{day}.parquet"
    qp = SIP / "quotes" / f"{day}.parquet"
    if not tp.exists():
        raise SystemExit(f"missing {tp}")
    trades = pl.read_parquet(tp)
    quotes = pl.read_parquet(qp) if qp.exists() else None
    return trades, quotes


def groups_by(df: pl.DataFrame, col: str = "symbol") -> dict:
    """group_by iteration keys are tuples in this polars version -> normalize to scalars."""
    out = {}
    for key, g in df.group_by(col, maintain_order=True):
        k = key[0] if isinstance(key, tuple) else key
        out[k] = g
    return out


def price_updating_mask(trades: pl.DataFrame) -> pl.Series:
    """Condition codes whose minute-bar rule updates at least one price field (oc>0 or hl>0)."""
    uniq = trades.select(["conditions", "tape"]).unique()
    ok = set()
    for conds, tape in uniq.iter_rows():
        oc, hl, v, unk = sb.combine(conds if conds else [], tape)
        if oc > 0 or hl > 0:
            ok.add(tuple(sorted(conds if conds else [])))
    return trades.with_columns(
        pl.col("conditions").list.sort().cast(pl.List(pl.Utf8)).list.join("|").alias("_ck")
    ).with_columns(
        pl.col("_ck").cast(pl.Utf8).is_in(["|".join(k) for k in ok]).alias("_po"),
    ).with_columns(
        (pl.col("ts_utc").dt.convert_time_zone("America/New_York").dt.hour().cast(pl.Int32) * 60
         + pl.col("ts_utc").dt.convert_time_zone("America/New_York").dt.minute().cast(pl.Int32)
         ).alias("et_min"),
    )


def certify_day(day: str) -> dict:
    rec = json.loads(Path(ANAT / f"{day}.jsonl").read_text())
    trades, quotes = load_day(day)
    bars = sb.build_bars(trades, "alpaca")
    syms = sorted(trades["symbol"].unique().to_list())

    out = {"day": day, "symbols": len(syms), "trades_rows": trades.height,
           "quotes_rows": 0 if quotes is None else quotes.height,
           "selection": [], "path": [], "exec": [], "tail": {}, "notes": []}

    # ---- per-symbol arrays (sorted)
    trades = trades.sort(["symbol", "ts_utc"])
    po = price_updating_mask(trades)
    tr = groups_by(trades)
    po_df = po.sort(["symbol", "ts_utc"])
    tr_po = groups_by(po_df)
    bars_by = groups_by(bars)

    def sym_arrays(sym, frame):
        g = frame.get(sym)
        if g is None or g.height == 0:
            return None
        ts = g["ts_utc"].dt.timestamp("us").to_numpy().astype(np.float64)
        return ts, g["price"].to_numpy().astype(np.float64)

    # ---- E tail integrity per symbol (RTH + price-updating vs raw window, kept separate)
    tail = {}
    for s in syms:
        a = sym_arrays(s, tr)
        g_po = tr_po.get(s)
        b = bars_by.get(s)
        brth = b.filter(pl.col("sess") == "rth") if b is not None and b.height else None
        sip_trade_max = R(float(a[1].max())) if a else None
        sip_trade_max_rth_po = None
        if g_po is not None and g_po.height:
            m = g_po.filter(pl.col("et_min") < 960)
            if m.height:
                sip_trade_max_rth_po = R(float(m["price"].max()))
        sip_bar_max_rth = R(float(brth["h"].max())) if brth is not None and brth.height else None
        tail[s] = {"sip_trade_max_raw": sip_trade_max,
                   "sip_trade_max_rth_priceupd": sip_trade_max_rth_po,
                   "sip_bar_max_rth": sip_bar_max_rth}
    out["tail"] = tail

    # stored per-name day_high for cross-checks (from snapshots / winners)
    stored_high = {}
    for s in rec["snapshots"]:
        for n in s["names"]:
            stored_high[n["ticker"]] = n.get("day_high")
    tail_cmp = []
    for s, v in tail.items():
        sh = stored_high.get(s)
        ref = v["sip_trade_max_rth_priceupd"] if v["sip_trade_max_rth_priceupd"] is not None else v["sip_bar_max_rth"]
        if sh is None or ref is None:
            continue
        d = (ref / sh - 1) if sh else None
        if d is not None and abs(d) > 0.005:
            tail_cmp.append({"ticker": s, "stored_high": sh, "sip_rth_po_max": ref,
                             "raw_window_max": v["sip_trade_max_raw"], "delta": R(d)})
    out["tail"] = {"symbols": tail, "diffs_vs_stored_high": sorted(
        tail_cmp, key=lambda x: -abs(x["delta"]))[:15]}

    # ---- A selection: per snapshot, within the fetched set
    def sip_px(sym, T):
        """B decision px from SIP bars: last completed bar close with et_min <= T-1."""
        g = bars_by.get(sym)
        if g is None or g.height == 0:
            return None
        g2 = g.filter(pl.col("et_min") <= T - 1)
        if g2.height == 0:
            return None
        return float(g2["c"][-1])

    def sip_open(sym):
        g = bars_by.get(sym)
        if g is None or g.height == 0:
            return None
        rth = g.filter(pl.col("sess") == "rth")
        if rth.height == 0:
            return None
        r0 = rth.filter(pl.col("et_min") == 570)
        return float((r0 if r0.height else rth)["o"][0])

    for snap in rec["snapshots"]:
        pop, T = snap["pop"], snap["T"]
        stored = [{"rank": n["rank"], "ticker": n["ticker"], "px": n["px_decision"],
                   "sel": n["sel"], "prev_close": n.get("prev_close")} for n in snap["names"][:10]]
        stored_set = {n["ticker"] for n in stored}
        pc_by = {n["ticker"]: n["prev_close"] for n in stored}
        sip_rows = []
        for s in syms:
            o = sip_open(s)
            if not o:
                continue
            if pop == "B":
                px = sip_px(s, T)
                sel = None if px is None else px / o - 1
            elif pop == "A_open":
                px = o if T == 570 else sip_px(s, T)
                pc = pc_by.get(s)
                sel = None if (px is None or not pc) else px / pc - 1
            else:  # A_pm
                px = None
                sel = None
            if px is None or sel is None:
                continue
            sip_rows.append({"ticker": s, "px_sip": R(px), "open_sip": R(o), "sel_sip": R(sel)})
        if pop == "A_pm":
            out["selection"].append({"pop": pop, "T": T, "skipped": "A_pm pilot: premarket ranking not re-derived"})
            continue
        sip_rows.sort(key=lambda r: -r["sel_sip"])
        sip_sub = sorted([r for r in sip_rows if r["ticker"] in stored_set],
                         key=lambda r: -r["sel_sip"])
        stored_order = [st["ticker"] for st in stored]
        sip_order_in = [r["ticker"] for r in sip_sub]
        flips = sum(1 for a, b in zip(stored_order, sip_order_in) if a != b)
        deltas = []
        for st in stored:
            r = next((x for x in sip_rows if x["ticker"] == st["ticker"]), None)
            if r and st["px"]:
                deltas.append(abs(r["px_sip"] / st["px"] - 1))
        outsiders = []
        if sip_sub:
            cutoff = sip_sub[-1]["sel_sip"]
            outsiders = sorted([r for r in sip_rows
                                if r["ticker"] not in stored_set and r["sel_sip"] >= cutoff],
                               key=lambda r: -r["sel_sip"])
        out["selection"].append({
            "pop": pop, "T": T, "stored_top10": stored,
            "sip_order_of_stored_top10": sip_order_in,
            "rank_flip_positions_within_stored_top10": flips,
            "outsiders_above_cutoff": [{"ticker": r["ticker"], "sel_sip": r["sel_sip"]}
                                       for r in outsiders[:10]],
            "px_delta_bps": {"n": len(deltas),
                             "p50": R(np.percentile(deltas, 50) * 1e4) if deltas else None,
                             "max": R(max(deltas) * 1e4) if deltas else None},
            "note": ("re-rank of the stored top-10 by SIP prices; outsiders = fetched symbols above "
                     "the 10th stored member's SIP score (lower bound; unfetched names not re-ranked)")})

    # ---- B/C/D per main top-3 member
    for snap in rec["snapshots"]:
        pop, T = snap["pop"], snap["T"]
        if pop == "A_pm":
            continue
        for n in snap["names"][:3]:
            sym = n["ticker"]
            f = n.get("fill")
            if not f or f.get("blocked") or not f.get("px"):
                continue
            fill_px = float(f["px"])
            g = bars_by.get(sym)
            if g is None or g.height == 0:
                continue
            if pop == "A_open":
                g2 = g.filter(pl.col("et_min") >= 571)
            else:
                g2 = g.filter(pl.col("et_min") >= T)
            if g2.height == 0:
                continue
            fill_et = int(g2["et_min"][0]); fill_sip = float(g2["o"][0])
            st = bar_path_stats(g, fill_et, fill_sip)
            stored_order = {}
            sip_order = {}
            for H in UP:
                for L in DN:
                    u = n["ladders"]["up"][str(H)]; d = n["ladders"]["dn"][str(L)]
                    if u is None and d is None:
                        stored_order[(H, L)] = "neither"
                    elif d is None:
                        stored_order[(H, L)] = "up_only"
                    elif u is None:
                        stored_order[(H, L)] = "dn_only"
                    elif u["i"] < d["i"]:
                        stored_order[(H, L)] = "up"
                    elif d["i"] < u["i"]:
                        stored_order[(H, L)] = "dn"
                    else:
                        stored_order[(H, L)] = "amb"
                    sip_order[(H, L)] = order_from_stats(st, H, L)
            flips = [f"{H}/{L}:{stored_order[(H, L)]}->{sip_order[(H, L)]}"
                     for (H, L) in stored_order
                     if stored_order[(H, L)] not in ("amb",) and sip_order[(H, L)] != stored_order[(H, L)]
                     and sip_order[(H, L)] not in ("neither",)]
            amb_res = {}
            a_ts = sym_arrays(sym, tr); a_ts_po = sym_arrays(sym, tr_po)
            fill_ts_us = float(g2["first_ts"][0].timestamp() * 1e6) if "first_ts" in g2.columns else None
            for H in UP:
                for L in DN:
                    if stored_order[(H, L)] != "amb":
                        continue
                    r = {}
                    for name, arr in (("raw", a_ts), ("price_upd", a_ts_po)):
                        if arr is None or fill_ts_us is None:
                            r[name] = None
                            continue
                        ts, px = arr
                        i0 = int(np.searchsorted(ts, fill_ts_us, side="left"))
                        ts2, px2 = ts[i0:], px[i0:]
                        u = first_touch_ts(ts2, px2, fill_px * (1 + H / 100), "up")
                        d = first_touch_ts(ts2, px2, fill_px * (1 - L / 100), "dn")
                        r[name] = resolve_order(u, d)
                    amb_res[f"{H}/{L}"] = r
            # D execution truth at fill
            qrow = None
            if quotes is not None:
                qs = quotes.filter(pl.col("symbol") == sym).sort("ts_utc")
                if qs.height:
                    tsq = qs["ts_utc"].dt.timestamp("us").to_numpy().astype(np.float64)
                    qdict = {k: qs[k].to_numpy() for k in ("bid_price", "bid_size", "ask_price", "ask_size")}
                    qrow = quote_at(tsq, qdict, fill_ts_us)
            spread_bps = None
            if qrow and qrow["bid_price"] and qrow["ask_price"]:
                mid = (qrow["bid_price"] + qrow["ask_price"]) / 2
                spread_bps = R((qrow["ask_price"] - qrow["bid_price"]) / mid * 1e4)
            out["path"].append({
                "pop": pop, "T": T, "ticker": sym,
                "fill_stored": R(fill_px), "fill_sip": R(fill_sip),
                "fill_delta_bps": R((fill_sip / fill_px - 1) * 1e4),
                "mfe_stored": n.get("mfe"), "mfe_sip": st["mfe"],
                "mae_stored": n.get("mae"), "mae_sip": st["mae"],
                "i_mfe_stored": n.get("i_mfe"), "i_mfe_sip": st["i_mfe"],
                "order_flips_nonamb": flips[:8],
                "amb_resolved": amb_res,
            })
            out["exec"].append({
                "pop": pop, "T": T, "ticker": sym,
                "quote_at_fill": None if qrow is None else {
                    "bid": R(qrow["bid_price"]), "ask": R(qrow["ask_price"]),
                    "bid_sz": qrow["bid_size"], "ask_sz": qrow["ask_size"]},
                "spread_bps": spread_bps,
                "buy_cross_bps": None if not qrow or not qrow["ask_price"] else R((qrow["ask_price"] / fill_px - 1) * 1e4),
            })
    return out


def summarize(per_day: list):
    s = {"days": len(per_day), "selection": {}, "path": {}, "exec": {}, "tail": {}}
    flips_tot = sel_snaps = 0
    px_deltas = []
    fill_deltas = []
    mfe_deltas = []
    flips_n = 0
    amb_total = amb_res = 0
    spreads = []
    tail_diff_n = 0
    tail_diff_max = 0.0
    for d in per_day:
        for row in d["selection"]:
            if row.get("skipped"):
                continue
            sel_snaps += 1
            flips_tot += row["rank_flip_positions_within_stored_top10"]
            if row["px_delta_bps"]["p50"] is not None:
                px_deltas.append(row["px_delta_bps"]["p50"])
        for row in d["path"]:
            if row["fill_delta_bps"] is not None:
                fill_deltas.append(row["fill_delta_bps"])
            if row["mfe_stored"] is not None and row["mfe_sip"] is not None:
                mfe_deltas.append((row["mfe_sip"] - row["mfe_stored"]) * 1e4)
            flips_n += len(row["order_flips_nonamb"])
            for k, v in row["amb_resolved"].items():
                amb_total += 1
                vals = [v.get("raw"), v.get("price_upd")]
                if any(x in ("up", "dn", "up_only", "dn_only") for x in vals):
                    amb_res += 1
        for row in d["exec"]:
            if row["spread_bps"] is not None:
                spreads.append(row["spread_bps"])
        for x in d["tail"]["diffs_vs_stored_high"]:
            tail_diff_n += 1
            tail_diff_max = max(tail_diff_max, abs(x["delta"]))
    s["selection"] = {"snapshots": sel_snaps, "rank_flip_positions_total": flips_tot,
                      "px_delta_bps_p50_of_p50s": R(np.percentile(px_deltas, 50)) if px_deltas else None}
    s["path"] = {"members": len(fill_deltas),
                 "fill_delta_bps": {"p50": R(np.percentile(fill_deltas, 50)) if fill_deltas else None,
                                    "p90": R(np.percentile(fill_deltas, 90)) if fill_deltas else None,
                                    "max_abs": R(max([abs(x) for x in fill_deltas])) if fill_deltas else None},
                 "mfe_delta_bps": {"p50": R(np.percentile(mfe_deltas, 50)) if mfe_deltas else None,
                                   "p90": R(np.percentile(mfe_deltas, 90)) if mfe_deltas else None,
                                   "max_abs": R(max([abs(x) for x in mfe_deltas])) if mfe_deltas else None},
                 "nonambiguous_order_flips": flips_n,
                 "amb_cases": amb_total, "amb_resolved_by_trades": amb_res}
    s["exec"] = {"members": len(spreads), "spread_bps": {
        "p50": R(np.percentile(spreads, 50)) if spreads else None,
        "p90": R(np.percentile(spreads, 90)) if spreads else None} if spreads else None}
    s["tail"] = {"days": len(per_day), "symbol_tail_diffs_total": tail_diff_n,
                 "max_abs_delta": R(tail_diff_max) if tail_diff_n else None}
    return s


def why(day: str, ticker: str, et_pad: int = 3):
    """Human-review printer: stored (HF) vs SIP bars/trades around the member's fill."""
    rec = json.loads(Path(ANAT / f"{day}.jsonl").read_text())
    rows = []
    for s in rec["snapshots"]:
        for n in s["names"]:
            if n["ticker"] == ticker and n.get("fill") and not n["fill"].get("blocked") and n["fill"].get("px"):
                rows.append((s["pop"], s["T"], n))
    if not rows:
        print(f"{ticker} {day}: no filled member rows")
        return
    for pop, T, n in rows[:4]:
        fe = int(n["fill"]["et"])
        print(f"[{pop} T={T}] stored fill {n['fill']['px']} @et={fe} gap={n['fill']['gap_min']} "
              f"mfe={n['mfe']} mae={n['mae']}")
        m = day[:7]
        for name, path in (("HF", ROOT / "data" / f"clean_ohlcv_{m}.parquet"),
                           ("HFraw", ROOT / "data" / f"ohlcv_{m}.parquet")):
            if path.exists():
                df = pl.read_parquet(path).filter(pl.col("ticker") == ticker)
                df = df.with_columns(pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("te"))
                df = df.with_columns((pl.col("te").dt.hour().cast(pl.Int32) * 60
                                      + pl.col("te").dt.minute().cast(pl.Int32)).alias("et"))
                df = df.filter((pl.col("te").dt.date().cast(pl.Utf8) == day)
                               & (pl.col("et") >= fe - et_pad) & (pl.col("et") <= fe + et_pad))
                print(f"  {name} bars et {fe-et_pad}..{fe+et_pad}:")
                for r in df.sort("et").iter_rows(named=True):
                    print(f"    {r['et']} o={r['open']} h={r['high']} l={r['low']} c={r['close']} v={r['volume']}")
        trades, _ = load_day(day)
        b = sb.build_bars(trades.filter(pl.col("symbol") == ticker), "alpaca")
        b = b.filter((pl.col("et_min") >= fe - et_pad) & (pl.col("et_min") <= fe + et_pad))
        print(f"  SIP bars et {fe-et_pad}..{fe+et_pad}:")
        for r in b.sort("et_min").iter_rows(named=True):
            print(f"    {r['et_min']} o={r['o']} h={r['h']} l={r['l']} c={r['c']} v={r['v']}")
        print()


def selftest():
    from datetime import datetime, timezone
    mk = lambda h, m, s_=0, us=0: datetime(2021, 2, 1, h, m, s_, us, tzinfo=timezone.utc)
    ts = np.array([mk(14, 30).timestamp() * 1e6, mk(14, 31).timestamp() * 1e6])
    px = np.array([10.0, 10.5])
    assert first_touch_ts(ts, px, 10.4, "up") == ts[1]
    assert first_touch_ts(ts, px, 9.9, "dn") is None
    assert resolve_order(ts[0], ts[1]) == "up"
    assert resolve_order(ts[1], ts[0]) == "dn"
    assert resolve_order(None, ts[0]) == "dn_only"
    assert resolve_order(None, None) == "neither"
    qts = np.array([mk(14, 29).timestamp() * 1e6, mk(14, 30, 30).timestamp() * 1e6])
    q = {"bid_price": np.array([9.9, 10.1]), "bid_size": np.array([100, 200]),
         "ask_price": np.array([10.1, 10.3]), "ask_size": np.array([100, 200])}
    assert quote_at(qts, q, ts[0])["bid_price"] == 9.9
    assert quote_at(qts, q, ts[1])["ask_price"] == 10.3
    bars = pl.DataFrame({
        "symbol": ["AAA"] * 3, "et_min": [570, 571, 572], "sess": ["rth"] * 3,
        "o": [10.0, 10.2, 10.4], "h": [10.1, 10.8, 10.5],
        "l": [9.9, 10.0, 9.5], "c": [10.0, 10.5, 10.0], "v": [1, 2, 3],
    })
    st = bar_path_stats(bars, 571, 10.2)
    assert st["mfe"] == round(10.8 / 10.2 - 1, 6), st
    assert st["up5"]["et"] == 571 and st["dn5"]["et"] == 572, st
    assert order_from_stats(st, 5, 5) == "up"
    print("self-test OK")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--day")
    ap.add_argument("--why")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        selftest()
        return
    if args.why:
        if not args.day:
            raise SystemExit("--why requires --day")
        why(args.day, args.why)
        return
    OUT.mkdir(parents=True, exist_ok=True)
    if args.summary:
        files = sorted(glob.glob(str(OUT / "certification_2*.json")))
        per = [json.loads(Path(f).read_text()) for f in files]
        s = summarize(per)
        with open(OUT / "certification_panel.json", "w") as fh:
            json.dump({"panel": s,
                       "days": [d["day"] for d in per]}, fh, indent=1, default=str)
        print(f"summary over {len(per)} days -> certification_panel.json")
        print(json.dumps(s, indent=1, default=str))
        return
    days = []
    if args.day:
        days = [args.day]
    elif args.all:
        days = sorted(Path(p).name[:10] for p in glob.glob(str(SIP / "trades" / "*.parquet")))
        days = [d for d in days if (SIP / "quotes" / f"{d}.parquet").exists()]
    if not days:
        raise SystemExit("select --day or --all")
    for d in days:
        op = OUT / f"certification_{d}.json"
        if op.exists() and not args.force:
            print(f"{d}: exists (skip)")
            continue
        res = certify_day(d)
        with open(op, "w") as fh:
            json.dump(res, fh, indent=1, default=str)
        n_path = len(res["path"])
        flips = sum(r["rank_flip_positions_within_stored_top10"]
                    for r in res["selection"] if not r.get("skipped"))
        tdiff = len(res["tail"]["diffs_vs_stored_high"])
        print(f"{d}: members={n_path} rank_flip_positions={flips} tail_diffs={tdiff} -> {op.name}")


if __name__ == "__main__":
    main()
