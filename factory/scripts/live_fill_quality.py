"""Live fill quality: was each live fill a -10% discount catch, or a crash buy?

For every fill in the live bot journals, compare our resting bid (B) against the
consolidated SIP tape:
  * when did the tape first trade at/below B, and how long before our fill
    (a long gap => our B was stale and the order sat above the market);
  * how far below B we filled (price improvement = the market had already
    collapsed through our order);
  * what the tape did in the 30 minutes after the fill: stop (0.9B) first,
    target (c0) first, or neither -- the frozen expectation is ~48% target.

Read-only diagnostic (no orders). Usage:
  uv run --python 3.11 --with pandas --with pyarrow --with alpaca-py \
    --with python-dotenv python factory/scripts/live_fill_quality.py
Artifacts: factory/artifacts/live_fill_quality.json + .parquet
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
load_env = ROOT / ".env"
from dotenv import load_dotenv  # noqa: E402

load_dotenv(load_env)
import pandas as pd  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from alpaca.data.enums import DataFeed  # noqa: E402
from alpaca.data.historical import StockHistoricalDataClient  # noqa: E402
from alpaca.data.requests import StockTradesRequest  # noqa: E402

ET = ZoneInfo("America/New_York")
TRADE_CAP = 200_000
PRE_MIN = 15
POST_MIN = 30


def journal_fills() -> list[dict]:
    out = []
    for d in sorted((ROOT / "data" / "forward" / "bot").glob("*/journal.jsonl")):
        for line in d.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("event") == "fill" and r.get("B") and r.get("c0"):
                out.append({"day": d.parent.name, "ts": r["ts"], "symbol": r["symbol"],
                            "fill": float(r["price"]), "B": float(r["B"]),
                            "c0": float(r["c0"]), "qty": int(float(r["qty"])),
                            "pf_est": r.get("pf_est")})
    return out


def trades(client, sym: str, t0: datetime, t1: datetime) -> pd.DataFrame:
    req = StockTradesRequest(symbol_or_symbols=sym, start=t0, end=t1,
                             feed=DataFeed.SIP, limit=TRADE_CAP)
    df = client.get_stock_trades(req).df.reset_index()
    df["ts"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(ET)
    return df[["ts", "price", "size"]].sort_values("ts")


def analyse(df: pd.DataFrame, f: dict) -> dict:
    ts = datetime.fromisoformat(f["ts"]).astimezone(ET)
    B, c0, stop = f["B"], f["c0"], round(f["B"] * 0.9, 2)
    pre = df[(df["ts"] >= ts - timedelta(minutes=PRE_MIN)) & (df["ts"] <= ts)]
    post = df[(df["ts"] > ts) & (df["ts"] <= ts + timedelta(minutes=POST_MIN))]
    r = {"day": f["day"], "symbol": f["symbol"], "ts": ts.isoformat(), "fill": f["fill"],
         "B": B, "c0": c0, "stop": stop, "qty": f["qty"], "pf_est": f["pf_est"]}
    r["fill_below_B_bps"] = round((f["fill"] / B - 1) * 10000, 1)
    if len(pre):
        hit = pre[pre["price"] <= B]
        r["pre_min"] = float(pre["price"].min())
        if len(hit):
            first = hit["ts"].iloc[0]
            r["pre_first_le_B"] = first.isoformat()
            r["pre_le_B_secs_before_fill"] = int((ts - first).total_seconds())
        else:
            r["pre_first_le_B"] = None
            r["pre_le_B_secs_before_fill"] = None
    else:
        r["pre_min"] = None
        r["pre_first_le_B"] = None
        r["pre_le_B_secs_before_fill"] = None
    if len(post):
        r["post_min"] = float(post["price"].min())
        r["post_max"] = float(post["price"].max())
        s = post[post["price"] <= stop]
        t = post[post["price"] >= c0]
        r["t_stop_s"] = int((s["ts"].iloc[0] - ts).total_seconds()) if len(s) else None
        r["t_target_s"] = int((t["ts"].iloc[0] - ts).total_seconds()) if len(t) else None
        if r["t_stop_s"] is not None and (r["t_target_s"] is None or r["t_stop_s"] < r["t_target_s"]):
            r["resolved"] = "stop"
        elif r["t_target_s"] is not None:
            r["resolved"] = "target"
        else:
            r["resolved"] = "neither"
        r["max_favourable_bps"] = round((r["post_max"] / f["fill"] - 1) * 10000, 1)
    else:
        r["post_min"] = r["post_max"] = None
        r["t_stop_s"] = r["t_target_s"] = None
        r["resolved"] = "no-trades-after"
        r["max_favourable_bps"] = None
    return r


def main() -> None:
    fills = journal_fills()
    if not fills:
        sys.exit("no live fills found")
    client = StockHistoricalDataClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"])
    rows = []
    for f in fills:
        ts = datetime.fromisoformat(f["ts"]).astimezone(ET)
        try:
            df = trades(client, f["symbol"], ts - timedelta(minutes=PRE_MIN),
                        ts + timedelta(minutes=POST_MIN + 2))
            rows.append(analyse(df, f))
        except Exception as e:  # noqa: BLE001
            print(f"  {f['symbol']} {f['ts']}: fetch failed {str(e)[:80]}")
    out = pd.DataFrame(rows)
    art = ROOT / "factory" / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    out.to_parquet(art / "live_fill_quality.parquet", index=False)
    summ = {
        "n_fills": int(len(out)),
        "mean_fill_below_B_bps": round(out["fill_below_B_bps"].mean(), 1) if len(out) else None,
        "share_fill_below_B": round((out["fill_below_B_bps"] < 0).mean(), 3) if len(out) else None,
        "share_tape_le_B_before_fill": round(out["pre_le_B_secs_before_fill"].notna().mean(), 3) if len(out) else None,
        "median_secs_tape_le_B_before_fill": float(out["pre_le_B_secs_before_fill"].median()) if len(out) else None,
        "resolved": out["resolved"].value_counts().to_dict() if len(out) else {},
        "median_t_stop_s": float(out["t_stop_s"].median()) if out["t_stop_s"].notna().any() else None,
        "median_t_target_s": float(out["t_target_s"].median()) if out["t_target_s"].notna().any() else None,
        "median_max_favourable_bps": float(out["max_favourable_bps"].median()) if len(out) else None,
    }
    (art / "live_fill_quality.json").write_text(json.dumps(
        {"summary": summ, "fills": rows}, indent=1, default=str))
    print(json.dumps(summ, indent=1))
    print(out[["day", "symbol", "fill", "B", "fill_below_B_bps", "pre_le_B_secs_before_fill",
               "resolved", "t_stop_s", "t_target_s", "max_favourable_bps"]].to_string(index=False))
    print("artifacts ->", art / "live_fill_quality.json", "/ .parquet")


if __name__ == "__main__":
    main()
