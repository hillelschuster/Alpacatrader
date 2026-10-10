#!/usr/bin/env python3
"""Consolidate the four night-extension program outputs into ONE strict evidence JSON.

The four new producers (alpha_sparse_execution_frontier, alpha_sparse_exit_management,
alpha_sparse_daily, alpha_micro_payoff) each own a read-only output root under
~/alpha-data/open-search-v1.  This report READS those JSON summaries and condenses each
program's finite choice plus its frozen chosen late/cost/scenario cells into compact,
explicitly unit-tagged records.  It is deliberately boring:

  * it never runs a producer and never rewrites a producer output;
  * it references the base packet by path + SHA-256 and never copies its blob;
  * every underlying surface stays available as a source path + SHA-256 in ``lineage``
    while bulky daily / per-trade blobs are dropped, not embedded;
  * it refuses to emit the final packet unless ALL FOUR programs have completed actual
    outputs (missing input => nonzero exit, no partial final packet).

Unit semantics are load-bearing and encoded in field names:
  *_fraction / *_return   FRACTION of the tested book or order (0.01 = 1%)
  *_pct                   percent, only where a producer already emitted percent
  *_usd / *_usd_per_*     dollars at the tested sizing (order_usd / book_usd travel along)
  *_per_year / annual_*   simple 252 US-session annualization, never compounded
  *_bps                   round-trip basis points
  n_* / *_count           counts; UNKNOWN outcomes are never cash and never dropped
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = Path.home() / "alpha-data" / "open-search-v1"
DEFAULT_BASE = ROOT / "factory" / "artifacts" / "alpha_search_20261008.json"
DEFAULT_OUT = ROOT / "factory" / "artifacts" / "alpha_night_extensions.json"
DEFAULT_PROGRESS = ROOT / "factory" / "artifacts" / "alpha_night_progress.json"
ASOF = "2026-10-09"
KIND = "ALPHA_NIGHT_EXTENSIONS_NOT_GOAL_COMPLETION"
SIP_OPEX_USD_PER_YEAR = 1188.0
SIP_OPEX_SOURCE = (
    "official live full-market SIP plan 99 USD/month "
    "(https://docs.alpaca.markets/us/docs/about-market-data-api); this "
    "RTH-only research does not by itself justify a 24/7 plan"
)
EXIT_MISSING = 3
EXIT_DRIFT = 4
# alpha_micro_core.ORDER_BUDGET: every micro cell is funded at one $250 order and the
# micro research book is 3 slots x $250.  The per-order mean is a FRACTION of this
# budget, so mean_net_known_fill_usd must equal this constant x that fraction.
MICRO_ORDER_BUDGET_USD = 250.0


class IncompleteError(Exception):
    """A required producer artifact is absent; the packet must not be written."""


class DriftError(Exception):
    """An artifact exists but its structure no longer matches the agreed schema."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_json(path: Path):
    try:
        with path.open() as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise DriftError(f"unreadable JSON {path}: {exc}") from exc


def req(d: dict, key: str, where: str):
    if not isinstance(d, dict) or d.get(key) is None:
        raise DriftError(f"{where}: missing key '{key}'")
    return d[key]


def req_str(d: dict, key: str, where: str) -> str:
    v = req(d, key, where)
    if not isinstance(v, str):
        raise DriftError(f"{where}: '{key}' must be a string")
    return v


def opt(d, key: str, default=None):
    return d.get(key, default) if isinstance(d, dict) else default


def ci95(boot) -> list | None:
    """[lo, hi] from any producer's bootstrap dict; None stays None."""
    if not isinstance(boot, dict):
        return None
    lo, hi = boot.get("ci95_lo", boot.get("ci95_low")), boot.get("ci95_hi", boot.get("ci95_high"))
    return None if lo is None or hi is None else [float(lo), float(hi)]


def row(d: dict, fields) -> dict:
    """Build a condensed row from (out_key, dotted_source_key) pairs; nulls stay null."""
    out = {}
    for o, s in fields:
        v = d
        for part in s.split("."):
            v = v.get(part) if isinstance(v, dict) else None
            if v is None:
                break
        out[o] = v
    return out


def file_ref(path: Path, role: str) -> dict:
    if not path.is_file():
        return {"role": role, "path": str(path), "present": False}
    return {
        "role": role,
        "path": str(path),
        "present": True,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def dir_ref(path: Path, role: str) -> dict:
    if not path.is_dir():
        return {"role": role, "path": str(path), "present": False}
    files = [p for p in path.rglob("*") if p.is_file()]
    return {
        "role": role,
        "path": str(path),
        "present": True,
        "kind": "directory",
        "n_files": len(files),
        "bytes": sum(p.stat().st_size for p in files),
    }


# --------------------------------------------------------------------------- #
# program registry: lane -> producer, candidate output roots, required artifacts
# --------------------------------------------------------------------------- #
LANES = {
    "sparse_execution_frontier": {
        "program": "alpha_sparse_execution_frontier",
        "roots": ("sparse_execution_frontier",),
        "required": (
            "frontier_summary.json",
            "annual_EV.json",
            "reproducibility.json",
            "missing_requests_manifest.json",
        ),
        "summary": "frontier_summary.json",
    },
    "sparse_exit_management": {
        "program": "alpha_sparse_exit_management",
        "roots": ("sparse_exit_management",),
        "required": ("results.json", "selection_freeze.json", "annual_EV.json"),
        "summary": "results.json",
    },
    # the parent contract names root sparse_daily; the producer's original constant wrote
    # learned_sparse_daily, so both are accepted and the ACTUAL root is recorded.
    "sparse_daily": {
        "program": "alpha_sparse_daily",
        "roots": ("sparse_daily", "learned_sparse_daily"),
        "required": ("results.json", "contract.json"),
        "summary": "results.json",
    },
    "micro_payoff": {
        "program": "alpha_micro_payoff",
        "roots": ("micro_payoff",),
        "required": (
            "results.json",
            "summary.json",
            "frozen_contract.json",
            "surface_validation.json",
        ),
        "summary": "results.json",
    },
}
DAILY_VIEWS = ("once_h60", "repeat_h60", "repeat_h30", "repeat_h15", "repeat_h30_strong")
DAILY_VAL_COSTS = ("25", "100", "150")
DAILY_LATE_COSTS = ("25", "100", "150", "200")


def resolve_lane(root_dir: Path, spec: dict):
    """First candidate root that exists AND carries every required artifact wins; every
    candidate is reported so an alias or a stale half-written root can never be silent."""
    found, others = None, []
    for rel in spec["roots"]:
        p = root_dir / rel
        complete = p.is_dir() and all((p / f).is_file() for f in spec["required"])
        if complete and found is None:
            found = p
        elif not p.is_dir():
            others.append(f"{p} (absent)")
        elif not complete:
            others.append(f"{p} (present but missing required artifacts)")
        else:
            others.append(str(p))
    return found, others


def late_costs(contract: dict):
    return tuple(
        str(int(float(c)))
        for c in (opt(contract, "selection") or {}).get("cost_ladder_late_bps") or DAILY_LATE_COSTS
    )


# --------------------------------------------------------------------------- #
# 1) execution frontier: capacity x residual x arrival-latency grid, PRIMARY vs scenario
# --------------------------------------------------------------------------- #
FR_CELL = (
    ("budget_usd", "budget_usd"),
    ("n_trades", "n_trades"),
    ("n_priced", "n_priced"),
    ("n_l1_supported", "n_l1_supported"),
    ("n_unknown", "n_unknown"),
    ("unknown_causes", "unknown_causes"),
    ("coverage_fraction_supported", "coverage_fraction_supported"),
    ("covered_mean_net_fraction", "covered_mean_net_fraction"),
    ("covered_mean_net_usd_per_fill", "covered_mean_net_usd_per_fill"),
    ("covered_win_rate_fraction", "covered_win_rate"),
    ("covered_profit_factor", "covered_profit_factor"),
    ("covered_profit_factor_note", "covered_profit_factor_note"),
    ("covered_worst_net_fraction", "covered_worst_net_fraction"),
    ("covered_traded_days", "covered_traded_days"),
    ("covered_bootstrap_ci95_mean_net_fraction", "covered_bootstrap_ci95_mean_net_fraction"),
    ("n_signal_intents", "n_signal_intents"),
    ("n_known_no_order", "n_known_no_order"),
    ("known_no_order_note", "known_no_order_note"),
)
FR_ANN = (
    ("annual_trades_per_year_whole_case", "trades_per_year_whole_case"),
    ("annual_trades_per_year_covered_case", "trades_per_year_covered_case"),
    ("annual_observed_covered_usd_per_year", "observed_covered_contribution_usd_per_year"),
    (
        "annual_conditional_whole_case_usd_per_year_if_unknown_matched_covered",
        "conditional_whole_case_usd_per_year_if_unknown_matched_covered",
    ),
    (
        "annual_observed_covered_net_of_sip_opex_usd_per_year",
        "observed_covered_contribution_net_of_sip_opex_usd_per_year",
    ),
    (
        "annual_n_known_no_order_excluded_from_whole_case",
        "n_known_no_order_excluded_from_whole_case",
    ),
    (
        "annual_known_no_order_cash_contribution_usd_per_year",
        "known_no_order_cash_contribution_usd_per_year",
    ),
)


def frontier_case_cell(cell: dict) -> dict:
    ann = opt(cell, "annual") or {}
    out = row(cell, FR_CELL)
    out.update(row(ann, FR_ANN))
    out["unknown_outcomes_unmeasured_not_zero"] = True
    out["shared_live_sip_opex_usd_per_year"] = opt(
        ann, "shared_live_sip_opex_usd_per_year", SIP_OPEX_USD_PER_YEAR
    )
    out["annual_no_cagr"] = "simple annualized contribution; no compounding"
    return out


def condense_frontier(lane_dir: Path, files: dict) -> dict:
    s = load_json(files["frontier_summary.json"])
    repro, miss = (
        load_json(files["reproducibility.json"]),
        load_json(files["missing_requests_manifest.json"]),
    )
    cohorts = req(s, "cohorts", "frontier_summary.json")
    missing = [c for c in ("val61", "late143") if c not in cohorts]
    if missing:
        raise IncompleteError(
            f"frontier_summary.json cohorts missing {missing} "
            "(a --only single-cohort run is not a complete frontier)"
        )
    a = req(s, "assumptions", "frontier_summary.json")
    # the PRE-FIX qty-0 schema is a dead path: IntegerCapacityRepair's corrected
    # producer emits the no-order policy, so a summary without it must be rerun,
    # never consolidated through an escape hatch
    no_order_policy = req_str(a, "no_order_policy", "assumptions")
    latencies = [int(x) for x in req(a, "arrival_latencies_ms", "assumptions")]
    residuals = [int(x) for x in req(a, "residual_rt_bps", "assumptions")]
    sizes = [int(x) for x in req(a, "sizes_usd", "assumptions")]
    base_latency = int(req(a, "base_latency_ms", "assumptions"))
    stale = req(a, "stale_scenario", "assumptions")
    rules = ("primary_2s",) + (("stale_scenario_15s",) if stale.get("enabled") else ())

    ladder = []
    for cohort in ("val61", "late143"):
        for rule in rules:
            for lat in latencies:
                for res in residuals:
                    for size in sizes:
                        cell = (
                            ((cohorts[cohort].get(rule) or {}).get(str(lat)) or {}).get(str(res))
                            or {}
                        ).get(str(size))
                        if cell is not None:
                            ladder.append(
                                {
                                    "cohort": cohort,
                                    "rule": rule,
                                    "latency_ms": lat,
                                    "residual_bps": res,
                                    **frontier_case_cell(cell),
                                }
                            )

    def pick(cohort, rule, lat, res, size=1000):
        cell = (((cohorts.get(cohort) or {}).get(rule) or {}).get(str(lat)) or {}).get(
            str(res)
        ) or {}
        cell = cell.get(str(size))
        return frontier_case_cell(cell) if cell is not None else {"absent_from_summary": True}

    return {
        "program": req_str(s, "study", "frontier_summary.json"),
        "output_root": str(lane_dir),
        "summary_file": str(files["frontier_summary.json"]),
        "producer_status": req_str(s, "status", "frontier_summary.json"),
        "producer_generated_at": req_str(s, "generated_at", "frontier_summary.json"),
        "producer_runtime_s": opt(s, "runtime_s"),
        "execution_model": "actual_ask_bid_touch_at_arrival_latency_L1_capacity_checked",
        "arrival_latency_policy": req_str(a, "latency_policy", "assumptions"),
        "assumptions": {
            "arrival_latencies_ms": latencies,
            "base_latency_ms": base_latency,
            "max_quote_age_s": opt(a, "max_quote_age_s"),
            "residual_rt_bps": residuals,
            "sizes_usd": sizes,
            "stale_scenario": {
                "enabled": bool(stale.get("enabled")),
                "max_age_s": opt(stale, "max_age_s"),
                "gate": req_str(stale, "gate", "assumptions.stale_scenario"),
                "risk": req_str(stale, "risk", "assumptions.stale_scenario"),
            },
            "unpriced_policy": req_str(a, "unpriced_policy", "assumptions"),
            # the three current no-order/capacity source fields of the corrected
            # producer; a PRE-FIX summary has none of them and is refused above
            "no_order_policy": no_order_policy,
            "integer_fee_funded_quantity": opt(a, "integer_fee_funded_quantity"),
            "l1_only_measurement": opt(a, "l1_only_measurement"),
            "shared_sip_opex_usd_per_year": opt(
                a, "shared_sip_opex_usd_per_year", SIP_OPEX_USD_PER_YEAR
            ),
            "no_cagr": opt(a, "no_cagr", True),
        },
        "cohorts": {
            n: {
                "label": req_str(c, "label", f"cohorts.{n}"),
                "status_note": req_str(c, "status_note", f"cohorts.{n}"),
                "trades_path": req_str(c, "trades_path", f"cohorts.{n}"),
                "trades_sha256": req_str(c, "trades_sha256", f"cohorts.{n}"),
                "n_trades": req(c, "n_trades", f"cohorts.{n}"),
                "period_days": req(c, "period_days", f"cohorts.{n}"),
                "trading_days_with_signals": req(c, "trading_days_with_signals", f"cohorts.{n}"),
                "proxy_baseline": opt(c, "proxy_baseline"),
                "day_coverage": opt(c, "day_coverage"),
                "reproduction_vs_prior_audit": opt(c, "reproduction_vs_prior_audit"),
            }
            for n, c in cohorts.items()
        },
        # PRIMARY evidence cells only; the 15s stale leg is kept as a labelled scenario.
        "cells_of_record": {
            "headline_primary_evidence": pick("late143", "primary_2s", base_latency, 0),
            "residual_diagnostics_actual_touch": [
                pick("late143", "primary_2s", base_latency, r) for r in residuals if r != 0
            ],
            "latency_sensitivity_no_best_latency_selected": [
                pick("late143", "primary_2s", leg, 0) for leg in latencies if leg != base_latency
            ],
            "validation_cohort_cell": pick("val61", "primary_2s", base_latency, 0),
            "headline_scenario_stale_15s": (
                pick("late143", "stale_scenario_15s", base_latency, 0)
                if stale.get("enabled")
                else {"not_run": True, "reason": "assumptions.stale_scenario.enabled=false"}
            ),
        },
        "capacity_cost_ladder_rows": ladder,
        "integer_capacity_no_order_class": (
            "quarter-budget pairs whose integer quantity is zero are a known NO-ORDER "
            "cash class (assumptions.no_order_policy + n_known_no_order per cell): "
            "they stay counted in n_signal_intents/n_priced, are excluded from "
            "n_l1_supported, the covered mean, the bootstrap CI and unknown_causes, "
            "contribute $0 cash (never an UNKNOWN loss and never a false filled count), "
            "and leave n_unknown unchanged, so coverage_fraction_supported drops at $250 "
            "while the covered mean rises"
        ),
        "reproduction_note": (
            "the 250ms base leg must reproduce the frozen prior quote audit exactly when "
            "no historical supplement is merged; in a --fetch-missing run the previously "
            "unacquired legs become priced, so reproduction_vs_prior_audit.all_match can "
            "legitimately be false - the keys are unchanged and every check still carries "
            "prior vs frontier values, so the diff explains itself (informational, not a "
            "producer failure)"
        ),
        "missing_acquisition_legs": {
            "legs_detected": req(miss, "legs_detected", "missing_requests_manifest.json"),
            "fetch_missing": bool(req(miss, "fetch_missing", "missing_requests_manifest.json")),
            "status_counts": dict(
                sorted(
                    Counter(
                        str(rq.get("status"))
                        for rq in req(miss, "requests", "missing_requests_manifest.json")
                    ).items()
                )
            ),
            "note": req_str(miss, "note", "missing_requests_manifest.json"),
            "requests": req(miss, "requests", "missing_requests_manifest.json"),
        },
        "reproduction": {
            "argv": opt(repro, "argv"),
            "inputs": opt(repro, "inputs"),
            "input_sha256": opt(repro, "input_sha256"),
            "program_files": opt(repro, "program_files"),
            "boot": opt(repro, "boot"),
            "fetch_missing": opt(repro, "fetch_missing"),
        },
        "_lineage_files": {
            "frontier_summary.json": files["frontier_summary.json"],
            "annual_EV.json": files["annual_EV.json"],
            "reproducibility.json": files["reproducibility.json"],
            "missing_requests_manifest.json": files["missing_requests_manifest.json"],
            "val61_pairs_parquet": lane_dir / "val61" / "pairs.parquet",
            "late143_pairs_parquet": lane_dir / "late143" / "pairs.parquet",
        },
        "_lineage_dirs": {
            "producer_snapshot": lane_dir / "producer_snapshot",
            "fetched_quotes": lane_dir / "fetched_quotes",
        },
    }


# --------------------------------------------------------------------------- #
# 2) exit management: paired hold60m control vs quote-triggered stops
# --------------------------------------------------------------------------- #
EX_VIEW = (
    ("days", "days"),
    ("attempts", "attempts"),
    ("fills", "fills"),
    ("known_fills", "known_fills"),
    ("unknown_fills", "unknown_fills"),
    ("cash_or_slot_skips", "cash_or_slot_skips"),
    ("cost_bps", "cost_bps"),
    ("horizon_minutes", "horizon"),
    ("pricing", "pricing"),
    ("execution_proxy_only", "execution_proxy_only"),
    ("quote_touch_not_fill_guaranteed", "quote_touch_not_fill_guaranteed"),
    ("mean_net_known_fill_fraction", "mean_net_known_fill"),
    ("mean_daily_lower_bound_book_fraction", "mean_daily_lower_bound"),
    ("daily_se_book_fraction", "daily_se"),
    ("known_win_rate_fraction", "known_win_rate"),
    ("known_profit_factor", "known_profit_factor"),
    ("worst_known_fill_fraction", "worst_known_fill"),
    ("positive_months_lower_bound", "positive_months_lower_bound"),
    ("months", "months"),
    ("traded_days", "traded_days"),
    ("unknown_causes", "unknown_causes"),
    ("known_no_order_fills", "known_no_order_fills"),
    ("known_no_order_note", "known_no_order_note"),
)


def book_view_row(policy: str, tag, view: dict) -> dict:
    out = {"policy": policy, "residual_bps": tag}
    out.update(row(view, EX_VIEW))
    boot = opt(view, "bootstrap_daily") or {}
    out["day_bootstrap_ci95_book_fraction"] = ci95(boot)
    out["day_bootstrap_p_gt_zero"] = opt(boot, "p_gt_zero")
    return out


def condense_exit(lane_dir: Path, files: dict) -> dict:
    r = load_json(files["results.json"])
    freeze = load_json(files["selection_freeze.json"])
    contract, selection = req(r, "contract", "results.json"), req(r, "selection", "results.json")
    chosen = req(selection, "chosen", "selection")
    if not isinstance(chosen, str):
        raise DriftError("selection.chosen must be a policy name string")
    if (contract.get("selection") or {}).get("chosen") != chosen:
        raise DriftError("contract.selection.chosen disagrees with selection.chosen")
    if (freeze.get("selection") or {}).get("chosen") != chosen:
        raise DriftError("selection_freeze.json chosen disagrees with results.json")

    def surface_rows(section):
        return [
            book_view_row(p, tag, v)
            for p, per in (opt(r, section) or {}).items()
            for tag, v in (per or {}).items()
        ]

    ann = req(r, "annual_ev", "annual_ev")
    primary = str(int(float(req(selection, "primary_residual_bps", "selection"))))
    late_chosen = (opt(ann, "late") or {}).get(chosen) or {}
    if primary not in late_chosen:
        raise DriftError(
            f"annual_ev.late.{chosen} missing residual {primary} "
            "(the producer must annualize the chosen policy at its primary "
            "residual)"
        )
    headline = dict(late_chosen[primary])
    return {
        "program": "alpha_sparse_exit_management",
        "output_root": str(lane_dir),
        "summary_file": str(files["results.json"]),
        "producer_status": req_str(r, "status", "results.json"),
        "decision": req_str(r, "decision", "results.json"),
        "producer_runtime_s": opt(r, "runtime_s"),
        "execution_model": "actual_nbbo_touch_at_250ms_arrival_gap_aware_stops",
        "arrival_latency_assumption": opt(contract, "arrival_latency_assumption"),
        "policies": opt(r, "policies"),
        "selection": {
            "objective": req_str(selection, "objective", "selection"),
            "primary_residual_bps": req(selection, "primary_residual_bps", "selection"),
            "scores": req(selection, "scores", "selection"),
            "chosen": chosen,
            "tie_break": req_str(selection, "tie_break", "selection"),
            "late_outcomes_used_in_selection": req(
                selection, "late_outcomes_used_in_selection", "selection"
            ),
        },
        "frozen_at": opt(freeze, "frozen_at"),
        "frozen_before_late_outcomes": opt(freeze, "frozen_before_late_outcomes"),
        "input_sha256": opt(freeze, "input_sha256"),
        "calendar": opt(freeze, "calendar"),
        "validation_surface_rows": surface_rows("validation_surface"),
        "validation_base_proxy_rows": [
            book_view_row("base_proxy", t, v)
            for t, v in (opt(r, "validation_base_proxy") or {}).items()
        ],
        "base_reproduction": opt(r, "base_reproduction"),
        "late_surface_rows": surface_rows("late_surface"),
        "paired_comparisons": {
            "validation": opt(opt(r, "paired") or {}, "validation"),
            "late": opt(opt(r, "paired") or {}, "late"),
        },
        "stop_quality_tail_diagnostics": {
            "validation": opt(opt(r, "stop_quality") or {}, "validation"),
            "late": opt(opt(r, "stop_quality") or {}, "late"),
        },
        "annual_ev": ann,
        "frequency_frozen": req(r, "frequency_frozen", "results.json"),
        "chosen_policy_late_headline": {
            "policy": chosen,
            "residual_bps": float(primary),
            "whole_lb_usd_per_year": opt(headline, "whole_lb_usd_per_year"),
            "covered_ev_usd_per_year": opt(headline, "covered_ev_usd_per_year"),
            "known_only_usd_per_year_no_unknown_charge": opt(
                headline, "known_only_usd_per_year_no_unknown_charge"
            ),
            "observed_known_fills_per_year": opt(headline, "observed_known_fills_per_year"),
            "calendar_days": opt(headline, "calendar_days"),
            "basis": (
                "simple 252-session annualization of the whole-book daily lower "
                "bound (UNKNOWN fills charged a full $1,000 unit loss); covered_ev "
                "is the quote-supported known-fill subset only, never the whole "
                "portfolio, and it -- not the whole-book coding bound -- is the "
                "supported figure in the CLI table and the lead ranking"
            ),
            "no_cagr": True,
        },
        "lower_bound_interpretation": {
            "coded_bound_not_observed_pnl": (
                "the whole-book lower bound charges every UNKNOWN leg (unpriced: "
                "top-of-book depth shortfall, stale quote, missing acquisition) a full "
                "$1,000 unit loss, so a deeply negative bound is a coding convention "
                "driven by how many legs stayed unpriced; it is NOT observed PnL and not "
                "an expected loss of that size"
            ),
            "no_purposeless_disqualification": (
                "a negative bound dominated by UNKNOWN legs is not a strategy "
                "falsification; the paired known-subset comparison and the stop-quality "
                "tail/gap diagnostics carry the economic signal"
            ),
            "control_price_protocol": (
                "the hold60m control here prices exits resting-until-next-regular-quote "
                "(known counts depend on data support); the original base study priced "
                "77 known pairs on "
                "the instant-quote protocol, so the two are different price protocols and "
                "are annotated, never blended"
            ),
            "quote_data_support": (
                "known-fill counts in this study depend on quote data support: a rerun "
                "may merge a read-only fetched supplement (all legs HTTP 200 acquired) "
                "over the ranked quote cache, so known counts can move versus an earlier "
                "run (late 90/92 known) with NO strategy-rule change - a data-support "
                "difference, not a different strategy and not a different price protocol"
            ),
            "integer_capacity_correction_scope": (
                "the integer-capacity correction (quarter-budget qty=0 pairs become a "
                "known NO-ORDER cash class) does not alter the retained baseline: the "
                "actual $1,000 cohort has zero qty-0 rows, and q>=1 financial values are "
                "unchanged by the fix"
            ),
        },
        "chosen_policy_validation_headline": ((opt(ann, "validation") or {}).get(chosen) or {}).get(
            primary
        ),
        "_lineage_files": {
            "results.json": files["results.json"],
            "selection_freeze.json": files["selection_freeze.json"],
            "annual_EV.json": files["annual_EV.json"],
            "trades_val_paired_parquet": lane_dir / "trades_val_paired.parquet",
            "trades_late_paired_parquet": lane_dir / "trades_late_paired.parquet",
        },
    }


# --------------------------------------------------------------------------- #
# 3) sparse daily frequency: five fixed cadence views on the frozen h60 model
# --------------------------------------------------------------------------- #
DAILY_CELL = (
    ("cadence", "view.cadence"),
    ("horizon_minutes", "horizon"),
    ("cooldown_min", "cooldown_min"),
    ("max_attempts", "max_attempts"),
    ("reenters", "view.reenters"),
    ("days_replayed", "days"),
    ("attempts", "attempts"),
    ("fills", "fills"),
    ("known_fills", "known_fills"),
    ("unknown_fills", "unknown_fills"),
    ("cash_or_slot_skips", "cash_or_slot_skips"),
    ("skips_total", "skips_total"),
    ("skip_reasons", "skip_reasons"),
    ("traded_days", "traded_days"),
    ("n_signals", "n_signals"),
    ("mean_net_known_fill_fraction", "mean_net_known_fill"),
    ("mean_daily_lower_bound_book_fraction", "mean_daily_lower_bound"),
    ("daily_se_book_fraction", "daily_se"),
    ("dollars_per_day_usd", "dollars_per_day"),
    ("dollars_per_year_252_usd", "dollars_per_year_252"),
    ("capital_density_annual_on_reserve_fraction", "capital_density_annual"),
    ("turnover_usd", "turnover_usd"),
    ("turnover_usd_per_day", "turnover_usd_per_day"),
    ("mean_hold_min", "mean_hold_min"),
    ("positions_mean", "positions_mean"),
    ("positions_peak", "positions_peak"),
    ("mean_peak_deployed_usd", "peak_deployed_usd"),
    ("carry_equity_end_usd", "carry_equity_end"),
    ("carry_equity_return_fraction", "carry_equity_return"),
    ("known_win_rate_fraction", "known_win_rate"),
    ("known_profit_factor", "known_profit_factor"),
    ("worst_known_fill_fraction", "worst_known_fill"),
    ("positive_months_lower_bound", "positive_months_lower_bound"),
    ("months", "months"),
    ("execution_proxy_only", "execution_proxy_only"),
    ("carry_equity_is_compounding", "carry_equity_is_compounding"),
)


def daily_cell_row(view_name: str, cost: str, cell: dict) -> dict:
    out = {"view": view_name, "cost_bps": int(cost)}
    out.update(row(cell, DAILY_CELL))
    boot = opt(cell, "bootstrap_daily") or {}
    out["day_bootstrap_ci95_book_fraction"] = ci95(boot)
    out["day_bootstrap_p_gt_zero"] = opt(boot, "p_gt_zero")
    return out


def daily_quote_audit(lane_dir: Path) -> dict:
    """Optional downstream as-of NBBO audit of the chosen repeat view's late trades."""
    p = lane_dir / "quote_audit" / "confirmation" / "summary.json"
    if not p.is_file():
        return {"present": False, "expected_path": str(p)}
    s = load_json(p)
    trades, supported = opt(s, "trades"), opt(s, "quote_supported_pairs")
    return {
        "present": True,
        "path": str(p),
        "sha256": sha256_file(p),
        "stage": "quote_audit (downstream as-of NBBO audit stage)",
        "input_producer": "alpha_sparse_daily",
        "executor": "alpha_quote_audit",
        "trades": trades,
        "quote_supported_pairs": supported,
        "unknown_pairs": opt(s, "unknown_pairs"),
        "coverage_pct": (
            round(100.0 * supported / trades, 4)
            if isinstance(trades, int) and trades and supported is not None
            else None
        ),
        "status_counts": opt(s, "status_counts"),
        "covered_mean_net_fraction_by_residual_bps": opt(s, "covered_mean_net"),
        "whole_portfolio_certified": opt(s, "whole_portfolio_certified"),
        "quote_is_not_exchange_fill": opt(s, "quote_is_not_exchange_fill"),
        "latency_ms": opt(s, "latency_ms"),
        "max_quote_age_s": opt(s, "max_quote_age_s"),
        "order_budget": opt(s, "order_budget"),
        "note": (
            "covered subset economics belong to the quote-audit stage over the "
            "replay's trades input (a NEW repeat-cadence cohort, not the original "
            "143-trade rare cohort); they are never attributed to the replay "
            "economics, and UNKNOWN legs are unmeasured, not cash"
        ),
    }


def condense_daily(lane_dir: Path, files: dict) -> dict:
    r = load_json(files["results.json"])
    contract = load_json(files["contract.json"])
    c_chosen = req(req(r, "contract", "results.json"), "chosen", "results.contract")
    if not isinstance(c_chosen, dict) or not c_chosen.get("name"):
        raise DriftError("contract.chosen must carry a view name")
    if (contract.get("chosen") or {}).get("name") != c_chosen["name"]:
        raise DriftError("contract.json chosen view disagrees with results.json")
    chosen = c_chosen["name"]
    if req_str(r, "status", "results.json") != "DISCOVERY-NOT-VALIDATED":
        raise DriftError("results.status must be DISCOVERY-NOT-VALIDATED")
    if contract.get("frozen_before_late_inspection") is not True:
        raise DriftError("contract.frozen_before_late_inspection must be true")
    val = req(r, "validation", "results.json")
    for v in DAILY_VIEWS:
        if v not in val:
            raise DriftError(f"validation surface missing view {v}")
        for c in DAILY_VAL_COSTS:
            if c not in (val[v] or {}):
                raise DriftError(f"validation[{v}] missing cost {c}")
    late = opt(r, "confirmation_by_cost")
    if late is None:
        raise IncompleteError(
            f"{lane_dir}/results.json has confirmation_by_cost=null "
            "(a --skip-late freeze-only run is not complete)"
        )
    for v in DAILY_VIEWS:
        for c in late_costs(contract):
            if c not in (late.get(v) or {}):
                raise DriftError(f"confirmation_by_cost[{v}] missing cost {c}")
    if "100" not in (late.get(chosen) or {}):
        raise DriftError(f"confirmation_by_cost[{chosen}] missing the 100bps headline cell")
    sel, model = req(contract, "selection", "contract"), req(contract, "model", "contract")
    chosen_cell = (late.get(chosen) or {}).get("100") or {}
    economics = opt(chosen_cell, "economics") or {}
    return {
        "program": "alpha_sparse_daily",
        "output_root": str(lane_dir),
        "summary_file": str(files["results.json"]),
        "producer_status": req_str(r, "status", "results.json"),
        "decision": req_str(r, "decision", "results.json"),
        "producer_runtime_s": opt(r, "runtime_s"),
        "execution_model": "minute_open_proxy_round_trip_fee_both_legs",
        "quote_latency_ms": None,
        "spread_model": "none",
        "chosen_view": c_chosen,
        "views": opt(contract, "views"),
        "selection": {
            "objective": req_str(sel, "objective", "contract.selection"),
            "selection_cost_bps": req(sel, "selection_cost_bps", "contract.selection"),
            "cost_ladder_validation_bps": req(
                sel, "cost_ladder_validation_bps", "contract.selection"
            ),
            "cost_ladder_late_bps": req(sel, "cost_ladder_late_bps", "contract.selection"),
            "ranked_dollars_per_day": req(sel, "ranked_dollars_per_day", "contract.selection"),
            "power_floors": opt(sel, "power_floors"),
            "median_or_tail_gates": opt(sel, "median_or_tail_gates"),
        },
        "model": {
            "refit": req(model, "refit", "contract.model"),
            "hpo": req(model, "hpo", "contract.model"),
            "feature_changes": req(model, "feature_changes", "contract.model"),
            "ticker_features_added": req(model, "ticker_features_added", "contract.model"),
            "horizons_scored": req(model, "horizons_scored", "contract.model"),
            "stored_path": req_str(model, "stored_path", "contract.model"),
            "stored_sha256": req_str(model, "stored_sha256", "contract.model"),
            "n_features": len(opt(model, "feature_order") or []),
        },
        "producer_sha256_reported_in_artifact": (opt(contract, "provenance") or {}).get(
            "producer_sha256"
        ),
        "panel_contract_sha256": (opt(contract, "provenance") or {}).get("panel_contract_sha256"),
        "replay_conventions": req(contract, "replay", "contract"),
        "control_reproduction": opt(opt(contract, "validation") or {}, "control_reproduction"),
        "validation_cell_rows": [
            daily_cell_row(v, c, val[v][c]) for v in DAILY_VIEWS for c in DAILY_VAL_COSTS
        ],
        "confirmation_cell_rows": [
            daily_cell_row(v, c, late[v][c]) for v in DAILY_VIEWS for c in late_costs(contract)
        ],
        "validation_incremental_vs_first_attempt": opt(r, "validation_incremental"),
        "confirmation_incremental_vs_first_attempt": opt(r, "confirmation_incremental"),
        "validation_first_attempt_baselines": opt(r, "validation_first_attempt_baselines"),
        "confirmation_first_attempt_baselines": opt(r, "confirmation_first_attempt_baselines"),
        "chosen_baseline_comparison": opt(r, "chosen_baseline_comparison"),
        "scale": req(r, "scale", "results.json"),
        "quote_audit_input": {
            "note": (
                "as-of NBBO audit INPUT for the chosen view's late trades; a quote "
                "audit is not an exchange fill and cannot certify the uncovered "
                "portfolio"
            ),
            "by_cost": opt(r, "quote_audit_input"),
        },
        "quote_audit_confirmation_summary": daily_quote_audit(lane_dir),
        "chosen_view_late_headline": {
            "view": chosen,
            "cost_bps": 100,
            "dollars_per_year_252_usd": opt(chosen_cell, "dollars_per_year_252"),
            "economics_dollars_per_year_252_usd": opt(economics, "dollars_per_year_252"),
            "mean_daily_lower_bound_book_fraction": opt(chosen_cell, "mean_daily_lower_bound"),
            "dollars_per_day_usd": opt(chosen_cell, "dollars_per_day"),
            "known_fills": opt(chosen_cell, "known_fills"),
            "unknown_fills": opt(chosen_cell, "unknown_fills"),
            "traded_days": opt(chosen_cell, "traded_days"),
            "attempts": opt(chosen_cell, "attempts"),
            "days_replayed": opt(chosen_cell, "days"),
            "basis": (
                "mean daily lower-bound $ x 252 US sessions on the fixed $3,000 "
                "research reserve; simple rate, not a CAGR; no capacity claim"
            ),
            "no_cagr": True,
        },
        "_lineage_files": {
            "results.json": files["results.json"],
            "contract.json": files["contract.json"],
            "signals_parquet": lane_dir / "signals.parquet",
            "daily_validation_npz": lane_dir / "daily_validation.npz",
            "daily_confirmation_npz": lane_dir / "daily_confirmation.npz",
            "producer_snapshot_py": lane_dir / "producer_snapshot.py",
        },
        "_lineage_dirs": {
            "quote_audit": lane_dir / "quote_audit",
            "collect_parts": lane_dir / "collect_parts",
        },
    }


# --------------------------------------------------------------------------- #
# 4) micro payoff: supervised 5s/15s quote-state heads over one-second labels
# --------------------------------------------------------------------------- #
MICRO_CELL = (
    ("period", "period"),
    ("horizon_seconds", "horizon_seconds"),
    ("threshold_fraction", "threshold"),
    ("cost_bps_residual", "cost_bps_residual"),
    ("n_days", "n_days"),
    ("n_traded_days", "n_traded_days"),
    ("n_signals", "n_signals"),
    ("n_states", "n_states"),
    ("attempts", "attempts"),
    ("fills", "fills"),
    ("n_known_fills", "n_known_fills"),
    ("n_unknown_fills", "n_unknown_fills"),
    ("coverage_unknown_days", "coverage_unknown_days"),
    ("skips", "skips"),
    ("mean_daily_net_usd", "mean_daily_net_usd"),
    ("mean_daily_net_known_complete_usd", "mean_daily_net_known_complete_usd"),
    ("n_known_complete_days", "n_known_complete_days"),
    ("mean_daily_net_annualized_usd", "mean_daily_net_annualized_usd"),
    ("annualization", "annualization"),
    ("known_fills_per_day", "known_fills_per_day"),
    ("mean_net_known_fill_usd", "mean_net_known_fill_usd"),
    # native source is a FRACTION of the $250 order budget (the producer renamed its
    # mean_net_known_fill_pct mislabel to mean_net_known_fill_fraction, same value,
    # no x100); the canonical field keeps the fraction name and reads straight through
    ("mean_net_known_fill_fraction", "mean_net_known_fill_fraction"),
    ("known_win_rate_fraction", "known_win_rate"),
    ("known_profit_factor", "known_profit_factor"),
    ("worst_known_fill_usd", "worst_known_fill_usd"),
    ("best_known_fill_usd", "best_known_fill_usd"),
    ("incremental_legs", "incremental_legs"),
    ("positive_months", "positive_months"),
    ("months", "months"),
    ("underpowered", "underpowered"),
    ("research_book_usd", "research_book"),
)
MICRO_HEAD = (
    ("horizon_seconds", "horizon_seconds"),
    ("train_rows", "train_rows"),
    ("train_days", "train_days"),
    ("train_day_min", "train_day_min"),
    ("train_day_max", "train_day_max"),
    ("sampled_every_seconds", "sampled_every_seconds"),
    ("weight_rule", "weight_rule"),
    ("label_unclipped", "label_unclipped"),
    ("label_clip_for_fit", "label_clip_for_fit"),
)


def micro_cell_row(key: str, cell: dict) -> dict:
    out = {"cell": key}
    out.update(row(cell, MICRO_CELL))
    boot = opt(cell, "day_bootstrap_ci95_usd") or {}
    out["day_bootstrap_ci95_usd"] = ci95(boot)
    out["day_bootstrap_p_gt_zero"] = opt(boot, "p_gt_zero")
    return out


def micro_funded_mean_check(rows: list[dict]) -> None:
    """mean_net_known_fill_usd is the funded mean of the SAME fills as the fraction:
    usd == ORDER_BUDGET x fraction.  A fraction published as a percent (or scaled by
    100 anywhere) fails here instead of reaching the packet, e.g. 8 known fills at
    -0.004107 are -$1.0268 = -0.4107% per order, never -0.0041%."""
    for c in rows:
        usd = _num(c.get("mean_net_known_fill_usd"))
        frac = _num(c.get("mean_net_known_fill_fraction"))
        if usd is None and frac is None:
            continue  # no known fills: the producer emits both as null together
        if usd is None or frac is None or abs(usd - MICRO_ORDER_BUDGET_USD * frac) > 1e-6:
            raise DriftError(
                f"micro cell {c.get('cell')}: mean_net_known_fill_usd={usd} is not "
                f"{MICRO_ORDER_BUDGET_USD:g} x mean_net_known_fill_fraction={frac}; the "
                "per-order mean is a FRACTION of the $250 order budget and is never "
                "multiplied by 100, and a results.json without the renamed fraction "
                "field is a stale pre-contract-repair schema"
            )


def condense_micro(lane_dir: Path, files: dict) -> dict:
    r = load_json(files["results.json"])
    late = req(r, "late", "results.json")
    for cost in ("0", "10", "25", "100"):
        if cost not in late:
            raise DriftError(f"late ladder missing residual {cost}")
    prov = req(r, "provenance", "results.json")
    fit = opt(r, "fit") or {}
    heads = {
        str(h): row(rep, MICRO_HEAD)
        for h, rep in fit.items()
        if h.isdigit() and isinstance(rep, dict)
    }
    fit_block = {k: v for k, v in fit.items() if not k.isdigit()}
    val_rows = [
        micro_cell_row(k, v) for k, v in req(r, "validation_surface", "results.json").items()
    ]
    late_rows = [
        dict(micro_cell_row(f"residual_{k}", v), frozen_chosen=opt(v, "frozen_chosen"))
        for k, v in late.items()
    ]
    micro_funded_mean_check(val_rows + late_rows)
    return {
        "program": "alpha_micro_payoff",
        "output_root": str(lane_dir),
        "summary_file": str(files["results.json"]),
        "producer_status": "DISCOVERY-NOT-VALIDATED",
        "verdict": req_str(r, "verdict", "results.json"),
        "chosen": opt(r, "chosen"),
        "selection": req(r, "selection", "results.json"),
        "fit_head_reports": heads,
        "fit_block": fit_block,
        "validation_cell_rows": val_rows,
        "validation_chosen_view": opt(r, "validation_chosen_view"),
        "late_cell_rows": late_rows,
        "late_surface_meta": req(r, "late_surface_meta", "results.json"),
        "assumptions": req(r, "assumptions", "results.json"),
        "period_days": req(r, "period_days", "results.json"),
        "coverage": {
            "coverage_epoch": opt(prov, "coverage_epoch"),
            "coverage_epoch_source": opt(prov, "coverage_epoch_source"),
        },
        "producer_sha256_reported_in_artifact": opt(prov, "producer_sha256"),
        "contract_sha256": opt(prov, "contract_sha256"),
        "model_file_sha256": opt(prov, "model_files"),
        "execution_model": (
            "one_second_quote_state_predictions_priced_at_actual_touch_residuals_0_10_25_100"
        ),
        "chosen_late_headline": {
            "residual_bps": 10,
            "cell": micro_cell_row("late_residual_10", late.get("10")),
            "basis": (
                "whole-book lower bound on the $750 micro book: coverage-unknown "
                "days charge the full $250 order budget; mean daily net x 252 is a "
                "simple research estimate, not a CAGR"
            ),
        },
        "_lineage_files": {
            "results.json": files["results.json"],
            "summary.json": files["summary.json"],
            "surface_validation.json": files["surface_validation.json"],
            "frozen_contract.json": files["frozen_contract.json"],
        },
        "_lineage_dirs": {
            "corpus": lane_dir / "corpus",
            "models": lane_dir / "models",
            "late": lane_dir / "late",
        },
    }


# --------------------------------------------------------------------------- #
# 5) the retained base-wave lead (referenced from the base packet, never copied)
# --------------------------------------------------------------------------- #
def condense_base_lead(base_path: Path) -> dict:
    p = load_json(base_path)
    s = req(req(p, "studies", "base packet"), "learned_sparse_extension", "base packet studies")
    rare = req(s, "rare_extension", "base packet study")
    chosen = req(rare, "chosen", "rare_extension")
    facts = req(s, "annual_ev_source_facts", "base packet study")
    derived = req(facts, "derived", "annual_ev_source_facts")
    quote = req(req(s, "quote_audits", "base packet study"), "confirmation_late", "quote_audits")
    late_by_cost = {
        str(rw.get("cost_bps")): {
            "mean_daily_lower_bound_book_pct": rw.get("mean_daily_lower_bound_book_pct"),
            "mean_net_known_fill_per_order_pct": rw.get("mean_net_known_fill_per_order_pct"),
            "n_known_fills": rw.get("n_known_fills"),
            "n_unknown_fills": rw.get("n_unknown_fills"),
            "n_days": rw.get("n_days"),
        }
        for rw in s.get("rows") or []
        if rw.get("period_role") == "confirmation" and rw.get("alternative") == "payoff_h60_thr0.03"
    }
    return {
        "program": "alpha_sparse_model_extension (base wave)",
        "base_packet_path": str(base_path),
        "base_packet_sha256": sha256_file(base_path),
        "base_packet_bytes": base_path.stat().st_size,
        "base_packet_status": req_str(p, "status", "base packet"),
        "base_packet_asof": req_str(p, "asof", "base packet"),
        "producer_status": req_str(rare, "status", "rare_extension"),
        "decision": req_str(s, "decision", "base packet study"),
        "chosen": chosen,
        "validation": {
            "mean_daily_lower_bound_fraction": chosen.get(
                "original_validation_mean_daily_lower_bound"
            ),
            "known_fills": chosen.get("original_known_fills"),
            "traded_days": chosen.get("original_traded_days"),
            "selection_cost_bps": chosen.get("selection_cost_bps"),
            "period_role": "validation",
            "headline_pct_of_3000_book": (opt(s, "headline") or {}).get("headline_value_pct"),
        },
        "late_by_cost": late_by_cost,
        "quote_audit_coverage": {
            "n_trades": quote.get("n_trades"),
            "n_quote_supported_pairs": quote.get("n_quote_supported_pairs"),
            "n_unknown_pairs": quote.get("n_unknown_pairs"),
            "coverage_pct": quote.get("coverage_pct"),
            "unknown_pct": quote.get("unknown_pct"),
            "status_counts": quote.get("status_counts"),
            "covered_mean_net_pct_by_residual_bps": quote.get("covered_mean_net_by_residual_bps"),
            "source": quote.get("source"),
            "source_sha256": quote.get("source_sha256"),
        },
        "annual_ev": {
            "inputs": req(facts, "inputs", "annual_ev_source_facts"),
            "derived": derived,
            "quote_covered_conditional": facts.get("quote_covered_conditional"),
        },
        "execution_model": "minute_open_proxy",
    }


# --------------------------------------------------------------------------- #
# edge inference and evidence-first lead ranking
# --------------------------------------------------------------------------- #
def _num(x):
    return float(x) if isinstance(x, (int, float)) else None


def infer_edge(
    numbers: dict, has_outputs: bool, empty_measurement: bool = False
) -> tuple[str, str]:
    """Absence of evidence is 'no_edge', never a zero and never a hidden promise."""
    if not has_outputs:
        return (
            "no_edge_no_completed_outputs",
            "no completed producer outputs were loaded; absence of evidence is "
            "treated as no edge, not as a measured zero",
        )
    if empty_measurement:
        return (
            "no_edge_inferred",
            "the measured block took no trades (zero filled attempts at the frozen "
            "view); the reported mean is an empty mean, not a measured zero",
        )
    supported = _num(numbers.get("supported_net_usd_per_year"))
    if supported is None:
        return (
            "no_edge_inferred",
            "no finite measured cell at the nominated headline configuration; "
            "inferred no edge, not a zero",
        )
    if supported > 0:
        return "measured_positive", "measured positive at the labelled basis"
    return "measured_nonpositive", "measured non-positive at the labelled basis"


RANK_FIELDS = (
    "supported_net_usd_per_year",
    "supported_basis",
    "whole_book_lb_usd_per_year",
    "covered_observed_usd_per_year",
    "conditional_whole_case_usd_per_year",
    "opportunity_fills_per_year",
    "opportunity_attempts_per_year",
    "n_known_fills",
    "n_unknown_fills",
    "n_days",
)


def ranking_entry(
    lane,
    alt,
    period_role,
    exec_model,
    cost_bps,
    book_usd,
    order_usd,
    numbers,
    coverage_fraction,
    counts,
    caveats,
    has_outputs=True,
    empty_measurement=False,
) -> dict:
    status, why = infer_edge(numbers, has_outputs, empty_measurement)
    out = {
        "rank": None,
        "program": lane,
        "alternative": alt,
        "period_role": period_role,
        "execution_model": exec_model,
        "cost_bps": cost_bps,
        "book_usd": book_usd,
        "order_usd": order_usd,
        "coverage_fraction_supported": coverage_fraction,
        "edge_status": status,
        "edge_status_reason": why,
        "caveats": caveats,
    }
    out.update({k: numbers.get(k) for k in RANK_FIELDS})
    # lane-specific basis / financial-type annotations ride along; RANK_FIELDS above
    # stays the uniform numeric contract every entry must carry
    out.update({k: v for k, v in numbers.items() if k not in out})
    out.update(
        {
            k: counts.get(k)
            for k in (
                "opportunity_fills_per_year",
                "opportunity_attempts_per_year",
                "n_known_fills",
                "n_unknown_fills",
                "n_days",
            )
        }
    )
    return out


def _rank_key(e: dict):
    whole, supported = (
        _num(e.get("whole_book_lb_usd_per_year")),
        _num(e.get("supported_net_usd_per_year")),
    )
    # Flags rank a positive WHOLE-BOOK measurement above a positive covered-only
    # figure, and the ranking MAGNITUDE is the labelled SUPPORTED figure: a whole-book
    # coding bound (UNKNOWN legs charged a full unit loss) never sets the magnitude, so
    # the stop program's -$38k bound cannot outrank measured covered economics.  The
    # bound stays on the row, labelled, as evidence.  Subset means and unpriced
    # notional never enter the key.  Flags for a DESCENDING sort:
    return (
        1 if (whole or 0) > 0 else 0,
        1 if (supported or 0) > 0 else 0,
        supported if supported is not None else (whole or 0.0),
        _num(e.get("opportunity_fills_per_year")) or 0.0,
    )


def build_ranking(base_lead: dict, programs: dict) -> dict:
    entries: list[dict] = []
    d = _num(
        ((base_lead.get("annual_ev") or {}).get("derived") or {}).get(
            "annual_ev_usd_before_opex_and_tax"
        )
    )
    fills = _num(((base_lead.get("annual_ev") or {}).get("derived") or {}).get("fills_per_year"))
    q = base_lead.get("quote_audit_coverage") or {}
    entries.append(
        ranking_entry(
            "retained_base_h60",
            "h60|thr0.03 (fixed rare selection, weights frozen)",
            "late_2025_02_to_2026_05",
            "minute_open_proxy",
            100.0,
            3000.0,
            1000.0,
            {
                "supported_net_usd_per_year": d,
                "supported_basis": (
                    "whole-book late block: 143 known fills, 0 UNKNOWN, so the "
                    "known-fill annual EV equals the whole-book lower bound"
                ),
                "whole_book_lb_usd_per_year": d,
            },
            (_num(q.get("coverage_pct")) / 100.0) if q.get("coverage_pct") is not None else None,
            {
                "opportunity_fills_per_year": fills,
                "n_known_fills": 143,
                "n_unknown_fills": 0,
                "n_days": 332,
            },
            [
                "DISCOVERY-NOT-VALIDATED; late block previously explored, not pristine",
                "quote audit covers only 77/143 pairs; 66 UNKNOWN are unmeasured, not cash",
            ],
        )
    )

    fr = programs.get("sparse_execution_frontier")
    if fr:
        cell = (fr.get("cells_of_record") or {}).get("headline_primary_evidence") or {}
        entries.append(
            ranking_entry(
                "sparse_execution_frontier",
                "h60 trades priced at 250ms as-of quote, $1000",
                "late_2025_02_to_2026_05",
                "actual_ask_bid_touch_at_250ms_L1_capacity_checked",
                0.0,
                3000.0,
                1000.0,
                {
                    "supported_net_usd_per_year": cell.get("annual_observed_covered_usd_per_year"),
                    "supported_basis": (
                        "COVERED subset only: quote-supported pairs at the "
                        "measured touch; UNKNOWN legs are unmeasured, never zero"
                    ),
                    "covered_observed_usd_per_year": cell.get(
                        "annual_observed_covered_usd_per_year"
                    ),
                    "whole_book_lb_usd_per_year": None,
                    "conditional_whole_case_usd_per_year": cell.get(
                        "annual_conditional_whole_case_usd_per_year_if_unknown_matched_covered"
                    ),
                },
                _num(cell.get("coverage_fraction_supported")),
                {
                    "opportunity_fills_per_year": cell.get("annual_trades_per_year_whole_case"),
                    "n_known_fills": cell.get("n_l1_supported"),
                    "n_unknown_fills": cell.get("n_unknown"),
                    "n_days": 332,
                },
                [
                    "an execution-feasibility study of the retained trades, not a new strategy",
                    "top-of-book capacity only; L2 depth and exchange fills are unmeasured",
                    "conditional whole-case figure requires UNKNOWN legs to match covered ones: "
                    "NOT confirmed",
                ],
                empty_measurement=not _num(cell.get("n_l1_supported")),
            )
        )

    ex = programs.get("sparse_exit_management")
    if ex:
        h = ex.get("chosen_policy_late_headline") or {}
        sel = ex.get("selection") or {}
        covered, bound = _num(h.get("covered_ev_usd_per_year")), _num(
            h.get("whole_lb_usd_per_year")
        )
        entries.append(
            ranking_entry(
                "sparse_exit_management",
                f"{h.get('policy')} vs hold60m control @+{h.get('residual_bps')}bps",
                "late_2025_02_to_2026_05",
                "actual_nbbo_touch_at_250ms_arrival_gap_aware_stops",
                h.get("residual_bps"),
                3000.0,
                1000.0,
                {
                    # the supported figure is the MEASURED covered known-fill subset;
                    # the whole-book figure below is a coding bound that charges every
                    # UNKNOWN exit a full $1,000 unit loss and is never supported income
                    "supported_net_usd_per_year": covered,
                    "supported_basis": (
                        "COVERED known-fill subset at the chosen residual (measured "
                        "fills only); the whole-book figure is a coding bound that "
                        "charges UNKNOWN exits a full $1,000 unit loss, so it is "
                        "retained as evidence and never read as supported income"
                    ),
                    "supported_financial_type": "measured_covered_known_subset",
                    "whole_book_lb_usd_per_year": bound,
                    "whole_book_lb_financial_type": (
                        "coding_bound: UNKNOWN exits charged a full $1,000 unit loss; "
                        "not observed PnL, not an expected loss and not supported income"
                    ),
                    "covered_observed_usd_per_year": covered,
                },
                None,
                {
                    "opportunity_fills_per_year": _num(h.get("observed_known_fills_per_year")),
                    "n_days": h.get("calendar_days"),
                },
                [
                    "chosen on the 2023 validation calendar lower bound only, frozen before any "
                    "late outcome: " + str(sel.get("objective")),
                    "UNKNOWN exits dominate the whole-book bound, so a worse covered "
                    "subset does NOT "
                    "invalidate the retained base (the base is untouched and reproduced)",
                    "the hold60m control prices exits resting-until-next-regular-quote "
                    "(known counts depend on data support), not the original instant-quote "
                    "protocol (77 known); the two protocols are annotated, never blended",
                    "the whole-book bound (UNKNOWN legs charged a full unit) is labelled a coding "
                    "bound, never reranked as measured negative expected portfolio income",
                ],
            )
        )

    dl = programs.get("sparse_daily")
    if dl:
        h = dl.get("chosen_view_late_headline") or {}
        r = next(
            (
                x
                for x in dl.get("confirmation_cell_rows") or []
                if x.get("view") == h.get("view") and x.get("cost_bps") == 100
            ),
            {},
        )
        days, fills, att = (
            _num(r.get("days_replayed")),
            _num(r.get("fills")),
            _num(r.get("attempts")),
        )
        entries.append(
            ranking_entry(
                "sparse_daily",
                f"{h.get('view')} (frozen h60 model, re-entry cadence)",
                "late_2025_02_to_2026_05",
                "minute_open_proxy_round_trip_fee_both_legs",
                100.0,
                3000.0,
                1000.0,
                {
                    "supported_net_usd_per_year": _num(h.get("dollars_per_year_252_usd")),
                    "supported_basis": (
                        "whole-book daily lower bound $ x 252 sessions on the "
                        "fixed $3,000 reserve (UNKNOWN exits and pending slots "
                        "charge a full unit, never cash); not a CAGR"
                    ),
                    "whole_book_lb_usd_per_year": _num(h.get("dollars_per_year_252_usd")),
                },
                None,
                {
                    "opportunity_fills_per_year": (fills / days * 252.0)
                    if fills and days
                    else None,
                    "opportunity_attempts_per_year": (att / days * 252.0) if att and days else None,
                    "n_known_fills": r.get("known_fills"),
                    "n_unknown_fills": r.get("unknown_fills"),
                    "n_days": r.get("days_replayed"),
                },
                [
                    "same stored h60 model, no refit and no HPO; only cadence differs",
                    "minute-open proxy with a round-trip fee on both legs; no spread model and "
                    "no quote latency in the replay (the quote audit is a separate input)",
                    "a repeat cadence adds frequency; added legs are valued per added fill in "
                    "confirmation_incremental_vs_first_attempt, never as blind churn",
                ],
                empty_measurement=not fills,
            )
        )

    mi = programs.get("micro_payoff")
    if mi:
        cells = mi.get("late_cell_rows") or []
        cell = next((c for c in cells if c.get("cost_bps_residual") == 10), {})
        chosen = mi.get("chosen") or {}
        fd, ns = _num(cell.get("known_fills_per_day")), _num(cell.get("n_signals"))
        empty = not _num(cell.get("fills"))
        entries.append(
            ranking_entry(
                "micro_payoff",
                (
                    f"h{chosen.get('horizon')}s|thr{chosen.get('threshold')}"
                    if chosen
                    else "no finite chosen cell"
                ),
                "late_2025_09_to_2026_05",
                "one_second_quote_state_predictions_priced_at_actual_touch_residuals",
                10.0,
                750.0,
                250.0,
                {
                    "supported_net_usd_per_year": _num(cell.get("mean_daily_net_annualized_usd")),
                    "supported_basis": (
                        "whole-book lower bound on the $750 micro book "
                        "(coverage-unknown days charge the full $250 order), "
                        "mean daily net x 252, simple research estimate"
                    ),
                    "whole_book_lb_usd_per_year": _num(cell.get("mean_daily_net_annualized_usd")),
                },
                (_num(cell.get("n_known_fills")) / ns) if ns else None,
                {
                    "opportunity_fills_per_year": (fd * 252.0) if fd is not None else None,
                    "n_known_fills": cell.get("n_known_fills"),
                    "n_unknown_fills": cell.get("n_unknown_fills"),
                    "n_days": cell.get("n_days"),
                },
                [
                    "supervised 5s/15s heads on past-only one-second quote/order-flow states",
                    "threshold ladder .001/.003/.01 priced at 0/10/25/100bps residuals; 10bps is "
                    "the selection touch, never an automatic barrier",
                    "cash reuse assumes an eligible margin account, not a $750 cash account",
                    "a surface where the frozen view takes zero trades is an empty mean, not a "
                    "measured zero; the traded views of the same surface stay in "
                    "validation_cell_rows",
                ],
                empty_measurement=empty,
            )
        )

    for lane in LANES:
        if lane not in programs:
            entries.append(
                ranking_entry(
                    lane,
                    "absent",
                    "n/a",
                    "n/a",
                    None,
                    None,
                    None,
                    {},
                    None,
                    {},
                    [],
                    has_outputs=False,
                )
            )
    entries.sort(key=_rank_key, reverse=True)
    for i, e in enumerate(entries, 1):
        e["rank"] = i
    return {
        "objective": (
            "evidence-first: highest SUPPORTED net dollars per year at the "
            "program's own tested sizing, with opportunity frequency beside it; "
            "subset means, unpriced notional and hypothetical conditional "
            "figures never set the rank"
        ),
        "sort_key": (
            "(positive whole-book measurement first, then a positive supported figure, "
            "then the labelled supported magnitude -- never a whole-book coding bound "
            "such as the stop program's UNKNOWN-charged figure -- and finally "
            "opportunity_fills_per_year); entries with no positive measured figure "
            "sort last and are labelled no_edge"
        ),
        "entries": entries,
    }


# --------------------------------------------------------------------------- #
# static evidence policy (unit, cost, coverage and live caveats)
# --------------------------------------------------------------------------- #
UNIT_CONVENTIONS = {
    "fraction_fields": (
        "every *_fraction / *_return field is a fraction of the tested "
        "book or order (0.01 = 1%); *_pct fields are percent and are never "
        "multiplied again"
    ),
    "micro_per_order_mean": (
        "micro_payoff cells carry mean_net_known_fill_fraction = the per-order "
        "net as a FRACTION of the $250 order budget, beside mean_net_known_fill_usd "
        "= ORDER_BUDGET x the same fraction (8 known fills at -0.004107 is -$1.0268 "
        "= -0.4107% per order); the producer's retired mean_net_known_fill_pct "
        "mislabel has no successor and no micro cell has a percent field"
    ),
    "dollar_fields": (
        "*_usd / *_usd_per_* fields are dollars at the tested sizing; "
        "order_usd and book_usd travel with every ranking row"
    ),
    "annualization": (
        "*_per_year / annual_* figures are simple mean-per-period x 252 US "
        "equity sessions; never compounded, never a CAGR, and a daily-reset "
        "research book does not self-finance"
    ),
    "bps_fields": "*_bps fields are round-trip basis points charged on fill notional",
    "counts_fields": "n_* / *_count fields are counts; UNKNOWN outcomes are never cash",
    "time_fields": "horizon_seconds / *_minutes fields; arrival latencies are milliseconds",
}

EVIDENCE_RULES = {
    "primary_vs_scenario": (
        "the 250ms/2s as-of-quote PRIMARY rule is the only execution "
        "evidence; the 15s stale-leg ladder is a corroborated SCENARIO "
        "with explicit price/depth/timestamp risk and never widens the "
        "primary rule"
    ),
    "covered_vs_whole": (
        "a covered (quote-supported) subset mean is not a whole-portfolio "
        "estimate; whole-book lower bounds charge UNKNOWN outcomes a full "
        "unit loss and are labelled whole_book"
    ),
    "actual_costs_vs_rulers": (
        "residuals 0/10/25 (50 in the frontier ladder) are ACTUAL "
        "quoted-touch diagnostics; 100/150/200bps minute-proxy "
        "charges are bookkeeping ladders; no arbitrary ruler is an "
        "automatic veto"
    ),
    "baseline_vs_improved_selection": (
        "the retained rare h60 once-a-day selection is the "
        "frozen baseline (immutable 2021-22 weights); every "
        "new alternative (repeat cadence, stop policy, micro "
        "head) was selected on the 2023 validation block "
        "only, before its own late block was touched"
    ),
    "risk_and_tail": (
        "day-block bootstrap CIs, worst known fills, stop-through gap "
        "diagnostics and win/loss transitions are reported beside every "
        "mean; no median or tail deletion is applied anywhere"
    ),
    "no_cagr_from_reset_book": (
        "annualization is a simple rate on the fixed research "
        "book; the daily-reset ledger does not compound and is not "
        "a self-financing equity curve"
    ),
}

DATA_REQUIREMENTS = {
    "specific_gaps": [
        "L2 depth beyond displayed top-of-book: capacity is measured on L1 only, so an L1 "
        "shortfall at a notional is UNKNOWN at that size, never 'cannot fill'",
        "actual exchange fill sizes and fill flags: no order/fill telemetry exists in the "
        "research data, so a quote touch is not a fill guarantee",
        "missing historical acquisitions: a few legs were never acquired and stay UNKNOWN "
        "unless a read-only historical fetch is run",
        "minute-bar feature publication latency: unmeasured, which is why arrival-latency "
        "sensitivity is reported instead of assumed away",
    ],
    "not_a_strategy_falsification": (
        "each gap bounds what can be claimed about execution; "
        "none of them falsifies the underlying signal, and no "
        "gap is used to delete the family"
    ),
}

LIVE_CAVEATS = {
    "live_feed": (
        "the primary live feed is the SIP consolidated tape; the official live "
        "full-market plan costs $99/month ($1,188/year) and a 24/7 plan is not "
        "justified by this RTH-only research"
    ),
    "feed_opex_source": SIP_OPEX_SOURCE,
    "publication_latency": (
        "minute bars can publish after the clock, so a 250ms as-of "
        "quote is baseline evidence, not an operationally guaranteed "
        "arrival; later arrivals are the assumption actually tested"
    ),
    "broker_account": (
        "margin-style cash reuse assumes an eligible margin account; the "
        "$3,000 / $750 research books are book constructs, no financing is "
        "netted anywhere, and no broker account is implied or connected"
    ),
}

ANNUAL_EV_CONVENTIONS = {
    "sessions": "252 US equity sessions per year",
    "per_tested_sizing": (
        "every annual figure is computed at the tested sizing carried on "
        "its row (order_usd x book_usd); it is never rescaled to a "
        "larger book without a capacity measurement"
    ),
    "simple_not_cagr": "mean per period x 252, never compounded",
    "operating_cost_source": SIP_OPEX_SOURCE,
    "unknown_outcomes": (
        "UNKNOWN outcomes are charged at their producer's full-unit lower "
        "bound, never dropped and never treated as cash"
    ),
    "no_guaranteed_profit": (
        "these are research estimates on previously explored blocks; "
        "no guaranteed or projected profit is claimed anywhere"
    ),
}


def _fmt(x, spec="+.2f"):
    return "n/a" if x is None else format(float(x), spec)


def _pctf(x):
    return "n/a" if x is None else f"{float(x) * 100:.1f}%"


def render_table(rows: list[dict]) -> str:
    head = (
        f"{'lane':<26}{'state':<10}{'edge':<24}{'supported $/yr (basis)':>22}"
        f"{'fills/yr':>10}{'coverage':>10}  headline"
    )
    out = [head, "-" * len(head)]
    for r in rows:
        out.append(
            f"{r['lane']:<26}{r['state']:<10}{r['edge']:<24}{r['supported']:>22}"
            f"{r['fills']:>10}{r['coverage']:>10}  {r['headline']}"
        )
    return "\n".join(out)


def build_progress(states: dict, complete: bool) -> dict:
    return {
        "kind": "RESEARCH_ORCHESTRATION_PROGRESS_NOT_COMPLETION",
        "asof": ASOF,
        "all_four_programs_complete": complete,
        "programs": states,
        "note": (
            "progress checkpoint only; the final packet "
            "factory/artifacts/alpha_night_extensions.json is written exclusively "
            "when all four programs have completed actual outputs"
        ),
    }


def lane_headline(lane: str, p: dict | None, base_lead: dict | None):
    """(headline, supported $/yr, fills/yr, coverage) for the compact CLI table."""
    if lane == "base":
        if not base_lead:
            return ("base packet unreadable", None, None, None)
        d = (base_lead.get("annual_ev") or {}).get("derived") or {}
        cov = _num((base_lead.get("quote_audit_coverage") or {}).get("coverage_pct"))
        return (
            "late@100bps whole book (77/143 quote-covered)",
            d.get("annual_ev_usd_before_opex_and_tax"),
            d.get("fills_per_year"),
            (cov / 100.0) if cov is not None else None,
        )
    if p is None:
        return ("no completed outputs", None, None, None)
    if lane == "sparse_execution_frontier":
        c = (p["cells_of_record"] or {}).get("headline_primary_evidence") or {}
        return (
            "late143 primary_2s 250ms res0 $1000 (covered)",
            c.get("annual_observed_covered_usd_per_year"),
            c.get("annual_trades_per_year_whole_case"),
            c.get("coverage_fraction_supported"),
        )
    if lane == "sparse_exit_management":
        h = p["chosen_policy_late_headline"]
        return (
            f"{h['policy']} late @+{h['residual_bps']}bps covered-known subset "
            "(whole-book UNKNOWN-charged coding bound is not supported income)",
            h["covered_ev_usd_per_year"],
            h["observed_known_fills_per_year"],
            None,
        )
    if lane == "sparse_daily":
        h = p["chosen_view_late_headline"]
        r = next(
            (
                x
                for x in p["confirmation_cell_rows"]
                if x["view"] == h["view"] and x["cost_bps"] == 100
            ),
            {},
        )
        days = r.get("days_replayed")
        return (
            f"{h['view']} late @100bps (whole book)",
            h["dollars_per_year_252_usd"],
            (r.get("fills") / days * 252.0) if r.get("fills") and days else None,
            None,
        )
    if lane == "micro_payoff":
        c = next((x for x in p["late_cell_rows"] if x["cost_bps_residual"] == 10), {})
        fd = c.get("known_fills_per_day")
        return (
            "chosen late @10bps (whole book)",
            c.get("mean_daily_net_annualized_usd"),
            (fd * 252.0) if fd is not None else None,
            None,
        )
    return ("no completed outputs", None, None, None)


CONDENSERS = {
    "sparse_execution_frontier": condense_frontier,
    "sparse_exit_management": condense_exit,
    "sparse_daily": condense_daily,
    "micro_payoff": condense_micro,
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="read-only program outputs root (default: %(default)s)",
    )
    ap.add_argument(
        "--base",
        type=Path,
        default=DEFAULT_BASE,
        help="base-wave packet referenced, never copied (default: %(default)s)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="final consolidated extensions JSON (default: %(default)s)",
    )
    ap.add_argument(
        "--progress",
        type=Path,
        default=DEFAULT_PROGRESS,
        help="progress checkpoint path (default: %(default)s)",
    )
    ap.add_argument(
        "--no-progress", action="store_true", help="do not write the progress checkpoint"
    )
    args = ap.parse_args(argv)

    states: dict[str, dict] = {}
    programs: dict[str, dict] = {}
    problems: list[str] = []
    drift: list[str] = []
    for lane, spec in LANES.items():
        lane_dir, candidates = resolve_lane(args.root, spec)
        if lane_dir is None:
            problems.append(
                f"{lane}: no complete output root among {list(spec['roots'])} "
                f"under {args.root}; candidates: {candidates}"
            )
            states[lane] = {
                "state": "missing",
                "detail": problems[-1],
                "candidate_roots": candidates,
            }
            continue
        try:
            files = {f: lane_dir / f for f in spec["required"]}
            programs[lane] = CONDENSERS[lane](lane_dir, files)
            states[lane] = {
                "state": "complete",
                "output_root": str(lane_dir),
                "summary": spec["summary"],
                "candidate_roots": candidates,
            }
        except (IncompleteError, DriftError) as exc:
            tag = "incomplete" if isinstance(exc, IncompleteError) else "schema_drift"
            problems.append(f"{lane}: {exc}")
            if isinstance(exc, DriftError):
                drift.append(f"{lane}: {exc}")
            states[lane] = {"state": tag, "detail": str(exc), "output_root": str(lane_dir)}

    base_lead = None
    try:
        base_lead = condense_base_lead(args.base)
    except DriftError as exc:
        drift.append(f"base packet: {exc}")
        problems.append(f"base packet: {exc}")
    except IncompleteError as exc:
        problems.append(f"base packet: {exc}")

    if not args.no_progress:
        args.progress.parent.mkdir(parents=True, exist_ok=True)
        args.progress.write_text(
            json.dumps(build_progress(states, not problems and base_lead is not None), indent=1)
            + "\n"
        )

    ranking = build_ranking(base_lead or {}, programs) if (base_lead or programs) else None
    edge_by_lane = {e["program"]: e for e in ranking["entries"]} if ranking else {}

    def table_row(lane, state, headline, supported, fills, coverage, edge):
        return {
            "lane": lane,
            "state": state,
            "edge": edge,
            "supported": _fmt(supported),
            "fills": _fmt(fills, ".1f"),
            "coverage": _pctf(coverage),
            "headline": headline,
        }

    rows = [
        table_row(
            "retained_base_h60",
            "BASE",
            *lane_headline("base", None, base_lead),
            edge=edge_by_lane.get("retained_base_h60", {}).get("edge_status", ""),
        )
    ]
    for lane in LANES:
        p = programs.get(lane)
        rows.append(
            table_row(
                lane,
                states[lane]["state"],
                *lane_headline(lane, p, base_lead),
                edge=edge_by_lane.get(lane, {}).get("edge_status", "no_edge_no_completed_outputs"),
            )
        )
    print(render_table(rows))
    print()
    if base_lead is not None:
        print(
            f"base packet: {base_lead['base_packet_path']} "
            f"(sha256 {base_lead['base_packet_sha256'][:12]}, "
            f"{base_lead['base_packet_bytes']} bytes)"
        )
    for lane in LANES:
        p = programs.get(lane)
        if p is not None:
            print(f"{lane}: {p['summary_file']} (root {p['output_root']})")
    print()

    if problems:
        print("FATAL: refusing to emit a partial final packet. Blocking inputs:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return EXIT_DRIFT if drift else EXIT_MISSING

    packet = {
        "kind": KIND,
        "asof": ASOF,
        "status": "COMPLETE-4-OF-4-EXTENSIONS",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "generator": {
            "script": "factory/scripts/alpha_night_report.py",
            "command": (
                "uv run --no-sync python factory/scripts/alpha_night_report.py "
                "--root ~/alpha-data/open-search-v1 --base "
                "factory/artifacts/alpha_search_20261008.json --out "
                "factory/artifacts/alpha_night_extensions.json"
            ),
            "defaults": {
                "--root": str(DEFAULT_ROOT),
                "--base": str(DEFAULT_BASE),
                "--out": str(DEFAULT_OUT),
            },
            "script_sha256": sha256_file(Path(__file__).resolve()),
            "reads_only": True,
        },
        "unit_conventions": UNIT_CONVENTIONS,
        "evidence_rules": EVIDENCE_RULES,
        "base_packet_reference": {
            "path": base_lead["base_packet_path"],
            "sha256": base_lead["base_packet_sha256"],
            "bytes": base_lead["base_packet_bytes"],
            "status": base_lead["base_packet_status"],
            "asof": base_lead["base_packet_asof"],
            "note": "referenced by path + SHA only; the base blob is not copied here",
        },
        "retained_base_lead": base_lead,
        "extensions": {
            lane: {k: v for k, v in programs[lane].items() if not k.startswith("_lineage")}
            for lane in LANES
        },
        "lead_ranking": ranking,
        "annual_ev_conventions": ANNUAL_EV_CONVENTIONS,
        "data_requirements": DATA_REQUIREMENTS,
        "live_caveats": LIVE_CAVEATS,
        "lineage": {
            "base_packet": {
                "path": base_lead["base_packet_path"],
                "sha256": base_lead["base_packet_sha256"],
            },
            "programs": {
                lane: {
                    "output_root": programs[lane]["output_root"],
                    "summary_file": programs[lane]["summary_file"],
                    "files": [
                        file_ref(p, n)
                        for n, p in (programs[lane].get("_lineage_files") or {}).items()
                    ],
                    "directories": [
                        dir_ref(p, n)
                        for n, p in (programs[lane].get("_lineage_dirs") or {}).items()
                    ],
                }
                for lane in LANES
            },
            "note": (
                "every consolidated number traces to these staged artifacts by path + "
                "SHA-256; bulky per-day and per-trade blobs stay in the program roots "
                "and are referenced, not embedded"
            ),
        },
        "goal_status": {
            "user_goal": (
                "best profitable top-gainer strategy, most work delegated, "
                "positive sparse h60 lead retained, profitable frequency "
                "scalping broadened"
            ),
            "status": "OPEN",
            "note": (
                "this packet is not a self-issued goal completion: every program here "
                "is DISCOVERY-NOT-VALIDATED research evidence on previously explored "
                "blocks; the quote audit of the repeat-cadence legs and the parent "
                "verification of every todo are still outstanding"
            ),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as fh:
        json.dump(packet, fh, indent=1, default=str)
        fh.write("\n")
    print(f"wrote {args.out} ({args.out.stat().st_size} bytes, sha256 {sha256_file(args.out)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
