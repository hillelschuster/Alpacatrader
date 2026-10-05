#!/usr/bin/env python3
"""OWNED-CLAIM CASH-FIRST BOOK STABILITY -- the SHAPE of every TEST book over time.

This producer is a READ-ONLY reader.  It opens exactly two things: the executed
discovery artifact (``owned_claim_cash_first_four_costs.json``) and the replay parquet
outputs it wrote (``<v2>/owned_claim/replay_cash_first_four_costs``). It replays
nothing, re-prices nothing, re-hurdles nothing, re-fits nothing and reads no price,
volume, roster or score value.  The score/panel/roster bytes are only re-hashed, to
prove the published corpus on disk is still the published corpus.

Units: every return, mass, mean and bound here is a NORMALIZED RATE on ONE book's own
original portfolio capital (the replay ledger starts each book at cash = 1.0 and a book
date's return is its settled cash minus that 1.0).  It is already a portfolio rate for that
one book; it is NOT the upstream forecast's unit (incremental inherited dollars per original
claim cash dollar), which is kept verbatim and separate under
``historical_lineage.upstream_forecast_and_fee_contracts`` and is never mixed in.

What it reports, for ALL active cells (9 views x 2 cycles x 4 clocks x 2 N x 4 costs
= 576), never for a hand-picked cell:

  * the exact book-date shape: book dates, KNOWN dates, the EXACT dates whose book is
    UNKNOWN, known-date sum / mean / median / day SE, positive / negative / zero counts;
  * per-fold and per-month returns, each with its own KNOWN and unknown denominators and
    the positive-fold / positive-month counts;
  * concentration in DATES and in calendar blocks (never in claim denominators): the
    single largest and smallest book date, the strongest and weakest fold and month, each
    with its share of that cell's positive mass and of its known net;
  * best-1 / best-3 / best-5 / best-10 DATE removal: that cell's own most favourable book
    dates removed, the mean over the REMAINING book dates, and the exact removed dates;
  * adjacent-cost matched-date differences and the repeat-minus-once
    matched-date difference, both only on dates where BOTH books are KNOWN, each with the
    exact excluded dates;
  * an UNKNOWN break-even BOUND, never an imputed mean: the cell's known net divided by its
    own count of UNKNOWN book dates is the SIGNED per-date amount those dates would have to
    move against the known net to erase it (a negative known net makes it a required gain,
    not a physically negative loss).  It is a hypothetical, not a forecast, and UNKNOWN is
    never filled in with a zero;
  * a deterministic MONTH-BLOCK resampling of the known-date mean (optional, ``--boot``)
    that moves whole calendar months and keeps UNKNOWN out of numerator and denominator;
  * all-cell breadth, so the spread is visible instead of a winner.

The cash0 control (``cash0:cash``, 32 cells) is carried in its OWN section and never
mixed into the active population, the breadth counts or any contrast.

Boundaries, repeated in the artifact itself:

  * Every active cell is an ALTERNATIVE book over the same roster, the same frozen
    forecasts and the same dates.  A cell's own figures are one book's normalized portfolio
    rate; what is forbidden is SUMMING or averaging ACROSS cells, treating them as
    independent samples, or reading a breadth average as one portfolio's dollars.
  * 576 cells were scored on one TEST corpus. The largest cell mean is an order statistic
    over correlated alternatives.  No cell is selected, promoted, named best or validated
    here; no significance test is run and no p-value is published.
  * There is deliberately NO universal stability threshold in this artifact: no pass,
    fail, promote or reject rule is defined or implied by any count it prints.
  * UNKNOWN books stay UNKNOWN: excluded from every mean with an explicit count and an
    exact date list, never imputed, never silently treated as a known zero.

Denominators are printed beside every statistic; see the ``denominators`` section.

CLI:
  python factory/scripts/owned_claim_cash_stability.py \
      --data-root /home/hillel/projects/Alpacatrader/data \
      --out factory/artifacts/owned_claim_cash_stability_four_costs.json
  [--replay-dir DIR]      # default <v2>/owned_claim/replay_cash_first_four_costs
  [--artifacts-root DIR]  # default <repo>/factory/artifacts
  [--discovery NAME]      # default owned_claim_cash_first_four_costs.json
  [--boot N]              # month-block draws per cell; 0 disables (default 200)
  [--seed N]
  [--no-input-rehash]     # skip re-hashing the declared score/panel/roster bytes
  [--limit N]             # bounded NON-EVIDENCE run over the first N published dates
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

for _v in (
    "POLARS_MAX_THREADS",
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ.setdefault(_v, "2")

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lifecycle_study as ls  # noqa: E402  (canonical discovery-day guard only)

ROOT = Path(__file__).resolve().parents[2]
KIND = "OWNED-CLAIM-CASH-FIRST-BOOK-STABILITY-DIAGNOSTIC-NOT-A-SELECTED-EDGE"
ARTIFACT_NAME = "owned_claim_cash_first_four_costs.json"
REPLAY_RELPATH = ("owned_claim", "replay_cash_first_four_costs")
CELL_KEYS = ("clock", "n", "side", "policy", "cycles")
CASH0_POLICY = "cash0:cash"
REMOVALS = (1, 3, 5, 10)
BOOT_DEFAULT = 200
SEED_DEFAULT = 17
HASH_WORKERS = 8  # the declared input bytes are ~1.5GB; hashing is IO-bound, not CPU-bound
# Two aggregations of the same floats in a different order differ by a few ulp; a
# denominator is called recovered on this absolute bound, never on bitwise equality.
DENOM_TOL = 1e-12
REPLAY_FRAMES = ("daily", "summary", "folds", "months", "paired", "timing", "per_date")
UNKNOWN_NOTE = (
    "an UNKNOWN book is an unresolved cashflow, not a zero: it leaves both the numerator "
    "and the denominator of every mean here and is never imputed"
)


# ------------------------------------------------------------------------------ helpers
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_matches(path: Path, declared: str | None) -> bool:
    """True only when the file exists AND its bytes hash to the declared digest."""
    return bool(path.is_file() and declared and sha256_file(path) == declared)


def f(x):
    """JSON-safe finite float.  NaN/inf are UNKNOWN (null), never a silent zero."""
    if x is None:
        return None
    x = float(x)
    if math.isnan(x) or math.isinf(x):
        return None
    return 0.0 if x == 0.0 else x  # -0.0 and 0.0 are the same zero; print the plain one


def mean(v: np.ndarray):
    return f(v.mean()) if v.size else None


def se(v: np.ndarray):
    """Day-level standard error of the mean (sample sd / sqrt(n)); None below n = 2."""
    if v.size < 2:
        return None
    return f(float(v.std(ddof=1)) / math.sqrt(int(v.size)))


def share(num, den):
    if num is None or den is None or den == 0:
        return None
    return f(num / den)


def bps_of(side: float) -> int:
    """Modeled ROUND-TRIP bps for one per-side cost: 0.005 -> 100bps, 0.0075 -> 150bps."""
    return int(round(float(side) * 2.0 * 10000.0))


def cell_id(key: dict) -> str:
    return (
        f"clock{key['clock']}|n{key['n']}|{bps_of(key['side'])}bps|{key['policy']}|{key['cycles']}"
    )


def block_rows(group_np: np.ndarray, ret: np.ndarray, known: np.ndarray, label: str) -> list[dict]:
    """Fold/month breakdown, each block with its OWN known/unknown denominators."""
    out = []
    for name in sorted(set(group_np.tolist())):
        in_block = group_np == name
        kret = ret[known & in_block]
        book_dates = int(in_block.sum())
        m = mean(kret)
        out.append(
            {
                label: name,
                "book_dates": book_dates,
                "known_book_dates": int(kret.size),
                "unknown_book_dates": book_dates - int(kret.size),
                "known_sum": f(kret.sum()) if kret.size else None,
                "known_book_date_mean": m,
                "known_book_date_se": se(kret),
                "positive": bool(m is not None and m > 0.0),
                "non_negative": bool(m is not None and m >= 0.0),
            }
        )
    return out


def month_block_bootstrap(
    month_np: np.ndarray,
    ret: np.ndarray,
    known: np.ndarray,
    boot: int,
    seed: int,
    cid: str,
) -> dict:
    """Deterministic resampling of WHOLE calendar months.

    A block is one calendar month and carries only its KNOWN book dates, so UNKNOWN dates
    are absent from both the numerator and the denominator and are never imputed.  A month
    with no KNOWN date contributes nothing and is counted.  This describes dispersion on
    the observed calendar; it is NOT a significance test and NOT a p-value.
    """
    months = sorted(set(month_np.tolist()))
    sums = np.zeros(len(months))
    counts = np.zeros(len(months))
    for i, m in enumerate(months):
        sel = known & (month_np == m)
        counts[i] = float(sel.sum())
        if counts[i]:
            sums[i] = float(ret[sel].sum())
    out = {
        "method": "draw whole calendar months with replacement; statistic = resampled KNOWN "
        "mass / resampled KNOWN date count",
        "months_in_cell": len(months),
        "months_with_at_least_one_known_date": int((counts > 0).sum()),
        "months_with_no_known_date": int((counts == 0).sum()),
        "draws_requested": boot,
        "seed": seed,
        "per_cell_seed_rule": "int.from_bytes(sha256(f'{seed}|{cell_id}').digest()[:8], 'big')",
        "unknown_handling": UNKNOWN_NOTE,
        "is_not": "a significance test, a p-value, a confidence claim or a selection rule",
        "ran": False,
    }
    if not boot:
        out["disabled_by"] = "--boot 0"
        return out
    cseed = int.from_bytes(hashlib.sha256(f"{seed}|{cid}".encode()).digest()[:8], "big")
    rng = np.random.default_rng(cseed)
    idx = rng.integers(0, len(months), size=(boot, len(months)))
    tot_n = counts[idx].sum(axis=1)
    good = tot_n > 0
    out["ran"] = True
    out["cell_seed"] = cseed
    out["draws_with_zero_known_dates_dropped"] = int((~good).sum())
    if not good.any():
        out["known_date_mean_resampled"] = None
        return out
    stat = sums[idx].sum(axis=1)[good] / tot_n[good]
    out["known_date_mean_resampled"] = {
        "p05": f(np.percentile(stat, 5)),
        "p25": f(np.percentile(stat, 25)),
        "median": f(np.percentile(stat, 50)),
        "p75": f(np.percentile(stat, 75)),
        "p95": f(np.percentile(stat, 95)),
        "min": f(stat.min()),
        "max": f(stat.max()),
        "mean": f(stat.mean()),
        "share_of_draws_at_or_below_zero": share(float((stat <= 0.0).sum()), float(stat.size)),
        "denominator": "known book dates in the drawn months (UNKNOWN excluded)",
    }
    return out


def analyse_cell(key: dict, sub: pl.DataFrame, boot: int, seed: int) -> tuple[dict, dict]:
    """Return (record, day->known return) for ONE cell, sorted by date."""
    sub = sub.sort("day")
    day_np = sub["day"].to_numpy()
    ret = sub["ret"].to_numpy()
    month_np = sub["month"].to_numpy()
    fold_np = sub["fold"].to_numpy()
    known = ~np.isnan(ret)

    kret = ret[known]
    kday = day_np[known]
    n_known = int(kret.size)
    n_book = int(day_np.size)
    known_sum = f(kret.sum()) if n_known else None
    known_mean = mean(kret)
    unknown_days = [str(d) for d in day_np[~known]]

    pos = kret[kret > 0.0]
    neg = kret[kret < 0.0]
    positive_mass = f(pos.sum())
    cid = cell_id(key)

    def mass_share(mass):
        return share(mass, positive_mass) if positive_mass not in (None, 0.0) else None

    order = np.argsort(-kret, kind="stable")  # descending return, ties by date order
    max_date = {"day": str(kday[order[0]]), "ret": f(kret[order[0]])} if n_known else None
    if max_date is not None:
        max_date["share_of_positive_mass"] = mass_share(kret[order[0]])
        max_date["share_of_known_net"] = share(kret[order[0]], known_sum)
    min_date = {"day": str(kday[order[-1]]), "ret": f(kret[order[-1]])} if n_known else None
    if min_date is not None:
        min_date["share_of_positive_mass"] = mass_share(kret[order[-1]])
        min_date["share_of_known_net"] = share(kret[order[-1]], known_sum)

    fold_rows = block_rows(fold_np, ret, known, "fold")
    month_rows = block_rows(month_np, ret, known, "month")

    def extreme(rows, label, top: bool):
        """Strongest/weakest block BY MONEY MASS.

        The comparison key is the mass, never the label: ordering on the raw tuple would
        rank folds/months lexicographically and report the last label regardless of P&L.
        Exact money ties are broken by the smallest label so the field is deterministic.
        """
        masses = [(r[label], r["known_sum"]) for r in rows if r["known_sum"] is not None]
        if not masses:
            return None
        name, mass = min(masses, key=lambda kv: (-kv[1], kv[0]) if top else (kv[1], kv[0]))
        return {
            label: name,
            "mass": mass,
            "selected_by": "highest known mass" if top else "lowest known mass",
            "tie_break": "smallest label among equal masses",
            "share_of_positive_mass": mass_share(mass),
            "share_of_known_net": share(mass, known_sum),
        }

    concentration = {
        "positive_mass": positive_mass,
        "negative_mass": f(neg.sum()),
        "net_mass": known_sum,
        "positive_book_dates": int(pos.size),
        "negative_book_dates": int(neg.size),
        "zero_book_dates": n_known - int(pos.size) - int(neg.size),
        "max_book_date": max_date,
        "min_book_date": min_date,
        "top_fold_by_mass": extreme(fold_rows, "fold", True),
        "bottom_fold_by_mass": extreme(fold_rows, "fold", False),
        "top_month_by_mass": extreme(month_rows, "month", True),
        "bottom_month_by_mass": extreme(month_rows, "month", False),
        "denominator_note": "shares are of BOOK-DATE mass (dates or calendar blocks), never "
        "of a claim, member or order count",
    }

    removal = {}
    for k in REMOVALS:
        eff = min(k, n_known)
        removed = order[:eff]
        kept = kret[order[eff:]]
        kept_mean = mean(kept)
        removal[f"best_{k}"] = {
            "removed_requested": k,
            "removed_effective": eff,
            "removed_dates": [{"day": str(kday[i]), "ret": f(kret[i])} for i in removed],
            "removed_sum": f(kret[removed].sum()) if eff else None,
            "retained_known_book_dates": int(kept.size),
            "retained_known_sum": f(kept.sum()) if kept.size else None,
            "retained_book_date_mean": kept_mean,
            "retained_book_date_se": se(kept),
            "retained_mean_minus_full_known_mean": (
                f(kept_mean - known_mean)
                if kept_mean is not None and known_mean is not None
                else None
            ),
            "retained_mean_positive": bool(kept_mean > 0.0) if kept.size else None,
        }

    unknown_be = {
        "unknown_book_dates": len(unknown_days),
        "unknown_date_list": unknown_days,
        "unknown_books_from_unresolved_fresh_buy": int(
            sub.filter(pl.col("unknown") & pl.col("unresolved_fresh")).height
        ),
        "unknown_books_with_affordability_gap": sub.filter(
            pl.col("unknown") & (pl.col("affordability_unknown") > 0)
        ).height,
        "affordability_affected_claims": int(sub["affordability_unknown"].sum()),
        "known_normalized_profit_sum": known_sum,
        "denominator": "this cell's OWN count of UNKNOWN book dates -- not claims, not "
        "orders, not dates shared with other cells, not a portfolio",
        "break_even_loss_per_unknown_date": (
            share(known_sum, float(len(unknown_days))) if unknown_days else None
        ),
        "bound_sign": (
            None
            if not unknown_days
            else (
                "positive_known_net_so_a_loss"
                if (known_sum or 0.0) > 0.0
                else (
                    "negative_known_net_so_a_gain"
                    if (known_sum or 0.0) < 0.0
                    else "zero_known_net_so_no_direction"
                )
            )
        ),
        "meaning": "the SIGNED amount, as a fraction of the book's own original portfolio "
        "capital, that every UNKNOWN book date would have to move AGAINST the known sum to "
        "erase it.  When the known sum is positive this is a loss the UNKNOWN dates would "
        "have to produce; when the known sum is negative the bound is negative and it means "
        "the UNKNOWN dates would instead have to GAIN that much for the book to break even "
        "-- it is a deficit to be covered, not a physically negative loss.  A zero known sum "
        "gives a zero bound with no direction.  Either way it is arithmetic on what is still "
        "unresolved, never a forecast for those dates and never a return: UNKNOWN stays out of "
        "every mean and is never imputed.",
        "undefined_because": None if unknown_days else "the cell has no UNKNOWN book date",
    }

    record = {
        "cell_id": cid,
        **key,
        "round_trip_bps": bps_of(key["side"]),
        "book_dates": n_book,
        "known_book_dates": n_known,
        "unknown_book_dates": n_book - n_known,
        "unknown_date_list": unknown_days,
        "known_sum": known_sum,
        "known_book_date_mean": known_mean,
        "known_book_date_se": se(kret),
        "known_book_date_median": f(float(np.median(kret))) if n_known else None,
        "orders_sum_known_book_dates": int(sub.filter(~pl.col("unknown"))["orders"].sum()),
        "fresh_entries_sum_known_book_dates": int(
            sub.filter(~pl.col("unknown"))["fresh_entries"].sum()
        ),
        "fees_sum_known_book_dates": f(sub.filter(~pl.col("unknown"))["fees"].sum())
        if n_known
        else None,
        "gross_pnl_sum_known_book_dates": f(sub.filter(~pl.col("unknown"))["gross_pnl"].sum())
        if n_known
        else None,
        "concentration": concentration,
        "folds": fold_rows,
        "months": month_rows,
        "positive_folds": sum(1 for r in fold_rows if r["positive"]),
        "folds_denominator": len(fold_rows),
        "positive_months": sum(1 for r in month_rows if r["positive"]),
        "months_denominator": len(month_rows),
        "best_date_removal": removal,
        "unknown_break_even": unknown_be,
        "month_block_resampling": month_block_bootstrap(month_np, ret, known, boot, seed, cid),
    }
    day_map = {str(d): float(v) for d, v in zip(day_np[known], kret, strict=True)}
    return record, day_map


def paired(a_ret: dict, b_ret: dict, days: list[str], a_name: str, b_name: str) -> dict:
    """Exact same-date difference between two books, on jointly KNOWN dates only."""
    matched, excluded = [], []
    only_a = only_b = 0
    for d in days:
        av, bv = a_ret.get(d), b_ret.get(d)
        ak, bk = av is not None, bv is not None
        if ak and bk:
            matched.append(d)
        else:
            excluded.append(d)
            only_a += int(ak)
            only_b += int(bk)
    diff = np.array([a_ret[d] - b_ret[d] for d in matched], dtype=float)
    return {
        "book_a": a_name,
        "book_b": b_name,
        "direction": f"{a_name} minus {b_name}",
        "book_dates_in_scope": len(days),
        "matched_known_dates": len(matched),
        "excluded_dates": len(excluded),
        "excluded_date_list": excluded,
        "excluded_known_in_book_a_only": only_a,
        "excluded_known_in_book_b_only": only_b,
        "excluded_because": "at least one of the two books is UNKNOWN on that date; "
        "UNKNOWN never enters a difference as a zero",
        "mean_delta_per_matched_known_date": mean(diff),
        "delta_se": se(diff),
        "sum_delta": f(diff.sum()) if diff.size else None,
        "matched_dates_delta_positive": int((diff > 0).sum()) if diff.size else 0,
        "matched_dates_delta_zero": int((diff == 0).sum()) if diff.size else 0,
        "matched_dates_delta_negative": int((diff < 0).sum()) if diff.size else 0,
        "share_of_matched_dates_delta_positive": share(
            float((diff > 0).sum()) if diff.size else None, float(diff.size)
        ),
    }


# ---------------------------------------------------------------------------- lineage
def declared_pins(art: dict, data_root: Path, scores_root: Path, days: list[str], rehash: bool):
    """Check the artifact's declared producer/engine/score/panel/roster pins."""
    v2 = ls.v2_dir(data_root)
    here = Path(__file__).resolve().parent
    rec: dict = {"rehashed": bool(rehash)}

    def check(path: Path, declared, label: str):
        entry: dict = {"path": str(path), "declared_sha256": declared}
        if not path.is_file():
            entry.update({"present": False, "on_disk_sha256": None, "match": None})
        elif rehash:
            got = sha256_file(path)
            entry.update({"present": True, "on_disk_sha256": got, "match": bool(got == declared)})
        else:
            entry.update(
                {
                    "present": True,
                    "on_disk_sha256": None,
                    "match": None,
                    "not_checked": "--no-input-rehash",
                }
            )
        rec[label] = entry

    check(here / "owned_claim_cash_first_replay.py", art.get("source_sha256"), "producer_script")
    check(here / "owned_claim_replay.py", art.get("engine_sha256"), "accounting_engine")
    check(Path(art["scores_metadata"]), art.get("scores_metadata_sha256"), "score_metadata")
    tp = art.get("transform_provenance_sha256") or {}
    check(v2 / "split.json", tp.get("split.json"), "transform_split_json")
    check(v2 / "manifest.json", tp.get("manifest.json"), "transform_manifest_json")

    score_bad, exec_bad = [], []
    if rehash:
        # ~1.5GB of declared bytes; hash them concurrently, in a deterministic order.
        sday = art.get("score_day_sha256") or {}
        ep = art.get("execution_input_sha256") or {}
        jobs: list[tuple[str, str, Path, str | None]] = [
            (
                f"{d}|n{n}",
                "score",
                scores_root / f"scores_n{n}" / f"{d}.parquet",
                (sday.get(d) or {}).get(f"n{n}"),
            )
            for d in days
            for n in (3, 5)
        ] + [
            (f"{d}|{kind}", "execution", p, (ep.get(d) or {}).get(kind))
            for d in days
            for kind, p in (
                ("panel", v2 / "panel" / f"{d}.parquet"),
                ("roster", v2 / "roster" / f"{d}.parquet"),
                ("done_marker", v2 / "_done" / f"{d}.json"),
            )
        ]
        with ThreadPoolExecutor(max_workers=HASH_WORKERS) as pool:
            results = list(pool.map(lambda j: file_matches(j[2], j[3]), jobs))
        for (label, kind, _path, _declared), ok in zip(jobs, results, strict=True):
            if not ok:
                (score_bad if kind == "score" else exec_bad).append(label)
    rec["score_partitions"] = {
        "note": "the frozen OOF forecast partitions actually consumed by the replay",
        "declared_days": len(art.get("score_day_sha256") or {}),
        "files_in_scope": 2 * len(days),
        "rehashed": bool(rehash),
        "mismatch_count": len(score_bad),
        "mismatches": score_bad[:50],
    }
    rec["execution_inputs"] = {
        "note": "the per-day panel bytes, roster bytes and v2 _done marker the replay "
        "consumed; these decide the actual opens, volumes, causal marks and shadow prices",
        "kinds": ["panel", "roster", "done_marker"],
        "files_in_scope": 3 * len(days),
        "rehashed": bool(rehash),
        "mismatch_count": len(exec_bad),
        "mismatches": exec_bad[:50],
    }
    return rec


def pin_failures(pins: dict) -> list[str]:
    bad = [k for k, v in pins.items() if isinstance(v, dict) and v.get("match") is False]
    for k in ("score_partitions", "execution_inputs"):
        if pins[k]["mismatch_count"]:
            bad.append(k)
    return bad


def historical_lineage(art: dict, meta: dict, data_root: Path) -> dict:
    """What the executed run declared about itself, reported apart from verification."""
    days = list(art.get("test_days") or [])
    return {
        "what_this_is": "the executed run's own declarations.  This reader's on-disk "
        "verification lives under lineage.pins and is never merged into these numbers.",
        "artifact_kind": art.get("kind"),
        "artifact_evidence": art.get("evidence"),
        "artifact_non_evidence_notes": art.get("non_evidence_notes"),
        "engine_declared": art.get("engine"),
        "producer_source_sha256_declared": art.get("source_sha256"),
        "engine_sha256_declared": art.get("engine_sha256"),
        "scores_run_id_declared": art.get("scores_run_id"),
        "scores_run_id_in_metadata": meta.get("run_id"),
        "scores_root": art.get("scores_root"),
        "scores_script_sha256_in_metadata": meta.get("script_sha256"),
        "scores_metadata_sha256_declared": art.get("scores_metadata_sha256"),
        "transform_provenance_sha256_declared": art.get("transform_provenance_sha256"),
        "test_dates": len(days),
        "test_first_date": days[0] if days else None,
        "test_last_date": days[-1] if days else None,
        "fold_boundaries_from_score_metadata": [
            {
                "fold": w.get("fold"),
                "test_dates": len(w.get("test_days") or []),
                "first": (w.get("test_days") or [None])[0],
                "last": (w.get("test_days") or [None])[-1],
            }
            for w in meta.get("folds") or []
        ],
        "declared_grid": {
            "policies": art.get("policies"),
            "cash0_reference": art.get("cash0_reference"),
            "cycles_run": art.get("cycles_run"),
            "clocks": art.get("clocks"),
            "ns": art.get("ns"),
            "sides": art.get("sides"),
        },
        "cash0_identity_declared": art.get("cash0_identity"),
        "test_discipline_declared": art.get("test_discipline"),
        "no_roster_change_declared": art.get("no_roster_change"),
        "n_isolation_declared": art.get("n_isolation"),
        "resume_identity_declared": art.get("resume_identity"),
        "upstream_forecast_and_fee_contracts": {
            "what": "the UPSTREAM DECISION contracts, verbatim from the executed run.  They "
            "denominate the frozen OOF forecast and its hurdle, NOT the book returns read "
            "here: a book return is a normalized rate on that book's own original portfolio "
            "capital.  The two units are deliberately kept apart and never converted into "
            "one another in this artifact.",
            "forecast_unit_contract": art.get("unit_contract"),
            "forecast_gross_equivalent": art.get("unit_contract", {}).get("gross_unit_equivalent"),
            "fee_contract": art.get("fee_contract"),
            "forecast_price_assumption": art.get("forecast_price_assumption"),
            "calendar_action_contract": art.get("calendar_action_contract"),
            "cash_ledger_contract": art.get("cash_ledger_contract"),
        },
        "caveats_declared": art.get("caveats"),
        "data_root_declared": art.get("data_root"),
        "data_root_resolved_by_this_reader": str(data_root),
    }


# ------------------------------------------------------------------- breadth
def breadth(cells: list[dict]) -> dict:
    def dist(a: np.ndarray) -> dict:
        if a.size == 0:
            return {"cells": 0}
        return {
            "cells": int(a.size),
            "positive": int((a > 0).sum()),
            "negative": int((a < 0).sum()),
            "zero": int((a == 0).sum()),
            "min": f(a.min()),
            "p25": f(np.percentile(a, 25)),
            "median": f(np.percentile(a, 50)),
            "p75": f(np.percentile(a, 75)),
            "max": f(a.max()),
            "mean": f(a.mean()),
        }

    def group(field: str) -> dict:
        out = {}
        for v in sorted({c[field] for c in cells}, key=str):
            vals = [
                c["known_book_date_mean"]
                for c in cells
                if c[field] == v and c["known_book_date_mean"] is not None
            ]
            out[str(v)] = dist(np.array(vals, dtype=float))
        return out

    all_means = np.array(
        [c["known_book_date_mean"] for c in cells if c["known_book_date_mean"] is not None],
        dtype=float,
    )
    fold_units = [r for c in cells for r in c["folds"]]
    month_units = [r for c in cells for r in c["months"]]
    return {
        "population": "ALL active cells only; the cash0:cash controls are excluded here and "
        "reported in their own section",
        "active_cells": len(cells),
        "cells_with_no_known_book_date": sum(1 for c in cells if not c["known_book_dates"]),
        "cells_with_at_least_one_unknown_book_date": sum(
            1 for c in cells if c["unknown_book_dates"]
        ),
        "unknown_book_dates_total": int(sum(c["unknown_book_dates"] for c in cells)),
        "cells_positive_known_mean": sum(
            1 for c in cells if (c["known_book_date_mean"] or 0.0) > 0.0
        ),
        "cells_all_folds_positive": sum(
            1 for c in cells if c["folds"] and c["positive_folds"] == len(c["folds"])
        ),
        "cells_no_fold_negative": sum(
            1 for c in cells if c["folds"] and all(r["non_negative"] for r in c["folds"])
        ),
        "cells_at_least_half_months_positive": sum(
            1 for c in cells if c["months"] and c["positive_months"] * 2 >= len(c["months"])
        ),
        "cells_all_months_positive": sum(
            1 for c in cells if c["months"] and c["positive_months"] == len(c["months"])
        ),
        "cells_with_no_positive_month": sum(1 for c in cells if c["positive_months"] == 0),
        "cells_still_positive_after_best_date_removal": {
            f"best_{k}": sum(
                1
                for c in cells
                if c["best_date_removal"][f"best_{k}"]["retained_mean_positive"] is True
            )
            for k in REMOVALS
        },
        "cells_with_a_finite_unknown_break_even_bound": sum(
            1
            for c in cells
            if c["unknown_break_even"]["break_even_loss_per_unknown_date"] is not None
        ),
        "cell_fold_units": len(fold_units),
        "cell_fold_units_positive": sum(1 for r in fold_units if r["positive"]),
        "cell_month_units": len(month_units),
        "cell_month_units_positive": sum(1 for r in month_units if r["positive"]),
        "known_book_date_mean_distribution": dist(all_means),
        "known_book_date_mean_by_n": group("n"),
        "known_book_date_mean_by_round_trip_bps": group("round_trip_bps"),
        "known_book_date_mean_by_side": group("side"),
        "known_book_date_mean_by_cycle": group("cycles"),
        "known_book_date_mean_by_clock": group("clock"),
        "known_book_date_mean_by_view": group("policy"),
        "ordering_note": "every breakdown is an average over ALTERNATIVE books on one shared "
        "roster/forecast/date set.  Cells are never summed into a portfolio and a breakdown "
        "row is NOT a selected configuration.  These are counts, not a threshold: no count "
        "here passes, fails, promotes or rejects a cell.",
    }


# ------------------------------------------------------------------------------- main
def recover_denominators(daily: pl.DataFrame, published: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    """Compare published rates with independent known-date and all-date candidates."""
    known = pl.col("ret").is_not_null()
    columns = ("ret", "fees", "orders", "gross_pnl")
    candidates = daily.group_by(CELL_KEYS).agg(
        pl.len().alias("days"),
        known.sum().alias("known_days"),
        *[pl.col(c).filter(known).mean().alias(f"{c}_known") for c in columns],
        *[(pl.col(c).sum() / pl.len()).alias(f"{c}_all") for c in columns],
    )
    joined = published.select(list(CELL_KEYS) + ["ev", "fees", "orders", "gross_pnl"]).join(
        candidates, on=list(CELL_KEYS), how="left"
    )
    recovered = {
        "what": "published rates are compared with independently computed candidates; "
        "a numerical match identifies a compatible denominator, not an imputed return",
        "match_tolerance_abs": DENOM_TOL,
        "why_a_tolerance_and_not_zero": "different floating reduction orders can differ "
        "by a few ulp; matches use the stated tolerance, not bitwise equality",
        "candidates": {
            "known_date_mean": "mean over KNOWN book dates only",
            "all_date_mean": "column sum divided by all book dates; for ret this is a "
            "diagnostic denominator candidate, NOT a return estimate for UNKNOWN dates",
        },
    }
    for published_column, source_column in (
        ("ev", "ret"),
        ("fees", "fees"),
        ("orders", "orders"),
        ("gross_pnl", "gross_pnl"),
    ):
        record = {}
        for label, suffix in (("known_date_mean", "known"), ("all_date_mean", "all")):
            comparison = joined.select(
                pl.col(published_column).cast(pl.Float64).alias("published"),
                pl.col(f"{source_column}_{suffix}").cast(pl.Float64).alias("candidate"),
            ).drop_nulls()
            record[f"max_abs_diff_vs_{label}"] = (
                f((comparison["published"] - comparison["candidate"]).abs().max())
                if comparison.height
                else None
            )
        known_diff = record["max_abs_diff_vs_known_date_mean"]
        all_diff = record["max_abs_diff_vs_all_date_mean"]
        known_hit = known_diff is not None and known_diff <= DENOM_TOL
        all_hit = all_diff is not None and all_diff <= DENOM_TOL
        if known_hit and all_hit:
            record["published_denominator_recovered"] = "BOTH_CANDIDATES_MATCH"
            record["why"] = "both independent aggregates match within tolerance; "
            record["why"] += "this does not identify values on UNKNOWN book dates"
        elif known_hit:
            record["published_denominator_recovered"] = "known_date_mean"
        elif all_hit:
            record["published_denominator_recovered"] = "all_date_mean"
        else:
            record["published_denominator_recovered"] = "NOT_REPRODUCED_BY_EITHER_CANDIDATE"
        recovered[published_column] = record
    return candidates, recovered


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="shape of every cash-first TEST book over time (read-only diagnostic)"
    )
    ap.add_argument(
        "--data-root", default=None, help="default: the data_root the artifact published"
    )
    ap.add_argument(
        "--artifacts-root",
        type=Path,
        default=ROOT / "factory/artifacts",
        help="directory holding the executed discovery artifact",
    )
    ap.add_argument("--discovery", default=ARTIFACT_NAME)
    ap.add_argument("--replay-dir", type=Path, default=None)
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "factory/artifacts/owned_claim_cash_stability_four_costs.json",
    )
    ap.add_argument("--boot", type=int, default=BOOT_DEFAULT, help="month-block draws/cell; 0 off")
    ap.add_argument("--seed", type=int, default=SEED_DEFAULT)
    ap.add_argument(
        "--no-input-rehash",
        action="store_true",
        help="do not re-hash the declared score/panel/roster bytes (records them unchecked)",
    )
    ap.add_argument("--limit", type=int, default=0, help="bounded NON-EVIDENCE run over N dates")
    a = ap.parse_args(argv)

    art_path = a.artifacts_root / a.discovery
    if not art_path.is_file():
        raise SystemExit(f"executed discovery artifact not found: {art_path}")
    art = json.loads(art_path.read_text())

    data_root = ls.bps.resolve_data_root(a.data_root or art.get("data_root"))
    scores_root = Path(art["scores_root"])
    meta = json.loads(Path(art["scores_metadata"]).read_text())
    replay_dir = a.replay_dir or ls.v2_dir(data_root).joinpath(*REPLAY_RELPATH)
    if not replay_dir.is_dir():
        raise SystemExit(f"replay output directory not found: {replay_dir}")

    all_days = list(art["test_days"])
    days = all_days[: a.limit] if a.limit else list(all_days)

    # ---- canonical-split guard: discovery days only, never a protected read ------
    discovery = ls.discovery_days(data_root)
    if not set(all_days) <= set(discovery):
        raise SystemExit("the discovery artifact names days outside the canonical discovery split")
    read_days = sorted(set(days))
    if not set(read_days) <= set(discovery):
        raise SystemExit("refusing a read that is not a subset of the canonical discovery days")

    # ---- folds come from the SCORE METADATA, never a guessed calendar split -------
    fold_of = {d: w["fold"] for w in meta["folds"] for d in w["test_days"]}
    if not set(days) <= set(fold_of):
        raise SystemExit("the score metadata publishes no fold for every requested date")

    evidence = bool(art.get("evidence", False))
    notes: list[str] = []
    if a.limit:
        evidence = False
        notes.append(
            f"NON-EVIDENCE: bounded run over the first {a.limit} of {len(all_days)} published "
            "TEST dates; every fold/month/date count is partial by construction"
        )
    if not art.get("evidence", False):
        notes.append("the upstream discovery artifact is itself non-evidence")

    # ---- the executed replay parquet outputs -------------------------------------
    frames, parquet_sha = {}, {}
    for name in REPLAY_FRAMES:
        p = replay_dir / f"{name}.parquet"
        if not p.is_file():
            raise SystemExit(f"replay output missing: {p}")
        frames[name] = pl.read_parquet(p)
        parquet_sha[f"{name}.parquet"] = sha256_file(p)

    daily = frames["daily"].filter(pl.col("day").is_in(read_days))
    if daily.height == 0:
        raise SystemExit("no replay book rows for the requested dates")

    # ---- the cell grid the artifact declared vs what was executed -----------------
    # The cash0 control runs ONE cycle value (it executes no orders, so a cycle bound cannot
    # bind), which the artifact declares; it is verified here rather than assumed.
    executed = {tuple(r) for r in daily.select(CELL_KEYS).unique().iter_rows()}
    cash0_cycles = str((art.get("requested_controls") or {}).get("cash0_reference_cycles", "once"))
    if cash0_cycles not in art["cycles_run"]:
        raise SystemExit(f"the artifact's cash0 cycle control {cash0_cycles!r} was not executed")
    declared = {
        (c, n, s, p, cy)
        for c in art["clocks"]
        for n in art["ns"]
        for s in art["sides"]
        for p in art["policies"]
        for cy in art["cycles_run"]
    } | {
        (c, n, s, CASH0_POLICY, cash0_cycles)
        for c in art["clocks"]
        for n in art["ns"]
        for s in art["sides"]
    }
    if executed != declared:
        raise SystemExit(
            "the executed replay cells do not match the artifact's declared grid "
            f"(only_on_disk={sorted(executed - declared)[:4]}, "
            f"only_declared={sorted(declared - executed)[:4]})"
        )
    active_cells = sorted(k for k in executed if k[3] != CASH0_POLICY)
    cash0_cells = sorted(k for k in executed if k[3] == CASH0_POLICY)
    declared_active = (
        len(art["policies"])
        * len(art["cycles_run"])
        * len(art["clocks"])
        * len(art["ns"])
        * len(art["sides"])
    )
    if len(active_cells) != declared_active:
        raise SystemExit(
            f"active cell count {len(active_cells)} != declared grid product {declared_active}"
        )
    if CASH0_POLICY not in set(art["cash0_reference"]):
        raise SystemExit("the artifact's cash0 reference is not the literal cash0:cash book")

    # ---- exact date coverage per cell ---------------------------------------------
    per_cell_days = daily.group_by(CELL_KEYS).agg(pl.col("day").n_unique().alias("n_dates"))
    if int(per_cell_days["n_dates"].min()) != len(read_days):
        raise SystemExit(
            "some cells are missing a published date; a cell/day grid gap is not a known "
            "cash 0 ("
            f"{per_cell_days.filter(pl.col('n_dates') != len(read_days)).height} cells short)"
        )

    # ---- the UNKNOWN flag is exactly the null return ----------------------------
    flag_disagreements = daily.filter(pl.col("ret").is_null() != pl.col("unknown")).height
    if flag_disagreements:
        raise SystemExit(
            f"daily.parquet's unknown flag disagrees with its own null return on "
            f"{flag_disagreements} rows"
        )

    # ---- calendar columns vs the score metadata folds ----------------------------
    fold_mismatches = [
        r for r in daily.select(["day", "fold"]).unique().iter_rows() if fold_of.get(r[0]) != r[1]
    ]
    month_mismatches = [
        r for r in daily.select(["day", "month"]).unique().iter_rows() if str(r[0])[:7] != r[1]
    ]

    # ---- the replay's own per-day markers carry the run's execution pins -----------
    exec_pins = art.get("execution_input_sha256") or {}
    obs_rows = {str(d): int(n) for d, n in daily.group_by("day").len().iter_rows()}
    marker_check = {
        "days": 0,
        "status_ok": 0,
        "execution_pin_equals_artifact": 0,
        "daily_rows_equals_observed": 0,
        "mismatches": [],
    }
    for d in read_days:
        mp = replay_dir / "_done" / f"{d}.json"
        if not mp.is_file():
            marker_check["mismatches"].append({"day": d, "issue": "no _done marker"})
            continue
        m = json.loads(mp.read_text())
        marker_check["days"] += 1
        marker_check["status_ok"] += int(m.get("status") == "ok")
        same_pin = m.get("execution_pin") == exec_pins.get(d)
        marker_check["execution_pin_equals_artifact"] += int(same_pin)
        marker_check["daily_rows_equals_observed"] += int(
            int(m.get("daily_rows", -1)) == obs_rows.get(d, -1)
        )
        if m.get("status") != "ok" or not same_pin:
            marker_check["mismatches"].append(
                {"day": d, "issue": "marker status/execution_pin differs from the artifact"}
            )

    # ---- reconcile this reader's sums against the executed aggregates ------------
    def reconcile(name: str, keys: tuple, mine: pl.DataFrame, extra: dict | None = None):
        """Recompute this reader's own aggregates and diff them against the executed ones."""
        pub = frames[name]
        vals = [
            c
            for c in ("ev", "known_days", "days", "fees", "orders", "gross_pnl")
            if c in pub.columns
        ]
        j = pub.select(list(keys) + vals).join(mine, on=list(keys), how="left")
        out: dict = {
            "rows_published": int(pub.height),
            "rows_recomputed": int(mine.height),
        }
        if a.limit:
            out["comparison"] = (
                "SKIPPED: a bounded --limit run cannot reproduce whole-corpus aggregates"
            )
        elif j.height:
            out["rows_matched"] = int(j.filter(pl.col("ev_right").is_not_null()).height)
            for col in vals:
                other = f"{col}_right"
                if other not in j.columns:
                    continue
                pair = j.select(
                    pl.col(col).cast(pl.Float64).alias("x"),
                    pl.col(other).cast(pl.Float64).alias("y"),
                ).drop_nulls()
                out[f"max_abs_diff_{col}"] = (
                    f(float((pair["x"] - pair["y"]).abs().max())) if pair.height else None
                )
                out[f"rows_compared_{col}"] = int(pair.height)
        if extra:
            out.update(extra)
        return out

    # Which denominator did each PUBLISHED summary column actually use: the KNOWN book-date
    # count or the ALL book-date count?  This is measured, not assumed, because a cash-unit
    # column whose denominator silently differs from its neighbour's would mis-state every
    # rate quoted beside it.
    mine_summary, recovered_denominators = recover_denominators(daily, frames["summary"])

    recon = {
        "summary": reconcile(
            "summary",
            CELL_KEYS,
            mine_summary.select(
                *CELL_KEYS,
                "known_days",
                "days",
                pl.col("ret_known").alias("ev"),
                pl.col("fees_known").alias("fees"),
                pl.col("orders_all").alias("orders"),
                pl.col("gross_pnl_known").alias("gross_pnl"),
            ),
            {"denominators_recovered": recovered_denominators},
        ),
        "folds": reconcile(
            "folds",
            ("fold",) + CELL_KEYS,
            daily.group_by("fold", *CELL_KEYS).agg(
                pl.col("ret").mean().alias("ev"),
                pl.col("ret").count().alias("known_days"),
                pl.col("day").n_unique().alias("days"),
            ),
        ),
        "months": reconcile(
            "months",
            ("month",) + CELL_KEYS,
            daily.group_by("month", *CELL_KEYS).agg(
                pl.col("ret").mean().alias("ev"),
                pl.col("ret").count().alias("known_days"),
                pl.col("day").n_unique().alias("days"),
            ),
        ),
    }
    pd_scope = frames["per_date"].filter(pl.col("day").is_in(read_days))
    joined = pd_scope.join(
        daily.filter(~pl.col("unknown")).select(list(CELL_KEYS) + ["day", "ret"]),
        on=list(CELL_KEYS) + ["day"],
        how="left",
        suffix="_daily",
    )
    recon["per_date"] = {
        "note": "per_date.parquet must be the KNOWN book rows of daily.parquet, value for "
        "value; an UNKNOWN book may never appear there",
        "rows_published": frames["per_date"].height,
        "rows_in_scope": pd_scope.height,
        "rows_disagreeing_with_daily_known": int(
            (joined["book_date_ret"] != joined["ret"]).fill_null(True).sum()
        ),
        "unknown_books_present": int(pd_scope["unknown_book"].sum()),
    }

    # The cash ledger's own identity, re-checked here rather than trusted: on every cell's
    # KNOWN books, net must equal gross minus the fees actually charged.
    ledger = (
        daily.filter(~pl.col("unknown"))
        .group_by(CELL_KEYS)
        .agg(
            pl.col("ret").sum().alias("net"),
            (pl.col("gross_pnl").sum() - pl.col("fees").sum()).alias("gross_less_fees"),
        )
        .with_columns((pl.col("net") - pl.col("gross_less_fees")).abs().alias("abs_diff"))
    )
    recon["cash_ledger_identity"] = {
        "identity": "sum(KNOWN ret) == sum(KNOWN gross_pnl) - sum(KNOWN fees), per cell",
        "cells": int(ledger.height),
        "max_abs_diff": f(float(ledger["abs_diff"].max())) if ledger.height else None,
        "cells_outside_1e-9": int(ledger.filter(pl.col("abs_diff") > 1e-9).height),
    }

    # ---- per-cell analysis -------------------------------------------------------
    cells: list[dict] = []
    day_returns: dict[str, dict] = {}
    for key in active_cells:
        kd = dict(zip(CELL_KEYS, key, strict=True))
        sub = daily.filter(
            (pl.col("clock") == kd["clock"])
            & (pl.col("n") == kd["n"])
            & (pl.col("side") == kd["side"])
            & (pl.col("policy") == kd["policy"])
            & (pl.col("cycles") == kd["cycles"])
        )
        rec, day_map = analyse_cell(kd, sub, a.boot, a.seed)
        cells.append(rec)
        day_returns[rec["cell_id"]] = day_map

    ranked = sorted(
        cells,
        key=lambda c: (
            -(c["known_book_date_mean"] if c["known_book_date_mean"] is not None else math.inf),
            c["cell_id"],
        ),
    )
    for i, c in enumerate(ranked, start=1):
        c["known_mean_rank"] = i

    # ---- same-cell contrasts (differences between alternative books) --------------
    by_cost: dict[tuple, dict] = {}
    by_cycle: dict[tuple, dict] = {}
    for c in cells:
        by_cost.setdefault((c["clock"], c["n"], c["policy"], c["cycles"]), {})[
            c["round_trip_bps"]
        ] = c["cell_id"]
        by_cycle.setdefault((c["clock"], c["n"], c["round_trip_bps"], c["policy"]), {})[
            c["cycles"]
        ] = c["cell_id"]

    cost_riders = sorted({bps_of(s) for s in art["sides"]})
    cost_contrasts = []
    for family in sorted(by_cost):
        sides = by_cost[family]
        for lo, hi in zip(cost_riders, cost_riders[1:], strict=False):
            if lo not in sides or hi not in sides:
                continue
            clock, n, policy, cycles = family
            cost_contrasts.append(
                {
                    "cell_family": f"clock{clock}|n{n}|{policy}|{cycles}",
                    "clock": clock,
                    "n": n,
                    "policy": policy,
                    "cycles": cycles,
                    **paired(
                        day_returns[sides[lo]],
                        day_returns[sides[hi]],
                        read_days,
                        f"{lo}bps",
                        f"{hi}bps",
                    ),
                }
            )

    cycle_names = list(art["cycles_run"])
    if len(cycle_names) != 2:
        raise SystemExit(
            "the cycle contrast is defined only for exactly two declared cycle controls; "
            f"the artifact declares {cycle_names}, so this reader would silently drop them"
        )
    cycle_contrasts = []
    for family in sorted(by_cycle):
        cyc = by_cycle[family]
        if any(k not in cyc for k in cycle_names):
            raise SystemExit(f"the executed grid is missing a cycle control for {family}")
        clock, n, bps, policy = family
        cycle_contrasts.append(
            {
                "cell_family": f"clock{clock}|n{n}|{bps}bps|{policy}",
                "clock": clock,
                "n": n,
                "round_trip_bps": bps,
                "policy": policy,
                **paired(
                    day_returns[cyc[cycle_names[1]]],
                    day_returns[cyc[cycle_names[0]]],
                    read_days,
                    cycle_names[1],
                    cycle_names[0],
                ),
            }
        )

    # ---- the cash0 ledger identity, kept strictly separate ------------------------
    cash0_rows = []
    for key in cash0_cells:
        kd = dict(zip(CELL_KEYS, key, strict=True))
        sub = daily.filter(
            (pl.col("clock") == kd["clock"])
            & (pl.col("n") == kd["n"])
            & (pl.col("side") == kd["side"])
            & (pl.col("policy") == kd["policy"])
            & (pl.col("cycles") == kd["cycles"])
        )
        r = sub["ret"].drop_nulls()
        cash0_rows.append(
            {
                "cell_id": cell_id(kd),
                **kd,
                "round_trip_bps": bps_of(kd["side"]),
                "book_dates": sub.height,
                "known_book_dates": int(r.len()),
                "unknown_book_dates": int(sub["unknown"].sum()),
                "orders_total": int(sub["orders"].sum()),
                "known_sum": f(r.sum()) if r.len() else None,
                "known_book_date_mean": mean(r.to_numpy()),
                "max_abs_return": f(r.abs().max()) if r.len() else None,
                "all_returns_exactly_zero": bool(r.len() == int((r == 0.0).sum())),
            }
        )
    cash0_section = {
        "policy_literal": CASH0_POLICY,
        "why_separate": "the cash0 book withholds every forecast, so it executes no orders and "
        "is a LEDGER IDENTITY reference rather than a second experiment.  It is excluded "
        "from every active-cell count, breadth row and contrast above.",
        "cell_grid": f"{len(art['clocks'])} clocks x {len(art['ns'])} N x {len(art['sides'])} "
        "cost legs, cycles=once only (a cycle bound cannot bind on a book that never trades)",
        "cells": len(cash0_rows),
        "books": int(sum(r["book_dates"] for r in cash0_rows)),
        "books_with_nonzero_return": sum(
            1 for r in cash0_rows if not r["all_returns_exactly_zero"]
        ),
        "cells_with_any_order": int(sum(1 for r in cash0_rows if r["orders_total"] > 0)),
        "books_unknown": int(sum(r["unknown_book_dates"] for r in cash0_rows)),
        "identity_reproduced": bool(
            cash0_rows and all(r["all_returns_exactly_zero"] for r in cash0_rows)
        ),
        "declared_identity_in_artifact": art.get("cash0_identity"),
        "per_cell": cash0_rows,
    }

    pins = declared_pins(art, data_root, scores_root, days, not a.no_input_rehash)
    failures = pin_failures(pins)
    if failures:
        evidence = False
        notes.append(
            "NON-EVIDENCE: declared input pins did not verify against disk ("
            + ", ".join(failures)
            + "); the books below are still read as executed, but this reader cannot prove "
            "they were produced from the published bytes"
        )

    artifact = {
        "kind": KIND,
        "status": "full_discovery_evidence" if evidence else "smoke_non_evidence",
        "evidence": evidence,
        "non_evidence_notes": notes,
        "producer": {
            "script": "factory/scripts/owned_claim_cash_stability.py",
            "this_script_sha256": sha256_file(Path(__file__).resolve()),
            "lifecycle_study_sha256": sha256_file(Path(ls.__file__).resolve()),
            "polars_version": pl.__version__,
            "numpy_version": np.__version__,
            "reads": "the executed discovery artifact, the replay parquet outputs it wrote, "
            "and (hash only) the score/panel/roster bytes it declares",
            "does_not": "replay, re-price, re-hurdle, refit, re-select or impute anything",
        },
        "units": {
            "return_unit": "the NORMALIZED PORTFOLIO return of ONE cash-first book.  The "
            "replay ledger starts every book at cash = 1.0, i.e. the whole ORIGINAL PORTFOLIO "
            "CAPITAL of that book, and a book date's return is its settled cash at the close "
            "minus that 1.0; each original claim owns its own 1/n slice of it and any "
            "remainder stays unassigned cash.  So ret, fees and gross_pnl are all fractions of "
            "the book's own original portfolio capital, not per-claim quantities and not the "
            "forecast's increment unit.",
            "not_the_forecast_unit": "the frozen OOF forecast that DECIDED the trades is "
            "denominated differently -- incremental inherited dollars per ORIGINAL CLAIM CASH "
            "dollar, with its own gross conversion and round-trip hurdle.  That is the "
            "upstream ACTION-INPUT unit and it is kept, verbatim and separate, in "
            "historical_lineage.upstream_forecast_and_fee_contracts.  It is NOT the unit of "
            "any return, mass, mean or bound printed here.",
            "capital_conversion": "a normalized rate converts to money by multiplying by the "
            "book's own original portfolio capital (the replay notional declared by the "
            "accounting engine).  This artifact reports the RATE; it never multiplies one out "
            "and never states a dollar P&L.",
            "what_one_mean_is": "the arithmetic mean of the KNOWN book-date rates of ONE "
            "cell, i.e. that single alternative book's average per-book-date return on its own "
            "original portfolio capital.  It is already a portfolio rate for that one book; "
            "it is not a per-claim return and it is not a rate on any shared capital.",
            "never": "cells are ALTERNATIVE books of the same roster, forecasts and dates, so "
            "no column here may be summed across cells, averaged into one portfolio, or read "
            "as a fundable combination.  The prohibition is about MIXING books, not about the "
            "unit being a portfolio rate.",
        },
        "denominators": {
            "book_date": "one (cell, published TEST date) row of daily.parquet; every active "
            "cell has exactly one row per published date in scope",
            "known_book_date_mean": "sum(ret)/count(ret) over KNOWN book dates only; an "
            "UNKNOWN book leaves BOTH the numerator and the denominator",
            "known_sum": "the same KNOWN returns summed, in the unit above",
            "known_book_date_se": "sample sd of the KNOWN returns / sqrt(known count); null "
            "below two known dates",
            "positive_folds": "folds whose KNOWN mean > 0, over the folds present in that cell",
            "positive_months": "months whose KNOWN mean > 0, over the months present in that cell",
            "positive_mass": "sum of strictly positive KNOWN book-date returns; "
            "share_of_positive_mass = block mass / positive mass, null when the cell has no "
            "positive book date",
            "share_of_known_net": "block mass / the cell's KNOWN sum; signed and unbounded, "
            "so it is never a claim denominator and never a claim count",
            "best_k_removal": "the k largest KNOWN book-date returns OF THAT CELL removed, "
            "then the mean over the remaining KNOWN book dates; removed dates are listed "
            "exactly, by date and value",
            "cost_contrast": "mean over dates where BOTH adjacent-cost books of the same "
            "(clock, N, view, cycle) are KNOWN; excluded dates are listed exactly",
            "cycle_contrast": f"mean of {cycle_names[1]} minus {cycle_names[0]} over dates "
            "where both books of the same (clock, N, cost, view) are KNOWN; excluded dates "
            "are listed exactly",
            "unknown_break_even": "the cell's KNOWN sum / its OWN count of UNKNOWN book "
            "dates, SIGNED and in the book's own normalized portfolio-capital unit: the "
            "per-date amount the UNKNOWN dates must move against the known sum to erase it.  "
            "It is a hypothetical bound, not an imputed return, not a forecast, and its "
            "direction is stated in cells[].unknown_break_even.bound_sign",
            "month_block_resampling": "resampled KNOWN mass / resampled KNOWN date count over "
            "whole months drawn with replacement; UNKNOWN dates are in neither",
            "summed_cash_columns": "fees_sum_known_book_dates, gross_pnl_sum_known_book_dates, "
            "orders_sum_known_book_dates and fresh_entries_sum_known_book_dates are SUMS over "
            "the cell's KNOWN books, not rates: their denominator is the set of resolved books, "
            "and gross_pnl - fees equals the known net mass of those same books, up to "
            "floating-point round-off",
        },
        "scope": {
            "published_test_dates": len(all_days),
            "dates_read": len(read_days),
            "dates_are_canonical_discovery_days": True,
            "protected_half_read": False,
            "discovery_days_in_split": len(discovery),
            "active_cells": len(active_cells),
            "active_cell_grid_product": {
                "policies": len(art["policies"]),
                "cycles": len(art["cycles_run"]),
                "clocks": len(art["clocks"]),
                "ns": len(art["ns"]),
                "sides": len(art["sides"]),
                "product": declared_active,
            },
            "cash0_control_cells": len(cash0_cells),
            "cash0_is_excluded_from_active_cells": True,
            "book_rows_read": daily.height,
            "cost_leg_riders_bps": cost_riders,
            "months_covered": len({str(m) for m in daily["month"].unique().to_list()}),
            "folds_covered": sorted({int(x) for x in daily["fold"].unique().to_list()}),
            "fold_source": "the score metadata folds (metadata.json 'folds'), never a guessed "
            "calendar split",
            "daily_fold_column_agrees_with_score_metadata": not fold_mismatches,
            "daily_month_column_equals_date_prefix": not month_mismatches,
        },
        "lineage": {
            "discovery_artifact_path": str(art_path),
            "discovery_artifact_sha256": sha256_file(art_path),
            "replay_dir": str(replay_dir),
            "replay_parquet_sha256": parquet_sha,
            "replay_day_marker_check": marker_check,
            "pins": pins,
            "pins_verified": not failures,
            "pin_failures": failures,
            "reconciliation_vs_published_aggregates": recon,
            "unknown_flag_equals_null_return": True,
        },
        "historical_lineage": historical_lineage(art, meta, data_root),
        "cells": cells,
        "breadth": breadth(cells),
        "contrasts": {
            "what": "same-cell, same-date DIFFERENCES between two alternative books of the "
            "same replay run, each side already a normalized rate on its own book.  Neither "
            "side is selected, and no difference is a total across books.",
            "cost_adjacent_contrasts": cost_contrasts,
            "cycle_contrasts": cycle_contrasts,
            "cycle_contrast_direction": f"{cycle_names[1]} minus {cycle_names[0]}",
        },
        "cash0_reference": cash0_section,
        "test_multiple_selection_boundary": {
            "published_test_dates": len(read_days),
            "active_cells_scored_on_those_dates": len(active_cells),
            "cash0_control_cells_scored_on_those_dates": len(cash0_cells),
            "cells_are": "alternative books over the SAME original roster, the SAME frozen "
            "OOF forecasts, the SAME minute grid and the SAME dates",
            "cells_are_not": "independent samples, portfolio positions or fundable "
            "combinations; they share dates, names and fills",
            "multiple_selection": "every active cell was scored on this one TEST corpus, so "
            "the largest cell mean, the most folds-positive cell and every 'best 1/3/5/10' "
            "removal are ORDER STATISTICS over correlated alternatives.  Reading the maximum "
            "as a single pre-registered hypothesis is exactly the selection this artifact "
            "refuses to make, and it is also why NO cell is named, promoted or frozen here.",
            "what_is_reported": "every cell's own fold/month/date shape, so the spread is "
            "visible instead of a winner",
            "no_significance_test": True,
            "no_p_value_published": True,
            "no_promoted_edge": "no cell is declared best, promoted, validated or "
            "out-of-sample here, and nothing here is a claim about future money",
            "no_stability_threshold": "this artifact deliberately defines NO universal "
            "pass/fail/promote threshold.  Counts such as positive folds or months describe "
            "this TEST corpus; they are not a rule, and a cell that satisfies none of them "
            "is not thereby validated.",
            "no_rule_tuned_on_test": "no date, view, clock, N, cost leg, cycle control or "
            "removal depth was chosen using these numbers",
            "ordering_field": "cells[].known_mean_rank is a descriptive descending ordering "
            "(1 = largest KNOWN book-date mean).  It selects nothing and implies nothing.",
        },
        "boundaries": [
            "UNKNOWN books are excluded from every mean and never imputed as a known zero; "
            "each cell lists its exact UNKNOWN dates, and its break-even bound states the "
            "SIGNED per-date amount those dates would have to move against the known sum to "
            "erase it (a negative known sum makes that a required gain, not a negative loss).",
            "each cell's own figures are already a NORMALIZED PORTFOLIO rate on that one "
            "book's original portfolio capital; what is forbidden is SUMMING or averaging "
            "ACROSS cells, because cells are alternative books of one shared roster and "
            "forecast corpus rather than positions of a single account.",
            "best-k date removal is a within-cell sensitivity on this same TEST corpus; it is "
            "not an out-of-sample check and not a selection rule.",
            "month-block resampling describes dispersion on the observed calendar; it is not "
            "a significance test, not a p-value and not a confidence claim.",
            "cost contrasts change both modeled fees and cost-aware admission on the same "
            "frozen forecasts and roster; actual trades can differ, and costs are not observed.",
            f"the {cycle_names[1]}-minus-{cycle_names[0]} contrast is a cycle-control "
            "difference over the same forecast corpus; neither cycle control is selected here.",
            "cash0:cash is a ledger identity and is excluded from every active count.",
        ],
    }

    a.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = a.out.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(artifact, indent=2, allow_nan=False) + "\n")
    tmp.replace(a.out)
    print(
        "[cash-stability]",
        a.out,
        "status",
        artifact["status"],
        "days",
        len(read_days),
        "active_cells",
        len(active_cells),
        "cash0_cells",
        len(cash0_cells),
        "cost_contrasts",
        len(cost_contrasts),
        "cycle_contrasts",
        len(cycle_contrasts),
        "unknown_book_dates",
        artifact["breadth"]["unknown_book_dates_total"],
        "pins_verified",
        artifact["lineage"]["pins_verified"],
        "summary_max_abs_diff_ev",
        recon["summary"].get("max_abs_diff_ev"),
        "boot",
        a.boot,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
