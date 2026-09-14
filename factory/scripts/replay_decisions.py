"""Daily dry run: what would flush_bot v2.1 have done on <day>?

Replays the live journal's scan history (rank/close/change per minute) together
with Alpaca IEX 1-min bars through the bot's own strict-state gate, then
simulates the frozen bid/fill/exit semantics: rolling B = 0.9 * close of the
latest strict minute (refresh at every strict minute, 120-min expiry, cancelled
at the 15:30 ET entry cutoff), fill on the first bar with low <= B, exit at
target c0 / stop 0.9B / tl30 (30 bars), 100 bps friction.

Read-only diagnostic: no orders, no writes outside stdout. Use it after a
session to answer "did the bot miss anything?" and before a session as a
rehearsal of the decision path.

Usage: uv run --python 3.11 --with pandas --with alpaca-py --with python-dotenv \
         python factory/scripts/replay_decisions.py --day 2026-09-14
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pandas as pd  # noqa: E402

import flush_bot as fb  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
from alpaca.data.enums import DataFeed  # noqa: E402
from alpaca.data.historical import StockHistoricalDataClient  # noqa: E402
from alpaca.data.requests import StockBarsRequest  # noqa: E402
from alpaca.data.timeframe import TimeFrame  # noqa: E402

FR = 0.01
WIN = fb.WIN_MIN
TL = fb.TL_BARS


def load_scans(day: str) -> dict:
    p = ROOT / "data" / "forward" / "bot" / day / "journal.jsonl"
    scans: dict = {}
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("event") != "scan_row":
            continue
        t = str(r["ts"])[11:16]
        scans.setdefault(r["symbol"], []).append(
            (int(t[:2]) * 60 + int(t[3:]), r.get("rank"), r.get("change"), r.get("close")))
    return scans


def iex_bars(symbols: list, day: str) -> pd.DataFrame:
    cli = StockHistoricalDataClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"])
    d0 = datetime.fromisoformat(day + "T09:30:00").replace(tzinfo=fb.ET)
    req = StockBarsRequest(symbol_or_symbols=symbols, timeframe=TimeFrame.Minute,
                           start=d0, end=d0 + timedelta(hours=6.5), feed=DataFeed.IEX)
    df = cli.get_stock_bars(req).df.reset_index()
    etd = df["timestamp"].dt.tz_convert("America/New_York")
    df["et"] = etd.dt.hour * 60 + etd.dt.minute
    return df.rename(columns={"timestamp": "ts"})


def replay_symbol(b: pd.DataFrame, scans: list) -> dict:
    cand = [s for s in scans if s[2] and s[2] >= fb.CAND_MIN]
    if not cand:
        return {"status": "never-candidate"}
    # the bot seeds prev_close from the first candidate row it sees
    seed = cand[0]
    prev = seed[3] / (1 + seed[2] / 100.0)
    schedules = []
    for m in range(571, fb.ENTRY_CUTOFF):
        comp = b[b["et"] <= m - 1]
        if len(comp) < 16:
            continue
        sm = fb.state_minutes(comp, prev)
        if len(sm) == 0 or int(sm["et"].iloc[-1]) != int(comp["et"].iloc[-1]):
            continue
        near = min(scans, key=lambda s: abs(s[0] - m))
        if near[1] and int(near[1]) <= 3:
            schedules.append((m, round(float(comp["close"].iloc[-1]) * 0.9, 2),
                              float(comp["close"].iloc[-1])))
    if not schedules:
        return {"status": "no-arm", "prev": prev}
    B = c0 = t_arm = None
    si = 0
    fill = None
    for j in range(len(b)):
        tj = int(b["et"].iloc[j])
        if tj >= fb.ENTRY_CUTOFF and B is not None:  # bot cancels resting bids at 15:30
            B = None
        while si < len(schedules) and schedules[si][0] < tj:
            B, c0, t_arm = schedules[si][1], schedules[si][2], schedules[si][0]
            si += 1
        if B is not None and t_arm is not None and tj - t_arm > WIN:
            B = None
        if B is not None and float(b["low"].iloc[j]) <= B:
            fill = (tj, B, c0)
            break
    out = {"status": "no-fill", "prev": prev, "arms": len(schedules),
           "arm_first": schedules[0][0], "arm_last": schedules[-1][0]}
    if fill is None:
        return out
    tf, B, c0 = fill
    stop = round(B * 0.9, 2)
    idx = int(b.index.get_loc(b[b["et"] == tf].index[0]))
    ret = None
    for k in range(idx, min(idx + TL + 1, len(b))):
        if float(b["low"].iloc[k]) <= stop:
            ret, reason = min(float(b["open"].iloc[k]), stop) / B - 1, "stop"
            break
        if float(b["high"].iloc[k]) >= c0:
            ret, reason = c0 / B - 1, "target"
            break
    if ret is None:
        k = min(idx + TL, len(b) - 1)
        ret, reason = float(b["close"].iloc[k]) / B - 1, "tl30"
    out.update({"status": "fill", "tf": tf, "B": B, "c0": c0, "exit": reason,
                "net": ret - FR})
    return out


def hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=str(fb.now_et().date()))
    a = ap.parse_args()
    scans = load_scans(a.day)
    if not scans:
        sys.exit(f"no scan rows in journal for {a.day}")
    df = iex_bars(sorted(scans), a.day)
    filled = []
    for sym in sorted(scans):
        b = df[df["symbol"] == sym].copy()
        if b.empty:
            print(f"{sym:6s}: no IEX bars")
            continue
        r = replay_symbol(b, scans[sym])
        if r["status"] == "fill":
            filled.append((sym, r))
            print(f"{sym:6s}: FILL {hm(r['tf'])} @B {r['B']} (c0 {r['c0']}) -> {r['exit']} net {r['net']*100:+.2f}%")
        elif r["status"] == "no-fill":
            print(f"{sym:6s}: armed {r['arms']}x {hm(r['arm_first'])}-{hm(r['arm_last'])} (prev {r['prev']:.2f}) -> no fill before cutoff")
        elif r["status"] == "no-arm":
            print(f"{sym:6s}: candidate but never strict (prev {r['prev']:.2f})")
        else:
            print(f"{sym:6s}: {r['status']}")
    print(f"\n{a.day}: {len(filled)} hypothetical fill(s) under live semantics (15:30 cancel, IEX bars)")


if __name__ == "__main__":
    main()
