#!/usr/bin/env python3
"""Relaxed re-arm lifecycle test. Compares, all with the canonical tl30 exit:
(1) strict lifecycle (re-arm only at fresh+thrust state minutes; reproduces
lb18_lifecycle with tl30), (2) relaxed lifecycle (re-arm at any top-3 minute
with gain>=1, no fresh/thrust requirement), (3) no-pyramid sequential filter
on the unique-flush events population (take an event only if its bid minute is
after the previous exit on that ticker). Answers whether the executable
one-order-at-a-time version can capture the events-population edge.
Artifact: factory/artifacts/lb18_relax.json
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
WIN = 120
TL = 30


def sim_tl30(fut, o, h, l, c, t, B, c0):
    stop = B * (1 - STOP_L)
    n = 0
    for j in fut:
        if l[j] <= stop:
            return min(float(o[j]), stop) / B - 1, int(t[j])
        if h[j] >= c0:
            return c0 / B - 1, int(t[j])
        n += 1
        if n >= TL:
            return float(c[j]) / B - 1, int(t[j])
    if len(fut) == 0:
        return None, None
    j = int(fut[-1])
    return float(c[j]) / B - 1, int(t[j])


def stats(df, tag):
    if len(df) == 0:
        print(f"{tag:36s} n=0")
        return {"n": 0}
    v = df["ret"]
    mo = df.groupby("month")["ret"].mean()
    d = {"n": int(len(df)), "mean": round(float(v.mean()), 4),
         "med": round(float(v.median()), 4),
         "pos": round(float((v > 0).mean()), 3),
         "months_pos": int((mo > 0).sum()), "n_months": int(len(mo)),
         "worst_month": round(float(mo.min()), 4)}
    print(f"{tag:36s} n={d['n']:5d} mean={d['mean']:+.4f} med={d['med']:+.4f} "
          f"pos={d['pos']:.2f} months+={d['months_pos']}/{d['n_months']} "
          f"worst={d['worst_month']:+.4f}")
    return d


def run_lifecycle(m, A, mask, tag):
    st = m[mask.fillna(False)].copy()
    st_groups = {k: g.sort_values("t").reset_index(drop=True)
                 for k, g in st.groupby(["date", "ticker"], sort=False)}
    rows = []
    nofill = 0
    for key, gs in st_groups.items():
        d = A.get(key)
        if d is None:
            continue
        t, o, h, l, c = d["t"], d["o"], d["h"], d["l"], d["c"]
        npx = d["newpos"]
        st_t = gs["t"].to_numpy()
        resolved = -10**9
        for si in range(len(gs)):
            t0 = int(st_t[si])
            if t0 <= resolved:
                continue
            pos0 = int(np.searchsorted(t, t0))
            if pos0 >= len(c):
                continue
            c0 = float(c[pos0])
            if c0 <= 0:
                continue
            B = c0 * (1 - STOP_L)
            j0 = int(np.searchsorted(npx, pos0, "right"))
            j1 = int(np.searchsorted(t[npx], t0 + WIN, "right"))
            fj = -1
            for jj in range(j0, j1):
                if l[npx[jj]] <= B:
                    fj = jj
                    break
            if fj < 0:
                nofill += 1
                resolved = t0 + WIN
                continue
            fp = int(npx[fj])
            tf = int(t[fp])
            ret, ex = sim_tl30(npx[fj:], o, h, l, c, t, B, c0)
            if ret is None:
                continue
            rr = gs.iloc[si]
            fc = float(c[fp]) / B - 1
            rows.append({
                "mode": tag, "date": rr["date"], "month": rr["date"][:7],
                "ticker": rr["ticker"], "t0": t0, "tf": tf,
                "gain": float(rr["gain"]), "rank": int(rr["rank"]),
                "r15": float(rr["r15"]), "pullback": float(rr["pullback"]),
                "prior_flush": int(rr["prior_flush"]),
                "fc": fc, "strong": bool(fc >= 0), "ret": ret - FR,
                "exit_t": int(ex)})
            resolved = int(ex)
    df = pd.DataFrame(rows)
    print(f"  [{tag}] state minutes: {len(st)}  groups: {len(st_groups)}  "
          f"fills: {len(df)}  no-fill: {nofill}")
    return df, nofill


def main():
    paths, lb = load()
    A = build(paths)
    gpb = paths.groupby(["date", "ticker"], sort=False)
    paths["pullback"] = paths["c"] / gpb["c"].transform("cummax") - 1
    paths["r15"] = paths["c"] / gpb["c"].shift(15) - 1
    m = lb.merge(paths[["date", "ticker", "t", "c", "pullback", "r15",
                        "prior_flush"]], on=["date", "ticker", "t"], how="left")
    strict = (m["gain"] >= 1.0) & (m["pullback"] >= -0.01) & (m["r15"] >= 0.03)
    relaxed = m["gain"] >= 1.0
    print(f"strict minutes: {int(strict.sum())}  relaxed minutes: {int(relaxed.sum())}")

    out = {}
    ds, _ = run_lifecycle(m, A, strict, "strict")
    dr, _ = run_lifecycle(m, A, relaxed, "relaxed")
    print()
    out["strict"] = stats(ds, "strict lifecycle tl30")
    out["relaxed"] = stats(dr, "relaxed lifecycle tl30")
    print()
    for tag, df in [("strict", ds), ("relaxed", dr)]:
        if len(df) == 0:
            continue
        stats(df[df["strong"]], f"  {tag} strong(fc>=0)")
        stats(df[(df["gain"] >= 3) & (df["r15"] >= 0.20)], f"  {tag} g3&r15.2")

    ev = pd.read_parquet(ART / "lb18_episodes_events.parquet")
    ev = ev[~ev["censored"]].copy()
    recs = []
    for r in ev.itertuples():
        key = (r.date, r.ticker)
        d = A.get(key)
        if d is None:
            continue
        t, o, h, l, c = d["t"], d["o"], d["h"], d["l"], d["c"]
        npx = d["newpos"]
        jf = int(np.searchsorted(t[npx], r.tf))
        if jf >= len(npx) or int(t[npx[jf]]) != int(r.tf):
            continue
        c0 = float(r.px)
        B = c0 * (1 - STOP_L)
        ret, ex = sim_tl30(npx[jf:], o, h, l, c, t, B, c0)
        if ret is None:
            continue
        recs.append({"date": r.date, "month": r.date[:7], "ticker": r.ticker,
                     "t0": int(r.t0), "tf": int(r.tf), "gain": float(r.gain),
                     "rank": int(r.rank), "r15": float(r.r15),
                     "prior_flush": int(r.prior_flush), "fc": float(r.fill_close),
                     "ret": ret - FR, "exit_t": int(ex)})
    dfe = pd.DataFrame(recs).sort_values(["date", "ticker", "t0"])
    taken = np.zeros(len(dfe), bool)
    last = {}
    for i, (_, r) in enumerate(dfe.iterrows()):
        k = (r["date"], r["ticker"])
        if r["t0"] > last.get(k, -10**9):
            taken[i] = True
            last[k] = r["exit_t"]
    print()
    out["events_all"] = stats(dfe, "events all (as before)")
    out["events_seq"] = stats(dfe[taken], "events sequential (no pyramid)")
    out["events_dropped"] = stats(dfe[~taken], "events dropped by seq filter")
    print()
    dseq = dfe[taken]
    stats(dseq[dseq["fc"] >= 0.02], "  seq strong(fc>=2%)")
    stats(dseq[dseq["prior_flush"] >= 2], "  seq pf2+")

    alld = pd.concat([ds, dr], ignore_index=True)
    alld.to_parquet(ART / "lb18_relax.parquet")
    (ART / "lb18_relax.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"\nartifact -> {ART/'lb18_relax.json'}")


if __name__ == "__main__":
    main()
