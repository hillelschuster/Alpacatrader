#!/usr/bin/env python3
"""PRE-REG-DEPTH-UNIVERSE-01 - widen the scored universe for the frozen depth rule.

Registers ONE variable against the frozen PRE-REG-DEPTH-01 rule
(``alpha_adaptive_depth_sizing.py`` arm ``thr300bps|p50``): the rank cutoff ``K`` on the
same open-anchored gain ranking (``score = px_T / o570 - 1``) and the same 0.05 admission
gate. ``K = 10`` is the control and MUST reproduce the frozen numbers bit-for-bit before
any ``K > 10`` cell is read.

Stages (each resumable, per-day atomic artifacts):
  stage0  free re-rank from the stored full-market RTH universe table
          (``data/sip/universe/rth/<day>.parquet``): exact rank lists 1..30 at the six
          decision snapshots, the admitted set per K and the EXACT extra (day, ticker)
          pairs. No network, no new bytes.
  stage1  minute bars for the extra names: the day's existing net tape wins (same source
          the K=10 panel uses); names absent from it are fetched once per (day, batch)
          from the SIP 1-minute bars endpoint into this study's own bars archive.
  stage2  NBBO quotes for the extra pairs, reusing ``alpha_quote_universe_acquire``
          mechanics verbatim (per-(day,ticker) resume, full pagination, 429-aware GET,
          PRIMARY_KEEPFIRST merge) driven from this module's own plan.
  stage3  widened states: the PRIMARY additive arm (stored K=10 rows bit-identical, extra
          names scored with ``rank_snapshot = min(rank, 11)`` and peer features computed
          over the ORIGINAL top-10 watchlist) plus the diagnostic POOLED arm and the
          diagnostic encoding B (``rank_snapshot = min(rank, K+1)``), scored with the
          frozen h60 head and replayed through the frozen execution code.
  stage4  the pre-registered selection rule applied mechanically + the full report.

Data guard: 2024-*, 2025-01-* and 2026-06..08 are refused in code (``guard_day``) before
any file or network read.

Usage:
  uv run --no-sync python factory/scripts/alpha_depth_universe.py stage0
  uv run --no-sync python factory/scripts/alpha_depth_universe.py stage1
  uv run --no-sync python factory/scripts/alpha_depth_universe.py stage2
  uv run --no-sync python factory/scripts/alpha_depth_universe.py stage3
  uv run --no-sync python factory/scripts/alpha_depth_universe.py stage4
  uv run --no-sync python factory/scripts/alpha_depth_universe.py all
  # smoke: --days 2023-05-15 (per stage, debug only)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))

import alpha_adaptive_depth_sizing as frozen  # noqa: E402
from alpha_open_learned import PANEL_ROOT, feature_matrix  # noqa: E402
from alpha_open_panel import SNAPSHOTS, allowed, symbol_rows  # noqa: E402
from alpha_open_sim import causal_liquidity  # noqa: E402
from alpha_quote_aware_frequency import (  # noqa: E402
    minute_us,
    quote_day_frame,
    symbol_quotes,
)
from alpha_sparse_daily import day_context  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data"
UNIVERSE_RTH = DATA / "sip" / "universe" / "rth"
NET_BARS = DATA / "sip" / "net" / "bars"
CALENDAR = REPO / "factory" / "artifacts" / "basket" / "sip" / "phase2_session_calendar.json"
PANEL_DAYS = PANEL_ROOT / "days"

OUT = PANEL_ROOT / "depth_universe"
PLAN = OUT / "plan"
BARS = OUT / "bars"
QUOTES = OUT / "quotes"  # wide acquisition root: quotes/<day>.parquet (supplement)
STATES = OUT / "states"
REPLAY = OUT / "replay"

K_VALUES = (10, 20, 30)
K_MAX = max(K_VALUES)
ENCODINGS = ("A", "B")
ARMS = ("additive", "pooled")
PRIMARY_ARM = "additive"
PRIMARY_ENCODING = "A"
VAL_BLOCK = "validation"
LATE_BLOCK = "confirmation"
RANKS = {"A": 11, "B": 12}  # B is re-capped per K: min(rank, K+1)
STUDY = "alpha_depth_universe"
VERSION = 1
STATUS = "DISCOVERY-NOT-VALIDATED"

# The frozen arm is re-used, never re-declared: same View, threshold, participation.
VIEW = frozen.View(0.030, 0.50)
assert VIEW.key == "thr300bps|p50"

# Supplement roots in exactly the frozen order, then this study's own wide acquisition.
SUPPLEMENT_ROOTS = tuple(frozen.SUPPLEMENT_ROOTS) + (QUOTES,)

FROZEN_REFERENCE = {
    VAL_BLOCK: {"usd_per_session_25": 1.3635270500000087, "known_fills": 53, "signals": 107},
    LATE_BLOCK: {"usd_per_session_25": 5.949121298192784, "known_fills": 122, "signals": 244},
}


# ----- data guard -------------------------------------------------------------
SEALED_PREFIXES = ("2024-", "2025-01", "2026-06", "2026-07", "2026-08")


def guard_day(day: str) -> None:
    """Refuse any sealed/out-of-scope date BEFORE any file or network read."""
    if not isinstance(day, str) or len(day) != 10:
        raise ValueError(f"bad day: {day!r}")
    if any(day.startswith(p) for p in SEALED_PREFIXES):
        raise ValueError(f"sealed date refused: {day}")
    if not allowed(day):
        raise ValueError(f"out-of-scope date refused: {day}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=1, sort_keys=False) + "\n")
    os.replace(tmp, path)


def block_of(day: str) -> str | None:
    if VAL_BLOCK == "validation" and "2023-01-01" <= day <= "2023-12-31":
        return VAL_BLOCK
    if "2025-02-01" <= day <= "2026-05-31":
        return LATE_BLOCK
    return None


def study_days() -> dict[str, list[str]]:
    """The two research blocks: every allowed panel session, nothing else."""
    days = []
    for path in sorted(PANEL_DAYS.glob("????-??-??.parquet")):
        day = path.stem
        if block_of(day) is None:
            continue
        guard_day(day)
        days.append(day)
    return {
        VAL_BLOCK: [d for d in days if block_of(d) == VAL_BLOCK],
        LATE_BLOCK: [d for d in days if block_of(d) == LATE_BLOCK],
    }


def session_ends(days: list[str]) -> dict[str, int]:
    cal = json.loads(CALENDAR.read_text())
    return {d: int(cal["evidence"][d]["session_end"]) for d in days}


def selected_days(args) -> dict[str, list[str]]:
    """The requested research blocks (``--blocks``), optionally restricted by ``--days``."""
    blocks = study_days()
    wanted = {b.strip() for b in str(getattr(args, "blocks", "validation,confirmation")).split(",")}
    keep = set(args.days) if getattr(args, "days", None) else None
    return {
        b: [d for d in v if (b in wanted and (keep is None or d in keep))]
        for b, v in blocks.items()
    }


# ----- stage 0: free re-rank of the stored full-market table -------------------
def rank_day(day: str, session_end: int) -> dict:
    """Rank the full PIT universe at every decision snapshot, ranks 1..K_MAX.

    The ranking rule is ``sip_candidates.snapshot`` B populations verbatim: eligible rows
    need ``o570 > 0`` and ``px_T >= $1``; ``score = px_T / o570 - 1``; sort by score desc
    then symbol asc. Only the cutoff is widened (K_TOP=10 -> 30).
    """
    guard_day(day)
    path = UNIVERSE_RTH / f"{day}.parquet"
    if not path.exists():
        raise ValueError(f"universe table missing: {path}")
    frame = pl.read_parquet(path)
    snaps: dict[str, dict] = {}
    admitted: dict[int, dict[str, dict]] = {k: {} for k in K_VALUES}
    for snap_t in SNAPSHOTS:
        pxc, pxe = f"px_{snap_t}", f"px_{snap_t}_et"
        eligible = frame.filter((pl.col("o570") > 0) & (pl.col(pxc) >= 1.0))
        ranked = (
            eligible.with_columns((pl.col(pxc) / pl.col("o570") - 1).alias("score"))
            .sort(["score", "symbol"], descending=[True, False])
            .head(K_MAX)
        )
        top = ranked.select(["symbol", "score", pxc, pxe]).to_dicts()
        for i, row in enumerate(top, start=1):
            row["rank"] = i
            et = row.pop(pxe)
            row.pop(pxc)
            row["fresh_px"] = bool(et is not None and int(et) >= snap_t - 2)
            row["admitted"] = bool(row["score"] >= 0.05 and row["fresh_px"])
        snaps[str(snap_t)] = {
            "n_eligible": int(eligible.height),
            "top": top,
            "n_admitted": sum(1 for r in top if r["admitted"]),
        }
        for k in K_VALUES:
            for row in top[:k]:
                if not row["admitted"]:
                    continue
                cur = admitted[k].get(row["symbol"])
                if cur is None or int(snap_t) < cur["admit_t"]:
                    admitted[k][row["symbol"]] = {
                        "admit_t": int(snap_t),
                        "admit_gain": float(row["score"]),
                        "rank_at_admit": int(row["rank"]),
                    }
    sets = {k: set(admitted[k]) for k in K_VALUES}
    assert sets[10] <= sets[20] <= sets[30], f"nested admission violated: {day}"
    extra = {k: sorted(sets[k] - sets[10]) for k in (20, 30)}
    return {
        "day": day,
        "schema": 1,
        "session_end": int(session_end),
        "rows_in": int(frame.height),
        "snapshots": snaps,
        "admitted": {str(k): admitted[k] for k in K_VALUES},
        "admitted_sets": {str(k): sorted(sets[k]) for k in K_VALUES},
        "extra": {str(k): extra[k] for k in (20, 30)},
        "extra_rank_only": {
            "20": sorted(set(extra[20])),
            "30": sorted(set(extra[30]) - set(extra[20])),
        },
        "note": "ranking only, no bars, no quotes, no labels",
        "source": str(path.relative_to(REPO)),
        "source_sha256": sha256_file(path),
    }


def _rank_task(task: tuple[str, int]) -> dict:
    """Picklable worker: one day's re-rank."""
    return rank_day(*task)


def stage0(args) -> None:
    blocks = study_days()
    days = blocks[VAL_BLOCK] + blocks[LATE_BLOCK]
    if args.days:
        days = [d for d in days if d in set(args.days)]
    ends = session_ends(days)
    tasks = [(d, ends[d]) for d in days]
    PLAN.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    todo = [t for t in tasks if args.force or not (PLAN / f"{t[0]}.json").exists()]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for n, rec in enumerate(pool.map(_rank_task, todo), 1):
            write_json(PLAN / f"{rec['day']}.json", rec)
            if n % 25 == 0 or args.days:
                print(f"[stage0] {n}/{len(todo)} {rec['day']}", flush=True)
    summary = summarize_plan(days)
    write_json(OUT / "plan_summary.json", summary)
    print(
        f"[stage0] {len(days)} days in {time.time() - t0:.1f}s -> {OUT / 'plan_summary.json'}",
        flush=True,
    )
    print(json.dumps(summary["totals"], indent=1), flush=True)


def load_plan(day: str) -> dict:
    return json.loads((PLAN / f"{day}.json").read_text())


def summarize_plan(days: list[str]) -> dict:
    per_block = {VAL_BLOCK: [], LATE_BLOCK: []}
    totals = {
        "days": 0,
        "pairs_20": 0,
        "pairs_30": 0,
        "ranks_11_20": 0,
        "ranks_21_30": 0,
        "extra_pairs_with_net_tape": {"20": 0, "30": 0},
    }
    per_day = []
    for day in days:
        rec = load_plan(day)
        per_block[block_of(day)].append(rec)
        e20, e30 = rec["extra"]["20"], rec["extra"]["30"]
        totals["days"] += 1
        totals["pairs_20"] += len(e20)
        totals["pairs_30"] += len(e30)
        totals["ranks_11_20"] += len(e20)
        totals["ranks_21_30"] += len(set(e30) - set(e20))
        per_day.append(
            {
                "day": day,
                "block": block_of(day),
                "admitted_10": len(rec["admitted_sets"]["10"]),
                "admitted_20": len(rec["admitted_sets"]["20"]),
                "admitted_30": len(rec["admitted_sets"]["30"]),
                "extra_20": len(e20),
                "extra_30": len(e30),
                "extra_names_30": e30,
            }
        )
    out = {
        "study": STUDY,
        "stage": "stage0_rerank",
        "status": STATUS,
        "variable": "rank cutoff K in {10,20,30}; score = px_T/o570-1; gate >= 0.05",
        "blocks": {
            b: {"sessions": len(v), "span": [v[0]["day"], v[-1]["day"]]}
            for b, v in per_block.items()
        },
        "totals": totals,
        "per_day": per_day,
        "producer_sha256": sha256_file(Path(__file__)),
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    return out


# ----- control check: the K=10 re-rank must reproduce the frozen candidate top-10 -----
def verify_ranks(days: list[str]) -> dict:
    """Bit-exact audit of stage 0 against ``data/sip/candidates`` B snapshots.

    Every clock, every rank: the (symbol, rank) list must equal the frozen file's top ten.
    """
    bad, checked = [], 0
    for day in days:
        rec = load_plan(day)
        cand = json.loads((REPO / "data" / "sip" / "candidates" / f"{day}.json").read_text())
        frozen_top = {
            s["T"]: [(r["symbol"], r["rank"]) for r in s["top"]]
            for s in cand["snapshots"]
            if s["pop"] == "B" and s["T"] in SNAPSHOTS
        }
        for snap_t in SNAPSHOTS:
            mine = [(r["symbol"], r["rank"]) for r in rec["snapshots"][str(snap_t)]["top"][:10]]
            checked += 1
            if mine != frozen_top.get(snap_t):
                bad.append(
                    {
                        "day": day,
                        "T": snap_t,
                        "mine": mine[:5],
                        "frozen": (frozen_top.get(snap_t) or [])[:5],
                    }
                )
    return {"clocks_checked": checked, "mismatches": len(bad), "examples": bad[:10]}


def verify_admission(days: list[str]) -> dict:
    """The K=10 admitted set must equal the frozen candidate admission set.

    The control is the admission rule of ``alpha_open_panel.build_day`` applied to the
    stored candidate JSON (top-10 B rows passing score>=0.05, px>=1, px_et>=T-2). The
    panel's *ticker* set is compared too; a difference there is a missing-tape fact, not a
    ranking fact, and is reported separately.
    """
    cand_bad, tape_bad = [], []
    for day in days:
        rec = load_plan(day)
        cand = json.loads((REPO / "data" / "sip" / "candidates" / f"{day}.json").read_text())
        frozen_admit = set()
        for snap in cand["snapshots"]:
            if snap["pop"] != "B" or snap["T"] not in SNAPSHOTS:
                continue
            snap_t = snap["T"]
            for r in snap["top"]:
                if (
                    r["score"] >= 0.05
                    and r.get(f"px_{snap_t}", 0) >= 1
                    and r.get(f"px_{snap_t}_et", -1) >= snap_t - 2
                ):
                    frozen_admit.add(r["symbol"])
        mine = set(rec["admitted_sets"]["10"])
        if mine != frozen_admit:
            cand_bad.append(
                {
                    "day": day,
                    "missing": sorted(frozen_admit - mine),
                    "extra": sorted(mine - frozen_admit),
                }
            )
        panel = set(
            pl.read_parquet(PANEL_DAYS / f"{day}.parquet", columns=["ticker"])["ticker"].unique()
        )
        if panel != mine:
            tape_bad.append(
                {"day": day, "no_tape": sorted(mine - panel), "unexpected": sorted(panel - mine)}
            )
    return {
        "days_checked": len(days),
        "admission_mismatches": len(cand_bad),
        "admission_examples": cand_bad[:10],
        "panel_ticker_differences": len(tape_bad),
        "panel_ticker_examples": tape_bad[:10],
        "panel_ticker_note": "a ticker admitted by the rule but absent from the panel day "
        "file has no 09:30-anchored tape; it is never a ranking difference",
    }


def stage0_verify(args) -> None:
    blocks = study_days()
    days = blocks[VAL_BLOCK] + blocks[LATE_BLOCK]
    if args.days:
        days = [d for d in days if d in set(args.days)]
    out = {
        "ranks": verify_ranks(days),
        "admission": verify_admission(days),
    }
    write_json(OUT / "stage0_verify.json", out)
    print(json.dumps(out, indent=1)[:4000], flush=True)


# ----- stage 1: minute bars for the extra names -------------------------------
def extra_symbols(day: str) -> list[str]:
    rec = load_plan(day)
    return rec["extra"]["30"]


def net_tape_tickers(day: str) -> set[str]:
    path = NET_BARS / f"{day}.parquet"
    if not path.exists():
        return set()
    return set(pl.read_parquet(path, columns=["ticker"])["ticker"].unique())


def bars_day(day: str) -> pl.DataFrame:
    """Provider minute bars acquired for this study (long frame, RTH only)."""
    path = BARS / f"{day}.parquet"
    if not path.exists():
        return pl.DataFrame(
            schema={
                "ticker": pl.String,
                "et": pl.Int64,
                "open": pl.Float64,
                "high": pl.Float64,
                "low": pl.Float64,
                "close": pl.Float64,
                "volume": pl.Float64,
            }
        )
    return pl.read_parquet(path)


def day_tape(day: str, names: set[str]) -> pl.DataFrame:
    """The day's trade-derived tape first (the K=10 panel source), provider bars second.

    A name present in the net tape is served from it, so the widened panel inherits the
    same bar source the frozen panel used wherever that source exists. Only names absent
    from the net tape can ever come from this study's provider bars archive.
    """
    net = pl.read_parquet(
        NET_BARS / f"{day}.parquet",
        columns=["ticker", "et", "open", "high", "low", "close", "volume"],
    ).filter(pl.col("ticker").is_in(list(names)))
    covered = set(net["ticker"].unique())
    todo = names - covered
    if not todo:
        return net.sort(["ticker", "et"])
    prov = bars_day(day)
    if prov.height:
        prov = prov.filter(pl.col("ticker").is_in(list(todo))).select(net.columns)
        return pl.concat([net, prov]).sort(["ticker", "et"])
    return net.sort(["ticker", "et"])


def stage1(args) -> None:
    """Fetch SIP minute bars for the extra names that the net tape does not already hold."""
    if not args.allow_network:
        raise SystemExit(
            "[stage1] acquisition is gated: re-run with --allow-network after the host-disk "
            "stop is lifted (see local://UniverseExpansionRun.md)"
        )
    blocks = selected_days(args)
    days = blocks[VAL_BLOCK] + blocks[LATE_BLOCK]
    if not days:
        raise SystemExit("[stage1] no day selected")
    from dotenv import load_dotenv

    load_dotenv(REPO / ".env")
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = StockHistoricalDataClient(
        os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"]
    )
    BARS.mkdir(parents=True, exist_ok=True)
    done, requests = 0, 0
    for n, day in enumerate(days, 1):
        guard_day(day)
        dest = BARS / f"{day}.parquet"
        if dest.exists() and not args.force:
            continue
        need = sorted(set(extra_symbols(day)) - net_tape_tickers(day))
        need = [s for s in need if "^" not in s and "/" not in s]
        if not need:
            _write_empty_bars(day)
            continue
        rows = []
        for i in range(0, len(need), 500):
            batch = need[i : i + 500]
            req = StockBarsRequest(
                symbol_or_symbols=batch,
                timeframe=TimeFrame.Minute,
                start=_et_us(day, 565),
                end=_et_us(day, 962),
                feed=DataFeed.SIP,
                adjustment=Adjustment.RAW,
            )
            res = client.get_stock_bars(req)
            requests += 1
            for symbol, bars in res.data.items():
                for b in bars:
                    et = _minute_of(b.timestamp)
                    if not (570 <= et < 960):
                        continue
                    rows.append(
                        {
                            "ticker": symbol,
                            "et": int(et),
                            "open": float(b.open),
                            "high": float(b.high),
                            "low": float(b.low),
                            "close": float(b.close),
                            "volume": float(b.volume or 0.0),
                        }
                    )
            time.sleep(0.4)
        frame = pl.DataFrame(rows) if rows else bars_day(day)
        tmp = dest.with_name(f"{dest.name}.tmp{os.getpid()}")
        frame.write_parquet(tmp)
        os.replace(tmp, dest)
        done += 1
        print(
            f"[stage1] {n}/{len(days)} {day} names={len(need)} rows={frame.height} "
            f"requests={requests}",
            flush=True,
        )
    write_json(
        OUT / "stage1_summary.json",
        {
            "study": STUDY,
            "stage": "stage1_bars",
            "days_present": len(list(BARS.glob("????-??-??.parquet"))),
            "days_fetched_this_run": done,
            "requests": requests,
            "producer_sha256": sha256_file(Path(__file__)),
        },
    )


def _write_empty_bars(day: str) -> None:
    path = BARS / f"{day}.parquet"
    if path.exists():
        return
    BARS.mkdir(parents=True, exist_ok=True)
    bars_day(day).write_parquet(path)


def _et_us(day: str, minute: int) -> int:
    from alpha_quote_audit import clock_us

    return clock_us(day, int(minute), 0)


def _minute_of(ts) -> int:
    from zoneinfo import ZoneInfo

    local = ts.astimezone(ZoneInfo("America/New_York"))
    return local.hour * 60 + local.minute


# ----- stage 2: NBBO quotes for the extra pairs --------------------------------
def stage2(args) -> None:
    """Acquire quotes for the extra pairs, reusing the proven acquisition mechanics.

    The plan is this study's own (stage-0 extras minus every symbol already covered by the
    ranked cache or a supplement), the mechanism is ``alpha_quote_universe_acquire``'s own
    resumable per-(day,ticker) fetch and PRIMARY_KEEPFIRST merge - imported, never copied.
    """
    if not args.allow_network:
        raise SystemExit(
            "[stage2] acquisition is gated: re-run with --allow-network after the host-disk "
            "stop is lifted"
        )
    import alpha_quote_universe_acquire as uqa
    import requests
    from alpha_quote_audit import clock_us

    blocks = selected_days(args)
    days = blocks[VAL_BLOCK] + blocks[LATE_BLOCK]
    if not days:
        raise SystemExit("[stage2] no day selected")
    QUOTES.mkdir(parents=True, exist_ok=True)
    supplemental = tuple(list(uqa.DEFAULT_EXISTING_ROOTS) + list(SUPPLEMENT_ROOTS))
    headers = uqa._alpaca_headers(args.env_path)
    if headers is None:
        raise SystemExit("[stage2] credentials not found in .env; nothing fetched")
    session = requests.Session()
    total_pairs = 0
    for n, day in enumerate(days, 1):
        guard_day(day)
        plan_rec = load_plan(day)
        need = sorted(plan_rec["extra"]["30"])
        if not need:
            continue
        have = uqa.existing_symbols(day, DATA, supplemental)
        missing = [t for t in need if t not in have]
        missing = [t for t in missing if "^" not in t and "/" not in t]
        if not missing:
            continue
        manifest_p = uqa.manifest_path(QUOTES, day)
        manifest = uqa.load_manifest(QUOTES, day) or {}
        records = {r["ticker"]: r for r in manifest.get("records", [])}
        session_end = int(plan_rec["session_end"])
        plan = uqa.DayPlan(
            day=day,
            qualified=sorted(need),
            missing=missing,
            session_end=session_end,
            earliest_intent_us=clock_us(day, min(int(k) for k in SNAPSHOTS) + 1, 0),
            close_us=clock_us(day, uqa.SESSION_CLOSE_MINUTE, 0),
            universe="wide",
        )
        for ticker in missing:
            prior = records.get(ticker)
            if prior and prior.get("status") not in (None, "pending"):
                continue
            rec = uqa.fetch_symbol_day(session, headers, QUOTES, plan, ticker, args.req_sleep)
            records[ticker] = rec
            total_pairs += 1
            time.sleep(args.req_sleep)
        merged = uqa.merge_day(QUOTES, plan, DATA, supplemental)
        write_json(
            manifest_p,
            {
                "day": day,
                "schema": uqa.MANIFEST_SCHEMA,
                "universe": "wide",
                "producer_sha256": sha256_file(Path(__file__)),
                "planned_before_any_quote_or_api_read": True,
                "extra_pairs_30": len(need),
                "candidates": missing,
                "records": [records[t] for t in missing if t in records],
                "merge": merged,
                "unknown_tickers": [
                    t for t in missing if records.get(t, {}).get("status", "").startswith("unknown")
                ],
            },
        )
        print(f"[stage2] {n}/{len(days)} {day} missing={len(missing)} merge={merged}", flush=True)
    write_json(
        OUT / "stage2_summary.json",
        {
            "study": STUDY,
            "stage": "stage2_quotes",
            "pairs_fetched_this_run": total_pairs,
            "output_root": str(QUOTES),
            "producer_sha256": sha256_file(Path(__file__)),
        },
    )


# ----- stage 3: widened states (primary additive arm + diagnostics) -------------
def _empty_panel() -> pl.DataFrame:
    from alpha_open_panel import FEATURES

    schema = {"day": pl.String, "ticker": pl.String, "t": pl.Int64}
    schema.update({f: pl.Float64 for f in FEATURES if f != "minute"})
    schema.update(
        {
            "minute": pl.Float64,
            "entry_et": pl.Int64,
            "entry_open": pl.Float64,
            "entry_status": pl.String,
            "session_end": pl.Int64,
            "admit_t": pl.Int64,
            "admit_gain": pl.Float64,
            "gross_15": pl.Float64,
            "gross_60": pl.Float64,
            "gross_390": pl.Float64,
            "exit_et_15": pl.Int64,
            "exit_et_60": pl.Int64,
            "exit_et_390": pl.Int64,
            "exit_status_15": pl.String,
            "exit_status_60": pl.String,
            "exit_status_390": pl.String,
        }
    )
    return pl.DataFrame(schema=schema)


def rank_map(plan: dict) -> dict[int, dict[str, int]]:
    return {
        int(snap_t): {r["symbol"]: int(r["rank"]) for r in plan["snapshots"][str(snap_t)]["top"]}
        for snap_t in SNAPSHOTS
    }


def widen_panel(
    day: str, plan: dict, k: int, arm: str, session_end: int
) -> tuple[pl.DataFrame, dict]:
    """One day's widened panel frame WITH peer context, per the registered arm.

    ``additive``: only the extra names (rank 11..K, absent from the K=10 admitted set) are
    built; their peer context is JOINED from the K=10 panel's own context at (day, t), so
    the context is exactly the one the original rows see (the extra rows never enter the
    peer pool). ``pooled``: every admitted name is rebuilt under the widened admission and
    the peer context is recomputed over the widened watchlist.
    """
    ranks = rank_map(plan)
    admitted = plan["admitted"][str(k)]
    k10 = set(plan["admitted_sets"]["10"])
    names = sorted(set(admitted) - k10) if arm == "additive" else sorted(admitted)
    tape = day_tape(day, set(names))
    have = set(tape["ticker"].unique()) if tape.height else set()
    rows = []
    for key, frame in tape.partition_by("ticker", as_dict=True).items() if tape.height else []:
        ticker = key[0]
        info = admitted[ticker]
        rows.extend(
            symbol_rows(
                day,
                ticker,
                frame.sort("et"),
                [(int(info["admit_t"]), float(info["admit_gain"]))],
                ranks,
                session_end,
            )
        )
    frame = pl.DataFrame(rows) if rows else _empty_panel()
    info_out = {
        "day": day,
        "K": k,
        "arm": arm,
        "names": len(names),
        "names_with_tape": len(have),
        "names_without_tape": sorted(set(names) - have),
        "rows": int(frame.height),
    }
    if not frame.height:
        info_out["context_null_rows"] = 0
        return frame, info_out
    if arm == "pooled":
        frame = day_context(frame)
        info_out["context_null_rows"] = 0
    else:
        ctx = (
            day_context(pl.read_parquet(PANEL_DAYS / f"{day}.parquet"))
            .select(["day", "t", "peer_positive3", "peer_gain_mean", "peer_breadth10", "peer_heat"])
            .unique(subset=["day", "t"])
        )
        frame = frame.join(ctx, on=["day", "t"], how="left")
        info_out["context_null_rows"] = int(frame["peer_positive3"].null_count())
    return frame, info_out


def encode_rank(frame: pl.DataFrame, enc: str, k: int) -> pl.DataFrame:
    cap = 11 if enc == "A" else k + 1
    return frame.with_columns(
        pl.min_horizontal(pl.col("rank_snapshot"), pl.lit(float(cap))).alias("rank_snapshot")
    )


def score_panel(
    day: str, frame: pl.DataFrame, streams: dict, model: dict
) -> tuple[pl.DataFrame, dict]:
    """Qualify, score with the frozen head and observe each intent - ``score_day`` verbatim."""
    if not frame.height:
        return frozen.empty_states(), {"panel_rows": 0, "qualified_rows": 0}
    cand = frame.filter(causal_liquidity(frame))
    info = {"panel_rows": int(frame.height), "qualified_rows": int(cand.height)}
    if not cand.height:
        return frozen.empty_states(), info
    preds = np.asarray(model[frozen.HEAD]["lgbm"].predict(feature_matrix(cand)), dtype=float)
    rows = cand.select(
        [
            "day",
            "ticker",
            "t",
            "admit_t",
            "entry_et",
            "entry_open",
            "entry_status",
            "session_end",
            "gross_60",
            "rank_snapshot",
        ]
    ).to_dicts()
    session_end = int(rows[0]["session_end"])
    out = []
    for i, r in enumerate(rows):
        entry_minute = int(r["t"]) + 1
        intent_us = minute_us(day, entry_minute)
        facts = frozen.intent_facts(streams.get(r["ticker"]), intent_us)
        rec = {
            "day": day,
            "ticker": r["ticker"],
            "t": int(r["t"]),
            "admit_t": int(r["admit_t"]) if r["admit_t"] is not None else None,
            "entry_et": int(r["entry_et"]) if r["entry_et"] is not None else None,
            "entry_open": r["entry_open"],
            "entry_status": r["entry_status"],
            "session_end": session_end,
            "proxy_gross_60": r["gross_60"],
            "pred_60": float(preds[i]),
            "entry_minute": entry_minute,
            "intent_us": intent_us,
        }
        rec.update(facts)
        out.append(rec)
    states = pl.DataFrame(
        {c: [rec[c] for rec in out] for c in frozen.STATE_COLUMNS},
        schema={c: frozen.STATE_TYPES[c] for c in frozen.STATE_COLUMNS},
    )
    info["rank_snapshot_values"] = {
        str(k): int(v)
        for k, v in zip(
            *np.unique(cand["rank_snapshot"].to_numpy(), return_counts=True), strict=True
        )
    }
    return states, info


CELL_SPECS = (
    ("k10_control", 10, "additive", "A"),  # == the stored frozen states, bit-identical
    ("k10_pooled_rebuild", 10, "pooled", "A"),
    ("k20_add_A", 20, "additive", "A"),
    ("k20_add_B", 20, "additive", "B"),
    ("k20_pool_A", 20, "pooled", "A"),
    ("k20_pool_B", 20, "pooled", "B"),
    ("k30_add_A", 30, "additive", "A"),
    ("k30_add_B", 30, "additive", "B"),
    ("k30_pool_A", 30, "pooled", "A"),
    ("k30_pool_B", 30, "pooled", "B"),
)


def stage3(args) -> None:
    """Build every cell's states and replay them through the frozen execution code."""
    blocks = selected_days(args)
    days = blocks[VAL_BLOCK] + blocks[LATE_BLOCK]
    if not days:
        raise SystemExit("[stage3] no day selected")
    model, _ = frozen.load_stored_model()
    cells = [c for c in CELL_SPECS if args.cells is None or c[0] in set(args.cells)]
    acc = {c[0]: {"daily": [], "trades": [], "coverage": []} for c in cells}
    t0 = time.time()
    for n, day in enumerate(days, 1):
        guard_day(day)
        plan = load_plan(day)
        session_end = int(plan["session_end"])
        stored = pl.read_parquet(frozen.OUTPUT / "states" / f"{day}.parquet")
        frames: dict[tuple[int, str], pl.DataFrame] = {}
        infos: dict[tuple[int, str], dict] = {}
        for k, arm in sorted({(c[1], c[2]) for c in cells if c[1] > 10}):
            frame, info = widen_panel(day, plan, k, arm, session_end)
            frames[(k, arm)] = frame
            infos[(k, arm)] = info
        if any(c[1] == 10 and c[2] == "pooled" for c in cells):
            frame, info = widen_panel(day, plan, 10, "pooled", session_end)
            frames[(10, "pooled")] = frame
            infos[(10, "pooled")] = info
        need = set(stored["ticker"].unique()) if stored.height else set()
        for frame in frames.values():
            if frame.height:
                need |= set(frame["ticker"].unique())
        qf = quote_day_frame(DATA, day, need, SUPPLEMENT_ROOTS) if need else None
        streams = symbol_quotes(qf, day)
        del qf
        for name, k, arm, enc in cells:
            if k == 10 and arm == "additive":
                states = stored
                info = {"panel_rows": int(stored.height), "source": "stored_frozen_states"}
            else:
                frame = frames[(k, arm)]
                info = dict(infos[(k, arm)])
                states, sinfo = score_panel(day, encode_rank(frame, enc, k), streams, model)
                info.update(sinfo)
                if k == 10:
                    info["control_state_match"] = compare_states(stored, states)
                if k > 10 and arm == "additive":
                    states = pl.concat([stored, states], how="vertical")
                    info["combined_rows"] = int(states.height)
            daily, trades = frozen.replay_day(day, states, streams, VIEW)
            for t in trades:
                t["cell"] = name
            acc[name]["daily"].append(daily)
            acc[name]["trades"].extend(trades)
            acc[name]["coverage"].append(info)
        if n % 10 == 0:
            print(f"[stage3] {n}/{len(days)} {day} ({time.time() - t0:.0f}s)", flush=True)
    out_root = REPLAY
    out_root.mkdir(parents=True, exist_ok=True)
    summary = {
        "study": STUDY,
        "stage": "stage3_cells",
        "status": STATUS,
        "blocks": {},
        "cells": {},
        "runtime_s": round(time.time() - t0, 1),
        "producer_sha256": sha256_file(Path(__file__)),
    }
    for name, k, arm, enc in cells:
        daily_frame = _frame_from_records(acc[name]["daily"], frozen.DAILY_TYPES)
        trade_frame = _frame_from_records(acc[name]["trades"], frozen.TRADE_TYPES)
        if daily_frame.height:
            daily_frame.write_parquet(out_root / f"{name}.daily.parquet")
            trade_frame.write_parquet(out_root / f"{name}.trades.parquet")
        summary["cells"][name] = {
            "K": k,
            "arm": arm,
            "encoding": enc,
            "primary": bool(
                name == "k10_control" or (arm == PRIMARY_ARM and enc == PRIMARY_ENCODING)
            ),
            "coverage": _coverage_totals(acc[name]["coverage"]),
        }
    write_json(out_root / "stage3_summary.json", summary)
    print(
        f"[stage3] done in {time.time() - t0:.0f}s -> {out_root / 'stage3_summary.json'}",
        flush=True,
    )


def _coverage_totals(cov: list[dict]) -> dict:
    matches = [c["control_state_match"] for c in cov if "control_state_match" in c]
    if matches:
        out_match = {
            "days_compared": len(matches),
            "days_bit_identical": sum(1 for m in matches if m.get("bit_identical")),
            "days_rows_equal": sum(1 for m in matches if m.get("rows_equal")),
            "max_abs_pred_diff": max((m.get("max_abs_pred_diff", 0.0) or 0.0) for m in matches),
            "intent_status_mismatches": sum(
                m.get("intent_status_mismatches", 0) or 0 for m in matches
            ),
            "intent_ask_mismatches": sum(m.get("intent_ask_mismatches", 0) or 0 for m in matches),
            "qty_capital_mismatches": sum(m.get("qty_capital_mismatches", 0) or 0 for m in matches),
        }
    else:
        out_match = None
    out = {
        "control_state_match": out_match,
        "days": len(cov),
        "panel_rows": sum(int(c.get("panel_rows", 0)) for c in cov),
        "qualified_rows": sum(int(c.get("qualified_rows", 0)) for c in cov),
        "extra_names": sum(int(c.get("names", 0)) for c in cov),
        "extra_names_without_tape": sum(len(c.get("names_without_tape", [])) for c in cov),
        "context_null_rows": sum(int(c.get("context_null_rows", 0)) for c in cov),
    }
    return out


# ----- harness: the K=10 control MUST reproduce the frozen rule exactly ----------------
def _frame_from_records(records: list[dict], schema: dict[str, object]) -> pl.DataFrame:
    if not records:
        return pl.DataFrame(schema=schema)
    frame = pl.DataFrame(records)
    return frame.select([pl.col(c).cast(t) for c, t in schema.items() if c in frame.columns])


def replay_day_in_memory(
    day: str, states: pl.DataFrame, supplement_roots: tuple[Path, ...]
) -> tuple[dict, list[dict]]:
    """Replay one day's states through the FROZEN execution code (no persistence)."""
    guard_day(day)
    signals = states.filter(pl.col("pred_60") >= VIEW.threshold) if states.height else states
    streams: dict = {}
    if signals.height:
        tickers = set(signals["ticker"].to_list())
        qf = quote_day_frame(DATA, day, tickers, supplement_roots)
        streams = symbol_quotes(qf, day)
        del qf
    return frozen.replay_day(day, states, streams, VIEW)


def replay_block_in_memory(
    block: str, days: list[str], states_root: Path, supplement_roots: tuple[Path, ...]
) -> dict:
    daily_rows, trade_rows = [], []
    t0 = time.time()
    for n, day in enumerate(days, 1):
        states = pl.read_parquet(states_root / f"{day}.parquet")
        daily, trades = replay_day_in_memory(day, states, supplement_roots)
        daily_rows.append(daily)
        trade_rows.extend(trades)
        if n % 50 == 0:
            print(f"[replay] {block} {n}/{len(days)} {time.time() - t0:.0f}s", flush=True)
    daily_frame = _frame_from_records(daily_rows, frozen.DAILY_TYPES)
    trade_frame = _frame_from_records(trade_rows, frozen.TRADE_TYPES)
    cell = frozen.view_cell(daily_frame, trade_frame, VIEW, days)
    known = trade_frame.filter(pl.col("fill_status") == "conditional_fill")
    net25 = known["net_usd_25"].to_list() if known.height else []
    cell["fills_median_known_usd_25"] = float(np.median(net25)) if net25 else None
    cell["top5_share_of_known_usd_25"] = (
        float(sum(sorted(net25, reverse=True)[:5]) / sum(net25)) if net25 and sum(net25) else None
    )
    cell["months_positive_25"] = {
        "positive": int(sum(v["known_usd_25"] > 0 for v in cell["monthly"].values())),
        "total": len(cell["monthly"]),
    }
    return {"cell": cell, "daily": daily_frame, "trades": trade_frame}


def harness(args) -> None:
    """Prove the frozen control on BOTH blocks before any widened cell is trusted."""
    blocks = study_days()
    out = {"study": STUDY, "stage": "harness_control", "status": STATUS, "blocks": {}}
    for block in (VAL_BLOCK, LATE_BLOCK):
        days = blocks[block]
        if args.days:
            days = [d for d in days if d in set(args.days)]
        if not days:
            continue
        res = replay_block_in_memory(block, days, frozen.OUTPUT / "states", SUPPLEMENT_ROOTS)
        cell = res["cell"]
        ref = FROZEN_REFERENCE[block]
        got = {
            "days_replayed": cell["days_replayed"],
            "signals": cell["signals"],
            "intents_funded": cell["intents_funded"],
            "known_fills": cell["known_fills"],
            "unknown_fills": cell["unknown_fills"],
            "no_match_fills": cell["no_match_fills"],
            "traded_days": cell["traded_days"],
            "usd_per_panel_session_25": cell["known_usd_per_panel_session"]["25"],
            "usd_per_calendar_day_span_25": cell["known_usd_per_calendar_day_span"]["25"],
            "median_known_fill_usd_25": cell["fills_median_known_usd_25"],
            "top5_share_known_usd_25": cell["top5_share_of_known_usd_25"],
            "months_positive_25": cell["months_positive_25"],
        }
        exact = abs(got["usd_per_panel_session_25"] - ref["usd_per_session_25"]) < 1e-9 and (
            got["known_fills"] == ref["known_fills"]
        )
        out["blocks"][block] = {"got": got, "reference": ref, "exact_match": bool(exact)}
        print(f"[harness] {block}: exact={exact} {json.dumps(got)}", flush=True)
    write_json(OUT / "harness_control.json", out)
    print(json.dumps(out["blocks"], indent=1)[:2000], flush=True)


def compare_states(stored: pl.DataFrame, rebuilt: pl.DataFrame) -> dict:
    """Bit-level control check: does the rebuilt K=10 state set equal the frozen one?"""
    if stored.height != rebuilt.height:
        return {
            "rows_equal": False,
            "stored_rows": int(stored.height),
            "rebuilt_rows": int(rebuilt.height),
        }
    key = ["ticker", "t"]
    a, b = stored.sort(key), rebuilt.sort(key)
    keys_equal = a.select(key).equals(b.select(key))
    if not keys_equal:
        return {"rows_equal": False, "keys_equal": False}
    pred = float(np.max(np.abs(a["pred_60"].to_numpy() - b["pred_60"].to_numpy())))
    status = int((a["intent_status"] != b["intent_status"]).sum())
    ask = int((a["intent_ask"].fill_null(-1) != b["intent_ask"].fill_null(-1)).sum())
    qty = int((a["qty_capital"] != b["qty_capital"]).sum())
    return {
        "rows_equal": True,
        "keys_equal": True,
        "max_abs_pred_diff": pred,
        "intent_status_mismatches": status,
        "intent_ask_mismatches": ask,
        "qty_capital_mismatches": qty,
        "bit_identical": bool(pred == 0.0 and status == 0 and ask == 0 and qty == 0),
    }


# ----- stage 4: the pre-registered selection rule ------------------------------
REPORT_RUNGS = (0, 25, 50, 100)


def cell_report(daily: pl.DataFrame, trades: pl.DataFrame, days: list[str]) -> dict:
    cell = frozen.view_cell(daily, trades, VIEW, days)
    known = trades.filter(pl.col("fill_status") == "conditional_fill") if trades.height else trades
    net25 = known["net_usd_25"].to_list() if known.height else []
    net50 = known["net_usd_50"].to_list() if known.height else []
    out = {
        "signals": cell["signals"],
        "funded": cell["intents_funded"],
        "attempts": cell["attempts"],
        "known_fills": cell["known_fills"],
        "unknown_fills": cell["unknown_fills"],
        "no_match_fills": cell["no_match_fills"],
        "traded_days": cell["traded_days"],
        "unknown_share": cell["unknown_share_of_funded_intents"],
        "usd_per_session": {
            str(r): cell["known_usd_per_panel_session"][str(r)] for r in REPORT_RUNGS
        },
        "usd_per_calendar_day": {
            str(r): cell["known_usd_per_calendar_day_span"][str(r)] for r in REPORT_RUNGS
        },
        "median_fill_usd_25": float(np.median(net25)) if net25 else None,
        "median_fill_usd_50": float(np.median(net50)) if net50 else None,
        "top5_share_known_usd_25": (
            float(sum(sorted(net25, reverse=True)[:5]) / sum(net25))
            if net25 and sum(net25)
            else None
        ),
        "months_positive_25": {
            "positive": int(sum(1 for v in cell["monthly"].values() if v["known_usd_25"] > 0)),
            "total": len(cell["monthly"]),
        },
        "skip_reasons": cell["skip_reasons"],
        "monthly": cell["monthly"],
    }
    return out


def extra_split(trades: pl.DataFrame, plans: dict[str, dict], k: int) -> dict:
    """The extra-name (rank 11..K) contribution, split out of the same trade set."""
    if not trades.height:
        return {"known_fills": 0, "funded": 0, "mean_net_usd_25": None, "total_usd_25": 0.0}
    extra_by_day = {d: set(p["extra"].get(str(k), [])) for d, p in plans.items()}
    mask = [
        (d in extra_by_day and t in extra_by_day[d])
        for d, t in zip(trades["day"].to_list(), trades["ticker"].to_list(), strict=True)
    ]
    sub = trades.filter(pl.Series(mask)) if any(mask) else trades.filter(pl.lit(False))
    known = sub.filter(pl.col("fill_status") == "conditional_fill") if sub.height else sub
    net25 = known["net_usd_25"].to_list() if known.height else []
    return {
        "funded": int(sub.height),
        "known_fills": int(known.height),
        "unknown_fills": int((sub["fill_status"] == "unknown_entry_execution").sum())
        if sub.height
        else 0,
        "mean_net_usd_25": float(np.mean(net25)) if net25 else None,
        "median_net_usd_25": float(np.median(net25)) if net25 else None,
        "total_usd_25": float(sum(net25)),
        "total_usd_50": float(known["net_usd_50"].sum()) if known.height else None,
    }


def stage4(args) -> None:
    """Assemble the pre-registered report and apply the selection rule mechanically."""
    blocks = study_days()
    days_by_block = {
        b: [d for d in v if not args.days or d in set(args.days)] for b, v in blocks.items()
    }
    plans = {d: load_plan(d) for b in days_by_block for d in days_by_block[b]}
    results = {
        "study": STUDY,
        "stage": "stage4_decision",
        "status": STATUS,
        "blocks": {},
        "cells": {},
    }
    available = {}
    for name, k, arm, enc in CELL_SPECS:
        dpath, tpath = REPLAY / f"{name}.daily.parquet", REPLAY / f"{name}.trades.parquet"
        if not dpath.exists():
            continue
        daily = pl.read_parquet(dpath)
        trades = pl.read_parquet(tpath) if tpath.exists() else pl.DataFrame()
        available[name] = (k, arm, enc, daily, trades)
    for block, days in days_by_block.items():
        results["blocks"][block] = {"sessions": len(days)}
    for name, (k, arm, enc, daily, trades) in available.items():
        cells = {}
        for block, days in days_by_block.items():
            if not days:
                continue
            sub = daily.filter(pl.col("day").is_in(days))
            tr = trades.filter(pl.col("day").is_in(days)) if trades.height else trades
            cell = cell_report(sub, tr, days)
            cell["extra_names"] = (
                extra_split(tr, plans, k)
                if k > 10
                else {"K": k, "note": "control universe: no extra names"}
            )
            cells[block] = cell
        results["cells"][name] = {"K": k, "arm": arm, "encoding": enc, "blocks": cells}
    decision = apply_rule(results)
    results["decision"] = decision
    results["producer_sha256"] = sha256_file(Path(__file__))
    results["frozen_reference"] = FROZEN_REFERENCE
    write_json(OUT / "results.json", results)
    print(json.dumps(decision, indent=1), flush=True)


def apply_rule(results: dict) -> dict:
    """The registered rule, applied mechanically to whatever cells exist."""
    control = results["cells"].get("k10_control", {}).get("blocks", {}).get(VAL_BLOCK)
    if control is None:
        return {"decision": "NOT-EVALUABLE", "reason": "control cell missing"}
    base = control["usd_per_session"]["25"]
    verdicts = {}
    for k in (20, 30):
        prim = results["cells"].get(f"k{k}_add_A", {}).get("blocks", {}).get(VAL_BLOCK)
        pool = results["cells"].get(f"k{k}_pool_A", {}).get("blocks", {}).get(VAL_BLOCK)
        if prim is None or pool is None:
            verdicts[str(k)] = {"state": "not_evaluated", "reason": "widened cell absent (no data)"}
            continue
        inc_prim = prim["usd_per_session"]["25"] - base
        inc_pool = pool["usd_per_session"]["25"] - base
        crit = {
            "a": prim["usd_per_session"]["25"] >= base,
            "b": prim["usd_per_session"]["50"] > 0,
            "c": bool(
                prim["extra_names"]["known_fills"] >= 10
                and (prim["extra_names"]["mean_net_usd_25"] or -1) > 0
            ),
            "d": bool(np.sign(inc_prim) == np.sign(inc_pool)),
        }
        verdicts[str(k)] = {
            "state": "evaluated",
            "criteria": crit,
            "pass": all(crit.values()),
            "increment_primary_usd_per_session_25": inc_prim,
            "increment_pooled_usd_per_session_25": inc_pool,
        }
    passing = [int(k) for k, v in verdicts.items() if v.get("pass")]
    if passing:
        return {"decision": f"ADOPT k={min(passing)}", "verdicts": verdicts}
    evaluated = [v for v in verdicts.values() if v["state"] == "evaluated"]
    if not evaluated:
        return {
            "decision": "BLOCKED_ON_STORAGE - not evaluated; K=10 control reproduced exactly; "
            "reopen cost K=20 ~1.45 GB/1.13 h validation-only, K=30 ~2.65 GB/2.08 h; resumes "
            "when an off-host data root is mounted",
            "verdicts": verdicts,
            "control_usd_per_session_25": base,
        }
    return {
        "decision": "K=10 (CLOSED): no K>10 cell passed (a)-(d)",
        "verdicts": verdicts,
        "control_usd_per_session_25": base,
    }


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "stage",
        choices=("stage0", "verify0", "harness", "stage1", "stage2", "stage3", "stage4", "all"),
    )
    p.add_argument("--days", nargs="+", default=None, help="smoke subset (debug only)")
    p.add_argument(
        "--blocks",
        default="validation,confirmation",
        help="comma subset of {validation,confirmation} (the emitted block keys)",
    )
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--force", action="store_true")
    p.add_argument("--plan-only", action="store_true")
    p.add_argument("--allow-network", action="store_true", help="unlock stage1/stage2 acquisition")
    p.add_argument("--cells", nargs="+", default=None, help="subset of cell names (debug)")
    p.add_argument("--req-sleep", type=float, default=0.35)
    args = p.parse_args()
    if args.stage == "stage0":
        stage0(args)
    elif args.stage == "stage1":
        stage1(args)
    elif args.stage == "stage2":
        stage2(args)
    elif args.stage == "stage3":
        stage3(args)
    elif args.stage == "verify0":
        stage0_verify(args)
    elif args.stage == "harness":
        harness(args)
    elif args.stage == "stage4":
        stage4(args)
    else:
        raise SystemExit(f"{args.stage} is gated behind the acquisition stop (see report)")


if __name__ == "__main__":
    main()
