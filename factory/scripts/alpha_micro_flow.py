#!/usr/bin/env python3
"""Fixed 30-second buy-flow imbalance continuation on raw SIP tick/NBBO flow.

ONE pre-registered family, registered before any outcome is inspected. On the
strictly-prior one-second core states (last print, last quote, 30s/60s flow
windows) a ticker/day qualifies at a second when, all together:

  family        ret30s >= 0.02   (momentum continuation)
                imbalance30s >= 0.40  (>=70% of classified dollars are buys)
                classified_dv30s >= 25_000
  common        fresh (<=2s print and <=2s regular quote, causal liveness)
  activity      dv60s >= 50_000, n60s >= 20, px >= 5, spread <= 0.003,
                ask_shares*ask >= 1.05 * $250 (entry-side depth cover)

First qualified second per ticker/day only; score = ret30s for simultaneous
capital priority. Entry is a market ASK buy at signal+250ms, exit a market BID
sell at entry+60s; if no fresh firm quote at exit the pending broker exit uses
the FIRST subsequent regular quote <= session_end (never best price/depth).
No passive limits. Entry and exit L1 depth must cover $250 notional; anything
insufficient stays UNKNOWN and is charged as a full-loss lower bound, never a
quiet cash zero. Missing admitted symbol streams make the whole date's
portfolio lower bound -100% of the research book. Results are future fills,
not prior picks; this is a discovery lane, not a fill guarantee.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import sys
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))

from alpha_micro_core import (
    COVERAGE_EPOCH,
    HORIZON_SECONDS,
    LATENCY_US,
    MONTHS,
    ORDER_BUDGET,
    admissions,
    coverage_is_complete,
    coverage_kind,
    coverage_unknown_reason,
    evaluate,
    execution,
    load_day,
    quote_condition_coverage,
    selected_days,
    states,
)

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
CALENDAR = ROOT / "factory" / "artifacts" / "basket" / "sip" / "phase2_session_calendar.json"
DEFAULT_OUT = Path.home() / "alpha-data" / "open-search-v1" / "micro_flow"
VERSION = 1
# Epoch of the persisted signal ROWS. Rows stay byte-comparable semantics
# (family, gates, execution model, gross_60/exit_et_60 output keys), so a cached
# day whose rows_hash matches is upgraded in place: only the derived quote-condition
# coverage fields and the epoch markers move, never a fabricated tick outcome.
ROWS_SCHEMA = 1
BOOT_SEED = 20261008
MAX_SLOTS = 3
BOOK = ORDER_BUDGET * MAX_SLOTS
COSTS = (0, 25, 100, 150)

FAMILY = {"ret30s_min": 0.02, "imbalance30s_min": 0.40, "classified_dv30s_min": 25000}
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
    "hypothesis": "30-second classified buy-flow imbalance push continues on raw "
    "SIP tick/NBBO flow",
    "family": FAMILY,
    "common_gate": COMMON_GATE,
    "qualification": "family AND common liveness/activity gate; first qualified "
    "second per ticker/day; score=ret30s",
    "feature_source": "alpha_micro_core.states one-second grid, strictly prior prints and quotes",
    "entry": f"market ASK buy at signal+{LATENCY_US // 1000}ms; "
    "quote-supported capacity checked, not a fill guarantee",
    "exit": f"market BID sell at entry+{HORIZON_SECONDS}s; pending exit "
    "rides to first subsequent regular quote <= session_end",
    "horizon_seconds": HORIZON_SECONDS,
    "costs_bps_residual_round_trip": list(COSTS),
    "cost_note": "actual side-aware ASK/BID prices plus residual RT; 0bps is a "
    "diagnostic, not a free-fill claim",
    "order_budget": ORDER_BUDGET,
    "max_slots": MAX_SLOTS,
    "research_book": BOOK,
    "periods": {k: {"months": list(v)} for k, v in MONTHS.items()},
    "protected_unread": ["2024", "2025-01", "2026-06", "2026-07", "2026-08"],
    "coverage_rule": "missing admitted symbol stream on a day => whole-date "
    "portfolio lower bound -100% of book; known-complete-day mean reported separately",
    "unknown_rule": "entry/exit quote or L1 depth insufficient => UNKNOWN intent "
    "retained, charged full loss per order",
    "selection": "none; single fixed family and gate registered before outcomes, no "
    "threshold/gain/hold/symbol tuning",
    "out_of_fit_not_pristine": True,
    "calendar": str(CALENDAR.relative_to(ROOT)),
}


def family_qualifier() -> pl.Expr:
    """Full qualification on causal core states: family AND common liveness/activity."""
    return (
        (pl.col("ret30s") >= FAMILY["ret30s_min"])
        & (pl.col("imbalance30s") >= FAMILY["imbalance30s_min"])
        & (pl.col("classified_dv30s") >= FAMILY["classified_dv30s_min"])
        & pl.col("fresh")
        & (pl.col("dv60s") >= COMMON_GATE["dv60s_min"])
        & (pl.col("n60s") >= COMMON_GATE["n60s_min"])
        & (pl.col("px") >= COMMON_GATE["px_min"])
        & (pl.col("spread") <= COMMON_GATE["spread_max"])
        & (
            pl.col("ask_shares") * pl.col("ask")
            >= COMMON_GATE["ask_notional_min_multiple"] * ORDER_BUDGET
        )
    )


def _default(obj):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    raise TypeError(f"not serializable: {type(obj)}")


def _digest_bytes(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def producer_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def period_of(day: str) -> str:
    for name, months in MONTHS.items():
        if day[:7] in months:
            return name
    raise ValueError(f"day outside fixed periods: {day}")


def session_ends(days: list[str]) -> dict[str, int]:
    cal = json.loads(CALENDAR.read_text())["evidence"]
    return {d: int(cal[d]["session_end"]) for d in days}


def period_days() -> dict[str, list[str]]:
    sel = selected_days(DATA)
    ends = session_ends(sorted(itertools.chain.from_iterable(sel.values())))
    out = {}
    for name, months in MONTHS.items():
        days = sorted(d for d in ends if d[:7] in months)
        if days != sel[name]:
            raise ValueError(f"calendar/candidate day mismatch for {name}")
        out[name] = days
    return out


def resume_hash(day: str, contract_sha: str) -> str:
    return hashlib.sha256(
        json.dumps([VERSION, contract_sha, day], sort_keys=True).encode()
    ).hexdigest()


def _write_json_atomic(path: Path, payload: dict) -> None:
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=1, default=_default) + "\n")
    os.replace(tmp, path)


def run_day(
    data: Path,
    day: str,
    session_end: int,
    out_dir: Path,
    producer: str,
    contract_sha: str,
    force: bool = False,
) -> dict:
    """Compute one day's qualified first-second signals + real execution, resume-safe."""
    day_path = out_dir / "days" / f"{day}.json"
    expect = resume_hash(day, contract_sha)
    if day_path.exists() and not force:
        try:
            prior = json.loads(day_path.read_text())
        except (json.JSONDecodeError, OSError):
            prior = None
        if (
            prior
            and prior.get("resume_hash") == expect
            and prior.get("version") == VERSION
            and prior.get("rows_schema", 1) == ROWS_SCHEMA
        ):
            # Cached rows still describe today's ROW semantics; refresh only the
            # derived quote-condition coverage from a conditions-only projection.
            upgraded = upgrade_day_coverage(data, day, prior, producer, expect)
            if upgraded is not None:
                _write_json_atomic(day_path, upgraded)
                return {
                    "day": day,
                    "resumed": True,
                    "coverage": upgraded["coverage"],
                    "signals": len(upgraded["signals"]),
                }
    cov: dict = {
        "watch_names": 0,
        "covered_names": 0,
        "missing_symbol_streams": [],
        "missing_day_file": False,
        "regular_price_prints": 0,
        "quote_events": 0,
        "qualified_seconds": 0,
        "coverage_kind": None,
        "unknown_reason": None,
        "coverage_epoch": COVERAGE_EPOCH,
        "complete": False,
        "error": None,
    }
    rows: list[dict] = []
    try:
        watch = admissions(data, day)
        cov["watch_names"] = len(watch)
        streams, load_cov = load_day(data, day)
        cov.update(load_cov)
        qualifier = family_qualifier()
        for ticker, stream in streams.items():
            st = states(day, ticker, stream, session_end)
            if st.is_empty():
                continue
            cov["qualified_seconds"] += int(st.filter(qualifier).height)
            first = st.filter(qualifier).sort("signal_us").head(1)
            if first.is_empty():
                continue
            r = first.row(0, named=True)
            ex = execution(day, ticker, int(r["signal_us"]), stream, session_end, HORIZON_SECONDS)
            rows.append(
                {
                    **r,
                    **ex,
                    "period": period_of(day),
                    "admit_t": stream["admit_t"],
                    "session_end_minute": session_end,
                    "score": float(r["ret30s"]),
                    "exit_rode": bool(ex["exit_us"] != ex["exit_target_us"]),
                }
            )
    except Exception as exc:  # keep the calendar running; day stays coverage-unknown
        cov["error"] = f"{type(exc).__name__}: {exc}"
    end_kind = (
        "day_error"
        if cov["error"]
        else coverage_kind(
            cov["watch_names"],
            cov.get("missing_symbol_streams") or [],
            cov.get("missing_day_file", False),
            cov.get("no_regular_quote_symbols", 0),
            cov.get("quote_condition_unknown_symbols", 0),
        )
    )
    cov["coverage_kind"] = end_kind
    cov["coverage_epoch"] = COVERAGE_EPOCH
    cov["unknown_reason"] = "day_error" if cov["error"] else coverage_unknown_reason(cov)
    # Cash is legitimate only for a real no-watch day or a completely coded
    # no-signal day; any unknown data shape is worst-bounded, never fake cash.
    cov["complete"] = coverage_is_complete(end_kind) and not cov["error"]
    payload = {
        "day": day,
        "version": VERSION,
        "producer_sha256": producer,
        "resume_hash": expect,
        "session_end": session_end,
        "rows_schema": ROWS_SCHEMA,
        "coverage": cov,
        "signals": rows,
    }
    _write_json_atomic(day_path, payload)
    return {"day": day, "resumed": False, "coverage": cov, "signals": len(rows)}


def upgrade_day_coverage(
    data: Path, day: str, prior: dict, producer: str, expect: str
) -> dict | None:
    """Refresh a cached day's coverage from a conditions-only projection.

    Signal rows are never faked: they are the rows the full pipeline already
    produced for this day under the same ROWS_SCHEMA. Returns None when the
    projection cannot be read, so the caller falls back to a full recompute.
    """
    try:
        watch = admissions(data, day)
        qcov = quote_condition_coverage(data, day, watch)
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None
    old = prior.get("coverage", {}) or {}
    cov = dict(old)
    cov.update(
        {k: qcov[k] for k in ("no_regular_quote_symbols", "quote_condition_unknown_symbols")}
    )
    if qcov.get("missing_day_file") and cov.get("watch_names"):
        cov["missing_day_file"] = True
    cov["coverage_epoch"] = COVERAGE_EPOCH
    kind = coverage_kind(
        cov.get("watch_names", 0),
        cov.get("missing_symbol_streams") or [],
        bool(cov.get("missing_day_file")),
        cov.get("no_regular_quote_symbols", 0),
        cov.get("quote_condition_unknown_symbols", 0),
    )
    cov["coverage_kind"] = kind
    cov["unknown_reason"] = coverage_unknown_reason(cov)
    cov["complete"] = coverage_is_complete(kind)
    payload = {
        **prior,
        "version": VERSION,
        "producer_sha256": producer,
        "resume_hash": expect,
        "rows_schema": ROWS_SCHEMA,
        "coverage_upgrade": {
            "from_coverage_epoch": old.get("coverage_epoch", 1),
            "to_coverage_epoch": COVERAGE_EPOCH,
            "note": "coverage fields refreshed from a conditions-only projection; "
            "signal rows are the unchanged saved rows",
        },
    }
    if any("depth_imbalance" not in r for r in (prior.get("signals") or [])):
        # This cached day predates the past-only depth columns, so its signal rows
        # cannot be passed off as a current-schema tick stage.
        payload["row_depth_columns_stale"] = True
    payload["coverage"] = cov
    return payload


def stage_days(data: Path, out_dir: Path, days: list[str], force: bool) -> list[dict]:
    out_dir.joinpath("days").mkdir(parents=True, exist_ok=True)
    producer = producer_sha256()
    contract_sha = _digest_bytes(CONTRACT)
    ends = session_ends(days)
    infos = []
    for n, day in enumerate(days, 1):
        info = run_day(data, day, ends[day], out_dir, producer, contract_sha, force)
        infos.append(info)
        if n % 10 == 0 or n == len(days):
            print(
                f"days {n}/{len(days)} {json.dumps(info['coverage'], default=_default)}", flush=True
            )
    return infos


def load_day_jsons(out_dir: Path, days: list[str]) -> tuple[list[dict], dict[str, dict]]:
    rows, coverage = [], {}
    for day in days:
        obj = json.loads((out_dir / "days" / f"{day}.json").read_text())
        coverage[day] = obj["coverage"]
        rows.extend(obj["signals"])
    return rows, coverage


def adjust_coverage(metrics: dict, dates: list[str], coverage: dict[str, dict]) -> dict:
    """Coverage-unknown days take the -100% whole-book lower bound for that date."""
    daily = list(metrics["daily"])
    by_day = {r["day"]: r for r in daily}
    unknown_days = set()
    for day in dates:
        if not coverage.get(day, {}).get("complete", False):
            unknown_days.add(day)
            if day in by_day:
                by_day[day] = {
                    **by_day[day],
                    "known_pnl": by_day[day]["known_pnl"],
                    "lower_bound_pnl": -BOOK,
                    "lower_bound_return": -1.0,
                    "coverage_unknown": True,
                }
    daily = [by_day.get(r["day"], {**r, "coverage_unknown": False}) for r in metrics["daily"]]
    returns = np.array([r["lower_bound_return"] for r in daily], dtype=float)
    complete = [r["lower_bound_return"] for r in daily if r["day"] not in set(unknown_days)]
    monthly: dict[str, list[float]] = {}
    yearly: dict[str, list[float]] = {}
    for r in daily:
        monthly.setdefault(r["day"][:7], []).append(r["lower_bound_return"])
        yearly.setdefault(r["day"][:4], []).append(r["lower_bound_return"])
    rng = np.random.default_rng(BOOT_SEED)
    draws = rng.choice(returns, size=(1000, len(returns)), replace=True).mean(axis=1)
    out = dict(metrics)
    out.pop("daily", None)
    out.pop("monthly_mean_lower_bound", None)
    out.update(
        {
            "mean_daily_lower_bound": float(returns.mean()) if len(returns) else None,
            "daily_se": float(returns.std(ddof=1) / np.sqrt(len(returns)))
            if len(returns) > 1
            else None,
            "day_bootstrap_ci95": [float(v) for v in np.quantile(draws, [0.025, 0.975])],
            "mean_daily_lower_bound_known_complete_days": float(np.mean(complete))
            if complete
            else None,
            "known_complete_days": len(complete),
            "coverage_unknown_days": len(unknown_days),
            "coverage_unknown_day_list": sorted(unknown_days),
            "monthly_mean_lower_bound": {k: float(np.mean(v)) for k, v in sorted(monthly.items())},
            "yearly_mean_lower_bound": {k: float(np.mean(v)) for k, v in sorted(yearly.items())},
            "positive_months_lower_bound": int(sum(np.mean(v) > 0 for v in monthly.values())),
            "months": len(monthly),
            "research_book": BOOK,
        }
    )
    out["daily"] = daily
    return out


def stage_results(out_dir: Path, per: dict[str, list[str]] | None = None) -> dict:
    per = per or period_days()
    rows, coverage = load_day_jsons(out_dir, sorted(itertools.chain.from_iterable(per.values())))
    signals = (
        pl.DataFrame(rows)
        if rows
        else pl.DataFrame(
            {
                "day": [],
                "t": [],
                "ticker": [],
                "score": [],
                "entry_status": [],
                f"gross_{HORIZON_SECONDS}": [],
                f"exit_et_{HORIZON_SECONDS}": [],
                "session_end": [],
                "entry_et": [],
                "entry_open": [],
                "horizon_seconds": [],
            }
        )
    )
    signals.write_parquet(out_dir / "signals.parquet")
    results, summary = [], {}
    for period, dates in per.items():
        psel = signals.filter(pl.col("day").is_in(dates)) if signals.height else signals
        unsupported = int(psel.filter(~pl.col("quote_supported")).height) if signals.height else 0
        rode = int(psel.filter(pl.col("exit_rode")).height) if signals.height else 0
        for cost in COSTS:
            metrics, trades = evaluate(psel.to_dicts(), dates, cost, HORIZON_SECONDS)
            metrics = adjust_coverage(metrics, dates, coverage)
            metrics["period"] = period
            metrics["cost_bps_residual"] = cost
            metrics["cost_rule"] = "actual ASK/BID + residual RT bps"
            metrics["signals_selected"] = psel.height
            metrics["quote_unsupported_signals"] = unsupported
            metrics["delayed_exit_signals"] = rode
            results.append(metrics)
            if trades:
                pl.DataFrame(trades).write_parquet(out_dir / f"trades_{period}_{cost}.parquet")
            print(
                f"{period} cost={cost} "
                + json.dumps(
                    {
                        k: metrics[k]
                        for k in (
                            "signals_selected",
                            "attempts",
                            "fills",
                            "known_fills",
                            "unknown_fills",
                            "mean_net_known_fill",
                            "mean_daily_lower_bound",
                            "mean_daily_lower_bound_known_complete_days",
                            "day_bootstrap_ci95",
                        )
                        if k in metrics
                    },
                    default=_default,
                ),
                flush=True,
            )
        rec = {}
        for cost in COSTS:
            m = next(r for r in results if r["period"] == period and r["cost_bps_residual"] == cost)
            rec[cost] = {
                k: m[k]
                for k in (
                    "fills",
                    "known_fills",
                    "unknown_fills",
                    "mean_net_known_fill",
                    "mean_daily_lower_bound",
                    "mean_daily_lower_bound_known_complete_days",
                    "known_win_rate",
                    "known_profit_factor",
                    "worst_known_fill",
                    "day_bootstrap_ci95",
                )
            }
        summary[period] = {
            "dates": len(dates),
            "signals": psel.height,
            "quote_unsupported_signals": unsupported,
            "delayed_exit_signals": rode,
            "by_cost": rec,
        }
    val = summary["validation"]["by_cost"]
    conf = summary["confirmation"]["by_cost"]
    dev = summary["development"]["by_cost"]
    notes = []
    if summary["development"]["signals"] == 0:
        notes.append(
            "development_unobservable: 2021 dev-window SIP quotes are all non-regular "
            "('?'), so the classified-buy-flow family cannot qualify there"
        )
    dev_unknown = [d for d in per["development"] if not coverage.get(d, {}).get("complete", False)]
    if dev_unknown:
        reasons = sorted({coverage.get(d, {}).get("unknown_reason") for d in dev_unknown} - {None})
        notes.append(
            f"development_partially_coverage_unknown: {len(dev_unknown)} dev dates "
            f"carry missing day files ({', '.join(reasons) if reasons else 'unknown'})"
            if all(coverage.get(d, {}).get("missing_day_file") for d in dev_unknown)
            else f"development_coverage_unknown: {len(dev_unknown)} dev dates are not "
            f"certified (raw '?' quote flags and/or missing/partial streams); "
            f"each is worst-bounded at -100% of the book, never booked as cash, "
            f"so the development mean is a lower bound, not an observed edge"
        )
    dev_positive = (dev[100]["mean_daily_lower_bound"] or 0) > 0
    val_positive = (val[100]["mean_daily_lower_bound"] or 0) > 0
    conf_positive = (conf[100]["mean_daily_lower_bound"] or 0) > 0
    late150 = (val[150]["mean_daily_lower_bound"] or 0) > 0 and (
        conf[150]["mean_daily_lower_bound"] or 0
    ) > 0
    support = val[100]["fills"] > 0 and conf[100]["fills"] > 0
    low_residual_positive = any(
        (summary[p]["by_cost"][c]["mean_daily_lower_bound"] or 0) > 0
        for p in summary
        for c in (0, 25)
    )
    if val_positive and conf_positive and late150 and support:
        verdict = "QUOTE_SUPPORTED_NEEDS_FORWARD (not guaranteed alpha)"
    elif dev_positive and not (val_positive and conf_positive):
        verdict = "development_only_not_confirmed"
    elif low_residual_positive:
        verdict = "cost_sensitive: positive only at 0/25bps residual; no 100bps pass"
    else:
        verdict = "no_edge_at_any_cost"
    verdict = verdict + ("; " + "; ".join(notes) if notes else "")
    cov_report = {}
    for period, dates in per.items():
        unknown = [d for d in dates if not coverage.get(d, {}).get("complete", False)]
        cov_report[period] = {
            "days": len(dates),
            "complete_days": sum(1 for d in dates if coverage.get(d, {}).get("complete")),
            "unknown_days": unknown,
            "unknown_days_by_reason": {
                reason: sorted(
                    d for d in unknown if coverage.get(d, {}).get("unknown_reason") == reason
                )
                for reason in sorted(
                    {coverage.get(d, {}).get("unknown_reason") for d in unknown} - {None}
                )
            },
            "quote_condition_unknown_symbol_days": [
                d
                for d in dates
                if (coverage.get(d, {}) or {}).get("quote_condition_unknown_symbols")
            ],
            "missing_symbol_streams": {
                d: coverage.get(d, {}).get("missing_symbol_streams", [])
                for d in dates
                if coverage.get(d, {}).get("missing_symbol_streams")
            },
        }
    contract = json.loads((out_dir / "contract.json").read_text())
    payload = {
        "contract": contract,
        "results": results,
        "coverage": cov_report,
        "summary": summary,
        "verdict": verdict,
        "assumptions": [
            "quote-supported touch is not a guaranteed exchange fill",
            "residual 0bps is a diagnostic; quote prices are real as-of ASK/BID",
            "cash/reuse assumes an eligible margin account, not a small cash account",
            "periods are previously explored markets, not pristine holdout",
        ],
    }
    _write_json_atomic(out_dir / "results.json", payload)
    _write_json_atomic(
        out_dir / "summary.json",
        {"verdict": verdict, "summary": summary, "coverage": cov_report, "contract": contract},
    )
    return payload


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, default=DATA)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument(
        "--stage", choices=["contract", "days", "signals", "results", "all"], default="all"
    )
    p.add_argument(
        "--days", type=str, default="", help="optional comma-separated day subset (smoke runs)"
    )
    p.add_argument("--force", action="store_true", help="recompute days, ignore resume cache")
    args = p.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    contract = {**CONTRACT, "producer_sha256": producer_sha256()}
    contract_path = out / "contract.json"
    semantic = {k: v for k, v in contract.items() if k != "producer_sha256"}
    if contract_path.exists():
        prior = json.loads(contract_path.read_text())
        if {k: v for k, v in prior.items() if k != "producer_sha256"} != semantic:
            raise ValueError("output root contract mismatch; use a new versioned output root")
    else:
        contract_path.write_text(json.dumps(contract, indent=2) + "\n")
    per = period_days()
    if args.days:
        subset = set(args.days.split(","))
        per = {k: [d for d in v if d in subset] for k, v in per.items()}
        missing = sorted(subset - set(itertools.chain.from_iterable(per.values())))
        if missing:
            raise ValueError(f"days outside fixed periods: {missing}")
    days = sorted(itertools.chain.from_iterable(per.values()))
    if args.stage in ("contract",):
        print(f"contract written -> {contract_path}", flush=True)
    if args.stage in ("days", "all"):
        infos = stage_days(args.data, out, days, args.force)
        _write_json_atomic(
            out / "day_stage.json",
            {
                "producer_sha256": producer_sha256(),
                "contract_sha256": _digest_bytes(CONTRACT),
                "days": [
                    {"day": i["day"], "resumed": i.get("resumed", False), "coverage": i["coverage"]}
                    for i in infos
                ],
            },
        )
    if args.stage in ("signals",):
        rows, coverage = load_day_jsons(out, days)
        pl.DataFrame(rows).write_parquet(out / "signals.parquet")
        _write_json_atomic(out / "signals_coverage.json", coverage)
        print(f"signals: {len(rows)} rows -> {out / 'signals.parquet'}", flush=True)
    if args.stage in ("results", "all"):
        payload = stage_results(out, per)
        print(f"verdict: {payload['verdict']}", flush=True)


if __name__ == "__main__":
    main()
