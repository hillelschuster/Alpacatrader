#!/usr/bin/env python3
"""Metadata-only catalyst event acquisition for the news-catalyst lane.

Sources (in the brief's preferred order):
  (a) SEC EDGAR submissions JSON per CIK (company_tickers.json ticker->CIK seed):
      acceptanceDateTime + form, plus linked older submissions shards that reach
      into the panel window. NO filing full text is ever fetched.
  (b) Alpaca NewsClient for panel symbols with no EDGAR seed (fallback / gap fill):
      created_at + headline + source only.

Stored fields are exactly {day, symbol, form_or_source, event_ts_utc,
headline_if_news} plus provenance (cik, accession, filing_date, wire bytes).
The acquisition is resumable and byte-accounted against a hard network cap.

Usage:
  uv run --no-sync python factory/scripts/alpha_news_catalyst_events.py edgar
  uv run --no-sync python factory/scripts/alpha_news_catalyst_events.py news
  uv run --no-sync python factory/scripts/alpha_news_catalyst_events.py finalize
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

PANEL_ROOT = Path(
    os.environ.get("ALPHA_OPEN_SEARCH_V1", Path.home() / "alpha-data" / "open-search-v1")
)
PANEL_DAYS = PANEL_ROOT / "days"

BLOCKS = {
    "dev": ("2021-05-01", "2022-12-31"),
    "validation": ("2023-01-01", "2023-12-31"),
    "late": ("2025-02-01", "2026-05-31"),
}
SEALED = ("2024", "2025-01", "2026-06", "2026-07", "2026-08")

# Event retention window: the earliest decision clock is 2021-05-03 09:35 ET and
# the 5-day feature looks back 120h, so filings from 2021-04 onward suffice. The
# upper bound is the last late-block session (2026-05-29); nothing later is used
# by any panel decision. Rows outside the window are counted, not silently kept.
KEEP_FROM = "2021-01-01T00:00:00+00:00"
KEEP_TO = "2026-06-01T00:00:00+00:00"

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions"
USER_AGENT = "Alpacatrader research contact research@alpacatrader.local"

NETWORK_CAP_BYTES = 100_000_000  # hard cap: metadata-only, no full-text bodies


# ---------------------------------------------------------------- panel input
def panel_tickers() -> tuple[set[str], dict[str, set[str]]]:
    files = sorted(PANEL_DAYS.glob("????-??-??.parquet"))
    if not files:
        raise SystemExit(f"no panel day parquets under {PANEL_DAYS}")
    days = [p.stem for p in files]
    for d in days:
        for sealed in SEALED:
            if d.startswith(sealed):
                raise SystemExit(f"protected panel day {d} in panel root")
    df = pl.scan_parquet(files).select(["day", "ticker"]).collect()
    per_block: dict[str, set[str]] = {}
    for name, (s, e) in BLOCKS.items():
        per_block[name] = set(
            df.filter((pl.col("day") >= s) & (pl.col("day") <= e))["ticker"].to_list()
        )
    return set().union(*per_block.values()), per_block


# ------------------------------------------------------------------ http glue
def http_get_json(url: str, timeout: int = 90, attempts: int = 4, pace_s: float = 0.0):
    """gzip-aware GET returning (wire_bytes, parsed_json, status, error, dec_bytes).

    wire_bytes is the compressed payload that actually crosses the network; the
    decompressed size is transient (never stored) but reported for the manifest.
    urllib does NOT auto-decompress a negotiated gzip body, so one read gives us
    both numbers.
    """
    last = None
    for k in range(attempts):
        if pace_s:
            time.sleep(pace_s)
        req = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = r.read()
                wire = len(payload)
                raw = (
                    gzip.decompress(payload)
                    if r.headers.get("Content-Encoding") == "gzip"
                    else payload
                )
            return wire, json.loads(raw), 200, None, len(raw)
        except urllib.error.HTTPError as e:  # 403/429 are pacing, not absence
            last = f"HTTP {e.code}"
            time.sleep(min(60, 5 * (2**k)))
        except Exception as e:  # noqa: BLE001 - recorded, never swallowed
            last = str(e)[:200]
            time.sleep(min(30, 2 * (2**k)))
    return 0, None, 0, f"failed after {attempts} attempts: {last}", 0


def epoch_us(value) -> int | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1_000_000)


def et_day(us: int) -> str:
    return datetime.fromtimestamp(us / 1_000_000, UTC).astimezone(ET).date().isoformat()


# ------------------------------------------------------------------- edgar
def flatten(js: dict) -> list[dict]:
    r = js.get("filings", {}).get("recent", {})
    out = []
    n = len(r.get("accessionNumber", []) or [])
    for i in range(n):
        out.append({k: (v[i] if i < len(v) else None) for k, v in r.items()})
    return out


def project_rows(symbol: str, cik: str, submissions: list[dict], url_note: str) -> list[dict]:
    rows = []
    for s in submissions:
        acc = s.get("acceptanceDateTime")
        us = epoch_us(acc)
        if us is None:
            continue
        iso_from = datetime.fromisoformat(KEEP_FROM).timestamp()
        iso_to = datetime.fromisoformat(KEEP_TO).timestamp()
        if not (iso_from <= us / 1_000_000 <= iso_to):
            continue
        rows.append(
            {
                "day": et_day(us),
                "symbol": symbol,
                "form_or_source": (s.get("form") or "unknown").strip(),
                "event_ts_utc": datetime.fromtimestamp(us / 1_000_000, UTC).isoformat(),
                "event_us": us,
                "cik": cik,
                "accession": s.get("accessionNumber"),
                "filing_date": s.get("filingDate"),
                "report_date": s.get("reportDate"),
                "headline_if_news": None,
                "source": "sec_edgar_submissions",
                "source_url": url_note,
            }
        )
    return rows


def cmd_edgar(out_dir: Path, workers: int, cap: int, sample: int | None) -> None:
    t0 = time.time()
    union, per_block = panel_tickers()
    seeded_file = out_dir / "ticker_to_cik.json"
    ticker_to_cik: dict[str, str] = {}
    if seeded_file.exists():
        ticker_to_cik = json.loads(seeded_file.read_text())
    if not ticker_to_cik:
        wire, js, status, err, dec = http_get_json(SEC_TICKERS_URL)
        if status != 200:
            raise SystemExit(f"company_tickers.json fetch failed: {err}")
        for v in js.values():
            t = str(v.get("ticker", "")).upper()
            if t in union:
                ticker_to_cik[t] = f"{int(v['cik_str']):010d}"
        seeded_file.write_text(json.dumps(ticker_to_cik, sort_keys=True))
        (out_dir / "manifest_company_tickers.json").write_text(
            json.dumps(
                {
                    "url": SEC_TICKERS_URL,
                    "http_status": status,
                    "wire_bytes": wire,
                    "decompressed_bytes": dec,
                    "panel_tickers_with_cik_seed": len(ticker_to_cik),
                    "panel_tickers_total": len(union),
                    "fetched_at": datetime.now(UTC).isoformat(),
                },
                indent=1,
            )
        )
    seeded = sorted(ticker_to_cik)
    if sample:
        seeded = seeded[:sample]

    manifest_path = out_dir / "manifest_edgar.jsonl"
    done: dict[str, dict] = {}
    if manifest_path.exists():
        for line in manifest_path.read_text().splitlines():
            if line.strip():
                m = json.loads(line)
                done[m["symbol"]] = m
    man = open(manifest_path, "a")
    rows_path = out_dir / "edgar_events.jsonl"
    rowf = open(rows_path, "a")
    wire_total = sum(m.get("wire_bytes", 0) for m in done.values())
    dec_total = sum(m.get("decompressed_bytes", 0) for m in done.values())
    n_rows = 0
    todo = [t for t in seeded if done.get(t, {}).get("status") != "complete"]
    print(f"edgar: {len(todo)} CIKs to fetch ({len(done)} already complete)", flush=True)

    def fetch(symbol: str) -> tuple[str, dict, list[dict]]:
        cik = ticker_to_cik[symbol]
        base_wire = 0
        base_dec = 0
        urls: list[str] = []
        wire, js, status, err, dec = http_get_json(
            f"{SEC_SUBMISSIONS}/CIK{cik}.json", pace_s=0.12
        )
        base_wire += wire
        base_dec += dec
        urls.append(f"{SEC_SUBMISSIONS}/CIK{cik}.json")
        if status != 200:
            return symbol, {
                "symbol": symbol,
                "cik": cik,
                "status": "unknown_fetch_failed",
                "error": err,
                "urls": urls,
                "wire_bytes": base_wire,
                "decompressed_bytes": base_dec,
                "rows_out": 0,
                "fetched_at": datetime.now(UTC).isoformat(),
            }, []
        subs = flatten(js)
        n_shards = 0
        for f in js.get("filings", {}).get("files", []) or []:
            # only shards that reach into the panel window are worth a request
            if str(f.get("filingTo") or "") < KEEP_FROM[:10]:
                continue
            name = f.get("name")
            w2, js2, st2, err2, dec2 = http_get_json(
                f"{SEC_SUBMISSIONS}/{name}", pace_s=0.12
            )
            base_wire += w2
            base_dec += dec2
            urls.append(f"{SEC_SUBMISSIONS}/{name}")
            if st2 == 200 and isinstance(js2, dict):
                subs.extend(flatten(js2))
                n_shards += 1
        rows = project_rows(symbol, cik, subs, f"{SEC_SUBMISSIONS}/CIK{cik}.json")
        return symbol, {
            "symbol": symbol,
            "cik": cik,
            "status": "complete",
            "submission_rows_seen": len(subs),
            "older_shards_fetched": n_shards,
            "urls": urls,
            "wire_bytes": base_wire,
            "decompressed_bytes": base_dec,
            "rows_out": len(rows),
            "fetched_at": datetime.now(UTC).isoformat(),
        }, rows

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch, s): s for s in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            symbol, m, rows = fut.result()
            man.write(json.dumps(m) + "\n")
            man.flush()
            for r in rows:
                rowf.write(json.dumps(r) + "\n")
            rowf.flush()
            n_rows += len(rows)
            wire_total += m.get("wire_bytes", 0)
            dec_total += m.get("decompressed_bytes", 0)
            if wire_total > cap:
                man.close()
                rowf.close()
                raise SystemExit(
                    f"HARD CAP EXCEEDED: network wire {wire_total/1e6:.1f}MB > "
                    f"{cap/1e6:.0f}MB at {symbol}"
                )
            if i % 100 == 0:
                print(
                    f"  {i}/{len(todo)} rows={n_rows} wire={wire_total/1e6:.1f}MB "
                    f"transient_dec={dec_total/1e6:.0f}MB elapsed={time.time()-t0:.0f}s",
                    flush=True,
                )
    man.close()
    rowf.close()
    print(
        f"edgar done: symbols={len(done)+len(todo)} rows={n_rows} "
        f"wire={wire_total/1e6:.1f}MB transient_dec={dec_total/1e6:.0f}MB "
        f"elapsed={time.time()-t0:.0f}s",
        flush=True,
    )


# ------------------------------------------------------------ alpaca news
def cmd_news(out_dir: Path, cap: int, per_ticker_years: bool, sample_n: int, seed: int) -> None:
    """Alpaca NewsClient fallback for panel symbols with no EDGAR CIK seed.

    The unseeded stratum is acquired as a DETERMINISTIC STRATIFIED RANDOM SAMPLE
    (fixed seed, proportional to each block's unseeded count) because the whole
    stratum does not fit the shared 100 MB metadata cap. Raw REST is used so the
    manifest records true wire bytes; bodies are excluded (include_content=false),
    only created_at + headline + symbols are retained. A shared byte guard stops
    cleanly at the cap and the manifest records exactly what was reached.
    """
    union, per_block = panel_tickers()
    t2c = json.loads((out_dir / "ticker_to_cik.json").read_text())
    unseeded = {b: sorted(t for t in ts if t not in t2c) for b, ts in per_block.items()}
    total_unseeded = sum(len(v) for v in unseeded.values())
    manifest_path = out_dir / "manifest_news.jsonl"
    done: set[str] = set()
    wire_total = 0
    rows_total = 0
    if (out_dir / "manifest_edgar.jsonl").exists():
        wire_total += sum(
            json.loads(l).get("wire_bytes", 0)
            for l in (out_dir / "manifest_edgar.jsonl").read_text().splitlines()
            if l.strip()
        )
    if manifest_path.exists():
        for line in manifest_path.read_text().splitlines():
            if line.strip():
                m = json.loads(line)
                done.add(m["symbol"])
                wire_total += m.get("wire_bytes", 0)
                rows_total += m.get("rows_out", 0)

    kv: dict[str, str] = {}
    envf = Path(".env")
    if envf.exists():
        for line in envf.read_text().splitlines():
            if line.startswith("ALPACA_API_KEY="):
                kv["APCA-API-KEY-ID"] = line.split("=", 1)[1].strip()
            elif line.startswith("ALPACA_SECRET_KEY="):
                kv["APCA-API-SECRET-KEY"] = line.split("=", 1)[1].strip()
    if not kv:
        raise SystemExit("ALPACA_API_KEY/ALPACA_SECRET_KEY are required for the news fallback")

    # stratified deterministic sample across the three blocks
    todo: list[str] = []
    strata: dict[str, int] = {}
    rng = random.Random(seed)
    for b, syms in unseeded.items():
        take = int(round(sample_n * len(syms) / total_unseeded))
        strata[b] = take
        todo += rng.sample(sorted(syms), min(take, len(syms)))
    todo = [t for t in todo if t not in done]
    (out_dir / "manifest_news_sampling.json").write_text(
        json.dumps(
            {
                "seed": seed,
                "requested_sample": sample_n,
                "strata_unseeded": {b: len(v) for b, v in unseeded.items()},
                "strata_sampled": strata,
                "sampled_total": sum(strata.values()),
            },
            indent=1,
        )
    )

    man = open(manifest_path, "a")
    rowf = open(out_dir / "news_events.jsonl", "a")
    print(
        f"news: {len(todo)} sampled unseeded symbols to query ({len(done)} done); "
        f"strata={strata}",
        flush=True,
    )
    years = ["2021", "2022", "2023", "2024", "2025", "2026"]

    def rest_news(url: str) -> tuple[int, dict | None, str | None]:
        last = None
        for k in range(4):
            req = urllib.request.Request(url, headers={**kv, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    raw = r.read()
                return len(raw), json.loads(raw), None
            except Exception as ex:  # noqa: BLE001
                last = str(ex)[:150]
                time.sleep(min(30, 2 * (2**k)))
        return 0, None, f"failed after 4 attempts: {last}"

    for i, sym in enumerate(todo, 1):
        rows, wire, fetched, errs, truncated = [], 0, 0, [], False
        for y in years:
            url = (
                "https://data.alpaca.markets/v1beta1/news"
                f"?symbols={sym}&start={y}-01-01&end={y}-12-31"
                "&limit=50&exclude_contentless=true&include_content=false&sort=desc"
            )
            w, js, err = rest_news(url)
            fetched += 1
            wire += w
            if err:
                errs.append(err)
                continue
            arts = (js or {}).get("news", []) or []
            if len(arts) == 50:
                truncated = True  # this symbol-year hit the page cap: incomplete
            token = (js or {}).get("next_page_token")
            pages = 1
            while token and pages < 6:
                w2, js2, err2 = rest_news(url + f"&page_token={token}")
                fetched += 1
                wire += w2
                if err2:
                    errs.append(err2)
                    break
                more = (js2 or {}).get("news", []) or []
                arts.extend(more)
                token = (js2 or {}).get("next_page_token")
                pages += 1
                if len(more) < 50:
                    break
            for a in arts:
                ca = a.get("created_at")
                try:
                    dt = datetime.fromisoformat(str(ca).replace("Z", "+00:00"))
                except ValueError:
                    continue
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                us = int(dt.timestamp() * 1_000_000)
                rows.append(
                    {
                        "day": datetime.fromtimestamp(us / 1_000_000, UTC)
                        .astimezone(ET)
                        .date()
                        .isoformat(),
                        "symbol": sym,
                        "form_or_source": "alpaca_news",
                        "event_ts_utc": datetime.fromtimestamp(us / 1_000_000, UTC).isoformat(),
                        "event_us": us,
                        "cik": None,
                        "accession": None,
                        "filing_date": None,
                        "report_date": None,
                        "headline_if_news": (a.get("headline") or "")[:200],
                        "source": "alpaca_news",
                        "source_url": f"news REST symbols={sym} {y}",
                    }
                )
            time.sleep(0.05)
        m = {
            "symbol": sym,
            "source": "alpaca_news",
            "status": "complete" if not errs else "partial",
            "requests": fetched,
            "rows_out": len(rows),
            "wire_bytes": wire,
            "truncated_page_cap": truncated,
            "errors": errs[:3],
            "fetched_at": datetime.now(UTC).isoformat(),
        }
        man.write(json.dumps(m) + "\n")
        man.flush()
        for r in rows:
            rowf.write(json.dumps(r) + "\n")
        rowf.flush()
        wire_total += wire
        rows_total += len(rows)
        if wire_total > cap:
            man.close()
            rowf.close()
            raise SystemExit(
                f"HARD CAP EXCEEDED: network wire {wire_total/1e6:.1f}MB > {cap/1e6:.0f}MB at {sym}"
            )
        if i % 50 == 0:
            print(
                f"  {i}/{len(todo)} news_rows={rows_total} wire_total={wire_total/1e6:.1f}MB",
                flush=True,
            )
    man.close()
    rowf.close()
    print(
        f"news done: symbols={len(done)+len(todo)} rows={rows_total} "
        f"wire_total(with edgar)={wire_total/1e6:.1f}MB",
        flush=True,
    )


# ------------------------------------------------------------- finalize
def cmd_finalize(out_dir: Path, dest_dir: Path) -> None:
    frames = []
    manifest_summary = {"created_at": datetime.now(UTC).isoformat()}
    edgar_rows = out_dir / "edgar_events.jsonl"
    news_rows = out_dir / "news_events.jsonl"
    for label, path in (("sec_edgar_submissions", edgar_rows), ("alpaca_news", news_rows)):
        if not path.exists():
            continue
        rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        if rows:
            frames.append(pl.DataFrame(rows))
        manifest_summary[label] = {"rows": len(rows)}
    if not frames:
        raise SystemExit("no event rows to finalize")
    df = pl.concat(frames, how="diagonal_relaxed").sort(["symbol", "event_us"])
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "news_catalyst_events.parquet"
    df.write_parquet(dest)
    manifest_summary["event_table"] = str(dest)
    manifest_summary["event_table_rows"] = df.height
    manifest_summary["event_table_bytes"] = dest.stat().st_size

    # per-symbol acquisition manifest (compact) + totals
    per_sym = {}
    for label, mfile in (
        ("sec_edgar_submissions", out_dir / "manifest_edgar.jsonl"),
        ("alpaca_news", out_dir / "manifest_news.jsonl"),
    ):
        if not mfile.exists():
            continue
        recs = [json.loads(l) for l in mfile.read_text().splitlines() if l.strip()]
        per_sym[label] = {
            "symbols_attempted": len(recs),
            "symbols_complete": sum(1 for r in recs if r.get("status") == "complete"),
            "symbols_failed": sum(1 for r in recs if r.get("status") != "complete"),
            "wire_bytes": sum(
                r.get("wire_bytes", r.get("wire_bytes_est", 0)) for r in recs
            ),
            "transient_decompressed_bytes": sum(
                r.get("decompressed_bytes", 0) for r in recs
            ),
            "rows_out": sum(r.get("rows_out", 0) for r in recs),
            "per_symbol": {
                r["symbol"]: {
                    "status": r.get("status"),
                    "rows_out": r.get("rows_out", 0),
                    "wire_bytes": r.get("wire_bytes", r.get("wire_bytes_est", 0)),
                }
                for r in recs
            },
        }
    manifest_summary["acquisition"] = per_sym
    manifest_summary["network_cap_bytes"] = NETWORK_CAP_BYTES
    manifest_summary["network_bytes_total"] = sum(
        v["wire_bytes"] for v in per_sym.values()
    )
    manifest_summary["transient_decompressed_bytes_total"] = sum(
        v["transient_decompressed_bytes"] for v in per_sym.values()
    )
    (dest_dir / "news_catalyst_events_manifest.json").write_text(json.dumps(manifest_summary, indent=1))
    print(json.dumps({k: v for k, v in manifest_summary.items() if k != "acquisition"}, indent=1))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["edgar", "news", "finalize"])
    p.add_argument("--out-dir", default="/tmp/news_lane")
    p.add_argument("--dest-dir", default="factory/artifacts/overnight")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--cap-bytes", type=int, default=NETWORK_CAP_BYTES)
    p.add_argument("--sample", type=int, default=None)
    p.add_argument("--per-ticker-years", action="store_true")
    p.add_argument("--sample-news", type=int, default=400)
    p.add_argument("--seed", type=int, default=20261012)
    a = p.parse_args()
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if a.mode == "edgar":
        cmd_edgar(out_dir, a.workers, a.cap_bytes, a.sample)
    elif a.mode == "news":
        cmd_news(out_dir, a.cap_bytes, a.per_ticker_years, a.sample_news, a.seed)
    else:
        cmd_finalize(out_dir, Path(a.dest_dir))


if __name__ == "__main__":
    main()
