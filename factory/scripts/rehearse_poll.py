"""Offline rehearsal of the live decision path on a real session.

Runs the ACTUAL `flush_bot.poll()` in dry-run at the first strict-state minute of
a journal day, using that day's own scan rows + Alpaca IEX bars. This exercises
the full path end-to-end - tracked loop, note_strict (creates _strict_seen),
sync_fills, manage loop, entry cutoff, new-bid placement - on real data, before
the next live session. Read-only: the broker is dry-run, day_dir is redirected,
no orders are placed.

Usage: uv run --python 3.11 --with pandas --with alpaca-py --with python-dotenv \
         python factory/scripts/rehearse_poll.py --day 2026-09-14
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pandas as pd  # noqa: E402

import flush_bot as fb  # noqa: E402
from replay_decisions import iex_bars, load_scans  # noqa: E402

WORK = Path("/tmp/opencode/rehearse_journal")


class DryBroker:
    """Read-only stand-in: dry-run Broker fields, forced clock/bars, no orders."""

    def __init__(self, bars_map: dict, now):
        self.live = False
        self._bars = bars_map
        self._now = now

    def clock(self):
        return True, self._now

    def bars(self, sym, start):
        return self._bars.get(sym, pd.DataFrame())

    def open_orders(self):
        return []

    def positions(self):
        return {}

    def order(self, oid):
        return None

    def submit_buy(self, *a, **k):
        return None

    def sell_oco(self, *a, **k):
        return None

    def cancel(self, oid):
        pass

    def close_market(self, sym):
        pass


def find_strict_minute(scans: dict, df: pd.DataFrame):
    for sym in sorted(scans):
        b = df[df["symbol"] == sym]
        cand = [s for s in scans[sym] if s[2] and s[2] >= fb.CAND_MIN]
        if b.empty or not cand:
            continue
        prev = cand[0][3] / (1 + cand[0][2] / 100.0)
        for m in range(571, fb.ENTRY_CUTOFF):
            comp = b[b["et"] <= m - 1]
            if len(comp) < 16:
                continue
            sm = fb.state_minutes(comp, prev)
            if len(sm) and int(sm["et"].iloc[-1]) == int(comp["et"].iloc[-1]):
                near = min(scans[sym], key=lambda s: abs(s[0] - m))
                if near[1] and int(near[1]) <= 3:
                    return sym, b[b["et"] <= m - 1], m, prev
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=str(fb.now_et().date()))
    a = ap.parse_args()
    scans = load_scans(a.day)
    if not scans:
        sys.exit(f"no scan rows for {a.day}")
    df = iex_bars(sorted(scans), a.day)
    found = find_strict_minute(scans, df)
    if not found:
        sys.exit(f"{a.day}: no strict minute with rank<=3 - nothing to rehearse")
    sym, bars, m, prev = found
    now = fb.now_et().replace(hour=m // 60, minute=m % 60, second=0, microsecond=0)
    day_dir = WORK / a.day
    day_dir.mkdir(parents=True, exist_ok=True)
    fb.day_dir = lambda: day_dir
    fb.now_et = lambda: now
    close = float(bars["close"].iloc[-1])
    fb.scan_candidates = lambda: (pd.DataFrame(
        {"symbol": [sym], "close": [close], "change": [(close / prev - 1) * 100], "rank": [1]}), "rehearsal")
    logs: list = []
    fb.jlog = lambda e, **kw: logs.append({"event": e, **kw})
    meta = {"_day": a.day}
    try:
        fb.poll(DryBroker({sym: bars}, now), meta, probe=False)
    except Exception as e:  # noqa: BLE001 - rehearsal must surface anything
        print(f"REHEARSAL FAIL: poll raised {type(e).__name__}: {e}")
        sys.exit(1)
    errs = [l for l in logs if l["event"] == "error"]
    bids = [{k: v for k, v in l.items() if k in ("symbol", "B", "c0", "qty", "rank", "pf_est", "oid")}
            for l in logs if l["event"] == "place_bid"]
    ok = not errs and meta.get("_strict_seen") == {sym} and bids
    print(f"{a.day}: first strict minute {m // 60:02d}:{m % 60:02d} on {sym} (prev {prev:.2f}, close {close:.2f})")
    print(f"  _strict_seen: {meta.get('_strict_seen')}")
    print(f"  place_bid:    {bids}")
    print(f"  errors:       {errs}")
    print("REHEARSAL", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
