#!/usr/bin/env python3
"""Flush-bid paper-trading bot v0 — frozen rule PRE-REG-FLUSH-01.

Per poll (market open only):
  1. TradingView scanner -> top gainers (exchange-filtered, change desc):
     candidates = change >= CAND_MIN, scanner position = causal gain rank.
  2. Alpaca SIP minute bars (today's ET session) per tracked candidate.
  3. Strict state per completed bar: gain >= 1.00 vs prev close (derived from
     first-sight close/change and cached), pullback >= -0.01, r15 >= 0.03;
     live approximation: scanner rank <= 3 at poll time.
  4. Flat symbols with a strict state minute -> resting bid at 0.90 * latest
     strict close; refresh if level moved >0.5%; expire after WIN_MIN.
  5. Fill -> OCO exits: stop sell at 0.90*B, limit sell at c0 = B/0.90;
     tl30 time-stop closes the remainder at market after 30 new bars.
  6. Guards: max POS_MAX concurrent, NOTIONAL per entry, no entries after
     15:30 ET, flatten 15:55 ET, kill file data/KILL, broker-truth resync
     every poll (orders/positions are read from the broker, not local state).

DRY RUN by default (logs intended actions, places nothing). --live uses the
Alpaca paper keys from .env. Journal: data/forward/bot/<ET-day>/journal.jsonl
(order/fill/exit/micro events; micro = SIP trades at/below B around a fill).

Usage: python flush_bot.py [--live] [--once] [--seconds 60]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

ET = ZoneInfo("America/New_York")
JOURNAL = ROOT / "data" / "forward" / "bot"
KILL = ROOT / "data" / "KILL"

L = 0.10
STOP_L = 0.10
WIN_MIN = 120
TL_BARS = 30
CAND_MIN = 50.0
POS_MAX = 3
NOTIONAL = 2000.0
ENTRY_CUTOFF = 15 * 60 + 30
FLAT_ET = 15 * 60 + 55
POLL_S = 60
REFRESH_EPS = 0.005
TOPN = 10

TV_URL = "https://scanner.tradingview.com/america/scan"
TV_BODY = {
    "symbols": {"tickers": [], "query": {"types": ["stock"]}},
    "columns": ["name", "close", "change", "volume", "market_cap_basic",
                "type", "subtype", "exchange", "typespecs"],
    "filter": [{"left": "exchange", "operation": "in_range",
                "right": ["NASDAQ", "NYSE", "AMEX"]},
               {"left": "type", "operation": "in_range", "right": ["stock"]},
               {"left": "subtype", "operation": "in_range", "right": ["common"]}],
    "sort": {"sortBy": "change", "sortOrder": "desc"},
    "range": [0, 50],
}


def now_et():
    return datetime.now(tz=ET)


def day_dir():
    return JOURNAL / now_et().strftime("%Y-%m-%d")


def jlog(event, **kw):
    rec = {"ts": now_et().isoformat(), "event": event}
    rec.update(kw)
    d = day_dir()
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "journal.jsonl", "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    print(json.dumps(rec, default=str), flush=True)


def tv_scan():
    req = urllib.request.Request(
        TV_URL, data=json.dumps(TV_BODY).encode(),
        headers={"Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                 "Origin": "https://www.tradingview.com"})
    with urllib.request.urlopen(req, timeout=25) as r:
        d = json.loads(r.read())
    rows = []
    for i, r0 in enumerate(d.get("data", [])):
        v = r0["d"]
        try:
            rows.append({"symbol": str(v[0]).upper(), "rank": i + 1,
                         "close": float(v[1]), "change": float(v[2]),
                         "market_cap": float(v[4] or 0)})
        except (TypeError, ValueError):
            continue
    return pd.DataFrame(rows)


def state_minutes(bars, prev_close):
    b = bars[(bars["et"] >= 570) & (bars["et"] <= 959)].sort_values("ts")
    if len(b) < 16:
        return b.iloc[0:0]
    c = b["close"]
    gain = c / prev_close - 1
    pullback = c / c.cummax() - 1
    r15 = c / c.shift(15) - 1
    m = (gain >= 1.0) & (pullback >= -0.01) & (r15 >= 0.03)
    return b[m.fillna(False)]


class Broker:
    def __init__(self, live):
        from alpaca.trading.client import TradingClient
        key = __import__("os").environ["ALPACA_API_KEY"]
        sec = __import__("os").environ["ALPACA_SECRET_KEY"]
        paper = __import__("os").environ.get("ALPACA_PAPER", "true") != "false"
        self.live = live
        self.tc = TradingClient(key, sec, paper=paper)
        from alpaca.data.historical import (StockHistoricalDataClient)
        self.dc = StockHistoricalDataClient(key, sec)

    def clock(self):
        c = self.tc.get_clock()
        return bool(c.is_open), c.timestamp

    def open_orders(self):
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        return self.tc.get_orders(filter=GetOrdersRequest(
            status=QueryOrderStatus.OPEN, limit=200))

    def positions(self):
        return {p.symbol: p for p in self.tc.get_all_positions()}

    def bars(self, symbol, start):
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from alpaca.data.enums import DataFeed
        for feed in (DataFeed.SIP, DataFeed.IEX):
            try:
                r = StockBarsRequest(symbol_or_symbols=symbol,
                                     timeframe=TimeFrame.Minute, start=start,
                                     feed=feed, limit=10000)
                df = self.dc.get_stock_bars(r).df.reset_index()
                if len(df):
                    df = df.rename(columns={"timestamp": "ts"})
                    ts = pd.to_datetime(df["ts"], utc=True)
                    et = ts.dt.tz_convert("America/New_York")
                    df["ts"] = ts
                    df["et"] = et.dt.hour * 60 + et.dt.minute
                    return df
            except Exception:
                continue
        return pd.DataFrame()

    def trades_at_bid(self, symbol, B, t0, t1):
        from alpaca.data.requests import StockTradesRequest
        from alpaca.data.enums import DataFeed
        try:
            r = StockTradesRequest(symbol_or_symbols=symbol, start=t0, end=t1,
                                   feed=DataFeed.SIP, limit=10000)
            df = self.dc.get_stock_trades(r).df.reset_index()
        except Exception:
            return {}
        if len(df) == 0:
            return {}
        px = df["price"]
        at = df[px <= B]
        return {"n": int(len(df)), "touch": bool(len(at)),
                "through": bool((px < B).any()),
                "vol_at": int(at["size"].sum()) if len(at) else 0}

    def buy_limit(self, symbol, qty, price):
        if not self.live:
            return None
        from alpaca.trading.requests import LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        return self.tc.submit_order(LimitOrderRequest(
            symbol=symbol, qty=qty, side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY, limit_price=round(price, 2)))

    def sell_oco(self, symbol, qty, stop_price, limit_price):
        if not self.live:
            return None
        from alpaca.trading.requests import (LimitOrderRequest,
                                             StopLossRequest,
                                             TakeProfitRequest)
        from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
        return self.tc.submit_order(LimitOrderRequest(
            symbol=symbol, qty=qty, side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC, order_class=OrderClass.OCO,
            take_profit=TakeProfitRequest(limit_price=round(limit_price, 2)),
            stop_loss=StopLossRequest(stop_price=round(stop_price, 2))))

    def cancel(self, oid):
        if not self.live:
            return None
        try:
            return self.tc.cancel_order_by_id(oid)
        except Exception as e:
            return str(e)

    def close_market(self, symbol):
        if not self.live:
            return None
        return self.tc.close_position(symbol)


def qty_for(price):
    return max(1, int(NOTIONAL / max(price, 0.01)))


def poll(br: Broker, meta: dict, probe=False):
    open_, _ = br.clock()
    d = day_dir()
    if not open_ and not probe:
        jlog("market_closed", note="no action")
        return
    scan = tv_scan()
    if probe:
        jlog("scan", n=int(len(scan)))
    for r in scan.head(5).itertuples():
        jlog("scan_row", rank=int(r.rank), symbol=r.symbol,
             close=float(r.close), change=float(r.change))
    cands = scan[scan["change"] >= CAND_MIN].head(TOPN)
    positions = br.positions()
    orders = br.open_orders()
    buys = {}
    for o in orders:
        if o.side.value == "buy":
            buys[o.symbol] = o
    et_now = now_et()
    minutes_now = et_now.hour * 60 + et_now.minute

    if minutes_now >= FLAT_ET and (positions or orders):
        for sym in list(positions.keys()):
            jlog("flatten", symbol=sym)
            br.close_market(sym)
        for o in orders:
            jlog("cancel_eod", symbol=o.symbol, oid=str(o.id))
            br.cancel(str(o.id))
        return

    for r in cands.itertuples():
        sym = r.symbol
        if sym not in meta:
            meta[sym] = {}
        m = meta[sym]
        if "prev_close" not in m:
            if r.change and r.close > 0:
                m["prev_close"] = r.close / (1 + r.change / 100.0)
            else:
                continue
        start_day = et_now
        if minutes_now < 570:
            start_day = et_now - timedelta(days=1)
        start = start_day.replace(hour=9, minute=30, second=0, microsecond=0)
        bars = br.bars(sym, start)
        if len(bars) == 0:
            continue
        sm = state_minutes(bars, m["prev_close"])
        m["n_bars"] = int(len(bars))
        if len(sm) == 0:
            continue
        last = sm.iloc[-1]
        if int(r.rank) > 3:
            continue
        B = round(float(last["close"]) * (1 - L), 2)
        c0 = round(float(last["close"]), 2)

        pos = positions.get(sym)
        ord_buy = buys.get(sym)

        if probe:
            jlog("probe", symbol=sym, rank=int(r.rank),
                 state_min=int(last["et"]), B=B, c0=c0)
            continue

        if pos is not None and float(pos.qty) != 0:
            filled_b = m.get("entry_bars")
            if filled_b is None:
                jlog("adopt_position", symbol=sym, qty=pos.qty,
                     entry=pos.avg_entry_price)
                continue
            if m.get("n_bars", 0) - filled_b >= TL_BARS:
                jlog("tl30_exit", symbol=sym)
                br.close_market(sym)
                m["entry_bars"] = None
            continue

        if ord_buy is not None:
            placed = m.get("order_t")
            if placed and (et_now - placed).total_seconds() / 60 > WIN_MIN:
                jlog("expire", symbol=sym, oid=str(ord_buy.id))
                br.cancel(str(ord_buy.id))
                m["order_t"] = None
                continue
            cur = float(ord_buy.limit_price)
            if abs(B - cur) / cur > REFRESH_EPS:
                jlog("refresh", symbol=sym, oid=str(ord_buy.id),
                     old=cur, new=B)
                res = br.cancel(str(ord_buy.id))
                if res is not None and not isinstance(res, str):
                    o = br.buy_limit(sym, qty_for(B), B)
                    m["order_t"] = et_now
                    m["order_id"] = str(o.id) if o else None
            continue

        if len(positions) >= POS_MAX or minutes_now >= ENTRY_CUTOFF:
            continue
        if m.get("last_exit_bars") is not None and \
                m["n_bars"] - m["last_exit_bars"] < 1:
            continue
        q = qty_for(B)
        o = br.buy_limit(sym, q, B)
        m["order_t"] = et_now
        m["order_id"] = str(o.id) if o else None
        m["entry_B"] = B
        m["entry_c0"] = c0
        jlog("place_bid", symbol=sym, B=B, c0=c0, qty=q, oid=m["order_id"],
             live=br.live)


def reconcile_fills(br: Broker, meta: dict):
    for sym, m in list(meta.items()):
        oid = m.get("order_id")
        if not oid:
            continue
        try:
            o = br.tc.get_order_by_id(oid)
        except Exception:
            continue
        if o.status.value == "filled" and m.get("entry_bars") is None:
            B = m.get("entry_B")
            c0 = m.get("entry_c0")
            m["entry_bars"] = m.get("n_bars", 0)
            jlog("fill", symbol=sym, oid=oid, price=str(o.filled_avg_price),
                 qty=str(o.filled_qty), B=B, c0=c0)
            qty = int(float(o.filled_qty))
            oc = br.sell_oco(sym, qty, B * (1 - STOP_L), c0)
            jlog("oco", symbol=sym,
                 oco_id=str(oc.id) if oc else None,
                 stop=round(B * (1 - STOP_L), 2), target=c0)
            t0 = o.filled_at - timedelta(minutes=2)
            t1 = o.filled_at + timedelta(minutes=2)
            micro = br.trades_at_bid(sym, B, t0, t1)
            if micro:
                jlog("micro", symbol=sym, **micro)
        if o.status.value in ("canceled", "expired", "rejected"):
            m["order_id"] = None
            m["order_t"] = None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--seconds", type=int, default=POLL_S)
    a = ap.parse_args()
    if KILL.exists():
        sys.exit("kill file present")
    br = Broker(a.live)
    jlog("start", live=a.live, once=a.once, probe=a.probe)
    meta: dict = {}
    while True:
        try:
            if KILL.exists():
                jlog("kill_file", note="stopping")
                break
            reconcile_fills(br, meta)
            poll(br, meta, probe=a.probe)
        except Exception:
            jlog("error", traceback=traceback.format_exc()[-800:])
        if a.once:
            break
        time.sleep(a.seconds)


if __name__ == "__main__":
    main()
