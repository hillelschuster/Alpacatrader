#!/usr/bin/env python3
"""Aggregate every registered alpha-search study into ONE strict evidence packet.

The packet is the single consumer-facing artifact for the open-anchored top-gainer
research lane.  It is deliberately boring:

  * it only READS read-only producer output roots (JSON summaries + contracts);
  * it condenses nested per-day / per-trade blobs into flat metric tables that carry
    an explicit unit in the field name, plus the source path, the source file SHA-256
    and the producer script SHA-256 for every family;
  * it refuses to emit anything unless EVERY registered study is complete.  There is
    no "most studies" mode and no silently omitted family.

Unit semantics are load-bearing and are encoded in the field names:
  mean_net_known_fill_*      PER-ORDER net return (never a capital / per-day return)
  mean_daily_lower_bound_*   PER-DAY fraction of the research book (book_usd on row)
  *_ratio                    unitless ratio (profit factor)
  n_*                        counts
  *_usd                      dollars
UNKNOWN exits are never cash and never dropped: they stay as n_unknown_fills and keep
the -100%-of-order lower bound.

Usage:
    uv run --no-sync python factory/scripts/alpha_search_report.py
    uv run --no-sync python factory/scripts/alpha_search_report.py --root DIR --out FILE
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = Path.home() / "alpha-data" / "open-search-v1"
DEFAULT_OUT = ROOT / "factory" / "artifacts" / "alpha_search_20261008.json"
ASOF = "2026-10-08"

PROTECTED_UNREAD = ["2024", "2025-01", "2026-06", "2026-07", "2026-08"]
STALE_WORKTREE = Path("/home/hillel/algo projects/worktrees/Alpacatrader/basket-phase2-f1")

MINUTE_PROXY = "minute_open_proxy"
TOUCH_250MS = "actual_ask_bid_touch_at_250ms_quote_capacity_checked"
NO_HIGH_CREDIT = "next_actual_minute_open_no_high_credit"
ACTUAL_TOUCH = "actual_ask_at_signal_plus_250ms_then_bid_at_target_or_next_regular_quote"
RT_ON_FILL = "round_trip_bps_charged_on_fill_notional"
RESIDUAL_ON_TOUCH = "residual_bps_on_top_of_actual_ask_bid_touch"


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def pct(x, nd: int = 4):
    """Fraction -> percent.  None stays None (never fabricated)."""
    return None if x is None else round(float(x) * 100.0, nd)


def mtime_iso(path: Path):
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()
    except OSError:
        return None


def size_bytes(path: Path):
    try:
        return path.stat().st_size
    except OSError:
        return None


def _avail_gib(path: str):
    try:
        st = os.statvfs(path)
        return round(st.f_bavail * st.f_frsize / (1024**3), 2)
    except OSError:
        return None


class MissingStudyError(Exception):
    pass


def load_json(path: Path):
    with path.open() as fh:
        return json.load(fh)


def require(root: Path, rel: str) -> Path:
    p = root / rel
    if not p.is_file():
        raise MissingStudyError(f"{p}")
    return p


def decoded(v):
    """Accept either a JSON-encoded string or an already-decoded object."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return None
    return v if isinstance(v, (dict, list)) else None


def optional(root: Path, rel: str):
    p = root / rel
    return p if p.is_file() else None


# --------------------------------------------------------------------------- #
# canonical metric row
# --------------------------------------------------------------------------- #
ROW_FIELDS = (
    "row_key",
    "study",
    "family",
    "alternative",
    "qualification",
    "horizon",
    "horizon_unit",
    "threshold",
    "period",
    "period_role",
    "is_out_of_fit",
    "out_of_fit_pristine_holdout",
    "cost_bps",
    "cost_basis",
    "execution_model",
    "fill_convention",
    "n_days",
    "n_attempts",
    "n_fills",
    "n_known_fills",
    "n_unknown_fills",
    "n_no_fill_or_skips",
    "n_traded_days",
    "n_fill_days",
    "n_months",
    "n_positive_months",
    "mean_net_known_fill_per_order_pct",
    "mean_daily_lower_bound_book_pct",
    "daily_se_book_pct",
    "ci95_lower_bound_book_pct",
    "known_win_rate_pct",
    "known_profit_factor_ratio",
    "worst_known_fill_per_order_pct",
    "best_known_fill_per_order_pct",
    "total_lower_bound_pnl_usd",
    "book_usd",
    "order_usd",
    "coverage_unknown_days",
    "coverage_worst_case_mean_daily_book_pct",
    "known_complete_day_mean_lower_bound_book_pct",
    "quote_supported",
    "finite",
    "selection_eligible",
    "study_specific",
    "notes",
)


HEADLINE_FIELDS = (
    "study",
    "verdict",
    "headline_metric",
    "headline_value_pct",
    "headline_unit",
    "n_known_fills",
    "n_unknown_fills",
    "n_days",
    "cost_bps",
    "period_role",
    "book_usd",
    "order_usd",
    "certified",
    "certification_blockers",
    "positive_regions",
    "all_known_negative",
    "profit_status",
)


def pick_pct(r: dict, base: str, *aliases) -> float | None:
    """Prefer an already-in-percent field; otherwise convert a fraction.

    Producers that emit BOTH ``mean_daily_lower_bound`` (fraction) and
    ``mean_daily_lower_bound_book_pct`` (percent) must not be multiplied by 100 twice,
    so the percent spelling always wins.  ``aliases`` covers the producer-specific
    suffixes (``_book_pct``, ``_per_order_pct``, ...) that the generic ``_pct`` /
    ``_percent`` probes would miss.
    """
    for name in (f"{base}_pct", f"{base}_percent", *aliases):
        v = r.get(name)
        if v is not None:
            return float(v)
    v = r.get(base)
    return None if v is None else round(float(v) * 100.0, 4)


def mrow(**kw) -> dict:
    unknown = set(kw) - set(ROW_FIELDS)
    if unknown:
        raise KeyError(f"unknown metric-row fields: {sorted(unknown)}")
    row = dict.fromkeys(ROW_FIELDS)
    row["notes"] = []
    row["study_specific"] = {}
    row["selection_eligible"] = False
    row.update(kw)
    return row


def proxy_metric_row(
    study,
    m,
    period_role,
    period=None,
    family=None,
    alternative=None,
    book=3000.0,
    order=1000.0,
    fills_are_known_only=False,
    extra=None,
):
    """Row builder for the minute-open-proxy families that share one metric shape
    (events / learned / sequence / proven_push).

    `fills_are_known_only=True` for producers whose `fills` counts ONLY known-exit
    trades (overnight); everywhere else `fills` already includes the UNKNOWN exits.
    Either way the emitted row carries n_fills = filled total (known + unknown) and
    n_known_fills = the known-exit subset.
    """
    boot = m.get("daily_lower_bound_bootstrap") or m.get("bootstrap_daily")
    ci95 = None
    if isinstance(boot, dict):
        lo = boot.get("ci95_low", boot.get("ci95_lo"))
        hi = boot.get("ci95_high", boot.get("ci95_hi"))
        if lo is not None and hi is not None:
            ci95 = [pct(lo), pct(hi)]
    kw = {
        "study": study,
        "family": family if family is not None else m.get("family"),
        "alternative": alternative,
        "period": period if period is not None else m.get("period", period_role),
        "period_role": period_role,
        "horizon": m.get("horizon"),
        "horizon_unit": "minutes",
        "cost_bps": m.get("cost_bps"),
        "cost_basis": RT_ON_FILL,
        "execution_model": MINUTE_PROXY,
        "fill_convention": NO_HIGH_CREDIT,
        "n_days": m.get("days"),
        "n_attempts": m.get("attempts"),
        "n_fills": (m.get("fills") + (m.get("unknown_fills") or 0))
        if fills_are_known_only
        else m.get("fills"),
        "n_known_fills": m.get("fills") if fills_are_known_only else m.get("known_fills"),
        "n_unknown_fills": m.get("unknown_fills"),
        "n_no_fill_or_skips": m.get("cash_or_slot_skips"),
        "n_traded_days": m.get("traded_days"),
        "n_months": m.get("months"),
        "n_positive_months": m.get("positive_months_lower_bound"),
        "mean_net_known_fill_per_order_pct": pct(m.get("mean_net_known_fill")),
        "mean_daily_lower_bound_book_pct": pct(m.get("mean_daily_lower_bound")),
        "daily_se_book_pct": pct(m.get("daily_se")),
        "ci95_lower_bound_book_pct": ci95,
        "known_win_rate_pct": pct(m.get("known_win_rate")),
        "known_profit_factor_ratio": m.get("known_profit_factor"),
        "worst_known_fill_per_order_pct": pct(m.get("worst_known_fill")),
        "book_usd": book,
        "order_usd": order,
        "finite": m.get("mean_net_known_fill") is not None,
    }
    row = mrow(**kw)
    notes = list(row["notes"])
    if m.get("execution_proxy_only"):
        notes.append("execution_proxy_only=true in the producer summary")
    if extra:
        notes.extend(extra)
    row["notes"] = notes
    return row


def monthly_rows(rows_src, study, period_role, cost_bps, book_usd=3000.0):
    """Condense a monthly dict/list into metric rows (never the raw daily blob)."""
    out = []
    if isinstance(rows_src, dict):
        for month in sorted(rows_src):
            v = rows_src[month]
            if not isinstance(v, dict):
                out.append(
                    {
                        "study": study,
                        "period_role": period_role,
                        "cost_bps": cost_bps,
                        "month": month,
                        "mean_lower_bound_book_pct": pct(v),
                        "book_usd": book_usd,
                    }
                )
                continue
            out.append(
                {
                    "study": study,
                    "period_role": period_role,
                    "cost_bps": cost_bps,
                    "month": month,
                    "days_replayed": v.get("days_replayed"),
                    "traded_days": v.get("traded_days"),
                    "fills": v.get("fills"),
                    "known_fills": v.get("known_fills"),
                    "unknown_fills": v.get("unknown_fills"),
                    "lower_bound_pnl_usd": v.get("lower_bound_pnl"),
                    "mean_lower_bound_book_pct": pct(v.get("mean_daily_lower_bound")),
                    "book_usd": book_usd,
                }
            )
    elif isinstance(rows_src, list):
        for v in rows_src:
            out.append(
                {
                    "study": study,
                    "period_role": period_role,
                    "cost_bps": cost_bps,
                    "month": v.get("month"),
                    "days_replayed": v.get("days_replayed"),
                    "traded_days": v.get("traded_days"),
                    "fills": v.get("fills"),
                    "known_fills": v.get("known_fills"),
                    "unknown_fills": v.get("unknown_fills"),
                    "lower_bound_pnl_usd": v.get("lower_bound_pnl"),
                    "mean_lower_bound_book_pct": pct(v.get("mean_daily_lower_bound")),
                    "book_usd": book_usd,
                }
            )
    return out


def pack_study(key, label, script, root, requires, extra_srcs, builder):
    """Load + hash one family, or raise MissingStudyError with the exact gap."""
    paths = {}
    for rel in requires:
        paths[rel] = require(root, rel)
    extra = {}
    for rel in extra_srcs:
        p = optional(root, rel)
        if p is not None:
            extra[rel] = p
    script_path = ROOT / script
    block = builder(root, paths)
    block.update(
        {
            "family": key,
            "label": label,
            "source": str(paths[requires[0]]),
            "source_sha256": sha256_file(paths[requires[0]]),
            "source_mtime_utc": mtime_iso(paths[requires[0]]),
            "extra_sources": {
                rel: {"path": str(p), "sha256": sha256_file(p), "bytes": size_bytes(p)}
                for rel, p in sorted({**paths, **extra}.items())
                if rel != requires[0]
            },
            "producer_script": script,
            "producer_script_sha256": sha256_file(script_path) if script_path.is_file() else None,
            "producer_script_mtime_utc": mtime_iso(script_path) if script_path.is_file() else None,
        }
    )
    # Grounded staleness flag: the artifacts were written by an OLDER producer than the one
    # currently on disk.  Both signals are measurements, not opinions about the results:
    #   (a) the sha the producer recorded inside its own artifact differs from the sha256 of
    #       the script currently in the repo (content-derived, the stronger signal);
    #   (b) the artifact file is older than the producer script (weaker: an unrelated touch
    #       also moves an mtime, which is why the two are reported separately).
    reported = block.get("producer_sha256_reported")
    disk_sha = block.get("producer_script_sha256")
    src_mt = paths[requires[0]].stat().st_mtime if paths[requires[0]].is_file() else None
    scr_mt = script_path.stat().st_mtime if script_path.is_file() else None
    sha_mismatch = bool(reported and disk_sha and reported != disk_sha)
    older_script = bool(scr_mt is not None and src_mt is not None and scr_mt > src_mt)
    block["stale_relative_to_current_producer"] = bool(sha_mismatch or older_script)
    block["stale_evidence"] = {
        "producer_sha_mismatch": sha_mismatch,
        "artifact_older_than_script": older_script,
        "producer_sha_reported_in_artifact": reported,
        "producer_sha_on_disk": disk_sha,
        "artifact_mtime_utc": block["source_mtime_utc"],
        "producer_script_mtime_utc": block["producer_script_mtime_utc"],
        "reasons": (
            [
                "producer_sha256 recorded inside the artifact differs from the sha256 of "
                "the script "
                f"currently on disk (reported={reported}, on_disk={disk_sha})"
            ]
            if sha_mismatch
            else []
        )
        + (
            [
                "the producer script was modified after the artifact was written "
                f"(script mtime {block['producer_script_mtime_utc']} > artifact mtime "
                f"{block['source_mtime_utc']})"
            ]
            if older_script
            else []
        ),
    }
    return block


# --------------------------------------------------------------------------- #
# panel
# --------------------------------------------------------------------------- #
def build_panel(root: Path, events_summary: dict) -> dict:
    import collections

    days_dir = root / "days"
    day_files = sorted(days_dir.glob("*.json")) if days_dir.is_dir() else []
    if not day_files:
        raise MissingStudyError(f"{days_dir}/*.json (panel day manifests)")
    by_period = collections.Counter()
    rows_by_period = collections.Counter()
    status = collections.Counter()
    total_rows = admitted = unfilled = unknown_exit_60 = missing_tapes = 0
    keys = set()
    for p in day_files:
        d = load_json(p)
        keys |= set(d)
        day = d["day"]
        per = (
            "train"
            if day < "2023-01-01"
            else "validation"
            if day < "2024-01-01"
            else "confirmation"
        )
        by_period[per] += 1
        rows_by_period[per] += d.get("rows", 0)
        status[d.get("status")] += 1
        total_rows += d.get("rows", 0)
        admitted += d.get("admitted_names", 0)
        unfilled += d.get("unfilled", 0)
        unknown_exit_60 += d.get("unknown_exit_60", 0)
        if d.get("missing_admitted_tapes"):
            missing_tapes += 1
    contract = optional(root, "contract.json")
    panel = {
        "root": str(root),
        "days_manifest_count": len(day_files),
        "panel_days": events_summary.get("panel_days", len(day_files)),
        "panel_rows": events_summary.get("panel_rows", total_rows),
        "rows_recomputed_from_day_manifests": total_rows,
        "panel_days_by_period": {
            "train": by_period.get("train", 0),
            "validation": by_period.get("validation", 0),
            "confirmation": by_period.get("confirmation", 0),
        },
        "rows_by_period": dict(rows_by_period),
        "producer_reported_days_by_period": events_summary.get("panel_days_by_period"),
        "days_with_status_not_built": {k: v for k, v in status.items() if k != "built"},
        "all_days_status_built": all(k == "built" for k in status),
        "admitted_name_days": admitted,
        "unfilled_intents": unfilled,
        "unknown_exit_60": unknown_exit_60,
        "days_with_missing_admitted_tapes": missing_tapes,
        "day_manifest_keys": sorted(keys),
        "consistent_with_producer_report": (
            events_summary.get("panel_days") == len(day_files)
            and events_summary.get("panel_rows") == total_rows
        ),
        "panel_contract_sha256": sha256_file(contract) if contract else None,
        "panel_producer_script": "factory/scripts/alpha_open_panel.py",
        "panel_producer_script_sha256": (
            load_json(day_files[0]).get("producer_sha256") if day_files else None
        ),
        "period_windows": {
            "train": "2021-02-01..2022-12-31",
            "validation": "2023-01-01..2023-12-31",
            "confirmation": (
                "2025-02-01..2026-05-31 (previously explored market periods, "
                "NOT a pristine holdout)"
            ),
        },
        "protected_unread": PROTECTED_UNREAD,
        "units": {
            "panel_rows": "causal observable symbol-minute states",
            "admitted_name_days": "distinct (day, ticker) admitted into the panel",
            "unfilled_intents": (
                "entry intents with no bar at the entry minute; cash retained, no fee"
            ),
            "unknown_exit_60": (
                "entry intents whose 60-minute exit open never printed; never zero-filled"
            ),
        },
    }
    return panel


# --------------------------------------------------------------------------- #
# events
# --------------------------------------------------------------------------- #
def build_events(root: Path, paths: dict) -> dict:
    s = load_json(paths["events/summary.json"])
    sel = s.get("selection", {})
    val = sel.get("validation_metrics", {})
    rows = []
    monthly = []

    train_surface = s.get("train_validation_surface", {}).get("train", {})
    val_surface = s.get("train_validation_surface", {}).get("validation_100bps", {})
    for alt in sorted(train_surface):
        m = dict(train_surface[alt].get("metrics", {}))
        r = proxy_metric_row(
            "events", m, "train", period=alt, family=m.get("family"), alternative=alt
        )
        r["row_key"] = f"events|train|{alt}|{m.get('cost_bps')}"
        r["selection_eligible"] = False
        rows.append(r)
    for alt in sorted(val_surface):
        m = dict(val_surface[alt].get("metrics", {}))
        r = proxy_metric_row(
            "events", m, "validation", period=alt, family=m.get("family"), alternative=alt
        )
        r["row_key"] = f"events|validation|{alt}|{m.get('cost_bps')}"
        r["selection_eligible"] = alt in sel.get("eligible_alternatives", [])
        rows.append(r)
        monthly += monthly_rows(
            m.get("monthly_mean_lower_bound"), "events", "validation", m.get("cost_bps")
        )
    for ctx_key in sorted(s.get("baseline_validation_context", {})):
        ctx = s["baseline_validation_context"][ctx_key]
        m = ctx.get("metrics", {})
        r = proxy_metric_row(
            "events", m, "validation", period=ctx_key, family=m.get("family"), alternative=ctx_key
        )
        r["row_key"] = f"events|context|{ctx_key}|{m.get('cost_bps')}"
        r["selection_eligible"] = False
        r["notes"] = ["context_only=true; baseline never selection-eligible"]
        rows.append(r)
    for cost_key in sorted(s.get("confirmation", {})):
        c = s["confirmation"][cost_key]
        m = c.get("metrics", {})
        r = proxy_metric_row(
            "events",
            m,
            "confirmation",
            period=c.get("alternative"),
            alternative=c.get("alternative"),
        )
        r["row_key"] = f"events|confirmation|{c.get('alternative')}|{c.get('cost_bps')}"
        r["is_out_of_fit"] = True
        r["out_of_fit_pristine_holdout"] = False
        r["selection_eligible"] = False
        rows.append(r)
        monthly += monthly_rows(
            m.get("monthly_mean_lower_bound"), "events", "confirmation", m.get("cost_bps")
        )

    diag = {}
    for fam, periods in s.get("diagnostics", {}).items():
        diag[fam] = {}
        for per, d in periods.items():
            diag[fam][per] = {
                "days_in_period": d.get("days_in_period"),
                "qualifications": d.get("qualifications"),
                "entries": d.get("entries"),
                "entries_filled_proxy": d.get("entries_filled_proxy"),
                "entries_unfilled_expired": d.get("entries_unfilled_expired"),
                "fill_rate_of_entries": d.get("fill_rate_of_entries"),
                "signals_per_day": d.get("signals_per_day"),
                "entry_conversion": d.get("entry_conversion"),
            }

    quote_audit = None
    qa = optional(root, "events/quote_audit_confirmation/summary.json")
    if qa is not None:
        quote_audit = quote_block(load_json(qa), qa, "events")

    headline_val = {
        "mean_daily_lower_bound_book_pct": pct(val.get("mean_daily_lower_bound")),
        "mean_net_known_fill_per_order_pct": pct(val.get("mean_net_known_fill")),
        "n_known_fills": val.get("known_fills"),
        "n_unknown_fills": val.get("unknown_fills"),
        "n_traded_days": val.get("traded_days"),
        "n_days": val.get("days"),
        "cost_bps": val.get("cost_bps"),
        "period_role": "validation",
    }
    all_late = [r for r in rows if r["period_role"] == "confirmation"]
    return {
        "headline": {
            "study": "events",
            "verdict": sel.get("status"),
            "headline_metric": "validation mean_daily_lower_bound at 100bps, chosen alternative "
            + str(sel.get("selected_alternative")),
            "headline_value_pct": headline_val["mean_daily_lower_bound_book_pct"],
            "headline_unit": "percent of the $3,000 research sub-book per day",
            **headline_val,
            "book_usd": 3000.0,
            "order_usd": 1000.0,
            "certified": False,
            "certification_blockers": [
                "minute-open execution proxy; no as-of side-aware quote audit for "
                "the chosen family",
                "confirmation window 2025-02..2026-05 was previously explored; "
                "NOT a pristine holdout",
            ],
            "positive_regions": [],
            "all_known_negative": all(
                (r["mean_daily_lower_bound_book_pct"] or 0) < 0 for r in all_late
            ),
            "profit_status": profit_status(all_late, "confirmation"),
        },
        "selection": {
            "status": sel.get("status"),
            "selected_alternative": sel.get("selected_alternative"),
            "selection_rule": sel.get("selection_rule"),
            "eligible_alternatives": sel.get("eligible_alternatives"),
            "n_eligible_alternatives": len(sel.get("eligible_alternatives", [])),
            "no_edge_promotion": sel.get("no_edge_promotion"),
            "validation_bootstrap": sel.get("validation_bootstrap"),
            "validation_metrics": headline_val,
        },
        "rows": rows,
        "monthly": monthly,
        "all_validation_alternatives": [
            {
                "alternative": r["alternative"],
                "finite": r["finite"],
                "selection_eligible": r["selection_eligible"],
                "n_known_fills": r["n_known_fills"],
                "mean_daily_lower_bound_book_pct": r["mean_daily_lower_bound_book_pct"],
                "mean_net_known_fill_per_order_pct": r["mean_net_known_fill_per_order_pct"],
            }
            for r in rows
            if r["period_role"] == "validation" and not r["alternative"].startswith("baseline")
        ],
        "family_definitions": s.get("family_definitions"),
        "no_fill_diagnostics": diag,
        "quote_audit": quote_audit,
        "method_notes": [
            "Selection = max 2023-validation mean daily lower-bound at 100bps subject to "
            "fills>=100 and traded_days>=50; the context baseline is never eligible.",
            "All 9 finite validation alternatives are in rows[] (validation period_role); the "
            "winner is not isolated.",
            "Exit label = first actual open >= min(entry+h, session_end); absent => UNKNOWN, "
            "never a silent zero; no high-touch profit credit.",
        ],
        "unit_semantics": [
            "mean_net_known_fill_per_order_pct = per-order net return after round-trip bps",
            "mean_daily_lower_bound_book_pct = whole-day lower-bound PnL / $3,000 sub-book",
            "entries_unfilled_expired is a NO-FILL count (no bar at entry minute), not a loss",
        ],
        "producer_sha256_reported": s.get("script_sha256"),
        "alpha_open_sim_sha256_reported": s.get("alpha_open_sim_sha256"),
        "panel_contract_sha256_reported": s.get("contract_sha256"),
        "producer_runtime_s": s.get("runtime_s"),
    }


# --------------------------------------------------------------------------- #
# learned_sparse_extension (rare/exploratory secondary probe)
# --------------------------------------------------------------------------- #
def build_sparse_extension(root: Path, paths: dict) -> dict:
    res = load_json(paths["learned_sparse_extension/results.json"])
    contract = res.get("contract", {})
    chosen = contract.get("chosen", {})
    trades_conf = optional(root, "learned_sparse_extension/trades_confirmation_100.parquet")
    quote_conf_p = optional(root, "learned_sparse_extension/quote_confirmation/summary.json")
    if trades_conf is not None and quote_conf_p is None:
        raise MissingStudyError(
            "learned_sparse_extension/quote_confirmation/summary.json "
            "(confirmation trades exist but their side-aware quote audit is absent)"
        )
    quote_audits = (
        {
            "confirmation_late": quote_block(
                load_json(quote_conf_p), quote_conf_p, "learned_sparse_extension"
            )
        }
        if quote_conf_p is not None
        else {}
    )
    rows, monthly = [], []
    val = res.get("validation")
    if val:
        r = proxy_metric_row(
            "sparse_extension",
            val,
            "validation",
            alternative=f"payoff_h{chosen.get('horizon')}_thr{chosen.get('threshold')}",
        )
        r["row_key"] = (
            "learned_sparse_extension|validation|"
            f"h{chosen.get('horizon')}|thr{chosen.get('threshold')}"
            f"|{val.get('cost_bps')}"
        )
        r["threshold"] = chosen.get("threshold")
        r["selection_eligible"] = True
        r["notes"].append(
            "SECONDARY/EXPLORATORY cell: 30<=known_fills<100 and "
            "traded_days>=30; it does NOT clear the primary power floor"
        )
        rows.append(r)
        monthly += monthly_rows(
            val.get("monthly_mean_lower_bound"),
            "sparse_extension",
            "validation",
            val.get("cost_bps"),
        )
    for cost_key in sorted((res.get("confirmation_by_cost") or {}), key=lambda c: float(c)):
        m = res["confirmation_by_cost"][cost_key]
        r = proxy_metric_row(
            "sparse_extension",
            m,
            "confirmation",
            alternative=f"payoff_h{chosen.get('horizon')}_thr{chosen.get('threshold')}",
        )
        r["row_key"] = (
            "learned_sparse_extension|confirmation|"
            f"h{chosen.get('horizon')}|thr{chosen.get('threshold')}"
            f"|{m.get('cost_bps')}"
        )
        r["threshold"] = chosen.get("threshold")
        r["is_out_of_fit"] = True
        r["out_of_fit_pristine_holdout"] = False
        r["notes"].append(
            "DISCOVERY-NOT-VALIDATED: exploratory probe on a previously "
            "explored block; not a pristine confirmation and not a promotion"
        )
        rows.append(r)
        monthly += monthly_rows(
            m.get("monthly_mean_lower_bound"), "sparse_extension", "confirmation", m.get("cost_bps")
        )
    conf = [r for r in rows if r["period_role"] == "confirmation"]
    elig_table = (contract.get("selection") or {}).get("eligibility_table") or []
    model_path = root / "learned" / "models" / f"payoff_h{chosen.get('horizon')}.joblib"
    return {
        "headline": {
            "study": "sparse_extension",
            "verdict": res.get("status"),
            "headline_metric": (
                "2023-validation ORIGINAL mean_daily_lower_bound of the chosen "
                f"sparse cell h={chosen.get('horizon')} "
                f"thr={chosen.get('threshold')}"
            ),
            "headline_value_pct": pct(chosen.get("original_validation_mean_daily_lower_bound")),
            "headline_unit": "percent of the $3,000 research sub-book per day",
            "n_known_fills": chosen.get("original_known_fills"),
            "n_unknown_fills": (val or {}).get("unknown_fills"),
            "n_days": (val or {}).get("days"),
            "cost_bps": chosen.get("selection_cost_bps"),
            "period_role": "validation",
            "book_usd": 3000.0,
            "order_usd": 1000.0,
            "certified": False,
            "certification_blockers": [
                "EXPLORATORY: 30<=known_fills<100, traded_days>=30 — below the primary power "
                "floor of >=100 known fills and >=50 traded days",
                "the 2025-02..2026-05 block was previously explored; NOT a pristine holdout "
                "(pristine_collision=false in the producer contract)",
                "the as-of side-aware quote audit (parent job bg88) prices ONLY the "
                "quote-supported subset of the late trades; whole_portfolio_certified=false, "
                "so a positive covered subset is neither a whole-book bound nor an "
                "exchange-fill guarantee",
                "minute-open entry/exit proxy; no side-aware quote audit on the panel legs",
                "the producer's own verdict is DISCOVERY-NOT-VALIDATED",
            ],
            "positive_regions": [
                {
                    "period_role": r["period_role"],
                    "row_key": r["row_key"],
                    "mean_daily_lower_bound_book_pct": r["mean_daily_lower_bound_book_pct"],
                    "mean_net_known_fill_per_order_pct": r["mean_net_known_fill_per_order_pct"],
                    "n_known_fills": r["n_known_fills"],
                    "label": "DISCOVERY-NOT-VALIDATED exploratory positive; NOT a promotable "
                    "candidate and NOT whole-portfolio certified",
                }
                for r in rows
                if (r["mean_daily_lower_bound_book_pct"] or 0) > 0
            ],
            "all_known_negative": all(
                (r["mean_daily_lower_bound_book_pct"] or 0) < 0 for r in conf
            ),
            "profit_status": profit_status(conf, "confirmation"),
        },
        "rare_extension": {
            "status": res.get("status"),
            "exploratory": contract.get("exploratory"),
            "pristine_collision": contract.get("pristine_collision"),
            "frozen_before_late_inspection": contract.get("frozen_before_late_inspection"),
            "frozen_at": contract.get("frozen_at"),
            "chosen": chosen,
            "selection": contract.get("selection"),
            "eligibility_table": elig_table,
            "n_alternatives_total": (contract.get("selection") or {}).get("alternatives_total"),
            "n_alternatives_eligible": (contract.get("selection") or {}).get(
                "alternatives_eligible"
            ),
            "source": contract.get("source"),
            "replay": contract.get("replay"),
            "model": contract.get("model"),
            "model_loaded_from": str(model_path),
            "model_loaded_from_exists": model_path.is_file(),
            "model_loaded_from_sha256": sha256_file(model_path) if model_path.is_file() else None,
            "refit": (contract.get("model") or {}).get("refit"),
            "hpo": (contract.get("model") or {}).get("hpo"),
            "feature_changes": (contract.get("model") or {}).get("feature_changes"),
            "honesty_note": (
                "this is the ONLY study in the packet whose late block is positive at 100 and "
                "150 bps; it is an exploratory sub-floor probe, its late block is not pristine, "
                "and the producer's own verdict is DISCOVERY-NOT-VALIDATED. It is the strongest "
                f"remaining lead and the reason a whole-portfolio execution-realistic re-test is "
                f"the top data need — it is not a promotion. Its as-of quote audit prices only "
                f"{(quote_audits.get('confirmation_late') or {}).get('n_covered')}"
                f"/{(quote_audits.get('confirmation_late') or {}).get('n_trades')} of the late "
                f"trades (whole_portfolio_certified=false)."
            ),
        },
        "quote_audits": quote_audits,
        "annual_ev_source_facts": _annual_ev_facts(root, res, contract, chosen, quote_audits),
        "rows": rows,
        "monthly": monthly,
        "confirmation_cost_effect": res.get("confirmation_cost_effect"),
        "method_notes": [
            "Reads the ORIGINAL learned/surface_validation.json with no rescan, no refit, no HPO "
            "and no feature change; loads only the stored h60 head.",
            "Eligibility is a SECONDARY exploratory floor (30<=known_fills<100, "
            "traded_days>=30, validation positive); median_or_tail_gates is null.",
            "The late block runs ONCE with the frozen strategy at 100/150/200 bps; the original "
            "h15 freeze, report, models and surface are never rewritten.",
        ],
        "unit_semantics": [
            "mean_net_known_fill_per_order_pct = per-order net return after round-trip bps",
            "mean_daily_lower_bound_book_pct = whole-day lower-bound PnL / $3,000 sub-book",
        ],
        "decision": res.get("decision"),
        "producer_runtime_s": res.get("runtime_s"),
        "producer_snapshot": {
            "path": str(root / "learned_sparse_extension" / "producer_snapshot.py"),
            "exists": (root / "learned_sparse_extension" / "producer_snapshot.py").is_file(),
            "sha256": (
                sha256_file(root / "learned_sparse_extension" / "producer_snapshot.py")
                if (root / "learned_sparse_extension" / "producer_snapshot.py").is_file()
                else None
            ),
            "equals_repo_producer_script": (
                sha256_file(root / "learned_sparse_extension" / "producer_snapshot.py")
                == sha256_file(ROOT / "factory/scripts/alpha_sparse_model_extension.py")
                if (root / "learned_sparse_extension" / "producer_snapshot.py").is_file()
                and (ROOT / "factory/scripts/alpha_sparse_model_extension.py").is_file()
                else None
            ),
            "note": (
                "the producer emits NO producer_sha256 inside its contract; the copy it "
                "leaves at <out>/producer_snapshot.py is the on-disk provenance for the run"
            ),
        },
        "producer_sha256_reported": (
            sha256_file(root / "learned_sparse_extension" / "producer_snapshot.py")
            if (root / "learned_sparse_extension" / "producer_snapshot.py").is_file()
            else None
        ),
    }


# --------------------------------------------------------------------------- #
# bid_backed_burst (new raw-micro base family)
# --------------------------------------------------------------------------- #
def _chosen_horizon_seconds(s: dict) -> int | None:
    """The horizon the producer's OWN frozen selection picked; None if it declared none.

    Reads only producer selection fields, in the order the producer writes them, and never
    re-derives a horizon from the results.  A packet must not pick a horizon itself: if no
    selection is declared the caller has to report "no headline" rather than fall back to
    whichever row happens to come first in ``results[]`` (which is h5 in every staged
    artifact, i.e. the horizon that was NOT selected).
    """
    verdict = s.get("verdict")
    verdict = verdict if isinstance(verdict, dict) else {}
    for v in (
        (s.get("selection") or {}).get("chosen_horizon_seconds"),
        verdict.get("chosen_horizon_seconds"),
        s.get("chosen_horizon_seconds"),
    ):
        if v is not None:
            return int(v)
    by_cost = s.get("chosen_horizon_by_cost")
    if isinstance(by_cost, dict):
        declared = sorted(
            {
                int(c["horizon_seconds"])
                for cells in by_cost.values()
                if isinstance(cells, dict)
                for c in cells.values()
                if isinstance(c, dict) and c.get("horizon_seconds") is not None
            }
        )
        if len(declared) == 1:
            return declared[0]
    return None


def build_bid_backed_burst(root: Path, paths: dict) -> dict:
    s = load_json(paths["bid_backed_burst/summary.json"])
    contract = s.get("contract", {}) or {}
    results = s.get("results")
    chosen_h = _chosen_horizon_seconds(s)
    rows, monthly = [], []

    def _from_rows(period, period_role, cost, r, note_extra=None):
        notes = [
            "cost is RESIDUAL round-trip bps charged on top of the actual ASK/BID touch",
            "0 bps is a diagnostic, not a free-fill claim",
        ]
        if note_extra:
            notes.extend(note_extra)
        # The row's own horizon.  A horizon-less row falls back to the producer's frozen
        # selection (never to an invented constant) so a row_key can never silently lose
        # the horizon dimension and collide with the other horizon's row.
        horizon = r.get("horizon_seconds", r.get("horizon"))
        if horizon is None:
            horizon = chosen_h if chosen_h is not None else contract.get("horizon_seconds")
        return mrow(
            study="bid_backed_burst",
            family="bid_backed_burst",
            alternative=f"bid_backed_burst_h{horizon}",
            row_key=f"bid_backed_burst|{period}|h{horizon}|{float(cost):g}",
            period=period,
            period_role=period_role,
            is_out_of_fit=(period_role == "confirmation"),
            out_of_fit_pristine_holdout=False,
            horizon=horizon,
            horizon_unit="seconds",
            cost_bps=float(cost),
            cost_basis=RESIDUAL_ON_TOUCH,
            execution_model=TOUCH_250MS,
            fill_convention=ACTUAL_TOUCH,
            n_days=r.get("days") or r.get("period_days"),
            n_attempts=r.get("attempts"),
            n_fills=r.get("fills"),
            n_known_fills=r.get("known_fills"),
            n_unknown_fills=r.get("unknown_fills"),
            n_no_fill_or_skips=r.get("cash_or_slot_skips"),
            n_traded_days=r.get("traded_days"),
            n_months=r.get("months"),
            n_positive_months=r.get("positive_months_lower_bound"),
            mean_net_known_fill_per_order_pct=pick_pct(
                r, "mean_net_known_fill", "mean_net_known_fill_per_order_pct"
            ),
            mean_daily_lower_bound_book_pct=pick_pct(
                r, "mean_daily_lower_bound", "mean_daily_lower_bound_book_pct"
            ),
            daily_se_book_pct=pick_pct(r, "daily_se"),
            ci95_lower_bound_book_pct=(
                [pct(v) for v in r["day_bootstrap_ci95"]] if r.get("day_bootstrap_ci95") else None
            ),
            known_win_rate_pct=pick_pct(r, "known_win_rate"),
            known_profit_factor_ratio=r.get("known_profit_factor"),
            worst_known_fill_per_order_pct=pick_pct(r, "worst_known_fill"),
            book_usd=float(r.get("research_book") or contract.get("research_book") or 750.0),
            order_usd=float(r.get("order_budget") or contract.get("order_budget") or 250.0),
            coverage_unknown_days=r.get("coverage_unknown_days"),
            coverage_worst_case_mean_daily_book_pct=pick_pct(
                r, "coverage_worst_case_mean_daily", "coverage_worst_case_mean_daily_book_pct"
            ),
            known_complete_day_mean_lower_bound_book_pct=pick_pct(
                r,
                "known_complete_day_mean_lower_bound",
                "known_complete_day_mean_lower_bound_book_pct",
            ),
            quote_supported=r.get("quote_supported"),
            finite=(r.get("mean_net_known_fill") is not None),
            notes=notes,
        )

    if isinstance(results, list) and results:
        shape = "results[] list (one row per period x horizon x cost)"
        for r0 in results:
            period = r0.get("period")
            role = r0.get("period_role") or (
                "development" if str(period).startswith("development") else period
            )
            cost = r0.get("cost_bps_residual", r0.get("cost_bps"))
            extra = None
            if str(period).endswith("_complete_days_only"):
                extra = [
                    "known-complete-days-only slice; the all-days variant is the "
                    "conservative read and this slice must never be promoted to a "
                    "whole-portfolio claim"
                ]
            r = _from_rows(period, role, cost, r0, extra)
            rows.append(r)
            for _m in monthly_rows(
                r0.get("monthly_mean_lower_bound"),
                "bid_backed_burst",
                role,
                float(cost),
                book_usd=750.0,
            ):
                _m["horizon"] = r["horizon"]
                monthly.append(_m)
    else:
        shape = "summary[period][by_cost] (cost keys as strings)"
        for period in ("development", "validation", "confirmation"):
            p = (s.get("summary") or {}).get(period) or {}
            by_cost = p.get("by_cost") or {}
            for cost in sorted(by_cost, key=lambda c: float(c)):
                m = by_cost[cost]
                src = {}
                r = _from_rows(period, period, float(cost), {**src, **m})
                rows.append(r)
                for _m in monthly_rows(
                    m.get("monthly_mean_lower_bound"),
                    "bid_backed_burst",
                    period,
                    float(cost),
                    book_usd=750.0,
                ):
                    _m["horizon"] = r["horizon"]
                    monthly.append(_m)

    conf = [r for r in rows if r["period_role"] == "confirmation"]
    cov = {
        period: _condense_coverage(c)
        for period, c in (s.get("coverage") or {}).items()
        if isinstance(c, dict)
    }
    book = float(contract.get("research_book") or 750.0)
    order = float(contract.get("order_budget") or 250.0)
    # Headline = the CHOSEN horizon's confirmation cell at the 100 bps residual rung.
    # results[] is emitted h5 before h15, so a cost-only `next(...)` silently returns the
    # NON-selected horizon (h5) and reports -0.8839906810%/day instead of the selected
    # h15's -0.9802612386%/day.  The horizon is therefore part of the selector.  When the
    # producer froze NO selection there is no headline row at all: the packet never
    # invents one and never falls back to the first cost row.
    at100 = None
    if chosen_h is not None:
        at100 = next((r for r in conf if r["cost_bps"] == 100 and r["horizon"] == chosen_h), None)
    headline_metric = (
        f"confirmation mean_daily_lower_bound at 100bps residual on the producer's "
        f"chosen h{chosen_h} horizon"
        if chosen_h is not None
        else "confirmation mean_daily_lower_bound at 100bps residual; the producer froze NO "
        "horizon selection, so no headline row is chosen"
    )
    return {
        "headline": {
            "study": "bid_backed_burst",
            "verdict": s.get("verdict") or "not_reported",
            "headline_metric": headline_metric,
            "headline_value_pct": at100["mean_daily_lower_bound_book_pct"] if at100 else None,
            "headline_unit": "percent of the research book per day",
            "headline_row_key": at100["row_key"] if at100 else None,
            "headline_horizon_seconds": chosen_h,
            "headline_horizon_unit": "seconds",
            "headline_horizon_selection_source": (
                "the producer's frozen selection field; the packet only reads it"
                if chosen_h is not None
                else None
            ),
            "n_known_fills": at100["n_known_fills"] if at100 else None,
            "n_unknown_fills": at100["n_unknown_fills"] if at100 else None,
            "n_days": at100["n_days"] if at100 else None,
            "cost_bps": 100,
            "period_role": "confirmation",
            "book_usd": book,
            "order_usd": order,
            "certified": bool(s.get("certified", False)),
            "certification_blockers": [
                "quote-supported touch is capacity-checked, never an exchange fill guarantee",
                "the confirmation months were previously explored; NOT a pristine holdout",
                "margin-style funded cash reuse is assumed, not a small-cash-account claim",
            ],
            "positive_regions": [
                {
                    "period_role": r["period_role"],
                    "row_key": r["row_key"],
                    "mean_daily_lower_bound_book_pct": r["mean_daily_lower_bound_book_pct"],
                    "n_known_fills": r["n_known_fills"],
                    "label": "positive subset; NOT whole-portfolio certified",
                }
                for r in rows
                if (r["mean_daily_lower_bound_book_pct"] or 0) > 0
            ],
            "all_known_negative": all(
                (r["mean_daily_lower_bound_book_pct"] or 0) < 0 for r in conf
            ),
            "profit_status": profit_status(conf, "confirmation"),
        },
        "hypothesis": contract.get("hypothesis"),
        "conditions": contract.get("conditions"),
        "liveness": contract.get("liveness"),
        "state_grid": contract.get("state_grid"),
        "entry_exit": {"entry": contract.get("entry"), "exit": contract.get("exit")},
        "depth_rule": contract.get("depth") or contract.get("depth_rule"),
        "extensions": contract.get("extensions") or contract.get("horizons_seconds"),
        "month_sets": {
            "development_months": (contract.get("periods") or {})
            .get("development", {})
            .get("months", (contract.get("periods") or {}).get("development_months")),
            "validation_months": (contract.get("periods") or {})
            .get("validation", {})
            .get("months", (contract.get("periods") or {}).get("validation_months")),
            "confirmation_months": (contract.get("periods") or {})
            .get("confirmation", {})
            .get("months", (contract.get("periods") or {}).get("confirmation_months")),
            "protected_unread": contract.get("protected_unread", PROTECTED_UNREAD),
        },
        "actual_touch_and_residual_cost": {
            "rule": contract.get("cost_note")
            or (
                "actual side-aware ASK/BID prices plus residual round-trip bps; 0bps is a "
                "diagnostic, not a free-fill claim"
            ),
            "costs_bps_residual_round_trip": contract.get("costs_bps_residual_round_trip"),
            "cost_basis": RESIDUAL_ON_TOUCH,
            "entry_price": contract.get("entry"),
            "exit_price": contract.get("exit"),
            "latency": contract.get("latency_us") or contract.get("latency_ms"),
            "order_budget_usd": order,
            "research_book_usd": book,
        },
        "coverage": cov,
        "coverage_worst_bounds": {
            "rule": "any coverage-unknown day is charged -100% of the research book; the "
            "known-complete-day mean is reported separately and never used to hide an "
            "unknown day",
            "by_period": _worst_by_period(rows),
        },
        "detected_result_schema": shape,
        "verdict_block": s.get("verdict"),
        "chosen_horizon_by_cost": s.get("chosen_horizon_by_cost"),
        "selection": s.get("selection"),
        "chosen_horizon_seconds": s.get("chosen_horizon_seconds"),
        "certified_complete": s.get("certified_complete"),
        "execution_proxy_only": s.get("execution_proxy_only"),
        "periods_months": s.get("periods_months"),
        "share_periods_reset_from_old_60s": s.get("share_periods_reset_from_old_60s"),
        "development_window_rationale": s.get("development_window_rationale"),
        "horizons_seconds_declared": s.get("horizons_seconds_declared"),
        "cost_rungs_bps_round_trip": s.get("cost_rungs_bps_round_trip"),
        "cost_type": s.get("cost_type"),
        "core_module": s.get("core_module"),
        "core_sha256": s.get("core_sha256"),
        "depth_rule_detail": (
            "depth_imbalance=(bid_shares-ask_shares)/(bid_shares+ask_shares), computed PAST-ONLY "
            "by the core states() at the strictly-prior displayed NBBO; the producer only reads "
            "the column. Execution entry at signal+250ms; each h-exit BID at entry+{h}s via "
            "core execution(h)."
        ),
        "execution_note": (
            "market ASK at signal+250ms / market BID at entry+{h}s; insufficient "
            "displayed depth on either side => UNKNOWN, charged -100% of its order "
            "in the lower bound"
        ),
        "coverage_rule_detail": (
            "any symbol with only '?' quote conditions on a date makes the WHOLE date "
            "coverage-unknown => -100% whole-book lower bound (never cash zero) and "
            "certified_complete_period=False; unknown days also get a "
            "<period>_complete_days_only slice which is never promoted to a whole-portfolio "
            "claim"
        ),
        "data_support": s.get("data_support"),
        "cost_unit": contract.get("cost_unit") or "residual_bps_round_trip_on_actual_touch",
        "day_manifests": {
            "dir": str(root / "bid_backed_burst" / "days"),
            "exists": (root / "bid_backed_burst" / "days").is_dir(),
            "count": (
                len(list((root / "bid_backed_burst" / "days").glob("*.json")))
                if (root / "bid_backed_burst" / "days").is_dir()
                else 0
            ),
            "note": "per-day intents + quote_executions for BOTH horizons with producer and "
            "core sha256; the packet only reads summary.json/contract.json and never "
            "re-derives fills",
        },
        "rows": rows,
        "monthly": monthly,
        "method_notes": [
            "Registered as a NEW base family with its own month sets; the 60-second months of "
            "the older micro families are not reused or reset.",
            "Bid-backed qualification requires past-only depth/imbalance fields evaluated at the "
            "execution and evaluate-h seconds; no future print or quote is read.",
        ],
        "unit_semantics": [
            "mean_net_known_fill_per_order_pct = per-order net return after residual bps",
            "mean_daily_lower_bound_book_pct = whole-day book PnL / research_book",
            "coverage_worst_case_mean_daily_book_pct charges unknown days at -100% of the book",
        ],
        "producer_sha256_reported": contract.get("producer_sha256"),
        "contract_sha256_reported": contract.get("contract_sha256"),
    }


# --------------------------------------------------------------------------- #
# learned
# --------------------------------------------------------------------------- #
def build_learned(root: Path, paths: dict) -> dict:
    rep = load_json(paths["learned/report.json"])
    surf = load_json(paths["learned/surface_validation.json"])
    frozen = load_json(paths["learned/frozen_contract.json"])
    chosen = rep.get("chosen", {})
    rows, monthly = [], []

    for key in sorted(surf, key=lambda k: (int(k.split("|")[0]), float(k.split("|")[1]))):
        m = surf[key]
        h, thr = key.split("|")
        r = proxy_metric_row("learned", m, "validation", period=key)
        r["row_key"] = f"learned|validation|h{h}|thr{thr}|{m.get('cost_bps')}"
        r["horizon"] = int(h)
        r["threshold"] = float(thr)
        r["alternative"] = f"payoff_h{h}_thr{thr}"
        eligible = (
            bool(chosen)
            and int(h) == chosen.get("horizon")
            and float(thr) == chosen.get("threshold")
        )
        r["selection_eligible"] = eligible
        rows.append(r)
        monthly += monthly_rows(
            m.get("monthly_mean_lower_bound"), "learned", "validation", m.get("cost_bps")
        )
    for cost_key in sorted(rep.get("confirmation", {}), key=lambda c: float(c)):
        c = rep["confirmation"][cost_key]
        r = proxy_metric_row("learned", c, "confirmation")
        r["row_key"] = (
            f"learned|confirmation|h{chosen.get('horizon')}|thr{chosen.get('threshold')}|{c.get('cost_bps')}"
        )
        r["alternative"] = f"payoff_h{chosen.get('horizon')}_thr{chosen.get('threshold')}"
        r["is_out_of_fit"] = True
        r["out_of_fit_pristine_holdout"] = False
        rows.append(r)
        monthly += monthly_rows(c.get("monthly"), "learned", "confirmation", c.get("cost_bps"))
    sv = rep.get("strategy_validation")
    if sv:
        r = proxy_metric_row("learned", sv, "validation")
        r["row_key"] = f"learned|strategy_validation|{sv.get('cost_bps')}"
        r["alternative"] = f"strategy_payoff_h{chosen.get('horizon')}_thr{chosen.get('threshold')}"
        r["notes"].append(
            "same numbers as the surface cell for the chosen (horizon, threshold); "
            "kept because the producer emits it as a distinct strategy_view"
        )
        rows.append(r)
        monthly += monthly_rows(sv.get("monthly"), "learned", "validation", sv.get("cost_bps"))

    # power-floor honesty: which alternatives actually cleared it, and which rare
    # extensions are exploratory only.
    surf_finite = {k: v for k, v in surf.items() if v.get("mean_net_known_fill") is not None}
    cleared = [
        k
        for k, v in surf_finite.items()
        if v.get("known_fills", 0) >= 100 and v.get("traded_days", 0) >= 50
    ]
    rare = [k for k, v in surf.items() if v.get("known_fills", 0) < 100]

    model_report = {}
    for h, mr in rep.get("model_report", {}).items():
        model_report[h] = {
            "train_rows": mr.get("train_rows"),
            "train_day_min": mr.get("train_day_min"),
            "train_day_max": mr.get("train_day_max"),
            "label_unclipped": mr.get("label_unclipped"),
            "label_clip_for_fit": mr.get("label_clip_for_fit"),
            "gain_importance_top10": dict(
                sorted((mr.get("gain_importance") or {}).items(), key=lambda kv: -kv[1])[:10]
            ),
        }

    qv = quote_block(
        load_json(paths["learned/quote_validation/summary.json"]),
        paths["learned/quote_validation/summary.json"],
        "learned",
    )
    qc = quote_block(
        load_json(paths["learned/quote_confirmation/summary.json"]),
        paths["learned/quote_confirmation/summary.json"],
        "learned",
    )

    val_pos = [
        r
        for r in rows
        if r["period_role"] == "validation" and (r["mean_daily_lower_bound_book_pct"] or 0) > 0
    ]
    all_late = [r for r in rows if r["period_role"] == "confirmation"]
    feat_order = optional(root, "learned/models/feature_order.json")
    return {
        "headline": {
            "study": "learned",
            "verdict": rep.get("decision"),
            "headline_metric": "2023-validation mean_daily_lower_bound of the frozen "
            f"h={chosen.get('horizon')} thr={chosen.get('threshold')}",
            "headline_value_pct": pct(chosen.get("validation_mean_daily_lower_bound")),
            "headline_unit": "percent of the $3,000 research sub-book per day",
            "n_known_fills": (sv or {}).get("known_fills"),
            "n_unknown_fills": (sv or {}).get("unknown_fills"),
            "n_days": (sv or {}).get("days"),
            "cost_bps": chosen.get("selection_cost_bps"),
            "period_role": "validation",
            "book_usd": 3000.0,
            "order_usd": 1000.0,
            "certified": bool(qv["whole_portfolio_certified"] and qc["whole_portfolio_certified"]),
            "certification_blockers": [
                "whole_portfolio_certified=false in BOTH quote audits "
                f"(validation {qv['n_unknown_pairs']}/{qv['n_trades']} pairs UNKNOWN, "
                f"confirmation {qc['n_unknown_pairs']}/{qc['n_trades']} pairs UNKNOWN)",
                "confirmation window 2025-02..2026-05 was previously explored; "
                "NOT a pristine holdout",
                "minute-open proxy; quote audit checks capacity support, never an exchange fill",
            ],
            "positive_regions": [
                {
                    "period_role": r["period_role"],
                    "row_key": r["row_key"],
                    "mean_daily_lower_bound_book_pct": r["mean_daily_lower_bound_book_pct"],
                    "n_known_fills": r["n_known_fills"],
                    "label": "positive subset; NOT whole-portfolio certified",
                }
                for r in val_pos
            ],
            "positive_subset_not_certified": bool(val_pos) or bool(qv["positive_covered_pairs"]),
            "all_known_negative": all(
                (r["mean_daily_lower_bound_book_pct"] or 0) < 0 for r in all_late
            ),
            "profit_status": profit_status(all_late, "confirmation"),
        },
        "selection": {
            "chosen": chosen,
            "power_floor": {
                "min_known_fills": 100,
                "min_traded_days": 50,
                "median_or_tail_gates": None,
            },
            "n_alternatives_on_validation_surface": len(surf),
            "n_alternatives_clearing_power_floor": len(cleared),
            "alternatives_clearing_power_floor": sorted(cleared),
            "n_rare_exploratory_below_floor": len(rare),
            "rare_exploratory_below_floor": sorted(rare),
            "frozen_before_confirmation": frozen.get("frozen_before_confirmation"),
            "frozen_at": frozen.get("frozen_at"),
            "rationale": chosen.get("rationale"),
        },
        "sparse_model_secondary_extension": {
            "status": "SECONDARY_DISCOVERY_PROBE",
            "scope": "3 fixed LightGBM payoff heads (h15/h60/h390) fit on 2021-02..2022-12, "
            "26 causal features incl. 3 corrected cross-sectional peer features, no HPO",
            "feature_order_file": str(feat_order) if feat_order else None,
            "feature_order_len": (len(load_json(feat_order)) if feat_order else None),
            "models": {
                h: {
                    "path": str(root / f"learned/models/payoff_h{h}.joblib"),
                    "path_exists": (root / f"learned/models/payoff_h{h}.joblib").is_file(),
                    "sha256": (
                        sha256_file(root / f"learned/models/payoff_h{h}.joblib")
                        if (root / f"learned/models/payoff_h{h}.joblib").is_file()
                        else None
                    ),
                }
                for h in (15, 60, 390)
            },
            "load_api": "alpha_open_learned.load_models(model_dir) -> {h: model}; "
            "score_frame(frame, models); feature_matrix(frame); "
            "make_signals(cand, horizon, threshold, days)",
            "importance_reported_for": "gain_importance (label-free diagnostic only)",
            "secondary_not_closure": True,
        },
        "model_report": model_report,
        "confirmation_cost_effect": rep.get("confirmation_cost_effect"),
        "novelty": rep.get("novelty"),
        "rows": rows,
        "monthly": monthly,
        "all_validation_alternatives": [
            {
                "row_key": r["row_key"],
                "horizon": r["horizon"],
                "threshold": r["threshold"],
                "finite": r["finite"],
                "selection_eligible": r["selection_eligible"],
                "n_known_fills": r["n_known_fills"],
                "mean_daily_lower_bound_book_pct": r["mean_daily_lower_bound_book_pct"],
                "mean_net_known_fill_per_order_pct": r["mean_net_known_fill_per_order_pct"],
            }
            for r in rows
            if r["row_key"].startswith("learned|validation|h")
        ],
        "quote_audits": {"validation_2023": qv, "confirmation_late": qc},
        "method_notes": [
            "Initial power floor (>=100 known fills AND >=50 traded days on the 2023 validation "
            "surface) is a sample-size floor, NOT a median/tail/statistical-significance gate.",
            "Rare extensions (thr 0.03/0.05 with 0-5 known fills) are exploratory only; they are "
            "kept in rows[] but can never clear the power floor and must not be promoted.",
            "The learned model is a SECONDARY discovery probe: a positive 2023-validation cell is "
            "not a promotion and not a closure of the open-anchored mechanism question.",
            "Confirmation mean daily lower bound is negative at 100, 150 and 200 bps.",
        ],
        "unit_semantics": [
            "mean_net_known_fill_per_order_pct = per-order net return after round-trip bps",
            "mean_daily_lower_bound_book_pct = whole-day lower-bound PnL / $3,000 sub-book",
            "lower_bound_pnl_usd in monthly[] is dollars; the *_pct fields are percent",
            "known_profit_factor_ratio is a ratio, not a percent",
        ],
        "producer_runtime_s": rep.get("runtime_s"),
    }


# --------------------------------------------------------------------------- #
# sequence
# --------------------------------------------------------------------------- #
def build_sequence(root: Path, paths: dict) -> dict:
    late = load_json(paths["sequence/late_summary.json"])
    surface = load_json(paths["sequence/validation_surface.json"])
    frozen = load_json(paths["sequence/frozen_config.json"])
    prov = load_json(paths["sequence/provenance.json"])
    contract = load_json(paths["sequence/contract.json"])
    rows = []

    for m in sorted(
        surface,
        key=lambda r: (
            r.get("horizon", 0),
            r.get("scale", 0),
            r.get("threshold_pred_gross", 0),
            r.get("cost_bps", 0),
        ),
    ):
        r = proxy_metric_row(
            "sequence",
            m,
            "validation",
            period=f"h{m.get('horizon')}|s{m.get('scale')}|thr{m.get('threshold_pred_gross')}",
        )
        r["row_key"] = (
            f"sequence|validation|h{m.get('horizon')}|s{m.get('scale')}"
            f"|thr{m.get('threshold_pred_gross')}|{m.get('cost_bps')}"
        )
        r["alternative"] = (
            f"head_scale{m.get('scale')}_h{m.get('horizon')}|thr{m.get('threshold_pred_gross')}"
        )
        r["threshold"] = m.get("threshold_pred_gross")
        r["selection_eligible"] = bool(m.get("feasible"))
        r["notes"] = (
            []
            if m.get("feasible")
            else ["feasible=false: below the 100-known-fill / 50-traded-day floor"]
        )
        rows.append(r)
    for key in sorted(late):
        m = late[key]
        r = proxy_metric_row("sequence", m, "confirmation")
        r["row_key"] = f"sequence|late|{key}"
        r["alternative"] = f"head_scale{m.get('scale')}_h{m.get('horizon')}"
        r["threshold"] = m.get("threshold_pred_gross")
        r["is_out_of_fit"] = True
        r["out_of_fit_pristine_holdout"] = False
        r["cost_bps"] = m.get("cost_bps")
        rows.append(r)

    all_late = [r for r in rows if r["period_role"] == "confirmation"]
    return {
        "headline": {
            "study": "sequence",
            "verdict": "no_selected_head_positive_on_late_block",
            "headline_metric": "late-block mean_daily_lower_bound across both frozen heads",
            "headline_value_pct": pct(late.get("h60_cost100", {}).get("mean_daily_lower_bound")),
            "headline_unit": "percent of the $3,000 research sub-book per day "
            "(h60/scale30 @100bps)",
            "n_known_fills": late.get("h60_cost100", {}).get("known_fills"),
            "n_unknown_fills": late.get("h60_cost100", {}).get("unknown_fills"),
            "n_days": late.get("h60_cost100", {}).get("days"),
            "cost_bps": late.get("h60_cost100", {}).get("cost_bps"),
            "period_role": "confirmation",
            "book_usd": 3000.0,
            "order_usd": 1000.0,
            "certified": False,
            "certification_blockers": [
                "TCN encoder weights were fit UNSUPERVISED on all 734 fit-block days "
                "2021-02..2023-12, so the 2023 validation window has encoder fit exposure",
                "late block 2025-02..2026-05 was previously explored; NOT a pristine holdout",
                "minute-open proxy; quote-side verification not performed",
            ],
            "positive_regions": [],
            "all_known_negative": all(
                (r["mean_daily_lower_bound_book_pct"] or 0) < 0 for r in all_late
            ),
            "profit_status": profit_status(all_late, "confirmation"),
        },
        "encoder_fit_2023_disclosure": {
            "head_fit_window": contract.get("head_fit"),
            "validation_window": contract.get("validation"),
            "encoder_fit_disclosure": (
                "TCN weights fit UNSUPERVISED on all 734 fit-block days 2021-02..2023-12; the "
                "2023 validation window therefore has encoder fit exposure. The late block "
                "2025-02..2026-05 encoder is out-of-fit and is the main independent period."
            ),
            "encoder_fit_block_days": 734,
            "late_block_out_of_fit_only": True,
        },
        "frozen_config": frozen,
        "cohort": prov.get("cohort"),
        "causality_checks": prov.get("causality_checks"),
        "provenance_sha256": {k: v for k, v in prov.items() if k.endswith("_sha256")},
        "saved_models": {
            f"head_scale{s}_h{h}": {
                "path": str(root / f"sequence/models/head_scale{s}_h{h}.joblib"),
                "path_exists": (root / f"sequence/models/head_scale{s}_h{h}.joblib").is_file(),
                "sha256": (
                    sha256_file(root / f"sequence/models/head_scale{s}_h{h}.joblib")
                    if (root / f"sequence/models/head_scale{s}_h{h}.joblib").is_file()
                    else None
                ),
            }
            for s in (30, 60, 120)
            for h in (15, 60)
        },
        "rows": rows,
        "all_validation_alternatives": [
            {
                "row_key": r["row_key"],
                "horizon": r["horizon"],
                "scale": r.get("alternative"),
                "threshold_pred_gross": r["threshold"],
                "cost_bps": r["cost_bps"],
                "finite": r["finite"],
                "selection_eligible": r["selection_eligible"],
                "n_known_fills": r["n_known_fills"],
                "mean_daily_lower_bound_book_pct": r["mean_daily_lower_bound_book_pct"],
                "mean_net_known_fill_per_order_pct": r["mean_net_known_fill_per_order_pct"],
            }
            for r in rows
            if r["period_role"] == "validation"
        ],
        "method_notes": [
            "All 54 finite validation cells are in rows[] (3 scales x 2 horizons x 3 thresholds "
            "x 3 costs); the feasible flag marks the 100-fill/50-day floor.",
            "One scale per horizon + one threshold frozen BEFORE the single late execution; no "
            "refit, no HPO, ridge alpha=1.0 on 32 ASOF latents.",
            "The 2023 validation surface is contaminated by unsupervised encoder fit and cannot "
            "be read as a clean holdout.",
        ],
        "unit_semantics": [
            "mean_net_known_fill_per_order_pct = per-order net return after round-trip bps",
            "mean_daily_lower_bound_book_pct = whole-day lower-bound PnL / $3,000 sub-book",
        ],
        "producer_sha256_reported": prov.get("code_sha256"),
    }


# --------------------------------------------------------------------------- #
# proven_push
# --------------------------------------------------------------------------- #
def build_proven_push(root: Path, paths: dict) -> dict:
    res = load_json(paths["proven_push/results.json"])
    contract = res.get("contract", {})
    rows, monthly = [], []
    for r0 in res.get("results", []):
        r = proxy_metric_row(
            "proven_push",
            r0,
            r0.get("block", "train"),
            alternative="proven_open_push_with_broad_flow_confirm",
        )
        r["row_key"] = f"proven_push|{r0.get('block')}|{r0.get('cost_bps')}"
        if r0.get("block") == "confirmation":
            r["is_out_of_fit"] = True
            r["out_of_fit_pristine_holdout"] = False
        rows.append(r)
        monthly += monthly_rows(
            r0.get("monthly_mean_lower_bound"), "proven_push", r0.get("block"), r0.get("cost_bps")
        )
    conf = [r for r in rows if r["period_role"] == "confirmation"]
    return {
        "headline": {
            "study": "proven_push",
            "verdict": "negative_at_all_cost_rungs",
            "headline_metric": "confirmation mean_daily_lower_bound at 100bps",
            "headline_value_pct": next(
                (r["mean_daily_lower_bound_book_pct"] for r in conf if r["cost_bps"] == 100), None
            ),
            "headline_unit": "percent of the $3,000 research sub-book per day",
            "n_known_fills": next((r["n_known_fills"] for r in conf if r["cost_bps"] == 100), None),
            "n_unknown_fills": next(
                (r["n_unknown_fills"] for r in conf if r["cost_bps"] == 100), None
            ),
            "n_days": next((r["n_days"] for r in conf if r["cost_bps"] == 100), None),
            "cost_bps": 100,
            "period_role": "confirmation",
            "book_usd": 3000.0,
            "order_usd": 1000.0,
            "certified": False,
            "certification_blockers": [
                "minute-open proxy; actual side-aware quote audit still required",
                "confirmation window 2025-02..2026-05 was previously explored; "
                "NOT a pristine holdout",
            ],
            "positive_regions": [],
            "all_known_negative": all(
                (r["mean_daily_lower_bound_book_pct"] or 0) < 0 for r in conf
            ),
            "profit_status": profit_status(conf, "confirmation"),
        },
        "hypothesis": contract.get("hypothesis"),
        "conditions": contract.get("conditions"),
        "peer_gate": contract.get("peer_gate"),
        "liquidity": contract.get("liquidity"),
        "selection": contract.get("selection"),
        "fill_model": contract.get("fill_model"),
        "rows": rows,
        "monthly": monthly,
        "method_notes": [
            "One fixed formulation registered before any outcome was inspected; no selection.",
            "peer gate already subtracts the subject: >=3 OTHER watchlist names with ret3>0.",
            "Reported at 100/150/200 bps on train, validation and the late confirmation block.",
        ],
        "unit_semantics": [
            "mean_net_known_fill_per_order_pct = per-order net return after round-trip bps",
            "mean_daily_lower_bound_book_pct = whole-day lower-bound PnL / $3,000 sub-book",
        ],
        "producer_sha256_reported": contract.get("producer_sha256"),
    }


# --------------------------------------------------------------------------- #
# short_diagnostic
# --------------------------------------------------------------------------- #
def build_short(root: Path, paths: dict) -> dict:
    res = load_json(paths["short_diagnostic/results.json"])
    contract = res.get("contract", {})
    rows, monthly = [], []
    for r0 in res.get("results", []):
        role = r0.get("block", "train")
        r = mrow(
            study="short_diagnostic",
            family="short_liquid_extremes",
            alternative="short_gain_open_ge_30",
            row_key=f"short_diagnostic|{role}|{r0.get('cost_bps')}",
            period=role,
            period_role=role,
            is_out_of_fit=(role == "confirmation"),
            out_of_fit_pristine_holdout=False,
            horizon=15,
            horizon_unit="minutes",
            cost_bps=r0.get("cost_bps"),
            cost_basis=RT_ON_FILL,
            execution_model=MINUTE_PROXY,
            fill_convention=NO_HIGH_CREDIT,
            n_days=r0.get("days"),
            n_attempts=r0.get("attempts"),
            n_fills=r0.get("fills"),
            n_known_fills=(r0.get("fills") or 0) - (r0.get("unknown_fills") or 0),
            n_unknown_fills=r0.get("unknown_fills"),
            n_traded_days=None,
            n_months=r0.get("months"),
            n_positive_months=r0.get("positive_months"),
            mean_net_known_fill_per_order_pct=pct(r0.get("mean_net_known_fill")),
            mean_daily_lower_bound_book_pct=pct(r0.get("mean_daily_known")),
            daily_se_book_pct=None,
            ci95_lower_bound_book_pct=[pct(r0["ci95_known_days"][0]), pct(r0["ci95_known_days"][1])]
            if r0.get("ci95_known_days")
            else None,
            known_win_rate_pct=None,
            known_profit_factor_ratio=None,
            worst_known_fill_per_order_pct=pct(r0.get("worst_known_fill")),
            book_usd=float(contract.get("order_budget") or 1000.0),
            order_usd=float(contract.get("order_budget") or 1000.0),
            finite=(r0.get("mean_net_known_fill") is not None),
            study_specific={
                "mean_daily_known_fill_days_only_pct": pct(r0.get("mean_daily_known")),
                "unconditional_daily_mean_pct": pct(r0.get("unconditional_daily_mean")),
                "mean_affordable_locate_borrow_bps": r0.get("mean_affordable_locate_borrow_bps"),
                "max_intratrade_adverse_per_order_pct": pct(r0.get("max_intratrade_adverse")),
                "ci95_known_days_pct": (
                    [pct(r0["ci95_known_days"][0]), pct(r0["ci95_known_days"][1])]
                    if r0.get("ci95_known_days")
                    else None
                ),
            },
            notes=[
                "mean_daily_lower_bound_book_pct here is the producer's mean_daily_known: the "
                "mean over KNOWN-FILL days only, per $1,000 order. It is NOT a whole-day book "
                "lower bound and must not be read as capital return.",
                "unconditional_daily_mean is null in the producer, so no whole-day book mean is "
                "claimed for this family; the row's book_usd/order_usd are the same $1,000 unit.",
                "UNKNOWN exits are unbounded, never zero-imputed.",
            ],
        )
        rows.append(r)
        monthly += monthly_rows(
            r0.get("monthly_mean"), "short_diagnostic", role, r0.get("cost_bps")
        )
    conf = [r for r in rows if r["period_role"] == "confirmation"]
    val = [r for r in rows if r["period_role"] == "validation"]
    return {
        "headline": {
            "study": "short_diagnostic",
            "verdict": contract.get("status"),
            "headline_metric": "confirmation mean net known fill at 100bps",
            "headline_value_pct": next(
                (r["mean_net_known_fill_per_order_pct"] for r in conf if r["cost_bps"] == 100), None
            ),
            "headline_unit": "percent per short order (NOT a percent of the book)",
            "n_known_fills": next((r["n_known_fills"] for r in conf if r["cost_bps"] == 100), None),
            "n_unknown_fills": next(
                (r["n_unknown_fills"] for r in conf if r["cost_bps"] == 100), None
            ),
            "n_days": next((r["n_days"] for r in conf if r["cost_bps"] == 100), None),
            "cost_bps": 100,
            "period_role": "confirmation",
            "book_usd": float(contract.get("order_budget") or 1000.0),
            "order_usd": float(contract.get("order_budget") or 1000.0),
            "certified": False,
            "certification_blockers": [
                "point-in-time locates/fees, SSR-aware legal fills, as-of bid/ask + market "
                "impact and margin/buy-in requirements are all UNVERIFIED (see borrow_ssr_blocker)",
                "minute-open SHORT proxy with no borrow/SSR model",
                "confirmation window 2025-02..2026-05 was previously explored; "
                "NOT a pristine holdout",
            ],
            "positive_regions": [
                {
                    "period_role": r["period_role"],
                    "row_key": r["row_key"],
                    "mean_net_known_fill_per_order_pct": r["mean_net_known_fill_per_order_pct"],
                    "n_known_fills": r["n_known_fills"],
                    "label": "positive per-order net fill on a non-confirmation block; borrow/SSR "
                    "unverified so this is NOT a promotable candidate",
                }
                for r in rows
                if (r["mean_net_known_fill_per_order_pct"] or 0) > 0
            ],
            "all_known_negative": all(
                (r["mean_net_known_fill_per_order_pct"] or 0) < 0 for r in conf
            ),
            "profit_status": profit_status(
                conf, "confirmation", metric="mean_net_known_fill_per_order_pct"
            ),
        },
        "borrow_ssr_blocker": {
            "status": contract.get("status"),
            "required_before_promotion": contract.get("required_before_promotion"),
            "borrow_verified": False,
            "ssr_verified": False,
            "locates_verified": False,
            "financing_netted": False,
            "evidence": [
                "minute-open SHORT proxy with no borrow/SSR model; the producer states no "
                "borrow/SSR is assumed verified",
                "mean_affordable_locate_borrow_bps is reported per row as the head-room only",
                "UNKNOWN exits are unbounded (not zero-imputed)",
            ],
        },
        "qualification": contract.get("qualification"),
        "attempts_rule": contract.get("attempts"),
        "entry_exit": {"entry": contract.get("entry"), "exit": contract.get("exit")},
        "order_budget_usd": contract.get("order_budget"),
        "integer_shares": contract.get("integer_shares"),
        "unknown_exit_rule": contract.get("unknown_exit"),
        "validation_vs_confirmation": {
            "validation_100bps_net_known_fill_pct": next(
                (r["mean_net_known_fill_per_order_pct"] for r in val if r["cost_bps"] == 100), None
            ),
            "confirmation_100bps_net_known_fill_pct": next(
                (r["mean_net_known_fill_per_order_pct"] for r in conf if r["cost_bps"] == 100), None
            ),
        },
        "rows": rows,
        "monthly": monthly,
        "method_notes": [
            "BLOCKER, not a statistical veto: the short lane is blocked on point-in-time "
            "locates/fees, SSR-aware legal fills, as-of bid/ask + impact and margin/buy-in data.",
            "Per-fill net percentages must not be read as capital returns; no whole-day book mean "
            "exists in the producer output (unconditional_daily_mean is null).",
        ],
        "unit_semantics": [
            "mean_net_known_fill_per_order_pct = per-order net return after round-trip bps",
            "mean_daily_known_book_pct = mean over KNOWN-FILL days only, per $1,000 order",
            "known_profit_factor_ratio is a ratio, not a percent",
        ],
        "producer_sha256_reported": contract.get("producer_sha256"),
    }


# --------------------------------------------------------------------------- #
# overnight
# --------------------------------------------------------------------------- #
def build_overnight(root: Path, paths: dict) -> dict:
    s = load_json(paths["overnight/summary.json"])
    rows, monthly = [], []
    for m in s.get("surface", []):
        r = proxy_metric_row("overnight", m, m.get("period", "train"), fills_are_known_only=True)
        r["row_key"] = f"overnight|surface|{m.get('period')}|{m.get('qual')}|{m.get('cost_bps')}"
        r["qualification"] = m.get("qual")
        r["alternative"] = f"overnight_{m.get('qual')}"
        r["selection_eligible"] = (
            m.get("qual") == s.get("selection", {}).get("chosen", {}).get("qualification")
            and m.get("period") == "validation"
        )
        rows.append(r)
        monthly += monthly_rows(
            decoded(m["monthly_mean_lower_bound"]),
            "overnight",
            m.get("period"),
            m.get("cost_bps"),
            book_usd=3000.0,
        )
    for key in ("train", "validation", "confirmation"):
        m = s.get(key)
        if not m:
            continue
        r = proxy_metric_row("overnight", m, key, fills_are_known_only=True)
        r["row_key"] = f"overnight|chosen|{key}|{m.get('cost_bps')}"
        r["qualification"] = m.get("qual")
        r["alternative"] = f"overnight_{m.get('qual')}"
        rows.append(r)
    ladder = s.get("confirmation_ladder", {})
    for cost_key in sorted(ladder, key=lambda c: float(c)):
        m = ladder[cost_key]
        r = proxy_metric_row("overnight", m, "confirmation", fills_are_known_only=True)
        r["row_key"] = f"overnight|chosen|confirmation|{m.get('cost_bps')}"
        r["qualification"] = m.get("qual")
        r["alternative"] = f"overnight_{m.get('qual')}"
        r["is_out_of_fit"] = True
        r["out_of_fit_pristine_holdout"] = False
        rows.append(r)
        monthly += monthly_rows(
            decoded(m["monthly_mean_lower_bound"]),
            "overnight",
            "confirmation",
            m.get("cost_bps"),
            book_usd=3000.0,
        )

    unver = s.get("unverified_action_identities", [])
    reasons = {}
    for u in unver:
        reasons[u.get("unknown_reason", "unspecified")] = (
            reasons.get(u.get("unknown_reason", "unspecified"), 0) + 1
        )
    conf = [r for r in rows if r["period_role"] == "confirmation"]
    return {
        "headline": {
            "study": "overnight",
            "verdict": "negative_at_all_cost_rungs",
            "headline_metric": "confirmation mean_daily_lower_bound of the chosen qA at 100bps",
            "headline_value_pct": next(
                (r["mean_daily_lower_bound_book_pct"] for r in conf if r["cost_bps"] == 100), None
            ),
            "headline_unit": "percent of the $3,000 research sub-book per day",
            "n_known_fills": next((r["n_known_fills"] for r in conf if r["cost_bps"] == 100), None),
            "n_unknown_fills": next(
                (r["n_unknown_fills"] for r in conf if r["cost_bps"] == 100), None
            ),
            "n_days": next((r["n_days"] for r in conf if r["cost_bps"] == 100), None),
            "cost_bps": 100,
            "period_role": "confirmation",
            "book_usd": 3000.0,
            "order_usd": 1000.0,
            "certified": False,
            "certification_blockers": [
                "minute-open entry proxy; no as-of side-aware quote audit",
                "confirmation window 2025-02..2026-05 was previously explored; "
                "NOT a pristine holdout",
                "unverified split-like corporate actions force UNKNOWN exits (see split_audit)",
                "no borrow/financing model on the margin-style research sub-book",
            ],
            "positive_regions": [],
            "all_known_negative": all(
                (r["mean_daily_lower_bound_book_pct"] or 0) < 0 for r in conf
            ),
            "profit_status": profit_status(conf, "confirmation"),
        },
        "adjacency_correction": {
            "rule": "leaderboard file names cross-checked against the canonical calendar; a "
            "boundary signal day whose next session is protected or missing is removed "
            "from the study day universe BEFORE qualification and reported",
            "boundary_excluded_days": s.get("boundary_excluded_days"),
            "n_boundary_excluded_days": len(s.get("boundary_excluded_days", [])),
            "n_day_info_records": len(s.get("day_info", [])),
            "decision_rows": s.get("decision_rows"),
            "period_days": s.get("period_days"),
            "adjacency_record_location": "day_info[] (per-day session_end/next_day/admitted) "
            "+ boundary_excluded_days[]; there is no other key",
        },
        "split_correction": {
            "rule": "split factor = old_rate/new_rate restates the entry basis; a residual "
            "after the factor outside [0.4, 2.5] => unverified => UNKNOWN",
            "splits_source": s.get("sources", {}).get("splits"),
            "splits_sha256": s.get("sources", {}).get("splits_sha256"),
            "split_like_band": [0.4, 2.5],
            "n_unverified_action_identities": len(unver),
            "unverified_action_by_reason": reasons,
            "unverified_action_pairs": [
                {"day": u.get("day"), "ticker": u.get("ticker"), "reason": u.get("unknown_reason")}
                for u in unver
            ],
            "action_verified_column": "intents.parquet/trades files carry action_events, "
            "action_types, action_ids, split_factor, action_verified, "
            "action_note",
        },
        "unfilled_cash_correction": {
            "rule": "a selected slot with no bar at the decision-minute open is UNFILLED: no fee, "
            "the slot is not consumed, cash is retained; it is never counted as a loss "
            "and never as cash-return PnL",
            "chosen_confirmation": {
                "selected_slots": (s.get("confirmation") or {}).get("selected_slots"),
                "selected_filled_slots": (s.get("confirmation") or {}).get("selected_filled_slots"),
                "selected_unfilled_slots": (s.get("confirmation") or {}).get(
                    "selected_unfilled_slots"
                ),
                "rank_skipped": (s.get("confirmation") or {}).get("rank_skipped"),
                "unknown_fills": (s.get("confirmation") or {}).get("unknown_fills"),
                "zero_cash_days": (s.get("confirmation") or {}).get("zero_cash_days"),
                "total_lower_bound_pnl_usd": (s.get("confirmation") or {}).get(
                    "total_lower_bound_pnl"
                ),
            },
            "unknown_by_reason": decoded(s["confirmation"]["unknown_by_reason"])
            if s.get("confirmation", {}).get("unknown_by_reason")
            else None,
            "unknown_reasons_top_level": s.get("unknown_reasons"),
        },
        "selection": s.get("selection"),
        "surface_qualifications": sorted(
            {m.get("qual") for m in s.get("surface", []) if m.get("qual")}
        ),
        "rows": rows,
        "monthly": monthly,
        "method_notes": [
            "Every qualification (qA/qB/qC) x cost (100/150/200) x period row is in rows[]; the "
            "chosen qA is not promoted merely because it was chosen.",
            "Adjacency boundary days (next session protected/missing) are removed BEFORE "
            "qualification and reported explicitly in adjacency_correction.",
            "Unfilled selected slots are no-fill, not cash returns; UNKNOWN exits charge the full "
            "$1,000 unit budget in the lower bound.",
        ],
        "unit_semantics": [
            "mean_daily_lower_bound_book_pct = whole-day lower-bound PnL / $3,000 sub-book",
            "total_lower_bound_pnl_usd = dollars on the $3,000 sub-book",
            "worst_fill/best_fill are per-fill fractions of the $1,000 unit",
            "known_profit_factor_ratio is a ratio, not a percent",
        ],
        "producer_sha256_reported": s.get("sources", {}).get("script_sha256"),
        "calendar_sha256_reported": s.get("sources", {}).get("calendar_sha256"),
    }


# --------------------------------------------------------------------------- #
# micro families
# --------------------------------------------------------------------------- #
def micro_row(study, r, period, period_role, cost, contract, extra_note=None):
    book = float(r.get("research_book") or contract.get("research_book") or 750.0)
    order = float(r.get("order_budget") or contract.get("order_budget") or 250.0)
    notes = [
        "cost is RESIDUAL round-trip bps charged on top of the actual ASK/BID touch",
        "0 bps is a diagnostic, not a free-fill claim",
    ]
    if extra_note:
        notes.extend(extra_note)
    return mrow(
        study=study,
        family=contract.get("hypothesis", study),
        alternative=MICRO_ALTERNATIVE.get(study, study),
        row_key=f"{study}|{period}|{cost}",
        period=period,
        period_role=period_role,
        is_out_of_fit=(period_role == "confirmation"),
        out_of_fit_pristine_holdout=False,
        horizon=r.get("horizon", contract.get("horizon_seconds")),
        horizon_unit="seconds",
        cost_bps=cost,
        cost_basis=RESIDUAL_ON_TOUCH,
        execution_model=TOUCH_250MS,
        fill_convention=ACTUAL_TOUCH,
        n_days=r.get("days") or r.get("period_days"),
        n_attempts=r.get("attempts"),
        n_fills=r.get("fills"),
        n_known_fills=r.get("known_fills"),
        n_unknown_fills=r.get("unknown_fills"),
        n_no_fill_or_skips=r.get("cash_or_slot_skips"),
        n_traded_days=r.get("traded_days"),
        n_months=r.get("months"),
        n_positive_months=r.get("positive_months_lower_bound"),
        mean_net_known_fill_per_order_pct=pick_pct(
            r, "mean_net_known_fill", "mean_net_known_fill_per_order_pct"
        ),
        mean_daily_lower_bound_book_pct=pick_pct(
            r, "mean_daily_lower_bound", "mean_daily_lower_bound_book_pct"
        ),
        daily_se_book_pct=pick_pct(r, "daily_se"),
        ci95_lower_bound_book_pct=(
            [pct(v) for v in r["day_bootstrap_ci95"]] if r.get("day_bootstrap_ci95") else None
        ),
        known_win_rate_pct=pick_pct(r, "known_win_rate"),
        known_profit_factor_ratio=r.get("known_profit_factor"),
        worst_known_fill_per_order_pct=pick_pct(r, "worst_known_fill"),
        book_usd=book,
        order_usd=order,
        coverage_unknown_days=r.get("coverage_unknown_days"),
        coverage_worst_case_mean_daily_book_pct=pick_pct(
            r, "coverage_worst_case_mean_daily", "coverage_worst_case_mean_daily_book_pct"
        ),
        known_complete_day_mean_lower_bound_book_pct=pick_pct(
            r, "known_complete_day_mean_lower_bound", "mean_daily_lower_bound_known_complete_days"
        ),
        quote_supported=(
            r.get("quote_supported") if r.get("quote_supported") is not None else None
        ),
        finite=(r.get("mean_net_known_fill") is not None),
        notes=notes,
    )


def micro_months(contract):
    p = contract.get("periods", {}) or {}
    return {
        "development_months": p.get("development", {}).get("months"),
        "validation_months": p.get("validation", {}).get("months"),
        "confirmation_months": p.get("confirmation", {}).get("months"),
        "protected_unread": contract.get("protected_unread", PROTECTED_UNREAD),
    }


def micro_headline(study, rows, contract, cert, verdict_text):
    conf = [r for r in rows if r["period_role"] == "confirmation"]
    book = float(contract.get("research_book") or 750.0)
    order = float(contract.get("order_budget") or 250.0)
    at100 = next((r for r in conf if r["cost_bps"] == 100), None)
    return {
        "study": study,
        "verdict": verdict_text,
        "headline_metric": "confirmation mean_daily_lower_bound at 100bps residual",
        "headline_value_pct": at100["mean_daily_lower_bound_book_pct"] if at100 else None,
        "headline_unit": "percent of the $750 research book per day (3 slots x $250)",
        "n_known_fills": at100["n_known_fills"] if at100 else None,
        "n_unknown_fills": at100["n_unknown_fills"] if at100 else None,
        "n_days": at100["n_days"] if at100 else None,
        "cost_bps": 100,
        "period_role": "confirmation",
        "book_usd": book,
        "order_usd": order,
        "certified": bool(cert),
        "certification_blockers": [
            "quote-supported touch is capacity-checked, never an exchange fill guarantee",
            "confirmation months 2025-03..2025-08 were previously explored; NOT a pristine holdout",
            "margin-style funded cash reuse is assumed, not a small-cash-account claim",
        ],
        "positive_regions": [],
        "all_known_negative": all((r["mean_daily_lower_bound_book_pct"] or 0) < 0 for r in conf),
        "profit_status": profit_status(conf, "confirmation"),
    }


def build_micro_flow(root: Path, paths: dict) -> dict:
    s = load_json(paths["micro_flow/summary.json"])
    contract = s.get("contract", {})
    rows, monthly = [], []
    full = optional(root, "micro_flow/results.json")
    full_rows = {}
    if full is not None:
        fr = load_json(full).get("results", [])
        full_rows = {(r.get("period"), r.get("cost_bps_residual")): r for r in fr}
    for period in ("development", "validation", "confirmation"):
        p = s.get("summary", {}).get(period, {})
        for cost in sorted((p.get("by_cost") or {}), key=lambda c: float(c)):
            m = p["by_cost"][cost]
            key = (period, float(cost))
            src = full_rows.get(key, {})
            r = micro_row("micro_flow", {**src, **m}, period, period, float(cost), contract)
            r["row_key"] = f"micro_flow|{period}|{float(cost):g}"
            rows.append(r)
            monthly += monthly_rows(
                src.get("monthly_mean_lower_bound"),
                "micro_flow",
                period,
                float(cost),
                book_usd=750.0,
            )
            monthly += monthly_rows(
                src.get("yearly_mean_lower_bound"),
                "micro_flow",
                period,
                float(cost),
                book_usd=750.0,
            )
    cov = {
        period: _condense_coverage(c)
        for period, c in (s.get("coverage") or {}).items()
        if isinstance(c, dict)
    }
    return {
        "headline": micro_headline("micro_flow", rows, contract, False, s.get("verdict")),
        "hypothesis": contract.get("hypothesis"),
        "family": contract.get("family"),
        "common_gate": contract.get("common_gate"),
        "qualification": contract.get("qualification"),
        "feature_source": contract.get("feature_source"),
        "state_grid": contract.get("state_grid"),
        "entry": contract.get("entry"),
        "exit": contract.get("exit"),
        "month_sets": micro_months(contract),
        "actual_touch_and_residual_cost": {
            "rule": contract.get("cost_note"),
            "costs_bps_residual_round_trip": contract.get("costs_bps_residual_round_trip"),
            "cost_basis": RESIDUAL_ON_TOUCH,
            "entry_price": "actual ASK at signal+250ms",
            "exit_price": (
                "actual BID at entry+60s, else first subsequent regular quote <= session_end"
            ),
            "latency_ms": 250,
            "order_budget_usd": contract.get("order_budget"),
            "max_slots": contract.get("max_slots"),
            "research_book_usd": contract.get("research_book"),
        },
        "coverage": cov,
        "coverage_worst_bounds": {
            "rule": "any unknown day is charged -100% of the $750 book; the known-complete-day "
            "mean is reported separately and is never used to hide an unknown day",
            "by_period": _worst_by_period(rows),
        },
        "signals_summary": {
            p: {
                "dates": v.get("dates"),
                "signals": v.get("signals"),
                "quote_unsupported_signals": v.get("quote_unsupported_signals"),
                "delayed_exit_signals": v.get("delayed_exit_signals"),
            }
            for p, v in (s.get("summary") or {}).items()
        },
        "rows": rows,
        "monthly": monthly,
        "method_notes": [
            "Single fixed family and gate registered before outcomes; no threshold, gain, hold "
            "or symbol tuning and no HPO.",
            "0 bps residual is a diagnostic only; negative at every cost rung on every period.",
        ],
        "unit_semantics": [
            "mean_net_known_fill_per_order_pct = per-$250-order net return after residual bps",
            "mean_daily_lower_bound_book_pct = whole-day book PnL / $750 research book",
            "coverage_worst_case_mean_daily_book_pct charges unknown days at -100% of the book",
        ],
        "assumptions": (load_json(full).get("assumptions") if full is not None else []),
        "producer_sha256_reported": contract.get("producer_sha256"),
    }


def build_micro_reclaim(root: Path, paths: dict) -> dict:
    s = load_json(paths["micro_reclaim/summary.json"])
    contract = s.get("contract", {})
    rows, monthly = [], []
    for r0 in s.get("results", []):
        period = r0.get("period")
        role = "development" if period.startswith("development") else period
        extra = []
        if period.endswith("_complete_days_only"):
            extra.append(
                "known-complete-days-only variant: excludes days with no firm quotes; "
                "the all-days variant is the conservative read"
            )
        r = micro_row(
            "micro_reclaim",
            r0,
            period,
            role,
            float(r0.get("cost_bps_residual")),
            contract,
            extra_note=extra,
        )
        rows.append(r)
        monthly += monthly_rows(
            r0.get("monthly_mean_lower_bound"),
            "micro_reclaim",
            role,
            float(r0.get("cost_bps_residual")),
            book_usd=750.0,
        )
    cov = {
        period: _condense_coverage(c)
        for period, c in (s.get("coverage") or {}).items()
        if isinstance(c, dict)
    }
    verdict = s.get("verdict", {})
    return {
        "headline": micro_headline(
            "micro_reclaim",
            rows,
            contract,
            s.get("certified", False),
            "not_positive" if not s.get("certified") else "certified",
        ),
        "headline_extra": {
            "verdict_by_period": {
                p: dict(d) for p, d in verdict.items() if isinstance(d, dict)
            },
            "verdict_scalar": {p: d for p, d in verdict.items() if not isinstance(d, dict)},
            "candidate": verdict.get("candidate"),
            "certified": s.get("certified"),
            "days_seen": len(s.get("days_seen", [])),
        },
        "hypothesis": contract.get("hypothesis"),
        "conditions": contract.get("conditions"),
        "liveness": contract.get("liveness"),
        "admission": contract.get("admission"),
        "state_grid": contract.get("state_grid"),
        "selection": contract.get("selection"),
        "entry_exit": {"entry": contract.get("entry"), "exit": contract.get("exit")},
        "depth_rule": contract.get("depth"),
        "account_rule": contract.get("account"),
        "month_sets": {
            "development_months": contract.get("periods", {}).get("development_months"),
            "validation_months": contract.get("periods", {}).get("validation_months"),
            "confirmation_months": contract.get("periods", {}).get("confirmation_months"),
            "protected_unread": PROTECTED_UNREAD,
        },
        "actual_touch_and_residual_cost": {
            "rule": "actual side-aware ASK/BID prices plus residual round-trip bps; "
            "0bps is a diagnostic, not a free-fill claim",
            "costs_bps_residual_round_trip": contract.get("costs_bps_residual_round_trip"),
            "cost_basis": RESIDUAL_ON_TOUCH,
            "entry_price": "market ASK at signal_us+250ms",
            "exit_price": "market BID at entry+60s (literal); no fresh firm quote => first "
            "subsequent regular quote <= session_end, never a favourable price",
            "latency_us": contract.get("latency_us"),
            "order_budget_usd": 250.0,
            "research_book_usd": 750.0,
            "max_slots": 3,
            "depth_requirement": "entry ASK and exit BID L1 must each cover $250 notional",
        },
        "coverage": cov,
        "coverage_worst_bounds": {
            "rule": "development days 2021-02-01..2021-04-23 carry only '?' quote conditions "
            "under the repo's regular-quote rule, so they are coverage-unknown and are "
            "charged -100% of the $750 book each",
            "by_period": _worst_by_period(rows),
        },
        "execution_model": contract.get("execution_model"),
        "hpo": contract.get("hpo"),
        "sibling_isolation": contract.get("sibling_isolation"),
        "rows": rows,
        "monthly": monthly,
        "method_notes": [
            "One fixed formulation, no HPO, no horizon or threshold search, one attempt per "
            "ticker/day; sibling results were not consulted for selection.",
            "The development portfolio is NOT certified because 58 days are coverage-unknown.",
            "cost is RESIDUAL bps on top of the actual ASK/BID touch, not a round-trip charge "
            "on a proxy fill.",
        ],
        "unit_semantics": [
            "mean_net_known_fill_per_order_pct = per-$250-order net return after residual bps",
            "mean_daily_lower_bound_book_pct = whole-day book PnL / $750 research book",
            "coverage_worst_case_mean_daily_book_pct charges unknown days at -100% of the book",
        ],
        "producer_sha256_reported": contract.get("producer_sha256"),
        "contract_sha256_reported": contract.get("contract_sha256"),
    }


# --------------------------------------------------------------------------- #
# quote audits
# --------------------------------------------------------------------------- #
def quote_block(q: dict, path: Path, study: str) -> dict:
    sc = q.get("status_counts", {}) or {}
    covered = q.get("quote_supported_pairs", 0)
    unknown = q.get("unknown_pairs", 0)
    total = q.get("trades", covered + unknown)
    return {
        "study": study,
        "source": str(path),
        "source_sha256": sha256_file(path),
        "trades_input": q.get("trades_input"),
        "n_trades": q.get("trades"),
        "n_quote_supported_pairs": covered,
        "n_unknown_pairs": unknown,
        "n_covered": covered,
        "n_uncovered": unknown,
        "coverage_pct": pct(covered / total) if total else None,
        "unknown_pct": pct(unknown / total) if total else None,
        "status_counts": sc,
        "covered_mean_net_by_residual_bps": {
            str(k): pct(v)
            for k, v in sorted(
                (q.get("covered_mean_net") or {}).items(), key=lambda kv: float(kv[0])
            )
        },
        "covered_mean_net_fraction": {
            str(k): v
            for k, v in sorted(
                (q.get("covered_mean_net") or {}).items(), key=lambda kv: float(kv[0])
            )
        },
        "covered_mean_net_unit": "percent per order, COVERED (quote-supported) pairs only",
        "whole_portfolio_certified": q.get("whole_portfolio_certified"),
        "quote_is_not_exchange_fill": q.get("quote_is_not_exchange_fill"),
        "positive_subset_not_certified": bool(
            any((v or 0) > 0 for v in (q.get("covered_mean_net") or {}).values())
            and not q.get("whole_portfolio_certified")
        ),
        "latency_ms": q.get("latency_ms"),
        "max_quote_age_s": q.get("max_quote_age_s"),
        "order_budget_usd": q.get("order_budget"),
        "status_label": (
            "covered pairs are quote-capacity supported; a quote is NOT an exchange "
            "fill and whole-portfolio positivity is NEVER claimed from this subset"
        ),
        "unit_source": q.get("unit_source"),
    }


# --------------------------------------------------------------------------- #
# profit status: from actual cost / block / support / unknown evidence only
# --------------------------------------------------------------------------- #
def profit_status(rows, period_role, metric="mean_daily_lower_bound_book_pct"):
    """Explicit profit status from measured evidence. No statistical veto: a negative
    bootstrap CI is not the reason; the measured cost-rung values, block, support and
    unknown burden are."""
    if not rows:
        return {
            "status": "not_measured",
            "reason": f"no {period_role} rows present",
            "evidence": [],
        }
    ev = []
    for r in rows:
        ev.append(
            {
                "row_key": r["row_key"],
                "cost_bps": r["cost_bps"],
                "metric": metric,
                "value_pct": r.get(metric),
                "n_known_fills": r["n_known_fills"],
                "n_unknown_fills": r["n_unknown_fills"],
                "n_days": r["n_days"],
                "period_role": r["period_role"],
                "out_of_fit_pristine_holdout": r["out_of_fit_pristine_holdout"],
            }
        )
    vals = [r.get(metric) for r in rows if r.get(metric) is not None]
    if not vals:
        return {
            "status": "not_measured",
            "reason": "metrics absent/null in every row",
            "evidence": ev,
        }
    if all(v < 0 for v in vals):
        status = "negative_at_every_measured_cost_rung"
    elif all(v > 0 for v in vals):
        status = "positive_at_every_measured_cost_rung"
    else:
        status = "mixed_across_cost_rungs"
    unknown_total = sum(r["n_unknown_fills"] or 0 for r in rows)
    fill_total = sum((r["n_fills"] or 0) for r in rows)
    return {
        "status": status,
        "basis": (
            "measured cost-rung values on the out-of-fit/late block only; NOT a "
            "median test, tail deletion or bootstrap-significance veto"
        ),
        "n_rows": len(rows),
        "min_value_pct": min(vals),
        "max_value_pct": max(vals),
        "unknown_fill_share_pct": pct(unknown_total / fill_total) if fill_total else None,
        "not_pristine_holdout": all(r["out_of_fit_pristine_holdout"] is False for r in rows),
        "profitable_candidate_claim": False,
        "evidence": ev,
    }


# --------------------------------------------------------------------------- #
# evidence context / data requirements / execution limits
# --------------------------------------------------------------------------- #
def rel_mtime(path: Path):
    return mtime_iso(path) if path.is_file() else None


def _summ_list(v: list) -> dict:
    return {
        "n": len(v),
        "first": min(v) if v else None,
        "last": max(v) if v else None,
        "days": v if len(v) <= 12 else v[:12] + ["...truncated"],
    }


def _condense_coverage(c: dict) -> dict:
    """Pass a producer coverage block through, trimming only the bulky day lists.

    Producers keep adding coverage keys (coverage_kind, unknown_reason, coverage_epoch,
    no_regular_quote_symbols, quote_condition_unknown_symbols, cash_days_legit, ...).
    Rather than hand-picking a subset and silently dropping the rest, every scalar key is
    carried verbatim; a top-level list becomes {n, first, last, days[]}; a dict whose values
    are ALL lists (e.g. ``unknown_days_by_reason``) is summarised the same way so the dates
    stay datable; a dict of per-day scalars (e.g. ``no_regular_quote_symbols``) is kept whole.
    """
    out = {}
    for k, v in (c or {}).items():
        if isinstance(v, list):
            out[k] = _summ_list(v)
        elif isinstance(v, dict):
            if v and all(isinstance(x, list) for x in v.values()):
                out[k] = {kk: _summ_list(x) for kk, x in v.items()}
            else:
                out[k] = {kk: (_summ_list(x) if isinstance(x, list) else x) for kk, x in v.items()}
        else:
            out[k] = v
    return out


def _worst_by_period(
    rows: list[dict], periods=("development", "validation", "confirmation")
) -> dict:
    """Most conservative read per period across every horizon/cost row.

    Takes the MAX coverage_unknown_days and the MIN (worst) lower-bound series, so a
    horizon whose coverage is worse cannot be hidden behind a better-looking sibling.
    `<period>_complete_days_only` slices are EXCLUDED from the conservative read (they are
    the known-complete subset and are never promoted to a whole-portfolio claim).
    """
    out = {}
    for p in periods:
        sel = [
            r
            for r in rows
            if (r["period"] == p or r["period_role"] == p)
            and not str(r["period"]).endswith("_complete_days_only")
        ]
        if not sel:
            continue

        def _min(key, sel=sel):
            vals = [r[key] for r in sel if r.get(key) is not None]
            return min(vals) if vals else None

        unk = [
            r["coverage_unknown_days"] for r in sel if r.get("coverage_unknown_days") is not None
        ]
        out[p] = {
            "coverage_unknown_days": max(unk) if unk else None,
            "coverage_worst_case_mean_daily_book_pct": _min(
                "coverage_worst_case_mean_daily_book_pct"
            ),
            "known_complete_day_mean_lower_bound_book_pct": _min(
                "known_complete_day_mean_lower_bound_book_pct"
            ),
            "mean_daily_lower_bound_book_pct": _min("mean_daily_lower_bound_book_pct"),
            "n_rows_considered": len(sel),
            "row_keys": [r["row_key"] for r in sel],
        }
    return out


def _annual_ev_facts(
    root: Path, res: dict, contract: dict, chosen: dict, quote_audits: dict
) -> dict:
    """Compact annual-EV source facts for the rare h60 lead.

    Every dollar/percent figure below is DERIVED on the fly from the staged artifacts
    (fills, days, mean_net_known_fill, covered quote means, and the daily lower-bound
    bootstrap CI95) so it can never drift from the data.  The few inputs that are not in
    the artifacts are carried as explicitly labelled parent/orchestration-supplied
    constants with their stated interpretation:
      * 252 trading days per year (calendar convention),
      * the $3,000 research book (this study's own sub-book),
      * the live SIP subscription price (external vendor page).

    The annualised CI band is NOT a magic number and NOT parent-supplied: it is
    ``bootstrap_daily.ci95_lo/hi x 252 x 3000`` read straight out of the staged
    confirmation artifact.  It is the historical mean uncertainty of the annualised
    figure, NOT a future prediction interval.
    """
    conf = (res.get("confirmation_by_cost") or {}).get("100") or {}
    fills = conf.get("fills")
    days = conf.get("days")
    net = conf.get("mean_net_known_fill")
    order = float((contract.get("replay") or {}).get("order_budget") or 1000.0)
    book = 3000.0
    days_per_year = 252  # parent-supplied calendar convention
    fills_per_year = fills / days * days_per_year if (fills and days) else None
    annual_ev = (
        round(fills_per_year * order * net, 3) if None not in (fills_per_year, net) else None
    )
    simple_book_pct = round(annual_ev / book * 100, 4) if annual_ev is not None else None
    # annualised CI: the DAILY lower-bound bootstrap CI95, scaled by the same 252 x 3000
    # constants the rest of the block uses.  Both raw fields live in the staged
    # confirmation artifact, so the band is reproducible line for line.
    boot = conf.get("daily_lower_bound_bootstrap") or conf.get("bootstrap_daily") or {}
    ci95_lo = boot.get("ci95_lo", boot.get("ci95_low"))
    ci95_hi = boot.get("ci95_hi", boot.get("ci95_high"))
    annual_ci = [None, None]
    if ci95_lo is not None:
        annual_ci[0] = round(float(ci95_lo) * days_per_year * book, 2)
    if ci95_hi is not None:
        annual_ci[1] = round(float(ci95_hi) * days_per_year * book, 2)
    qa = (quote_audits or {}).get("confirmation_late") or {}
    cov = qa.get("covered_mean_net_by_residual_bps") or {}
    covered_n, trades_n = qa.get("n_covered"), qa.get("n_trades")
    # quote_block already carries the RAW fraction alongside the rounded percent, so the
    # hypothetical is computed from the unrounded value and cannot drift by rounding.
    raw_cov = qa.get("covered_mean_net_fraction") or {}
    cov25 = raw_cov.get("25")
    hypothetical = (
        round(fills_per_year * order * cov25, 3) if None not in (fills_per_year, cov25) else None
    )
    quoted_contribution = (
        round(hypothetical * covered_n / trades_n, 3)
        if None not in (hypothetical, covered_n, trades_n)
        else None
    )
    return {
        "applies_to": (
            "learned_sparse_extension h60/thr0.03 — the ONLY family in this packet "
            "whose late block is positive; retained as a DISCOVERY ALPHA LEAD, not a "
            "closed/failed candidate"
        ),
        "formula": (
            f"fills_per_year = ({fills} fills / {days} days) x {days_per_year}; "
            "annual_ev_usd = fills_per_year x order_usd x mean_net_known_fill"
        ),
        "inputs": {
            "fills_confirmation_100bps": fills,
            "known_fills": conf.get("known_fills"),
            "unknown_fills": conf.get("unknown_fills"),
            "confirmation_days": days,
            "mean_net_known_fill_pct": pick_pct(conf, "mean_net_known_fill"),
            "order_usd": order,
            "research_book_usd": book,
            "trading_days_per_year": days_per_year,
            "trading_days_per_year_source": "parent/orchestration-supplied calendar convention",
        },
        "derived": {
            "fills_per_year": round(fills_per_year, 4) if fills_per_year is not None else None,
            "annual_ev_usd_before_opex_and_tax": annual_ev,
            "annual_ev_pct_of_3k_book_simple": simple_book_pct,
            "annual_ev_pct_is_simple_not_cagr": True,
        },
        "quote_covered_conditional": {
            "covered_pairs": covered_n,
            "unknown_pairs": qa.get("n_unknown_pairs"),
            "trades": trades_n,
            "coverage_pct": qa.get("coverage_pct"),
            "covered_mean_net_per_order_pct_at_25bps_residual": cov.get("25"),
            "covered_mean_net_fraction_at_25bps_residual": raw_cov.get("25"),
            "hypothetical_annual_ev_usd_if_every_fill_looked_like_the_covered_subset": hypothetical,
            "representativity_of_the_covered_subset": "NOT CONFIRMED",
            "quoted_subset_known_annual_contribution_usd": quoted_contribution,
            "whole_portfolio_annual_ev_claimed": False,
            "why": (
                "the covered subset is 77 of 143 pairs and is quote-capacity-supported, not "
                "exchange-filled; the remaining 66 pairs are UNKNOWN. Extrapolating the "
                "covered mean to all fills is a conditional hypothetical, not a measured "
                "portfolio EV."
            ),
        },
        "annual_mean_ci_annualized_usd": annual_ci,
        "annual_mean_ci_interpretation": (
            "historical mean uncertainty of the annualised figure, NOT a future prediction "
            "interval and NOT a claim about any forward period"
        ),
        "annual_mean_ci_source": (
            "DERIVED from the staged confirmation artifact "
            "confirmation_by_cost['100'].bootstrap_daily.ci95_lo/ci95_hi x 252 trading "
            "days x $3,000 research book"
        ),
        "annual_mean_ci_derivation": {
            "bootstrap_field": "confirmation_by_cost['100'].bootstrap_daily.ci95_lo/ci95_hi",
            "daily_lower_bound_ci95_lo_fraction": ci95_lo,
            "daily_lower_bound_ci95_hi_fraction": ci95_hi,
            "trading_days_per_year": days_per_year,
            "research_book_usd": book,
            "formula": "annual_usd = ci95 * trading_days_per_year * research_book_usd",
            "reproduces_band_exactly": (annual_ci[0] is not None and annual_ci[1] is not None),
        },
        "live_data_cost": {
            "usd_per_month": 99.0,
            "usd_per_year": 1188.0,
            "applies_unless_already_covered": True,
            "source": (
                "https://docs.alpaca.markets/us/docs/about-market-data-api (updated 2026-10-06)"
            ),
        },
        "cost_stress_policy": (
            "no automatic 100 / 150 / 200 bps or CI gate rejection. 100bps means a 1% "
            "round-trip frictional charge; 1000bps was never used anywhere in this packet and "
            "nothing here should be read as a 1000bps stress."
        ),
        "veto_absent": [
            "median_or_tail_gate",
            "bootstrap_significance_gate",
            "cost_stress_gate",
            "prediction_interval_gate",
        ],
    }


def build_micro_coverage_integrity(root: Path, studies: dict) -> dict:
    """Cross-family coverage reconciliation.

    micro_flow and micro_reclaim share the same development window and the same raw SIP
    quote substrate, yet disagree about whether the early-2021 days are usable: reclaim
    marks 2021-02-01..2021-04-23 as no-firm-quote coverage-unknown days, flow reports the
    same window as complete.  The packet must not resolve that disagreement itself; it
    reports it, names the owner, and carries the conservative read.
    """
    # Read the RAW summaries for both families.  The packet blocks condense the day lists
    # (which would lose the disputed window), and the producers keep adding coverage keys,
    # so the comparison must run on the producers' own vocabulary.
    raw_flow = optional(root, "micro_flow/summary.json")
    raw_reclaim = optional(root, "micro_reclaim/summary.json")

    def _raw_cov(path):
        if path is None:
            return {}
        try:
            return load_json(path).get("coverage") or {}
        except (ValueError, OSError):
            return {}

    def _unknown_count(c: dict) -> int:
        """Coverage-unknown days in a producer coverage block, whichever key it uses."""
        for key in ("unknown_days", "coverage_unknown_days", "exception_days"):
            v = c.get(key)
            if isinstance(v, list):
                return len(v)
            if isinstance(v, dict):
                return sum(len(x) for x in v.values() if isinstance(x, list))
            if isinstance(v, int):
                return v
        return 0

    raw_flow_cov = _raw_cov(raw_flow)
    raw_reclaim_cov = _raw_cov(raw_reclaim)

    reclaim_exc = {}
    first = last = None
    for period, c in raw_reclaim_cov.items():
        days = (
            (c.get("exception_days") or {}).get("no_firm_quotes")
            or (c.get("exception_days") or {}).get("quote_condition_unknown")
            or []
        )
        if not days:
            ud = c.get("unknown_days")
            days = ud if isinstance(ud, list) else []
        if days:
            reclaim_exc[period] = {
                "n_exception_days": len(days),
                "first_day": min(days),
                "last_day": max(days),
                "flow_unknown_days_same_period": _unknown_count(raw_flow_cov.get(period) or {}),
            }
            first = min(days) if first is None else min(first, min(days))
            last = max(days) if last is None else max(last, max(days))

    disputed = bool(reclaim_exc) and all(
        raw_reclaim_cov and _unknown_count(raw_flow_cov.get(p) or {}) == 0 for p in reclaim_exc
    )
    flow_all = _condense_coverage(raw_flow_cov)
    reclaim_all = _condense_coverage(raw_reclaim_cov)

    def _unknown_windows(cov: dict) -> dict:
        out = {}
        for period, c in cov.items():
            rec = {"n_unknown_days": _unknown_count(c)}
            for key in ("unknown_days", "coverage_unknown_days"):
                v = c.get(key)
                if isinstance(v, list) and v:
                    rec["first_day"], rec["last_day"] = min(v), max(v)
                    break
            exc = c.get("exception_days")
            if isinstance(exc, dict):
                for kk, vv in exc.items():
                    if isinstance(vv, list) and vv and "first_day" not in rec:
                        rec[f"{kk}_first_day"], rec[f"{kk}_last_day"] = min(vv), max(vv)
            out[period] = rec
        return out

    return {
        "status": "DISPUTED" if disputed else "consistent",
        "owner": "CoreCorrection (owns the micro_flow coverage fix)",
        "comparison_basis": (
            "per-period coverage-unknown day counts: micro_reclaim's "
            "no_firm_quotes / quote_condition_unknown exception days vs "
            "micro_flow's unknown_days for the SAME period"
        ),
        "micro_flow_coverage": flow_all,
        "micro_reclaim_coverage": reclaim_all,
        "unknown_windows_by_family": {
            "micro_flow": _unknown_windows(raw_flow_cov),
            "micro_reclaim": _unknown_windows(raw_reclaim_cov),
        },
        "micro_reclaim_unknown_windows": reclaim_exc,
        "disputed_window": [first, last] if disputed else None,
        "finding": (
            "micro_reclaim marks the early-2021 days "
            f"{first}..{last} as quote-condition-unknown (only '?' conditions under the repo's "
            "regular-quote rule) and charges each of them -100% of its $750 book, while "
            "micro_flow reports the SAME window as complete with 0 unknown days. One of the two "
            "coverage classifiers is wrong."
        )
        if disputed
        else (
            "no coverage contradiction detected between the micro families on the shared "
            "development window; both report the same coverage-unknown days and both bound them "
            "at -100% of the book"
        ),
        "consequence_for_this_packet": (
            "Until the flow coverage fix lands, micro_flow's development "
            "mean_daily_lower_bound is NOT a certified whole-book bound: it may be treating "
            "58 quote-condition-unknown days as legitimate zero-cash days. micro_reclaim's "
            "coverage_worst_case_mean_daily is the conservative read for that window. The "
            "packet therefore reports both, claims no certified flow result, and does not "
            "count any flow day as cash."
        )
        if disputed
        else (
            "both families now report the same coverage-unknown days and bound them at -100% "
            "of the book, so the conservative read is the shared one; a coverage-unknown day "
            "is still never counted as cash"
        ),
        "not_resolved_here": True,
    }


def build_evidence_context(root: Path, studies: dict | None = None) -> dict:
    stale_docs = []
    for rel in ("HANDOFF.md", "SPEC.md", "README.md", "SOUL.md"):
        p = STALE_WORKTREE / rel
        if p.is_file():
            stale_docs.append({"path": str(p), "mtime_utc": rel_mtime(p), "bytes": size_bytes(p)})
    for rel in ("factory/STATE.md", "factory/HYPOTHESES.jsonl", "factory/EXPERIMENTS.jsonl"):
        p = STALE_WORKTREE / rel
        if p.is_file():
            stale_docs.append({"path": str(p), "mtime_utc": rel_mtime(p), "bytes": size_bytes(p)})
    for rel in ("factory/STATE.md", "factory/HYPOTHESES.jsonl", "HANDOFF.md", "SPEC.md"):
        p = ROOT / rel
        if p.is_file():
            stale_docs.append({"path": str(p), "mtime_utc": rel_mtime(p), "bytes": size_bytes(p)})

    h025_exit = STALE_WORKTREE / "factory/artifacts/h025_research/exit/summary.json"
    h025_notes = STALE_WORKTREE / "factory/artifacts/h025_research/SELECTION_NOTES.md"
    h025_exec = STALE_WORKTREE / "factory/artifacts/h025_research/execution/all_summary.json"
    h025 = {
        "status": "RESOLVED NEGATIVE",
        "facts": [
            "88-90% of the H025 exit targets printed in the SAME bar as the entry, i.e. before "
            "the entry was achievable; the historical target race is not deployable as measured",
            "pf2 per-fill net is -1.72% across the 1,046 allowed development days",
            "baseline blanket baskets are negative",
        ],
        "facts_source": (
            "orchestration brief / parent-verified closure record; these three "
            "numbers are NOT re-derived from a committed artifact in this repo "
            "and are carried as reported context"
        ),
        "grounded_in_this_repo": [],
    }
    if h025_exit.is_file():
        d = load_json(h025_exit)
        h025["grounded_in_this_repo"].append(
            {
                "path": str(h025_exit),
                "sha256": sha256_file(h025_exit),
                "status": d.get("status"),
                "calendar_days": d.get("calendar_days"),
                "candidate_exit": d.get("candidate_exit"),
                "excluded": d.get("excluded"),
                "candidate_selection": d.get("candidate_selection"),
            }
        )
    if h025_notes.is_file():
        h025["grounded_in_this_repo"].append(
            {
                "path": str(h025_notes),
                "note": (
                    "1046 legitimate days = 533 original development (2021-02-01..2023-03-14) + "
                    "513 already-seen larger block; all already H025-seen, so this is "
                    "development/temporal replication and NOT fresh OOS; 2024 + Jan/Feb 2025 + "
                    "2026-06..08 excluded from any new selection"
                ),
            }
        )
    if h025_exec.is_file():
        d = load_json(h025_exec)
        h025["grounded_in_this_repo"].append(
            {
                "path": str(h025_exec),
                "stage": d.get("stage"),
                "calendar_days": d.get("calendar_days"),
                "scope": d.get("scope"),
                "same_bar_audit_fields_present": [
                    k
                    for k in (
                        "fillbar_target_count",
                        "fillbar_target_without_close_proof",
                        "fillbar_stop_and_target_count",
                    )
                    if any(k in c for c in (d.get("cells") or {}).values())
                ],
            }
        )
    h025["root_doc_staleness_effect"] = (
        "The latest worktree's root main docs (HANDOFF.md 2026-10-06, factory/STATE.md, "
        "factory/HYPOTHESES.jsonl) still record H025 as 'OOS-PASS-PAPER' flush-bid with a "
        "+1.14%/trade 2024 pass.  That record is STALE relative to the resolved-negative "
        "closure: those fills were never certified as real exchange queue priority, and the "
        "same-bar target race invalidates the deployable form.  Consumers must read the exit / "
        "execution / core artifacts, not the root status line."
    )

    # measured anchor for the new lane: the panel + all families
    data_prereq = {
        "protected_unread_dates": PROTECTED_UNREAD,
        "protected_unread_note": (
            "2024, 2025-01 and 2026-06..2026-08 outcomes were never read "
            "by any producer in this lane; the 2025-02..2026-05 "
            "confirmation block was previously EXPLORED in other lanes, so "
            "it is out-of-fit but NOT a pristine holdout"
        ),
        "shared_data_mount": {
            "path": "/mnt/c (Windows mount)",
            "avail_gib": _avail_gib("/mnt/c") if os.path.isdir("/mnt/c") else None,
            "avail_reported_by_parent": "24 GiB free",
            "measurement": "os.statvfs at packet build time (df -h /mnt/c reported 24G)",
            "prerequisite": (
                "the shared data Windows mount must stay under its free head-room; "
                "new harvest/backfill must be staged to Linux before use"
            ),
        },
        "outputs_root": str(root),
        "outputs_note": (
            "all producer output roots are Linux-side under ~/alpha-data/open-search-v1"
        ),
        "network": "net SIP decode is fast under WSL; raw tick/NBBO micro replay is the slow leg",
        "panel_prerequisite": (
            "the 1,066-day new-bar panel (1,076,304 causal symbol-minute "
            "rows) must exist and be status=built for all days before any "
            "minute-proxy family can run"
        ),
        "micro_data_prerequisite": (
            "raw SIP prints + NBBO with displayed size on the one-second "
            "state grid; both micro studies ran 291 candidate days "
            "complete, so micro data availability is not the blocker "
            "for that lane"
        ),
    }
    return {
        "h025_closure": h025,
        "root_staleness": {
            "latest_worktree": str(STALE_WORKTREE),
            "stale_documents": stale_docs,
            "truth_location": (
                "/home/hillel/projects/Alpacatrader NEW causal research files "
                "(alpha_* producers, factory/artifacts, and this packet)"
            ),
        },
        "protected_unread_dates": PROTECTED_UNREAD,
        "data_and_storage_prerequisites": data_prereq,
        "micro_coverage_integrity": (
            build_micro_coverage_integrity(root, studies) if studies else None
        ),
        "measured_baseline_negative": {
            "statement": "baseline blanket first-liquidity baskets are negative on validation at "
            "every horizon (see events baseline_validation_context rows)",
        },
    }


def _audit_line(label: str, audit: dict) -> str:
    if not audit:
        return f"{label}: no quote audit present"
    cov = audit.get("covered_mean_net_by_residual_bps") or {}
    parts = ", ".join(f"{k} bps -> {v:+.4f}%/order" for k, v in cov.items())
    return (
        f"{label}: {audit.get('n_covered')}/{audit.get('n_trades')} entry/exit pairs "
        f"covered ({audit.get('coverage_pct')}%), whole_portfolio_certified="
        f"{audit.get('whole_portfolio_certified')}; covered-pair mean net per order: {parts}"
    )


def build_data_requirements(studies: dict) -> dict:
    learned = studies["learned"]
    qv = learned["quote_audits"]["validation_2023"]
    qc = learned["quote_audits"]["confirmation_late"]
    qa = studies["events"].get("quote_audit")
    covered_0bps_net = (
        list((qv.get("covered_mean_net_by_residual_bps") or {}).values())[0]
        if qv.get("covered_mean_net_by_residual_bps")
        else None
    )
    sparse = studies.get("learned_sparse_extension")
    sparse_q = (sparse.get("quote_audits") or {}).get("confirmation_late") if sparse else None
    audit_lines = [
        _audit_line("learned validation (2023)", qv),
        _audit_line("learned confirmation (2025-02..2026-05)", qc),
        _audit_line("events confirmation (2025-02..2026-05)", qa),
        _audit_line("learned_sparse_extension confirmation (rare h60 cohort @100bps)", sparse_q),
    ]
    chosen = learned["selection"]["chosen"]
    val_row = next(
        (
            r
            for r in learned["rows"]
            if r["row_key"].startswith(
                f"learned|validation|h{chosen['horizon']}|thr{chosen['threshold']}|"
            )
            and r["cost_bps"] == chosen.get("selection_cost_bps")
        ),
        None,
    )
    sparse_late = None
    sparse_claim = None
    if sparse:
        conf_rows = [r for r in sparse["rows"] if r["period_role"] == "confirmation"]
        sparse_late = [
            {
                "cost_bps": r["cost_bps"],
                "mean_daily_lower_bound_book_pct": r["mean_daily_lower_bound_book_pct"],
                "n_known_fills": r["n_known_fills"],
                "n_unknown_fills": r["n_unknown_fills"],
            }
            for r in conf_rows
        ]
        rare = sparse.get("rare_extension") or {}
        sparse_claim = {
            "what": (
                "learned_sparse_extension h60/thr0.03: the ONLY family in this packet "
                "whose late block is positive at 100 and 150 bps"
            ),
            "measured": sparse_late,
            "why_it_is_not_a_candidate": [
                "the producer's own verdict is DISCOVERY-NOT-VALIDATED",
                "exploratory=true in the frozen contract; eligibility is a SECONDARY floor "
                "(30<=known_fills<100, traded_days>=30), not the primary power floor",
                "the 2025-02..2026-05 block was previously explored; NOT a pristine holdout",
                (
                    f"the rare h60 late-block quote audit covers only the quote-supported subset "
                    f"({(sparse_q or {}).get('n_covered')}/"
                    f"{(sparse_q or {}).get('n_trades')} pairs, "
                    f"whole_portfolio_certified=false) — a covered positive subset is not a "
                    f"whole-book bound and not an exchange fill"
                ),
            ],
            "cohort_note": (
                "this quote audit is the rare h60/thr0.03 cohort ONLY; it is a "
                "DIFFERENT, smaller cohort than the learned study's h15 audits and "
                "must NOT be merged with the learned h15 'SIP0' 76/208-positive or "
                "the late 229/447-negative readings"
            ),
            "status_verdict": (
                "POSITIVE-BUT-UNCERTAIN: the late block is positive at 100 and 150 bps "
                "on 143 known fills over 124 traded days, but it is an exploratory sub-floor "
                "probe (30<=known_fills<100), run on a previously-explored non-pristine block, "
                "whose as-of quote audit covers only 77/143 pairs with "
                "whole_portfolio_certified=false. It requires a pristine, side-aware re-test "
                "clearing the >=100-known-fill / >=50-traded-day power floor before any "
                "promotion. The producer's own decision field stays DISCOVERY-NOT-VALIDATED; "
                "this is the packet-level read on top of it, which neither denies the positive "
                "late branch nor promotes it."
            ),
            "annual_ev_source_facts": (sparse.get("annual_ev_source_facts") if sparse else None),
            "verdict": (sparse.get("headline") or {}).get("verdict"),
            "frozen_at": rare.get("frozen_at"),
        }
    all_negative_families = []
    for key, block in studies.items():
        h = block.get("headline") or {}
        if h.get("all_known_negative") is True:
            all_negative_families.append(key)
    return {
        "strongest_remaining_new_mechanism": {
            "claim": (
                "NO PROMOTABLE CANDIDATE. One exploratory probe is late-block POSITIVE and is "
                "the strongest remaining lead; see strongest_lead."
            ),
            "promotable_candidates": [],
            "strongest_lead": sparse_claim,
            "families_negative_at_every_measured_late_cost_rung": sorted(all_negative_families),
            "why_no_promotion": [
                "the one positive late-block result sits below the primary power floor, on a "
                "non-pristine previously-explored block, under a minute-open proxy, and its own "
                "producer labels it DISCOVERY-NOT-VALIDATED",
                "whole_portfolio_certified=false in every quote audit, and the micro_flow "
                "development coverage is disputed against micro_reclaim, so no whole-portfolio "
                "positivity can be claimed from any subset",
            ],
            "strongest_new_data_need": [
                "WHOLE-PORTFOLIO as-of side-aware quote + L1 displayed depth. Measured coverage: "
                + "; ".join(audit_lines)
                + ". Every 'positive covered subset' reading is unusable until the uncovered "
                "pairs are covered, because whole_portfolio_certified=false in all three audits.",
                "A single pre-registered, execution-realistic re-test of the sparse-extension "
                "cell (h60/thr0.03) on a genuinely pristine block with side-aware quotes, "
                "recording the power floor it must clear (>=100 known fills, >=50 traded days) "
                "before anything is promoted. It currently has 143 late known fills on 124 "
                "traded days at 100 bps, so the support exists; what is missing is a clean "
                "holdout and quote-side execution evidence.",
                "Resolution of the micro_flow vs micro_reclaim coverage disagreement over "
                "2021-02-01..2021-04-23 (CoreCorrection owns it), so that micro development "
                "bounds stop being un-trustworthy in either direction.",
                "Point-in-time locate availability/fees, SSR-aware legal short fills and "
                "margin/buy-in terms for the short lane (the producer already reports "
                "mean_affordable_locate_borrow_bps head-room per row).",
            ],
            "strongest_testable_hypothesis_from_observed_results": (
                "The open-anchored long edge is period-dependent rather than universally dead. "
                f"Two positive measured regions survive the sample-size power floor: (a) the "
                f"learned h{chosen.get('horizon')}/thr{chosen.get('threshold')} 2023-validation "
                f"cell at {val_row['mean_daily_lower_bound_book_pct'] if val_row else None}"
                f"%/book/day over {val_row['n_known_fills'] if val_row else None} known fills, and "
                f"(b) the 2023 quote-COVERED pairs of that same model at "
                f"{covered_0bps_net}"
                f"%/order at 0 bps residual. Their 2025-02..2026-05 counterparts are negative at "
                "every cost rung, including the quote-covered confirmation subset, so the "
                "2023-positive / 2025-26-negative contrast is confounded by quote coverage."
            ),
        },
        "explicitly_not_claimed": [
            "no profitable candidate is claimed from any quoted subset",
            "no general market impossibility is claimed from these bounded formulations",
            "no median/tail-deletion or bootstrap-significance gate was applied to any family",
            "no automatic 100 / 150 / 200 bps or CI gate rejection: 100bps means a 1% "
            "round-trip frictional charge, and 1000bps was never used anywhere in this packet",
            "the annualised CI band is historical mean uncertainty of the annualised figure, "
            "not a future prediction interval",
            "the quote-covered conditional annual figure is a hypothetical conditional on "
            "representativity, which is NOT confirmed; the remaining UNKNOWN pairs are excluded",
        ],
    }


def build_execution_limits() -> dict:
    return {
        "execution_proxy": {
            "minute_proxy_families": [
                "events",
                "learned",
                "sequence",
                "proven_push",
                "short_diagnostic",
                "overnight",
            ],
            "rule": "entry and exit are next-actual-minute-OPEN proxies; no high-touch profit "
            "credit; any promising candidate still needs as-of side-aware quotes and a "
            "size review",
        },
        "quote_touch_families": {
            "families": ["micro_flow", "micro_reclaim"],
            "rule": "actual ASK at signal+250ms and actual BID at the 60s target (or the first "
            "subsequent regular quote), capacity-checked at $250 per side",
            "limit": "quote-supported capacity is NOT a guaranteed exchange fill",
        },
        "cost_bases": {
            RT_ON_FILL: "round-trip bps charged on the fill notional of a minute-open proxy",
            RESIDUAL_ON_TOUCH: "residual round-trip bps charged on top of the actual ASK/BID "
            "touch; 0 bps is a diagnostic, not a free fill",
        },
        "unknown_convention": (
            "UNKNOWN exits are retained forever, charged the full position "
            "budget in the lower bound, and never zero-filled or dropped"
        ),
        "no_fill_convention": (
            "a no-fill (unfilled/expired/no bar at entry) intent keeps cash, "
            "is charged no fee and is NOT a loss and NOT a cash return"
        ),
        "not_pristine_holdout": (
            "the 2025-02..2026-05 block used by six families, and the "
            "2025-03..2025-08 months used by the micro families, were "
            "previously explored market periods, NOT pristine holdouts"
        ),
        "accounting": {
            "research_unit_usd": 1000.0,
            "sub_book_usd": 3000.0,
            "max_positions_per_day": 3,
            "micro_order_usd": 250.0,
            "micro_book_usd": 750.0,
            "note": (
                "margin-style funded cash reuse in the micro lanes is an assumption about "
                "an eligible margin account, not a claim that a $750 cash account "
                "day-trades; no financing is netted anywhere"
            ),
        },
    }


# --------------------------------------------------------------------------- #
# CLI table
# --------------------------------------------------------------------------- #
MICRO_ALTERNATIVE = {
    "micro_flow": "flow_imbalance_push_30s",
    "micro_reclaim": "aggressive_buyer_reversal_120s",
}
ROLE_TAG = {
    "train": "TRN",
    "validation": "VAL",
    "confirmation": "CONF",
    "development": "DEV",
    "late": "LATE",
}


def fmt(v, nd=2, dash="-"):
    if v is None:
        return dash
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def render_table(rows: list[dict], studies_order: list[str]) -> str:
    head = (
        f"{'STUDY':<18}{'ALTERNATIVE':<33}{'P':<5}{'COST':>6}  {'COST TYPE':<10}"
        f"{'n(fill+unk)':>13}{'NET/FILL %':>12}{'BOOK %/DAY':>12}"
    )
    lines = ["", "=" * len(head), head, "=" * len(head)]
    cost_tag = {RT_ON_FILL: "rt-fill", RESIDUAL_ON_TOUCH: "resid-touch"}
    for study in studies_order:
        srows = [r for r in rows if r["study"] == study]
        if not srows:
            continue
        lines.append(f"-- {study} " + "-" * max(0, len(head) - len(study) - 4))
        for r in srows:
            nfill = r["n_known_fills"] if r["n_known_fills"] is not None else 0
            nunk = r["n_unknown_fills"] if r["n_unknown_fills"] is not None else 0
            alt = (r["alternative"] or r["family"] or "")[:32]
            lines.append(
                f"{r['study']:<18}{alt:<33}"
                f"{ROLE_TAG.get(r['period_role'], r['period_role'] or '-'):<5}"
                f"{fmt(r['cost_bps'], 0):>6}  "
                f"{cost_tag.get(r['cost_basis'], '?'):<10}"
                f"{f'{nfill}+{nunk}':>13}"
                f"{fmt(r['mean_net_known_fill_per_order_pct']):>12}"
                f"{fmt(r['mean_daily_lower_bound_book_pct']):>12}"
            )
    lines.append("=" * len(head))
    lines.append(
        "units: NET/FILL % = per-ORDER net return after the row's cost basis (NOT a capital "
        "return);\n       BOOK %/DAY = whole-day lower-bound PnL / the row's book_usd.\n"
        "n(fill+unk) = known-exit fills + UNKNOWN fills; n_fills is the filled total.  "
        "UNKNOWN is never cash and never dropped.\n"
        "P: TRN=train VAL=validation CONF=late confirmation DEV=development.  "
        "cost type: rt-fill = round-trip bps on a minute-open proxy fill;\n"
        "       resid-touch = residual bps on top of the actual ASK/BID touch."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #
REGISTRY = [
    (
        "events",
        "causal open-event alternatives (9 families x horizons)",
        "factory/scripts/alpha_open_events.py",
        ["events/summary.json"],
        ["events/candidate.json", "events/quote_audit_confirmation/summary.json"],
        build_events,
    ),
    (
        "learned",
        "sparse LightGBM payoff model + quote audits",
        "factory/scripts/alpha_open_learned.py",
        [
            "learned/report.json",
            "learned/surface_validation.json",
            "learned/frozen_contract.json",
            "learned/quote_validation/summary.json",
            "learned/quote_confirmation/summary.json",
        ],
        ["learned/models/feature_order.json"],
        build_learned,
    ),
    (
        "sequence",
        "causal TCN encoder + ridge heads",
        "factory/scripts/alpha_sequence_payoff.py",
        [
            "sequence/late_summary.json",
            "sequence/validation_surface.json",
            "sequence/frozen_config.json",
            "sequence/provenance.json",
            "sequence/contract.json",
        ],
        [],
        build_sequence,
    ),
    (
        "proven_push",
        "proven open-anchored push with broad flow confirmation",
        "factory/scripts/alpha_proven_push.py",
        ["proven_push/results.json"],
        ["proven_push/contract.json"],
        build_proven_push,
    ),
    (
        "short_diagnostic",
        "short diagnostic on liquid extremes",
        "factory/scripts/alpha_short_diagnostic.py",
        ["short_diagnostic/results.json"],
        ["short_diagnostic/contract.json"],
        build_short,
    ),
    (
        "overnight",
        "causal long overnight participation (corrected producer)",
        "factory/scripts/alpha_open_overnight.py",
        ["overnight/summary.json"],
        ["overnight/contract.json"],
        build_overnight,
    ),
    (
        "learned_sparse_extension",
        "exploratory rare secondary probe of the learned sparse tail",
        "factory/scripts/alpha_sparse_model_extension.py",
        ["learned_sparse_extension/results.json", "learned_sparse_extension/contract.json"],
        ["learned_sparse_extension/quote_confirmation/summary.json"],
        build_sparse_extension,
    ),
    (
        "micro_flow",
        "raw SIP 30s classified buy-flow push",
        "factory/scripts/alpha_micro_flow.py",
        ["micro_flow/summary.json"],
        ["micro_flow/results.json", "micro_flow/contract.json"],
        build_micro_flow,
    ),
    (
        "micro_reclaim",
        "raw SIP aggressive-buyer reversal reclaim",
        "factory/scripts/alpha_micro_reclaim.py",
        ["micro_reclaim/summary.json"],
        ["micro_reclaim/contract.json"],
        build_micro_reclaim,
    ),
    (
        "bid_backed_burst",
        "raw SIP bid-backed 5s/15s burst impulse (new base family)",
        "factory/scripts/alpha_bid_backed_burst.py",
        ["bid_backed_burst/summary.json"],
        ["bid_backed_burst/contract.json"],
        build_bid_backed_burst,
    ),
]


def _tick_strategies_in_progress(root: Path) -> list[dict]:
    """The four NEW tick strategies still being built, as an explicit scope marker.

    None of them is a BASE-10 study: the registry does not require their artifacts, the
    producer never fails on them, and nothing here is a result.  Each entry records where
    the producer would write and whether that script is on disk / its output is staged --
    both measurements, neither a requirement.
    """
    return [
        {
            "study": "sparse_execution_frontier",
            "label": "sparse execution frontier (size / latency / quote-age / residual)",
            "producer_script": "factory/scripts/alpha_sparse_execution_frontier.py",
            "output_root": "sparse_execution_frontier",
            "status": "IN_PROGRESS",
            "registered_in_base_ten": False,
            "required_by_base_producer": False,
            "script_exists": (
                ROOT / "factory/scripts/alpha_sparse_execution_frontier.py"
            ).is_file(),
            "staged_outputs_present": (root / "sparse_execution_frontier").is_dir(),
            "note": (
                "new family; shares alpha_sparse_quote_service.load_day_quotes with the "
                "quote loader. No output is staged and none is required."
            ),
        },
        {
            "study": "sparse_exit_management",
            "label": "paired exit management (h60 control vs bid-triggered 10/15%)",
            "producer_script": "factory/scripts/alpha_sparse_exit_management.py",
            "output_root": "sparse_exit_management",
            "status": "IN_PROGRESS",
            "registered_in_base_ten": False,
            "required_by_base_producer": False,
            "script_exists": (ROOT / "factory/scripts/alpha_sparse_exit_management.py").is_file(),
            "staged_outputs_present": (root / "sparse_exit_management").is_dir(),
            "note": (
                "paired control on the same h60 rules and qualification weights; no "
                "level fill and no original weight change."
            ),
        },
        {
            "study": "sparse_daily",
            "label": "daily frequency of the sparse h60/h30/h15 signals",
            "producer_script": "factory/scripts/alpha_sparse_daily.py",
            "output_root": "sparse_daily",
            "status": "IN_PROGRESS",
            "registered_in_base_ten": False,
            "required_by_base_producer": False,
            "script_exists": (ROOT / "factory/scripts/alpha_sparse_daily.py").is_file(),
            "staged_outputs_present": (root / "sparse_daily").is_dir(),
            "note": (
                "reuses the ORIGINAL h60 model with no refit; 5 finite once / repeat / "
                "cooldown-15-max-3-attempts ticker variants."
            ),
        },
        {
            "study": "micro_payoff",
            "label": "supervised one-second quote / order-flow payoff learner",
            "producer_script": "factory/scripts/alpha_micro_payoff.py",
            "output_root": "micro_payoff",
            "status": "IN_PROGRESS",
            "registered_in_base_ten": False,
            "required_by_base_producer": False,
            "script_exists": (ROOT / "factory/scripts/alpha_micro_payoff.py").is_file(),
            "staged_outputs_present": (root / "micro_payoff").is_dir(),
            "note": (
                "LightGBM h5/h15-second heads with no ticker/date features, quarter "
                "capital, no concurrent ticker and no future-fill filter."
            ),
        },
    ]


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="read-only producer output root (default: %(default)s)",
    )
    ap.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help="packet path (default: %(default)s)"
    )
    ap.add_argument("--no-table", action="store_true", help="do not print the CLI table")
    args = ap.parse_args()

    root = args.root.expanduser().resolve()
    if not root.is_dir():
        print(f"FATAL: producer output root not found: {root}", file=sys.stderr)
        return 2
    contract_p = optional(root, "contract.json")
    if contract_p is None:
        print(
            f"FATAL: root contract.json missing in {root}; refusing to emit a partial packet",
            file=sys.stderr,
        )
        return 2

    try:
        events_summary_path = require(root, "events/summary.json")
        events_summary = load_json(events_summary_path)
    except MissingStudyError as exc:
        print(f"FATAL: missing required artifact {exc}", file=sys.stderr)
        return 3

    problems: list[str] = []
    studies: dict[str, dict] = {}
    for key, label, script, requires, extra_srcs, builder in REGISTRY:
        try:
            studies[key] = pack_study(key, label, script, root, requires, extra_srcs, builder)
        except MissingStudyError as exc:
            problems.append(f"{key}: missing required artifact {exc}")
        except Exception as exc:  # unreadable / schema drift
            problems.append(f"{key}: {type(exc).__name__}: {exc}")
    if problems:
        print(
            "FATAL: refusing to emit a partial packet. Registered studies incomplete:",
            file=sys.stderr,
        )
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 3

    # structural invariants: every row must carry the canonical field set and every
    # headline the canonical unit fields, so a producer schema drift cannot slip through.
    for key, block in studies.items():
        for i, r in enumerate(block.get("rows", [])):
            if set(r) != set(ROW_FIELDS):
                problems.append(
                    f"{key}: rows[{i}] field set drifted from the canonical row "
                    f"schema ({sorted(set(r) ^ set(ROW_FIELDS))})"
                )
        for name in ("headline",):
            h = block.get(name)
            missing = [k for k in HEADLINE_FIELDS if k not in h]
            if missing:
                problems.append(f"{key}: {name} missing canonical fields {missing}")
    if problems:
        print("FATAL: packet structure invariant violated:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 4

    # cross-checks that must hold for the packet to be trustworthy
    notes = []
    lv = optional(root, "learned/surface_validation.json")
    if lv is not None:
        raw = load_json(lv)
        surface_rows = [
            r for r in studies["learned"]["rows"] if r["row_key"].startswith("learned|validation|h")
        ]
        if len(raw) != len(surface_rows):
            notes.append("learned/surface_validation.json row count disagrees with report.json")
    for key, block in studies.items():
        reported = block.get("producer_sha256_reported")
        if (
            reported
            and block.get("producer_script_sha256")
            and reported != block["producer_script_sha256"]
        ):
            notes.append(
                f"{key}: producer script sha256 on disk != sha reported inside the "
                f"artifact ({reported})"
            )

    panel = build_panel(root, events_summary)

    packet = {
        "status": "COMPLETE",
        "status_scope": "BASE10_CHECKPOINT",
        "status_meaning": (
            "COMPLETE means the TEN registered BASE-10 studies are all present, normalised "
            "and cross-checked and this packet was written.  It is NOT the night goal being "
            "closed, NOT a claim that any strategy is profitable, and NOT a statement about "
            "the four new tick strategies, which are still being built."
        ),
        "asof": ASOF,
        "panel": panel,
        "studies": {k: studies[k] for k, *_ in REGISTRY},
        "tick_strategies_in_progress": _tick_strategies_in_progress(root),
        "count_semantics": {
            "n_fills": "filled intents, TOTAL = known-exit fills + UNKNOWN-exit fills",
            "n_known_fills": "the known-exit subset used for every *_known_* metric",
            "n_unknown_fills": "exits that never printed; charged -100% of the order budget "
            "in the lower bound, never dropped, never treated as cash",
            "n_no_fill_or_skips": "no-fill intents (unfilled / expired / no bar at entry / "
            "slot or cash refusals). Cash is retained, no fee is charged; "
            "these are NOT losses and NOT cash returns",
            "n_attempts": "signals/qualifications that reached the entry test",
            "note": "per-family producer differences are normalised so that n_fills is always "
            "the filled total and n_known_fills is always the known-exit subset",
        },
        "evidence_context": build_evidence_context(root, studies),
        "data_requirements": build_data_requirements(studies),
        "execution_limits": build_execution_limits(),
        "reproduction": {
            "generator": {
                "script": "factory/scripts/alpha_search_report.py",
                "command": (
                    "uv run --no-sync python factory/scripts/alpha_search_report.py "
                    "--root ~/alpha-data/open-search-v1 "
                    "--out factory/artifacts/alpha_search_20261008.json"
                ),
                "cwd": "repo root (the repo path contains a space; both --root and --out "
                "default are applied when omitted)",
                "defaults": {"--root": str(DEFAULT_ROOT), "--out": str(DEFAULT_OUT)},
                "script_sha256": sha256_file(Path(__file__).resolve()),
            },
            "producer_commands": {
                "events": (
                    "uv run --no-sync python factory/scripts/alpha_open_events.py "
                    "--panel-root ~/alpha-data/open-search-v1 "
                    "--out ~/alpha-data/open-search-v1/events"
                ),
                "learned": (
                    "uv run --no-sync python factory/scripts/alpha_open_learned.py "
                    "--out ~/alpha-data/open-search-v1/learned   "
                    "(default MODEL_DIR = <out>/models; replay subcommand: "
                    "--horizon {15,60,390} --threshold {0.01,0.03,0.05} "
                    "--period {train,validation,confirmation} --cost 100)"
                ),
                "sequence": (
                    "uv run --no-sync python factory/scripts/alpha_sequence_payoff.py run "
                    "--npz data/atlas/sequence/v1/runs/causal/block/embeddings.npz "
                    "--out ~/alpha-data/open-search-v1/sequence"
                ),
                "proven_push": (
                    "uv run --no-sync python factory/scripts/alpha_proven_push.py "
                    "--panel ~/alpha-data/open-search-v1 "
                    "--out ~/alpha-data/open-search-v1/proven_push"
                ),
                "short_diagnostic": (
                    "uv run --no-sync python factory/scripts/alpha_short_diagnostic.py "
                    "--panel ~/alpha-data/open-search-v1"
                ),
                "overnight": (
                    "uv run --no-sync python factory/scripts/alpha_open_overnight.py "
                    "--out ~/alpha-data/open-search-v1/overnight   "
                    "(--days SUBSET for a smoke run; full adjacency rules still apply)"
                ),
                "learned_sparse_extension": (
                    "uv run --no-sync python "
                    "factory/scripts/alpha_sparse_model_extension.py "
                    "--panel ~/alpha-data/open-search-v1 "
                    "--out ~/alpha-data/open-search-v1/learned_sparse_extension "
                    "(--out defaults to <panel>/learned_sparse_extension)"
                ),
                "learned_sparse_extension_quote_audit": (
                    "uv run --no-sync python factory/scripts/alpha_quote_audit.py "
                    "--trades ~/alpha-data/open-search-v1/learned_sparse_extension/"
                    "trades_confirmation_100.parquet "
                    "--out ~/alpha-data/open-search-v1/learned_sparse_extension/quote_confirmation "
                    "--latency-ms 250 --max-age-s 2.0 --order-budget 1000 "
                    "(required: the packet refuses to emit if these confirmation trades exist "
                    "without their side-aware quote audit)"
                ),
                "micro_flow": (
                    "uv run --no-sync python factory/scripts/alpha_micro_flow.py "
                    "--stage all --out ~/alpha-data/open-search-v1/micro_flow"
                ),
                "micro_reclaim": (
                    "uv run --no-sync python factory/scripts/alpha_micro_reclaim.py "
                    "report --out ~/alpha-data/open-search-v1/micro_reclaim"
                ),
                "bid_backed_burst": (
                    "uv run --no-sync python "
                    "factory/scripts/alpha_bid_backed_burst.py "
                    "--command report "
                    "--out ~/alpha-data/open-search-v1/bid_backed_burst "
                    "(--command run for the per-day build; --days SUBSET smoke)"
                ),
                "quote_audit_legacy": (
                    "uv run --no-sync python factory/scripts/alpha_quote_audit.py "
                    "<trades>   (producer of the covered-vs-unknown audits under "
                    "learned/quote_validation, learned/quote_confirmation, "
                    "events/quote_audit_confirmation and "
                    "learned_sparse_extension/quote_confirmation)"
                ),
            },
            "saved_model_paths": {
                "learned": {
                    "payoff_h15": str(root / "learned/models/payoff_h15.joblib"),
                    "payoff_h60": str(root / "learned/models/payoff_h60.joblib"),
                    "payoff_h390": str(root / "learned/models/payoff_h390.joblib"),
                    "feature_order": str(root / "learned/models/feature_order.json"),
                    "load_api": (
                        "alpha_open_learned.load_models(model_dir) -> {h: model}; "
                        "score_frame(frame, models); feature_matrix(frame); "
                        "make_signals(cand, horizon, threshold, days)"
                    ),
                },
                "sequence": {
                    f"head_scale{s}_h{h}": str(root / f"sequence/models/head_scale{s}_h{h}.joblib")
                    for s in (30, 60, 120)
                    for h in (15, 60)
                },
            },
            "producer_scripts": {r[0]: r[2] for r in REGISTRY},
        },
        "provenance": {
            "root": str(root),
            "root_contract_sha256": sha256_file(contract_p),
            "panel_contract_sha256": panel["panel_contract_sha256"],
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "generator_script_sha256": sha256_file(Path(__file__).resolve()),
            "sources": {
                k: {
                    "path": block["source"],
                    "sha256": block["source_sha256"],
                    "producer_script": block["producer_script"],
                    "producer_script_sha256": block["producer_script_sha256"],
                    "producer_sha256_reported_in_artifact": block.get("producer_sha256_reported"),
                    "extra_sources": block["extra_sources"],
                }
                for k, block in studies.items()
            },
            "cross_check_notes": notes,
            "studies_stale_relative_to_current_producer": sorted(
                k for k, b in studies.items() if b.get("stale_relative_to_current_producer")
            ),
            "staleness_note": (
                "a study flagged here has artifacts written by an OLDER producer "
                "than the script currently in the repo (sha recorded inside the "
                "artifact and/or file mtimes disagree). Its numbers are still "
                "reported verbatim from the staged artifacts; the flag exists so "
                "a consumer never mistakes them for the output of the current "
                "producer. It is a measurement, not a verdict on the results."
            ),
            "read_only": True,
        },
        "method_notes": [
            "INITIAL POWER FLOOR (honest): families that select on a 2023 validation surface "
            "require >=100 known fills AND >=50 traded days (learned: 4/9 alternatives clear it; "
            "sequence: 9/54 cells are feasible).  This is a SAMPLE-SIZE floor so a 2-fill cell "
            "can never be promoted; it is NOT a median, tail-deletion or bootstrap-significance "
            "gate, and no such veto was applied anywhere in this packet.",
            "EXPLORATORY RARE EXTENSIONS (honest): the thr=0.03 / thr=0.05 learned cells, the "
            "rare sequence cells (1-19 known fills) and the whole learned_sparse_extension "
            "study are exploratory only. The learned cells stay in rows[] but cannot clear the "
            "power floor; the sparse extension clears only a SECONDARY exploratory floor "
            "(30<=known_fills<100, traded_days>=30) and its producer labels it "
            "DISCOVERY-NOT-VALIDATED. None of them is a promotion.",
            "SECONDARY EXTENSION (honest): the learned LightGBM model, the causal-TCN heads and "
            "the sparse-model extension are SECONDARY discovery probes. A positive validation "
            "or late cell is a reason to run a clean, execution-realistic re-test, never an "
            "automatic closure of the mechanism question.",
            "TEN REGISTERED STUDIES (8 base families + the rare sparse extension + the new "
            "bid_backed_burst base family). All are REQUIRED; the producer exits without writing "
            "anything if any one is missing.",
            "BASE-10 CHECKPOINT SCOPE (honest): every headline, every metric row and every "
            "status field above refers to those ten registered studies ONLY.  The four new "
            "tick strategies (sparse_execution_frontier, sparse_exit_management, sparse_daily, "
            "micro_payoff) are listed under tick_strategies_in_progress with status "
            "IN_PROGRESS; their artifacts are not required here, their absence never blocks "
            "this packet, and none of their numbers appears anywhere above.  packet.status = "
            "COMPLETE therefore means 'BASE-10 CHECKPOINT written', NOT 'the night goal is "
            "closed' and NOT 'a profitable strategy was found'.",
            "BID_BACKED_BURST HORIZON (honest): its headline is the confirmation cell at the "
            "100 bps residual rung ON THE PRODUCER'S CHOSEN horizon (read from the producer's "
            "own frozen selection field, never picked here).  results[] is emitted h5 before "
            "h15, so a cost-only match would have silently reported the non-selected h5 row; "
            "every metric row_key now carries the horizon, and if the producer declared no "
            "selection the headline is reported as absent rather than defaulted to the first "
            "row.  The strategy, the folds, the costs and the underlying numbers are "
            "unchanged; only the selection of which cell is the headline, and its labelling, "
            "changed.",
            "ANNUAL EV (honest): the only late-positive family (learned_sparse_extension "
            "h60/thr0.03) carries a compact annual_ev_source_facts block whose every dollar and "
            "percent figure is derived on the fly from its own staged artifacts "
            "(143 fills / 332 days x 252 = 108.542 fills/yr; x $1,000 x 0.986894% net per fill = "
            "$1,071.196/yr before opex/tax = 35.7065% of the $3,000 research book, SIMPLE and not "
            "CAGR). The quote-covered conditional ($2,510.991/yr if every fill looked like the "
            "77/143 covered pairs) is labelled NOT CONFIRMED on representativity, and the quoted "
            "subset's own known annual contribution is $1,352.072 with 66 pairs still UNKNOWN. "
            "100bps means a 1% round-trip charge; 1000bps was never used. No automatic "
            "100/150/200bps or CI gate rejection is applied anywhere.",
            "The chosen late/confirmation cost rungs are reported for EVERY family (100/150/200 "
            "bps for minute-proxy families, 0/25/100/150 residual bps for the micro families).",
        ],
    }

    all_rows = [r for block in studies.values() for r in block.get("rows", [])]
    order = [k for k, *_ in REGISTRY]
    if not args.no_table:
        print(render_table(all_rows, order))
        print()
        for k in order:
            h = studies[k]["headline"]
            print(
                f"{k:<16} {str(h.get('verdict'))[:64]:<66} "
                f"profit_status={h['profit_status']['status']}"
            )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as fh:
        json.dump(packet, fh, indent=1, sort_keys=False)
        fh.write("\n")
    print(f"\nwrote {args.out} ({args.out.stat().st_size} bytes, sha256 {sha256_file(args.out)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
