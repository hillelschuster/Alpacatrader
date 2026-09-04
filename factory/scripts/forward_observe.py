"""Forward observer: research-grade live scanner. OBSERVES ONLY — never trades.

Polls the live market causally during session, maintains a small high-attention
watchlist under several parallel selection rules, and logs formulation-agnostic
raw state (scans, promotions, per-poll state, bars, quotes, news) as JSONL under
data/forward/YYYY-MM-DD/. All scoring/comparison happens OFFLINE on these logs,
so no scanner definition is frozen here.

Deliberately imports NOTHING that can place orders (no TradingClient,
no paper_execution). Promotion rules are pure functions (tested in
tests/test_forward_observe.py).

Usage:
  python factory/scripts/forward_observe.py --once --symbols AAPL,MSFT   # dry run
  python factory/scripts/forward_observe.py --live                       # session loop
"""
from __future__ import annotations
import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Safety: this module must never gain order-placement imports.
_FORBIDDEN_IMPORTS = ("TradingClient", "paper_execution")
_this = Path(__file__).read_text()
assert not any(("import " + f in _this or "from " + f in _this)
               for f in _FORBIDDEN_IMPORTS), "observer must stay order-free"

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(".env")

POLL_SECS = 120
SESSION_START_MIN = 13 * 60 + 25  # 13:25 UTC pre-open snapshot
SESSION_END_MIN = 20 * 60 + 10    # 20:10 UTC post-close snapshot
PROMOTE_CAP = 8


# ── pure selection rules (offline-replayable) ──────────────────────────────
def _gain(c) -> float:
    g = c.get("percent_gain")
    return float(g) if g is not None else float("-inf")


def _dvol(c) -> float:
    v = c.get("dollar_volume")
    return float(v) if v not in (None, 0) else 0.0


def rule_top_gain(rows, k=4):
    return [r["symbol"] for r in sorted(rows, key=_gain, reverse=True)[:k]
            if _gain(r) > float("-inf")]


def rule_gain_x_vol(rows, k=4):
    def score(r):
        return _gain(r) * math.log1p(_dvol(r)) if _gain(r) > float("-inf") else float("-inf")
    return [r["symbol"] for r in sorted(rows, key=score, reverse=True)[:k]
            if score(r) > float("-inf")]


def rule_sep_shortlist(rows, k=5):
    """Top-k by gain; separation recorded per name for offline use."""
    top = sorted(rows, key=_gain, reverse=True)[:k]
    out = []
    for i, r in enumerate(top):
        nxt = _gain(top[i + 1]) if i + 1 < len(top) else 0.0
        g = _gain(r)
        if g <= float("-inf"):
            continue
        out.append((r["symbol"], {"diff_pp": g - nxt,
                                 "ratio": (g / nxt) if nxt > 0 else None}))
    return out


def select_watchlist(rows):
    """Union of rules; returns (promoted_symbols, debug)."""
    a = rule_top_gain(rows)
    b = rule_gain_x_vol(rows)
    c = [s for s, _ in rule_sep_shortlist(rows)]
    promoted, debug = [], {}
    for sym in a + b + c:
        if sym not in promoted:
            promoted.append(sym)
            debug[sym] = {"top_gain": sym in a, "gain_x_vol": sym in b, "sep_list": sym in c}
    return promoted[:PROMOTE_CAP], debug


# ── live I/O ───────────────────────────────────────────────────────────────
def _jlog(path: Path, obj: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(obj, default=str) + "\n")


def poll_once(day_dir: Path, promoted_state: dict, symbols_override=None) -> dict:
    from src.scanner.scanner import scan_dynamic_candidates, scan_manual_watchlist
    from src.market_data import build_market_snapshots

    now = datetime.now(timezone.utc).isoformat()
    # WIDE net (50): live rows for rank 31-50 let future rules replay names today's
    # watchlist missed. Full-market depth comes from next-day historical backfill.
    cands = (scan_manual_watchlist(symbols_override) if symbols_override
             else scan_dynamic_candidates(max_candidates=50))
    snaps = build_market_snapshots(cands)
    rows = []
    for c in cands:
        s = snaps.get(c.symbol)
        q = getattr(s, "quote", None) if s else None
        rows.append({
            "symbol": c.symbol, "price": c.price, "percent_gain": c.percent_gain,
            "current_volume": c.current_volume, "dollar_volume": c.dollar_volume,
            "day_high": c.day_high, "day_low": c.day_low, "previous_close": c.previous_close,
            "float_shares": c.float_shares, "source": c.source,
            "bid": getattr(q, "bid", None) if q else None,
            "ask": getattr(q, "ask", None) if q else None,
        })
    _jlog(day_dir / "scans.jsonl", {"ts": now, "n": len(rows), "rows": rows})

    promoted, debug = select_watchlist(rows)
    for sym in promoted:
        if sym not in promoted_state:
            promoted_state[sym] = {"first_seen": now, "rules": debug[sym]}
            _jlog(day_dir / "promotions.jsonl",
                  {"ts": now, "symbol": sym, "rules": debug[sym],
                   "snapshot": next((r for r in rows if r["symbol"] == sym), None)})
            deep_snapshot(day_dir, sym, now)
    # per-poll compact state for promoted names
    by_sym = {r["symbol"]: r for r in rows}
    gains = sorted([_gain(r) for r in rows if _gain(r) > float("-inf")], reverse=True)
    for sym in list(promoted_state):
        r = by_sym.get(sym)
        if r is None:
            continue
        g = _gain(r)
        rank = sum(1 for x in gains if x > g) + 1 if g > float("-inf") else None
        nxt = gains[rank] if rank is not None and rank < len(gains) else None
        _jlog(day_dir / "state.jsonl", {"ts": now, "symbol": sym, "gain": g, "rank": rank,
               "diff_pp": (g - nxt) if (g and nxt is not None) else None,
               "dollar_volume": r.get("dollar_volume"), "price": r.get("price"),
               "day_high": r.get("day_high"), "bid": r.get("bid"), "ask": r.get("ask")})
    return {"ts": now, "candidates": len(rows), "promoted": promoted}


def deep_snapshot(day_dir: Path, symbol: str, now: str):
    """One-off rich capture at promotion: SIP bars, ADV baseline, float, news."""
    from src.market_data import fetch_avg_daily_volume
    api_key, secret = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
    out = {"ts": now, "symbol": symbol}
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from alpaca.data.enums import DataFeed
        from alpaca.common.enums import Sort
        hc = StockHistoricalDataClient(api_key, secret)
        for feed in (DataFeed.SIP, DataFeed.IEX):
            try:
                bs = hc.get_stock_bars(StockBarsRequest(
                    symbol_or_symbols=[symbol], timeframe=TimeFrame.Minute,
                    sort=Sort.DESC, limit=390, feed=feed)).data.get(symbol, [])
                if bs:
                    out["bars"] = [{"t": str(b.timestamp), "o": b.open, "h": b.high,
                                    "l": b.low, "c": b.close, "v": b.volume} for b in bs]
                    out["bars_feed"] = feed.value
                    break
            except Exception as e:
                out.setdefault("bars_err", str(e)[:120])
    except Exception as e:
        out["bars_err"] = str(e)[:120]
    try:
        out["adv20"] = fetch_avg_daily_volume(symbol, api_key, secret)
    except Exception as e:
        out["adv20_err"] = str(e)[:120]
    try:
        from src.scanner.enrichment import enrich_float_shares
        out["float_shares_live"] = enrich_float_shares(symbol)
    except Exception as e:
        out["float_err"] = str(e)[:120]
    try:
        from alpaca.data.historical.news import NewsClient
        from alpaca.data.requests import NewsRequest
        nc = NewsClient(api_key, secret)
        news = nc.get_news(NewsRequest(symbols=[symbol], limit=10)).data.get("news", [])
        out["news"] = [{"t": str(n.created_at), "headline": n.headline[:200],
                        "source": n.source} for n in news]
    except Exception as e:
        out["news_err"] = str(e)[:120]
    _jlog(day_dir / "deep.jsonl", out)


def in_window(now_min: int) -> bool:
    return SESSION_START_MIN <= now_min <= SESSION_END_MIN


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--symbols", default=None, help="comma list for dry-run")
    args = ap.parse_args()

    base = Path("data/forward")
    if args.once:
        day_dir = base / datetime.now(timezone.utc).strftime("%Y-%m-%d") / "dryrun"
        res = poll_once(day_dir, {}, args.symbols.split(",") if args.symbols else None)
        print(json.dumps(res, default=str)[:500])
        return
    if not args.live:
        ap.error("pass --live or --once")
    promoted_state: dict = {}
    print("observer live loop (order-free). window 13:25-20:10 UTC.", flush=True)
    while True:
        now = datetime.now(timezone.utc)
        now_min = now.hour * 60 + now.minute
        if in_window(now_min):
            day_dir = base / now.strftime("%Y-%m-%d")
            try:
                res = poll_once(day_dir, promoted_state)
                print(f"{res['ts'][:19]} cands={res['candidates']} "
                      f"watch={len(promoted_state)}", flush=True)
            except Exception as e:
                print(f"poll error: {str(e)[:200]}", flush=True)
            time.sleep(POLL_SECS)
        else:
            time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())
