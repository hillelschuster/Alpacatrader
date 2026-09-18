#!/usr/bin/env python3
"""SIP certification -> Phase-1 regeneration decision note (families A-E).

Reads the committed per-day certification artifacts
(factory/artifacts/basket/sip/certification_YYYY-MM-DD.json) and aggregates the
five comparison families the user asked for:

  A selection        did SIP change basket membership / rank / decision prices?
  B path             did SIP change fills / MFE / MAE of stored members?
  C first passage    did subminute ordering change +H/-L outcomes?
  D execution        what did prevailing quotes say at causal entry?
  E tail integrity   which stored extremes survive SIP re-measurement?

Writes (with --write):
  factory/artifacts/basket/sip/SIP_DECISION_NOTE.md
  factory/artifacts/basket/sip/sip_decision.json

Measurement only. No rule changes, no parameter search; the note states the
evidence next to transparent criteria and recommends a regeneration scope.

Usage:
  .venv/bin/python factory/scripts/sip_decision.py --self-test
  .venv/bin/python factory/scripts/sip_decision.py            # print summary
  .venv/bin/python factory/scripts/sip_decision.py --write    # + artifacts
"""
from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SIPD = ROOT / "factory" / "artifacts" / "basket" / "sip"


def _pct(num, den):
    return round(num / den, 4) if den else None


def _q(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return None
    a = np.array(v, dtype=float)
    return {"n": int(len(a)), "p50": round(float(np.percentile(a, 50)), 2),
            "p90": round(float(np.percentile(a, 90)), 2),
            "max": round(float(np.abs(a).max()), 2)}


def aggregate(certs: list) -> dict:
    sel_snap = sel_set_change = sel_order_change = 0
    sel_skipped = 0
    flips_total = 0
    px_p50, px_max = [], []
    outsiders = []
    fill_d, mfe_d, mae_d = [], [], []
    flips_bar = amb_cells = amb_rt = amb_po = amb_unres = 0
    spreads, crosses = [], []
    tail_diffs = []
    mfe_stored_big = mfe_sip_big = sip_only_big = stored_only_big = 0
    per_day = []

    for c in sorted(certs, key=lambda x: x["day"]):
        day = c["day"]
        d_flips = d_sel = d_setchg = 0
        for s in c.get("selection", []):
            if "stored_top10" not in s:  # e.g. A_pm skipped in pilot-era certs
                sel_skipped += 1
                continue
            sel_snap += 1
            st = [r["ticker"] for r in s["stored_top10"][:3]]
            sip = s.get("sip_order_of_stored_top10", [])[:3]
            if st and sip and set(st) != set(sip):
                sel_set_change += 1
                d_setchg += 1
            if st != sip:
                sel_order_change += 1
            fl = int(s.get("rank_flip_positions_within_stored_top10", 0))
            flips_total += fl
            d_flips += fl
            pd = s.get("px_delta_bps", {})
            if pd and pd.get("n"):
                px_p50.append(pd.get("p50", 0.0))
                px_max.append(pd.get("max", 0.0))
            for o in s.get("outsiders_above_cutoff", []):
                outsiders.append({"day": day, "pop": s["pop"], "T": s["T"],
                                  "ticker": o.get("ticker"), "score": o.get("sel_sip")})
        for p in c.get("path", []):
            if p.get("fill_delta_bps") is not None:
                fill_d.append(abs(p["fill_delta_bps"]))
            if p.get("mfe_stored") is not None and p.get("mfe_sip") is not None:
                mfe_d.append((p["mfe_sip"] - p["mfe_stored"]) * 1e4)
                if p["mfe_stored"] >= 1.0:
                    mfe_stored_big += 1
                    if p["mfe_sip"] >= 1.0:
                        mfe_sip_big += 1
                    else:
                        stored_only_big += 1
                elif p["mfe_sip"] >= 1.0:
                    sip_only_big += 1
            if p.get("mae_stored") is not None and p.get("mae_sip") is not None:
                mae_d.append((p["mae_sip"] - p["mae_stored"]) * 1e4)
            flips_bar += len(p.get("order_flips_bar_based", p.get("order_flips_nonamb", [])) or [])
            for k, v in (p.get("amb_resolved") or {}).items():
                # per-(H,L) ambiguity cells; per-lens resolution; genuine unresolved bucket
                amb_cells += 1
                r_ok = v.get("raw") in ("up", "dn", "up_only", "dn_only")
                p_ok = v.get("price_upd") in ("up", "dn", "up_only", "dn_only")
                amb_rt += int(r_ok)
                amb_po += int(p_ok)
                amb_unres += int(not (r_ok or p_ok))
        for e in c.get("exec", []):
            if e.get("spread_bps") is not None:
                spreads.append(e["spread_bps"])
            if e.get("buy_cross_bps") is not None:
                crosses.append(e["buy_cross_bps"])
        for t in c.get("tail", {}).get("diffs_vs_stored_high", []):
            tail_diffs.append({"day": day, **t})
        per_day.append({"day": day, "sel_set_change": d_setchg, "flips": d_flips,
                        "members": len(c.get("path", [])),
                        "tail_diffs": len(c.get("tail", {}).get("diffs_vs_stored_high", []))})

    tail_sorted = sorted(tail_diffs, key=lambda x: abs(x["delta"]), reverse=True)
    agg = {
        "days": len(certs),
        "A_selection": {
            "snapshots": sel_snap,
            "skipped_snapshots": sel_skipped,
            "top3_set_change_share": _pct(sel_set_change, sel_snap),
            "top3_set_change_scope": ("LOWER BOUND: stored top-10 reranked by SIP only; "
                                      "full-universe SIP-bar discovery yields the actual number"),
            "top3_order_change_share": _pct(sel_order_change, sel_snap),
            "rank_flip_positions_total": flips_total,
            "px_delta_bps_p50_of_p50s": _q(px_p50), "px_delta_bps_max": max(px_max) if px_max else None,
            "outsiders_above_cutoff": len(outsiders),
            "outsider_examples": outsiders[:10],
        },
        "B_path": {
            "member_days": len(fill_d),
            "fill_delta_bps": _q(fill_d),
            "fill_delta_share_gt_100bps": _pct(sum(1 for x in fill_d if x > 100), len(fill_d)),
            "mfe_delta_bps": _q(mfe_d), "mae_delta_bps": _q(mae_d),
        },
        "C_first_passage": {
            "bar_based_order_flips": flips_bar,
            "bar_based_order_flips_method": ("legacy-bar vs SIP-derived-bar first-touch ordering "
                                             "(bar-level; subminute ordering measured by amb lens)"),
            "amb": {"cells": amb_cells, "resolved_raw_trades": amb_rt,
                    "resolved_price_updating": amb_po, "unresolved": amb_unres}},
        "D_execution": {"spread_bps": _q(spreads), "buy_cross_bps": _q(crosses)},
        "E_tail": {"symbol_diffs_total": len(tail_diffs),
                   "stored_big_members": mfe_stored_big, "confirmed_by_sip": mfe_sip_big,
                   "sip_only_big_new": sip_only_big, "stored_only_big_unconfirmed": stored_only_big,
                   "largest_revisions": tail_sorted[:15]},
        "per_day": per_day,
    }
    return agg


def render_note(agg: dict) -> str:
    A, B, C, D, E = (agg["A_selection"], agg["B_path"], agg["C_first_passage"],
                     agg["D_execution"], agg["E_tail"])
    L = []
    L.append("# SIP certification -> Phase-1 regeneration decision note")
    L.append("")
    L.append(f"Panel days certified: **{agg['days']}**. Method: `sip_certify.py` "
             "(SIP raw trades/quotes -> own bars per Alpaca's documented rules), compared "
             "event-by-event against the stored legacy anatomy. Pilot scope limits: "
             "candidate-union symbols only; stored prev_close reused; A_pm excluded; "
             "quoted spreads are market state, not assumed fills.")
    L.append("")
    L.append("## A. Selection differences (membership / rank / decision prices)")
    L.append(f"- top-3 **set** changed on {A['top3_set_change_share']} of {A['snapshots']} snapshots "
             f"({A['top3_set_change_scope']}); order-only changes on {A['top3_order_change_share']}")
    L.append(f"- rank-flip positions within stored top-10 (total): {A['rank_flip_positions_total']}")
    L.append(f"- decision-price deltas (bps): median-of-medians {A['px_delta_bps_p50_of_p50s']}, "
             f"max {A['px_delta_bps_max']}")
    L.append(f"- fetched outsiders above the 10th stored member: {A['outsiders_above_cutoff']}")
    L.append("")
    L.append("## B. Path differences (fills / MFE / MAE of stored members)")
    L.append(f"- member-days compared: {B['member_days']}")
    L.append(f"- |fill delta| bps: {B['fill_delta_bps']}; share >100bps: {B['fill_delta_share_gt_100bps']}")
    L.append(f"- MFE delta bps (SIP - stored): {B['mfe_delta_bps']}; MAE delta bps: {B['mae_delta_bps']}")
    L.append("")
    L.append("## C. First-passage differences")
    L.append(f"- bar-based order flips (legacy bars vs SIP-derived bars): {C['bar_based_order_flips']}; "
             f"subminute ambiguity cells {C['amb']['cells']} (resolved by raw trades "
             f"{C['amb']['resolved_raw_trades']}, by price-updating trades "
             f"{C['amb']['resolved_price_updating']}, unresolved {C['amb']['unresolved']})")
    L.append("")
    L.append("## D. Execution truth at causal entry")
    L.append(f"- quoted spread at fill bps: {D['spread_bps']}; buy-cross bps: {D['buy_cross_bps']}")
    L.append("")
    L.append("## E. Extreme-tail integrity")
    L.append(f"- stored member-days with MFE >= +100%: {E['stored_big_members']}; "
             f"confirmed by SIP: {E['confirmed_by_sip']}; unconfirmed (stored-only): "
             f"{E['stored_only_big_unconfirmed']}; SIP-only new: {E['sip_only_big_new']}")
    L.append(f"- symbols with stored-high revision > 0.5%: {E['symbol_diffs_total']}")
    L.append("- largest revisions:")
    for t in E["largest_revisions"]:
        L.append(f"  - {t['day']} {t['ticker']}: stored {t['stored_high']} -> SIP "
                 f"{t['sip_rth_po_max']} ({t['delta']:+.2%})")
    L.append("")
    L.append("## Per-day digest")
    L.append("")
    L.append("| day | top3 set changes | rank flips | members | tail diffs |")
    L.append("|---|---|---|---|---|")
    for d in agg["per_day"]:
        L.append(f"| {d['day']} | {d['sel_set_change']} | {d['flips']} | {d['members']} | {d['tail_diffs']} |")
    L.append("")
    L.append("## Decision framework (evidence -> scope)")
    L.append("- **remains valid**: all families ~zero — not the observed picture.")
    L.append("- **selective regeneration**: membership stable, but path/tail/order layers "
             "materially differ -> rebuild the anatomy path/barrier layers from SIP bars, "
             "keep selection where certified.")
    L.append("- **regenerate Phase-1 from SIP-derived bars**: membership or first-passage "
             "materially differs, or tail revisions are large -> the legacy substrate is "
             "not a sufficient measurement instrument.")
    L.append("")
    L.append("Owner reads the numbers above; no thresholds are pre-claimed as verdicts.")
    return "\n".join(L) + "\n"


def selftest():
    cert = {
        "day": "2025-01-02",
        "selection": [{"pop": "B", "T": 600,
                       "stored_top10": [{"ticker": "AAA", "px": 10.0}, {"ticker": "BBB", "px": 9.0},
                                        {"ticker": "CCC", "px": 8.0}, {"ticker": "DDD", "px": 7.0}],
                       "sip_order_of_stored_top10": ["BBB", "AAA", "DDD", "CCC"],
                       "rank_flip_positions_within_stored_top10": 2,
                       "px_delta_bps": {"n": 4, "p50": 4.0, "max": 12.0},
                       "outsiders_above_cutoff": [{"ticker": "ZZZ", "sel_sip": 0.5}]}],
        "path": [{"pop": "B", "T": 600, "ticker": "AAA", "fill_stored": 10.0, "fill_sip": 10.11,
                  "fill_delta_bps": 110.0, "mfe_stored": 1.2, "mfe_sip": 1.1, "mae_stored": -0.1,
                  "mae_sip": -0.2, "order_flips_bar_based": [{"H": 30, "L": 10}],
                  "amb_resolved": {"30/10": {"raw": "up", "price_upd": "amb"}}},
                 {"pop": "B", "T": 600, "ticker": "BBB", "fill_stored": 9.0, "fill_sip": 9.0,
                  "fill_delta_bps": 0.0, "mfe_stored": 0.5, "mfe_sip": 1.4, "mae_stored": -0.05,
                  "mae_sip": -0.06, "order_flips_bar_based": [],
                  "amb_resolved": {"10/5": {"raw": None, "price_upd": "amb"}}}],
        "exec": [{"pop": "B", "T": 600, "ticker": "AAA", "spread_bps": 90.0, "buy_cross_bps": 45.0}],
        "tail": {"diffs_vs_stored_high": [{"ticker": "AAA", "stored_high": 22.0,
                                           "sip_rth_po_max": 23.0, "raw_window_max": 23.0,
                                           "delta": 0.0455}]},
    }
    agg = aggregate([cert])
    A, B, C, E = agg["A_selection"], agg["B_path"], agg["C_first_passage"], agg["E_tail"]
    assert A["snapshots"] == 1 and A["top3_set_change_share"] == 1.0, A
    assert A["top3_order_change_share"] == 1.0 and A["rank_flip_positions_total"] == 2
    assert A["outsiders_above_cutoff"] == 1
    assert B["member_days"] == 2
    assert B["fill_delta_share_gt_100bps"] == 0.5, B
    assert E["stored_big_members"] == 1 and E["confirmed_by_sip"] == 1
    assert E["sip_only_big_new"] == 1 and E["stored_only_big_unconfirmed"] == 0
    assert C["bar_based_order_flips"] == 1 and C["amb"] == {
        "cells": 2, "resolved_raw_trades": 1, "resolved_price_updating": 0, "unresolved": 1}
    note = render_note(agg)
    assert "Decision framework" in note and "2025-01-02" in note
    print("self-test OK")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(SIPD))
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        selftest()
        return
    d = Path(args.dir)
    files = sorted(f for f in glob.glob(str(d / "certification_*.json"))
                   if "_panel" not in Path(f).name)
    certs = [json.load(open(f)) for f in files]
    if not certs:
        raise SystemExit("no certification day files")
    agg = aggregate(certs)
    if args.write:
        with open(d / "sip_decision.json", "w") as fh:
            json.dump(agg, fh, indent=1, default=str)
        with open(d / "SIP_DECISION_NOTE.md", "w") as fh:
            fh.write(render_note(agg))
        print(f"wrote sip_decision.json + SIP_DECISION_NOTE.md over {agg['days']} days")
    else:
        print(f"{agg['days']} days: A set-change {agg['A_selection']['top3_set_change_share']}, "
              f"flips {agg['A_selection']['rank_flip_positions_total']}, "
              f"B fill>100bps {agg['B_path']['fill_delta_share_gt_100bps']}, "
              f"C flips {agg['C_first_passage']['bar_based_order_flips']}, "
              f"E stored-big {agg['E_tail']['stored_big_members']}/confirmed {agg['E_tail']['confirmed_by_sip']}")


if __name__ == "__main__":
    main()
