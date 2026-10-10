#!/usr/bin/env python3
"""Fixed causal orderflow-reversal test: aggressive-buyer reclaim after a small pullback.

One hypothesis, frozen in contract.json before any outcome was inspected:
  - pullback   dd120s <= -1%      (last print >=1% below the trailing 120s high of the
                                  one-second grid; every print is strictly prior),
  - reclaim    ret5s >= +0.5%     AND imbalance5s >= 0.40 (net aggressive-BUYING dollar
                                  share of classified flow over the trailing 5s),
  - backdrop   imbalance30s < 0   (the trailing 30s flow is still net selling),
  - core common liveness/activity: fresh causal quote+tick, dv60s >= $50k, n60s >= 20,
    px >= $5, spread <= 0.30%, displayed ask notional >= 1.05 * $250.

This is NOT the resting -10% bid, NOT an H025-style confirmation statistic and NOT
a bar reclaim: every feature is a strictly-prior raw trade / NBBO quantity.

Execution: market buy at the ASK 250ms after the signal second, literal 60-second
BID exit. If no fresh firm quote exists at the target instant, the pending order
fills at the FIRST subsequent regular quote <= session_end - never a favourable
price/depth choice. Entry ASK and exit BID L1 depth must each cover $250 notional;
otherwise the order stays UNKNOWN and is retained at a -100% lower bound. No
future-price gate, no target credit, no horizon search, no threshold search.

Periods are the shared development (6mo) / validation (2mo) / confirmation (6mo)
sets; all were explored before but never with this raw-flow hypothesis. Zero
residual cost is diagnostic (quote touch only, not a free-fill guarantee), 100 and
150bps are the conservative project rulers. Research evidence, not a strategy.

Usage:
    uv run --no-sync python factory/scripts/alpha_micro_reclaim.py \
        --command run   --out /home/hillel/alpha-data/open-search-v1/micro_reclaim
    uv run --no-sync python factory/scripts/alpha_micro_reclaim.py \
        --command report --out /home/hillel/alpha-data/open-search-v1/micro_reclaim
    (per-day resume is hash-pinned: --days <day> re-checks a single day, --force rebuilds)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import polars as pl
from alpha_micro_core import (
    COVERAGE_EPOCH,
    COVERAGE_UNKNOWN_KINDS,
    HORIZON_SECONDS,
    ORDER_BUDGET,
    admissions,
    evaluate,
    execution,
    load_day,
    quote_condition_coverage,
    selected_days,
    states,
)
from alpha_open_panel import ROOT, allowed

CONTRACT = {
    "version": 1,
    "hypothesis": "fresh aggressive-buyer reversal: price >=1% below the 120s high, "
    "+0.5% 5s reclaim with >=40% 5s buy imbalance while 30s flow is net selling",
    "conditions": {
        "dd120s_max": -0.01,
        "ret5s_min": 0.005,
        "imbalance5s_min": 0.40,
        "imbalance30s_max": 0.0,
    },
    "liveness": {
        "fresh_causal_quote_tick": True,
        "dv60s_min": 50_000,
        "n60s_min": 20,
        "px_min": 5.0,
        "spread_max": 0.003,
        "ask_displayed_notional_min": 1.05 * ORDER_BUDGET,
    },
    "admission": "parent alpha_micro_core.admissions: full-PIT B snapshot top-3, "
    "score>=0.05, px>=5, px fresh (px_et>=T-2)",
    "state_grid": "one second from admission minute to 13:00 (or session_end-1); "
    "prints/quotes strictly prior to each second",
    "selection": "first qualified second per ticker/day; score=ret5s for simultaneous "
    "capital priority; one attempt per ticker/day",
    "entry": "market at ASK at signal_us+250ms",
    "exit": "BID at entry+60s (literal); no fresh firm quote => first subsequent regular "
    "quote <= session_end (pending broker exit), never a favourable price",
    "depth": "entry ASK and exit BID L1 must each cover $250 notional; insufficient or "
    "missing => UNKNOWN, retained at -100% lower bound",
    "account": "equal $250 orders, max 3 concurrent slots, $750 research sub-book, "
    "margin-style funded cash reuse; not a claim a $750 cash account day-trades",
    "horizon_seconds": 60,
    "latency_us": 250_000,
    "alternatives": 1,
    "costs_bps_residual_round_trip": [0, 25, 100, 150],
    "periods": {"development_months": 6, "validation_months": 2, "confirmation_months": 6},
    "out_of_fit_not_pristine": True,
    "hpo": "none; no horizon or threshold search",
    "execution_model": "quote-supported touch, capacity-checked; NOT an exchange fill guarantee",
    "sibling_isolation": "no other worker's results consulted for selection",
}
COSTS = (0.0, 25.0, 100.0, 150.0)
BOOT_DRAWS = 1000
BOOT_SEED = 1000
CALENDAR = ROOT / "factory" / "artifacts" / "basket" / "sip" / "phase2_session_calendar.json"
OUT_DEFAULT = Path.home() / "alpha-data" / "open-search-v1" / "micro_reclaim"
# Epoch of the persisted day panel + intent rows. The horizon is explicit (60s for
# this study) and the depth columns are past-only, so a cached day whose
# rows_schema still matches can be metadata-upgraded in place without faking a tick.
ROWS_SCHEMA = 1
STATE_COLUMNS = (
    "day",
    "ticker",
    "signal_us",
    "px",
    "ask",
    "bid",
    "ask_shares",
    "bid_shares",
    "depth_imbalance",
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
)
INT_COLUMNS = ("signal_us", "rank", "n5s", "n30s", "n60s")
FEATURE_KEYS = (
    "dd120s",
    "ret5s",
    "ret30s",
    "ret60s",
    "imbalance5s",
    "imbalance30s",
    "imbalance60s",
    "dv60s",
    "n60s",
    "px",
    "ask",
    "bid",
    "spread",
    "ask_shares",
    "bid_shares",
    "depth_imbalance",
    "fresh",
)


def reversal() -> pl.Expr:
    """Model-free causal reversal predicate: one fixed formulation, no fitted parameters."""
    return (
        (pl.col("dd120s") <= CONTRACT["conditions"]["dd120s_max"])
        & (pl.col("ret5s") >= CONTRACT["conditions"]["ret5s_min"])
        & (pl.col("imbalance5s") >= CONTRACT["conditions"]["imbalance5s_min"])
        & (pl.col("imbalance30s") < CONTRACT["conditions"]["imbalance30s_max"])
    )


def liveness() -> pl.Expr:
    """Core common activity/liveness gate for the micro-entry family."""
    return (
        pl.col("fresh")
        & (pl.col("dv60s") >= CONTRACT["liveness"]["dv60s_min"])
        & (pl.col("n60s") >= CONTRACT["liveness"]["n60s_min"])
        & (pl.col("px") >= CONTRACT["liveness"]["px_min"])
        & (pl.col("spread") <= CONTRACT["liveness"]["spread_max"])
        & (
            pl.col("ask_shares") * pl.col("ask")
            >= CONTRACT["liveness"]["ask_displayed_notional_min"]
        )
    )


def qualifies() -> pl.Expr:
    return reversal() & liveness()


def period(day: str) -> str:
    if day < "2023-01-01":
        return "development"
    if day < "2024-01-01":
        return "validation"
    return "confirmation"


def _plain(value):
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if np.isnan(value) else float(value)
    return value


def session_ends(days: list[str]) -> tuple[dict[str, int], list[str]]:
    evidence = json.loads(CALENDAR.read_text())["evidence"]
    return (
        {d: int(evidence[d]["session_end"]) for d in days if d in evidence},
        sorted(d for d in days if d not in evidence),
    )


def intent_of(row: dict, stream: dict, day: str, session_end: int) -> dict:
    """Execution record for one selected second, kept even when the future is UNKNOWN."""
    ex = execution(day, row["ticker"], int(row["signal_us"]), stream, session_end, HORIZON_SECONDS)
    out = {**ex, "score": _plain(row["ret5s"]), "admit_t": int(stream["admit_t"])}
    out.update({f"q_{k}": _plain(row[k]) for k in FEATURE_KEYS})
    return out


def _day_coverage_labels(info: dict) -> tuple[str, str | None]:
    """(coverage label, specific unknown reason) with a stable by_kind vocabulary.

    The label keeps its pre-registered meaning; the reason is aligned with the core
    quote-condition metadata: a raw '?' flag is data-unknown, a known non-regular
    status is regular-absent, and neither is ever booked as market-closed cash.
    """
    watch = info["watch_names"]
    unknown_flags = int(info.get("quote_condition_unknown_symbols") or 0)
    no_regular = int(info.get("no_regular_quote_symbols") or 0)
    firm = sorted(info.get("names_with_firm_quotes") or [])
    if watch == 0:
        return "no_watch_names", None  # real no-admission day: cash is legit
    if info.get("missing_day_file"):
        return "missing_day_file", "missing_day_file"
    if not firm or info["covered_names"] < watch:
        if unknown_flags and info["covered_names"] >= watch:
            return "no_firm_quotes", "quote_condition_unknown"
        if unknown_flags:
            return "partial_streams", "quote_condition_unknown"
        if info["covered_names"] < watch:
            return "partial_streams", "missing_symbol_streams"
        return "no_firm_quotes", "quote_regular_absent"
    if unknown_flags:
        return "quote_condition_unknown", "quote_condition_unknown"
    if no_regular >= watch:
        return "no_firm_quotes", "quote_regular_absent"
    return "complete", None


def upgrade_manifest_coverage(man: Path, data_dir: Path) -> dict | None:
    """Refresh a cached manifest's coverage metadata from a conditions-only projection.

    The day's tick panel and saved intents are kept untouched (no fabricated tick
    outcome); only the derived quote-condition counts, labels and epoch markers move.
    Returns None when there is nothing to upgrade.
    """
    prior = json.loads(man.read_text())
    if prior.get("coverage_epoch", 1) >= COVERAGE_EPOCH:
        return None
    day = prior["day"]
    watch = admissions_for_day(data_dir, day)
    qcov = quote_condition_coverage(Path(data_dir), day, watch)
    info = {
        **prior,
        "no_regular_quote_symbols": qcov.get("no_regular_quote_symbols", 0),
        "quote_condition_unknown_symbols": qcov.get("quote_condition_unknown_symbols", 0),
        "coverage_epoch": COVERAGE_EPOCH,
    }
    info["coverage"], info["unknown_reason"] = _day_coverage_labels(info)
    info["complete_names"] = info["coverage"] in ("no_watch_names", "complete")
    if qcov.get("names_with_firm_quotes"):
        info["names_with_firm_quotes"] = qcov["names_with_firm_quotes"]
    intents = prior.get("intents") or []
    if any("q_depth_imbalance" not in r for r in intents):
        # This day stage predates the past-only depth columns; a metadata upgrade
        # keeps the original panel/intents, so the stale schema is stated explicitly
        # instead of silently claiming a current-schema tick stage.
        info["panel_schema_stale"] = True
    info["coverage_upgrade"] = {
        "from_coverage_epoch": prior.get("coverage_epoch", 1),
        "to_coverage_epoch": COVERAGE_EPOCH,
        "note": "coverage metadata refreshed from a conditions-only projection; "
        "the tick panel and saved intents are the unchanged original day stage",
    }
    tmp = man.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(info, indent=1) + "\n")
    tmp.replace(man)
    return info


def admissions_for_day(data_dir: Path, day: str) -> dict:
    return admissions(Path(data_dir), day)


def build_day(job: tuple) -> dict:
    day, data_dir, out_dir, session_end, force = job
    if not allowed(day):
        raise ValueError(f"protected/out-of-scope day refused: {day}")
    out = Path(out_dir)
    dest = out / "days" / f"{day}.parquet"
    man = out / "days" / f"{day}.json"
    if not force and dest.exists() and man.exists():
        prior = json.loads(man.read_text())
        if prior.get("producer_sha256") == PRODUCER_SHA:
            return {"day": day, "status": "cached"}
        if (
            prior.get("coverage_epoch", 1) < COVERAGE_EPOCH
            and prior.get("rows_schema", 1) == ROWS_SCHEMA
        ):
            # Cheap metadata upgrade: same tick panel/intents, refreshed coverage.
            info = upgrade_manifest_coverage(man, data_dir)
            if info is not None:
                return {
                    "day": day,
                    "status": "upgraded",
                    "coverage": info["coverage"],
                    "unknown_reason": info.get("unknown_reason"),
                    "watch_names": info.get("watch_names", 0),
                    "covered_names": info.get("covered_names", 0),
                    "qualified_rows": info.get("qualified_rows", 0),
                    "intents": len(info.get("intents", [])),
                }
    start = time.monotonic()
    streams, coverage = load_day(Path(data_dir), day)
    info = {
        "day": day,
        "status": "built",
        "period": period(day),
        "session_end": session_end,
        "producer_sha256": PRODUCER_SHA,
        "contract_sha256": CONTRACT_SHA,
        "watch_names": coverage.get("watch_names", 0),
        "covered_names": coverage.get("covered_names", 0),
        "missing_symbol_streams": coverage.get("missing_symbol_streams", []),
        "missing_day_file": bool(coverage.get("missing_day_file")),
        "regular_price_prints": coverage.get("regular_price_prints", 0),
        "no_regular_quote_symbols": coverage.get("no_regular_quote_symbols", 0),
        "quote_condition_unknown_symbols": coverage.get("quote_condition_unknown_symbols", 0),
        "coverage_epoch": COVERAGE_EPOCH,
        "rows_schema": ROWS_SCHEMA,
    }
    quotes = {t: s["quotes"] for t, s in streams.items()}
    info["quote_events"] = int(sum(len(q["ts"]) for q in quotes.values()))
    info["firm_quote_events"] = int(sum(int(q["regular"].sum()) for q in quotes.values()))
    info["valid_quote_events"] = int(sum(int(q["valid"].sum()) for q in quotes.values()))
    info["names_with_firm_quotes"] = sorted(t for t, q in quotes.items() if q["regular"].any())
    states_rows, intents, per_ticker = [], [], {}
    for ticker, stream in sorted(streams.items()):
        frame = states(day, ticker, stream, session_end)
        rows = frame.filter(qualifies()) if frame.height else frame
        per_ticker[ticker] = {"state_rows": int(frame.height), "qualified": int(rows.height)}
        if rows.height:
            flagged = (
                rows.sort("signal_us")
                .with_row_index("rank")
                .with_columns((pl.col("rank") == 0).alias("selected"))
            )
            states_rows.append(flagged)
            intents.append(intent_of(flagged.row(0, named=True), stream, day, session_end))
    info["tickers"] = per_ticker
    info["state_rows"] = int(sum(v["state_rows"] for v in per_ticker.values()))
    info["qualified_rows"] = int(sum(v["qualified"] for v in per_ticker.values()))
    info["tickers_with_qualified"] = sum(1 for v in per_ticker.values() if v["qualified"])
    info["intents"] = intents
    info["coverage"], info["unknown_reason"] = _day_coverage_labels(info)
    # Cash stays legitimate only for real no-watch days / coded no-signal days;
    # the specific unknown reason is aligned with the core coverage metadata.
    info["complete_names"] = info["coverage"] in ("no_watch_names", "complete")
    schema = {
        k: (pl.Int64 if k in INT_COLUMNS else pl.Float64)
        for k in STATE_COLUMNS
        if k not in ("day", "ticker")
    }
    schema.update({"day": pl.String, "ticker": pl.String, "selected": pl.Boolean})
    panel = pl.concat(states_rows) if states_rows else pl.DataFrame(schema=schema)
    out.joinpath("days").mkdir(parents=True, exist_ok=True)
    panel.write_parquet(str(dest) + ".tmp")
    Path(str(dest) + ".tmp").replace(dest)
    info["runtime_s"] = round(time.monotonic() - start, 2)
    man.with_suffix(".json.tmp").write_text(json.dumps(info, indent=1) + "\n")
    man.with_suffix(".json.tmp").replace(man)
    return {
        "day": day,
        "status": "built",
        "coverage": info["coverage"],
        "watch_names": info["watch_names"],
        "covered_names": info["covered_names"],
        "qualified_rows": info["qualified_rows"],
        "intents": len(intents),
        "runtime_s": info["runtime_s"],
    }


def run(args: argparse.Namespace) -> None:
    args.out.mkdir(parents=True, exist_ok=True)
    contract = {
        **CONTRACT,
        "producer_sha256": PRODUCER_SHA,
        "contract_sha256": CONTRACT_SHA,
        "calendar": str(CALENDAR),
    }
    (args.out / "contract.json").write_text(json.dumps(contract, indent=1) + "\n")
    plan = selected_days(args.data)
    days = args.days or sorted({d for ds in plan.values() for d in ds})
    ends, uncalled = session_ends(days)
    if uncalled:
        raise ValueError(f"no session_end evidence for {uncalled}")
    jobs = [(d, str(args.data), str(args.out), ends[d], args.force) for d in days]
    for n, info in enumerate(_map_jobs(jobs, args.workers), 1):
        if args.days or n % 10 == 0 or n == len(jobs):
            print(f"{n}/{len(jobs)} {json.dumps(info)}", flush=True)
    print(f"built {len(jobs)} days -> {args.out}", flush=True)


def _map_jobs(jobs: list[tuple], workers: int):
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=workers) as pool:
            yield from pool.map(build_day, jobs)
    else:
        for job in jobs:
            yield build_day(job)


def load_manifests(out: Path, days: list[str] | None = None) -> dict[str, dict]:
    manifests = {}
    for path in sorted((out / "days").glob("????-??-??.json")):
        if days and path.stem not in days:
            continue
        manifests[path.stem] = json.loads(path.read_text())
    return manifests


def coverage_block(manifests: dict[str, dict], days: list[str]) -> dict:
    watch = covered = watch_with_firm = 0
    by_kind: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    quote_unknown_days = no_regular_days = 0
    odd: dict[str, list[str]] = {}
    for day in days:
        info = manifests[day]
        watch += info.get("watch_names", 0)
        covered += info.get("covered_names", 0)
        watch_with_firm += len(info.get("names_with_firm_quotes", []))
        quote_unknown_days += 1 if (info.get("quote_condition_unknown_symbols") or 0) else 0
        no_regular_days += 1 if (info.get("no_regular_quote_symbols") or 0) else 0
        kind = info.get("coverage", "missing_manifest")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        reason = info.get("unknown_reason")
        if reason:
            by_reason[reason] = by_reason.get(reason, 0) + 1
        if kind not in ("complete", "no_watch_names"):
            odd.setdefault(kind, []).append(day)
    return {
        "days": len(days),
        "watch_names": watch,
        "covered_names": covered,
        "names_with_firm_quotes": watch_with_firm,
        "by_kind": by_kind,
        "unknown_reasons": dict(sorted(by_reason.items())),
        "quote_condition_unknown_symbol_days": quote_unknown_days,
        "no_regular_quote_symbol_days": no_regular_days,
        "complete_days": by_kind.get("complete", 0) + by_kind.get("no_watch_names", 0),
        "cash_days_legit": by_kind.get("no_watch_names", 0),
        "exception_days": odd,
    }


def _bootstrap(daily: list[dict]) -> list[float] | None:
    values = np.array([r["lower_bound_return"] for r in daily], dtype=float)
    if not len(values):
        return None
    rng = np.random.default_rng(BOOT_SEED)
    sample = rng.choice(values, size=(BOOT_DRAWS, len(values)), replace=True).mean(axis=1)
    return [float(v) for v in np.quantile(sample, [0.025, 0.975])]


def summarise(manifests: dict[str, dict], days: list[str], cost: float, label: str) -> dict:
    intents = [r for d in days for r in manifests[d].get("intents", [])]
    metrics, trades = evaluate(intents, days, cost, HORIZON_SECONDS)
    daily = metrics["daily"]
    complete = {d for d, m in manifests.items() if m.get("complete_names")}
    # Unknown derives from the core coverage metadata, not from zero signals:
    # raw '?' quote flags / missing or partial streams / known regular absence
    # are all worst-bounded; a coded no-signal day stays legitimate cash.
    unknown = {
        d
        for d in days
        if (
            manifests[d].get("coverage") in COVERAGE_UNKNOWN_KINDS
            or (
                manifests[d].get("unknown_reason") not in (None, "no_watch_names")
                and not manifests[d].get("complete_names")
            )
            or (manifests[d].get("quote_condition_unknown_symbols") or 0) > 0
        )
    }
    unknown_by_reason: dict[str, list[str]] = {}
    for d in sorted(unknown):
        reason = manifests[d].get("unknown_reason") or manifests[d].get("coverage", "unlabelled")
        unknown_by_reason.setdefault(reason, []).append(d)
    worst_case = [-1.0 if r["day"] in unknown else r["lower_bound_return"] for r in daily]
    known_rows = [r["lower_bound_return"] for r in daily if r["day"] in complete]
    monthly: dict[str, list[float]] = {}
    for r in daily:
        monthly.setdefault(r["day"][:7], []).append(r["lower_bound_return"])
    delayed = sum(
        1
        for r in intents
        if r.get("exit_us") is not None
        and r.get("exit_target_us") is not None
        and r["exit_us"] > r["exit_target_us"]
    )
    return {
        "period": label,
        "period_days": len(days),
        "cost_bps_residual": cost,
        "order_budget": ORDER_BUDGET,
        "research_book": 3 * ORDER_BUDGET,
        "signals": len(intents),
        "tickers": len({r["ticker"] for r in intents}),
        "attempts": metrics["attempts"],
        "fills": metrics["fills"],
        "known_fills": metrics["known_fills"],
        "unknown_fills": metrics["unknown_fills"],
        "cash_or_slot_skips": metrics["cash_or_slot_skips"],
        "quote_supported": sum(1 for r in intents if r.get("quote_supported")),
        "delayed_exit_supported": delayed,
        "delayed_exit_frequency": (delayed / len(intents)) if intents else None,
        "mean_net_known_fill": metrics["mean_net_known_fill"],
        "mean_daily_lower_bound": metrics["mean_daily_lower_bound"],
        "daily_se": metrics["daily_se"],
        "day_bootstrap_ci95": _bootstrap(daily),
        "known_win_rate": metrics["known_win_rate"],
        "known_profit_factor": metrics["known_profit_factor"],
        "worst_known_fill": metrics["worst_known_fill"],
        "months": metrics["months"],
        "positive_months_lower_bound": metrics["positive_months_lower_bound"],
        "monthly_mean_lower_bound": metrics["monthly_mean_lower_bound"],
        "monthly_n": {k: len(v) for k, v in sorted(monthly.items())},
        "traded_days": metrics["traded_days"],
        "known_complete_day_n": len(known_rows),
        "known_complete_day_mean_lower_bound": (float(np.mean(known_rows)) if known_rows else None),
        "coverage_unknown_days": len(unknown),
        "coverage_unknown_days_by_reason": dict(sorted(unknown_by_reason.items())),
        "quote_condition_unknown_symbol_days": [
            d for d in days if (manifests[d].get("quote_condition_unknown_symbols") or 0) > 0
        ],
        "coverage_worst_case_mean_daily": float(np.mean(worst_case)) if worst_case else None,
        "coverage_worst_case_ci95": _bootstrap([{"lower_bound_return": v} for v in worst_case]),
        "execution_proxy_only": True,
        "daily": [
            {
                "day": r["day"],
                "known_pnl": r["known_pnl"],
                "unknown": r["unknown"],
                "lower_bound_pnl": r["lower_bound_pnl"],
                "lower_bound_return": r["lower_bound_return"],
            }
            for r in daily
        ],
        "_trades": trades,
    }


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
            }
        ).write_parquet(path)
        return
    pl.DataFrame(trades).write_parquet(path)


def verdict(results: list[dict]) -> dict:
    out = {}
    for block in ("development", "validation", "confirmation"):
        rows = [
            r for r in results if r["period"] == block and r["cost_bps_residual"] in (100.0, 150.0)
        ]
        if not rows:
            out[block] = {"status": "no_result"}
            continue
        ci = [r["day_bootstrap_ci95"] for r in rows if r["day_bootstrap_ci95"]]
        surviving = [
            r["cost_bps_residual"]
            for r in rows
            if (r["mean_daily_lower_bound"] or 0.0) > 0
            and r["day_bootstrap_ci95"]
            and r["day_bootstrap_ci95"][0] > 0
        ]
        out[block] = {
            "status": "positive" if surviving else "not_positive",
            "surviving_costs_bps": surviving,
            "worst_100_150_mean_daily_lower_bound": min(
                (r["mean_daily_lower_bound"] or 0.0) for r in rows
            ),
            "worst_100_150_ci95_low": min((c[0] for c in ci), default=None),
            "signals": max(r["signals"] for r in rows),
            "coverage_unknown_days": max(r["coverage_unknown_days"] for r in rows),
        }
    late = [out[b]["status"] == "positive" for b in ("validation", "confirmation")]
    clean = all(
        out[b].get("coverage_unknown_days", 1) == 0
        for b in ("validation", "confirmation")
        if b in out
    )
    out["candidate"] = bool(late and all(late) and clean)
    return out


def report(args: argparse.Namespace) -> None:
    args.out.mkdir(parents=True, exist_ok=True)
    contract = {**CONTRACT, "producer_sha256": PRODUCER_SHA, "contract_sha256": CONTRACT_SHA}
    (args.out / "contract.json").write_text(json.dumps(contract, indent=1) + "\n")
    plan = selected_days(args.data)
    wanted = args.days or sorted({d for ds in plan.values() for d in ds})
    manifests = load_manifests(args.out, wanted)
    missing = sorted(set(wanted) - set(manifests))
    if missing:
        raise ValueError(
            f"report refused: {len(missing)} study days have no manifest "
            f"({missing[:5]}...); run the build first - a covered subset is "
            f"never promoted to the whole portfolio"
        )
    blocks = ("development", "validation", "confirmation")
    days_by_period = {b: sorted(d for d in manifests if period(d) == b) for b in blocks}
    intents = [r for m in manifests.values() for r in m.get("intents", [])]
    columns = sorted({k for r in intents for k in r})
    intents_frame = (
        pl.DataFrame([{k: _plain(r.get(k)) for k in columns} for r in intents])
        if intents
        else pl.DataFrame(schema={"day": pl.String, "ticker": pl.String})
    )
    intents_frame.write_parquet(args.out / "intents.parquet")
    results = []
    for block in blocks:
        days = days_by_period[block]
        for cost in COSTS:
            summary = summarise(manifests, days, cost, block)
            _write_trades(summary.pop("_trades"), args.out / f"trades_{block}_{int(cost)}.parquet")
            results.append(summary)
            print(
                json.dumps(
                    {
                        k: v
                        for k, v in summary.items()
                        if k not in ("daily", "monthly_mean_lower_bound", "monthly_n")
                    }
                ),
                flush=True,
            )
    for block in blocks:
        days = [d for d in days_by_period[block] if manifests[d].get("complete_names")]
        if len(days) == len(days_by_period[block]):
            continue
        for cost in (100.0, 150.0):
            summary = summarise(manifests, days, cost, f"{block}_complete_days_only")
            _write_trades(
                summary.pop("_trades"), args.out / f"trades_{block}_complete_{int(cost)}.parquet"
            )
            results.append(summary)
            print(
                json.dumps(
                    {
                        k: v
                        for k, v in summary.items()
                        if k not in ("daily", "monthly_mean_lower_bound", "monthly_n")
                    }
                ),
                flush=True,
            )
    summary = {
        "contract": contract,
        "days_seen": sorted(manifests),
        "coverage": {b: coverage_block(manifests, days_by_period[b]) for b in blocks},
        "certified": False,
        "verdict": verdict(results),
        "results": results,
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(f"report -> {args.out}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--out", type=Path, default=OUT_DEFAULT)
    parser.add_argument("--days", nargs="+", help="restrict the build/report to these days")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--command",
        choices=("run", "report"),
        default="run",
        help="run: build per-day states/intents; report: replay periods/costs",
    )
    args = parser.parse_args()
    (run if args.command == "run" else report)(args)


PRODUCER_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
CONTRACT_SHA = hashlib.sha256(json.dumps(CONTRACT, sort_keys=True).encode()).hexdigest()

if __name__ == "__main__":
    main()
