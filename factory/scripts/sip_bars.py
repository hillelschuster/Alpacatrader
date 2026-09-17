#!/usr/bin/env python3
"""SIP trade -> 1-minute bar builder (BASKET-01 data upgrade, derived layer).

Rebuilds the exact minute bars the existing BASKET machinery needs, directly from the
canonical raw SIP trades (data/sip/trades/YYYY-MM-DD.parquet). The update rules follow
Alpaca's documented bar-aggregation table verbatim (docs: Market Data FAQ, "How are bars
aggregated?"), including tape-conditional rows and strictest-rule-wins for multi-condition
trades. Minute semantics: trade timestamp truncated to the minute (NY), bar stamped at the
left edge; session tags pre/rth/post are explicit.

This is a derived layer: raw trades are never modified. Auction/official prints (Q, M, O,
6, 5, ...) are excluded from bar prices per the same table and are ALSO written to a
separate auction-print artifact so nothing is silently lost.

Policies:
  alpaca  (default) - Alpaca's documented table above
  all               - every trade updates everything (naive; diagnostic bracket)

Outputs (local, gitignored):
  data/sip/derived/bars_1m_<day>_<policy>.parquet   (symbol, et_min, sess, o,h,l,c,v,n,vw, first_ts,last_ts)
  data/sip/derived/auction_prints_<day>.parquet     (raw trades carrying official/auction codes)

Usage:
  .venv/bin/python factory/scripts/sip_bars.py --self-test
  .venv/bin/python factory/scripts/sip_bars.py --day 2021-02-01 --policy alpaca
  .venv/bin/python factory/scripts/sip_bars.py --day 2021-02-01 --compare-alpaca
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
SIP = ROOT / "data" / "sip"
OUT = SIP / "derived"

# update rules: 2 = updates, 1 = first-trade-only for open/close, 0 = does not update.
# code -> (open_close, high_low, volume). Minute-bar rows of Alpaca's table.
G, Y, R = 2, 1, 0
RULES_M = {
    " ": (G, G, G), "@": (G, G, G), "A": (G, G, G), "C": (R, R, G),
    "D": (G, G, G), "E": (G, G, G), "F": (G, G, G), "G": (R, R, G),
    "H": (R, R, G), "I": (R, R, G), "K": (G, G, G), "L": (G, G, G),
    "M": (R, R, R), "N": (R, R, G), "O": (G, G, G), "P": (R, R, G),
    "Q": (R, R, R), "R": (R, R, G), "T": (G, G, G), "U": (R, R, G),
    "V": (R, R, G), "W": (R, R, G), "X": (G, G, G), "Y": (G, G, G),
    "Z": (R, R, G), "4": (R, R, G), "5": (G, G, G), "6": (G, G, G),
    "7": (R, R, G), "9": (R, R, R),
}
AUCTION_CODES = {"Q", "M", "O", "5", "6"}  # official open/close, MC open/reopen/close


def _rule_for(code: str, tape: str):
    if code == "B":  # tape-dependent: Average Price (AB) vs Bunched (C)
        return (R, R, G) if tape in ("A", "B") else (G, G, G)
    return RULES_M.get(code)


def combine(conds, tape):
    """Strictest rule across a trade's condition codes + unknown-code reporting."""
    oc, hl, v = G, G, G
    unknown = []
    for c in conds:
        r = _rule_for(c, tape)
        if r is None:
            unknown.append(c)
            r = (R, R, G)  # conservative default; counted in diagnostics
        oc, hl, v = min(oc, r[0]), min(hl, r[1]), min(v, r[2])
    return oc, hl, v, unknown


def build_bars(trades: pl.DataFrame, policy: str = "alpaca"):
    """trades: raw SIP trades frame (symbol, ts_utc, price, size, conditions, tape, ...)."""
    if policy not in ("alpaca", "all"):
        raise ValueError(f"unknown policy {policy}")
    if policy == "all":
        t = trades.with_columns(
            pl.lit(G, dtype=pl.Int8).alias("oc"),
            pl.lit(G, dtype=pl.Int8).alias("hl"),
            pl.lit(G, dtype=pl.Int8).alias("v"))
    else:
        t = trades.with_columns(pl.col("conditions").list.join("|").alias("_ck"))
        key = t.select(["_ck", "tape"]).unique()
        rows, unknown_all = [], set()
        for ck, tp in key.iter_rows():
            conds = ck.split("|") if ck else []
            oc, hl, v, unk = combine(conds, tp)
            unknown_all.update(unk)
            rows.append({"_ck": ck, "tape": tp, "oc": oc, "hl": hl, "v": v})
        rule_df = pl.DataFrame(rows)
        t = t.join(rule_df, on=["_ck", "tape"], how="left")
        if unknown_all:
            print(f"[warn] unknown condition codes treated conservatively: {sorted(unknown_all)}")

    t = t.sort(["symbol", "ts_utc"])
    t = t.with_columns(
        pl.col("ts_utc").dt.convert_time_zone("America/New_York").alias("ts_et"),
    )
    t = t.with_columns(
        (pl.col("ts_et").dt.hour().cast(pl.Int32) * 60 + pl.col("ts_et").dt.minute().cast(pl.Int32)).alias("et_min"),
        pl.col("ts_et").dt.truncate("1m").dt.replace_time_zone("UTC").alias("ts_min_utc"),
    )
    t = t.with_columns(
        pl.when(pl.col("et_min") < 570).then(pl.lit("pre"))
        .when(pl.col("et_min") < 960).then(pl.lit("rth")).otherwise(pl.lit("post")).alias("sess"),
    )
    g = t.group_by(["symbol", "et_min", "ts_min_utc", "sess"], maintain_order=True).agg(
        pl.col("price").filter(pl.col("oc") >= Y).first().alias("o"),
        pl.col("price").filter(pl.col("hl") == G).max().alias("h"),
        pl.col("price").filter(pl.col("hl") == G).min().alias("l"),
        pl.col("price").filter(pl.col("oc") == G).last().alias("c_g"),
        pl.col("price").filter(pl.col("oc") == Y).first().alias("c_y"),
        pl.col("size").filter(pl.col("v") == G).sum().alias("v"),
        pl.col("size").filter(pl.col("v") == G).len().alias("n"),
        pl.col("ts_utc").first().alias("first_ts"),
        pl.col("ts_utc").last().alias("last_ts"),
        ((pl.col("price") * pl.col("size")).filter((pl.col("hl") == G) & (pl.col("v") == G)).sum()).alias("_pv"),
        pl.col("size").filter((pl.col("hl") == G) & (pl.col("v") == G)).sum().alias("_vv"),
    )
    g = g.with_columns(
        pl.coalesce([pl.col("c_g"), pl.col("c_y")]).alias("c"),
        pl.when(pl.col("_vv") > 0).then(pl.col("_pv") / pl.col("_vv")).otherwise(None).alias("vw"),
    ).drop(["c_g", "c_y"])
    bars = g.filter((pl.col("o").is_not_null()) & (pl.col("h").is_not_null())
                    & (pl.col("l").is_not_null()) & (pl.col("c").is_not_null()))
    return bars.sort(["symbol", "et_min"])


def auction_prints(trades: pl.DataFrame) -> pl.DataFrame:
    cond = None
    for code in AUCTION_CODES:
        c = pl.col("conditions").list.contains(code)
        cond = c if cond is None else (cond | c)
    return trades.filter(cond)


def compare_alpaca_bars(day: str, ours: pl.DataFrame, symbols: list):
    """Fetch provider 1Min SIP bars for the day and compare cell-by-cell."""
    import os
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed
    from datetime import datetime, timezone

    c = StockHistoricalDataClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"])
    start = datetime.fromisoformat(day + "T00:00:00+00:00")
    end = datetime.fromisoformat(day + "T23:59:59+00:00")
    req = StockBarsRequest(symbol_or_symbols=symbols, timeframe=TimeFrame.Minute,
                           start=start, end=end, feed=DataFeed.SIP)
    bs = c.get_stock_bars(req)
    rows = []
    for sym, blist in bs.data.items():
        for b in blist:
            rows.append({"symbol": sym, "ts_min_utc": b.timestamp,
                         "open": b.open, "high": b.high, "low": b.low,
                         "close": b.close, "volume": b.volume})
    prov = pl.DataFrame(rows)
    prov = prov.with_columns(
        pl.col("ts_min_utc").dt.convert_time_zone("America/New_York").alias("ts_et"))
    prov = prov.with_columns(
        (pl.col("ts_et").dt.hour().cast(pl.Int32) * 60 + pl.col("ts_et").dt.minute().cast(pl.Int32)).alias("et_min"))
    j = ours.select(["symbol", "et_min", "o", "h", "l", "c", "v"]).join(
        prov.select(["symbol", "et_min", "open", "high", "low", "close", "volume"]),
        on=["symbol", "et_min"], how="full", suffix="_p")
    prov_only_df = j.filter(pl.col("o").is_null() & pl.col("open").is_not_null())
    prov_in = prov_only_df.filter((pl.col("et_min") >= 565) & (pl.col("et_min") <= 965))
    both = j.filter(pl.col("o").is_not_null() & pl.col("open").is_not_null())
    ours_only = j.filter(pl.col("open").is_null() & pl.col("o").is_not_null()).height
    diff = both.filter(~((pl.col("o") == pl.col("open")) & (pl.col("h") == pl.col("high"))
                         & (pl.col("l") == pl.col("low")) & (pl.col("c") == pl.col("close"))
                         & (pl.col("v") == pl.col("volume"))))
    examples = diff.head(10).to_dicts()
    return {
        "bars_ours": ours.height, "bars_provider": prov.height, "bars_matched": both.height,
        "ours_only": ours_only,
        "provider_only_out_of_window": prov_only_df.height - prov_in.height,
        "provider_only_in_window": prov_in.height,
        "exact_cell_match": int(both.height - diff.height),
        "match_rate": round(float((both.height - diff.height) / both.height), 5) if both.height else None,
        "mismatch_examples": examples,
    }


# ---------------------------------------------------------------- self-test


def _t(sym, ts, px, sz, conds, tape="C"):
    return {"symbol": sym, "ts_utc": ts, "price": px, "size": sz, "conditions": conds, "tape": tape}


def selftest():
    from datetime import datetime, timezone
    mk = lambda h, m, s, us=0: datetime(2021, 2, 1, h, m, s, us, tzinfo=timezone.utc)
    rows = [
        # 14:30:00.000 = 09:30 ET exactly (EST) -> rth
        _t("AAA", mk(14, 30, 0), 10.0, 100, ["@"]),
        _t("AAA", mk(14, 30, 30), 10.5, 200, ["@"]),
        _t("AAA", mk(14, 30, 58), 9.9, 10, ["@", "Z"]),            # out of seq: volume only (minute)
        _t("AAA", mk(14, 30, 59, 999000), 10.2, 50, ["@", "I"]),   # odd lot: volume only
        _t("AAA", mk(14, 29, 59), 9.5, 7, ["@"]),                  # pre session
        _t("AAA", mk(14, 32, 0), 11.0, 25, ["Q"]),                 # official open: no updates
        _t("AAA", mk(14, 32, 1), 10.4, 40, ["B"], tape="C"),       # bunched (C): all update
        _t("BBB", mk(14, 33, 0), 5.0, 60, ["@", "4"]),             # derivatively priced: no px, yes vol
        _t("BBB", mk(14, 33, 10), 5.05, 10, ["@"]),                # regular: sets o/h/l/c
        _t("BBB", mk(14, 33, 30), 5.1, 30, ["B"], tape="A"),       # avg price (AB): volume only
        _t("BBB", mk(14, 33, 45), 5.2, 20, ["Q"]),                 # official code: no update
        _t("CCC", mk(14, 34, 0), 7.0, 5, ["I"]),                   # only odd lot -> bar must vanish
    ]
    df = pl.DataFrame(rows)
    bars = build_bars(df, "alpaca")
    b = {(r["symbol"], r["et_min"]): r for r in bars.iter_rows(named=True)}
    a630 = b[("AAA", 570)]
    assert a630["o"] == 10.0 and a630["h"] == 10.5, a630       # I/Z/Q excluded from px
    assert a630["l"] == 10.0 and a630["c"] == 10.5, a630
    assert a630["v"] == 100 + 200 + 10 + 50, a630              # all volume-green (I,Z update volume)
    assert a630["n"] == 4, a630
    assert ("AAA", 571) not in b
    a632 = b[("AAA", 572)]
    assert a632["o"] == 10.4 and a632["c"] == 10.4 and a632["v"] == 40, a632  # Q dropped, B(C) full
    b633 = b[("BBB", 573)]
    assert b633["o"] == 5.05 and b633["c"] == 5.05 and b633["h"] == 5.05, b633
    assert b633["v"] == 60 + 10 + 30, b633                     # 4 and B(A): volume only
    assert not any(r["symbol"] == "CCC" for r in bars.iter_rows(named=True))
    s = set(bars["sess"].to_list())
    assert s == {"rth", "pre"}, s
    prev = b[("AAA", 569)]
    assert prev["sess"] == "pre" and prev["v"] == 7, prev
    auc = auction_prints(df)
    assert set(auc["symbol"].to_list()) == {"AAA", "BBB"}, auc
    assert auc.height == 2, auc.height  # Q at 14:32 and Q at 14:33:45
    print("self-test OK")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--day")
    ap.add_argument("--policy", default="alpaca", choices=["alpaca", "all"])
    ap.add_argument("--compare-alpaca", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        selftest()
        return
    if not args.day:
        raise SystemExit("--day required")
    tp = SIP / "trades" / f"{args.day}.parquet"
    if not tp.exists():
        raise SystemExit(f"missing raw trades: {tp}")
    trades = pl.read_parquet(tp)
    syms = trades["symbol"].unique().sort().to_list()
    bars = build_bars(trades, args.policy)
    OUT.mkdir(parents=True, exist_ok=True)
    bp = OUT / f"bars_1m_{args.day}_{args.policy}.parquet"
    bars.write_parquet(bp)
    auc = auction_prints(trades)
    apth = OUT / f"auction_prints_{args.day}.parquet"
    auc.write_parquet(apth)
    print(f"{args.day}: {trades.height} trades -> {bars.height} bars ({len(syms)} syms), "
          f"auction prints {auc.height} -> {bp.name}")
    if args.compare_alpaca:
        res = compare_alpaca_bars(args.day, bars, syms)
        print("compare-alpaca:", json.dumps(res))
    return bars


if __name__ == "__main__":
    main()
