"""Stage-A core experiment: discovery + opportunity-state + leader-health vs baselines.

Compares, per day at snapshot t (default 10:00 ET, path from 09:45 ET):
  baselines : cur1, top3/5/10/20 raw gain
  +sep      : top5 gain ordered/filtered by price separation
  +opp      : opportunity-state gate (n10 / top1-share / churn variants + shapes)
  +health   : leader-health filter (VWAP-hold, drawdown cap, accel cap variants)
  +emerge   : emerging-mover lane (rank climbers from outside top-10)
Outcomes per name: E0 fwd/MFE/MAE; day aggregates; SELECTED vs REJECTED (top-20
minus selected) comparison. Multi-block reporting. Shapes, not cutoffs.

Usage:
  python factory/scripts/stagea_eval.py --months 2025-03 --max-days 3
"""
from __future__ import annotations
import argparse
import statistics as st
from pathlib import Path

from replay_watchlist import (load_day, snapshot, outcome_E0, entries_E1,  # noqa: E402
                              entries_E2, entries_E3)

T_SNAP, T_PATH = 600, 585  # ET minutes: 10:00 / 09:45 ET (executable: bars <= t-1)


def day_state(e15, e1445):
    """Opportunity-state vars at snapshot. e15/e1445: snapshot frames."""
    top20 = e15.head(20)
    gains = top20["gain"]
    n10 = int((gains > 0.10).sum())
    n5 = int((gains > 0.05).sum())
    cdv = top20["cdv"]
    tot = cdv.sum()
    shares = cdv / tot if tot > 0 else cdv * 0
    gain1_share = float(shares.iloc[0]) if len(shares) else 0.0  # gain-#1's $-share
    hhi = float((shares ** 2).sum())
    diff_pp = float((top20["gain"].iloc[0] - top20["gain"].iloc[1]) * 100) if len(top20) > 1 else 0.0
    # churn: 1 - spearman(rank1445, rank15) over union top-20 symbols
    u = list(dict.fromkeys(list(e1445.head(20).index) + list(top20.index)))
    r1 = {s: i for i, s in enumerate(e1445["gain"].sort_values(ascending=False).index) if s in u}
    r2 = {s: i for i, s in enumerate(e15["gain"].sort_values(ascending=False).index) if s in u}
    common = [s for s in u if s in r1 and s in r2]
    if len(common) > 3:
        import numpy as np
        a = np.array([r1[s] for s in common], dtype=float)
        b = np.array([r2[s] for s in common], dtype=float)
        ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
        c = np.corrcoef(ra, rb)[0, 1]
        churn = 1.0 - float(c) if c == c and abs(float(c)) <= 1 else 0.0
    else:
        churn = 0.0
    held = e15[e15["pc"] > e15["vwap"]]
    vhold = float(len(held.head(10)) / 10)
    return {"n10": n10, "n5": n5, "top1sh": round(gain1_share, 3), "hhi": round(hhi, 3),
            "diff_pp": round(diff_pp, 2), "churn": round(float(churn), 3),
            "vhold": round(vhold, 3)}


def health(e15, e1445, sym):
    """Path/health features for one name (bars <= snapshot only)."""
    try:
        r = e15.loc[sym]
        g15, vwap_d = float(r["gain"]), float(r["pc"] / r["vwap"] - 1)
        dd = float(r["pc"] / r["ph"] - 1)
        g45 = float(e1445.loc[sym]["gain"]) if sym in e1445.index else 0.0
        v_early, v_late = g45 / 15.0, (g15 - g45) / 15.0
        return {"gain": g15, "vwap_d": vwap_d, "dd": dd,
                "accel": bool(v_late > v_early), "vlate": v_late, "vearly": v_early}
    except KeyError:
        return None


def build_lists(e15, e1445):
    top = list(e15.head(20).index)
    lists = {"cur1": top[:1], "top3": top[:3], "top5": top[:5],
             "top10": top[:10], "top20": top[:20]}
    # separation shortlist: top5 by gain, keep 3 with biggest lead over next
    t5 = e15.head(5)
    leads = {s: float(t5["gain"].iloc[i] - t5["gain"].iloc[i + 1])
             for i, s in enumerate(t5.index) if i + 1 < len(t5)}
    lists["sep3"] = sorted(leads, key=leads.get, reverse=True)[:3]
    # emerging lane: outside top-10 at 14:45, top-10 at 15:00, gain>5%, above VWAP
    r45 = {s: i for i, s in enumerate(e1445["gain"].sort_values(ascending=False).index)}
    r15 = {s: i for i, s in enumerate(e15["gain"].sort_values(ascending=False).index)}
    em = [s for s in e15.head(10).index
          if r45.get(s, 999) >= 10 and float(e15.loc[s]["gain"]) > 0.05
          and float(e15.loc[s]["pc"]) > float(e15.loc[s]["vwap"])]
    lists["emerge"] = em
    lists["top5+emerge"] = list(dict.fromkeys(top[:5] + em))
    return lists


def health_filter(syms, e15, e1445, mode):
    if mode == "none":
        return syms
    out = []
    for s in syms:
        h = health(e15, e1445, s)
        if not h:
            continue
        if h["vwap_d"] < 0:
            continue
        if h["dd"] < -0.10:
            continue
        if mode == "full" and h["accel"]:
            continue
        out.append(s)
    return out


def outcome_MB(late, dd_bps: int = 100):
    """MFE before -dd_bps drawdown touch (excursion-gated opportunity).
    Conservative same-bar rule: if a bar touches both, DD counts first."""
    late = late.sort_values("timestamp")
    if len(late) < 2:
        return None
    import numpy as np
    ref = late["open"].iloc[0]
    lo = late["low"].to_numpy() / ref - 1
    hi = late["high"].to_numpy() / ref - 1
    dd = np.nonzero(lo <= -dd_bps / 10000)[0]
    end = dd[0] if len(dd) else len(late)
    best = hi[:end].max(initial=0.0)
    return round(float(best * 10000), 1)


def outcome_E0f(late):
    """E0 on a pre-filtered, time-sorted ticker frame."""
    late = late.sort_values("timestamp")
    if len(late) < 2:
        return None
    ref = late["open"].iloc[0]
    fwd = (late["close"].iloc[-1] / ref - 1) * 10000 - 20.0
    mfe = (late["high"].max() / ref - 1) * 10000
    mae = (late["low"].min() / ref - 1) * 10000
    up = late[late["high"] / ref - 1 >= 0.01]
    dn = late[late["low"] / ref - 1 <= -0.01]
    iu = up["timestamp"].iloc[0] if len(up) else None
    idn = dn["timestamp"].iloc[0] if len(dn) else None
    return {"fwd": round(float(fwd), 1), "mfe": round(float(mfe), 1),
            "mae": round(float(mae), 1),
            "obp": bool(iu is not None and (idn is None or iu <= idn))}


def eval_day(month, day, gates=("none", "n10", "sh", "both"), hmodes=("none", "lite", "full")):
    sess = load_day(month, day)
    e15, e1445 = snapshot(sess, T_SNAP), snapshot(sess, T_PATH)
    frames = {t: g.sort_values("timestamp")
              for t, g in sess[sess["et"] >= T_SNAP].groupby("ticker")}
    if len(e15) < 20:
        return None
    state = day_state(e15, e1445)
    lists = build_lists(e15, e1445)
    res = {"day": f"{month} {str(day.date())}", "state": state, "rules": {}}
    e_cache = {}

    def ex_of(fn, s):
        key = (fn.__name__, s)
        if key not in e_cache:
            r = fn(sess, s, T_SNAP)
            e_cache[key] = r["ret"] if r else None
        return e_cache[key]

    def e1_of(s):
        return ex_of(entries_E1, s)

    for lname, syms in lists.items():
        fwd_all = [(s, outcome_E0f(frames[s])) for s in syms if s in frames]
        fwd_all = [(s, o) for s, o in fwd_all if o]
        for h in hmodes:
            kept = set(health_filter([s for s, _ in fwd_all], e15, e1445, h))
            sel = [(s, o) for s, o in fwd_all if s in kept]
            rej = [(t, o) for t in lists["top20"] if t not in kept and t in frames
                   for o in [outcome_E0f(frames[t])] if o]
            for g in gates:
                ok = (g == "none" or (g == "n10" and state["n10"] >= 4)
                      or (g == "sh" and state["top1sh"] < 0.5)
                      or (g == "both" and state["n10"] >= 4 and state["top1sh"] < 0.5))
                key = f"{lname}|{h}|{g}"
                res["rules"][key] = {
                    "gate_ok": ok, "n": len(sel),
                    "mean": round(st.mean([o["fwd"] for _, o in sel]), 1) if sel else None,
                    "med": round(st.median([o["fwd"] for _, o in sel]), 1) if sel else None,
                    "hit": round(sum(1 for _, o in sel if o["fwd"] > 20) / len(sel), 2) if sel else None,
                    "mfe": round(st.mean([o["mfe"] for _, o in sel]), 1) if sel else None,
                    "rej_mean": round(st.mean([o["fwd"] for _, o in rej]), 1) if rej else None,
                    "names": [{"t": s, "fwd": o["fwd"], "mfe": o["mfe"],
                                 "mae": o["mae"], "obp": o["obp"],
                                 "mb100": outcome_MB(frames[s], 100),
                                 "mb200": outcome_MB(frames[s], 200),
                                 "e1": e1_of(s),
                                 "e2": ex_of(entries_E2, s),
                                 "e3": ex_of(entries_E3, s)} for s, o in sel]}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", nargs="+", required=True)
    ap.add_argument("--max-days", type=int, default=3)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    import pandas as pd, json
    allres = []
    for month in args.months:
        base = Path("data/backfill") if month >= "2026-03" else Path("data")
        probe = pd.read_parquet(base / f"clean_ohlcv_{month}.parquet", columns=["timestamp"])
        probe["timestamp"] = pd.to_datetime(probe["timestamp"], utc=True)
        for d in sorted(probe["timestamp"].dt.floor("D").unique())[:args.max_days]:
            try:
                r = eval_day(month, d)
                if r:
                    allres.append(r)
                    print(f"  {r['day']} n10={r['state']['n10']} sh={r['state']['top1sh']} ok",
                          flush=True)
            except Exception as ex:
                print(f"  {month} {str(d.date())} SKIP {str(ex)[:100]}", flush=True)
    # summary: pool rule keys, split by gate_ok
    keys = sorted({k for r in allres for k in r["rules"]})
    print(f"\n=== pooled n_days={len(allres)} ===")
    for k in keys:
        on = [r["rules"][k] for r in allres if r["rules"][k]["gate_ok"] and r["rules"][k]["n"]]
        means = [x["mean"] for x in on if x["mean"] is not None]
        if means:
            ns = sum(x["n"] for x in on)
            print(f"{k}: days={len(on)} names={ns} mean={st.mean(means):.1f} "
                  f"med={st.median([x['med'] for x in on if x['med'] is not None]):.1f}")
    if args.out:
        Path(args.out).write_text(json.dumps(allres))
    return allres


if __name__ == "__main__":
    main()
