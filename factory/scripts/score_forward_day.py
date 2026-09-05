"""Score a forward-observer session after close: pull SIP 1-min bars for promoted
symbols and compute per-name outcomes from first-promotion time.

Usage (after 20:10 UTC): python factory/scripts/score_forward_day.py --day 2026-09-04
Writes data/forward/<day>/scores.json. READ-ONLY vs repo; only Alpaca reads.
"""
from __future__ import annotations
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(".env")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True)
    args = ap.parse_args()
    daydir = Path("data/forward") / args.day
    proms = [json.loads(l) for l in open(daydir / "promotions.jsonl")]
    first = {}
    for p in proms:
        first.setdefault(p["symbol"], p["ts"])
    syms = sorted(first)
    print(f"{len(proms)} promotions, {len(syms)} symbols", flush=True)

    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed
    hc = StockHistoricalDataClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"])
    from datetime import timedelta
    y, m, d = map(int, args.day.split("-"))
    day_start = datetime(y, m, d, tzinfo=timezone.utc)
    out = []

    def variants(sym):
        vs = [sym]
        for a, b in [(".", "/"), (".", ""), (".", "+")]:
            v = sym.replace(a, b)
            if v != sym and v not in vs:
                vs.append(v)
        return vs

    for i in range(0, len(syms), 50):
        batch = syms[i:i + 50]
        data = {}
        for feed in (DataFeed.SIP, DataFeed.IEX):
            try:
                data = hc.get_stock_bars(StockBarsRequest(
                    symbol_or_symbols=batch, timeframe=TimeFrame.Minute,
                    start=day_start,
                    end=day_start + timedelta(days=1),
                    feed=feed)).data or {}
                for s in [x for x in batch if x not in data]:
                    for v in variants(s)[1:]:  # warrant symbology (EONR.WS etc.)
                        try:
                            r = hc.get_stock_bars(StockBarsRequest(
                                symbol_or_symbols=[v], timeframe=TimeFrame.Minute,
                                start=day_start, end=day_start + timedelta(days=1),
                                feed=feed)).data
                            if r.get(v):
                                data[s] = r[v]
                                break
                        except Exception:
                            continue
                if data:
                    break
            except Exception as e:
                print("feed err", feed, str(e)[:80], flush=True)
                data = {}
        for sym in batch:
            bars = sorted(data.get(sym, []), key=lambda b: b.timestamp) if data else []
            t0 = datetime.fromisoformat(first[sym])
            t0s = first[sym]
            fut = [b for b in bars if b.timestamp >= t0]
            if len(fut) < 2:
                out.append({"symbol": sym, "first_seen": t0s, "n_bars": len(fut),
                            "note": "insufficient-bars"})
                continue
            ref = fut[0].open
            fwd = (fut[-1].close / ref - 1) * 10000 - 20.0
            mfe = (max(b.high for b in fut) / ref - 1) * 10000
            mae = (min(b.low for b in fut) / ref - 1) * 10000
            up = next((b for b in fut if b.high / ref - 1 >= 0.01), None)
            dn = next((b for b in fut if b.low / ref - 1 <= -0.01), None)
            out.append({"symbol": sym, "first_seen": t0s, "n_bars": len(fut), "feed": str(feed),
                        "fwd": round(fwd, 1), "mfe": round(mfe, 1), "mae": round(mae, 1),
                        "obp": bool(up and (not dn or up.timestamp <= dn.timestamp))})
    (daydir / "scores.json").write_text(json.dumps(out))
    scored = [o for o in out if "fwd" in o]
    import statistics as st
    if not scored:
        print(f"scored 0/{len(syms)}: no outcomes (all insufficient-bars)", flush=True)
        (daydir / "scores.json").write_text(json.dumps(out))
        return
    print(f"scored {len(scored)}/{len(syms)}: mean_fwd={st.mean([o['fwd'] for o in scored]):.0f} "
          f"med={st.median([o['fwd'] for o in scored]):.0f} "
          f"hit={sum(1 for o in scored if o['fwd'] > 0)}/{len(scored)} "
          f"mean_mfe={st.mean([o['mfe'] for o in scored]):.0f}", flush=True)


if __name__ == "__main__":
    main()
