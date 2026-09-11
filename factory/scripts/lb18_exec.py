#!/usr/bin/env python3
"""Execution realism for the frozen flush rule (PRE-REG-FLUSH-01).
Reads dev+OOS frozen-rule fills, joins path bars, and reports:
 (1) fill classes (clean touch vs gap-through) and their economics;
 (2) capacity proxies (fill-bar share / dollar volume);
 (3) halt/gap tails that breach the -10% stop;
 (4) continuous monthly record 2024-01..2026-08 (decay check);
 (5) robustness: stop depth and TL (diagnostic only, no rule change);
 (6) no-fill-on-gap-through variant (queue-conservative).
Artifact: factory/artifacts/lb18_exec.json
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lb18_episodes import build, load  # noqa: E402

ART = ROOT / "factory" / "artifacts"
FR = 0.01
STOP_L = 0.10
TL = 30


def sim(fut, o, h, l, c, t, B, c0, tl=TL, stop_l=STOP_L):
    stop = B * (1 - stop_l)
    n = 0
    for j in fut:
        if l[j] <= stop:
            return min(float(o[j]), stop) / B - 1
        if h[j] >= c0:
            return c0 / B - 1
        n += 1
        if n >= tl:
            return float(c[j]) / B - 1
    return None if len(fut) == 0 else float(c[fut[-1]]) / B - 1


def agg(df, tag):
    if len(df) == 0:
        print(f"  {tag:26s} n=0")
        return {"n": 0}
    mo = df.groupby("month")["ret"].mean()
    d = {"n": int(len(df)), "mean": round(float(df["ret"].mean()), 4),
         "med": round(float(df["ret"].median()), 4),
         "pos": round(float((df["ret"] > 0).mean()), 3),
         "months_pos": int((mo > 0).sum()), "n_months": int(len(mo)),
         "worst": round(float(mo.min()), 4)}
    print(f"  {tag:26s} n={d['n']:4d} mean={d['mean']:+.4f} med={d['med']:+.4f} "
          f"pos={d['pos']:.2f} months+={d['months_pos']}/{d['n_months']}")
    return d


def main():
    paths, _ = load()
    A = build(paths)
    fills = pd.concat([
        pd.read_parquet(ART / "lb18_oos_dev.parquet"),
        pd.read_parquet(ART / "lb18_oos_oos.parquet"),
    ], ignore_index=True)

    rows = []
    for r in fills.itertuples():
        d = A.get((r.date, r.ticker))
        if d is None:
            continue
        t, o, h, l, c, v = d["t"], d["o"], d["h"], d["l"], d["c"], d["v"]
        nb = d["nb"]
        npx = d["newpos"]
        jf = int(np.searchsorted(t[npx], int(r.tf)))
        if jf >= len(npx) or int(t[npx[jf]]) != int(r.tf):
            continue
        j = int(npx[jf])
        B = float(o[j])  # placeholder; overwritten below
        # recover B from fc? B = c[j]/(1+fc)
        B = float(c[j]) / (1 + float(r.fc))
        c0 = B / (1 - STOP_L)
        if B <= 0:
            continue
        fbar = {"o": float(o[j]), "h": float(h[j]), "l": float(l[j]),
                "c": float(c[j]), "v": float(v[j])}
        nbf = int(nb[j])
        nbp = int(nb[npx[jf - 1]]) if jf > 0 else nbf
        miss = nbf - nbp - 1
        gap = bool(fbar["o"] < B)
        fut = npx[jf:]
        rows.append({
            "date": r.date, "month": r.date[:7], "ticker": r.ticker,
            "tf": int(r.tf), "rank": int(r.rank), "gain": float(r.gain),
            "prior_flush": int(r.prior_flush), "fc": float(r.fc),
            "ret": float(r.ret), "gap": gap, "miss": miss,
            "px": B, "shares": fbar["v"], "dollar": fbar["v"] * B,
            "r_tl20": sim(fut, o, h, l, c, t, B, c0, 20) - FR,
            "r_tl45": sim(fut, o, h, l, c, t, B, c0, 45) - FR,
            "r_tl60": sim(fut, o, h, l, c, t, B, c0, 60) - FR,
            "r_s08": sim(fut, o, h, l, c, t, B, c0, TL, 0.08) - FR,
            "r_s12": sim(fut, o, h, l, c, t, B, c0, TL, 0.12) - FR,
            "r_s15": sim(fut, o, h, l, c, t, B, c0, TL, 0.15) - FR,
        })
    d = pd.DataFrame(rows)
    print(f"frozen-rule fills joined: {len(d)} (of {len(fills)})")

    out = {"n_fills": int(len(d))}

    print("\n# fill classes")
    out["classes"] = {"gap_through_share": round(float(d["gap"].mean()), 4),
                      "halt_gap_share": round(float((d["miss"] > 0).mean()), 4),
                      "clean": agg(d[~d["gap"]], "clean touch"),
                      "gap": agg(d[d["gap"]], "gap-through")}
    print("\n# capacity proxies (fill-bar)")
    for col, nm in (("shares", "shares"), ("dollar", "dollar vol $")):
        q = d[col].quantile([0.1, 0.25, 0.5, 0.75]).round(1)
        out[f"cap_{nm}"] = {str(k): float(x) for k, x in q.items()}
        print(f"  {nm:12s} p10={q[0.1]:,.0f} p25={q[0.25]:,.0f} "
              f"med={q[0.5]:,.0f} p75={q[0.75]:,.0f}")
    for part in (0.05, 0.10):
        size_usd = d["dollar"] * part
        q = size_usd.quantile([0.25, 0.5, 0.75]).round(0)
        out[f"part{int(part*100)}_usd"] = {str(k): float(x) for k, x in q.items()}
        print(f"  {int(part*100)}% of fill-bar $: p25=${q[0.25]:,.0f} "
              f"med=${q[0.5]:,.0f} p75=${q[0.75]:,.0f}")

    print("\n# stop breaches beyond -10%")
    bad = d[d["ret"] < -0.11]
    out["breach"] = {"n": int(len(bad)), "share": round(len(bad) / len(d), 4),
                     "gap_share_among_breaches": round(float(bad["gap"].mean()), 4)
                     if len(bad) else None,
                     "halt_share_among_breaches": round(float((bad["miss"] > 0).mean()), 4)
                     if len(bad) else None,
                     "worst": bad.nsmallest(8, "ret")[
                         ["date", "ticker", "ret", "fc", "gap", "miss"]
                     ].to_dict("records") if len(bad) else []}
    print(f"  breaches n={len(bad)} ({len(bad)/len(d):.1%})")
    if len(bad):
        print(bad.nsmallest(8, "ret")[
            ["date", "ticker", "ret", "fc", "gap", "miss"]].to_string(index=False))

    print("\n# continuous monthly record (dev+OOS)")
    mo = d.groupby("month").agg(n=("ret", "size"), mean=("ret", "mean"),
                                pf2n=("prior_flush", lambda s: (s >= 2).sum()),
                                pf2mean=("ret", lambda s: s[
                                    d.loc[s.index, "prior_flush"] >= 2].mean()))
    mo = mo.round(4)
    print(mo.to_string())
    out["monthly"] = mo.reset_index().to_dict("records")
    h1 = mo[mo.index < "2025-07"]["mean"].mean()
    h2 = mo[mo.index >= "2025-07"]["mean"].mean()
    print(f"  half1 (2024-01..2025-06) mean={h1:+.4f} | "
          f"half2 (2025-07..2026-08) mean={h2:+.4f}")
    out["halves"] = {"h1_mean": round(float(h1), 4), "h2_mean": round(float(h2), 4)}

    print("\n# robustness (diagnostic; rule unchanged)")
    out["robust"] = {
        "base": agg(d, "base tl30 stop10"),
        "tl20": agg(d.assign(ret=d["r_tl20"]), "tl20"),
        "tl45": agg(d.assign(ret=d["r_tl45"]), "tl45"),
        "tl60": agg(d.assign(ret=d["r_tl60"]), "tl60"),
        "stop08": agg(d.assign(ret=d["r_s08"]), "stop -8%"),
        "stop12": agg(d.assign(ret=d["r_s12"]), "stop -12%"),
        "stop15": agg(d.assign(ret=d["r_s15"]), "stop -15%"),
    }
    print("\n# pf>=2 subsets")
    p2 = d[d["prior_flush"] >= 2]
    out["pf2"] = {"base": agg(p2, "pf2 base"),
                  "no_gap_fills": agg(p2[~p2["gap"]], "pf2 clean-touch only"),
                  "tl20": agg(p2.assign(ret=p2["r_tl20"]), "pf2 tl20"),
                  "tl45": agg(p2.assign(ret=p2["r_tl45"]), "pf2 tl45")}

    (ART / "lb18_exec.json").write_text(json.dumps(out, indent=1, default=str))
    d.to_parquet(ART / "lb18_exec.parquet")
    print(f"\nartifact -> {ART/'lb18_exec.json'}")


if __name__ == "__main__":
    main()
