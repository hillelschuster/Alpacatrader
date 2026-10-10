#!/usr/bin/env python3
"""BID-backed early 5-second impulse: a NEW fixed micro-entry family on raw SIP tick/NBBO.

This is a genuinely new rival, NOT the failed 30s/60s momentum-reclaim family. It is
registered in <out>/contract.json before any outcome is inspected and is never
re-tuned on results.

Signal (four-way conjunction strictly on the causal one-second core states):
  ret5s          >= 0.005   (price up >=0.5% over the trailing 5s of strictly-prior prints)
  imbalance5s    >= 0.40    (>=70% of classified 5s dollar flow is aggressive buys)
  classified_dv5s>= 5_000   (at least $5k of classified 5s dollars)
  depth_imbalance>= 0.50    ((bid_shares-ask_shares)/(bid_shares+ask_shares)>=0.50 from the
                             strictly-prior displayed NBBO <=> >=75% of displayed depth on the BID;
                             this is the BID-backed signature, past-only, safe denominator)
gated by the common liveness/activity floor: fresh, dv60s>=$50k, n60s>=20, px>=5,
spread<=0.30%, and displayed ask notional >= 1.05*$250 (entry-side depth cover).

Selection: FIRST qualified second per ticker/day only; score = ret5s. Two DECLARED exit
views (5s, 15s) run as independent replays; the horizon is chosen by 2023 (validation)
mean_daily_lower_bound at 100bps residual ONLY, frozen before confirmation. If both views
are <=0 the least-bad is kept as DIAGNOSTIC, never promoted. No hyperparameter grid beyond
the two declared views; no threshold / price-floor / gain adaptation on outcomes.

Execution reality: market ASK buy at signal_us+250ms; market BID at entry+{h}s. If no fresh
firm quote exists at the target instant the pending broker exit fills at the FIRST
subsequent regular quote <= session_end -- never a favourable price or depth, no passive
limits, no price-level touch. Entry ASK and exit BID L1 depth must each cover $250; anything
insufficient stays UNKNOWN and is charged a full-loss lower bound, never a quiet cash zero.

Periods (own power windows, NOT the cancelled Feb-Apr '?' unknown months):
  development  2021-05..2021-10  (six fully R-regular-quote-covered months)
  validation   all of 2023       (twelve months)
  confirmation 2025-09..2026-05  (nine months; previously explored by bar studies, not pristine OOS)
The shared core default MONTHS (original 6+2+6) are left unchanged; this rival chooses its
own more-powerful calendar. Coverage: a date is complete only if EVERY watched admitted name
has usable regular (R) quotes and a present stream; any '?' quote-condition-unknown or
no-regular-quote or missing name makes the WHOLE date coverage-UNKNOWN and takes a -100%
whole-book floor (never fake cash). Not a fill guarantee; research evidence, not a strategy.

Usage:
    uv run --no-sync python factory/scripts/alpha_bid_backed_burst.py \
        --command run --out /home/hillel/alpha-data/open-search-v1/bid_backed_burst
    uv run --no-sync python factory/scripts/alpha_bid_backed_burst.py \
        --command report --out /home/hillel/alpha-data/open-search-v1/bid_backed_burst
    uv run --no-sync python factory/scripts/alpha_bid_backed_burst.py --command signalcondition
    (per-day build is resume-hash pinned: --days <day> re-checks single days, --force rebuilds)
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import time
from pathlib import Path

import alpha_micro_core
import numpy as np
import polars as pl
from alpha_micro_core import (
    COVERAGE_EPOCH,
    ORDER_BUDGET,
    coverage_is_complete,
    coverage_kind,
    evaluate,
    execution,
    load_day,
    states,
)
from alpha_open_panel import ROOT, allowed

DATA = ROOT / "data"
CALENDAR = ROOT / "factory" / "artifacts" / "basket" / "sip" / "phase2_session_calendar.json"
OUT_DEFAULT = Path.home() / "alpha-data" / "open-search-v1" / "bid_backed_burst"
VERSION = 1
BOOT_SEED = 20261008
BOOT_DRAWS = 1000
MAX_SLOTS = 3
BOOK = ORDER_BUDGET * MAX_SLOTS
COSTS = (0.0, 25.0, 100.0, 150.0)
HORIZONS = (5, 15)
LATENCY_MS = 250

# Own power windows: dev 2021-05..10 (fully R), validation ALL 2023, confirmation 2025-09..2026-05.
PERIODS = {
    "development": ("2021-05", "2021-06", "2021-07", "2021-08", "2021-09", "2021-10"),
    "validation": tuple(f"2023-{m:02d}" for m in range(1, 13)),
    "confirmation": (
        "2025-09",
        "2025-10",
        "2025-11",
        "2025-12",
        "2026-01",
        "2026-02",
        "2026-03",
        "2026-04",
        "2026-05",
    ),
}

FAMILY = {
    "ret5s_min": 0.005,
    "imbalance5s_min": 0.40,
    "classified_dv5s_min": 5000.0,
    "depth_imbalance_min": 0.50,
}
COMMON_GATE = {
    "fresh": True,
    "dv60s_min": 50000,
    "n60s_min": 20,
    "px_min": 5.0,
    "spread_max": 0.003,
    "ask_notional_min_multiple": 1.05,
}

CONTRACT = {
    "version": VERSION,
    "hypothesis": "BID-backed early 5-second impulse continues: price +>=0.5% over trailing 5s "
    "prints "
    "with >=40% classified 5s buy-imbalance, >=$5k classified 5s dollars and >=75% of "
    "displayed depth on the BID (depth_imbalance>=0.50)",
    "family": FAMILY,
    "common_gate": COMMON_GATE,
    "qualification": "family AND common liveness/activity gate; first qualified second per "
    "ticker/day; "
    "score=ret5s",
    "depth_imbalance_definition": "(bid_shares - ask_shares)/(bid_shares + ask_shares) from the "
    "strictly-prior "
    "displayed NBBO top-of-book; >=0.50 <=> bid displayed share >=75%; safe "
    "denominator, no future quote, computed past-only by the corrected core states()",
    "feature_source": "alpha_micro_core.states one-second grid, strictly prior prints and quotes "
    "(bid_shares, depth_imbalance added by the corrected core)",
    "admission": "parent alpha_micro_core.admissions: full-PIT B snapshot top-3, score>=0.05, "
    "px>=5, "
    "px fresh (px_et>=T-2)",
    "selection": "first qualified second per ticker/day only; score=ret5s for simultaneous "
    "capital priority; "
    "one attempt per ticker/day",
    "entry": f"market ASK buy at signal_us+{LATENCY_MS}ms; quote-supported capacity "
    f"checked, not a fill guarantee",
    "exit": "market BID at entry+{h}s for each declared view h in (5,15); pending exit rides to "
    "the first "
    "subsequent regular quote <= session_end, never a favourable price/depth; no passive limits, "
    "no price-level touch",
    "declared_exit_views_seconds": list(HORIZONS),
    "horizon_selection": "chosen by 2023 (validation) mean_daily_lower_bound at 100bps residual "
    "ONLY, frozen "
    "before confirmation; if both views <=0 keep least-bad as DIAGNOSTIC, never promoted",
    "costs_bps_residual_round_trip": list(COSTS),
    "cost_note": "actual side-aware as-of ASK/BID quote prices plus residual round-trip bps; "
    "0bps is a "
    "diagnostic touch, not a free-fill claim",
    "order_budget": ORDER_BUDGET,
    "max_slots": MAX_SLOTS,
    "research_book": BOOK,
    "account": "equal $250 orders, max 3 concurrent slots, $750 research sub-book, margin-style "
    "funded cash "
    "reuse; feasible-broker-account declared, NOT a claim a $750 cash account day-trades",
    "quote_sufficient_not_fill": "quote-capacity support is necessary, not a guaranteed "
    "exchange fill",
    "periods": {k: {"months": list(v)} for k, v in PERIODS.items()},
    "protected_unread": ["2024", "2025-01", "2026-06", "2026-07", "2026-08"],
    "coverage_rule": "a date is complete only if every watched admitted name has usable regular "
    "(R) quotes and "
    "a present stream; any quote_condition_unknown ('?') or no-regular-quote or missing name "
    "=> the WHOLE date is coverage-UNKNOWN and takes a -100% whole-book floor (never cash zero)",
    "unknown_rule": "entry/exit quote or L1 depth insufficient => UNKNOWN intent retained, charged "
    "full loss per "
    "order; '?' quote conditions are data-unknown, not proof of no signal",
    "selection_no_grid": "no hyperparameter grid beyond the two declared exit views {5s,15s} x the "
    "cost ladder; "
    "no threshold / price-floor / gain tuning on outcomes",
    "development_window_rationale": "2021 May-Oct are the six fully R-regular-quote-covered "
    "months; 2021 Feb-Apr "
    "quotes are all '?' unknown conditions and are deliberately excluded",
    "out_of_fit_not_pristine": True,
    "sibling_isolation": "no other worker's results consulted for selection; CoreCorrection "
    "substrate only",
    "core_module": "alpha_micro_core",
    "calendar": str(CALENDAR.relative_to(ROOT)),
}

# State features archived per selected second (bid_shares/depth_imbalance from the corrected core).
FEATURE_KEYS = (
    "px",
    "ask",
    "bid",
    "ask_shares",
    "bid_shares",
    "spread",
    "fresh",
    "dd120s",
    "rebound30s",
    "ret5s",
    "ret30s",
    "ret60s",
    "imbalance5s",
    "imbalance30s",
    "imbalance60s",
    "classified_dv5s",
    "classified_dv30s",
    "classified_dv60s",
    "dv5s",
    "dv30s",
    "dv60s",
    "n5s",
    "n30s",
    "n60s",
    "depth_imbalance",
)
PANEL_SCHEMA = {
    "day": pl.String,
    "ticker": pl.String,
    "signal_us": pl.Int64,
    "px": pl.Float64,
    "ask": pl.Float64,
    "bid": pl.Float64,
    "spread": pl.Float64,
    "ret5s": pl.Float64,
    "imbalance5s": pl.Float64,
    "classified_dv5s": pl.Float64,
    "depth_imbalance": pl.Float64,
    "selected": pl.Boolean,
}


# ---------------------------------------------------------------- qualifier
def family_qualifier() -> pl.Expr:
    """The BID-backed early 5s impulse conjunction (past-only core states)."""
    return (
        (pl.col("ret5s") >= FAMILY["ret5s_min"])
        & (pl.col("imbalance5s") >= FAMILY["imbalance5s_min"])
        & (pl.col("classified_dv5s") >= FAMILY["classified_dv5s_min"])
        & (pl.col("depth_imbalance") >= FAMILY["depth_imbalance_min"])
    )


def common_qualifier() -> pl.Expr:
    """Core common liveness/activity gate for the micro-entry family."""
    return (
        pl.col("fresh")
        & (pl.col("dv60s") >= COMMON_GATE["dv60s_min"])
        & (pl.col("n60s") >= COMMON_GATE["n60s_min"])
        & (pl.col("px") >= COMMON_GATE["px_min"])
        & (pl.col("spread") <= COMMON_GATE["spread_max"])
        & (
            pl.col("ask_shares") * pl.col("ask")
            >= COMMON_GATE["ask_notional_min_multiple"] * ORDER_BUDGET
        )
    )


def signal_qualifier() -> pl.Expr:
    """Full pre-registered qualification: family AND common liveness/activity."""
    return family_qualifier() & common_qualifier()


# ---------------------------------------------------------------- helpers
def _plain(value):
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return None if (value is None or np.isnan(value)) else float(value)
    return value


def _default(obj):
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return None if np.isnan(obj) else float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    raise TypeError(f"not serializable: {type(obj)}")


def core_sha256() -> str:
    return hashlib.sha256(Path(alpha_micro_core.__file__).read_bytes()).hexdigest()


def contract_sha256() -> str:
    return hashlib.sha256(json.dumps(CONTRACT, sort_keys=True).encode()).hexdigest()


def period(day: str) -> str:
    for name, months in PERIODS.items():
        if day[:7] in months:
            return name
    raise ValueError(f"day outside fixed periods: {day}")


def resume_hash(day: str, core_sha: str, con_sha: str) -> str:
    return hashlib.sha256(
        json.dumps(
            [VERSION, COVERAGE_EPOCH, core_sha, con_sha, PERIODS, day], sort_keys=True
        ).encode()
    ).hexdigest()


def _write_json_atomic(path: Path, payload: dict) -> None:
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=1, default=_default) + "\n")
    tmp.replace(path)


def _calendar() -> dict:
    return json.loads(CALENDAR.read_text())["evidence"]


def period_days(data: Path, subset: set[str] | None = None) -> dict[str, list[str]]:
    files = sorted(
        p.stem for p in (data / "sip" / "candidates").glob("????-??-??.json") if allowed(p.stem)
    )
    cal = _calendar()
    out: dict[str, list[str]] = {}
    for name, months in PERIODS.items():
        days = [d for d in files if d[:7] in months and d in cal]
        if subset is not None:
            days = [d for d in days if d in subset]
        missing = [d for d in days if d not in cal]
        if missing:
            raise ValueError(f"no session_end evidence for {missing[:3]} in {name}")
        out[name] = days
    return out


# ---------------------------------------------------------------- build
def intent_of(row: dict, stream: dict, day: str, session_end: int) -> dict:
    """Execution record for one selected second; both declared horizon views kept, even UNKNOWN."""
    ticker = row["ticker"]
    sus = int(row["signal_us"])
    ex = {h: execution(day, ticker, sus, stream, session_end, h) for h in HORIZONS}
    base = ex[HORIZONS[0]]
    out = {
        "day": day,
        "ticker": ticker,
        "t": sus,
        "signal_us": sus,
        "score": _plain(row.get("ret5s")),
        "period": period(day),
        "admit_t": int(stream["admit_t"]),
        "session_end_minute": session_end,
        "session_end": _plain(base["session_end"]),
        "entry_et": _plain(base["entry_et"]),
        "entry_open": _plain(base["entry_open"]),
        "entry_status": base.get("entry_status", "filled_proxy"),
    }
    for h in HORIZONS:
        target = ex[h].get("exit_target_us")
        actual = ex[h].get("exit_us")
        out[f"gross_{h}"] = _plain(ex[h][f"gross_{h}"])
        out[f"exit_et_{h}"] = _plain(ex[h][f"exit_et_{h}"])
        out[f"quote_supported_{h}"] = bool(ex[h].get("quote_supported"))
        out[f"exit_us_{h}"] = _plain(actual)
        out[f"exit_target_us_{h}"] = _plain(target)
        out[f"delayed_{h}"] = bool(
            actual is not None and target is not None and int(actual) > int(target)
        )
    for k in FEATURE_KEYS:
        if k in row:
            out[f"q_{k}"] = _plain(row[k])
    return out


def build_day(job: tuple) -> dict:
    day, data_dir, out_dir, session_end, force, producer, con_sha, core_sha = job
    if not allowed(day):
        raise ValueError(f"protected/out-of-scope day refused: {day}")
    out = Path(out_dir)
    out.joinpath("days").mkdir(parents=True, exist_ok=True)
    dest_panel = out / "days" / f"{day}.parquet"
    man = out / "days" / f"{day}.json"
    rh = resume_hash(day, core_sha, con_sha)
    if not force and man.exists():
        try:
            prior = json.loads(man.read_text())
        except (json.JSONDecodeError, OSError):
            prior = None
        if (
            prior
            and prior.get("resume_hash") == rh
            and prior.get("producer_sha256") == producer
            and prior.get("core_sha256") == core_sha
        ):
            return {
                "day": day,
                "status": "cached",
                "coverage": prior.get("coverage_label"),
                "intents": len(prior.get("intents", [])),
                "coverage_complete": prior.get("coverage_complete"),
            }
    start = time.monotonic()
    error = None
    try:
        streams, cov = load_day(Path(data_dir), day)
        watch = int(cov.get("watch_names", 0))
        covered = int(cov.get("covered_names", 0))
        missing_streams = list(cov.get("missing_symbol_streams", []))
        missing_file = bool(cov.get("missing_day_file", False))
        no_reg = int(cov.get("no_regular_quote_symbols", 0))
        q_unknown = int(cov.get("quote_condition_unknown_symbols", 0))
        q_events = int(cov.get("quote_events", 0))
        reg_prints = int(cov.get("regular_price_prints", 0))
        # Canonical shared-substrate coverage classification: complete only if every
        # watched admitted name is observable (present stream AND usable regular R quote,
        # no '?' unknown condition); unknown kinds are never booked as cash.
        coverage_label = coverage_kind(watch, missing_streams, missing_file, no_reg, q_unknown)
        coverage_complete = coverage_is_complete(coverage_label)
        qualifier = signal_qualifier()
        intents, panel_rows = [], []
        for ticker, stream in sorted(streams.items()):
            frame = states(day, ticker, stream, session_end)
            if not frame.height:
                continue
            qualified = frame.filter(qualifier)
            if not qualified.height:
                continue
            first = qualified.sort("signal_us").head(1)
            r = first.row(0, named=True)
            intents.append(intent_of(r, stream, day, session_end))
            row = {k: _plain(r[k]) for k in r}
            row["selected"] = True
            panel_rows.append(row)
    except Exception as exc:  # calendar keeps running; day stays coverage-unknown
        error = f"{type(exc).__name__}: {exc}"
        watch = covered = no_reg = q_unknown = q_events = reg_prints = 0
        missing_streams, coverage_complete = [], False
        coverage_label = "day_error"
        intents, panel_rows = [], []
    panel = pl.DataFrame(panel_rows) if panel_rows else pl.DataFrame(schema=PANEL_SCHEMA)
    panel.write_parquet(str(dest_panel) + ".tmp")
    Path(str(dest_panel) + ".tmp").replace(dest_panel)
    manifest = {
        "day": day,
        "period": period(day),
        "session_end": session_end,
        "version": VERSION,
        "producer_sha256": producer,
        "core_sha256": core_sha,
        "contract_sha256": con_sha,
        "resume_hash": rh,
        "coverage": {
            "watch_names": watch,
            "covered_names": covered,
            "no_regular_quote_symbols": no_reg,
            "quote_condition_unknown_symbols": q_unknown,
            "missing_symbol_streams": missing_streams,
            "missing_day_file": missing_file,
            "quote_events": q_events,
            "regular_price_prints": reg_prints,
        },
        "coverage_label": coverage_label,
        "coverage_complete": coverage_complete,
        "error": error,
        "intents": intents,
        "runtime_s": round(time.monotonic() - start, 3),
    }
    man.with_suffix(".json.tmp").write_text(json.dumps(manifest, indent=1, default=_default) + "\n")
    man.with_suffix(".json.tmp").replace(man)
    return {
        "day": day,
        "status": "built" if error is None else "error",
        "coverage": coverage_label,
        "intents": len(intents),
        "coverage_complete": coverage_complete,
        "error": error,
        "runtime_s": manifest["runtime_s"],
    }


def _map_jobs(jobs: list[tuple], workers: int):
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=workers) as pool:
            yield from pool.map(build_day, jobs)
    else:
        for job in jobs:
            yield build_day(job)


def run(args: argparse.Namespace) -> None:
    args.out.mkdir(parents=True, exist_ok=True)
    producer = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    core_sha, con_sha = core_sha256(), contract_sha256()
    contract = {
        **CONTRACT,
        "producer_sha256": producer,
        "core_sha256": core_sha,
        "contract_sha256": con_sha,
        "calendar": str(CALENDAR),
    }
    contract_path = args.out / "contract.json"
    if contract_path.exists():
        prior = json.loads(contract_path.read_text())
        semantic_now = {k: v for k, v in contract.items() if k != "producer_sha256"}
        semantic_prior = {k: v for k, v in prior.items() if k != "producer_sha256"}
        if semantic_prior != semantic_now:
            raise ValueError("output root contract mismatch; use a new versioned output root")
    else:
        contract_path.write_text(json.dumps(contract, indent=1) + "\n")
    cal = _calendar()
    subset = set(args.days) if args.days else None
    per = period_days(args.data, subset)
    if subset is not None:
        wanted = set(args.days)
        seen = set(itertools.chain.from_iterable(per.values()))
        outside = sorted(wanted - seen)
        if outside:
            raise ValueError(f"days outside fixed periods: {outside}")
    days = sorted(itertools.chain.from_iterable(per.values()))
    jobs = [
        (
            d,
            str(args.data),
            str(args.out),
            int(cal[d]["session_end"]),
            args.force,
            producer,
            con_sha,
            core_sha,
        )
        for d in days
    ]
    infos = []
    for n, info in enumerate(_map_jobs(jobs, args.workers), 1):
        infos.append(info)
        if args.days or n % 10 == 0 or n == len(jobs):
            shown = {k: info[k] for k in info if k != "day"}
            print(
                f"{n}/{len(jobs)} {json.dumps(shown, default=_default)}",
                flush=True,
            )
    _write_json_atomic(
        args.out / "day_stage.json",
        {
            "producer_sha256": producer,
            "core_sha256": core_sha,
            "contract_sha256": con_sha,
            "resume_rule": "sha256[VERSION, core_sha256, contract_sha256, PERIODS, day]",
            "days": [
                {
                    "day": i["day"],
                    "status": i["status"],
                    "coverage": i.get("coverage"),
                    "coverage_complete": i.get("coverage_complete"),
                    "intents": i.get("intents", 0),
                }
                for i in infos
            ],
        },
    )
    print(f"built/checked {len(days)} days -> {args.out}", flush=True)


# ---------------------------------------------------------------- report
def load_manifests(out: Path, days: list[str]) -> dict[str, dict]:
    manifests = {}
    for day in days:
        manifests[day] = json.loads((out / "days" / f"{day}.json").read_text())
    return manifests


def coverage_block(manifests: dict[str, dict], days: list[str]) -> dict:
    watch = covered = watch_with_regular = 0
    by_kind: dict[str, int] = {}
    unknown_names: dict[str, int] = {}
    exception_days: dict[str, list[str]] = {}
    for day in days:
        info = manifests[day]
        cov = info.get("coverage", {})
        watch += cov.get("watch_names", 0)
        covered += cov.get("covered_names", 0)
        watch_with_regular += max(
            0, cov.get("watch_names", 0) - cov.get("no_regular_quote_symbols", 0)
        )
        kind = info.get("coverage_label", "missing_manifest")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        unknown_names[day] = cov.get("quote_condition_unknown_symbols", 0)
        if kind not in ("complete", "no_watch_names"):
            exception_days.setdefault(kind, []).append(day)
    return {
        "days": len(days),
        "watch_names": watch,
        "covered_names": covered,
        "names_with_regular_quotes": watch_with_regular,
        "quote_condition_unknown_symbols_by_day": unknown_names,
        "by_kind": by_kind,
        "exception_days": exception_days,
        "complete_days": by_kind.get("complete", 0) + by_kind.get("no_watch_names", 0),
        "coverage_unknown_days": len(days)
        - by_kind.get("complete", 0)
        - by_kind.get("no_watch_names", 0),
    }


def _bootstrap(returns: list[float]) -> list[float] | None:
    values = np.array(list(returns), dtype=float)
    if not len(values):
        return None
    rng = np.random.default_rng(BOOT_SEED)
    sample = rng.choice(values, size=(BOOT_DRAWS, len(values)), replace=True).mean(axis=1)
    return [float(v) for v in np.quantile(sample, [0.025, 0.975])]


def summarise(
    manifests: dict[str, dict], days: list[str], cost: float, horizon: int, label: str
) -> dict:
    intents = [r for d in days for r in manifests[d].get("intents", [])]
    metrics, trades = evaluate(intents, days, cost, horizon)
    daily = metrics["daily"]
    incomplete = {d for d in days if not manifests[d].get("coverage_complete", False)}
    complete = set(days) - incomplete
    lb: list[float] = []
    for r in daily:
        if r["day"] in incomplete:
            r["lower_bound_return"] = -1.0  # coverage-unknown whole-book floor
            r["lower_bound_pnl"] = -BOOK
        lb.append(r["lower_bound_return"])
    known_complete = [r["lower_bound_return"] for r in daily if r["day"] in complete]
    monthly, yearly = {}, {}
    for r in daily:
        monthly.setdefault(r["day"][:7], []).append(r["lower_bound_return"])
        yearly.setdefault(r["day"][:4], []).append(r["lower_bound_return"])
    sup = sum(1 for r in intents if r.get(f"quote_supported_{horizon}"))
    unsup = sum(1 for r in intents if not r.get(f"quote_supported_{horizon}"))
    delayed = sum(1 for r in intents if r.get(f"delayed_{horizon}"))
    return {
        "period": label,
        "period_role": label,
        "horizon_seconds": horizon,
        "cost_bps_residual": cost,
        "cost_type": "residual round-trip bps on actual as-of ASK/BID quotes",
        "cost_unit": "bps",
        "order_usd": ORDER_BUDGET,
        "book_usd": BOOK,
        "execution_proxy_only": True,
        "period_days": len(days),
        "signals": len(intents),
        "tickers": len({r["ticker"] for r in intents}),
        "attempts": metrics["attempts"],
        "fills": metrics["fills"],
        "known_fills": metrics["known_fills"],
        "unknown_fills": metrics["unknown_fills"],
        "cash_or_slot_skips": metrics["cash_or_slot_skips"],
        "quote_supported_signals": sup,
        "quote_unsupported_signals": unsup,
        "delayed_exit_signals": delayed,
        "mean_net_known_fill": metrics["mean_net_known_fill"],
        "mean_net_known_fill_per_order_pct": (
            metrics["mean_net_known_fill"] * 100.0
            if metrics["mean_net_known_fill"] is not None
            else None
        ),
        "mean_daily_lower_bound": float(np.mean(lb)) if lb else None,
        "mean_daily_lower_bound_book_pct": (float(np.mean(lb)) * 100.0) if lb else None,
        "daily_se": (float(np.std(lb, ddof=1) / np.sqrt(len(lb))) if len(lb) > 1 else None),
        "day_bootstrap_ci95": _bootstrap(lb),
        "known_win_rate": metrics["known_win_rate"],
        "known_profit_factor": metrics["known_profit_factor"],
        "worst_known_fill": metrics["worst_known_fill"],
        "positive_months_lower_bound": int(sum(np.mean(v) > 0 for v in monthly.values())),
        "months": len(monthly),
        "monthly_mean_lower_bound": {k: float(np.mean(v)) for k, v in sorted(monthly.items())},
        "monthly_n": {k: len(v) for k, v in sorted(monthly.items())},
        "yearly_mean_lower_bound": {k: float(np.mean(v)) for k, v in sorted(yearly.items())},
        "yearly_n": {k: len(v) for k, v in sorted(yearly.items())},
        "traded_days": metrics["traded_days"],
        "known_complete_day_n": len(known_complete),
        "known_complete_day_mean_lower_bound": (
            float(np.mean(known_complete)) if known_complete else None
        ),
        "coverage_unknown_days": len(incomplete),
        "coverage_unknown_day_list": sorted(incomplete),
        "coverage_worst_case_mean_daily": float(np.mean(lb)) if lb else None,
        "coverage_worst_case_mean_daily_book_pct": (float(np.mean(lb)) * 100.0) if lb else None,
        "coverage_worst_case_ci95": _bootstrap(lb),
        "certified_complete_period": len(incomplete) == 0,
        "daily": [
            {
                "day": r["day"],
                "known_pnl": r["known_pnl"],
                "unknown": r["unknown"],
                "lower_bound_pnl": r["lower_bound_pnl"],
                "lower_bound_return": r["lower_bound_return"],
                "coverage_unknown": r["day"] in incomplete,
            }
            for r in daily
        ],
        "_trades": trades,
    }


def _thin(summary: dict) -> dict:
    keep = (
        "period",
        "horizon_seconds",
        "cost_bps_residual",
        "cost_type",
        "cost_unit",
        "order_usd",
        "book_usd",
        "period_days",
        "signals",
        "fills",
        "known_fills",
        "unknown_fills",
        "cash_or_slot_skips",
        "quote_supported_signals",
        "delayed_exit_signals",
        "mean_net_known_fill",
        "mean_daily_lower_bound",
        "day_bootstrap_ci95",
        "known_win_rate",
        "positive_months_lower_bound",
        "months",
        "monthly_n",
        "yearly_mean_lower_bound",
        "yearly_n",
        "known_complete_day_n",
        "known_complete_day_mean_lower_bound",
        "coverage_unknown_days",
        "coverage_worst_case_mean_daily",
        "certified_complete_period",
    )
    return {k: summary[k] for k in keep if k in summary}


def _write_trades(trades: list[dict], path: Path) -> None:
    if not trades:
        pl.DataFrame(
            schema={
                "day": pl.String,
                "ticker": pl.String,
                "t": pl.Int64,
                "net": pl.Float64,
                "gross": pl.Float64,
                "status": pl.String,
                "horizon": pl.Int64,
            }
        ).write_parquet(path)
        return
    pl.DataFrame([{k: _plain(v) for k, v in t.items()} for t in trades]).write_parquet(path)


def select_horizon(val_by_cost: dict[int, dict[float, dict]]) -> dict:
    at100 = {
        h: (val_by_cost[h][100.0]["mean_daily_lower_bound"] if 100.0 in val_by_cost[h] else None)
        for h in HORIZONS
    }

    def key(h: int) -> float:
        v = at100[h]
        return v if v is not None else float("-inf")

    chosen = max(HORIZONS, key=key)
    chosen_val = at100[chosen]
    diag = not (chosen_val is not None and chosen_val > 0)
    return {
        "rule": "max(2023 mean_daily_lower_bound at 100bps residual); frozen before confirmation",
        "validation_mean_daily_lower_bound_at_100_by_horizon": {str(h): at100[h] for h in HORIZONS},
        "chosen_horizon_seconds": chosen,
        "chosen_validation_value_at_100": chosen_val,
        "selection_positive_on_validation": not diag,
        "diagnostic_not_promotion": diag,
    }


def report(args: argparse.Namespace) -> None:
    args.out.mkdir(parents=True, exist_ok=True)
    producer = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    core_sha, con_sha = core_sha256(), contract_sha256()
    contract = {
        **CONTRACT,
        "producer_sha256": producer,
        "core_sha256": core_sha,
        "contract_sha256": con_sha,
        "calendar": str(CALENDAR),
    }
    (args.out / "contract.json").write_text(json.dumps(contract, indent=1, default=_default) + "\n")
    subset = set(args.days) if args.days else None
    per = period_days(args.data, subset)
    blocks = ("development", "validation", "confirmation")
    wanted = sorted(itertools.chain.from_iterable(per.values()))
    manifests = load_manifests(args.out, wanted)
    missing = sorted(set(wanted) - set(manifests))
    if missing:
        raise ValueError(
            f"report refused: {len(missing)} study days have no manifest "
            f"({missing[:5]}...); run the build first - a covered subset is never "
            f"promoted to the whole portfolio"
        )
    intents = [r for m in manifests.values() for r in m.get("intents", [])]
    cols = sorted({k for r in intents for k in r})
    frame = (
        pl.DataFrame([{k: _plain(r.get(k)) for k in cols} for r in intents])
        if intents
        else pl.DataFrame(schema={"day": pl.String, "ticker": pl.String})
    )
    frame.write_parquet(args.out / "intents.parquet")

    results: list[dict] = []
    summaries: dict[tuple[str, int, float], dict] = {}
    for block in blocks:
        days = per[block]
        for horizon in HORIZONS:
            for cost in COSTS:
                s = summarise(manifests, days, cost, horizon, block)
                trades = s.pop("_trades")
                _write_trades(trades, args.out / f"trades_{block}_h{horizon}_c{int(cost)}.parquet")
                results.append(s)
                summaries[(block, horizon, cost)] = s

    # Known-complete-days-only slices for any incomplete period (never promoted to whole book).
    for block in blocks:
        days = per[block]
        complete_days = [d for d in days if manifests[d].get("coverage_complete")]
        if complete_days and len(complete_days) < len(days):
            for horizon in HORIZONS:
                for cost in (100.0, 150.0):
                    s = summarise(
                        manifests, complete_days, cost, horizon, f"{block}_complete_days_only"
                    )
                    _write_trades(
                        s.pop("_trades"),
                        args.out / f"trades_{block}_complete_h{horizon}_c{int(cost)}.parquet",
                    )
                    results.append(s)

    # Horizon selection uses ONLY validation (2023) daily lower bound at 100bps;
    # frozen pre-confirmation.
    val_by_cost = {h: {c: summaries[("validation", h, c)] for c in COSTS} for h in HORIZONS}
    selection = select_horizon(val_by_cost)
    chosen = selection["chosen_horizon_seconds"]

    chosen_by_cost = {}
    for block in blocks:
        chosen_by_cost[block] = {str(int(c)): _thin(summaries[(block, chosen, c)]) for c in COSTS}
    conf = {c: summaries[("confirmation", chosen, c)] for c in COSTS}
    data_support = {
        str(int(c)): {
            "known_fills": conf[c]["known_fills"],
            "unknown_fills": conf[c]["unknown_fills"],
            "n_no_fill_or_skips": conf[c]["cash_or_slot_skips"],
            "delayed_exit_signals": conf[c]["delayed_exit_signals"],
            "quote_supported_signals": conf[c]["quote_supported_signals"],
            "signals": conf[c]["signals"],
            "coverage_unknown_days": conf[c]["coverage_unknown_days"],
            "certified_complete_period": conf[c]["certified_complete_period"],
        }
        for c in COSTS
    }
    ci100, ci150 = conf[100.0]["day_bootstrap_ci95"], conf[150.0]["day_bootstrap_ci95"]
    certified = all(
        len({d for d in per[b] if not manifests[d].get("coverage_complete")}) == 0 for b in blocks
    )
    conf_pos = bool(
        (conf[100.0]["mean_daily_lower_bound"] or 0) > 0
        and (conf[150.0]["mean_daily_lower_bound"] or 0) > 0
    )
    ci_ok = bool(ci100 and ci100[0] > 0 and ci150 and ci150[0] > 0)
    if selection["diagnostic_not_promotion"]:
        status = "DIAGNOSTIC: chosen horizon least-bad but 2023 daily lower bound <=0; not promoted"
    elif conf_pos and ci_ok and certified:
        status = "QUOTE_SUPPORTED_NEEDS_FORWARD (not guaranteed alpha)"
    elif conf_pos and not ci_ok:
        status = "confirmation_positive_mean_but_ci_not_all_positive"
    else:
        status = "selection_positive_but_confirmation_not_positive_at_100_150"
    verdict = {
        "status": status,
        "chosen_horizon_seconds": chosen,
        "candidate": status.startswith("QUOTE_SUPPORTED"),
        "certified_complete_all_periods": certified,
        "validation_at_100": {
            "mean_daily_lower_bound": val_by_cost[chosen][100.0]["mean_daily_lower_bound"],
            "day_bootstrap_ci95": val_by_cost[chosen][100.0]["day_bootstrap_ci95"],
            "coverage_unknown_days": val_by_cost[chosen][100.0]["coverage_unknown_days"],
        },
        "confirmation_at_100": {
            "mean_daily_lower_bound": conf[100.0]["mean_daily_lower_bound"],
            "day_bootstrap_ci95": ci100,
            "coverage_unknown_days": conf[100.0]["coverage_unknown_days"],
        },
        "confirmation_at_150": {
            "mean_daily_lower_bound": conf[150.0]["mean_daily_lower_bound"],
            "day_bootstrap_ci95": ci150,
            "coverage_unknown_days": conf[150.0]["coverage_unknown_days"],
        },
        "out_of_fit_not_pristine": True,
        "data_support_confirmation_by_cost": data_support,
    }

    summary = {
        "contract": contract,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "core_module": "alpha_micro_core",
        "core_sha256": core_sha,
        "producer_sha256": producer,
        "contract_sha256": con_sha,
        "cost_type": "residual round-trip bps on top of actual as-of ASK/BID quote prices",
        "cost_rungs_bps_round_trip": [int(c) for c in COSTS],
        "horizons_seconds_declared": list(HORIZONS),
        "periods_months": {k: list(v) for k, v in PERIODS.items()},
        "share_periods_reset_from_old_60s": True,
        "development_window_rationale": CONTRACT["development_window_rationale"],
        "selection": selection,
        "chosen_horizon_seconds": chosen,
        "chosen_horizon_by_cost": chosen_by_cost,
        "verdict": verdict,
        "coverage": {b: coverage_block(manifests, per[b]) for b in blocks},
        "certified_complete": certified,
        "execution_proxy_only": True,
        "results": results,
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1, default=_default) + "\n")
    print(f"report -> {args.out}", flush=True)
    print(
        f"chosen horizon {chosen}s | validation@100 "
        f"lb={selection['chosen_validation_value_at_100']:.5f}"
        if selection["chosen_validation_value_at_100"] is not None
        else f"chosen horizon {chosen}s | validation@100 lb=None",
        flush=True,
    )
    print(f"verdict: {status}", flush=True)


# ---------------------------------------------------------------- signalcondition
def signalcondition(args: argparse.Namespace) -> dict:
    decl = {
        "family": FAMILY,
        "common_gate": COMMON_GATE,
        "combination": "ret5s>=0.005 AND imbalance5s>=0.40 AND classified_dv5s>=5000 AND "
        "depth_imbalance>=0.50, gated by common liveness/activity "
        "(fresh, dv60s>=50k, n60s>=20, px>=5, spread<=0.003, "
        "ask_shares*ask>=1.05*$250)",
        "depth_imbalance": CONTRACT["depth_imbalance_definition"],
        "selection": CONTRACT["selection"],
        "entry": CONTRACT["entry"],
        "exit": CONTRACT["exit"],
        "declared_exit_views_seconds": list(HORIZONS),
        "horizon_selection": CONTRACT["horizon_selection"],
        "costs_bps_residual_round_trip": list(COSTS),
        "periods": {k: list(v) for k, v in PERIODS.items()},
        "protected_unread": CONTRACT["protected_unread"],
        "coverage_rule": CONTRACT["coverage_rule"],
        "unknown_rule": CONTRACT["unknown_rule"],
    }
    print(json.dumps(decl, indent=2), flush=True)
    return decl


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--out", type=Path, default=OUT_DEFAULT)
    parser.add_argument(
        "--days", nargs="+", help="restrict build/report to these days (smoke runs)"
    )
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--force", action="store_true", help="recompute days, ignore resume cache")
    parser.add_argument(
        "--command",
        choices=("run", "report", "signalcondition"),
        default="run",
        help="run: build per-day intents/executions; report: replay periods/horizons/costs; "
        "signalcondition: print the pre-registered qualification",
    )
    args = parser.parse_args()
    ({"run": run, "report": report, "signalcondition": signalcondition})[args.command](args)


PRODUCER_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
CORE_SHA = core_sha256()
CONTRACT_SHA = contract_sha256()

if __name__ == "__main__":
    main()
