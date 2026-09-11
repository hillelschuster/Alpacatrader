#!/usr/bin/env python3
"""Paper-fill ledger: Alpaca FILL activities -> per-trade P&L, joined to the
bot journal's intended B/c0. Measurement instrument for the paper accumulation
target (30+ fills) and fill-realism vs sim.
Usage: python factory/scripts/flush_bot_ledger.py
Artifact: factory/artifacts/flush_bot_ledger.json
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
FRICTION = 0.01


def journal_events():
    ev = []
    for jp in sorted((ROOT / "data" / "forward" / "bot").glob("*/journal.jsonl")):
        for line in jp.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev.append(json.loads(line))
            except Exception:
                pass
    return ev


def main():
    argparse.ArgumentParser().parse_args()
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus
    key = os.environ.get("ALPACA_API_KEY")
    sec = os.environ.get("ALPACA_SECRET_KEY")
    if not key or not sec:
        sys.exit("missing ALPACA_API_KEY/ALPACA_SECRET_KEY in .env")
    tc = TradingClient(key, sec, paper=True)
    orders = tc.get_orders(GetOrdersRequest(status=QueryOrderStatus.ALL,
                                            limit=500)) or []
    fills = []
    for o in orders:
        fq = float(getattr(o, "filled_qty", 0) or 0)
        px = getattr(o, "filled_avg_price", None)
        sym = getattr(o, "symbol", None)
        if fq <= 0 or not sym or px is None:
            continue
        ts = getattr(o, "filled_at", None) or getattr(o, "submitted_at", None)
        fills.append({"time": str(ts), "symbol": sym,
                      "side": str(getattr(o, "side", None)),
                      "qty": fq, "price": float(px)})
    fills.sort(key=lambda f: f["time"] or "")

    j = journal_events()
    allowed = set()
    bids = {}
    for e in j:
        ev = e.get("event")
        if ev in ("place_bid", "fill", "exit", "oco") and e.get("symbol"):
            allowed.add((str(e.get("ts", ""))[:10], e["symbol"]))
        if ev == "place_bid":
            bids[e.get("symbol")] = {"B": e.get("B"), "c0": e.get("c0")}
    n_all = len(fills)
    fills = [f for f in fills if (f["time"][:10], f["symbol"]) in allowed]
    n_foreign = n_all - len(fills)

    pos = defaultdict(lambda: {"qty": 0.0, "cost": 0.0})
    trades = []
    for f in fills:
        s = f["symbol"]
        p = pos[s]
        if "buy" in f["side"].lower():
            p["qty"] += f["qty"]
            p["cost"] += f["qty"] * f["price"]
            continue
        if "sell" not in f["side"].lower():
            continue
        entry = (p["cost"] / p["qty"]) if p["qty"] > 0 else float("nan")
        q = min(f["qty"], p["qty"]) if p["qty"] > 0 else f["qty"]
        ok = entry == entry and entry > 0
        trades.append({
            "symbol": s, "qty": q,
            "entry": round(entry, 4) if ok else None,
            "exit": round(f["price"], 4),
            "ret_gross": round(f["price"] / entry - 1, 4) if ok else None,
            "ret_net": round(f["price"] / entry - 1 - FRICTION, 4) if ok else None,
            "t_exit": f["time"]})
        if p["qty"] > 0:
            p["cost"] -= entry * q
            p["qty"] -= q

    for t in trades:
        b = bids.get(t["symbol"])
        if b:
            t["rule_B"], t["rule_c0"] = b["B"], b["c0"]

    out = {"n_fills": len(fills), "n_trades": len(trades),
           "n_foreign_ignored": n_foreign,
           "recent_fills": fills[-20:], "trades": trades[-100:]}
    covered = [t for t in trades if t["ret_net"] is not None]
    if covered:
        import statistics as st
        rets = [t["ret_net"] for t in covered]
        out["summary"] = {"n": len(rets), "mean_net": round(st.mean(rets), 4),
                          "median_net": round(st.median(rets), 4),
                          "pos": round(sum(1 for x in rets if x > 0) / len(rets), 3)}
    art = ROOT / "factory" / "artifacts" / "flush_bot_ledger.json"
    art.write_text(json.dumps(out, indent=1, default=str))
    print(f"fills={len(fills)} closed_trades={len(trades)}")
    for t in trades[-10:]:
        print(f"  {t['symbol']:6s} entry={t['entry']} exit={t['exit']} "
              f"qty={t['qty']} ret_net={t['ret_net']}")
    if out.get("summary"):
        print("summary:", out["summary"])
    print("artifact ->", art)


if __name__ == "__main__":
    main()
