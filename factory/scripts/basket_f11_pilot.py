#!/usr/bin/env python
"""F11 pilot — entry-window NBBO/spread, next-print slippage, halt/gap, dollar volume.

Reads the paired raw SIP sessions (``data/sip/quotes`` + ``data/sip/trades``) and the
canonical anatomy + bars, then measures, per top-three canonical slot of the four fixed
base architectures, the observable execution-realism statistics.  This is measurement:
it never alters selection, fills, or engine semantics, and it is not evidence against the
top-gainer phenomenon.

Evidence scope: only the intersection of paired raw-tape days with canonical dev days
(20 sessions), never the full 1,066-day span.  The run records the discovered pair count
and symbols so the coverage claim cannot be silently widened.

See ``factory/artifacts/basket/phase2/F11_exec/plan.md`` for the frozen measurement spec.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import polars as pl

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))

from basket_sim import (  # noqa: E402
    BARS_DIR, ANAT_DIR, guard_day, load_anatomy, session_end_map, snapshot_of,
)

RAW_SIP = ROOT / "data" / "sip"
QUOTES_DIR = RAW_SIP / "quotes"
TRADES_DIR = RAW_SIP / "trades"

NY = ZoneInfo("America/New_York")
UTC = timezone.utc

# architecture id -> (pop, T, canonical expected fill minute)
ARCHITECTURES = {
    "A_pm_N3_R2_10_5": ("A_pm", 570, 570),
    "A_pm31_N3_R2_10_5": ("A_pm31", 570, 571),
    "B600_N3_R2_10_5": ("B", 600, 600),
    "A_pm_N3_R0": ("A_pm", 570, 570),
}
QUOTE_AGE_S = 60
HALT_GAP_MIN = 5


def entry_timestamp(day: str, et: int) -> datetime:
    """ET minute-of-day ``et`` on ``day`` as a tz-aware UTC datetime."""
    base = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=NY)
    return (base + timedelta(minutes=et)).astimezone(UTC)


@dataclass
class EntryCandidate:
    architecture: str
    day: str
    ticker: str
    expected_et: int
    entry_et: Optional[int]
    entry_px: Optional[float]
    blocked: bool
    gap_minutes: int


@dataclass
class EntryObservation:
    pre_quote_covered: bool
    post_quote_covered: bool
    spread_bps: Optional[float]
    half_spread_bps: Optional[float]
    next_print_covered: bool
    next_print_slippage_bps: Optional[float]
    halt_like_gap_count: int


def observe_entry(candidate: EntryCandidate, quotes: pl.DataFrame,
                  trades: pl.DataFrame, bars: pl.DataFrame) -> EntryObservation:
    """Per-entry observability measures from raw quotes/trades and canonical bars."""
    sym = candidate.ticker
    et = candidate.entry_et
    px = candidate.entry_px
    assert et is not None and px is not None, "observe_entry requires a filled candidate"
    entry_ts = entry_timestamp(candidate.day, et)
    lo = entry_ts - timedelta(seconds=QUOTE_AGE_S)
    hi = entry_ts + timedelta(seconds=QUOTE_AGE_S)

    q = quotes.filter(pl.col("symbol") == sym)
    pre = q.filter((pl.col("ts_utc") <= entry_ts) & (pl.col("ts_utc") >= lo))
    valid = (pl.col("bid_price") > 0) & (pl.col("ask_price") >= pl.col("bid_price"))
    pre_valid = pre.filter(valid).sort("ts_utc")
    post_valid = q.filter(valid & (pl.col("ts_utc") >= entry_ts) &
                          (pl.col("ts_utc") <= hi)).sort("ts_utc")

    spread_bps = half_spread_bps = None
    if pre_valid.height:
        bid = float(pre_valid["bid_price"][-1])
        ask = float(pre_valid["ask_price"][-1])
        spread_bps = 10000.0 * (ask - bid) / ((ask + bid) / 2.0)
        half_spread_bps = spread_bps / 2.0

    tr = trades.filter(pl.col("symbol") == sym)
    nxt = tr.filter((pl.col("ts_utc") >= entry_ts) & (pl.col("ts_utc") <= hi)).sort("ts_utc")
    slippage_bps = None
    if nxt.height:
        slippage_bps = 10000.0 * (float(nxt["price"][0]) / px - 1.0)

    ets = bars.filter(pl.col("ticker") == sym).select("et").sort("et")["et"].to_list()
    halt_gap = sum(1 for i in range(1, len(ets)) if ets[i] - ets[i - 1] >= HALT_GAP_MIN)

    return EntryObservation(
        pre_quote_covered=pre_valid.height > 0,
        post_quote_covered=post_valid.height > 0,
        spread_bps=spread_bps,
        half_spread_bps=half_spread_bps,
        next_print_covered=nxt.height > 0,
        next_print_slippage_bps=slippage_bps,
        halt_like_gap_count=halt_gap,
    )


def paired_days() -> list[str]:
    q = {p.name[:-8] for p in QUOTES_DIR.glob("*.parquet")}
    t = {p.name[:-8] for p in TRADES_DIR.glob("*.parquet")}
    days = sorted(q & t)
    for d in days:
        guard_day(d)
    return days


def process_day(day: str, sem: dict[str, int]) -> list[dict]:
    rec = load_anatomy(day)
    quotes = pl.read_parquet(QUOTES_DIR / f"{day}.parquet")
    trades = pl.read_parquet(TRADES_DIR / f"{day}.parquet")
    bars = pl.read_parquet(BARS_DIR / f"{day}.parquet")
    se = sem.get(day, 959)
    bars = bars.filter(pl.col("et") <= se)

    rth_lo = entry_timestamp(day, 570)
    rth_hi = entry_timestamp(day, se)

    q_by = {k[0]: d for k, d in quotes.partition_by("symbol", as_dict=True).items()}
    t_by = {k[0]: d for k, d in trades.partition_by("symbol", as_dict=True).items()}

    rows: list[dict] = []
    for arch, (pop, T, expected_et) in ARCHITECTURES.items():
        snap = snapshot_of(rec, pop, T)
        if snap is None:
            continue
        for nm in snap["names"][:3]:
            ticker = nm["ticker"]
            fl = nm.get("fill")
            blocked = not fl or bool(fl.get("blocked"))
            entry_et = int(fl["et"]) if fl else None
            entry_px = float(fl["px"]) if fl else None
            gap_min = int(fl.get("gap_min", 0)) if fl else 0
            cand = EntryCandidate(arch, day, ticker, expected_et, entry_et, entry_px,
                                  blocked, gap_min)
            base = {
                "day": day, "architecture": arch, "ticker": ticker,
                "rank": int(nm.get("rank", 0)), "expected_et": expected_et,
                "entry_et": entry_et, "entry_px": entry_px, "blocked": blocked,
                "gap_minutes": gap_min,
                "late": (not blocked) and (entry_et is not None and entry_et > expected_et),
            }
            if blocked:
                rows.append({**base, "pre_quote_covered": False, "post_quote_covered": False,
                             "spread_bps": None, "half_spread_bps": None,
                             "next_print_covered": False, "next_print_slippage_bps": None,
                             "adverse_slippage_bps": None, "halt_like_gap_count": 0,
                             "raw_rth_dv": 0.0, "bar_dv_proxy": 0.0, "entry_min_dv": 0.0,
                             "dv_ratio": None})
                continue
            assert entry_et is not None and entry_px is not None
            obs = observe_entry(cand, q_by.get(ticker, quotes.limit(0)),
                                t_by.get(ticker, trades.limit(0)), bars)
            tq = t_by.get(ticker, trades.limit(0))
            rt = tq.filter((pl.col("ts_utc") >= rth_lo) & (pl.col("ts_utc") <= rth_hi))
            raw_rth_dv = float((rt["price"] * rt["size"]).sum()) if rt.height else 0.0
            em_lo = entry_timestamp(day, entry_et)
            em = tq.filter((pl.col("ts_utc") >= em_lo) &
                           (pl.col("ts_utc") < em_lo + timedelta(minutes=1)))
            entry_min_dv = float((em["price"] * em["size"]).sum()) if em.height else 0.0
            tb = bars.filter(pl.col("ticker") == ticker)
            bar_dv_proxy = float((tb["close"] * tb["volume"]).sum()) if tb.height else 0.0
            slip = obs.next_print_slippage_bps
            rows.append({**base,
                         **asdict(obs),
                         "adverse_slippage_bps": max(0.0, slip) if slip is not None else None,
                         "raw_rth_dv": raw_rth_dv, "bar_dv_proxy": bar_dv_proxy,
                         "entry_min_dv": entry_min_dv,
                         "dv_ratio": (raw_rth_dv / bar_dv_proxy) if bar_dv_proxy else None})
    return rows


def _quantiles(vals: list[float]) -> dict[str, float]:
    if not vals:
        return {"n": 0.0}
    import numpy as np
    a = np.asarray(vals, dtype=np.float64)
    out: dict[str, float] = {"n": float(len(a))}
    for q in (10, 25, 50, 75, 90, 95, 99):
        out[f"p{q}"] = float(np.percentile(a, q))
    return out


def _arch_quantiles(rows: list[dict], field: str) -> dict:
    return {a: _quantiles([r[field] for r in rows if r["architecture"] == a
                           and r.get(field) is not None])
            for a in ARCHITECTURES}


def finalize(out: Path, rows: list[dict]) -> dict:
    entries = pl.DataFrame(rows)
    entries.write_parquet(out / "pilot_entries.parquet")

    daily = (entries.group_by(["day", "architecture"])
             .agg([pl.len().alias("n_candidates"),
                   pl.col("blocked").sum().alias("n_blocked"),
                   pl.col("pre_quote_covered").sum().alias("n_pre_quote_covered"),
                   pl.col("next_print_covered").sum().alias("n_next_print_covered"),
                   pl.col("raw_rth_dv").sum().alias("raw_rth_dv"),
                   pl.col("bar_dv_proxy").sum().alias("bar_dv_proxy")])
             .sort(["day", "architecture"]))
    daily.write_parquet(out / "pilot_daily.parquet")

    nb = [r for r in rows if not r["blocked"]]
    spread_all = [r["spread_bps"] for r in nb if r["spread_bps"] is not None]
    slip_signed = [r["next_print_slippage_bps"] for r in nb
                   if r["next_print_slippage_bps"] is not None]
    slip_adv = [max(0.0, s) for s in slip_signed]
    tables = {
        "spread_bps": _quantiles(spread_all),
        "spread_bps_by_architecture": _arch_quantiles(nb, "spread_bps"),
        "half_spread_bps": _quantiles([s / 2.0 for s in spread_all]),
        "next_print_slippage_bps_signed": _quantiles(slip_signed),
        "next_print_slippage_bps_adverse": _quantiles(slip_adv),
    }
    (out / "spread_slippage_tables.json").write_text(json.dumps(tables, indent=1))

    coverage = {
        "n_paired_days": len({r["day"] for r in rows}),
        "paired_days": sorted({r["day"] for r in rows}),
        "candidates_total": len(rows),
        "candidates_non_blocked": len(nb),
        "pre_quote_covered": sum(r["pre_quote_covered"] for r in nb),
        "post_quote_covered": sum(r["post_quote_covered"] for r in nb),
        "next_print_covered": sum(r["next_print_covered"] for r in nb),
        "halt_like_gaps": sum(r["halt_like_gap_count"] for r in nb),
        "by_architecture": {
            a: {
                "candidates_total": sum(1 for r in rows if r["architecture"] == a),
                "candidates_non_blocked": sum(1 for r in nb if r["architecture"] == a),
                "pre_quote_covered": sum(r["pre_quote_covered"] for r in nb
                                        if r["architecture"] == a),
                "next_print_covered": sum(r["next_print_covered"] for r in nb
                                          if r["architecture"] == a),
            } for a in ARCHITECTURES
        },
        "symbols": sorted({r["ticker"] for r in rows}),
    }
    (out / "pilot_coverage.json").write_text(json.dumps(coverage, indent=1))

    summary = {
        "evidence_label": "PILOT",
        "scope": "20 paired raw SIP sessions (quotes+trades) intersected with canonical dev days; NOT 1066-day coverage",
        "n_paired_days": coverage["n_paired_days"],
        "candidates_total": coverage["candidates_total"],
        "candidates_non_blocked": coverage["candidates_non_blocked"],
        "pre_quote_covered_share": coverage["pre_quote_covered"] / max(1, len(nb)),
        "next_print_covered_share": coverage["next_print_covered"] / max(1, len(nb)),
        "spread_bps_median": tables["spread_bps"].get("p50"),
        "half_spread_bps_median": tables["half_spread_bps"].get("p50"),
        "next_print_slippage_bps_adverse_median": tables["next_print_slippage_bps_adverse"].get("p50"),
    }
    (out / "pilot_summary.json").write_text(json.dumps(summary, indent=1))
    return {"entries": entries.height, "daily": daily.height, "coverage": coverage}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "factory" / "artifacts" / "basket" / "phase2" / "F11_exec"))
    ap.add_argument("--smoke", action="store_true", help="only the first 5 paired days")
    ap.add_argument("--max-days", type=int, default=None)
    ap.add_argument("--workers", type=int, default=1, help="accepted for parity; single-process")
    a = ap.parse_args(argv)

    out = Path(a.out)
    if a.smoke:
        out = out / "smoke" / "pilot"
    out.mkdir(parents=True, exist_ok=True)
    days = paired_days()
    if a.smoke:
        days = days[:5]
    elif a.max_days:
        days = days[: a.max_days]
    sem = session_end_map()
    prog_path = out / "_pilot_progress.json"
    done = set(json.loads(prog_path.read_text()).get("done", [])) if prog_path.exists() else set()
    rows: list[dict] = []
    for d in days:
        if d in done:
            with (out / "parts" / "entries" / f"{d}.jsonl").open() as fh:
                rows.extend(json.loads(l) for l in fh if l.strip())
            continue
        day_rows = process_day(d, sem)
        part = out / "parts" / "entries" / f"{d}.jsonl"
        part.parent.mkdir(parents=True, exist_ok=True)
        tmp = part.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(json.dumps(r) + "\n" for r in day_rows))
        tmp.replace(part)
        rows.extend(day_rows)
        done.add(d)
        prog_path.write_text(json.dumps({"done": sorted(done)}, indent=1))
        print(f"  {d}: {len(day_rows)} entries (total {len(rows)})", flush=True)
    res = finalize(out, rows)
    print(f"pilot done: {res['entries']} entries, {res['daily']} daily rows, "
          f"paired={res['coverage']['n_paired_days']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
