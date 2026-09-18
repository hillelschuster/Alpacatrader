#!/usr/bin/env python3
"""BASKET-01 SIP canonical ingestion — raw Alpaca SIP trades + quotes, day by day.

Principle (user directive): raw market record first; explicit interpretation second.
This layer stores provider events as intactly as reasonably possible:
  * no trade-condition filtering, no dedup, no cleaning of any kind;
  * conditions/tape/exchange/id preserved;
  * deterministic window per day (09:25-16:05 ET), resumable, atomic writes;
  * per-artifact manifest with counts/checksum/schema/window/source, plus a global
    append-only index (data/sip/manifest.jsonl).

Derived layers (bar reconstruction, condition policy) must sit ON TOP of this data.

Pilot scope: symbols = union of the day's anatomy candidate names (top-10 per
snapshot + session winners) for trades; top-3-per-snapshot union for quotes.
Full-universe ranking replication is a post-pilot decision (see PANEL notes).

Usage:
  .venv/bin/python factory/scripts/sip_ingest.py --self-test
  .venv/bin/python factory/scripts/sip_ingest.py --panel                 # pilot panel
  .venv/bin/python factory/scripts/sip_ingest.py --days 2022-03-10 2025-09-09
  .venv/bin/python factory/scripts/sip_ingest.py --panel --kinds trades --req-sleep 0.5
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone, date
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
ANAT = ROOT / "factory" / "artifacts" / "basket" / "anatomy"
CAND = ROOT / "data" / "sip" / "candidates"
OUT = ROOT / "data" / "sip"
NET_OUT = ROOT / "data" / "sip" / "net"
ET = ZoneInfo("America/New_York")
SESSION_START_ET = (9, 25)
SESSION_END_ET = (16, 5)

# Pilot certification panel (selected from committed anatomy artifacts; era-spread):
PANEL = {
    "2022-03-10": "known bad-print day (BRP corrupted HF prints)",
    "2025-09-09": "extreme +1585% (QMMM)",
    "2021-02-09": "extreme (era 2021)",
    "2022-02-17": "extreme (era 2022)",
    "2025-03-24": "extreme (era 2025)",
    "2025-10-30": "largest B/600 day-max (+538%, BQ)",
    "2026-04-27": "extreme (era 2026)",
    "2021-02-02": "multi-survivor (3 members >= +30%)",
    "2023-12-28": "multi-survivor (3 members >= +30%)",
    "2026-05-15": "multi-survivor (3 members >= +30%)",
    "2021-10-25": "ordinary day (day-max < 2%)",
    "2023-08-30": "ordinary day (day-max < 2%)",
    "2025-11-25": "ordinary day (day-max < 2%)",
    "2021-02-01": "halt-heavy day",
    "2023-03-16": "halt day",
    "2026-05-29": "halt day + top-3 churn 0",
    "2021-03-08": "minute-bar ambiguity day",
    "2023-12-15": "minute-bar ambiguity day",
    "2026-04-29": "minute-bar ambiguity day",
    "2023-03-17": "top-3 churn 0 (high opening churn)",
}

TRADE_COLS = ["symbol", "ts_utc", "price", "size", "exchange", "conditions", "trade_id", "tape"]
QUOTE_COLS = ["symbol", "ts_utc", "bid_price", "bid_size", "ask_price", "ask_size",
              "bid_exchange", "ask_exchange", "conditions", "tape"]


def window_utc(day: str):
    d = date.fromisoformat(day)
    start = datetime(d.year, d.month, d.day, *SESSION_START_ET, tzinfo=ET).astimezone(timezone.utc)
    end = datetime(d.year, d.month, d.day, *SESSION_END_ET, tzinfo=ET).astimezone(timezone.utc)
    return start, end


def symbols_for_day(day: str, include_adj: bool = True):
    """Trades set: union of stored candidate names + session winners for the day."""
    p = ANAT / f"{day}.jsonl"
    if not p.exists():
        raise FileNotFoundError(f"no anatomy day file for {day}")
    rec = json.loads(p.read_text())
    names = set()
    for s in rec["snapshots"]:
        for n in s["names"]:
            names.add(n["ticker"])
    for w in rec.get("winners_open", []) + rec.get("winners_prev", []):
        names.add(w["ticker"])
    quotes = set()
    for s in rec["snapshots"]:
        for n in s["names"][:3]:
            quotes.add(n["ticker"])
    return sorted(names), sorted(quotes)


def symbols_for_day_net(day: str):
    """Regeneration mode: trades = SIP-discovered candidate net; quotes = top-3 per
    SIP snapshot. The legacy union is NOT consulted (owner amendment 2026-09-18)."""
    p = CAND / f"{day}.json"
    if not p.exists():
        raise FileNotFoundError(f"no candidates file for {day}")
    rec = json.loads(p.read_text())
    trades = sorted(set(rec["net"]))
    quotes = set()
    for s in rec["snapshots"]:
        for n in s.get("top", [])[:3]:
            quotes.add(n["symbol"])
    return trades, sorted(quotes)


def list_net_days():
    return sorted(p.name[:10] for p in CAND.glob("*.json") if len(p.name) == 15)


PART_SYMS = 1            # flush after each symbol (bounded memory: one symbol's events per worker)
SPACE_FLOOR_GB = 50      # stop before C: free space drops below this (user floor ~50)
ZSTD_LEVEL = 3


def _flush_part(acc, cols, parts_dir: Path, idx: int) -> int:
    if not acc.get("ts_utc"):
        return 0
    df = frame(acc, cols)
    df.write_parquet(parts_dir / f"part_{idx:03d}.parquet",
                     compression="zstd", compression_level=ZSTD_LEVEL)
    return df.height


def cols_from_trades(trades, symbol: str):
    """Column-oriented accumulation (cheap for multi-million-event extreme days)."""
    ts, px, sz, ex, cd, tid, tp = [], [], [], [], [], [], []
    for t in trades:
        ts.append(t.timestamp); px.append(float(t.price)); sz.append(float(t.size))
        ex.append(t.exchange); cd.append(list(t.conditions) if t.conditions else [])
        tid.append(int(t.id) if t.id is not None else None); tp.append(t.tape)
    return {"symbol": [symbol] * len(ts), "ts_utc": ts, "price": px, "size": sz,
            "exchange": ex, "conditions": cd, "trade_id": tid, "tape": tp}


def cols_from_quotes(quotes, symbol: str):
    ts, bp, bsz, ap, asz, bxe, axe, cd, tp = [], [], [], [], [], [], [], [], []
    for q in quotes:
        ts.append(q.timestamp); bp.append(float(q.bid_price)); bsz.append(float(q.bid_size))
        ap.append(float(q.ask_price)); asz.append(float(q.ask_size))
        bxe.append(q.bid_exchange); axe.append(q.ask_exchange)
        cd.append(list(q.conditions) if q.conditions else []); tp.append(q.tape)
    return {"symbol": [symbol] * len(ts), "ts_utc": ts, "bid_price": bp, "bid_size": bsz,
            "ask_price": ap, "ask_size": asz, "bid_exchange": bxe, "ask_exchange": axe,
            "conditions": cd, "tape": tp}


def empty_frame(cols):
    schema = {"symbol": pl.Utf8, "ts_utc": pl.Datetime("us", "UTC")}
    for c in cols:
        if c not in schema:
            schema[c] = pl.Float64 if c not in ("conditions", "tape", "exchange",
                                                "bid_exchange", "ask_exchange", "trade_id") else (
                pl.List(pl.Utf8) if c == "conditions" else
                (pl.Int64 if c == "trade_id" else pl.Utf8))
    return pl.DataFrame(schema=schema)


def frame(acc, cols):
    if not acc or not acc.get("ts_utc"):
        return empty_frame(cols)
    df = pl.DataFrame(acc)
    return df.select([c for c in cols if c in df.columns] +
                     [c for c in df.columns if c not in cols])


def sha256_file(p: Path):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def should_skip(out_root: Path, day: str, kind: str, force: bool) -> bool:
    if force:
        return False
    mp = out_root / kind / f"{day}.manifest.json"
    fp = out_root / kind / f"{day}.parquet"
    if not (mp.exists() and fp.exists()):
        return False
    try:
        m = json.loads(mp.read_text())
    except Exception:
        return False
    return m.get("status") == "ok"


def write_artifact(out_root: Path, day: str, kind: str, df: "pl.DataFrame",
                   meta: dict, append_global: bool = True) -> dict:
    d = out_root / kind
    d.mkdir(parents=True, exist_ok=True)
    fp = d / f"{day}.parquet"
    tmp = d / f"{day}.parquet.tmp"
    df.write_parquet(tmp, compression="zstd", compression_level=ZSTD_LEVEL)
    os.replace(tmp, fp)
    man = dict(meta)
    man.update({"day": day, "kind": kind, "file": f"{kind}/{day}.parquet",
                "rows": int(df.height), "sha256": sha256_file(fp),
                "schema": {c: str(df.schema[c]) for c in df.columns}})
    if df.height:
        man["min_ts"] = str(df["ts_utc"].min())
        man["max_ts"] = str(df["ts_utc"].max())
    mp = d / f"{day}.manifest.json"
    tmpm = d / f"{day}.manifest.json.tmp"
    tmpm.write_text(json.dumps(man, indent=1, default=str))
    os.replace(tmpm, mp)
    if append_global:
        with open(out_root / "manifest.jsonl", "a") as fh:
            fh.write(json.dumps(man, separators=(",", ":"), default=str) + "\n")
    return man


PAGE_LIMIT = 10000
PAGE_FLUSH_ROWS = 50000


def _parse_ts(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if "." in s:
        head, tail = s.split(".", 1)
        digits, off = "", ""
        for i, ch in enumerate(tail):
            if ch.isdigit():
                digits += ch
            else:
                off = tail[i:]
                break
        s = f"{head}.{digits[:6]}{off}"
    return datetime.fromisoformat(s)


def cols_from_trade_dicts(ev, symbol: str):
    ts, px, sz, ex, cd, tid, tp = [], [], [], [], [], [], []
    for t in ev:
        ts.append(_parse_ts(t["t"])); px.append(float(t["p"])); sz.append(float(t["s"]))
        ex.append(t.get("x")); cd.append(list(t.get("c") or []))
        i_ = t.get("i"); tid.append(int(i_) if i_ is not None else None); tp.append(t.get("z"))
    return {"symbol": [symbol] * len(ts), "ts_utc": ts, "price": px, "size": sz,
            "exchange": ex, "conditions": cd, "trade_id": tid, "tape": tp}


def cols_from_quote_dicts(ev, symbol: str):
    ts, bp, bsz, ap, asz, bxe, axe, cd, tp = [], [], [], [], [], [], [], [], []
    for q in ev:
        ts.append(_parse_ts(q["t"])); bp.append(float(q["bp"])); bsz.append(float(q["bs"]))
        ap.append(float(q["ap"])); asz.append(float(q["as"]))
        bxe.append(q.get("bx")); axe.append(q.get("ax"))
        cd.append(list(q.get("c") or [])); tp.append(q.get("z"))
    return {"symbol": [symbol] * len(ts), "ts_utc": ts, "bid_price": bp, "bid_size": bsz,
            "ask_price": ap, "ask_size": asz, "bid_exchange": bxe, "ask_exchange": axe,
            "conditions": cd, "tape": tp}


def iter_pages(kind: str, symbol: str, start, end, headers: dict, session, retries: int = 3):
    """Stream provider pages (<=PAGE_LIMIT events each) so memory stays flat."""
    url = f"https://data.alpaca.markets/v2/stocks/{kind}"
    params = {"symbols": symbol, "start": start.isoformat(), "end": end.isoformat(),
              "feed": "sip", "limit": PAGE_LIMIT}
    tok = None
    while True:
        if tok:
            params["page_token"] = tok
        r = None
        for attempt in range(retries):
            try:
                r = session.get(url, params=params, headers=headers, timeout=60)
                if r.status_code == 429:
                    time.sleep(2 * (attempt + 1))
                    continue
                r.raise_for_status()
                break
            except Exception:
                if attempt == retries - 1:
                    raise
                time.sleep(3 * (attempt + 1))
        if r is None or r.status_code == 429:
            raise RuntimeError(f"request failed after {retries} attempts")
        js = r.json()
        data = js.get(kind) or {}
        ev = data.get(symbol, []) if isinstance(data, dict) else list(data)
        if ev:
            yield ev
        tok = js.get("next_page_token")
        if not tok:
            return


def fetch_day(day: str, kinds, req_sleep: float, out_root: Path, force: bool,
              limit_symbols: int | None = None, symbol_fn=symbols_for_day,
              append_global: bool = True):
    import shutil
    import requests
    if shutil.disk_usage("/mnt/c").free < SPACE_FLOOR_GB * 2**30:
        return {k: "space_stop" for k in kinds}
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    headers = {"APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
               "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"]}
    session = requests.Session()
    start, end = window_utc(day)
    sym_trades, sym_quotes = symbol_fn(day)
    if limit_symbols:
        sym_trades, sym_quotes = sym_trades[:limit_symbols], sym_quotes[:limit_symbols]
    results = {}
    for kind, syms in (("trades", sym_trades), ("quotes", sym_quotes)):
        if kind not in kinds:
            continue
        if should_skip(out_root, day, kind, force):
            results[kind] = "skip"
            continue
        errors, got = [], 0
        t0 = time.time()
        cols = TRADE_COLS if kind == "trades" else QUOTE_COLS
        acc = {c: [] for c in cols}
        parts_dir = out_root / kind / f"parts_{day}"
        if parts_dir.exists():
            shutil.rmtree(parts_dir)  # stale parts from an interrupted attempt
        parts_dir.mkdir(parents=True, exist_ok=True)
        n_parts, rows_total = 0, 0
        for i, s in enumerate(syms):
            n_ev = 0
            try:
                for ev in iter_pages(kind, s, start, end, headers, session):
                    part = (cols_from_trade_dicts(ev, s) if kind == "trades"
                            else cols_from_quote_dicts(ev, s))
                    for c in cols:
                        acc[c].extend(part[c])
                    n_ev += len(ev)
                    if len(acc["ts_utc"]) >= PAGE_FLUSH_ROWS:
                        rows_total += _flush_part(acc, cols, parts_dir, n_parts)
                        n_parts += 1
                        acc = {c: [] for c in cols}
                    del part, ev
            except Exception as e:
                errors.append({"symbol": s, "error": str(e)[:200]})
            if n_ev:
                got += 1
            if req_sleep:
                time.sleep(req_sleep)
        rows_total += _flush_part(acc, cols, parts_dir, n_parts)
        n_parts += 1
        if rows_total:
            df = pl.concat([pl.read_parquet(p) for p in sorted(parts_dir.glob("*.parquet"))])
        else:
            df = empty_frame(cols)
        status = "probe" if limit_symbols else ("ok" if not errors else "partial")
        meta = {"status": status,
                "provider": "alpaca", "feed": "sip", "client": "alpaca-rest-v2",
                "requested_at": datetime.now(timezone.utc).isoformat(),
                "window_utc": [start.isoformat(), end.isoformat()],
                "window_et": "09:25-16:05 America/New_York",
                "symbols_requested": len(syms), "symbols_with_data": got,
                "errors": errors, "elapsed_s": round(time.time() - t0, 1),
                "parts": int(n_parts), "part_syms": PART_SYMS}
        man = write_artifact(out_root, day, kind, df, meta, append_global)
        for p_ in sorted(parts_dir.glob("*.parquet")):
            p_.unlink()
        try:
            parts_dir.rmdir()
        except OSError:
            pass
        results[kind] = f"{man['status']} rows={man['rows']} syms={got}/{len(syms)} {man['elapsed_s']}s"
    return results


def rebuild_index(out_root: Path):
    """Rebuild the merged artifact index from per-day manifests (safe after workers)."""
    rows = []
    for mp in sorted(out_root.glob("*/*.manifest.json")):
        try:
            rows.append(json.loads(mp.read_text()))
        except Exception:
            continue
    ip = out_root / "manifest_index.jsonl"
    tmp = out_root / "manifest_index.jsonl.tmp"
    with open(tmp, "w") as fh:
        for m in rows:
            fh.write(json.dumps(m, separators=(",", ":"), default=str) + "\n")
    os.replace(tmp, ip)
    print(f"index: {len(rows)} artifacts -> {ip}")


def selftest():
    s, e = window_utc("2022-03-10")  # EST
    assert (e - s).total_seconds() == 6 * 3600 + 40 * 60
    assert s.astimezone(ET).hour == 9 and s.astimezone(ET).minute == 25
    s2, _ = window_utc("2025-06-02")  # EDT
    assert s2.astimezone(ET).hour == 9 and s2.astimezone(ET).minute == 25
    assert s2.hour == 13  # 09:25 EDT == 13:25 UTC
    # symbol derivation on a real anatomy day
    tr, qs = symbols_for_day("2022-03-10")
    assert "BRP" in tr, tr[:20]
    assert len(tr) <= 200 and 0 < len(qs) <= len(tr)
    # regeneration mode: SIP-discovered net + top-3 quotes + day listing
    trn, qsn = symbols_for_day_net("2021-02-01")
    assert "LODE" in trn and 0 < len(qsn) <= len(trn)
    assert "2021-02-01" in list_net_days()
    # rows_from_* pure helpers
    from types import SimpleNamespace as NS
    t = NS(timestamp=s, price=1.5, size=100.0, exchange="D", conditions=["@"], id=7, tape="A")
    r = cols_from_trades([t], "XYZ")
    assert r["conditions"] == [["@"]] and r["price"] == [1.5] and r["trade_id"] == [7]
    q = NS(timestamp=s, bid_price=1.0, bid_size=1.0, ask_price=1.2, ask_size=2.0,
           bid_exchange="T", ask_exchange="M", conditions=["R"], tape="C")
    rq = cols_from_quotes([q], "XYZ")
    assert rq["ask_price"] == [1.2] and rq["conditions"] == [["R"]]
    df = frame(r, TRADE_COLS)
    assert df.height == 1 and "trade_id" in df.columns
    assert frame({"symbol": [], "ts_utc": []}, TRADE_COLS).height == 0
    # REST dict adapters (page streaming)
    td = {"t": "2021-02-01T14:30:00.123456789Z", "p": 1.5, "s": 100, "x": "D",
          "c": ["@"], "i": 7, "z": "A"}
    r3 = cols_from_trade_dicts([td], "XYZ")
    assert r3["price"] == [1.5] and r3["trade_id"] == [7]
    assert r3["ts_utc"][0].microsecond == 123456 and r3["ts_utc"][0].tzinfo is not None
    qd = {"t": "2021-02-01T14:30:00.5Z", "bp": 1.0, "bs": 1.0, "ap": 1.2, "as": 2.0,
          "bx": "T", "ax": "M", "c": ["R"], "z": "C"}
    r4 = cols_from_quote_dicts([qd], "XYZ")
    assert r4["ask_price"] == [1.2] and r4["conditions"] == [["R"]]
    # atomic write + resume logic
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        assert not should_skip(td, "2022-03-10", "trades", False)
        write_artifact(td, "2022-03-10", "trades", df,
                       {"status": "ok", "provider": "alpaca"})
        assert (td / "trades" / "2022-03-10.parquet").exists()
        assert should_skip(td, "2022-03-10", "trades", False)
        assert not should_skip(td, "2022-03-10", "trades", True)
        write_artifact(td, "2022-03-10", "quotes", df, {"status": "partial"})
        assert not should_skip(td, "2022-03-10", "quotes", False)  # partial retries
        assert (td / "manifest.jsonl").exists()
    # worker-safe index rebuild (no global log)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        write_artifact(td, "2022-03-10", "trades", df, {"status": "ok"}, append_global=False)
        write_artifact(td, "2022-03-10", "quotes", df, {"status": "ok"}, append_global=False)
        rebuild_index(td)
        assert (td / "manifest_index.jsonl").exists()
        assert len((td / "manifest_index.jsonl").read_text().splitlines()) == 2
        assert not (td / "manifest.jsonl").exists()
    print("self-test OK")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", nargs="*", default=None)
    ap.add_argument("--panel", action="store_true")
    ap.add_argument("--all", action="store_true",
                    help="net mode: every day with a candidates file")
    ap.add_argument("--net", action="store_true",
                    help="regeneration mode: symbols from SIP candidate net (data/sip/net)")
    ap.add_argument("--workers", type=int, default=1,
                    help="disjoint-day worker processes (no shared output contention)")
    ap.add_argument("--no-global-log", action="store_true",
                    help="skip the shared manifest.jsonl append (worker-safe); use --index later")
    ap.add_argument("--index", action="store_true", help="rebuild manifest_index.jsonl")
    ap.add_argument("--kinds", default="trades,quotes")
    ap.add_argument("--req-sleep", type=float, default=0.45)
    ap.add_argument("--limit-symbols", type=int, default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        selftest()
        return
    out_root = Path(args.out) if args.out else (NET_OUT if args.net else OUT)
    if args.index:
        rebuild_index(out_root)
        return
    days = args.days or (list_net_days() if (args.net and args.all)
                         else (sorted(PANEL) if args.panel else []))
    if not days:
        ap.error("provide --days, --panel, or --net --all")
    symbol_fn = symbols_for_day_net if args.net else symbols_for_day
    kinds = {k.strip() for k in args.kinds.split(",") if k.strip()}
    out_root.mkdir(parents=True, exist_ok=True)

    if args.workers > 1:
        import subprocess
        import sys
        chunks = [days[i::args.workers] for i in range(args.workers)]
        procs = []
        for i, ch in enumerate(chunks):
            if not ch:
                continue
            cmd = [sys.executable, str(Path(__file__).resolve()),
                   "--days", *ch, "--kinds", args.kinds,
                   "--req-sleep", str(args.req_sleep), "--out", str(out_root),
                   "--no-global-log"]
            if args.net:
                cmd.append("--net")
            if args.force:
                cmd.append("--force")
            log = open(f"/tmp/opencode/ingest_w{i}.log", "ab")
            procs.append(subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT))
            print(f"worker {i}: {len(ch)} days -> /tmp/opencode/ingest_w{i}.log", flush=True)
        rc = 0
        for p in procs:
            rc |= p.wait()
        print(f"workers done rc={rc}", flush=True)
        return

    for day in days:
        try:
            res = fetch_day(day, kinds, args.req_sleep, out_root, args.force,
                            args.limit_symbols, symbol_fn, not args.no_global_log)
            print(f"{day}: " + " | ".join(f"{k}:{v}" for k, v in res.items()), flush=True)
        except FileNotFoundError as e:
            print(f"{day}: SKIP ({e})", flush=True)


if __name__ == "__main__":
    main()
