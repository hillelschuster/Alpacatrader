#!/usr/bin/env python3
"""BASKET-01 T7 — policy-free pay-for-team economics (the missing contract item).

EX-POST illustration only (THESIS §6/§7). The best member is known only after the
fact; nothing here is a rule, a target, or a policy. No release/survivor logic.

Definitions (locked in this file and in the artifact):
  members        = the day's top-3 selected names with an accessible fill (blocked
                   slots and unfilled slots are cash: counted, never substituted)
  all-hold EOD   = member return from fill to the session's last close (anatomy
                   eod_ret), gross and net of 100 bps charged once per filled slot
  pay-for-team   = best member's return >= sum of the other members' losses
  stylized costs = each losing ticket's cost capped at c in {3,5,8,10}% (as if the
                   failed ticket had been released at -c); a labeled scenario, not
                   an assumption about any rule
  break-even map = required survivor return r* = k*c to cover k losing tickets cut
                   at cost c; coverage = share of days where the best member's
                   post-fill touch excursion (MFE) / all-hold return reaches r*

Output: <root>/agg/T7_payforteam.json (monthly rows + pooled rows + break-even map)

Usage:
  .venv/bin/python factory/scripts/basket_t7_econ.py --self-test
  BASKET_ART_ROOT=factory/artifacts/basket/sip .venv/bin/python factory/scripts/basket_t7_econ.py
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ART = Path(os.environ.get("BASKET_ART_ROOT", str(ROOT / "factory" / "artifacts" / "basket")))
ANAT = ART / "anatomy"
PRIMARY_N = 3
FRICTION = 0.01
COSTS = [3, 5, 8, 10]
KEYS = [1, 2]


def day_econ(rec: dict, month: str):
    rows = []
    for s in rec.get("snapshots", []):
        names = s["names"][:PRIMARY_N]
        filled = [n for n in names if n.get("fill") and not n["fill"].get("blocked")
                  and n.get("eod_ret") is not None]
        r = {"month": month, "pop": s["pop"], "T": s["T"], "days": 1,
             "lt3": int(len(filled) < PRIMARY_N), "empty": int(len(filled) == 0),
             "blocked": int(sum(1 for n in names if (n.get("fill") or {}).get("blocked")))}
        if not filled:
            rows.append(r)
            continue
        gross = sorted((float(n["eod_ret"]) for n in filled), reverse=True)
        net = [x - FRICTION for x in gross]
        best_g = gross[0]
        peer_g = sum(max(0.0, -x) for x in gross[1:])
        best_n = net[0]
        peer_n = sum(max(0.0, -x) for x in net[1:])
        r.update({"filled": len(filled),
                  "pays_gross": int(best_g >= peer_g),
                  "surplus_gross": best_g - peer_g,
                  "pays_net": int(best_n >= peer_n),
                  "surplus_net": best_n - peer_n,
                  "best_gross": best_g, "best_net": best_n, "peer_loss_net": peer_n})
        for c in COSTS:
            cap = c / 100.0
            capped = sum(min(max(0.0, -x), cap) for x in net[1:])
            r[f"pays_net_c{c}"] = int(best_n >= capped)
            r[f"surplus_net_c{c}"] = best_n - capped
        mfes = [float(n["mfe"]) for n in filled if n.get("mfe") is not None]
        r["best_mfe"] = max(mfes) if mfes else None
        r["best_eod"] = best_g
        rows.append(r)
    return rows


def augment(rows):
    """Add break-even coverage counters per (month,pop,T) row."""
    for r in rows:
        if "best_mfe" not in r and "best_eod" not in r:
            continue
        for k in KEYS:
            for c in COSTS:
                req = k * c / 100.0
                r[f"cover_mfe_k{k}_c{c}"] = int((r.get("best_mfe") or -1) >= req)
                r[f"cover_eod_k{k}_c{c}"] = int((r.get("best_eod") or -1) >= req)
    return rows


def _sum(rows, keys):
    out = {}
    for k in keys:
        vals = [r[k] for r in rows if k in r]
        out[k] = sum(vals)
    return out


SUM_FIELDS = (["days", "lt3", "empty", "blocked", "filled",
               "pays_gross", "pays_net", "surplus_gross", "surplus_net",
               "best_gross", "best_net", "peer_loss_net", "best_mfe", "best_eod"]
              + [f"pays_net_c{c}" for c in COSTS]
              + [f"surplus_net_c{c}" for c in COSTS]
              + [f"cover_mfe_k{k}_c{c}" for k in KEYS for c in COSTS]
              + [f"cover_eod_k{k}_c{c}" for k in KEYS for c in COSTS])


def finalize(rows):
    pooled = {}
    for (pop, T) in sorted({(r["pop"], r["T"]) for r in rows}):
        sub = [r for r in rows if r["pop"] == pop and r["T"] == T]
        s = _sum(sub, SUM_FIELDS)
        d = max(s["days"] - s["empty"], 1)
        entry = {"days": s["days"], "filled_days": s["days"] - s["empty"],
                 "empty_days": s["empty"], "lt3_days": s["lt3"], "blocked_slots": s["blocked"],
                 "members_filled": s["filled"],
                 "pays_gross": round(s["pays_gross"] / d, 4),
                 "pays_net": round(s["pays_net"] / d, 4),
                 "mean_surplus_gross": round(s["surplus_gross"] / d, 4),
                 "mean_surplus_net": round(s["surplus_net"] / d, 4),
                 "mean_best_gross": round(s["best_gross"] / d, 4),
                 "mean_best_net": round(s["best_net"] / d, 4),
                 "mean_peer_loss_net": round(s["peer_loss_net"] / d, 4)}
        for c in COSTS:
            entry[f"pays_net_c{c}"] = round(s[f"pays_net_c{c}"] / d, 4)
            entry[f"mean_surplus_net_c{c}"] = round(s[f"surplus_net_c{c}"] / d, 4)
        pooled[f"{pop}/{T}"] = entry
    be = {}
    for (pop, T) in sorted({(r["pop"], r["T"]) for r in rows}):
        sub = [r for r in rows if r["pop"] == pop and r["T"] == T and "best_mfe" in r]
        be[f"{pop}/{T}"] = {}
        for k in KEYS:
            for c in COSTS:
                req = k * c / 100.0
                n = len(sub)
                tot_mfe = sum(r[f"cover_mfe_k{k}_c{c}"] for r in sub)
                tot_eod = sum(r[f"cover_eod_k{k}_c{c}"] for r in sub)
                be[f"{pop}/{T}"][f"k{k}_c{c}"] = {
                    "required_return": req,
                    "days_with_member": n,
                    "cover_mfe": round(tot_mfe / max(n, 1), 4),
                    "cover_eod": round(tot_eod / max(n, 1), 4)}
    return {"pooled": pooled, "break_even_map": be, "monthly": rows}


def selftest():
    def mk(tkr, eod, mfe, blocked=False):
        return {"ticker": tkr, "fill": {"px": 10.0, "blocked": blocked},
                "eod_ret": eod, "mfe": mfe}
    rec = {"date": "2025-06-02", "snapshots": [{"pop": "B", "T": 600, "names": [
        mk("AAA", 0.30, 0.50), mk("BBB", -0.06, 0.05), mk("CCC", -0.04, 0.02)]}]}
    rows = augment(day_econ(rec, "2025-06"))
    r = rows[0]
    # gross: best .30 vs peers .10 -> pays; net: .29 vs (.07+.05)=.12 -> pays
    assert r["pays_gross"] == 1 and r["pays_net"] == 1, r
    assert abs(r["surplus_gross"] - 0.20) < 1e-9 and abs(r["surplus_net"] - 0.17) < 1e-9, r
    # c=3: peers capped at .03 each -> .06; .29 >= .06 pays; surplus .23
    assert r["pays_net_c3"] == 1 and abs(r["surplus_net_c3"] - 0.23) < 1e-9, r
    # break-even: k1_c3 requires .03; best_mfe .50 covers; k2_c10 requires .20 -> covers
    assert r["cover_mfe_k1_c3"] == 1 and r["cover_mfe_k2_c10"] == 1, r
    rec2 = {"date": "2025-06-03", "snapshots": [{"pop": "B", "T": 600, "names": [
        mk("DDD", -0.09, 0.04), mk("EEE", -0.08, 0.03), mk("FFF", 0.02, 0.05)]}]}
    rows += augment(day_econ(rec2, "2025-06"))
    out = finalize(rows)
    p = out["pooled"]["B/600"]
    assert p["days"] == 2 and p["filled_days"] == 2 and p["empty_days"] == 0, p
    assert p["pays_gross"] == 0.5 and p["pays_net"] == 0.5, p
    # day2 net: best -.08 vs peers (.10+.09)=.19 -> fails; c3: 2*.03=.06 -> fails
    assert p["pays_net_c3"] == 0.5, p
    assert out["break_even_map"]["B/600"]["k2_c3"]["required_return"] == 0.06
    rec3 = {"date": "2025-06-04", "snapshots": [{"pop": "B", "T": 600, "names": [
        mk("GGG", None, None, blocked=True)]}]}
    rows3 = augment(day_econ(rec3, "2025-06"))
    assert rows3[0]["empty"] == 1 and rows3[0]["blocked"] == 1, rows3
    print("self-test OK")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ART / "agg"))
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        selftest()
        return
    files = sorted(glob.glob(str(ANAT / "*.jsonl")))
    if not files:
        raise SystemExit(f"no anatomy day files under {ART}")
    rows = []
    for f in files:
        rec = json.load(open(f))
        rows += augment(day_econ(rec, rec["date"][:7]))
    out = finalize(rows)
    out["_producer"] = "basket_t7_econ.py"
    out["_definitions"] = ("ex-post illustration only; best member = max all-hold EOD return; "
                           "peers = other filled members; stylized cost c caps each loser at -c; "
                           "break-even r* = k*c; cover = best member's MFE / EOD reaches r*")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "T7_payforteam.json", "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(f"T7_payforteam: {len(files)} days, {len(out['monthly'])} monthly rows -> "
          f"{out_dir/'T7_payforteam.json'}")
    print(f"  pooled keys: {sorted(out['pooled'])[:6]}...")


if __name__ == "__main__":
    main()
