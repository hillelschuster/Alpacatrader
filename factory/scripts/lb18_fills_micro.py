#!/usr/bin/env python3
"""Fill-realism microstructure evidence for frozen-rule fills (SIP trades).

For each fill: B = c[tf]/(1+fc) (recovered bid level). Fetch Alpaca SIP trades
for the flush-bar minute [tf-1, tf) ET and the following minute [tf, tf+1):
measure whether trades touched/traded through B, volume at/below B, duration,
and snapback. Optional NBBO slice around the first at-bid trade. Resumable:
existing rows in the output parquet are skipped. Free of any rule changes.

Artifacts: factory/artifacts/lb18_fills_micro.parquet + lb18_fills_micro.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lb18_episodes import build, load  # noqa: E402

from alpaca.data.enums import DataFeed  # noqa: E402
from alpaca.data.historical import StockHistoricalDataClient  # noqa: E402
from alpaca.data.requests import (  # noqa: E402
    StockQuotesRequest, StockTradesRequest)

ART = ROOT / "factory" / "artifacts"
OUT = ART / "lb18_fills_micro.parquet"
FILLS = {
    "oos": ART / "lb18_oos_oos.parquet",
    "dev": ART / "lb18_oos_dev.parquet",
}


def etwin(day, m0, m1):
    s = pd.Timestamp(f"{day} {m0 // 60:02d}:{m0 % 60:02d}",
                     tz="America/New_York").tz_convert("UTC").to_pydatetime()
    e = pd.Timestamp(f"{day} {m1 // 60:02d}:{m1 % 60:02d}",
                     tz="America/New_York").tz_convert("UTC").to_pydatetime()
    return s, e


def trade_metrics(df, B):
    if df is None or len(df) == 0:
        return {"n": 0}
    px = df["price"].to_numpy(float)
    sz = df["size"].to_numpy(float)
    m = px <= B
    out = {"n": int(len(df)), "vol": float(sz.sum()),
           "min_px": float(px.min())}
    if not m.any():
        out.update({"touch": 0, "through": 0, "vol_at": 0.0, "span_s": 0.0})
        return out
    ts = pd.to_datetime(df.loc[m, "timestamp"], utc=True).sort_values()
    out.update({
        "touch": 1,
        "through": int((px[m] < B).any()),
        "vol_at": float(sz[m].sum()),
        "span_s": float((ts.iloc[-1] - ts.iloc[0]).total_seconds()),
        "first_at": ts.iloc[0].isoformat(),
    })
    return out


TRADE_CAP = 200_000
QUOTE_CAP = 20_000


def fetch(c, sym, s, e, kind):
    req = (StockTradesRequest(symbol_or_symbols=sym, start=s, end=e,
                              feed=DataFeed.SIP, limit=TRADE_CAP) if kind == "trades"
           else StockQuotesRequest(symbol_or_symbols=sym, start=s, end=e,
                                   feed=DataFeed.SIP, limit=QUOTE_CAP))
    for attempt in range(2):
        try:
            df = c.get_stock_trades(req).df if kind == "trades" else c.get_stock_quotes(req).df
            df = df.reset_index()
            trunc = int(len(df) >= (TRADE_CAP if kind == "trades" else QUOTE_CAP))
            return df, trunc
        except Exception as ex:
            if attempt == 0:
                time.sleep(2.0)
                continue
            print(f"  {kind} ERR {sym}: {type(ex).__name__} {str(ex)[:120]}", flush=True)
            return None, 0
    return None, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="oos", choices=["oos", "dev", "both"])
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    env = dotenv_values(ROOT / ".env")
    c = StockHistoricalDataClient(env.get("ALPACA_API_KEY"),
                                  env.get("ALPACA_SECRET_KEY"))

    paths, lb = load()
    A = build(paths)

    keys = [a.pool] if a.pool != "both" else ["oos", "dev"]
    fills = pd.concat([pd.read_parquet(FILLS[k]) for k in keys],
                      ignore_index=True)
    fills = fills.sort_values(["date", "ticker", "tf"]).reset_index(drop=True)

    done = set()
    if OUT.exists():
        prev = pd.read_parquet(OUT)
        done = set(zip(prev["date"], prev["ticker"], prev["tf"].astype(int)))
        print(f"resume: {len(done)} fills already done")

    rows = []
    n_req = 0
    for r in fills.itertuples():
        key = (r.date, r.ticker, int(r.tf))
        if key in done:
            continue
        d = A.get((r.date, r.ticker))
        if d is None:
            continue
        t = d["t"]
        pos = int(np.searchsorted(t, int(r.tf)))
        if pos >= len(t) or int(t[pos]) != int(r.tf):
            continue
        c_f = float(d["c"][pos])
        B = c_f / (1.0 + float(r.fc))
        day = str(r.date)[:10]
        tf = int(r.tf)
        s0, e0 = etwin(day, tf - 1, tf)
        s1, e1 = etwin(day, tf, tf + 1)

        df0, tr0 = fetch(c, r.ticker, s0, e0, "trades"); n_req += 1
        df1, tr1 = fetch(c, r.ticker, s1, e1, "trades"); n_req += 1
        m0 = trade_metrics(df0, B)
        m1 = trade_metrics(df1, B)

        q = {}
        touch_ts = m0.get("first_at") or m1.get("first_at")
        if touch_ts:
            tq = pd.Timestamp(touch_ts)
            qdf, qt = fetch(c, r.ticker, (tq - pd.Timedelta(seconds=3)).to_pydatetime(),
                            (tq + pd.Timedelta(seconds=3)).to_pydatetime(), "quotes")
            n_req += 1
            if qdf is not None and len(qdf):
                qs = qdf.sort_values("timestamp")
                q = {"qn": int(len(qs)),
                     "q_spread_last": float((qs["ask_price"] - qs["bid_price"]).iloc[-1]),
                     "q_bid_last": float(qs["bid_price"].iloc[-1]),
                     "q_ask_last": float(qs["ask_price"].iloc[-1]),
                     "q_bidsz_last": float(qs["bid_size"].iloc[-1]),
                     "q_trunc": int(qt)}

        rows.append({
            "date": day, "ticker": r.ticker, "tf": tf, "fc": float(r.fc),
            "ret": float(r.ret), "prior_flush": int(r.prior_flush),
            "rank": int(r.rank), "gain": float(r.gain), "B": B,
            "flush_n": m0.get("n", 0), "flush_vol": m0.get("vol", 0.0),
            "flush_touch": m0.get("touch", 0), "flush_through": m0.get("through", 0),
            "flush_vol_at": m0.get("vol_at", 0.0), "flush_span_s": m0.get("span_s", 0.0),
            "next_n": m1.get("n", 0), "next_vol": m1.get("vol", 0.0),
            "next_touch": m1.get("touch", 0), "next_through": m1.get("through", 0),
            "next_vol_at": m1.get("vol_at", 0.0), "next_min_px": m1.get("min_px", np.nan),
            "trunc": int(tr0) + int(tr1), **q,
        })
        if len(rows) % 25 == 0:
            print(f"{len(rows)} done (reqs {n_req})", flush=True)
            pd.concat([pd.read_parquet(OUT) if OUT.exists() else pd.DataFrame(),
                       pd.DataFrame(rows)], ignore_index=True).to_parquet(OUT)
            rows = []
        if a.limit and (len(done) + len(rows) >= a.limit):
            break

    if rows:
        prev = pd.read_parquet(OUT) if OUT.exists() else pd.DataFrame()
        pd.concat([prev, pd.DataFrame(rows)], ignore_index=True).to_parquet(OUT)
    if not OUT.exists():
        sys.exit("no rows produced")
    d = pd.read_parquet(OUT)
    print(f"\ntotal fills measured: {len(d)}")

    def rep(tag, s):
        if len(s) == 0:
            return {}
        o = {"n": int(len(s)),
             "touch_rate": round(float((s["flush_touch"] == 1).mean()), 3),
             "touch_or_next": round(float(((s["flush_touch"] == 1) |
                                           (s["next_touch"] == 1)).mean()), 3),
             "through_rate": round(float((s["flush_through"] == 1).mean()), 3),
             "vol_at_med": float(s["flush_vol_at"].median()),
             "vol_at_q25": float(s["flush_vol_at"].quantile(.25)),
             "vol_at_q75": float(s["flush_vol_at"].quantile(.75)),
             "span_med_s": float(s["flush_span_s"].median()),
             "trunc_rate": round(float((s["trunc"] > 0).mean()), 3)}
        print(tag, o)
        return o

    cls = d["fc"] >= 0
    out = {"n": int(len(d)),
           "all": rep("all", d),
           "clean": rep("clean", d[cls]),
           "gap": rep("gap", d[~cls]),
           "strong": rep("strong", d[d["fc"] >= .02])}
    for q in (.1, .25, .5):
        k = f"10%part" if q == .1 else (f"25%part" if q == .25 else "50%part")
        out[k] = round(float((d["flush_vol_at"] * q).median()), 0)
    qq = d.dropna(subset=["q_spread_last"])
    if len(qq):
        out["nbbo"] = {"n": int(len(qq)),
                       "spread_med": round(float(qq["q_spread_last"].median()), 4),
                       "spread_q75": round(float(qq["q_spread_last"].quantile(.75)), 4),
                       "bid_sz_med": float(qq["q_bidsz_last"].median())}
    (ART / "lb18_fills_micro.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"artifact -> {ART/'lb18_fills_micro.json'}")


if __name__ == "__main__":
    main()
