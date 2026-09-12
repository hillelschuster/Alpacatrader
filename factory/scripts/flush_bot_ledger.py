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
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
FRICTION = 0.01
ET = ZoneInfo("America/New_York")


def et_day(ts):
    if not ts:
        return ""
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return str(ts)[:10]
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(ET).date().isoformat()


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
    orders, after = [], None
    for _ in range(10):
        kw = {"status": QueryOrderStatus.ALL, "limit": 500}
        if after:
            kw["after"] = after
        try:
            batch = tc.get_orders(GetOrdersRequest(**kw)) or []
        except Exception as e:
            print(f"get_orders page failed: {e}")
            break
        orders.extend(batch)
        if len(batch) < 500:
            break
        last = None
        for last in batch:
            pass
        after = str(getattr(last, "id", "") or "")
        if not after:
            break
    fills = []
    for o in orders:
        fq = float(getattr(o, "filled_qty", 0) or 0)
        px = getattr(o, "filled_avg_price", None)
        sym = getattr(o, "symbol", None)
        if fq <= 0 or not sym or px is None:
            continue
        ts = getattr(o, "filled_at", None) or getattr(o, "submitted_at", None)
        fills.append({"time": str(ts), "day_et": et_day(ts), "symbol": sym,
                      "side": str(getattr(o, "side", None)),
                      "qty": fq, "price": float(px)})
    fills.sort(key=lambda f: f["time"] or "")

    j = journal_events()
    allowed = set()
    bids = {}
    tags = {}
    for e in j:
        ev = e.get("event")
        if ev in ("place_bid", "fill", "exit", "oco") and e.get("symbol"):
            allowed.add((et_day(e.get("ts")), e["symbol"]))
        if ev == "place_bid":
            bids[(et_day(e.get("ts")), e.get("symbol"))] = {
                "B": e.get("B"), "c0": e.get("c0")}
        if ev in ("place_bid", "fill") and e.get("symbol"):
            key = (et_day(e.get("ts")), str(e["symbol"]))
            current = tags.get(key, {})
            for tag in ("pf_est", "n_strict_est"):
                if e.get(tag) is not None:
                    current[tag] = e[tag]
            tags[key] = current
    n_all = len(fills)
    fills = [f for f in fills if (f["day_et"], f["symbol"]) in allowed]
    n_foreign = n_all - len(fills)

    pos = defaultdict(lambda: {"qty": 0.0, "cost": 0.0})
    trades = []
    for f in fills:
        s = f["symbol"]
        p = pos[s]
        side = f["side"].lower()
        if "buy" in side:
            p["qty"] += f["qty"]
            p["cost"] += f["qty"] * f["price"]
            continue
        if "sell" not in side:
            continue
        entry = (p["cost"] / p["qty"]) if p["qty"] > 0 else float("nan")
        q = min(f["qty"], p["qty"]) if p["qty"] > 0 else f["qty"]
        ok = entry == entry and entry > 0
        closed = p["qty"] - q <= 1e-9
        if p["qty"] > 0:
            p["cost"] -= entry * q
            p["qty"] -= q
        gross = (f["price"] / entry - 1) if ok else None
        net = (gross - (FRICTION if closed else 0.0)) if gross is not None else None
        trades.append({
            "symbol": s, "qty": q,
            "entry": round(entry, 4) if ok else None,
            "exit": round(f["price"], 4),
            "ret_gross": round(gross, 4) if gross is not None else None,
            # friction charged once per round trip (closing leg)
            "ret_net": round(net, 4) if net is not None else None,
            "closed": closed, "t_exit": f["time"]})

    for t in trades:
        b = bids.get((et_day(t["t_exit"]), t["symbol"]))
        if b:
            t["rule_B"], t["rule_c0"] = b["B"], b["c0"]
        t.update(tags.get((et_day(t["t_exit"]), t["symbol"]), {}))

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
        def partition(field, keep):
            values = [t["ret_net"] for t in covered if keep(t.get(field))]
            return {"n": len(values),
                    "mean_net": round(st.mean(values), 4) if values else None}
        out["summary_by_tag"] = {
            "pf_est": {
                "0_1": partition("pf_est", lambda v: v is not None and int(v) <= 1),
                "2plus": partition("pf_est", lambda v: v is not None and int(v) >= 2),
            },
            "n_strict_est": {
                "0_1": partition("n_strict_est", lambda v: v is not None and int(v) <= 1),
                "2plus": partition("n_strict_est", lambda v: v is not None and int(v) >= 2),
            },
            "baseline": {"a3b_pf2_mean_net": 0.0113,
                         "note": "judge live paper vs A3b once n >= 30 fills"},
        }
    art = ROOT / "factory" / "artifacts" / "flush_bot_ledger.json"
    art.write_text(json.dumps(out, indent=1, default=str))
    print(f"fills={len(fills)} closed_trades={len(trades)}")
    for t in trades[-10:]:
        print(f"  {t['symbol']:6s} entry={t['entry']} exit={t['exit']} "
              f"qty={t['qty']} ret_net={t['ret_net']}")
    if out.get("summary"):
        print("summary:", out["summary"])
    if out.get("summary_by_tag"):
        print("by tag:", json.dumps(out["summary_by_tag"]))
    print("artifact ->", art)


if __name__ == "__main__":
    main()
