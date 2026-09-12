#!/usr/bin/env python3
"""Flush-bid paper-trading bot v2.1 — frozen rule PRE-REG-FLUSH-01.

Position/order lifecycle is managed for ALL tracked symbols every poll,
independent of whether the symbol still appears in the scanner (fixes
unmanaged bids/positions when a name drops out of the top-N).

Rule: while flat, maintain a resting buy limit at B = 0.90 * close of the
latest strict-state minute (strict state: gain >= 1.0 vs prev close, pullback
>= -0.01, r15 >= 0.03) and scanner rank <= 3 at that same minute. Anchor and
120-min expiry refresh at every strict-state minute; the broker order is only
replaced when the executable tick changes (no queue model in the backtest;
reposting every minute would degrade live fills). Fill -> OCO sell
(stop 0.90*B, limit B/0.90) + tl30 time-stop + paper micro capture. Guards:
$2 price floor, POS_MAX concurrent, NOTIONAL sizing, no entries after 15:30
ET, flatten 15:55 ET, KILL file, ownership via client_order_id prefix,
broker-truth reconciliation every poll.

Live translations of the frozen tape (deliberate, see function docstrings):
- r15 runs on a forward-filled minute grid => 15 CLOCK minutes, not 15 bars.
- tl30 is 30 clock minutes from filled_at (30 new bars on the complete
  historical tape); a bare IEX bar count would stretch the hold.
- pf_est tags the anchor with the causal prior-flush count (sparse IEX
  undercounts). It is a journal TAG, never an entry gate: paper trades the
  broad population on purpose and fills are partitioned ex-post.
- n_strict_est tags day breadth (distinct strict-state symbols seen so far
  today, IEX-estimated, reset on restart) on place_bid/fill. TAG only.
- Restart: owned resting buys are cancelled and re-placed on the next
  strict-state minute; held positions rehydrate from the journal.
- KILL cancels owned entry buys and leaves protective OCO sells in place so
  existing positions stay covered; nothing is auto-flattened.
- The paper account is dedicated to this bot: any position in it is adopted.

DRY RUN by default. --live uses the Alpaca paper keys from .env.
Journal: data/forward/bot/<ET-day>/journal.jsonl
Usage: python flush_bot.py [--live] [--once] [--probe] [--seconds 60]
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
MIN_PRICE = 2.0
POS_MAX = 3
NOTIONAL = 2000.0
ENTRY_CUTOFF = 15 * 60 + 30
FLAT_ET = 15 * 60 + 55
POLL_S = 60
REFRESH_EPS = 0.005
TOPN = 10
OWN_PREFIX = "flushbot-"
TERMINAL = {"canceled", "expired", "rejected", "replaced", "done_for_day",
            "stopped", "calculated", "suspended"}

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


_PIT = None


def pit_symbols():
    global _PIT
    if _PIT is None:
        p = ROOT / "data" / "pit" / "pit_symbols.parquet"
        df = pd.read_parquet(p, columns=["vintage", "symbol"])
        v = df["vintage"].max()
        _PIT = set(df[df["vintage"] == v]["symbol"])
    return _PIT


def alpaca_movers(n=50):
    """Live top-gainer list from Alpaca's screener. The TV scanner lags
    microcaps intraday (verified 2026-09-11: SWRD TV 3.56/+59.6% while the
    IEX last trade was 4.92 => +120.6%), which blinds the bot to qualifying
    candidates. The raw movers include warrants/rights/units and sub-$2 names
    (APURR 0.39, CHPGR 0.15, AENTW 0.43, BRLSW 0.05) which would crowd the
    rank gate, so rows are filtered to the latest PIT universe and the frozen
    $2 floor BEFORE ranks are assigned. Same frame shape as tv_scan()."""
    import os as _os
    from alpaca.data.historical.screener import ScreenerClient
    from alpaca.data.requests import MarketMoversRequest
    c = ScreenerClient(_os.environ["ALPACA_API_KEY"],
                       _os.environ["ALPACA_SECRET_KEY"])
    mv = c.get_market_movers(MarketMoversRequest(top=n))
    pit = pit_symbols()
    rows = []
    for g in (getattr(mv, "gainers", []) or []):
        sym = str(getattr(g, "symbol", "") or "").strip().upper()
        px = getattr(g, "price", None)
        pc = getattr(g, "percent_change", None)
        if not sym or px is None or pc is None:
            continue
        if sym not in pit or float(px) < MIN_PRICE:
            continue
        rows.append({"symbol": sym, "rank": len(rows) + 1, "close": float(px),
                     "change": float(pc), "market_cap": 0.0})
    return pd.DataFrame(rows)


def scan_candidates():
    try:
        df = alpaca_movers(50)
        if len(df):
            return df, "alpaca"
    except Exception as e:
        jlog("error", where="alpaca_movers", msg=str(e))
    df = tv_scan()
    if len(df):
        pit = pit_symbols()
        df = df[df["symbol"].isin(pit) & (df["close"] >= MIN_PRICE)].copy()
        df["rank"] = range(1, len(df) + 1)
    return df, "tv"


def state_minutes(bars, prev_close):
    """Strict-state minutes: gain >= 1.00, pullback >= -0.01, r15 >= 0.03.

    r15 is evaluated on a forward-filled 1-minute grid: the research tape is a
    regular minute grid whose stale rows carry the last completed bar
    (lb18.py:97-103), so `c.shift(15)` there means 15 CLOCK minutes. Shifting
    the live IEX frame directly would make it 15 *printed* IEX bars, which on
    sparse IEX can span far longer and mislabels thrust. Only close is ffilled
    (pullback/cummax and gain are unaffected)."""
    b = bars[(bars["et"] >= 570) & (bars["et"] <= 959)].sort_values("ts")
    if len(b) < 2:
        return b.iloc[0:0]
    b = b.drop_duplicates("et", keep="last").set_index("et")
    g = b.reindex(range(570, int(b.index.max()) + 1))
    g["close"] = g["close"].ffill()
    g = g[g["close"].notna()]
    if len(g) < 16:
        return b.iloc[0:0]
    c = g["close"]
    gain = c / prev_close - 1
    pullback = c / c.cummax() - 1
    r15 = c / c.shift(15) - 1
    m = (gain >= 1.0) & (pullback >= -0.01) & (r15 >= 0.03)
    return g[m.fillna(False)].reset_index()


def completed(bars, minutes_now):
    """Bars strictly before the current ET minute (Alpaca may include the
    in-progress minute; the frozen rule only consumes completed bars).
    Pre-open, the frame holds the previous session and is fully completed."""
    if len(bars) == 0 or minutes_now < 570:
        return bars
    return bars[bars["et"] <= minutes_now - 1]


def _et_dt(t):
    """Normalize a filled_at / journal timestamp to tz-aware ET."""
    if isinstance(t, str):
        t = datetime.fromisoformat(t)
    if isinstance(t, pd.Timestamp):
        t = t.to_pydatetime()
    if t.tzinfo is None:
        return t.replace(tzinfo=ET)
    return t.astimezone(ET)


def prior_flush_est(bars, anchor_et):
    """Causal count of flush-episode starts before the anchor minute, mirroring
    lb18_episodes.py:50-71 on the live tape: a start is a NEW bar with
    low <= 0.9 * running session max close (distinct starts, no window).

    Live IEX bars are sparse, so this UNDERCOUNTS flushes printed off-IEX.
    pf_est is a journal TAG only, never an entry gate (paper trades the broad
    population on purpose); partition fills ex-post by pf_est >= 2."""
    if bars is None or len(bars) == 0:
        return 0
    b = bars[bars["et"] < anchor_et].sort_values("ts")
    if len(b) == 0 or "low" not in b:
        return 0
    under = b["low"] <= b["close"].cummax() * (1 - L)
    starts = under & ~under.shift(1, fill_value=False)
    return int(starts.sum())


def note_strict(meta, sym, m, bars):
    """Record `sym` as strict-state-seen today (latest completed bar), the live
    estimate of PRE-REG-DAYTYPE-01's day-breadth feature. Journal tag only, no
    behavior change; IEX-derived and reset on restart, so it undercounts."""
    if not len(bars) or not m.get("prev_close"):
        return
    sm = state_minutes(bars, m["prev_close"])
    if len(sm) and int(sm["et"].iloc[-1]) == int(bars["et"].iloc[-1]):
        meta.setdefault("_strict_seen", set()).add(sym)
        m["n_strict_est"] = len(meta["_strict_seen"])


def qty_for(price):
    if price < MIN_PRICE or price <= 0:
        return 0
    q = int(NOTIONAL / price)
    return q if q >= 1 else 0


def owned(o):
    cid = getattr(o, "client_order_id", None) or ""
    return str(cid).startswith(OWN_PREFIX)


class Broker:
    def __init__(self, live):
        from alpaca.trading.client import TradingClient
        import os
        key = os.environ["ALPACA_API_KEY"]
        sec = os.environ["ALPACA_SECRET_KEY"]
        paper = os.environ.get("ALPACA_PAPER", "true") != "false"
        self.live = live
        self.tc = TradingClient(key, sec, paper=paper)
        from alpaca.data.historical import StockHistoricalDataClient
        self.dc = StockHistoricalDataClient(key, sec)

    def clock(self):
        # Alpaca /clock can 500 transiently (observed 2026-09-11 12:16-13:02 ET);
        # retry once, then fall back to the local ET session clock so a single
        # API hiccup doesn't blind the whole poll cycle.
        for attempt in (1, 2):
            try:
                c = self.tc.get_clock()
                return bool(c.is_open), c.timestamp
            except Exception as e:
                if attempt == 1:
                    time.sleep(1)
                    continue
                jlog("clock_fallback", msg=str(e)[:160])
        n = now_et()
        m = n.hour * 60 + n.minute
        return n.weekday() < 5 and 570 <= m < 960, n

    def open_orders(self):
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        return self.tc.get_orders(filter=GetOrdersRequest(
            status=QueryOrderStatus.OPEN, limit=200))

    def positions(self):
        return {p.symbol: p for p in self.tc.get_all_positions()}

    def order(self, oid):
        try:
            return self.tc.get_order_by_id(oid)
        except Exception:
            return None

    def bars(self, symbol, start):
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from alpaca.data.enums import DataFeed
        # IEX first: real-time intraday on this plan. SIP intraday minute data
        # is stale/limited (2 rows at 09:45) and previously starved the state
        # eval of its 16-bar minimum; SIP stays as fallback for gaps.
        for feed in (DataFeed.IEX, DataFeed.SIP):
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

    def submit_buy(self, symbol, qty, price):
        if not self.live:
            return None
        from alpaca.trading.requests import LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        cid = f"{OWN_PREFIX}b-{symbol}-{int(time.time() * 1000) % 10**9}"
        return self.tc.submit_order(LimitOrderRequest(
            symbol=symbol, qty=qty, side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY, limit_price=round(price, 2),
            client_order_id=cid))

    def sell_oco(self, symbol, qty, stop_price, limit_price):
        if not self.live:
            return None
        from alpaca.trading.requests import (LimitOrderRequest,
                                            StopLossRequest,
                                            TakeProfitRequest)
        from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
        stop = round(stop_price, 2)
        lim = round(limit_price, 2)
        if stop >= lim:
            stop = round(lim - 0.01, 2)
        cid = f"{OWN_PREFIX}s-{symbol}-{int(time.time() * 1000) % 10**9}"
        return self.tc.submit_order(LimitOrderRequest(
            symbol=symbol, qty=qty, side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC, order_class=OrderClass.OCO,
            take_profit=TakeProfitRequest(limit_price=lim),
            stop_loss=StopLossRequest(stop_price=stop),
            client_order_id=cid))

    def cancel(self, oid):
        if not self.live:
            return
        try:
            self.tc.cancel_order_by_id(oid)
        except Exception:
            pass

    def close_market(self, symbol):
        if not self.live:
            return
        try:
            self.tc.close_position(symbol)
        except Exception as e:
            jlog("close_error", symbol=symbol, err=str(e)[:200])


def own_sells(sym, orders):
    return [o for o in orders if o.symbol == sym and o.side.value == "sell"
            and owned(o)]


def bar_index_at(bars, t):
    """Index of the first completed bar at/after timestamp t (for tl30)."""
    if bars is None or len(bars) == 0:
        return 0
    ts = (pd.to_datetime(bars["ts"], utc=True).dt.tz_convert("UTC")
          .dt.tz_localize(None).to_numpy())
    ft = pd.Timestamp(t)
    if ft.tzinfo is None:
        ft = ft.tz_localize("UTC")
    ft = ft.tz_convert("UTC").tz_localize(None).to_numpy()
    mask = ts >= ft
    return int(mask.argmax()) if mask.any() else len(bars) - 1


def protect(br, sym, m, qty):
    """Place the OCO for a filled position; retry until accepted."""
    B = m.get("entry_B")
    if not B:
        return False
    oc = br.sell_oco(sym, qty, B * (1 - STOP_L), B / (1 - L))
    if oc is None and br.live:
        m["protect_pending"] = True
        jlog("protect_retry", symbol=sym, qty=qty, B=B)
        return False
    m["oco_id"] = str(oc.id) if oc else "dryrun"
    m["protect_pending"] = False
    jlog("oco", symbol=sym, oco_id=m["oco_id"],
         stop=round(B * (1 - STOP_L), 2), target=round(B / (1 - L), 2))
    return True


def manage_symbol(br, sym, m, bars, positions, orders, minutes_now, et_now):
    """Lifecycle for one tracked symbol: fill/idle, protect, tl30, refresh,
    expiry, re-arm. Runs regardless of scanner membership."""
    pos = positions.get(sym)
    buys = [o for o in orders if o.symbol == sym and o.side.value == "buy"]
    sells = own_sells(sym, orders)
    resting = buys[0] if buys else None
    nb = len(bars)

    # -- position present -------------------------------------------------
    if pos is not None and float(pos.qty) != 0:
        held = int(float(pos.qty))
        if not m.get("entry_ts"):
            # adopted (restart/foreign-to-meta): reconstruct from avg entry
            B_est = float(pos.avg_entry_price)
            m["entry_B"] = B_est
            m["entry_c0"] = round(B_est / (1 - L), 2)
            m["entry_ts"] = et_now
            jlog("adopt_position", symbol=sym, qty=str(held), entry=B_est)
        # Protection must cover the WHOLE position: a partially filled entry
        # we cancelled can still fill its remainder before the cancel lands,
        # leaving an OCO sized for the old quantity.
        prot = max((int(float(o.qty or 0)) for o in sells), default=0)
        if sells and prot and prot < held:
            for o in sells:
                br.cancel(str(o.id))
            m["oco_id"] = None
            jlog("protect_resize", symbol=sym, was=prot, now=held)
            protect(br, sym, m, held)
            return
        if not sells and (m.get("protect_pending") or not m.get("oco_id")):
            protect(br, sym, m, held)
        # tl30: 30 new bars on the near-complete research tape ~= 30 clock
        # minutes; on sparse IEX a bar count stretches the hold, so time from
        # filled_at instead.
        ets = m.get("entry_ts")
        if ets is not None:
            held_min = (et_now - _et_dt(ets)).total_seconds() / 60.0
            if held_min >= TL_BARS:
                if sells:
                    for o in sells:
                        br.cancel(str(o.id))
                    jlog("tl30_cancel_oco", symbol=sym)
                else:
                    jlog("tl30_exit", symbol=sym)
                    br.close_market(sym)
                    m["last_exit_ts"] = et_now
        return

    # -- no position: exit bookkeeping ------------------------------------
    if m.get("entry_ts") and not sells:
        jlog("exit", symbol=sym, oco=m.get("oco_id"))
        m["last_exit_ts"] = et_now
        m["entry_ts"] = None
        m["entry_B"] = None
        m["entry_c0"] = None
        m["entry_bar_i"] = None
        m["oco_id"] = None
        m["protect_pending"] = False

    if resting is None:
        return

    # -- resting bid management (no position) ------------------------------
    if minutes_now >= ENTRY_CUTOFF:
        jlog("cancel_cutoff", symbol=sym, oid=str(resting.id))
        br.cancel(str(resting.id))
        return

    if nb == 0:
        return
    last_bar_et = int(bars["et"].iloc[-1])
    sm = state_minutes(bars, m["prev_close"])
    strict_now = len(sm) > 0 and int(sm["et"].iloc[-1]) == last_bar_et
    if strict_now:
        new_B = round(float(bars["close"].iloc[-1]) * (1 - L), 2)
        m["anchor_ts"] = et_now
        m["refresh_B"] = new_B
        m["pf_est"] = prior_flush_est(bars, last_bar_et)
    anchor = m.get("anchor_ts")
    if anchor and (et_now - anchor).total_seconds() / 60 > WIN_MIN:
        jlog("expire", symbol=sym, oid=str(resting.id))
        br.cancel(str(resting.id))
        m["order_id"] = None
        return
    cur = float(resting.limit_price)
    m["refresh_B"] = m.get("refresh_B", cur)
    if abs(m["refresh_B"] - cur) / cur > REFRESH_EPS and strict_now:
        jlog("refresh", symbol=sym, oid=str(resting.id), old=cur,
             new=m["refresh_B"])
        br.cancel(str(resting.id))
        o = br.submit_buy(sym, qty_for(m["refresh_B"]), m["refresh_B"])
        if o is not None:
            m["order_id"] = str(o.id)
            m["entry_B"] = round(float(o.limit_price), 2)
        else:
            m["order_id"] = None
            m["entry_B"] = m["refresh_B"]


def read_journal(path):
    events = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
    except FileNotFoundError:
        pass
    return events


def startup_reconcile(br, meta):
    """Broker-truth restart semantics: owned resting entry buys are cancelled
    and re-placed from the next strict-state minute (frozen fidelity choice,
    no persistent local state). Positions are re-adopted with lifecycle
    metadata rehydrated from today's journal / broker so protection and tl30
    keep the original anchor instead of resetting to restart time."""
    meta["_day"] = day_dir().name
    try:
        orders = br.open_orders()
    except Exception as e:
        jlog("startup_reconcile_error", where="open_orders", msg=str(e)[:200])
        return
    canceled = 0
    for o in orders:
        if owned(o) and o.side.value == "buy":
            br.cancel(str(o.id))
            jlog("startup_cancel_entry", symbol=o.symbol, oid=str(o.id))
            canceled += 1
    try:
        positions = br.positions()
    except Exception:
        positions = {}
    filled = {}
    for e in read_journal(day_dir() / "journal.jsonl"):
        if e.get("event") == "fill" and e.get("symbol"):
            filled[e["symbol"]] = e
    rehydrated = 0
    for sym, p in positions.items():
        if float(p.qty) == 0 or sym not in filled:
            continue
        m = meta.setdefault(sym, {})
        m["entry_B"] = filled[sym].get("B")
        m["entry_c0"] = filled[sym].get("c0")
        t = filled[sym].get("ts")
        oid = filled[sym].get("oid")
        o = br.order(oid) if oid else None
        if o is not None and getattr(o, "filled_at", None):
            t = o.filled_at
        m["entry_ts"] = t
        rehydrated += 1
    jlog("startup_reconcile", canceled_entries=canceled, rehydrated=rehydrated,
         day=meta["_day"], live=br.live)


def kill_cleanup(br):
    """KILL stops decisions and new-entry risk only: owned resting buys are
    cancelled, protective OCO sells stay at the broker so existing positions
    remain covered. Positions are not flattened (market-sweeping illiquid
    microcaps is worse than holding a bracketed position). Returns the number
    of entry orders cancelled."""
    try:
        orders = br.open_orders()
    except Exception as e:
        jlog("kill_error", where="open_orders", msg=str(e)[:200])
        return 0
    n = 0
    for o in orders:
        if owned(o) and o.side.value == "buy":
            jlog("kill_cancel_entry", symbol=o.symbol, oid=str(o.id))
            br.cancel(str(o.id))
            n += 1
    for sym in br.positions():
        if not any(owned(o) and o.symbol == sym and o.side.value == "sell"
                   for o in orders):
            jlog("kill_warning", symbol=sym,
                 note="position without owned protective sell")
    return n


def sync_fills(br, meta, orders, positions, bars_cache, et_now):
    """Book owned entry fills, full or partial, and keep protection in step.

    A partially filled limit order stays OPEN, so it never reaches the
    'left the open book' path: inspect every tracked order each poll, cancel
    the remainder once any quantity fills, book the broker-reported fill, and
    protect the quantity actually held."""
    open_by_id = {str(o.id): o for o in orders}
    for sym, m in list(meta.items()):
        if sym == "_day":
            continue
        oid = m.get("order_id")
        if not oid:
            continue
        o = open_by_id.get(oid)
        if o is None:
            o = br.order(oid)  # resolved while we were away
            if o is None:
                continue
        st = str(o.status.value)
        fq = int(float(o.filled_qty or 0))
        if fq > 0 and not m.get("entry_ts"):
            m["entry_B"] = round(float(o.limit_price), 2)
            m["entry_c0"] = round(float(o.limit_price) / (1 - L), 2)
            m["entry_ts"] = getattr(o, "filled_at", None) or et_now
            jlog("fill", symbol=sym, oid=oid, price=str(o.filled_avg_price),
                 qty=str(fq), B=m["entry_B"], c0=m["entry_c0"],
                 pf_est=m.get("pf_est"), n_strict_est=m.get("n_strict_est"))
            if not m.get("micro_done") and getattr(o, "filled_at", None) is not None:
                micro = br.trades_at_bid(sym, m["entry_B"],
                                         o.filled_at - timedelta(minutes=2),
                                         o.filled_at + timedelta(minutes=2))
                m["micro_done"] = True
                if micro:
                    jlog("micro", symbol=sym, **micro)
        if st == "partially_filled":
            jlog("partial", symbol=sym, oid=oid, filled=fq)
            br.cancel(oid)
            m["order_id"] = None
        elif st == "filled":
            m["order_id"] = None
        elif st in TERMINAL:
            m["order_id"] = None
        if m.get("entry_ts"):
            held = fq
            p = positions.get(sym)
            if p is not None and float(p.qty) != 0:
                held = int(float(p.qty))
            if not own_sells(sym, orders) and (
                    m.get("protect_pending") or not m.get("oco_id")):
                protect(br, sym, m, held)


def poll(br: Broker, meta: dict, probe=False):
    open_, _ = br.clock()
    et_now = now_et()
    minutes_now = et_now.hour * 60 + et_now.minute
    today = day_dir().name
    if meta.get("_day") is None:
        meta["_day"] = today
    if meta.get("_day") != today:
        jlog("day_roll", old=meta.get("_day"), new=today)
        try:
            for o in br.open_orders():
                if owned(o):
                    br.cancel(str(o.id))
            for sym in [k for k in meta if k != "_day"]:
                if sym in br.positions():
                    br.close_market(sym)
        except Exception:
            pass
        meta.clear()
        meta["_day"] = today

    if open_ or probe:
        scan, scan_src = scan_candidates()
        if probe:
            jlog("scan", n=int(len(scan)), src=scan_src)
        for r in scan.head(5).itertuples():
            jlog("scan_row", rank=int(r.rank), symbol=r.symbol,
                 close=float(r.close), change=float(r.change), src=scan_src)
        cands = scan[scan["change"] >= CAND_MIN].head(TOPN)
    else:
        cands = pd.DataFrame(columns=["rank", "symbol", "close", "change"])
    positions = br.positions()
    orders = br.open_orders()

    tracked = {k for k in meta if k != "_day"} | set(cands["symbol"]) | set(positions)
    bars_cache = {}
    for sym in tracked:
        if sym not in meta:
            meta[sym] = {}
        m = meta[sym]
        row = cands[cands["symbol"] == sym]
        if "prev_close" not in m and len(row):
            r0 = row.iloc[0]
            if r0["change"] and r0["close"] > 0:
                m["prev_close"] = float(r0["close"]) / (1 + float(r0["change"]) / 100.0)
            else:
                continue
        start_day = et_now if minutes_now >= 570 else et_now - timedelta(days=1)
        start = start_day.replace(hour=9, minute=30, second=0, microsecond=0)
        bars = br.bars(sym, start)
        bars = completed(bars, minutes_now)
        bars_cache[sym] = bars
        note_strict(meta, sym, m, bars)

    sync_fills(br, meta, orders, positions, bars_cache, et_now)

    # lifecycle for every tracked symbol
    for sym in list(tracked):
        m = meta.get(sym)
        if m is None:
            continue
        try:
            manage_symbol(br, sym, m, bars_cache.get(sym, pd.DataFrame()),
                          positions, orders, minutes_now, et_now)
        except Exception:
            jlog("error", where=f"manage:{sym}",
                 traceback=traceback.format_exc()[-500:])

    # EOD flatten (owned only)
    if minutes_now >= FLAT_ET:
        if positions or orders:
            for o in orders:
                if owned(o):
                    jlog("cancel_eod", symbol=o.symbol, oid=str(o.id))
                    br.cancel(str(o.id))
            for sym in list(positions):
                if sym in meta:
                    jlog("flatten", symbol=sym)
                    br.close_market(sym)
        return

    if probe or not open_:
        if not open_ and not probe:
            jlog("market_closed", note="no action")
        if probe:
            for r in cands.itertuples():
                sym = r.symbol
                b = bars_cache.get(sym)
                m = meta.get(sym, {})
                if b is None or not len(b) or not m.get("prev_close"):
                    continue
                sm = state_minutes(b, m["prev_close"])
                if len(sm) == 0:
                    continue
                if int(r.rank) > 3:
                    continue
                last = sm.iloc[-1]
                jlog("probe", symbol=sym, rank=int(r.rank),
                     state_min=int(last["et"]),
                     B=round(float(last["close"]) * (1 - L), 2),
                     c0=round(float(last["close"]), 2))
        return
    if minutes_now >= ENTRY_CUTOFF:
        return

    # new bids
    n_orders = len([o for o in orders if o.side.value == "buy" and owned(o)])
    slots = POS_MAX - len(positions) - n_orders
    for r in cands.itertuples():
        if slots <= 0:
            break
        sym = r.symbol
        m = meta.get(sym, {})
        if sym in positions or any(o.symbol == sym for o in orders):
            continue
        if int(r.rank) > 3 or float(r.close) < MIN_PRICE:
            continue
        b = bars_cache.get(sym)
        if b is None or not len(b) or not m.get("prev_close"):
            continue
        sm = state_minutes(b, m["prev_close"])
        last_bar_et = int(b["et"].iloc[-1])
        if len(sm) == 0 or int(sm["et"].iloc[-1]) != last_bar_et:
            continue  # require strict state at the latest completed bar
        if m.get("last_exit_ts") and m["last_exit_ts"] >= et_now - timedelta(seconds=60):
            continue
        B = round(float(b["close"].iloc[-1]) * (1 - L), 2)
        q = qty_for(B)
        if q <= 0:
            continue
        pf = prior_flush_est(b, last_bar_et)
        o = br.submit_buy(sym, q, B)
        m["entry_B"] = B
        m["entry_c0"] = round(float(b["close"].iloc[-1]), 2)
        m["anchor_ts"] = et_now
        m["pf_est"] = pf
        m["order_id"] = str(o.id) if o else None
        jlog("place_bid", symbol=sym, B=B, c0=m["entry_c0"], qty=q, rank=int(r.rank),
             pf_est=pf, n_strict_est=m.get("n_strict_est"), oid=m["order_id"], live=br.live)
        slots -= 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--seconds", type=int, default=POLL_S)
    a = ap.parse_args()
    br = Broker(a.live)
    jlog("start", live=a.live, once=a.once, probe=a.probe)
    meta: dict = {}
    if KILL.exists():
        jlog("kill_file", note="stopping at startup", canceled=kill_cleanup(br))
        return
    startup_reconcile(br, meta)
    while True:
        try:
            if KILL.exists():
                jlog("kill_file", note="stopping", canceled=kill_cleanup(br))
                break
            poll(br, meta, probe=a.probe)
        except Exception:
            jlog("error", traceback=traceback.format_exc()[-800:])
        if a.once:
            break
        time.sleep(a.seconds)


if __name__ == "__main__":
    main()
