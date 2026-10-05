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
  strict-state minute; leftover owned entries with no anchor are cancelled
  (never given an invented anchor) and foreign buys are never adopted; held
  positions rehydrate from the journal. Rehydration also RETAINS the OWN
  protective OCO family the previous process left working (root id, never a
  foreign order) and accounts the shares it has already sold, and baselines
  the entered inventory as broker NET plus those accounted sales; a restart
  must never re-submit a bracket over shares an inherited family already
  sold, nor under-protect a family that sold part of the entry.
  Protection acceptance (a covering bracket already on the book) is cached by
  broker id too, and a working or unknown family blocks replacement and
  close until it resolves.
- Cancels are never assumed: a partially filled entry or a replaced bid keeps
  its broker id until the broker reports a terminal status, and a cancel
  failure is logged (not treated as resolved); a missing/stale protective OCO
  is re-submitted from broker truth; a due tl30 exit outranks (re)protection.
- Protection is sized by ACTIVE REMAINING quantity, never an order's original
  qty: Alpaca shrinks an OCO's sibling leg as the other leg fills ("if the
  take-profit order is partially filled, the stop-loss order will be adjusted
  to the remaining quantity"), so a family whose target leg filled 40 of 100
  still protects only 60. Mutually exclusive legs are compared by max, never
  summed, and only LIVE legs count: a canceled/expired/rejected/filled leg
  protects nothing, so a canceled root beside a still-working child
  contributes only that child's remainder. A leg whose status the broker has
  not reported is excluded rather than assumed live. A retained family
  missing from the open snapshot is measured the same way by broker id, so it
  is resized when it no longer covers the holding; a family with no
  identifiable live leg is UNKNOWN — never zero, never its nominal qty — and
  blocks both replacement and close rather than cancelling protection the
  broker has not confirmed is gone.
- A scanner outage disables new entries for that poll only; fills, protection,
  expiry, tl30 and EOD keep running from broker truth.
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
ALERT = ROOT / "data" / "forward" / "bot" / "ALERT"

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
        # nested=True so each OCO parent carries its legs (take-profit parent,
        # stop-loss child); without it a child-only fill is invisible.
        return self.tc.get_orders(filter=GetOrdersRequest(
            status=QueryOrderStatus.OPEN, limit=200, nested=True))

    def positions(self):
        return {p.symbol: p for p in self.tc.get_all_positions()}

    def order(self, oid):
        from alpaca.trading.requests import GetOrderByIdRequest
        try:
            # nested=True: reconcile the whole OCO family, not just the parent
            # (a child leg may hold the fill / a nonterminal status).
            return self.tc.get_order_by_id(
                oid, filter=GetOrderByIdRequest(nested=True))
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
        """Request a broker cancel and report the outcome. A failure is never
        swallowed as success: callers retain the order id and retry until the
        broker reports a terminal status (a cancel can fail or sit pending)."""
        if not self.live:
            return True
        try:
            self.tc.cancel_order_by_id(oid)
            return True
        except Exception as e:
            jlog("cancel_error", oid=str(oid), err=str(e)[:160])
            return False

    def close_market(self, symbol):
        """Submit a market close and report the accepted order. Returns the
        broker order object (or a "dryrun" sentinel in paper mode), or None
        when the close was rejected/failed: callers must not latch a phantom
        exit on None — retry instead."""
        if not self.live:
            return "dryrun"
        try:
            return self.tc.close_position(symbol)
        except Exception as e:
            jlog("close_error", symbol=symbol, err=str(e)[:200])
            return None


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
    _set_families(m, [str(oc.id) if oc else "dryrun"])
    m["protect_pending"] = False
    m["protect_qty"] = qty
    jlog("oco", symbol=sym, oco_id=m["oco_id"],
         stop=round(B * (1 - STOP_L), 2), target=round(B / (1 - L), 2))
    return True


def _due_exit(m, et_now):
    """tl30 is due from the entry timestamp (or an already-latched exit)."""
    if m.get("exit_due"):
        return True
    ets = m.get("entry_ts")
    if not ets:
        return False
    return (et_now - _et_dt(ets)).total_seconds() / 60.0 >= TL_BARS


def _submit_exit(br, sym, m, et_now):
    """Flatten once and latch ONLY an accepted close order. A failed submit
    leaves the latch clear so the next poll retries instead of stranding a
    held position behind a phantom exit."""
    oc = br.close_market(sym)
    if oc is None:
        jlog("close_retry", symbol=sym)
        return
    oid = getattr(oc, "id", None)
    m["exit_submitted"] = True
    m["exit_order_id"] = str(oid) if oid is not None else "dryrun"
    _clear_families(m)
    m["last_exit_ts"] = et_now
    jlog("tl30_exit", symbol=sym, exit_oid=m["exit_order_id"])


def _reconcile_exit(br, sym, m):
    """Reconcile a latched market close against broker truth. Its actual
    fills are accounted as inventory sold (not merely its acceptance), so a
    close that has not filled yet cannot mask a still-held position; a
    rejected/canceled/expired close clears the latch so the next poll
    retries. An unavailable lookup changes nothing."""
    oid = m.get("exit_order_id")
    if not oid or oid == "dryrun":
        return
    o = br.order(oid)
    if o is None:
        return
    _account_family(m, o)          # actual close fills only
    st = str(o.status.value)
    if st in TERMINAL or st == "filled":
        # Resolved (filled/canceled/rejected/expired): clear the latch so the
        # caller can retry if real inventory remains or finish if flat. A
        # still-working close (new/accepted/pending_cancel/partially_filled)
        # keeps the latch and ID so no duplicate close is submitted.
        m["exit_submitted"] = False
        m["exit_order_id"] = None
        jlog("close_reconcile", symbol=sym, status=st)


def _entry_terminal_no_position(br, oid, m):
    """True only when a deferred replacement's predecessor is confirmed
    broker-terminal AND no fill is being established. Absence from the open
    snapshot, an unavailable lookup, or a nonterminal (pending_cancel) status
    is NOT confirmation."""
    if m.get("entry_ts"):
        return False
    if not oid:
        return True
    o = br.order(oid)
    if o is None:
        return False
    return str(o.status.value) in TERMINAL


def _begin_resize(br, sym, m, sells, held):
    """Cancel the undersized protective OCO and wait for broker-terminal
    confirmation before submitting the replacement (no overlapping OCO).

    Every family this symbol is protecting with must move to the cancel set
    BEFORE the cached identities are dropped: the visible OWN sells plus
    every already-retained root, which may include a family missing from
    this snapshot (a restart-retained id whose snapshot lagged). Recording
    only the visible orders and then clearing the cache would forget a still
    working or unknown family and let the replacement overlap it."""
    prot = max((int(float(o.qty or 0)) for o in sells), default=0)
    ids = []
    for oid in _family_ids(m) + [str(o.id) for o in sells]:
        if oid and oid != "dryrun" and oid not in ids:
            ids.append(oid)
    m["resize_cancel_ids"] = ids
    _clear_families(m)
    for oid in ids:
        br.cancel(oid)
    jlog("protect_resize", symbol=sym, was=prot, now=held, cancelling=len(ids))


def _covers_held(br, sym, m, pos, sells=()):
    """Whether live protection still covers this entry's known holding,
    acting on the shortfall. The quantity is derived HERE, after every
    fill-reconciling read the caller made, rather than taken as a pre-computed
    scalar: reconciling a family can record a new fill (a cancellation race),
    and a `held` value read before that would be stale — over-protecting
    shares just proven sold.

    `pos` is only an UPPER BOUND on the holding (it may lag or be absent);
    booked inventory remains the ledger truth, so passing None is correct
    whenever the position snapshot cannot be trusted.

    Returns True when the caller must STOP this poll without submitting
    anything: coverage is sufficient, unverifiable, or a resize was just
    started and must reach terminal first. Returns False only when NOTHING
    live protects the holding, so the caller must submit — and the caller must
    re-derive the quantity again before doing so, because acting on a
    shortfall can itself reconcile more fills.

    ONE handler for both consumers (position-present and position-absent) so
    an invariant cannot be fixed on one path and skipped on the other:
      * coverage is ACTIVE remaining LIVE-leg size versus the known holding,
        never an order's nominal qty and never a stale submitted watermark;
      * a WORKING family that no longer covers the holding is resized — a
        bracket sized for a pre-fill quantity leaves its remainder uncovered;
      * UNKNOWN coverage (an unresolvable family, or one with no live leg)
        neither resizes nor submits, because cancelling or duplicating
        protection the broker has not confirmed is gone is worse than
        deferring;
      * a resize requests a cancel for EVERY tracked id, so the replacement
        waits until each family is broker-terminal.

    `sells` are this snapshot's OWN protective orders. When empty, coverage
    comes from the tracked roots read by id, so a family missing from the open
    snapshot is still measured rather than assumed to cover."""
    held = _effective_held(m, pos)           # AFTER the caller's reconciling reads
    if held <= 0:
        return True                         # nothing left to protect
    if sells:
        prot = 0
        for o in sells:
            c = _active_coverage(o)
            if c is None:
                return True                 # unverifiable: wait
            prot = max(prot, c)
        cov = prot
    else:
        # Family hidden from the snapshot: measure the retained roots by id.
        cov = _cached_coverage(br, m)
        if cov is None:
            return True                     # unknown: never assume, never act
    if cov >= held:
        return True                         # sufficiently covered
    # Under-covered. Only cancel into a resize when something live is actually
    # working; otherwise there is nothing to wait for and the caller submits.
    if _any_family_working(br, m, sells):
        _begin_resize(br, sym, m, sells, held)
        return True
    return False


def _any_family_working(br, m, sells=()):
    """True when a protective family is still working and can be cancelled
    into a resize.

    An order present in the open-orders snapshot IS the broker's own evidence
    that it is live, so visible sells are judged from the snapshot object
    itself rather than through a second lookup whose absence says nothing.
    Tracked roots are additionally checked by id, so a family that dropped out
    of the snapshot still counts as something to wait on."""
    for o in sells or ():
        if _family_state(_family_members(o)) == "working":
            return True
    for oid in _family_ids(m):
        o = br.order(oid)
        if o is not None and _family_state(_family_members(o)) == "working":
            return True
    return False


def _account_exit_fill(m, o):
    """Account an exit order's (protective OCO or market close) cumulative
    filled_qty for ANY status — partially_filled, filled, canceled, expired
    or still working. Positive deltas are added to the monotonic lifetime
    sold total, so shares sold before a cancel won are never re-protected.
    Idempotent per order id (a per-order watermark); the total is never
    decremented."""
    if o is None:
        return
    oid = str(getattr(o, "id", "") or "")
    if not oid:
        return
    try:
        fq = int(float(getattr(o, "filled_qty", 0) or 0))
    except (TypeError, ValueError):
        fq = 0
    marks = m.setdefault("sold_marks", {})
    prev = int(marks.get(oid, 0))
    if fq > prev:
        marks[oid] = fq
        m["sold_total"] = int(m.get("sold_total") or 0) + (fq - prev)


def _family_members(o):
    """OCO family: the order itself plus its nested legs (Alpaca OCO =
    take-profit parent + stop-loss child), deduped by broker order id. A
    plain order is a family of one. Legs are reached only from an owned
    root, so a generated (non-OWN) child id is still part of this family and
    never adopted as a foreign order."""
    members = []
    seen = set()
    stack = [o]
    raw = getattr(o, "legs", None)
    if raw:
        stack.extend(raw)
    for x in stack:
        if x is None:
            continue
        xid = str(getattr(x, "id", "") or "")
        if not xid or xid in seen:
            continue
        seen.add(xid)
        members.append(x)
    return members


def _remaining_qty(o):
    """Shares an order can still fill (qty minus what already filled)."""
    try:
        q = int(float(getattr(o, "qty", 0) or 0))
    except (TypeError, ValueError):
        return 0
    try:
        fq = int(float(getattr(o, "filled_qty", 0) or 0))
    except (TypeError, ValueError):
        fq = 0
    return max(0, q - fq)


def _leg_active(o):
    """True only for a leg that can still reach the market. A TERMINAL leg
    (canceled/expired/rejected/replaced/done_for_day/...) and a filled leg can
    sell nothing more, so neither contributes coverage. A MISSING/blank status
    is NOT active: the broker has not confirmed the leg is live, and an
    unconfirmed leg must never be read as protection."""
    st = str(getattr(getattr(o, "status", None), "value", "") or "")
    if not st:
        return False
    return st not in TERMINAL and st != "filled"


def _active_coverage(o):
    """Shares an OWN protective family can still SELL if triggered now, or
    None when no live leg can be identified (coverage genuinely unknown).

    Alpaca's OCO legs are mutually exclusive (only one may fill) and the
    sibling leg's quantity is ADJUSTED DOWN as the other leg fills — "If the
    take-profit order is partially filled, the stop-loss order will be
    adjusted to the remaining quantity" (docs.alpaca.markets, Placing
    Orders). So an OCO whose take-profit has filled 40 of 100 can still only
    sell the remaining 60: its original ``qty`` no longer describes what is
    protected.

    Coverage is the MAXIMUM remaining quantity across the family's LIVE
    members, never their sum — the legs are alternatives, so summing them
    would double-count the same shares.

    Only legs that can still reach the market count. A canceled/expired/
    rejected/filled leg protects nothing, and a canceled parent beside a
    still-working child is a real broker state (the family as a whole stays
    working) — counting the dead parent's full qty overstates coverage and
    leaves the child's remainder unprotected. A leg whose status the broker
    has not reported is excluded rather than assumed active, so an
    unconfirmed leg is never fabricated into protection.

    None (no identifiable live leg) means UNKNOWN, distinct from 0: the caller
    must keep waiting, never resize and never assume full coverage."""
    live = [_remaining_qty(x) for x in _family_members(o) if _leg_active(x)]
    if not live:
        return None
    return max(live)


def _account_family(m, o):
    """Account every member of an exit order's OCO family (parent + legs) by
    unique order id watermark, so a child fill the parent does not report is
    recorded exactly once (flat+nested duplication is deduped by id)."""
    for x in _family_members(o):
        _account_exit_fill(m, x)


def _family_state(members):
    """Aggregate OCO family state: 'working' if ANY member is unresolved
    (missing status, or not TERMINAL and not 'filled'); else 'filled' if any
    member filled, otherwise 'dead'. A canceled parent with a still-working
    child is NOT terminal."""
    any_filled = False
    for x in members:
        if x is None:
            return "working"
        st = str(getattr(getattr(x, "status", None), "value", ""))
        if not st:
            return "working"      # unknown status: never assume resolved
        if st == "filled":
            any_filled = True
        elif st not in TERMINAL:
            return "working"
    return "filled" if any_filled else "dead"


def _resolve_resize_cancels(br, sym, m, open_by_id):
    """Retry/confirm cancels of an in-flight protective cancel (protection
    resize OR tl30 exit). Returns True while any cancelled OCO family still
    has an unresolved member or its lookup unavailable (caller must NOT
    submit/close); False once every family is resolved. Absence from the open
    snapshot is NOT confirmation, and a canceled parent does not resolve a
    still-working child; any sold shares are accounted, never left pending."""
    pending = []
    for sid in list(m.get("resize_cancel_ids") or []):
        o = open_by_id.get(sid)
        if o is None:
            o = br.order(sid)
        if o is None:
            pending.append(sid)          # lookup unavailable: keep waiting
            continue
        _account_family(m, o)            # sold before the cancel won
        if _family_state(_family_members(o)) == "working":
            pending.append(sid)
            br.cancel(sid)
            continue
        # resolved (filled or dead): drop
    if pending:
        m["resize_cancel_ids"] = pending
        jlog("protect_resize_wait", symbol=sym, pending=len(pending))
        return True
    m["resize_cancel_ids"] = []
    return False


def _cached_oco_order(br, m):
    """Classify every tracked protective OCO family against broker truth,
    returning a representative parent order and the aggregate family state,
    while accounting every family member's fill (any status).

    Absence from the open snapshot is not proof of cancellation: only a
    fully-resolved family is terminal. The aggregate is the most
    unresolved state across the tracked roots — an unknown lookup outranks a
    known-working family, which outranks resolved — so ANY working or unknown
    family blocks a duplicate submission or a close. Resolved roots are
    dropped from the tracked set so a later adoption or resubmission is not
    blocked by settled history."""
    ids = _family_ids(m)
    if not ids:
        return None, "none"
    first = None
    unknown = working = filled = False
    keep = []
    for oid in ids:
        o = br.order(oid)
        if o is None:
            unknown = True
            keep.append(oid)
            continue
        _account_family(m, o)
        st = _family_state(_family_members(o))
        if st == "working":
            working = True
            keep.append(oid)
        elif st == "filled":
            filled = True
        else:                              # dead: resolved, stop tracking it
            continue
        if first is None:
            first = o
    _set_families(m, keep)
    if unknown:
        return first, "unknown"
    if working:
        return first, "working"
    if filled:
        return first, "filled"
    return first, "dead"


def _cached_coverage(br, m):
    """Active remaining protection of every tracked family, per broker truth.

    Reads each cached root by id (not from the open snapshot), so a working
    family that dropped out of the snapshot still contributes the shares it
    can actually sell — the same ACTIVE (qty - filled) measure as the visible
    path, for the same reason: Alpaca shrinks the sibling leg as the other
    leg fills, so the root's original qty overstates coverage.

    Only LIVE legs count: a canceled/expired/filled leg protects nothing, and a
    canceled parent beside a still-working child is a real broker state whose
    dead leg must not be read as coverage.

    Returns None when any tracked root cannot be resolved OR exposes no
    identifiable live leg: that family's coverage is unknown, never zero and
    never the root qty, so the caller must keep waiting rather than resize
    against a guess."""
    ids = _family_ids(m)
    if not ids:
        return 0
    total = 0
    for oid in ids:
        o = br.order(oid)
        if o is None:
            return None
        cov = _active_coverage(o)
        if cov is None:
            return None          # no live leg: unknown, not zero
        total += cov
    return total


def _family_ids(m):
    """Broker order ids of every protective OCO family root this symbol's
    lifecycle tracks: the primary ``oco_id`` plus any additional retained
    roots (a symbol can briefly carry more than one live bracket), deduped
    and with the dry-run placeholder dropped."""
    ids = []
    for oid in [m.get("oco_id")] + list(m.get("oco_family_ids") or []):
        oid = str(oid or "")
        if oid and oid != "dryrun" and oid not in ids:
            ids.append(oid)
    return ids


def _set_families(m, ids):
    """Track exactly these protective family roots: the first is the primary
    ``oco_id``, the rest are retained so a multi-family book still blocks a
    duplicate submission until every family resolves."""
    keep = []
    for oid in ids:
        oid = str(oid or "")
        if oid and oid != "dryrun" and oid not in keep:
            keep.append(oid)
    m["oco_id"] = keep[0] if keep else None
    m["oco_family_ids"] = keep[1:]


def _clear_families(m):
    _set_families(m, [])


def _retain_protection(m, sells):
    """Cache the OWN protective families already on the broker book for this
    symbol and account what they have sold.

    Startup rehydration, position adoption and healthy-protection acceptance
    all pass through here, so an inherited bracket is reconciled by broker id
    instead of being re-derived from a (possibly stale) held snapshot. Only
    OWN sells reach this path: a foreign order is never adopted as our
    protection. A family already being cancelled is owned by
    ``resize_cancel_ids`` and is never re-adopted, and an id already covered
    by a tracked family is not added twice."""
    if m.get("resize_cancel_ids"):
        return
    ids = _family_ids(m)
    covered = set(ids)
    added = False
    # A nested snapshot can list one family twice (parent and its own leg);
    # adopt the parent first so the leg is covered by its own family.
    for o in sorted(sells, key=lambda o: 0 if getattr(o, "legs", None) else 1):
        oid = str(getattr(o, "id", "") or "")
        if not oid or oid == "dryrun" or oid in covered:
            continue
        for x in _family_members(o):
            covered.add(str(getattr(x, "id", "") or ""))
        ids.append(oid)
        added = True
        _account_family(m, o)     # fills won before we inherited it
    if added:
        _set_families(m, ids)


def _seed_entry_baseline(m, net_qty):
    """Baseline the entered-inventory watermark of an adopted/rehydrated
    position from broker truth.

    ``entry_filled_qty`` is the LIFETIME entered quantity and ``sold_total``
    the lifetime sold quantity, so the shares still held from this entry are
    their difference. A family inherited from a previous process has already
    had its sales accounted, so a fresh baseline is broker NET plus those
    accounted sales: deriving it from NET alone would subtract them a second
    time and UNDER-protect (NET 60 with 40 already sold must still cover 60,
    not 20).

    The watermark is raised again on later polls ONLY while nothing has been
    accounted as sold: with no sale evidence the broker net position is the
    truth, so entry shares still filling in must not be clamped to an
    adoption-time estimate. Once a sell IS accounted the broker net is
    bounded by entered-minus-sold, because the net position may be stale
    (the protective family sold before the snapshot printed it) and raising
    the watermark there would resurrect those shares. A position this
    process already tracks keeps its authoritative baseline: its own entry
    order reports real cumulative fills through ``_sync_one_fill``."""
    entered = int(m.get("entry_filled_qty") or 0)
    net = max(0, int(net_qty))
    sold = int(m.get("sold_total") or 0)
    if not entered:
        m["entry_filled_qty"] = net + sold
    elif not sold and net > entered:
        m["entry_filled_qty"] = net


def _effective_held(m, pos):
    """Shares currently held from this entry: the broker net position bounded
    by known entered-minus-sold inventory. The sold total is a monotonic
    lifetime watermark (never decremented), so a stale snapshot cannot
    resurrect sold shares and a fresh snapshot cannot erase the evidence that
    they are gone. With no position printed yet the estimate is the booked
    entry watermark minus sold; an unknown baseline (no entered accounting)
    trusts the broker position."""
    sold = int(m.get("sold_total") or 0)
    entered = int(m.get("entry_filled_qty") or 0)
    if pos is not None and float(pos.qty) != 0:
        raw = int(float(pos.qty))
        known = max(0, entered - sold) if entered > 0 else raw
        return min(raw, known)
    return max(0, entered - sold) if entered > 0 else 0


def _book_exit(sym, m, et_now):
    """Confirmed flat exit: clear the per-position lifecycle so the next
    poll's admission/eligibility semantics are restored (the original
    no-position bookkeeping), while retaining the entry order id and
    cumulative watermarks (entry_filled_qty / sold_total / sold_marks) so a
    later remainder is booked and protected correctly."""
    jlog("exit", symbol=sym, oco=m.get("oco_id"))
    m["last_exit_ts"] = et_now
    m["entry_ts"] = None
    m["entry_B"] = None
    m["entry_c0"] = None
    m["entry_bar_i"] = None
    _clear_families(m)
    m["protect_pending"] = False
    m["protect_qty"] = 0
    m["entry_open_qty"] = 0
    m["bid_replacing"] = False
    m.pop("exit_due", None)
    m.pop("exit_submitted", None)
    m.pop("exit_order_id", None)


def _tl30_exit(br, sym, m, sells, open_by_id, et_now, held, pos):
    """Overdue time-limit exit. Cancel the protective OCO and flatten ONLY
    once every protection cancel is resolved: a pending cancel that vanished
    from the open snapshot must never let a market close overlap a
    still-working protective sell. A positively-working cached OCO absent
    from the snapshot is cancelled (retained to terminal) so the exit still
    advances. A filled protective order / zero accounted held is never
    market-closed as a stale holding."""
    m["exit_due"] = True
    if sells:
        ids = m.setdefault("resize_cancel_ids", [])
        for o in sells:
            sid = str(o.id)
            if sid not in ids:
                ids.append(sid)
            br.cancel(sid)
        jlog("tl30_cancel_oco", symbol=sym)
        return
    if m.get("resize_cancel_ids"):
        if _resolve_resize_cancels(br, sym, m, open_by_id):
            return
    # Reconcile a latched close FIRST: a terminal (incl filled) close clears
    # its latch after accounting its actual qty, so a positive residual can
    # be managed/closed; a still-working close retains the ID (no duplicate).
    if m.get("exit_submitted"):
        _reconcile_exit(br, sym, m)
        if m.get("exit_submitted"):
            return
        held = _effective_held(m, pos)
    oco, state = _cached_oco_order(br, m)
    if state == "filled":
        _clear_families(m)
        m["protect_qty"] = 0
        state = "none"
    if state == "working":
        # Positively working but absent from the snapshot: request its cancel
        # and retain the id until the broker resolves it. EVERY unresolved
        # family is cancelled, not just the primary root, so a second live
        # bracket can never overlap the market close.
        ids = m.setdefault("resize_cancel_ids", [])
        for oid in _family_ids(m):
            if oid not in ids:
                ids.append(oid)
            br.cancel(oid)
        jlog("tl30_cancel_cached", symbol=sym, oid=m.get("oco_id"))
        return
    if state == "unknown":
        jlog("tl30_wait_oco", symbol=sym)
        return
    if state == "dead":
        _clear_families(m)
    held = _effective_held(m, pos)   # after all cancel/fill accounting
    if held <= 0:
        # Cumulative accounting says every booked share is already sold; a
        # stale snapshot must not trigger a market close (oversell).
        m["protect_qty"] = 0
        if pos is None:
            # Confirmed fully-sold flat: restore exit bookkeeping so the next
            # poll's re-entry/eligibility semantics are unchanged. The entry
            # order id and cumulative watermarks are retained for a later
            # remainder.
            _book_exit(sym, m, et_now)
        return
    _submit_exit(br, sym, m, et_now)


def manage_symbol(br, sym, m, bars, positions, orders, minutes_now, et_now):
    """Lifecycle for one tracked symbol: fill/idle, protect, tl30, refresh,
    expiry, re-arm. Runs regardless of scanner membership."""
    pos = positions.get(sym)
    # Only OUR entry buys are managed; a foreign buy for the same symbol is
    # never cancelled, refreshed, or adopted.
    buys = [o for o in orders
            if o.symbol == sym and o.side.value == "buy" and owned(o)]
    sells = own_sells(sym, orders)
    open_by_id = {str(o.id): o for o in orders}
    resting = buys[0] if buys else None
    nb = len(bars)

    # Protective sells that have printed shares (partially filled, filled, or
    # canceled with a fill) have SOLD inventory: account every watermark
    # before sizing anything. The lifetime total and the cached OCO evidence
    # keep a stale held snapshot from re-protecting or market-closing shares
    # that are already gone.
    for o in sells:
        _account_family(m, o)
    # Retain the OWN families already working at the broker. This runs before
    # any sizing so a restart/adoption (and a healthy bracket accepted as
    # covering) reconciles the inherited ids instead of submitting a second
    # bracket from a possibly stale held snapshot.
    _retain_protection(m, sells)
    _oco, _state = _cached_oco_order(br, m)
    if _state == "filled":
        _clear_families(m)
        m["protect_qty"] = 0
    due = _due_exit(m, et_now)

    # -- position present -------------------------------------------------
    if pos is not None and float(pos.qty) != 0:
        raw = int(float(pos.qty))
        if not m.get("entry_ts"):
            # adopted (restart/foreign-to-meta): reconstruct from avg entry
            B_est = float(pos.avg_entry_price)
            m["entry_B"] = B_est
            m["entry_c0"] = round(B_est / (1 - L), 2)
            m["entry_ts"] = et_now
            jlog("adopt_position", symbol=sym, qty=str(raw), entry=B_est)
            # Baseline the entered inventory from broker NET plus the sales an
            # inherited family has already accounted. A position this process
            # is ALREADY tracking (a rehydrated restart keeps entry_ts, so this
            # branch is not the only path that needs a baseline) is baselined by
            # startup_reconcile from the same broker truth; seeding it again
            # here would clamp later entry fills to an adoption-time estimate.
            _seed_entry_baseline(m, raw)
        # A bid fill (even one we were cancelling) resolves any refresh in
        # flight; the position path owns this symbol now.
        m.pop("bid_replacing", None)
        if minutes_now >= FLAT_ET:
            return  # EOD flatten owns the exit (poll cancels + closes)
        resolved_resize = False
        if m.get("resize_cancel_ids"):
            # Confirm/retry the in-flight protective cancel first: it may have
            # filled (a cancellation race), which accounting must record
            # before we size a replacement.
            if _resolve_resize_cancels(br, sym, m, open_by_id):
                return
            resolved_resize = True
        held = _effective_held(m, pos)
        m["entry_open_qty"] = held
        # tl30 takes precedence over (re)protection: an overdue exit must stay
        # reachable even when the OCO is rejected/unavailable.
        if due:
            _tl30_exit(br, sym, m, sells, open_by_id, et_now, held, pos)
            return
        if held <= 0:
            # Every booked share is accounted as sold; a stale snapshot must
            # not re-protect or close them until it prints flat.
            return
        if not sells or resolved_resize:
            # Absence from the snapshot is NOT cancellation: reconcile the
            # cached OCO before resubmitting. This call can account a new
            # fill, so every quantity below is re-derived AFTER it.
            state = _cached_oco_order(br, m)[1]
            if state == "unknown":
                return
            if state == "working":
                # A retained working root missing from the snapshot must still
                # COVER the held qty; its ACTIVE remaining size can be smaller
                # (a leg filled, so Alpaca shrank the sibling). The handler
                # derives the quantity post-reconcile and resizes.
                _covers_held(br, sym, m, pos, [])
                return
            if state == "dead":
                jlog("protect_missing", symbol=sym, cached=m.get("oco_id"))
                _clear_families(m)
            # Re-derive after reconciliation: shares proven sold this poll
            # must not be re-protected.
            held = _effective_held(m, pos)
            if held > 0:
                protect(br, sym, m, held)
            return
        # Coverage is measured by ACTIVE remaining LIVE-leg size, never an
        # order's nominal qty: Alpaca shrinks an OCO's sibling leg as the
        # other leg fills, and a canceled/expired/filled leg protects nothing.
        # Shared with the position-absent branch so both consumers apply the
        # same working/unknown/resize invariants.
        if not _covers_held(br, sym, m, pos, sells):
            held = _effective_held(m, pos)
            if held > 0:
                protect(br, sym, m, held)
        return

    # -- no position: exit handling, else cover a lagging snapshot ---------
    # A flat position with no resolved protection can mean a genuine exit or
    # a fresh fill the snapshot has not printed yet. Cumulative-vs-covered
    # accounting decides; lifecycle state is never cleared while a protective
    # order may still be live.
    if m.get("entry_ts"):
        # Reconcile EVERYTHING that can record a fill BEFORE deriving any
        # quantity: resolving a protective cancel, or reconciling a cached
        # family, can account shares just sold (a cancellation race). A held
        # value read before those calls is stale and would size protection for
        # shares this very poll proved sold. Booked inventory is the ledger
        # truth here; the position snapshot may lag or be absent entirely.
        if due:
            _tl30_exit(br, sym, m, sells, open_by_id, et_now,
                       _effective_held(m, None), None)
            return
        if m.get("resize_cancel_ids"):
            if _resolve_resize_cancels(br, sym, m, open_by_id):
                return
        _cached_oco_order(br, m)
        held_est = _effective_held(m, None)      # post-reconcile, pos absent
        if held_est <= 0:
            # Ledger exhausted — never from a watermark or an absent snapshot.
            # Confirm flat only while no protective order can still be live.
            if not sells and not m.get("resize_cancel_ids"):
                _book_exit(sym, m, et_now)
            return
        # A WORKING family is not automatically adequate: it must still cover
        # the shares this entry has booked. A bracket sized for a pre-fill
        # quantity leaves its remainder unprotected, so compare ACTIVE
        # coverage against the known holding (unknown coverage waits) rather
        # than returning on status alone. Same handler as the position-present
        # branch, so neither consumer can skip these invariants.
        if _covers_held(br, sym, m, None, sells):
            return            # covered, unverifiable, or a resize is in flight
        # Re-derive once more: the handler's own reconciliation may have
        # accounted further fills, and only ledger exhaustion means there is
        # nothing left to protect.
        held_est = _effective_held(m, None)
        if held_est <= 0:
            if not sells and not m.get("resize_cancel_ids"):
                _book_exit(sym, m, et_now)
            return
        # Cover the exact unsold remainder, never the cumulative entry qty.
        if minutes_now < FLAT_ET:
            protect(br, sym, m, held_est)
        return

    if resting is None:
        # Deferred refresh replacement: never submit until the broker confirms
        # the predecessor terminal (absence from the snapshot or an
        # unavailable lookup is not confirmation) and no fill is being
        # established — otherwise two resting buys could coexist.
        if m.get("bid_replacing"):
            old_id = m.get("order_id")
            if not _entry_terminal_no_position(br, old_id, m):
                if old_id:
                    br.cancel(str(old_id))
                jlog("refresh_wait_unresolved", symbol=sym, oid=str(old_id))
                return
            m["bid_replacing"] = False
            m["order_id"] = None
            anchor = m.get("anchor_ts")
            if anchor and (et_now - anchor).total_seconds() / 60 > WIN_MIN:
                jlog("expire", symbol=sym, note="replacement_anchor")
                return
            B = m.get("refresh_B") or m.get("entry_B")
            if minutes_now < ENTRY_CUTOFF and B:
                o = br.submit_buy(sym, qty_for(B), B)
                if o is not None:
                    m["order_id"] = str(o.id)
                    m["entry_B"] = round(float(o.limit_price), 2)
                    jlog("refresh", symbol=sym, oid=m["order_id"], new=B)
                else:
                    m["order_id"] = None
                    m["entry_B"] = B
            return
        return

    # An in-flight remainder cancel owns this order until the broker confirms
    # terminal: never refresh/replace it (that would create a second buy).
    if m.get("entry_cancel_pending"):
        br.cancel(str(resting.id))
        return

    # -- resting bid management (no position) ------------------------------
    if minutes_now >= ENTRY_CUTOFF:
        jlog("cancel_cutoff", symbol=sym, oid=str(resting.id))
        br.cancel(str(resting.id))
        return

    # A replacement is already in flight: retry the cancel and never submit a
    # second buy until the prior order is terminal.
    if m.get("bid_replacing"):
        jlog("refresh_wait", symbol=sym, oid=str(resting.id))
        br.cancel(str(resting.id))
        return

    if nb == 0:
        # Empty bar fetch: the strict-refresh ordering is unchanged, but a
        # stored anchor's known 120-min window still expires the bid.
        anchor = m.get("anchor_ts")
        if anchor and (et_now - anchor).total_seconds() / 60 > WIN_MIN:
            jlog("expire", symbol=sym, oid=str(resting.id), note="empty_bars")
            br.cancel(str(resting.id))
            m["order_id"] = None
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
        # Replace only after the working order is terminal: cancel now, submit
        # the replacement on a later poll once the broker confirms it is gone.
        jlog("refresh_cancel", symbol=sym, oid=str(resting.id), old=cur,
             new=m["refresh_B"])
        br.cancel(str(resting.id))
        m["bid_replacing"] = True
        return


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
    keep the original anchor instead of resetting to restart time.

    Rehydration is EXECUTION-complete, not just lifecycle-complete: the
    protective OCO family the previous process left working is retained by
    broker id (and its actual fills accounted), and the entered-inventory
    baseline is seeded from broker NET plus those accounted sales. Without
    both, a restarted process holds an unaccounted position behind an
    uncached bracket: once the stop leg fills and leaves the open snapshot,
    nothing links the sold shares to the position, so a stale held snapshot
    re-protects or market-closes shares that are already gone. Entry
    timestamp / price / anchor come from the journal and are never reset."""
    meta["_day"] = day_dir().name
    orders = []
    fetch_err = None
    try:
        orders = br.open_orders()
    except Exception as e:
        # Do not abandon the restart: a failed order fetch must not leave held
        # positions unprotected. Late-arriving owned buys are still tracked
        # and reconciled by poll().
        fetch_err = str(e)[:200]
        jlog("startup_reconcile_error", where="open_orders", msg=fetch_err)
    canceled = 0
    for o in orders:
        if owned(o) and o.side.value == "buy":
            br.cancel(str(o.id))
            jlog("startup_cancel_entry", symbol=o.symbol, oid=str(o.id))
            canceled += 1
    try:
        positions = br.positions()
    except Exception as e:
        jlog("startup_reconcile_error", where="positions", msg=str(e)[:200])
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
        # Retain the OWN protective family already on the book (never a
        # foreign order) and account the shares it has sold, so the family is
        # reconciled by broker id instead of being re-submitted from a stale
        # held snapshot.
        sells = own_sells(sym, orders)
        _retain_protection(m, sells)
        # Baseline = broker NET + already-accounted family sales. Seeding from
        # NET alone would subtract those sales twice and UNDER-protect (NET 60
        # with 40 already sold must still cover 60, not 20). An existing
        # baseline is authoritative.
        _seed_entry_baseline(m, int(float(p.qty)))
        rehydrated += 1
    jlog("startup_reconcile", canceled_entries=canceled, rehydrated=rehydrated,
         day=meta["_day"], live=br.live, fetch_error=bool(fetch_err))


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
    """Book owned entry fills, full or partial, and drive entry remainder
    cancels. Protection submission is owned solely by manage_symbol (called
    right after in the same poll), so a fresh fill can never be protected
    twice from a stale order snapshot. Each symbol is isolated so one broker
    or submission failure cannot abort the others."""
    open_by_id = {str(o.id): o for o in orders}
    for sym, m in list(meta.items()):
        if not isinstance(m, dict):
            continue
        try:
            _sync_one_fill(br, sym, m, open_by_id, et_now)
        except Exception:
            jlog("error", where=f"sync:{sym}",
                 traceback=traceback.format_exc()[-500:])


def _sync_one_fill(br, sym, m, open_by_id, et_now):
    """Book one symbol's incremental entry fill and drive its remainder
    cancel. Cumulative accounting (entry_filled_qty) survives an exit, so an
    unchanged broker filled_qty is never replayed and only the new increment
    is ever added to the held estimate."""
    oid = m.get("order_id")
    if not oid:
        return
    o = open_by_id.get(oid)
    if o is None:
        o = br.order(oid)  # resolved while we were away
        if o is None:
            # Broker truth unavailable: keep the id (and any pending cancel)
            # so a later poll reconciles — never drop a non-terminal entry.
            if m.get("entry_cancel_pending"):
                jlog("entry_cancel_unconfirmed", symbol=sym, oid=oid)
            return
    st = str(o.status.value)
    fq = int(float(o.filled_qty or 0))
    booked = int(m.get("entry_filled_qty") or 0)
    new = fq - booked
    if new > 0:
        m["entry_filled_qty"] = fq
        if not m.get("entry_ts"):
            m["oco_id"] = None
            m["protect_pending"] = False
            m["protect_qty"] = 0
            m.pop("exit_due", None)
            m.pop("exit_submitted", None)
            m.pop("exit_order_id", None)
            m["entry_B"] = round(float(o.limit_price), 2)
            m["entry_c0"] = round(float(o.limit_price) / (1 - L), 2)
            m["entry_ts"] = getattr(o, "filled_at", None) or et_now
            jlog("fill", symbol=sym, oid=oid, price=str(o.filled_avg_price),
                 qty=str(fq), B=m["entry_B"], c0=m["entry_c0"],
                 pf_est=m.get("pf_est"), n_strict_est=m.get("n_strict_est"))
            if (not m.get("micro_done")
                    and getattr(o, "filled_at", None) is not None):
                micro = br.trades_at_bid(sym, m["entry_B"],
                                         o.filled_at - timedelta(minutes=2),
                                         o.filled_at + timedelta(minutes=2))
                m["micro_done"] = True
                if micro:
                    jlog("micro", symbol=sym, **micro)
    if st == "partially_filled":
        # A partial fill leaves the order OPEN. Cancel the remainder but
        # retain the id until the broker reports terminal: the cancel may
        # fail or sit pending, and the remainder can still print (even
        # after the quantity we already booked is exited).
        jlog("partial", symbol=sym, oid=oid, filled=fq)
        m["entry_cancel_pending"] = True
        br.cancel(oid)
    elif st == "filled" or st in TERMINAL:
        m["order_id"] = None
        m["entry_cancel_pending"] = False
    elif m.get("entry_cancel_pending"):
        # pending_cancel / accepted / new while a cancel is in flight.
        jlog("entry_cancel_pending", symbol=sym, oid=oid, status=st,
             filled=fq)
        br.cancel(oid)


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
        try:
            scan, scan_src = scan_candidates()
        except Exception as e:
            # A scanner outage disables NEW entries for this poll only. It must
            # never stop broker-truth management (fills, protection, expiry,
            # tl30, EOD) of symbols we already hold or have resting.
            jlog("scan_error", msg=str(e)[:200])
            scan, scan_src = pd.DataFrame(
                columns=["rank", "symbol", "close", "change"]), "none"
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

    # Every owned resting entry buy is managed even when its symbol is neither
    # a candidate nor a position: a startup cancel may have failed, or a buy
    # can outlive its candidate row. Foreign (non-owned) orders are never
    # adopted or cancelled. Entries with no anchor predate this process;
    # cancel them (never invent an anchor, never adopt a foreign buy) so the
    # next strict-state minute re-places cleanly.
    buy_syms = set()
    for o in orders:
        if owned(o) and o.side.value == "buy":
            mo = meta.get(o.symbol)
            if isinstance(mo, dict) and mo.get("anchor_ts"):
                buy_syms.add(o.symbol)  # ours this process: manage normally
            else:
                # Leftover entry with no anchor (e.g. a failed startup
                # cancel): cancel it — never invent an anchor, never adopt a
                # foreign buy. Retried each poll until it leaves the book.
                jlog("cancel_unknown_entry", symbol=o.symbol, oid=str(o.id))
                br.cancel(str(o.id))

    tracked = {k for k, v in meta.items() if isinstance(v, dict)} | set(cands["symbol"]) | set(positions) | buy_syms
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
        # Never open a second entry while this symbol already tracks an
        # in-flight entry/cancel/replacement (the orders snapshot is stale
        # within a poll, e.g. after a deferred refresh replacement).
        if (m.get("order_id") or m.get("entry_ts") or m.get("bid_replacing")
                or m.get("entry_cancel_pending")):
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
        m["bid_replacing"] = False
        m["entry_cancel_pending"] = False
        m["protect_qty"] = 0
        m["entry_filled_qty"] = 0
        m["entry_open_qty"] = 0
        m["sold_total"] = 0
        m["sold_marks"] = {}
        m.pop("resize_cancel_ids", None)
        m.pop("exit_due", None)
        m.pop("exit_submitted", None)
        m.pop("exit_order_id", None)
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
    n_err = 0
    n_poll = 0
    while True:
        try:
            if KILL.exists():
                jlog("kill_file", note="stopping", canceled=kill_cleanup(br))
                break
            poll(br, meta, probe=a.probe)
            n_err = 0
            if ALERT.exists():
                ALERT.unlink()
        except Exception:
            n_err += 1
            jlog("error", traceback=traceback.format_exc()[-800:])
            if n_err >= 3:
                ALERT.write_text(traceback.format_exc()[-800:])
                jlog("alert", consecutive_errors=n_err)
        n_poll += 1
        if n_poll % 5 == 0:
            jlog("alive", polls=n_poll)
        if a.once:
            break
        time.sleep(a.seconds)


if __name__ == "__main__":
    main()
