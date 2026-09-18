#!/usr/bin/env python3
"""BASKET-01 SIP Layer 1 — full PIT-universe SIP minute-bar discovery.

Canonical selection substrate for the SIP regeneration (owner-approved 2026-09-18):
reconstruct the frozen ranking snapshots from SIP itself, independent of the legacy
tape.  For every development day this fetches Alpaca SIP 1-minute bars across the
complete point-in-time eligible universe and stores ONE COMPACT ROW PER SYMBOL:

    o570 + delayed-open flag, px at every frozen decision T (close of the last bar
    with et <= T-1, with the et actually used), day OHLC/volume/bar counts.

Also supported: `--premarket` fetches the A_pm window (04:00-09:29:59 ET) and stores
the last premarket close + its minute (A_pm definition unchanged: freshness 15 min).

Rules (owner amendments): feed=sip, raw/unadjusted (Adjustment.RAW), PIT identity from
data/pit/pit_symbols.parquet (latest vintage <= day), deterministic pagination via the
alpaca-py client, per-day atomic artifacts + per-day manifests (canonical provenance);
a merged index is rebuilt from manifests with --index (never from concurrent appends).

Nothing here filters by price/gain: eligibility is recorded, ranking is derived later.

Usage:
  .venv/bin/python factory/scripts/sip_universe.py --self-test
  .venv/bin/python factory/scripts/sip_universe.py --days 2021-02-01
  .venv/bin/python factory/scripts/sip_universe.py --months 2021-02
  .venv/bin/python factory/scripts/sip_universe.py --all-dev --workers 5
  .venv/bin/python factory/scripts/sip_universe.py --premarket --months 2025-02
  .venv/bin/python factory/scripts/sip_universe.py --index
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, date
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
PITP = ROOT / "data" / "pit" / "pit_symbols.parquet"
OUT = ROOT / "data" / "sip" / "universe"
PREM = ROOT / "data" / "sip" / "premarket_bars"
ET = ZoneInfo("America/New_York")
T_LIST = [575, 580, 585, 590, 595, 600, 615, 630, 645, 660, 690, 720]
RTH_LO, RTH_HI = 570, 960          # [09:30, 16:00) ET
BATCH = 500
SEED_DAYS = ["2021-01-29", "2025-01-31"]   # prev-session seeds for stretch starts

_PIT: dict = {}
VERBOSE = False
MAX_BATCHES = 0


def pit_elig(day: str) -> frozenset:
    if "df" not in _PIT:
        pit = pl.read_parquet(PITP, columns=["vintage", "symbol"])
        _PIT["df"] = pit
        _PIT["vintages"] = sorted(pit["vintage"].unique().to_list())
        _PIT["memo_v"], _PIT["memo_set"] = None, frozenset()
    i = bisect.bisect_right(_PIT["vintages"], day) - 1
    if i < 0:
        # seed days before the PIT archive starts: use the earliest snapshot as that session's universe
        if not _PIT["vintages"]:
            return frozenset()
        i = 0
    v = _PIT["vintages"][i]
    if _PIT["memo_v"] != v:
        _PIT["memo_set"] = frozenset(
            s.strip() for s in _PIT["df"].filter(pl.col("vintage") == v)["symbol"].to_list()
            if s and s.strip())
        _PIT["memo_v"] = v
    return _PIT["memo_set"]


def window_utc(day: str, premarket: bool = False):
    d = date.fromisoformat(day)
    if premarket:
        start = datetime(d.year, d.month, d.day, 4, 0, tzinfo=ET)
        end = datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET)
    else:
        start = datetime(d.year, d.month, d.day, 9, 25, tzinfo=ET)
        end = datetime(d.year, d.month, d.day, 16, 5, tzinfo=ET)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def et_min(ts) -> int:
    t = ts.astimezone(ET)
    return t.hour * 60 + t.minute


def chunks(seq, n):
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def compact_rth(bars, premarket: bool = False) -> dict | None:
    """One compact row from a symbol's bars (list of alpaca Bar models), or None."""
    if not bars:
        return None
    b = sorted(((et_min(x.timestamp), x) for x in bars), key=lambda p: p[0])
    if premarket:
        pm = [(e, x) for e, x in b if e <= 569]
        if not pm:
            return None
        e_last, last = pm[-1]
        return {"symbol": last.symbol, "pm_last_et": int(e_last),
                "pm_last_px": float(last.close), "pm_n_bars": int(len(pm)),
                "pm_vol": float(sum(x.volume for _, x in pm if x.volume is not None))}
    rth = [(e, x) for e, x in b if RTH_LO <= e < RTH_HI]
    if not rth:
        return None
    row = {"symbol": rth[0][1].symbol,
           "first_et": int(rth[0][0]), "last_et": int(rth[-1][0]),
           "o570": None, "delayed_open": False,
           "hi": max(float(x.high) for _, x in rth),
           "lo": min(float(x.low) for _, x in rth),
           "c_last": float(rth[-1][1].close),
           "vol": float(sum(x.volume for _, x in rth if x.volume is not None)),
           "n_bars": int(len(rth))}
    for e, x in rth:
        if e == 570:
            row["o570"] = float(x.open)
            break
    row["delayed_open"] = row["o570"] is None and row["first_et"] > 570
    ets = [e for e, _ in rth]
    for T in T_LIST:
        j = bisect.bisect_right(ets, T - 1) - 1
        row[f"px_{T}"] = float(rth[j][1].close) if j >= 0 else None
        row[f"px_{T}_et"] = int(ets[j]) if j >= 0 else None
    return row


def fetch_bars(client, day: str, symbols, premarket: bool):
    import re
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed, Adjustment
    start, end = window_utc(day, premarket)
    # Alpaca's data API rejects preferred/class spellings with '^' or '/' and fails the WHOLE
    # batch on one bad symbol -> filter those two syntactic forms up front (recorded), and for
    # anything else the API still rejects, remove just that symbol and re-issue the batch.
    skipped_syntax = [s for s in symbols if ("^" in s or "/" in s)]
    symbols = [s for s in symbols if not ("^" in s or "/" in s)]
    rows, errors, invalid, got, raw_n = [], [], [], 0, 0
    batches = chunks(symbols, BATCH)
    if MAX_BATCHES:
        batches = batches[:MAX_BATCHES]
    for bi, batch in enumerate(batches, 1):
        todo = list(batch)
        attempt = 0
        if VERBOSE:
            print(f"  [batch {bi}/{len(batches)}] first={todo[0]} n={len(todo)} rows_so_far={len(rows)}", flush=True)
        while todo:
            try:
                req = StockBarsRequest(symbol_or_symbols=todo, timeframe=TimeFrame.Minute,
                                       start=start, end=end, feed=DataFeed.SIP,
                                       adjustment=Adjustment.RAW)
                t_b = time.time()
                res = client.get_stock_bars(req)
                for sym in todo:
                    r = compact_rth(res.data.get(sym, []), premarket)
                    if r is not None:
                        rows.append(r)
                        got += 1
                    raw_n += len(res.data.get(sym, []))
                if VERBOSE:
                    print(f"  [batch {bi}] done n={len(todo)} in {time.time()-t_b:.1f}s "
                          f"rows={len(rows)}", flush=True)
                break
            except Exception as e:  # API/network errors
                m = re.search(r"invalid symbol:\s*([^\"\\]+)", str(e))
                if m:  # drop the single bad symbol and re-issue the batch
                    bad = m.group(1).strip()
                    invalid.append(bad)
                    before = len(todo)
                    todo = [s for s in todo if s.strip() != bad]
                    if len(todo) == before or len(invalid) > 250:
                        # cannot remove (or absurd count): drop the batch rather than spin forever
                        errors.append({"batch0": batch[0] if batch else None, "n": before,
                                       "error": f"unresolvable invalid symbol {bad!r}"[:200]})
                        break
                    continue
                if attempt >= 2:
                    errors.append({"batch0": todo[0], "n": len(todo), "error": str(e)[:200]})
                    break
                attempt += 1
                time.sleep(5 * attempt)
    return rows, errors, invalid, skipped_syntax, got, raw_n


def sha256_file(p: Path):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def should_skip(out_root: Path, day: str, premarket: bool, force: bool) -> bool:
    if force:
        return False
    sub = "premarket" if premarket else "rth"
    mp = out_root / sub / f"{day}.manifest.json"
    fp = out_root / sub / f"{day}.parquet"
    if not (mp.exists() and fp.exists()):
        return False
    try:
        return json.loads(mp.read_text()).get("status") == "ok"
    except Exception:
        return False


def write_artifact(out_root: Path, day: str, premarket: bool, df: pl.DataFrame, meta: dict):
    sub = "premarket" if premarket else "rth"
    d = out_root / sub
    d.mkdir(parents=True, exist_ok=True)
    fp, tmp = d / f"{day}.parquet", d / f"{day}.parquet.tmp"
    df.write_parquet(tmp)
    os.replace(tmp, fp)
    man = dict(meta, day=day, kind=sub, file=f"{sub}/{day}.parquet", rows=int(df.height),
               sha256=sha256_file(fp), schema={c: str(df.schema[c]) for c in df.columns})
    mp, tmpm = d / f"{day}.manifest.json", d / f"{day}.manifest.json.tmp"
    tmpm.write_text(json.dumps(man, indent=1, default=str))
    os.replace(tmpm, mp)
    return man


def fetch_day(day: str, out_root: Path, premarket: bool, force: bool):
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from alpaca.data.historical import StockHistoricalDataClient

    if should_skip(out_root, day, premarket, force):
        return f"{day}: skip"
    syms = sorted(pit_elig(day))
    if not syms:
        return f"{day}: no PIT universe"
    client = StockHistoricalDataClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"])
    t0 = time.time()
    rows, errors, invalid, skipped_syntax, got, raw_n = fetch_bars(client, day, syms, premarket)
    df = pl.DataFrame(rows) if rows else pl.DataFrame(schema={"symbol": pl.Utf8})
    meta = {"status": "ok" if not errors else "partial",
            "provider": "alpaca", "feed": "sip", "client": "alpaca-py",
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "window_utc": [x.isoformat() for x in window_utc(day, premarket)],
            "window_et": "04:00-09:30" if premarket else "09:25-16:05",
            "symbols_requested": len(syms), "symbols_with_data": got,
            "symbols_invalid": sorted(set(invalid)), "symbols_skipped_syntax": skipped_syntax,
            "bars_raw": raw_n, "batch": BATCH, "errors": errors,
            "elapsed_s": round(time.time() - t0, 1)}
    man = write_artifact(out_root, day, premarket, df, meta)
    return (f"{day}: {man['status']} rows={man['rows']} syms={got}/{len(syms)} "
            f"(invalid={len(set(invalid)) + len(skipped_syntax)}) bars={raw_n} {man['elapsed_s']}s")


def build_index(out_root: Path):
    for sub in ("rth", "premarket"):
        d = out_root / sub
        if not d.exists():
            continue
        lines = []
        for mp in sorted(d.glob("*.manifest.json")):
            m = json.loads(mp.read_text())
            lines.append({"day": m["day"], "status": m["status"], "rows": m["rows"],
                          "symbols_with_data": m["symbols_with_data"], "sha256": m["sha256"],
                          "elapsed_s": m["elapsed_s"], "bars_raw": m.get("bars_raw")})
        outp = out_root / f"index_{sub}.jsonl"
        with open(outp, "w") as fh:
            for ln in lines:
                fh.write(json.dumps(ln, separators=(",", ":"), default=str) + "\n")
        print(f"{outp.name}: {len(lines)} days "
              f"(ok={sum(1 for x in lines if x['status']=='ok')}, "
              f"partial={sum(1 for x in lines if x['status']=='partial')})")


def run_days(days, out_root: Path, premarket: bool, force: bool):
    for day in days:
        try:
            print(fetch_day(day, out_root, premarket, force), flush=True)
        except Exception as e:
            print(f"{day}: FAILED {str(e)[:200]}", flush=True)


def run_workers(days, out_root: Path, premarket: bool, force: bool, workers: int):
    parts = [[] for _ in range(workers)]
    for i, d in enumerate(sorted(days)):
        parts[i % workers].append(d)
    procs = []
    for i, part in enumerate(parts):
        if not part:
            continue
        cmd = [sys.executable, "-u", str(Path(__file__)), "--out", str(out_root),
               "--days", *part] + (["--premarket"] if premarket else []) + (["--force"] if force else [])
        log = open(f"/tmp/opencode/universe_w{i}.log", "w")
        procs.append((subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT), log, i, len(part)))
    print(f"launched {len(procs)} workers over {len(days)} days")
    for p, log, i, n in procs:
        rc = p.wait()
        log.close()
        print(f"worker {i}: rc={rc} days={n} log=/tmp/opencode/universe_w{i}.log")


def selftest():
    from types import SimpleNamespace as NS
    from datetime import datetime as dt, timedelta
    mk = lambda e, o, h, l, c, v: NS(timestamp=dt(2021, 2, 1, 14, 30, tzinfo=timezone.utc) + timedelta(minutes=e - 570),
                                     symbol="X", open=o, high=h, low=l, close=c, volume=v)
    bars = [mk(570, 10.0, 10.5, 9.8, 10.2, 100), mk(574, 10.2, 10.6, 10.1, 10.4, 50),
            mk(575, 10.4, 10.9, 10.3, 10.8, 70), mk(599, 10.8, 11.0, 10.7, 10.9, 80),
            mk(600, 10.9, 11.5, 10.85, 11.4, 90)]
    row = compact_rth(bars)
    assert row is not None
    assert row["o570"] == 10.0 and not row["delayed_open"]
    assert row["px_575"] == 10.4 and row["px_575_et"] == 574     # last bar et<=574
    assert row["px_580"] == 10.8 and row["px_580_et"] == 575     # fallback to last available <=579
    assert row["px_600"] == 10.9 and row["px_600_et"] == 599     # exact completed bar
    assert row["px_615"] == 11.4 and row["px_615_et"] == 600
    assert row["hi"] == 11.5 and row["lo"] == 9.8 and row["c_last"] == 11.4 and row["n_bars"] == 5
    late = [mk(572, 5.0, 5.1, 4.9, 5.0, 10)]
    r2 = compact_rth(late)
    assert r2 is not None
    assert r2["o570"] is None and r2["delayed_open"] is True
    assert r2["px_575"] == 5.0 and r2["px_575_et"] == 572    # late-open name still has a px at 575
    pm = compact_rth([mk(560, 3.0, 3.1, 2.9, 3.0, 5), mk(569, 3.0, 3.2, 3.0, 3.1, 7)], premarket=True)
    assert pm is not None
    assert pm["pm_last_et"] == 569 and pm["pm_last_px"] == 3.1 and pm["pm_n_bars"] == 2
    assert compact_rth([], premarket=True) is None
    assert chunks([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    s, e = window_utc("2022-03-10")
    assert s.astimezone(ET).hour == 9 and s.astimezone(ET).minute == 25
    s2, e2 = window_utc("2025-06-02", premarket=True)
    assert (e2 - s2).total_seconds() == 5.5 * 3600 and s2.astimezone(ET).hour == 4
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        df = pl.DataFrame([row])
        assert not should_skip(td, "2021-02-01", False, False)
        write_artifact(td, "2021-02-01", False, df, {"status": "ok"})
        assert should_skip(td, "2021-02-01", False, False)
        assert not should_skip(td, "2021-02-01", False, True)
        write_artifact(td, "2021-02-01", True, df, {"status": "partial"})
        assert not should_skip(td, "2021-02-01", True, False)
    print("self-test OK")


def main(argv=None):
    global VERBOSE, MAX_BATCHES

    ap = argparse.ArgumentParser()
    ap.add_argument("--days", nargs="*", default=None)
    ap.add_argument("--months", nargs="*", default=None)
    ap.add_argument("--all-dev", action="store_true")
    ap.add_argument("--premarket", action="store_true")
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--index", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--max-batches", type=int, default=0)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    VERBOSE, MAX_BATCHES = args.verbose, args.max_batches
    if args.self_test:
        selftest()
        return
    out_root = Path(args.out)
    if args.index:
        build_index(out_root)
        return
    days = list(args.days or [])
    if args.all_dev or args.months:
        lb = sorted(Path(p).name[3:13] for p in (ROOT / "data" / "leaderboard").glob("lb_*.parquet"))
        lb = [d for d in lb if len(d) == 10]
        if args.all_dev:
            # frozen dev span only: 2021-02..2023-12 + 2025-02..2026-05
            # (2024 and 2025-01 are outside the frozen span; SIP could fill them later if wanted)
            months = {d[:7] for d in lb
                      if d[:7] <= "2026-05" and (d[:7] >= "2025-02" or d[:7] <= "2023-12")}
            days = [d for d in lb if d[:7] in months] + SEED_DAYS
        else:
            months = set(args.months)
            days = [d for d in lb if d[:7] in months]
            if "2021-02" in months:
                days.append("2021-01-29")
            if "2025-02" in months:
                days.append("2025-01-31")
    if not days:
        ap.error("provide --days/--months/--all-dev or --index")
    if args.workers and args.workers > 1 and len(days) > 1:
        run_workers(days, out_root, args.premarket, args.force, args.workers)
    else:
        run_days(days, out_root, args.premarket, args.force)


if __name__ == "__main__":
    main()
