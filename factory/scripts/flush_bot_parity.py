#!/usr/bin/env python3
"""Post-close parity for the flush bot: replay the frozen engine on the bot's
own journal data and diff engine-expected fills against journal fills.
Reads data/forward/bot/<day>/journal.jsonl (scan_row events logged each poll),
rebuilds observer-schema scans/bars in a temp dir, runs lb18_oos.run_engine,
then matches engine fills to journal `fill` events by symbol and time.
Usage: python flush_bot_parity.py --day 2026-09-11 [--journal PATH]
Artifact: factory/artifacts/flush_bot_parity_<day>.json
"""
import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lb18_oos import run_engine  # noqa: E402
from flush_forward_score import load_day  # noqa: E402
from forward_backfill_bars import fetch_day, write_bars  # noqa: E402

BOT = ROOT / "data" / "forward" / "bot"
ART = ROOT / "factory" / "artifacts"


def et_minute(ts):
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    e = t.tz_convert("America/New_York")
    return int(e.hour * 60 + e.minute), e.date().isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day")
    ap.add_argument("--journal")
    a = ap.parse_args()
    jp = Path(a.journal) if a.journal else BOT / a.day / "journal.jsonl"
    if not jp.exists():
        sys.exit(f"no journal at {jp}")
    day = a.day or jp.parent.name
    ev = [json.loads(x) for x in jp.read_text().splitlines() if x.strip()]
    scan_rows = [e for e in ev if e.get("event") == "scan_row"]
    fills = [e for e in ev if e.get("event") == "fill"]
    if not scan_rows:
        print(f"{day}: no scan_row events (bot needs restart with new code)")
        (ART / f"flush_bot_parity_{day}.json").write_text(json.dumps(
            {"day": day, "n_scan_rows": 0, "n_actual": len(fills),
             "note": "no scan_row events"}, indent=1))
        return
    by_t = {}
    for e in scan_rows:
        if int(e.get("rank", 99)) > 3:
            continue
        t, d = et_minute(e["ts"])
        if d != day or not (570 <= t <= 959):
            continue
        by_t.setdefault(t, {"ts": e["ts"], "rows": []})
        by_t[t]["rows"].append({"symbol": e["symbol"],
                                "price": e.get("close"),
                                "percent_gain": e.get("change")})
    syms = sorted({r["symbol"] for x in by_t.values() for r in x["rows"]})
    got, feed = fetch_day(day, syms)
    nrows = sum(len(v) for v in got.values())
    print(f"bars: {nrows} rows, {len(got)}/{len(syms)} syms (feed {feed})")
    tmp = Path(tempfile.mkdtemp(prefix="botparity_"))
    try:
        (tmp / "scans.jsonl").write_text("\n".join(
            json.dumps({"ts": x["ts"], "n": len(x["rows"]), "rows": x["rows"]})
            for x in by_t.values()) + "\n")
        write_bars(tmp, day, got)
        pit = pd.read_parquet(ROOT / "data" / "pit" / "pit_symbols.parquet")
        elig = set(pit[pit["vintage"] == pit["vintage"].max()]["symbol"])
        paths, lb = load_day(tmp, day, elig)
        expected = pd.DataFrame()
        if paths is not None and lb is not None and len(lb):
            expected = run_engine(paths, lb)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    act = []
    for f in fills:
        t, d = et_minute(f["ts"])
        act.append({"symbol": f.get("symbol"), "oid": f.get("oid"), "t": t,
                    "price": f.get("price"), "B": f.get("B"), "c0": f.get("c0")})
    exp = expected.to_dict("records") if len(expected) else []
    matches, unmatched_exp, unmatched_act = [], [], []
    used = set()
    for x in exp:
        hit = None
        best = None
        for i, aa in enumerate(act):
            if i in used or aa["symbol"] != x["ticker"]:
                continue
            dt = abs(int(aa["t"]) - int(x["tf"]))
            if dt <= 5 and (best is None or dt < best[0]):
                best = (dt, i, aa)
        if best:
            hit = (best[1], best[2])
        if hit:
            used.add(hit[0])
            matches.append({"engine": {k: x[k] for k in ("ticker", "tf", "ret")},
                            "actual": hit[1], "dmin": best[0]})
        else:
            unmatched_exp.append({k: x[k] for k in ("ticker", "tf", "ret")})
    for i, aa in enumerate(act):
        if i not in used:
            unmatched_act.append(aa)
    out = {"day": day, "n_scan_rows": len(scan_rows), "n_syms": len(syms),
           "n_expected": len(exp), "n_actual": len(act), "matches": matches,
           "expected_no_fill": unmatched_exp, "actual_not_expected": unmatched_act,
           "approx": ["scan assigned to its own minute (live decision bar t-1 not modeled)",
                      "engine replay does not replicate POS_MAX/15:30/refresh-anchor guards"]}
    (ART / f"flush_bot_parity_{day}.json").write_text(
        json.dumps(out, indent=1, default=str))
    print(f"expected {len(exp)} | actual {len(act)} | matched {len(matches)}")
    for m in matches:
        print("  match", m["engine"], "px", m["actual"]["price"], "B", m["actual"]["B"])
    for u in unmatched_exp:
        print("  engine-only", u)
    for u in unmatched_act:
        print("  bot-only", u)
    print(f"artifact -> {ART / f'flush_bot_parity_{day}.json'}")


if __name__ == "__main__":
    main()
