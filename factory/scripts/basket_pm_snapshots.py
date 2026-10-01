#!/usr/bin/env python3
"""BASKET-01 / HARVEST01 — broad causal PM snapshot acquisition (Layer-1b).

For every development day: fetch the full point-in-time universe's Alpaca SIP minute
bars over the premarket window [04:00, 09:30) ET (feed=sip, Adjustment.RAW) and compact
each symbol to ONE snapshot row that carries, for each frozen PM decision clock C:

    px_C  = close of the last completed bar (et <= C-1)   (causal decision price)
    et_C  = that bar's et                                  (staleness visible)
    fo_C  = open of the first bar with et >= C             (first executable price)
    fet_C = that bar's et                                  (fill delay visible)

plus pm_first/pm_last, pm_n_bars, pm_vol, pm_hi, pm_lo. pm_last_et/pm_last_px share the
legacy compact's column names so the two lanes are directly reconcilable.

Nothing here filters by price, gain, volume or freshness: eligibility is the same PIT
listed-common set as sip_universe.pit_elig; ranking and floors are derived later.

Outputs (atomic, resumable; nothing written on partial failure):
    <data>/sip/pm_snapshots/<day>.parquet
    <data>/sip/pm_snapshots/<day>.manifest.json
    <data>/sip/pm_snapshots/index.jsonl            (--index; rebuilt from manifests)
    <data>/sip/pm_snapshots/dev_days.json          (--dev-days provenance)

Usage:
    .venv/bin/python factory/scripts/basket_pm_snapshots.py --self-test
    .venv/bin/python factory/scripts/basket_pm_snapshots.py --day 2021-02-01
    .venv/bin/python factory/scripts/basket_pm_snapshots.py --days 2021-02-01 2022-05-09
    .venv/bin/python factory/scripts/basket_pm_snapshots.py --dev-days --workers 3
    .venv/bin/python factory/scripts/basket_pm_snapshots.py --reconcile 2021-02-01
    .venv/bin/python factory/scripts/basket_pm_snapshots.py --index
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sip_universe as su  # noqa: E402  (frozen producer; reused for window/et helpers only)

PM_CLOCKS = [510, 540, 550, 555, 560, 565, 569]
BATCH = 500
OUT_SUB = ("sip", "pm_snapshots")
CALENDAR = ROOT / "factory" / "artifacts" / "basket" / "sip" / "phase2_session_calendar.json"
PRODUCER_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
VERBOSE = False

_CLIENT = None
_PIT: dict = {}


def pit_elig(day: str, data_root: Path) -> frozenset:
    """Latest PIT vintage <= day from <data_root>/pit/pit_symbols.parquet (same semantics
    as sip_universe.pit_elig, but resolved against the real data root)."""
    if "df" not in _PIT:
        pit = pl.read_parquet(data_root / "pit" / "pit_symbols.parquet",
                              columns=["vintage", "symbol"])
        _PIT["df"] = pit
        _PIT["vintages"] = sorted(pit["vintage"].unique().to_list())
        _PIT["memo_v"], _PIT["memo_set"] = None, frozenset()
    i = bisect.bisect_right(_PIT["vintages"], day) - 1
    if i < 0:
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


def resolve_data_root(explicit: str | None = None) -> Path:
    cands = []
    if explicit:
        cands.append(Path(explicit))
    if os.environ.get("HARVEST_DATA_ROOT"):
        cands.append(Path(os.environ["HARVEST_DATA_ROOT"]))
    cands.append(Path("/home/hillel/projects/Alpacatrader/data"))
    cands.append(ROOT / "data")
    for c in cands:
        if (c / "sip").exists():
            return c
    raise SystemExit(f"no data root among {[str(c) for c in cands]}")


def dev_days() -> list[str]:
    cal = json.loads(CALENDAR.read_text())
    return sorted(cal["evidence"].keys())


def out_dir(data_root: Path) -> Path:
    d = data_root.joinpath(*OUT_SUB)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------ compaction


def compact_snapshot(items: list[tuple]) -> dict | None:
    """items: sorted-unordered list of (et, open, high, low, close, volume)."""
    pm = sorted(((int(e), float(o), float(h), float(lo), float(c), float(v or 0.0))
                 for (e, o, h, lo, c, v) in items if int(e) <= 569), key=lambda x: x[0])
    if not pm:
        return None
    ets = [p[0] for p in pm]
    row = {
        "symbol": None,
        "pm_n_bars": len(pm),
        "pm_vol": float(sum(p[5] for p in pm)),
        "pm_hi": float(max(p[2] for p in pm)),
        "pm_lo": float(min(p[3] for p in pm)),
        "pm_first_et": int(ets[0]),
        "pm_first_px": float(pm[0][1]),
        "pm_last_et": int(ets[-1]),
        "pm_last_px": float(pm[-1][4]),
    }
    for C in PM_CLOCKS:
        j = bisect.bisect_right(ets, C - 1) - 1
        row[f"px_{C}"] = float(pm[j][4]) if j >= 0 else None
        row[f"et_{C}"] = int(ets[j]) if j >= 0 else None
        k = bisect.bisect_left(ets, C)
        row[f"fo_{C}"] = float(pm[k][1]) if k < len(pm) else None
        row[f"fet_{C}"] = int(ets[k]) if k < len(pm) else None
    return row


def row_schema() -> dict:
    d = {
        "symbol": pl.Utf8, "pm_n_bars": pl.Int32, "pm_vol": pl.Float64,
        "pm_hi": pl.Float64, "pm_lo": pl.Float64,
        "pm_first_et": pl.Int32, "pm_first_px": pl.Float64,
        "pm_last_et": pl.Int32, "pm_last_px": pl.Float64,
    }
    for C in PM_CLOCKS:
        d.update({f"px_{C}": pl.Float64, f"et_{C}": pl.Int32,
                  f"fo_{C}": pl.Float64, f"fet_{C}": pl.Int32})
    return d


# ------------------------------------------------------------------ fetching


def _client():
    global _CLIENT
    if _CLIENT is None:
        from dotenv import load_dotenv
        load_dotenv(Path("/home/hillel/projects/Alpacatrader/.env"))
        load_dotenv(ROOT / ".env")
        from alpaca.data.historical import StockHistoricalDataClient
        _CLIENT = StockHistoricalDataClient(os.environ["ALPACA_API_KEY"],
                                            os.environ["ALPACA_SECRET_KEY"])
    return _CLIENT


def fetch_day_rows(day: str, data_root: Path, max_batches: int = 0):
    """Fetch + compact one day. Returns (rows, errors, invalid, skipped_syntax, bars_raw)."""
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed, Adjustment

    syms = sorted(pit_elig(day, data_root))
    if not syms:
        return [], [{"error": "no PIT universe"}], [], [], 0
    start, end = su.window_utc(day, premarket=True)
    client = _client()
    skipped_syntax = [s for s in syms if ("^" in s or "/" in s)]
    syms = [s for s in syms if not ("^" in s or "/" in s)]
    rows, errors, invalid, raw_n = [], [], [], 0
    batches = su.chunks(syms, BATCH)
    if max_batches:
        batches = batches[:max_batches]
    for bi, batch in enumerate(batches, 1):
        todo = list(batch)
        attempt = 0
        while todo:
            try:
                req = StockBarsRequest(symbol_or_symbols=todo, timeframe=TimeFrame.Minute,
                                       start=start, end=end, feed=DataFeed.SIP,
                                       adjustment=Adjustment.RAW)
                res = client.get_stock_bars(req)
                for sym in todo:
                    bars = res.data.get(sym, [])
                    raw_n += len(bars)
                    items = [(su.et_min(b.timestamp), b.open, b.high, b.low, b.close, b.volume)
                             for b in bars]
                    r = compact_snapshot(items)
                    if r is not None:
                        r["symbol"] = sym
                        rows.append(r)
                if VERBOSE:
                    print(f"  [{day}] batch {bi}/{len(batches)} done rows={len(rows)}", flush=True)
                break
            except Exception as e:  # API/network errors
                m = re.search(r"invalid symbol:\s*([^\"\\]+)", str(e))
                if m:
                    bad = m.group(1).strip()
                    invalid.append(bad)
                    before = len(todo)
                    todo = [s for s in todo if s.strip() != bad]
                    if len(todo) == before or len(invalid) > 250:
                        errors.append({"batch0": batch[0] if batch else None, "n": before,
                                       "error": f"unresolvable invalid symbol {bad!r}"[:200]})
                        break
                    continue
                if attempt >= 2:
                    errors.append({"batch0": todo[0], "n": len(todo), "error": str(e)[:200]})
                    break
                attempt += 1
                time.sleep(5 * attempt)
    return rows, errors, invalid, skipped_syntax, raw_n


def write_day(data_root: Path, day: str, rows, meta: dict) -> dict:
    d = out_dir(data_root)
    fp, tmp = d / f"{day}.parquet", d / f"{day}.parquet.tmp"
    df = pl.DataFrame(rows, schema=row_schema()) if rows else pl.DataFrame(schema=row_schema())
    df.write_parquet(tmp)
    os.replace(tmp, fp)
    man = dict(meta, day=day, kind="pm_snapshots", file=f"{day}.parquet", rows=int(df.height),
               sha256=su.sha256_file(fp), clocks=list(PM_CLOCKS),
               schema={c: str(row_schema()[c]) for c in df.columns})
    mp, tmpm = d / f"{day}.manifest.json", d / f"{day}.manifest.json.tmp"
    tmpm.write_text(json.dumps(man, indent=1, default=str))
    os.replace(tmpm, mp)
    return man


def ok_manifest(data_root: Path, day: str) -> bool:
    mp = out_dir(data_root) / f"{day}.manifest.json"
    if not mp.exists():
        return False
    try:
        return json.loads(mp.read_text()).get("status") == "ok"
    except Exception:
        return False


def process_day(day: str, data_root: Path, force: bool = False,
                max_batches: int = 0) -> str:
    if not force and ok_manifest(data_root, day):
        return f"{day}: skip"
    t0 = time.time()
    rows, errors, invalid, skipped_syntax, raw_n = fetch_day_rows(day, data_root,
                                                                  max_batches=max_batches)
    start, end = su.window_utc(day, premarket=True)
    meta = {
        "status": "ok" if not errors else "partial",
        "provider": "alpaca", "feed": "sip", "adjustment": "raw", "client": "alpaca-py",
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "window_utc": [x.isoformat() for x in (start, end)],
        "window_et": "04:00-09:30",
        "symbols_requested": len(pit_elig(day, data_root)),
        "symbols_with_data": len(rows),
        "bars_raw": int(raw_n),
        "invalid_symbols": len(set(invalid)) + len(skipped_syntax),
        "errors": errors[:20],
        "elapsed_s": round(time.time() - t0, 1),
        "producer_sha256": PRODUCER_SHA,
        "universe": "sip_universe.pit_elig (latest PIT vintage <= day)",
    }
    write_day(data_root, day, rows, meta)
    return (f"{day}: {meta['status']} rows={len(rows)} bars={raw_n} "
            f"err={len(errors)} el={meta['elapsed_s']}s")


# ------------------------------------------------------------------ modes


def self_test() -> int:
    bars = [(559, 1.00, 1.10, 0.90, 1.00, 10.0),
            (560, 1.01, 1.05, 0.95, 1.02, 5.0),
            (562, 1.03, 1.06, 1.00, 1.03, 7.0)]
    r = compact_snapshot(bars)
    assert r is not None
    assert r["pm_n_bars"] == 3 and abs(r["pm_vol"] - 22.0) < 1e-9
    assert r["pm_last_et"] == 562 and abs(r["pm_last_px"] - 1.03) < 1e-9
    assert r["px_560"] == 1.00 and r["et_560"] == 559
    assert r["fo_560"] == 1.01 and r["fet_560"] == 560
    assert r["px_565"] == 1.03 and r["et_565"] == 562     # last completed <= 564
    assert r["fo_565"] is None and r["fet_565"] is None
    assert r["px_510"] is None and r["et_510"] is None
    assert r["fo_510"] == 1.00 and r["fet_510"] == 559   # first bar at/after 08:30
    assert r["fo_569"] is None
    # bar exactly at C-1 is completed for C
    r2 = compact_snapshot([(568, 2.0, 2.1, 1.9, 2.0, 1.0)])
    assert r2["px_569"] == 2.0 and r2["et_569"] == 568
    assert r2["fo_569"] is None
    # out-of-window bars are ignored entirely
    assert compact_snapshot([(570, 1, 1, 1, 1, 1)]) is None
    # missing values tolerated
    r3 = compact_snapshot([(100, 1.0, 1.0, 1.0, 1.0, None)])
    assert r3 is not None and r3["pm_vol"] == 0.0
    print("self-test OK")
    return 0


def rebuild_index(data_root: Path) -> int:
    d = out_dir(data_root)
    entries = []
    for mp in sorted(d.glob("*.manifest.json")):
        try:
            man = json.loads(mp.read_text())
        except Exception:
            continue
        entries.append({"day": man.get("day"), "status": man.get("status"),
                        "rows": man.get("rows"), "bars_raw": man.get("bars_raw"),
                        "symbols_with_data": man.get("symbols_with_data"),
                        "elapsed_s": man.get("elapsed_s"), "sha256": man.get("sha256"),
                        "producer_sha256": man.get("producer_sha256")})
    entries.sort(key=lambda e: e["day"] or "")
    ip, tmp = d / "index.jsonl", d / "index.jsonl.tmp"
    tmp.write_text("\n".join(json.dumps(e, default=str) for e in entries) + "\n")
    os.replace(tmp, ip)
    print(f"index: {len(entries)} days -> {ip}")
    return 0


def reconcile(data_root: Path, days: list[str]) -> int:
    """Compare pm_last_{et,px} against the legacy premarket compact, same source."""
    tot = eq = ne = miss = 0
    for day in days:
        legacy_p = data_root / "sip" / "universe" / "premarket" / f"{day}.parquet"
        new_p = out_dir(data_root) / f"{day}.parquet"
        if not legacy_p.exists() or not new_p.exists():
            print(f"{day}: missing legacy={legacy_p.exists()} new={new_p.exists()}")
            continue
        lg = pl.read_parquet(legacy_p, columns=["symbol", "pm_last_et", "pm_last_px",
                                                "pm_n_bars", "pm_vol"])
        nw = pl.read_parquet(new_p, columns=["symbol", "pm_last_et", "pm_last_px",
                                             "pm_n_bars", "pm_vol"])
        j = lg.join(nw, on="symbol", how="full", suffix="_new")
        bad = j.filter(
            (pl.col("pm_last_et").is_null()) | (pl.col("pm_last_et_new").is_null())
            | (pl.col("pm_last_et") != pl.col("pm_last_et_new"))
            | ((pl.col("pm_last_px") - pl.col("pm_last_px_new")).abs() > 1e-9)
            | (pl.col("pm_n_bars") != pl.col("pm_n_bars_new"))
            | ((pl.col("pm_vol") - pl.col("pm_vol_new")).abs() > 1e-6)
        )
        n_l = j.height
        n_bad = bad.height
        tot += n_l
        eq += n_l - n_bad
        ne += n_bad
        miss_l = lg.height - nw.height
        miss += abs(miss_l)
        print(f"{day}: legacy={lg.height} new={nw.height} compared={n_l} "
              f"equal={n_l - n_bad} mismatched={n_bad}")
        if n_bad:
            print(bad.head(5))
    print(f"TOTAL compared={tot} equal={eq} mismatched={ne} rowcount_delta={miss}")
    return 0 if ne == 0 and miss == 0 else 1


def run_days(days: list[str], data_root: Path, workers: int, force: bool) -> int:
    t0 = time.time()
    if workers <= 1:
        for day in days:
            print(process_day(day, data_root, force=force), flush=True)
    else:
        import multiprocessing as mp
        with mp.Pool(processes=workers) as pool:
            for line in pool.imap_unordered(_worker, [(d, str(data_root), force) for d in days]):
                print(line, flush=True)
    print(f"done {len(days)} days in {round(time.time() - t0, 1)}s")
    return 0


def _worker(args):
    global VERBOSE
    day, data_root, force = args
    return process_day(day, Path(data_root), force=force)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--day")
    ap.add_argument("--days", nargs="+", default=[])
    ap.add_argument("--dev-days", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--index", action="store_true")
    ap.add_argument("--reconcile", nargs="+", default=[])
    ap.add_argument("--data-root", default=None)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    global VERBOSE
    VERBOSE = args.verbose

    if args.self_test:
        return self_test()
    data_root = resolve_data_root(args.data_root)
    if args.index:
        return rebuild_index(data_root)
    if args.reconcile:
        return reconcile(data_root, args.reconcile)
    days = list(args.days)
    if args.day:
        days.append(args.day)
    if args.dev_days:
        days = dev_days()
        (out_dir(data_root) / "dev_days.json").write_text(json.dumps(days))
    if args.limit:
        days = days[:args.limit]
    if not days:
        ap.print_help()
        return 2
    return run_days(days, data_root, args.workers, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
