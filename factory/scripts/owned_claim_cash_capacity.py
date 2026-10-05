#!/usr/bin/env python3
"""CASH-FIRST OWNED-CLAIM CAPACITY READER -- how much REALIZED money sat on large sizes.

The cash-first replay published its economics and an execution reader published what its
legs cost.  This reader publishes the one number neither of them answers in money terms:
of the money that was ALREADY REALIZED, how much of it was produced by legs whose size
was a large fraction of the minute bar's historical traded volume. This measures money
dependence on unpriced liquidity assumptions, not whether additional orders could fill.

It is a READER.  It replays nothing, re-prices nothing, re-sizes nothing, re-hurdles
nothing, refits nothing and opens no price, quote, volume, roster or score source.  The
only size evidence it uses is ``fills.participation``, which the executed replay already
published.  No new source-panel read is attempted.

Every ruler is prespecified and every ruler is retained
----------------------------------------------------
Participation bands are fixed before any number is seen: ``<=1%``, ``(1%,5%]``,
``(5%,25%]``, ``(25%,100%]``, ``>100%`` (order size exceeding the WHOLE bar's traded
volume) and ``missing volume``.  Nothing is tuned, nothing is merged and NO band is
dropped.  A band is a CLASSIFICATION OF ALREADY REALIZED MONEY, not a filter: the whole
cell's own known-set net, fee and gross remain published beside every band table, and no
band-restricted mean is ever named a strategy EV, an out-of-sample figure or a
recommendation.

Money unit
----------
Every money-like field is a FRACTION OF THE BOOK'S OWN ORIGINAL PORTFOLIO CAPITAL: the
replay ledger starts each book at ``cash = 1.0``, so ``daily.ret``, ``daily.gross_pnl``,
``daily.fees``, ``members.net_pnl``/``gross_pnl``/``fees``, and every fills and pair
gross/fee fraction are already on that book's original portfolio capital.  This is NOT the
upstream forecast's unit (incremental inherited dollars per ORIGINAL CLAIM CASH dollar),
which decides the trades and is never converted here.

The published-notional ``$`` restatement
----------------------------------------
Participation is defined by the engine's PUBLISHED ``notional`` default of 10,000 USD, a
size the executed corpus already assumes.  A single, clearly labelled linear restatement
of the SAME realized money at that already-declared size is published under
``dollar_restatement_at_published_notional``.  It is arithmetic on already-realized
fractions.  It is NOT a claim that 10,000 USD is funded, available, executable or
capacity-certified.

The lower-account ladder is NOT that
------------------------------------
``account_size_ladder`` rescales PARTICIPATION ONLY, at 10,000 / 1,000 / 100 USD.  Under
the unchanged engine, sizing is a fixed fraction of ledger cash, so order quantity is
homogeneous of degree one in account size and participation scales exactly linearly:
1,000 USD is one tenth of 10,000 USD at the same bar.  That is quantity linearity and
nothing else.  No smaller-size dollar profit, fill, slippage, fee or capacity figure is
produced, implied or priced, and no re-priced P&L appears anywhere in this artifact.

Boundaries, each also published in the artifact rather than merely claimed here
---------------------------------------------------------------------------
* ALTERNATIVE BOOKS, NEVER A PORTFOLIO.  A cell is one (clock, N, side_cost, policy,
  cycles) alternative book over the same dates.  No figure is ever added across cells, no
  cell is ranked, selected, promoted or named best, and no band table is a portfolio.
* WHOLE UNKNOWN BOOK EXCLUDED FROM EVERY NET COHORT.  An UNKNOWN book is an unresolved
  cashflow, not a zero.  Its pairs, legs, members, gross, fees and net all leave the money
  cohorts together -- not merely its unknown member while its otherwise-known siblings stay
  in.  Every cell reports its exact UNKNOWN book-date count, its unresolved fresh tickers
  and its affordability-unknown counts; the corpus section publishes the exact UNKNOWN
  date list.  UNKNOWN is never imputed.
* PROXY, NOT QUOTE.  Participation is order size over the traded volume of the bar whose
  OPEN is the fill.  The fill is a modelled canonical-bar open.  Nothing here certifies a
  broker, venue, route, queue position, fill probability or extended-hours behaviour, and
  no quote acquisition or network read is required or performed.  A band boundary is a
  SIZE ruler, not a fillability verdict.
* NO RETROACTIVE EXCLUSION.  A band table measures dependence of ALREADY REALIZED money.
  No cut, cap, filter, entry rule or exit rule is derived from it, no trade is removed
  retroactively, and no kept mean is presented as a strategy EV.
* PREDECLARED VINTAGE PINS ARE RECORDED, NOT FORCED.  The executed artifact's declared
  producer SHA belongs to the driver that actually ran it -- the archived two-cost run
  pins the 016056a-vintage driver, which HEAD no longer carries.  Declared and on-disk
  digests are both recorded with an interpretation; a driver that has since changed on
  disk does not make this read non-evidence and is never used to reject a corpus.
* PRE-OPEN vs REGULAR.  ET is ABSOLUTE minutes since midnight on the canonical grid
  (540 = 09:00, 570 = 09:30, 959 = 15:59 session end).  The pre-open / regular split is a
  literal 570-minute threshold on that grid.  It is a classification of the modelled bar
  clock, not a venue session statement and not broker extended-hours certification.

Optional: the SAME reader runs against the archived TWO-cost corpus or the FOUR-cost
corpus.  The cost grid is read from the executed artifact's declared grid, so a 2-cost and
a 4-cost run differ only in the declared riders, never in code.

Usage
-----
    .venv/bin/python factory/scripts/owned_claim_cash_capacity.py \
        --replay-root <v2>/owned_claim/replay_cash_first_four_costs \
        --evidence factory/artifacts/owned_claim_cash_first_four_costs.json \
        --out factory/artifacts/owned_claim_cash_capacity_four_costs.json

  [--data-root DIR]     # default: the data_root the evidence artifact published
  [--replay-root DIR]   # default: <v2>/owned_claim/replay_cash_first, or the artifact's
                        #   declared replay root when it publishes one
  [--evidence PATH]     # the executed replay artifact whose declared grid is read
  [--out PATH]          # atomic write: a .tmp sibling is replaced, never a partial file
  [--limit-days N]      # tiny NON-EVIDENCE read over the first N published dates
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
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
import lifecycle_study as ls  # noqa: E402
import owned_claim_cash_execution as exe  # noqa: E402  (build_pairs only; its main is not run)

SCRIPT = Path(__file__).resolve()
PRODUCER = SCRIPT.with_name("owned_claim_cash_first_replay.py")
ENGINE = SCRIPT.with_name("owned_claim_replay.py")
DEFAULT_REPLAY_SUBDIR = ("owned_claim", "replay_cash_first")
FRAMES = ("daily", "members", "fills", "summary")

CELL_KEYS = ("clock", "n", "side_cost", "policy", "cycles")
BOOK_KEYS = ("day", "clock", "n", "side_cost", "policy", "cycles")
PAIR_KEYS = (*BOOK_KEYS, "ticker")
CASH0_POLICY = "cash0:cash"

# The engine's PUBLISHED ``notional`` default.  The producer calls simulate() without that
# argument, so this is the size behind every published participation figure.
PUBLISHED_NOTIONAL = 10000.0
# A DIAGNOSTIC ladder only.  Quantity linearity, never a smaller-size priced profit.
ACCOUNT_LADDER = (10000.0, 1000.0, 100.0)

# Prespecified participation rulers.  Ordered low to high; ``missing_volume`` is separate
# because an absent denominator is an unknown, not a zero.
BAND_EDGES = (0.01, 0.05, 0.25, 1.00)
BAND_LABELS = (
    "missing_volume",
    "le_1pct",
    "gt_1pct_le_5pct",
    "gt_5pct_le_25pct",
    "gt_25pct_le_100pct",
    "gt_100pct_full_bar_volume",
)
BAND_MISSING = 0
BAND_OVER_FULL_BAR = 5
BAND_MEASURED = (1, 2, 3, 4)

# A KNOWN book date that placed NO order is not a date with unknown size: it is a real,
# common, financially distinct case -- capital simply stayed undeployed -- and it must never
# be filed under ``missing_volume``, which means a leg whose bar volume was absent.
DATE_BAND_LABELS = (
    "no_leg_executed",
    "missing_volume",
    "le_1pct",
    "gt_1pct_le_5pct",
    "gt_5pct_le_25pct",
    "gt_25pct_le_100pct",
    "gt_100pct_full_bar_volume",
)
DATE_NO_LEG = 0
DATE_OFFSET = 1  # every participation band shifts by one behind the no-leg band

# (labels, missing-volume index, over-full-bar index, measured-band indices).  A leg and a
# pair are always classified by a real participation value; only the book-date table
# carries the extra no-leg band.
BAND_LAYOUT_LEG = (BAND_LABELS, BAND_MISSING, BAND_OVER_FULL_BAR, BAND_MEASURED)
BAND_LAYOUT_DATE = (
    DATE_BAND_LABELS,
    DATE_OFFSET + BAND_MISSING,
    DATE_OFFSET + BAND_OVER_FULL_BAR,
    tuple(i + DATE_OFFSET for i in BAND_MEASURED),
)

# ET is absolute minutes since midnight on the canonical grid: 570 = 09:30.
PRE_OPEN_ET = 570

TOL = 1e-9  # reader-side sum reordering tolerance
PER_CELL_UNKNOWN_DAY_LIST_CAP = 40


# ------------------------------------------------------------------------------- helpers
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def f(x):
    """JSON-safe finite float.  NaN/inf are UNKNOWN (null), never a silent zero."""
    if x is None:
        return None
    x = float(x)
    if math.isnan(x) or math.isinf(x):
        return None
    return 0.0 if x == 0.0 else x  # -0.0 and 0.0 are the same zero


def ratio(num, den):
    if num is None or den is None or den == 0:
        return None
    return f(num / den)


def dist(a, unit: str) -> dict:
    a = np.asarray(a, dtype=float)
    a = a[np.isfinite(a)]
    if not a.size:
        return {"unit": unit, "n": 0}
    return {
        "unit": unit,
        "n": int(a.size),
        "sum": f(a.sum()),
        "mean": f(a.mean()),
        "median": f(float(np.median(a))),
        "p05": f(float(np.percentile(a, 5))),
        "p95": f(float(np.percentile(a, 95))),
        "max": f(a.max()),
        "min": f(a.min()),
        "n_positive": int((a > 0).sum()),
        "n_negative": int((a < 0).sum()),
        "n_zero": int((a == 0).sum()),
    }


def bps_of(side: float) -> int:
    """Modeled ROUND-TRIP bps for one per-side cost: 0.005 -> 100, 0.00125 -> 25."""
    return int(round(float(side) * 2.0 * 10000.0))


def cell_id(key: dict) -> str:
    return (
        f"clock{key['clock']}|n{key['n']}|{bps_of(key['side_cost'])}bps|"
        f"{key['policy']}|{key['cycles']}"
    )


def band_codes(p) -> np.ndarray:
    """Map measured nonnegative participation to bands; unavailable/invalid values stay missing."""
    p = np.asarray(p, dtype=float)
    out = np.full(p.shape, BAND_MISSING, dtype=np.int8)
    fin = np.isfinite(p) & (p >= 0)
    out[fin & (p <= 0.01)] = 1
    out[fin & (p > 0.01) & (p <= 0.05)] = 2
    out[fin & (p > 0.05) & (p <= 0.25)] = 3
    out[fin & (p > 0.25) & (p <= 1.00)] = 4
    out[fin & (p > 1.00)] = BAND_OVER_FULL_BAR
    return out


def partition(frame: pl.DataFrame) -> dict:
    """{cell_key_tuple: subframe}.  One pass; no whole-frame regroup inside the loop."""
    if frame.height == 0:
        return {}
    return {
        (k if isinstance(k, tuple) else (k,)): sub
        for k, sub in frame.partition_by(list(CELL_KEYS), as_dict=True).items()
    }


def top_k_share(a, ks=(1, 5, 10, 25)) -> dict:
    a = np.asarray(a, dtype=float)
    a = a[np.isfinite(a)]
    n = int(a.size)
    if n == 0:
        return {"n": 0, "is_a_measure_not_a_rule": CONCENTRATION_NOTE}
    total = float(a.sum())
    out: dict = {"n": n, "sum": f(total)}
    if total > 0:
        srt = np.sort(a)[::-1]
        out["top_k_share_of_total"] = {
            f"top{k}": f(float(srt[: min(k, n)].sum()) / total) for k in ks
        }
        cum = np.cumsum(srt)
        out["units_for_50pct_of_total"] = int(min(np.searchsorted(cum, 0.5 * total) + 1, n))
        out["units_for_80pct_of_total"] = int(min(np.searchsorted(cum, 0.8 * total) + 1, n))
    else:
        out["top_k_share_of_total"] = None
        out["share_undefined_reason"] = (
            "the total is not positive, so a share of total is undefined; it is published as "
            "null rather than as a signed or absolute substitute"
        )
    for k in ks:
        out[f"mean_excluding_top{k}"] = (
            f(float(np.delete(np.sort(a)[::-1], np.arange(k)).sum()) / (n - k)) if n > k else None
        )
    out["is_a_measure_not_a_rule"] = CONCENTRATION_NOTE
    return out


CONCENTRATION_NOTE = (
    "concentration of an already published mean; no cut, cap or exclusion is derived from "
    "it, no trade was removed retroactively and nothing was optimized on it"
)


# ------------------------------------------------------------------- band money tables
def band_rows(codes, net, gross, fees, labels=BAND_LABELS) -> list[dict]:
    """One row per PRESET band.  Every band appears, including the empty ones."""
    codes = np.asarray(codes, dtype=np.int8)
    net = np.asarray(net, dtype=float)
    gross = np.asarray(gross, dtype=float)
    fees = np.asarray(fees, dtype=float)
    n = int(codes.size)
    pos_mass = float(net[net > 0].sum()) if n else 0.0
    net_sum = float(net.sum()) if n else 0.0
    rows = []
    for i, label in enumerate(labels):
        sel = codes == i
        cnt = int(sel.sum())
        bn = net[sel]
        rows.append(
            {
                "band": label,
                "count": cnt,
                "share_of_units": ratio(cnt, n),
                "net_sum": f(bn.sum()) if cnt else None,
                "net_mean_per_unit": f(bn.mean()) if cnt else None,
                "gross_sum": f(gross[sel].sum()) if cnt else None,
                "fees_sum": f(fees[sel].sum()) if cnt else None,
                "n_positive_net": int((bn > 0).sum()) if cnt else 0,
                "n_negative_net": int((bn < 0).sum()) if cnt else 0,
                "share_of_positive_mass": ratio(float(bn[bn > 0].sum()), pos_mass)
                if cnt and pos_mass
                else None,
                "share_of_net": ratio(float(bn.sum()), net_sum) if cnt and net_sum else None,
            }
        )
    return rows


def band_table(codes, net, gross, fees, definition: str, layout=BAND_LAYOUT_LEG) -> dict:
    """Realized money split by a prespecified participation band.  EVERY band retained."""
    codes = np.asarray(codes, dtype=np.int8)
    net = np.asarray(net, dtype=float)
    gross = np.asarray(gross, dtype=float)
    fees = np.asarray(fees, dtype=float)
    n = int(codes.size)
    labels, missing_idx, over_idx, measured = layout
    rows = band_rows(codes, net, gross, fees, labels)
    pos_mass = float(net[net > 0].sum()) if n else 0.0
    net_sum = float(net.sum()) if n else 0.0
    over = codes == over_idx
    under = np.isin(codes, measured)
    no_leg_idx = layout[0].index("no_leg_executed") if "no_leg_executed" in layout[0] else None
    no_leg = int((codes == no_leg_idx).sum()) if no_leg_idx is not None else 0
    n_missing = int((codes == missing_idx).sum())
    n_over, n_under = int(over.sum()), int(under.sum())
    return {
        "definition": definition,
        "units": n,
        "bands": rows,
        "partition_identity": {
            "sum_of_band_counts": int(sum(r["count"] for r in rows)),
            "equals_units": bool(sum(r["count"] for r in rows) == n),
            "net_sum": f(net_sum),
            "sum_of_band_net": f(sum(r["net_sum"] or 0.0 for r in rows)),
            "abs_residual_net": f(abs(sum(r["net_sum"] or 0.0 for r in rows) - net_sum)),
            "gross_sum": f(gross.sum() if n else 0.0),
            "fees_sum": f(fees.sum() if n else 0.0),
            "tolerance": TOL,
        },
        "full_bar_volume_split": {
            "definition": (
                "gt_100pct_full_bar_volume = order size EXCEEDING the whole executed bar's "
                "traded volume; the four lower measured bands are at or under it. Those two "
                "sides partition every unit that carries a participation value. Missing volume "
                "is an UNKNOWN denominator, never a zero, and a book date that placed no order "
                "carries no size at all -- both are counted beside the split rather than "
                "folded into it, so the four counts below always add back to the units."
            ),
            "units_over_full_bar_volume": n_over,
            "units_at_or_under_full_bar_volume": n_under,
            "units_missing_volume": n_missing,
            "units_with_no_leg_executed": no_leg,
            "units_with_no_leg_executed_meaning": (
                "nonzero only in the book-date table, where it counts KNOWN dates that placed "
                "no order: capital stayed undeployed, a real dated outcome rather than an "
                "unknown size. It is zero for the leg and pair tables, whose units always "
                "carry a participation value."
            ),
            "four_way_partition_identity": {
                "over": n_over,
                "at_or_under": n_under,
                "missing_volume": n_missing,
                "no_leg_executed": no_leg,
                "sum": n_over + n_under + n_missing + no_leg,
                "units": n,
                "equals_units": bool(n_over + n_under + n_missing + no_leg == n),
                "note": (
                    "no_leg is counted directly by its own band index, not derived as a "
                    "remainder, so this identity is an independent check on the split"
                ),
            },
            "net_from_over_full_bar_volume": f(net[over].sum()) if n_over else None,
            "net_from_at_or_under_full_bar_volume": f(net[under].sum()) if n_under else None,
            "share_of_net_from_over_full_bar_volume": ratio(
                float(net[over].sum()) if n_over else None, net_sum
            ),
            "share_of_positive_mass_from_over_full_bar_volume": ratio(
                float(net[over][net[over] > 0].sum()) if n_over else None, pos_mass
            ),
            "gross_from_over_full_bar_volume": f(gross[over].sum()) if n_over else None,
            "fees_from_over_full_bar_volume": f(fees[over].sum()) if n_over else None,
            "is_not": (
                "a fillability verdict: historical traded volume is not displayed depth, "
                "and an additional order could change counterfactual volume. Queue, venue, "
                "route, partial-fill and price-impact behavior remain unobserved."
            ),
        },
    }


# ------------------------------------------------------------------- clock-band tables
def clock_codes(et) -> np.ndarray:
    """1 = pre-open (ET < 570), 2 = regular grid (ET >= 570), 0 = ET unknown."""
    et = np.asarray(et, dtype=float)
    out = np.zeros(et.shape, dtype=np.int8)
    fin = np.isfinite(et)
    out[fin & (et < PRE_OPEN_ET)] = 1
    out[fin & (et >= PRE_OPEN_ET)] = 2
    return out


CLOCK_LABELS = {0: "et_unknown", 1: "pre_open_et_lt_570", 2: "regular_et_ge_570"}

ET_NOTE = (
    "ET is ABSOLUTE minutes since midnight on the canonical grid: 540 = 09:00, 570 = 09:30, "
    "959 = 15:59 session end. The split is a literal 570-minute threshold on that grid, a "
    "classification of the MODELLED BAR CLOCK -- not a venue session statement, not a broker "
    "extended-hours certification, and not a statement that either kind of order would fill"
)


def clock_band_table(codes, net, attributed_to: str) -> dict:
    codes = np.asarray(codes, dtype=np.int8)
    net = np.asarray(net, dtype=float)
    n = int(codes.size)
    pos_mass = float(net[net > 0].sum()) if n else 0.0
    net_sum = float(net.sum()) if n else 0.0
    rows = []
    for code, label in CLOCK_LABELS.items():
        sel = codes == code
        cnt = int(sel.sum())
        bn = net[sel]
        rows.append(
            {
                "band": label,
                "count": cnt,
                "share_of_units": ratio(cnt, n),
                "net_sum": f(bn.sum()) if cnt else None,
                "net_mean_per_unit": f(bn.mean()) if cnt else None,
                "share_of_positive_mass": ratio(float(bn[bn > 0].sum()), pos_mass)
                if cnt and pos_mass
                else None,
                "share_of_net": ratio(float(bn.sum()), net_sum) if cnt and net_sum else None,
            }
        )
    return {
        "et_units": ET_NOTE,
        "money_attributed_to": attributed_to,
        "units": n,
        "bands": rows,
        "partition_identity": {
            "sum_of_band_counts": int(sum(r["count"] for r in rows)),
            "equals_units": bool(sum(r["count"] for r in rows) == n),
            "abs_residual_net": f(abs(sum(r["net_sum"] or 0.0 for r in rows) - net_sum)),
        },
        "is_not": "a venue session claim or a broker extended-hours certificate",
    }


# ------------------------------------------------------------------------ account ladder
def ladder_block(codes_by_size: dict, n_units: int) -> dict:
    """PARTICIPATION rescaling only.  No profit, no fill, no fee, no capacity figure.

    Under the unchanged engine an order is a fixed fraction of ledger cash, so order
    quantity is homogeneous of degree one in account size and participation scales exactly
    linearly.  This block reports, per account size, only how many units fall in each band
    -- and never a re-priced dollar profit.
    """
    out = {
        "what_this_is": (
            "a DIAGNOSTIC of order SIZE under smaller account sizes. Order quantity is a "
            "fixed fraction of ledger cash under the unchanged engine, so participation at "
            "account size A equals participation at 10,000 USD times A / 10,000, exactly."
        ),
        "what_this_is_not": (
            "it is NOT a smaller-size profit, NOT a fill estimate, NOT a slippage or fee "
            "estimate and NOT a capacity certificate. No re-priced P&L appears here, at any "
            "account size, in any band."
        ),
        "why": (
            "the ledger is scale free in cash units: ret, gross_pnl and fees are fractions "
            "of the book's own original portfolio capital and are exactly invariant to the "
            "dollar size chosen. Only the SIZE measured against traded volume moves."
        ),
        "published_notional_usd": PUBLISHED_NOTIONAL,
        "units_in_scope": int(n_units),
        "ladder": [],
    }
    for size in ACCOUNT_LADDER:
        codes = np.asarray(codes_by_size[size], dtype=np.int8)
        counts = {label: int((codes == i).sum()) for i, label in enumerate(BAND_LABELS)}
        out["ladder"].append(
            {
                "account_size_usd": f(size),
                "participation_scale_factor": f(size / PUBLISHED_NOTIONAL),
                "band_counts": counts,
                "units_over_full_bar_volume": counts[BAND_LABELS[BAND_OVER_FULL_BAR]],
                "units_at_or_under_full_bar_volume": sum(
                    counts[BAND_LABELS[i]] for i in BAND_MEASURED
                ),
                "units_missing_volume": counts[BAND_LABELS[BAND_MISSING]],
                "share_of_units_over_full_bar_volume": ratio(
                    counts[BAND_LABELS[BAND_OVER_FULL_BAR]], int(codes.size)
                ),
                "band_count_identity": {
                    "sum_of_band_counts": int(sum(counts.values())),
                    "equals_units": bool(sum(counts.values()) == int(codes.size)),
                },
                "money_restated_at_this_size": None,
                "money_restated_why": (
                    "deliberately null: the realized money is already a fraction of the "
                    "book's own original portfolio capital, so multiplying it by a smaller "
                    "account size would fabricate a priced smaller-account profit"
                ),
            }
        )
    return out


# ------------------------------------------------------------------- per-cell analysis
def analyse_cell(
    key: dict,
    d: pl.DataFrame,
    fills_cell: pl.DataFrame,
    members_cell: pl.DataFrame,
    pairs_cell: pl.DataFrame,
    srow: dict | None,
) -> dict:
    """All realized money of ONE alternative book, classified by ALREADY PUBLISHED size.

    Money cohorts run on the KNOWN book set ONLY: a whole UNKNOWN book -- its pairs, legs,
    members, gross, fees and net together -- leaves every cohort below.  The UNKNOWN mass is
    counted and its ledger amounts reported, never imputed and never dropped silently.
    """
    day = d["day"].cast(pl.Utf8).to_numpy()
    ret = d["ret"].cast(pl.Float64).fill_null(float("nan")).to_numpy()
    known = np.isfinite(ret)
    known_days = [str(x) for x in day[known]]
    unknown_days = sorted({str(x) for x in day[~known]})
    net_known = float(ret[known].sum())
    d_known = d.filter(pl.col("ret").is_not_null())
    gross_known = float(d_known["gross_pnl"].cast(pl.Float64).sum() or 0.0)
    fees_known = float(d_known["fees"].cast(pl.Float64).sum() or 0.0)
    book_gross_all = float(d["gross_pnl"].cast(pl.Float64).sum() or 0.0)
    book_fees_all = float(d["fees"].cast(pl.Float64).sum() or 0.0)
    n_known, n_books = int(known.sum()), int(d.height)

    # ---- whole-UNKNOWN-BOOK exclusion ledger -----------------------------------------
    m_unknown = (
        members_cell.filter(pl.col("day").is_in(unknown_days))
        if members_cell.height
        else members_cell
    )
    aff = int(d["affordability_unknown"].sum() or 0)
    unres = d.explode("unresolved_fresh_tickers", empty_as_null=False).filter(
        pl.col("unresolved_fresh_tickers").is_not_null()
    )
    unknown_ledger = {
        "books_excluded_whole": int(d.height - n_known),
        "member_rows_excluded": int(m_unknown.height),
        "member_rows_with_known_net_on_excluded_books": (
            int(m_unknown["net_pnl"].is_not_null().sum()) if m_unknown.height else 0
        ),
        "member_net_on_excluded_books": None,
        "member_net_on_excluded_books_note": (
            "an excluded book's own member net is not a KNOWN quantity: it is null in the "
            "member frame and is reported as null, never as a zero and never as an imputed "
            "value. The gross and fee amounts the ledger DID record are published beside it."
        ),
        "member_gross_sum_excluded": f(m_unknown["gross_pnl"].sum()) if m_unknown.height else None,
        "member_fees_sum_excluded": f(m_unknown["fees"].sum()) if m_unknown.height else None,
        "book_gross_sum_excluded": f(book_gross_all - gross_known),
        "book_fees_sum_excluded": f(book_fees_all - fees_known),
        "affordability_unknown_ticker_count": aff,
        "books_flagged_unresolved_fresh": int(d["unresolved_fresh"].sum() or 0),
        "named_unresolved_fresh_ticker_days": int(unres.height),
        "named_unresolved_fresh_tickers_top": (
            {
                str(r["unresolved_fresh_tickers"]): int(r["len"])
                for r in unres.group_by("unresolved_fresh_tickers")
                .len()
                .sort("len", descending=True)
                .head(12)
                .to_dicts()
            }
            if unres.height
            else {}
        ),
        "rule": (
            "a book is UNKNOWN as a whole: its pairs, legs, members, gross, fees and net all "
            "leave the money cohorts together. Keeping its otherwise-known siblings would "
            "attribute money to a book whose own total is unresolved."
        ),
    }

    # ---- member / day / pair identities on the SAME known book set --------------------
    m_known = (
        members_cell.filter(pl.col("day").is_in(known_days))
        if members_cell.height
        else members_cell
    )
    mem_rows = int(m_known.height)
    mem_invalid = int((~m_known["net_pnl"].is_finite()).fill_null(True).sum()) if mem_rows else 0
    mem_net = float(m_known["net_pnl"].sum() or 0.0)
    mem_gross = float(m_known["gross_pnl"].sum() or 0.0)
    mem_fees = float(m_known["fees"].sum() or 0.0)

    pairs = pairs_cell.filter(pl.col("day").is_in(known_days)) if pairs_cell.height else pairs_cell
    closed = pairs.filter(pl.col("exit_et").is_not_null()) if pairs.height else pairs
    pair_net = float(closed["net_account"].sum() or 0.0) if closed.height else 0.0
    open_pairs = int(pairs["exit_et"].is_null().sum() or 0) if pairs.height else 0
    # ``roster_claims`` is a PER-BOOK count, so its denominator here is the SAME known book
    # set the member rows come from. Summing it over ALL books would compare known-only
    # members against a denominator that includes the UNKNOWN books' claims, and would report
    # a shortfall that is an exclusion artifact rather than a broken identity.
    roster = int(d_known["roster_claims"].sum() or 0)
    roster_all = int(d["roster_claims"].sum() or 0)
    cash_resid = gross_known - fees_known - net_known
    # Per-book, so a contradictory book cannot hide inside a cancelling aggregate.
    per_book = d_known.select(["day", "ret", "gross_pnl", "fees", "roster_claims"]).join(
        m_known.group_by("day").agg(
            pl.len().alias("member_rows"),
            pl.col("ticker").n_unique().alias("member_tickers"),
            pl.col("net_pnl").sum().alias("member_net"),
        ),
        on="day",
        how="left",
    )
    per_book = per_book.join(
        closed.group_by("day").agg(pl.col("net_account").sum().alias("pair_net"))
        if closed.height
        else pl.DataFrame(
            {
                "day": pl.Series([], dtype=pl.Utf8),
                "pair_net": pl.Series([], dtype=pl.Float64),
            }
        ),
        on="day",
        how="left",
    ).with_columns(
        pl.col("member_rows").fill_null(0),
        pl.col("member_tickers").fill_null(0),
        pl.col("member_net").fill_null(0.0),
    )
    pb_cash = (per_book["gross_pnl"] - per_book["fees"] - per_book["ret"]).abs().max() or 0.0
    pb_member = (per_book["member_net"] - per_book["ret"]).abs().max() or 0.0
    pb_pair = (
        (per_book["pair_net"].fill_null(0.0) - per_book["ret"]).abs().max() or 0.0
        if closed.height
        else 0.0
    )
    pb_rows_bad = int((per_book["member_rows"] != per_book["roster_claims"]).sum() or 0)
    pb_tickers_bad = int((per_book["member_tickers"] != per_book["roster_claims"]).sum())
    identities = {
        "identity": (
            "gross_pnl - fees = ret per book; member net and closed-pair net each equal that "
            "book's OWN ret on the KNOWN book set"
        ),
        "known_books": n_known,
        "net_known": f(net_known),
        "gross_known": f(gross_known),
        "fees_known": f(fees_known),
        "cash_ledger_residual_gross_minus_fees_minus_net": f(cash_resid),
        "member_rows_known_books": mem_rows,
        "roster_claims_known_books": roster,
        "roster_claims_all_books": roster_all,
        "roster_denominator_note": (
            "roster_claims is a PER-BOOK count, so it is summed over the SAME known books the "
            "member rows and money cohorts come from. Its all-books value is published beside "
            "it; the excluded books' claims are counted in unknown_book_ledger instead of "
            "being silently compared against known-only members."
        ),
        "member_rows_equal_roster_claims": bool(mem_rows == roster),
        "member_rows_minus_roster_claims": mem_rows - roster,
        "per_book_max_abs_residuals": {
            "books_compared": int(per_book.height),
            "cash_ledger_gross_minus_fees_minus_net": f(pb_cash),
            "member_net_vs_book_net": f(pb_member),
            "closed_pair_net_vs_book_net": f(pb_pair),
            "books_whose_member_row_count_differs_from_its_roster": pb_rows_bad,
            "books_whose_unique_member_ticker_count_differs_from_its_roster": pb_tickers_bad,
            "rule": (
                "each KNOWN book must carry its complete unique roster with no UNKNOWN member "
                "and no position left open. A book that fails is a verification failure, "
                "never an exclusion that could hide contradictory support."
            ),
        },
        "member_net_known_books": f(mem_net),
        "member_net_vs_book_net_residual": f(mem_net - net_known),
        "member_gross_vs_book_gross_residual": f(mem_gross - gross_known),
        "member_fees_vs_book_fees_residual": f(mem_fees - fees_known),
        "members_with_nonfinite_net_on_known_books": mem_invalid,
        "closed_pairs_known_books": int(closed.height),
        "closed_pair_net_known_books": f(pair_net),
        "closed_pair_net_vs_book_net_residual": f(pair_net - net_known),
        "open_pairs_on_known_books": open_pairs,
        "open_pairs_note": (
            "an unpaired buy held at the terminal boundary has no exit price inside the "
            "corpus; its net is UNKNOWN and it is excluded from the closed-pair cohorts. Its "
            "legs still appear in the leg tables below, so no leg disappears silently."
        ),
        "residual_rule": (
            "member net and closed-pair net are compared ONLY on books whose own ret is "
            "known. A residual is a verification failure, never a silent reallocation: "
            "UNKNOWN mass stays out of both sides of the identity."
        ),
        "tolerance": TOL,
    }

    # An unpaired buy has no exit price inside the corpus, so its net is UNKNOWN: it is
    # counted below and stays out of every closed-pair band. These defaults keep the
    # downstream concentration reads well defined for a cell that executed nothing.
    p_codes = np.zeros(0, dtype=np.int8)
    p_net = np.zeros(0)
    # ---- legs and pairs as numpy ------------------------------------------------------
    legs = fills_cell.filter(pl.col("day").is_in(known_days)) if fills_cell.height else fills_cell
    if legs.height:
        leg_part = legs["participation"].cast(pl.Float64).fill_null(float("nan")).to_numpy()
        leg_sides = legs["side"].cast(pl.Utf8).to_numpy()
        leg_dec_et = legs["decision_et"].cast(pl.Float64).fill_null(float("nan")).to_numpy()
        leg_exec_et = legs["exec_et"].cast(pl.Float64).fill_null(float("nan")).to_numpy()
        leg_gross = legs["gross_fraction"].cast(pl.Float64).fill_null(0.0).to_numpy()
        leg_fees = legs["fee_fraction"].cast(pl.Float64).fill_null(0.0).to_numpy()
    else:
        leg_part = np.zeros(0)
        leg_sides = np.zeros(0, dtype=str)
        leg_dec_et = np.zeros(0)
        leg_exec_et = np.zeros(0)
        leg_gross = np.zeros(0)
        leg_fees = np.zeros(0)

    if pairs.height:
        p_codes = band_codes(
            pairs["cycle_participation"].cast(pl.Float64).fill_null(float("nan")).to_numpy()
        )
        p_net = pairs["net_account"].cast(pl.Float64).fill_null(float("nan")).to_numpy()
        p_gross = (
            pairs["exit_gross"].cast(pl.Float64).to_numpy()
            - pairs["entry_gross"].cast(pl.Float64).to_numpy()
        )
        p_fees = (
            pairs["exit_fee"].cast(pl.Float64).to_numpy()
            + pairs["entry_fee"].cast(pl.Float64).to_numpy()
        )
        p_kind = pairs["cycle_kind"].cast(pl.Utf8).to_numpy()
        p_entry_part = (
            pairs["entry_participation"].cast(pl.Float64).fill_null(float("nan")).to_numpy()
        )
        p_exit_part = (
            pairs["exit_participation"].cast(pl.Float64).fill_null(float("nan")).to_numpy()
        )
        p_entry_et = pairs["entry_et"].cast(pl.Float64).to_numpy()
        p_exit_et = pairs["exit_et"].cast(pl.Float64).to_numpy()

        pair_bands = band_table(
            p_codes,
            p_net,
            p_gross,
            p_fees,
            "each position is classified by the MAXIMUM participation over its OWN cycle: "
            "the larger of its entry-leg and exit-leg participation, which is the size that "
            "round trip actually had to absorb. Money is closed-pair net, closed-pair gross "
            "(exit gross minus entry gross) and closed-pair fees, in this book's own "
            "original portfolio capital fractions",
        )
        pair_bands["by_entry_leg_band"] = band_table(
            band_codes(p_entry_part),
            p_net,
            p_gross,
            p_fees,
            "the SAME closed-pair money reclassified by the ENTRY leg's own participation, so "
            "entry-side size dependence is separable from the cycle maximum",
        )
        pair_bands["by_exit_leg_band"] = band_table(
            band_codes(p_exit_part),
            p_net,
            p_gross,
            p_fees,
            "the SAME closed-pair money reclassified by the EXIT leg's own participation",
        )
        pair_bands["by_cycle_kind"] = {
            kind: {
                "pairs": int((p_kind == kind).sum()),
                "net_sum": f(p_net[p_kind == kind].sum()),
                "gross_sum": f(p_gross[p_kind == kind].sum()),
                "fees_sum": f(p_fees[p_kind == kind].sum()),
                "n_over_full_bar_volume_cycle_max": int(
                    ((p_kind == kind) & (p_codes == BAND_OVER_FULL_BAR)).sum()
                ),
                "meaning": (
                    "a first executed fresh buy of a (book, ticker)"
                    if kind == "entry"
                    else (
                        "every later executed fresh buy of the same claim, allowed only under "
                        "the repeat cycle control"
                        if kind == "reentry"
                        else "held at the terminal boundary: no exit inside the corpus, net "
                        "UNKNOWN, no realized money in any band"
                    )
                ),
            }
            for kind in ("entry", "reentry", "open_at_close")
        }
        pair_bands["pair_count_identity"] = {
            "pairs_total": int(pairs.height),
            "closed_pairs": int(np.isfinite(p_net).sum()),
            "open_at_close": int((~np.isfinite(p_net)).sum()),
        }
        pair_et = clock_band_table(
            clock_codes(p_entry_et),
            p_net,
            "the ENTRY leg's clock band: the pair's realized net is counted once, under the "
            "band of the leg that put the size on",
        )
        pair_et["by_exit_leg_clock_band"] = clock_band_table(
            clock_codes(p_exit_et),
            p_net,
            "the EXIT leg's clock band, over the same pairs and the same realized net",
        )
    else:
        # Same shape as a populated table, with explicit zeros, so a consumer never has to
        # branch on whether a cell happened to trade. The bands are still listed in full.
        empty_reason = (
            "this cell executed no position on a KNOWN book inside the requested dates, so it "
            "has no closed pair to classify. Every band is listed with a zero count and a null "
            "money sum: that is an empty population, not a suppressed one"
        )
        pair_bands = band_table(
            np.zeros(0, dtype=np.int8),
            np.zeros(0),
            np.zeros(0),
            np.zeros(0),
            empty_reason,
        )
        pair_bands["by_cycle_kind"] = {
            kind: {
                "pairs": 0,
                "net_sum": None,
                "gross_sum": None,
                "fees_sum": None,
                "n_over_full_bar_volume_cycle_max": 0,
            }
            for kind in ("entry", "reentry", "open_at_close")
        }
        pair_bands["pair_count_identity"] = {
            "pairs_total": 0,
            "closed_pairs": 0,
            "open_at_close": 0,
        }
        pair_et = clock_band_table(
            np.zeros(0, dtype=np.int8), np.zeros(0), "no position was executed on a known book"
        )
        pair_et["by_exit_leg_clock_band"] = clock_band_table(
            np.zeros(0, dtype=np.int8), np.zeros(0), "no position was executed on a known book"
        )
        for side_name in ("by_entry_leg_band", "by_exit_leg_band"):
            pair_bands[side_name] = band_table(
                np.zeros(0, dtype=np.int8),
                np.zeros(0),
                np.zeros(0),
                np.zeros(0),
                empty_reason,
            )

    # ---- book-date money by the date's MAXIMUM leg participation ----------------------
    # A KNOWN date that placed no order is filed under its OWN no-leg band, not under
    # missing_volume: undeployed capital is a real, dated outcome, not an unknown size.
    # The population is the KNOWN book dates ONLY. An UNKNOWN book leaves whole, so it must
    # not appear here at all: masking its money to a zero while leaving its row in would
    # file an unresolved cashflow under the no-leg band and understate the date count.
    day_codes = np.zeros(int(known.sum()), dtype=np.int8)
    max_by_day: dict[str, float] = {}
    executed_days = set()
    missing_volume_days = set()
    if legs.height:
        for dy, pv in zip(legs["day"].cast(pl.Utf8).to_numpy(), leg_part, strict=True):
            executed_days.add(str(dy))
            if not np.isfinite(pv) or pv < 0:
                missing_volume_days.add(str(dy))
                continue
            cur = max_by_day.get(dy)
            max_by_day[dy] = pv if cur is None else max(cur, pv)
    for i, dy in enumerate(day[known]):
        if str(dy) not in executed_days:
            day_codes[i] = DATE_NO_LEG
        elif str(dy) in missing_volume_days:
            day_codes[i] = DATE_OFFSET + BAND_MISSING
        else:
            day_codes[i] = band_codes(np.array([max_by_day[str(dy)]]))[0] + DATE_OFFSET
    date_bands = band_table(
        day_codes,
        ret[known],
        d["gross_pnl"].cast(pl.Float64).fill_null(0.0).to_numpy()[known],
        d["fees"].cast(pl.Float64).fill_null(0.0).to_numpy()[known],
        "each KNOWN book date is classified by the MAXIMUM participation over every leg that "
        "date executed, and carries that date's own net, gross and fees. A KNOWN date that "
        "placed NO order sits in its own no_leg_executed band -- capital stayed undeployed -- "
        "which is a real dated outcome, not an unknown size and not a missing bar volume. "
        "UNKNOWN dates are not in this table at all: they left whole, with counts above",
        BAND_LAYOUT_DATE,
    )
    date_bands["known_book_dates"] = n_known
    date_bands["excluded_unknown_book_dates"] = int(d.height - n_known)

    # ---- leg money / size tables -------------------------------------------------------
    leg_codes = band_codes(leg_part)
    signed_gross = leg_gross * np.where(leg_sides == "buy", -1.0, 1.0)
    leg_rows = [
        {
            "band": label,
            "count": int((leg_codes == i).sum()),
            "buy_legs": int(((leg_codes == i) & (leg_sides == "buy")).sum()),
            "sell_legs": int(((leg_codes == i) & (leg_sides == "sell")).sum()),
            "signed_gross_sum": f(signed_gross[leg_codes == i].sum()),
            "fees_sum": f(leg_fees[leg_codes == i].sum()),
        }
        for i, label in enumerate(BAND_LABELS)
    ]
    leg_table = {
        "population": (
            "legs executed on KNOWN books. A leg carries no net of its own -- money realizes "
            "at the pair -- so its columns are the signed gross flow (buys negative, sells "
            "positive) and the fee actually charged."
        ),
        "legs": int(leg_codes.size),
        "legs_with_participation": int(np.isfinite(leg_part).sum()),
        "legs_missing_volume": int((~np.isfinite(leg_part)).sum()),
        "participation": dist(leg_part, "fraction of that minute's traded volume"),
        "bands": leg_rows,
        "band_count_identity": {
            "sum_of_band_counts": int(sum(r["count"] for r in leg_rows)),
            "equals_legs": bool(sum(r["count"] for r in leg_rows) == int(leg_codes.size)),
        },
        "by_side": {
            side: {
                "legs": int((leg_sides == side).sum()),
                "participation": dist(leg_part[leg_sides == side], "fraction of bar volume"),
                "decision_et": dist(leg_dec_et[leg_sides == side], "absolute ET minutes"),
                "exec_et": dist(leg_exec_et[leg_sides == side], "absolute ET minutes"),
                "exec_et_absolute_range": [
                    f(leg_exec_et[leg_sides == side].min()),
                    f(leg_exec_et[leg_sides == side].max()),
                ]
                if int((leg_sides == side).sum())
                else None,
            }
            for side in ("buy", "sell")
        },
        "et_absolute": {
            "unit": "ABSOLUTE minutes since midnight on the canonical grid (540 = 09:00)",
            "note": ET_NOTE,
            "decision_et": dist(leg_dec_et, "absolute ET minutes"),
            "exec_et": dist(leg_exec_et, "absolute ET minutes"),
            "pre_open_threshold_et": PRE_OPEN_ET,
            "legs_decided_pre_open": int((leg_dec_et < PRE_OPEN_ET).sum()),
            "legs_executed_pre_open": int((leg_exec_et < PRE_OPEN_ET).sum()),
            "legs_executed_regular": int((leg_exec_et >= PRE_OPEN_ET).sum()),
            "legs_exec_et_unknown": int((~np.isfinite(leg_exec_et)).sum()),
            "unknown_preserved": (
                "an ET that is not finite is counted in legs_exec_et_unknown and stays "
                "UNKNOWN; it is never rounded into a clock band or a session"
            ),
        },
    }

    # ---- the account ladder: participation only ---------------------------------------
    ladder_codes = {
        size: band_codes(leg_part * (size / PUBLISHED_NOTIONAL)) for size in ACCOUNT_LADDER
    }
    ladder = ladder_block(ladder_codes, int(leg_codes.size))
    ladder["scope"] = (
        "computed over this cell's legs on its KNOWN book set. It reports band COUNTS per "
        "account size and a NULL money field by construction."
    )

    # ---- concentration ----------------------------------------------------------------
    trade_conc = {
        "closed_pairs_net": top_k_share(p_net) if pairs.height else {"n": 0},
        "closed_pairs_net_at_or_under_full_bar_volume": top_k_share(
            p_net[np.isin(p_codes, BAND_MEASURED)]
        )
        if pairs.height
        else {"n": 0},
        "closed_pairs_net_over_full_bar_volume": top_k_share(p_net[p_codes == BAND_OVER_FULL_BAR])
        if pairs.height
        else {"n": 0},
        "closed_pairs_net_missing_volume": top_k_share(p_net[p_codes == BAND_MISSING])
        if pairs.height
        else {"n": 0},
        "known_book_dates_net": top_k_share(ret[known]),
        "confidence": "NONE",
        "confidence_meaning": (
            "no confidence interval, probability, significance test or p-value is published "
            "anywhere in this artifact; these counts describe ALREADY REALIZED money"
        ),
        "over_under_note": (
            "the over-full-bar, at-or-under and missing-volume subsets partition the closed "
            "pairs, so their counts and sums add back to the full cell. Their top-k shares are "
            "per-subset order statistics over correlated alternatives, not separate "
            "experiments, and none of them excludes a trade from the cell above."
        ),
        "no_kept_mean_is_a_strategy_ev": (
            "mean_excluding_top{k} is published ONLY as a concentration reading of the cell's "
            "own published mean. It is not named a strategy EV, is not an out-of-sample "
            "figure, and nothing was removed from the cell."
        ),
    }

    producer_cross = None
    if srow:
        pub_ev = f(srow.get("ev"))
        mine = f(net_known / n_known) if n_known else None
        producer_cross = {
            "published_ev": pub_ev,
            "reader_known_book_date_mean": mine,
            "abs_diff": f(abs(pub_ev - mine))
            if (pub_ev is not None and mine is not None)
            else None,
            "published_known_days": int(srow.get("known_days") or 0),
            "published_days": int(srow.get("days") or 0),
            "book_set_note": (
                "summary.ev is a mean over the SAME known book set this reader uses; "
                "summary.orders is an ALL-book mean and is never compared against a known-set "
                "figure here"
            ),
        }

    return {
        "cell_id": cell_id(key),
        "cell_key": list(CELL_KEYS),
        "clock": int(key["clock"]),
        "n": int(key["n"]),
        "side_cost": f(key["side_cost"]),
        "per_leg_cost_bps": int(round(float(key["side_cost"]) * 10000.0)),
        "round_trip_cost_bps": bps_of(key["side_cost"]),
        "policy": key["policy"],
        "cycles": key["cycles"],
        "days_in_cell": n_books,
        "known_book_dates": n_known,
        "unknown_book_dates": int(d.height - n_known),
        "unknown_book_date_list": {
            "count": int(d.height - n_known),
            "published": int(min(len(unknown_days), PER_CELL_UNKNOWN_DAY_LIST_CAP)),
            "complete": bool(len(unknown_days) <= PER_CELL_UNKNOWN_DAY_LIST_CAP),
            "dates": unknown_days[:PER_CELL_UNKNOWN_DAY_LIST_CAP],
            "cap": PER_CELL_UNKNOWN_DAY_LIST_CAP,
            "exact_corpus_list": "corpus.unknown_book_dates_published_exactly",
            "why_capped": (
                "a per-cell date list is capped so the artifact stays readable; the COUNT is "
                "exact for every cell and the EXACT corpus-wide list is published once under "
                "corpus.unknown_book_dates_published_exactly"
            ),
        },
        "known_set_money": {
            "unit": "fraction of this book's own ORIGINAL PORTFOLIO CAPITAL",
            "known_books": n_known,
            "net_sum": f(net_known),
            "net_mean_per_known_book_date": mine_of(net_known, n_known),
            "gross_sum": f(gross_known),
            "fees_sum": f(fees_known),
            "cash_ledger_residual": f(cash_resid),
            "x_published_notional_usd": PUBLISHED_NOTIONAL,
            "net_sum_x_published_notional": f(net_known * PUBLISHED_NOTIONAL),
            "gross_sum_x_published_notional": f(gross_known * PUBLISHED_NOTIONAL),
            "fees_sum_x_published_notional": f(fees_known * PUBLISHED_NOTIONAL),
            "dollar_restatement_means": (
                "the SAME realized money scaled once by the account size the executed corpus "
                "already assumes for its participation definition. Arithmetic on realized "
                "fractions: not a P&L forecast, not a claim the size is funded, available or "
                "executable, and not a capacity certificate. No other money in this artifact "
                "is converted to dollars at any size."
            ),
        },
        "unknown_book_ledger": unknown_ledger,
        "identities": identities,
        "closed_pair_money_by_size": pair_bands,
        "book_date_money_by_size": date_bands,
        "leg_money_and_size": leg_table,
        "et_clock_dependence": pair_et,
        "account_size_ladder": ladder,
        "trade_and_date_concentration": trade_conc,
        "producer_summary_crosscheck": producer_cross,
        "every_ruler_retained": (
            "the bands, the full-bar-volume split and the clock bands are CLASSIFICATIONS OF "
            "ALREADY REALIZED MONEY. No band excludes a trade, no kept mean is named a "
            "strategy EV, and no out-of-sample or capacity claim is made from any of them."
        ),
    }


def mine_of(total: float, n: int):
    return f(total / n) if n else None


# ---------------------------------------------------------------------------------- main
def attach_participation(pairs: pl.DataFrame, fills: pl.DataFrame) -> pl.DataFrame:
    """Carry each side's own participation onto its pair, then the cycle maximum.

    The pair key is ``(book keys, ticker, cycle_index)`` -- the SAME key
    ``owned_claim_cash_execution.build_pairs`` joins on -- with a POSITIVE cycle index on
    BOTH sides: it is the running count of buys for that (book, ticker), and a sell never
    precedes its own buy, so the k-th sell also sits at count k.

    ``build_pairs`` derives that index on its own working copy and does not write it back to
    ``fills``, so it is recomputed here under the identical rule: sort the legs by the pair
    keys, then ``exec_et``, then ``side``, and take the running count of buys within each
    key. Recomputing it rather than importing a private helper keeps the two indices
    identical by construction instead of by coincidence.
    """
    if pairs.height == 0:
        return pairs.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("entry_participation"),
            pl.lit(None, dtype=pl.Float64).alias("exit_participation"),
            pl.lit(None, dtype=pl.Float64).alias("cycle_participation"),
        )
    indexed = fills.sort([*PAIR_KEYS, "exec_et", "side"]).with_columns(
        pl.col("side").eq("buy").cast(pl.Int64).cum_sum().over(list(PAIR_KEYS)).alias("cycle_index")
    )
    side = (
        indexed.group_by([*PAIR_KEYS, "cycle_index", "side"])
        .agg(pl.col("participation").max().alias("participation"))
        .pivot(on="side", index=[*PAIR_KEYS, "cycle_index"], values="participation")
    )
    side = side.rename(
        {
            name: mapped
            for name, mapped in (("buy", "entry_participation"), ("sell", "exit_participation"))
            if name in side.columns
        }
    )
    side = side.with_columns(
        *[
            pl.lit(None, dtype=pl.Float64).alias(name)
            for name in ("entry_participation", "exit_participation")
            if name not in side.columns
        ]
    )
    both_known = (
        pl.col("entry_participation").is_finite()
        & (pl.col("entry_participation") >= 0)
        & pl.col("exit_participation").is_finite()
        & (pl.col("exit_participation") >= 0)
    )
    both = (
        pl.when(both_known)
        .then(pl.max_horizontal("entry_participation", "exit_participation"))
        .otherwise(None)
    )
    return pairs.join(side, on=[*PAIR_KEYS, "cycle_index"], how="left").with_columns(
        both.alias("cycle_participation")
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "read how much ALREADY REALIZED cash-first money was produced by legs sized as a "
            "large fraction of the whole executed bar's traded volume (read-only diagnostic)"
        )
    )
    ap.add_argument(
        "--data-root", default=None, help="default: the data_root the evidence published"
    )
    ap.add_argument(
        "--replay-root", default=None, help="directory holding the replay parquet outputs"
    )
    ap.add_argument(
        "--evidence", required=True, help="the executed replay artifact (declares the grid)"
    )
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit-days", type=int, default=0, help="tiny NON-EVIDENCE read over N days")
    a = ap.parse_args(argv)

    ev_path = Path(a.evidence)
    if not ev_path.is_file():
        raise SystemExit(f"executed replay artifact missing: {ev_path}")
    up = json.loads(ev_path.read_text())

    data_root = ls.bps.resolve_data_root(a.data_root or up.get("data_root"))
    v2 = ls.v2_dir(data_root)
    if a.replay_root:
        root = Path(a.replay_root)
        root_origin = "explicit --replay-root"
    elif up.get("replay_root"):
        root = Path(up["replay_root"])
        root_origin = "declared by the executed artifact (replay_root)"
    else:
        root = v2.joinpath(*DEFAULT_REPLAY_SUBDIR)
        root_origin = f"defaulted to <v2>/{'/'.join(DEFAULT_REPLAY_SUBDIR)}"
    if not root.is_dir():
        raise SystemExit(f"replay root not found: {root}")

    evidence = bool(up.get("evidence"))
    notes: list[str] = []
    if not evidence:
        notes.append(
            "upstream replay artifact declares itself NON-EVIDENCE: "
            + "; ".join(up.get("non_evidence_notes") or ["unspecified"])
        )
    if a.limit_days:
        evidence = False
        notes.append(
            f"NON-EVIDENCE: bounded read over the first {int(a.limit_days)} published dates"
        )

    # ---- declared grid, DYNAMIC in the number of cost riders --------------------------
    for field in ("clocks", "ns", "sides", "policies", "cycles_run", "test_days"):
        if not up.get(field):
            raise SystemExit(f"the executed artifact declares no {field}; the grid cannot be read")
    declared_sides = [float(s) for s in up["sides"]]
    riders = sorted({bps_of(s) for s in declared_sides})
    cash0_cycles = str((up.get("requested_controls") or {}).get("cash0_reference_cycles", "once"))

    frame_sha = {}
    for name in FRAMES:
        p = root / f"{name}.parquet"
        if not p.is_file():
            raise SystemExit(f"replay output missing: {p}")
        frame_sha[p.name] = sha256_file(p)

    # ``daily`` and ``members`` spell the modelled per-leg cost ``side``; ``fills`` calls it
    # ``side_cost``.  One name from here on, so a cell key means the same thing everywhere.
    daily = pl.read_parquet(root / "daily.parquet").rename({"side": "side_cost"})
    members = pl.read_parquet(root / "members.parquet").rename({"side": "side_cost"})
    fills = pl.read_parquet(root / "fills.parquet")
    summary = pl.read_parquet(root / "summary.parquet").rename({"side": "side_cost"})

    days_all = list(up["test_days"])
    days = days_all[: int(a.limit_days)] if a.limit_days else list(days_all)
    discovery = ls.discovery_days(data_root)
    if not set(days_all) <= set(discovery):
        raise SystemExit("the artifact names days outside the canonical discovery split")
    if not set(days) <= set(discovery):
        raise SystemExit("refusing a read that is not a subset of the canonical discovery days")

    day_mask = pl.col("day").is_in(days)
    daily = daily.filter(day_mask)
    if daily.height == 0:
        raise SystemExit("no replay book rows for the requested dates")
    fills_in = fills.filter(day_mask)
    members_in = members.filter(day_mask)
    stray = sorted(set(daily["day"].unique().to_list()) - set(days))
    if stray:
        raise SystemExit(f"{len(stray)} replayed date(s) fall outside the requested set; refusing")

    # ---- executed grid vs declared grid ------------------------------------------------
    executed = {tuple(r) for r in daily.select(list(CELL_KEYS)).unique().iter_rows()}
    declared = {
        (c, n, s, p, cy)
        for c in up["clocks"]
        for n in up["ns"]
        for s in declared_sides
        for p in up["policies"]
        for cy in up["cycles_run"]
    } | {
        (c, n, s, CASH0_POLICY, cash0_cycles)
        for c in up["clocks"]
        for n in up["ns"]
        for s in declared_sides
    }
    if executed != declared:
        raise SystemExit(
            "the executed replay cells do not match the artifact's declared grid "
            f"(only_on_disk={sorted(executed - declared)[:4]}, "
            f"only_declared={sorted(declared - executed)[:4]})"
        )
    active_keys = sorted(k for k in executed if k[3] != CASH0_POLICY)
    cash0_keys = sorted(k for k in executed if k[3] == CASH0_POLICY)
    declared_active = (
        len(up["policies"])
        * len(up["cycles_run"])
        * len(up["clocks"])
        * len(up["ns"])
        * len(declared_sides)
    )
    if len(active_keys) != declared_active:
        raise SystemExit(
            f"active cell count {len(active_keys)} != declared grid product {declared_active}"
        )

    # ``n`` is itself a CELL KEY, so the coverage alias must not reuse it.
    per_cell_days = daily.group_by(list(CELL_KEYS)).agg(
        pl.col("day").n_unique().alias("dates_present")
    )
    if int(per_cell_days["dates_present"].min()) != len(days):
        raise SystemExit(
            "some cells are missing a published date; a cell/day grid gap is never a known "
            f"cash 0 ({per_cell_days.filter(pl.col('dates_present') != len(days)).height} "
            "cells short)"
        )
    flag_disagreements = int((daily["ret"].is_null() != daily["unknown"]).sum() or 0)
    if flag_disagreements:
        raise SystemExit(
            "daily.parquet's unknown flag disagrees with its own null return on "
            f"{flag_disagreements} rows"
        )

    # ---- pairs: the executed reader's own builder, unchanged --------------------------
    pairs_all = attach_participation(exe.build_pairs(fills_in), fills_in)

    d_cells = partition(daily)
    f_cells = partition(fills_in)
    m_cells = partition(members_in)
    p_cells = partition(pairs_all)
    s_cells = {cell_id(r): r for r in summary.to_dicts()}

    # ---- per-cell read -----------------------------------------------------------------
    cells, worst, failing = [], 0.0, []
    for key in active_keys:
        kd = dict(zip(CELL_KEYS, key, strict=True))
        rec = analyse_cell(
            kd,
            d_cells.get(key, daily.head(0)),
            f_cells.get(key, fills.head(0)),
            m_cells.get(key, members.head(0)),
            p_cells.get(key, pairs_all.head(0)),
            s_cells.get(cell_id(kd)),
        )
        ident = rec["identities"]
        for field in (
            "cash_ledger_residual_gross_minus_fees_minus_net",
            "member_net_vs_book_net_residual",
            "member_gross_vs_book_gross_residual",
            "member_fees_vs_book_fees_residual",
            "closed_pair_net_vs_book_net_residual",
        ):
            if ident.get(field) is not None:
                worst = max(worst, abs(float(ident[field])))
        pbr = ident["per_book_max_abs_residuals"]
        for field in (
            "cash_ledger_gross_minus_fees_minus_net",
            "member_net_vs_book_net",
            "closed_pair_net_vs_book_net",
        ):
            if pbr.get(field) is not None:
                worst = max(worst, abs(float(pbr[field])))
        if pbr["books_whose_member_row_count_differs_from_its_roster"]:
            failing.append(f"{rec['cell_id']}:per_book_member_rows_vs_roster")
        if pbr["books_whose_unique_member_ticker_count_differs_from_its_roster"]:
            failing.append(f"{rec['cell_id']}:per_book_unique_tickers_vs_roster")
        for section in (
            "closed_pair_money_by_size",
            "book_date_money_by_size",
            "leg_money_and_size",
        ):
            blk = rec[section]
            pi = blk.get("partition_identity") or {}
            resid = pi.get("abs_residual_net")
            if resid is not None:
                worst = max(worst, abs(float(resid)))
            if pi.get("equals_units") is False:
                failing.append(f"{rec['cell_id']}:{section}:band_count_partition")
            fb = blk.get("full_bar_volume_split")
            if fb is not None and fb["four_way_partition_identity"]["equals_units"] is False:
                failing.append(f"{rec['cell_id']}:{section}:full_bar_volume_split")
            if fb is not None:
                over_n = fb["units_over_full_bar_volume"]
                over_net = fb["net_from_over_full_bar_volume"]
                band_over_net = next(
                    (
                        r["net_sum"]
                        for r in blk["bands"]
                        if r["band"].endswith("gt_100pct_full_bar_volume")
                    ),
                    None,
                )
                if over_n and abs(float(over_net or 0.0) - float(band_over_net or 0.0)) > TOL:
                    failing.append(f"{rec['cell_id']}:{section}:over_band_net_mismatch")
        li = rec["leg_money_and_size"]["band_count_identity"]
        if li["equals_legs"] is not True:
            failing.append(f"{rec['cell_id']}:leg_band_count_partition")
        for r in rec["account_size_ladder"]["ladder"]:
            if r["band_count_identity"]["equals_units"] is not True:
                failing.append(f"{rec['cell_id']}:ladder_{r['account_size_usd']}")
        if not ident["member_rows_equal_roster_claims"]:
            failing.append(f"{rec['cell_id']}:member_rows_vs_roster")
        if abs(ident["cash_ledger_residual_gross_minus_fees_minus_net"] or 0.0) > TOL:
            failing.append(f"{rec['cell_id']}:cash_ledger")
        if ident["members_with_nonfinite_net_on_known_books"]:
            failing.append(f"{rec['cell_id']}:nonfinite_member_net")
        cells.append(rec)
    cells.sort(key=lambda c: c["cell_id"])

    # ---- journal rule ------------------------------------------------------------------
    orders_total = int(daily["orders"].sum() or 0)
    legs_total = int(fills_in.height)
    if legs_total != orders_total:
        raise SystemExit(
            f"fills journal has {legs_total} rows against {orders_total} executed orders; "
            "refusing to describe a ledger whose legs and books disagree"
        )
    journal = {
        "rule": (
            "a missing or empty fills journal is admissible only where the corresponding "
            "books report an actual zero order count; a nonzero order count with no legs is "
            "a hard failure, never a dropped row"
        ),
        "books": int(daily.height),
        "book_order_count": orders_total,
        "fills_rows": legs_total,
        "orders_residual": legs_total - orders_total,
        "fills_rows_equal_book_orders": True,
    }

    # ---- the cash0 ledger identity, kept strictly separate ------------------------------
    cash0_rows = []
    for key in cash0_keys:
        kd = dict(zip(CELL_KEYS, key, strict=True))
        d = d_cells.get(key, daily.head(0))
        r = d["ret"].drop_nulls()
        cash0_rows.append(
            {
                "cell_id": cell_id(kd),
                "clock": int(kd["clock"]),
                "n": int(kd["n"]),
                "side_cost": f(kd["side_cost"]),
                "round_trip_cost_bps": bps_of(kd["side_cost"]),
                "policy": kd["policy"],
                "cycles": kd["cycles"],
                "book_dates": int(d.height),
                "known_book_dates": int(r.len()),
                "unknown_book_dates": int(d["unknown"].sum() or 0),
                "orders_total": int(d["orders"].sum() or 0),
                "legs": int(f_cells.get(key, fills.head(0)).height),
                "known_sum": f(r.sum()) if r.len() else None,
                "all_returns_exactly_zero": bool(r.len() == int((r == 0.0).sum())),
            }
        )
    cash0_section = {
        "policy_literal": CASH0_POLICY,
        "why_separate": (
            "the cash0 book withholds every forecast, so it executes no orders, places no "
            "size against any bar and is a LEDGER IDENTITY reference rather than a second "
            "experiment. It is excluded from every active-cell count, breadth row and band "
            "table above."
        ),
        "cells": len(cash0_rows),
        "books": int(sum(r["book_dates"] for r in cash0_rows)),
        "books_with_nonzero_return": sum(
            1 for r in cash0_rows if not r["all_returns_exactly_zero"]
        ),
        "cells_with_any_order": int(sum(1 for r in cash0_rows if r["orders_total"] > 0)),
        "identity_reproduced": bool(
            cash0_rows and all(r["all_returns_exactly_zero"] for r in cash0_rows)
        ),
        "declared_identity_in_artifact": up.get("cash0_identity"),
        "per_cell": cash0_rows,
    }

    # ---- corpus-level size picture ------------------------------------------------------
    # A leg rides a book, so a leg on an UNKNOWN book leaves whole with it. The known-book
    # leg set is a semi-join on the KNOWN (cell keys, day) pairs, not a per-member filter.
    known_book_keys = (
        daily.filter(pl.col("ret").is_not_null()).select(list(CELL_KEYS) + ["day"]).unique()
    )
    legs_known_df = fills_in.join(known_book_keys, on=list(CELL_KEYS) + ["day"], how="semi")
    n_legs_known, n_legs_unknown = (
        int(legs_known_df.height),
        int(fills_in.height - legs_known_df.height),
    )
    corpus_part_known = (
        legs_known_df["participation"].cast(pl.Float64).fill_null(float("nan")).to_numpy()
    )
    corpus_codes_known = band_codes(corpus_part_known)
    corpus_part = fills_in["participation"].cast(pl.Float64).fill_null(float("nan")).to_numpy()
    corpus_codes = band_codes(corpus_part)
    corpus_codes_over = corpus_codes == BAND_OVER_FULL_BAR
    corpus_codes_under = (corpus_codes >= 1) & (corpus_codes <= 4)
    corpus = {
        "census_only": (
            "these are COUNTS and SIZE readings over the whole executed corpus. NO money is "
            "summed here: cells are alternative books of the same corpus, so a corpus-level "
            "gross, fee or net would be a portfolio of mutually exclusive books that nobody "
            "traded. Every money figure lives inside ONE cell."
        ),
        "money_summed_across_cells": (
            "NONE: no gross, fee, net or dollar figure is ever added over cells"
        ),
        "books": int(daily.height),
        "known_books": int(daily.filter(pl.col("ret").is_not_null()).height),
        "unknown_books": int(daily["unknown"].sum() or 0),
        "member_rows": int(members_in.height),
        "members_unknown_net": int(members_in["net_pnl"].is_null().sum() or 0),
        "members_open_at_close": int(members_in["remaining_shares"].gt(1e-10).sum() or 0),
        "legs": int(fills_in.height),
        "pairs_total": int(pairs_all.height),
        "closed_pairs": int(pairs_all["exit_et"].is_not_null().sum() or 0)
        if pairs_all.height
        else 0,
        "days": len(days),
        "unknown_book_dates_published_exactly": sorted(
            {str(x) for x in daily.filter(pl.col("ret").is_null())["day"].unique().to_list()}
        ),
        "legs_on_known_books": n_legs_known,
        "legs_on_unknown_books_excluded": n_legs_unknown,
        "leg_participation_all_legs": dist(corpus_part, "fraction of that minute's traded volume"),
        "leg_participation_known_books": dist(
            corpus_part_known, "fraction of that minute's traded volume"
        ),
        "leg_band_counts_all_legs": {
            label: int((corpus_codes == i).sum()) for i, label in enumerate(BAND_LABELS)
        },
        "leg_band_counts_known_books": {
            label: int((corpus_codes_known == i).sum()) for i, label in enumerate(BAND_LABELS)
        },
        "legs_over_full_bar_volume": int(corpus_codes_over.sum()),
        "legs_at_or_under_full_bar_volume": int(corpus_codes_under.sum()),
        "legs_missing_volume": int((corpus_codes == BAND_MISSING).sum()),
        "signed_gross_all_legs": None,
        "fees_all_legs": None,
        "signed_gross_known_books": None,
        "fees_known_books": None,
        "money_fields_deliberately_null_why": (
            "a corpus gross/fee sum would add 288 alternative books of the same dates into "
            "one number. Those books share roster, forecasts, dates and fills, so the sum "
            "describes no executable account. The COUNT and SIZE distributions below are "
            "legitimate corpus readings; the money is published per cell only."
        ),
        "unknown_book_books_are_excluded_whole": True,
    }

    # ---- breadth across cells: a spread, never a winner ---------------------------------
    def extremes(values) -> dict:
        vals = [v for v in values if v is not None]
        if not vals:
            return {"cells_with_a_value": 0}
        a = np.asarray(vals, dtype=float)
        return {
            "cells_with_a_value": int(a.size),
            "min": f(a.min()),
            "median": f(float(np.median(a))),
            "max": f(a.max()),
            "cells_positive": int((a > 0).sum()),
            "cells_zero": int((a == 0).sum()),
            "cells_negative": int((a < 0).sum()),
            "mean_of_cell_values": None,
            "mean_of_cell_values_why": (
                "deliberately null: cells are alternative books, so averaging their values "
                "would describe no single book and no portfolio. The min/median/max spread "
                "shows the variation without inventing a blended figure."
            ),
            "is_not": (
                "a portfolio figure, a weighted combination or a selection; cells are "
                "ALTERNATIVE books over one shared corpus and no mean across cells is ever "
                "published as money"
            ),
        }

    over_share = [
        (c["closed_pair_money_by_size"].get("full_bar_volume_split") or {}).get(
            "share_of_positive_mass_from_over_full_bar_volume"
        )
        for c in cells
    ]
    breadth_block = {
        "known_book_date_net_mean": extremes(
            [c["known_set_money"]["net_mean_per_known_book_date"] for c in cells]
        ),
        "closed_pair_share_of_positive_mass_over_full_bar_volume": extremes(over_share),
        "closed_pairs_over_full_bar_volume": extremes(
            [
                (c["closed_pair_money_by_size"].get("full_bar_volume_split") or {}).get(
                    "units_over_full_bar_volume"
                )
                for c in cells
            ]
        ),
        "confidence": "NONE",
        "is_not_a_winner": (
            "the min/median/max spread is published so the variation is visible. No cell is "
            "named best, promoted or validated, and none of these aggregates is a portfolio."
        ),
    }

    # ---- provenance: declared pins recorded, never forced --------------------------------
    pins = {}
    for label, path, declared in (
        ("producer_script", PRODUCER, up.get("source_sha256")),
        ("accounting_engine", ENGINE, up.get("engine_sha256")),
    ):
        if not path.is_file():
            pins[label] = {"path": str(path), "present": False, "on_disk_sha256": None}
            continue
        got = sha256_file(path)
        entry = {
            "path": str(path),
            "present": True,
            "declared_sha256": declared,
            "on_disk_sha256": got,
            "matches_declared": bool(declared and got == declared),
        }
        if declared and got != declared:
            entry["interpretation"] = (
                "the executed artifact pins the driver that ACTUALLY produced its corpus. A "
                "driver that has since changed on disk does not alter those bytes: this reader "
                "records both digests and refuses neither corpus. The declared digest is the "
                "vintage of record; nothing here re-runs, re-verifies or re-attributes it."
            )
            entry["declared_digest_is_vintage_of_record"] = True
        pins[label] = entry

    artifact = {
        "kind": "OWNED-CLAIM-CASH-FIRST-REALIZED-MONEY-CAPACITY-DIAGNOSTIC-NOT-A-CAPACITY-CERT",
        "status": "full_discovery_evidence" if evidence else "smoke_non_evidence",
        "evidence": evidence,
        "non_evidence_notes": notes,
        "reader": {
            "script": str(SCRIPT),
            "this_script_sha256": sha256_file(SCRIPT),
            "lifecycle_study_sha256": sha256_file(Path(ls.__file__).resolve()),
            "reuses": (
                "owned_claim_cash_execution.build_pairs, imported unchanged; that module's "
                "main() is never invoked and no other helper of it is used"
            ),
            "polars_version": pl.__version__,
            "numpy_version": np.__version__,
            "reads": "the executed artifact and the replay parquet outputs daily/members/fills/"
            "summary it published",
            "does_not": (
                "replay, re-price, re-size, re-hurdle, refit, re-select, impute or buy/sell "
                "anything; no price, quote, volume, roster, score or protected outcome is read"
            ),
            "no_new_source_panel_read": (
                "fills.participation is the only size evidence used, and the executed corpus "
                "already published it. No panel, roster, tape or quote source is opened."
            ),
        },
        "scope": {
            "inputs_root": str(root),
            "inputs_root_origin": root_origin,
            "inputs_root_is_explicit": root_origin == "explicit --replay-root",
            "replay_parquet_sha256": frame_sha,
            "replay_outputs_read_only": "verified, never written",
            "evidence_artifact": {
                "path": str(ev_path),
                "sha256": sha256_file(ev_path),
                "kind": up.get("kind"),
                "declared_evidence": up.get("evidence"),
            },
            "data_root": str(data_root),
            "published_test_dates": len(days_all),
            "dates_read": len(days),
            "dates_are_canonical_discovery_days": True,
            "protected_half_read": False,
            "active_cells": len(cells),
            "active_cell_grid_product": {
                "policies": len(up["policies"]),
                "cycles": len(up["cycles_run"]),
                "clocks": len(up["clocks"]),
                "ns": len(up["ns"]),
                "cost_sides": len(declared_sides),
                "product": declared_active,
            },
            "cash0_control_cells": len(cash0_keys),
            "cash0_is_excluded_from_active_cells": True,
        },
        "declared_grid": {
            "source": (
                "read from the executed artifact; the number of cost riders is DYNAMIC "
                "(2-cost or 4-cost), never assumed in code"
            ),
            "clocks": up["clocks"],
            "ns": up["ns"],
            "policies": up["policies"],
            "cycles_run": up["cycles_run"],
            "sides": declared_sides,
            "round_trip_cost_bps": riders,
            "per_leg_cost_bps": [int(round(s * 10000.0)) for s in declared_sides],
            "fee_contract_declared": up.get("fee_contract"),
            "cost_semantics": (
                "each side_cost is a PER-LEG modelled cost charged on that leg's own actual "
                "fill price; the round-trip figure is 2 x per_leg, so total round-trip "
                "25/50/100/150bps appears as per-leg 0.00125/0.0025/0.005/0.0075"
            ),
            "executed_cells_match_declared_grid": True,
            "grid_is_not_reexecuted": "the driver is never invoked by this reader",
        },
        "units_and_normalization": {
            "money_unit": (
                "every money-like field is a FRACTION OF THAT BOOK'S OWN ORIGINAL PORTFOLIO "
                "CAPITAL: the replay ledger starts every book at cash = 1.0 and each original "
                "claim owns its own 1/n slice of it. daily.ret = settled cash - 1.0, "
                "daily.gross_pnl = signed gross flows, daily.fees = fees actually charged, and "
                "members/fills/pairs carry the same unit."
            ),
            "not_the_forecast_unit": (
                "the frozen OOF forecast that DECIDED the trades is denominated in incremental "
                "inherited dollars per ORIGINAL CLAIM CASH dollar, with its own gross "
                "conversion and round-trip hurdle. That upstream ACTION-INPUT unit is kept "
                "verbatim and separate and is never converted into a figure here."
            ),
            "participation_unit": (
                "fraction of the executed bar's traded volume: order size over the volume of "
                "the canonical bar row whose OPEN is the fill price, at the engine's PUBLISHED "
                f"notional of {PUBLISHED_NOTIONAL:.0f} USD"
            ),
            "no_forecast_unit_mixing": True,
        },
        "dollar_restatement_at_published_notional": {
            "what": (
                "the SAME already-realized money, multiplied once by the account size the "
                "executed corpus already assumes for its participation definition. It is "
                "published PER CELL, inside that cell's own known-set money block."
            ),
            "account_size_usd": PUBLISHED_NOTIONAL,
            "arithmetic": (
                "a per-cell sum in original-portfolio-capital fractions times "
                f"{PUBLISHED_NOTIONAL:.0f} USD. This is arithmetic on realized fractions; it is "
                "not a P&L forecast, not a claim that the size is funded, available or "
                "executable, and not a capacity certificate."
            ),
            "per_cell_field": "cells[].known_set_money.*_x_published_notional",
            "corpus_level_dollar_figure": None,
            "corpus_level_dollar_figure_why": (
                "deliberately null: a corpus dollar total would add 288 alternative books of "
                "the same dates into one balance sheet. No money is ever summed across cells, "
                "so there is no corpus dollar figure to publish."
            ),
            "not_done_anywhere_else": (
                "no band's money is re-priced at any other account size, and no money figure "
                "outside a single cell's known_set_money block is converted to dollars"
            ),
        },
        "band_rulers": {
            "prespecified_before_any_read": True,
            "edges_as_fraction_of_bar_volume": list(BAND_EDGES),
            "labels": list(BAND_LABELS),
            "leg_and_pair_labels": list(BAND_LABELS),
            "book_date_labels": list(DATE_BAND_LABELS),
            "no_leg_executed_is_not_missing_volume": (
                "a KNOWN date that placed no order is undeployed capital, a real dated "
                "outcome. missing_volume means a leg whose executed bar carried no volume. "
                "Filing the two together would overstate how often a size is UNKNOWN and "
                "understate how often capital simply stayed in cash."
            ),
            "cycle_rule": (
                "a CLOSED PAIR is classified by the MAXIMUM participation over its own cycle: "
                "the larger of its entry-leg and exit-leg participation, which is the size the "
                "round trip actually had to absorb. Entry-side and exit-side classifications "
                "of the SAME money are published beside it."
            ),
            "leg_rule": "each leg is classified by its own published participation",
            "book_date_rule": (
                "a KNOWN book date is classified by the MAXIMUM participation over every leg "
                "that date executed; a KNOWN date that placed no order gets its OWN "
                "no_leg_executed band, which is undeployed capital rather than an unknown size"
            ),
            "missing_volume_is_its_own_band": (
                "an absent bar volume leaves participation null. That is UNKNOWN, reported as "
                "its own band and never folded into a zero band."
            ),
            "every_band_retained": (
                "no band is merged, dropped, tuned or thresholded. Each is a classification of "
                "ALREADY REALIZED money, published beside the cell's full known-set money."
            ),
            "is_not": (
                "a fillability verdict, a queue or venue model, or a basis for removing any "
                "trade after the fact"
            ),
        },
        "contracts": {
            "alternative_books_never_summed": (
                "a cell is one alternative book over the same dates; no band table, count or "
                "money figure is ever added across cells, averaged into a portfolio or read as "
                "a fundable combination"
            ),
            "whole_unknown_book_excluded": (
                "UNKNOWN books leave every money cohort entirely -- pairs, legs, members, gross, "
                "fees and net together, not merely their unknown member. Their counts, their "
                "exact corpus-wide date list and their recorded ledger amounts are published; "
                "nothing is imputed as a zero."
            ),
            "no_retroactive_exclusion": (
                "no trade is removed after the fact, and no band-restricted or exclusion-based "
                "mean is ever named a strategy EV, an out-of-sample result or a recommendation"
            ),
            "no_new_thresholds": (
                "no hurdle, gate, quantile, score cut, sizing rule, selection or filter is "
                "introduced, tuned or suggested; every field describes executed legs"
            ),
            "ladder_is_quantity_linearity_only": (
                "the 10,000/1,000/100 USD ladder rescales PARTICIPATION only. It produces no "
                "smaller-size profit, fill, fee or capacity figure, and its money fields are "
                "null by construction."
            ),
            "proxy_not_quote": (
                "participation is size over the traded volume of the bar whose open is the "
                "fill. Nothing certifies a broker, venue, route, queue position, fill "
                "probability, order type or extended-hours behaviour; no quote or network read "
                "is required or performed."
            ),
            "confidence_is_none": (
                "no confidence interval, probability, significance test or p-value is "
                "published. Counts are a description of realized money."
            ),
            "declared_vintage_pins_recorded": (
                "the executed artifact's declared producer digest belongs to the driver that "
                "actually produced its corpus; declared and on-disk digests are both recorded "
                "and neither corpus is rejected for a driver that has since moved on HEAD."
            ),
            "cash0_is_a_ledger_identity": (
                "cash0:cash executes nothing, places no size and is excluded from every active "
                "count and band table"
            ),
        },
        "participation_rulers": {
            "definition": (
                "participation = fills.shares_per_capital * 10,000.0 / the executed bar's "
                "traded share volume, where shares_per_capital is SHARES PER ONE ACCOUNT "
                "DOLLAR of the book's original portfolio capital"
            ),
            "certification": (
                "SIZE-OVER-VOLUME PROXY ONLY. The fill is a modelled canonical-bar OPEN: not a "
                "bid/ask quote, not a VWAP, not a mid and not an observed broker execution."
            ),
            "prespecified_bands": list(BAND_LABELS),
            "rulers_are_not_truths": (
                "a band boundary is a SIZE ruler chosen before the read. It is not a statement "
                "about what would or would not have filled."
            ),
        },
        "integrity": {
            "cash_ledger_and_member_identities_max_abs_residual": f(worst),
            "tolerance": TOL,
            "identity_failures": failing,
            "all_identities_hold": bool(not failing and worst <= TOL),
            "journal": journal,
            "per_cell_date_coverage_is_complete": True,
            "unknown_flag_equals_null_return": True,
            "day_sets_identical_across_cells": True,
        },
        "corpus": corpus,
        "breadth": breadth_block,
        "cells": cells,
        "cash0_reference": cash0_section,
        "test_multiple_selection_boundary": {
            "active_cells_scored_on_this_corpus": len(cells),
            "cash0_control_cells": len(cash0_keys),
            "cells_are": (
                "alternative books over the SAME original roster, the SAME frozen OOF "
                "forecasts, the SAME minute grid and the SAME dates"
            ),
            "cells_are_not": (
                "independent samples, portfolio positions or fundable combinations; they share "
                "dates, names and fills"
            ),
            "multiple_selection": (
                "every active cell was scored on this one corpus, so any largest or smallest "
                "figure here is an order statistic over correlated alternatives. No cell is "
                "selected, promoted, named best or validated."
            ),
            "no_significance_test": True,
            "no_p_value_published": True,
            "no_promoted_edge": (
                "nothing here is a claim about future money, a capacity certification or a "
                "deployable edge"
            ),
            "ordering": "cells are sorted by cell_id; no cell carries a rank field",
        },
        "caveats": [
            "a participation band is a SIZE ruler over already-realized money. It is not a "
            "fillability verdict and it never removes a trade after the fact.",
            "the full-bar-volume split compares size with historical printed volume, not "
            "available market depth. Additional orders could alter volume and prices; a ratio "
            "above one flags unpriced liquidity assumptions, not mathematical fill impossibility.",
            "every figure is a fraction of that book's own original portfolio capital, not of "
            "any shared account and not of the upstream forecast's per-claim unit.",
            "UNKNOWN books leave every money cohort whole; their counts and exact dates are "
            "published and nothing is imputed.",
            "cells are alternative books of one corpus and are never summed into a portfolio.",
            "the lower-account ladder is quantity linearity on participation only; no "
            "smaller-size profit is priced anywhere in this artifact.",
            "nothing here certifies a broker, venue, route, fill probability or extended-hours "
            "behaviour, and no protected outcome was read.",
        ],
        "source_lineage": {
            "declared_pins": pins,
            "declared_driver_digest": up.get("source_sha256"),
            "declared_engine_digest": up.get("engine_sha256"),
            "engine_note": (
                "the accounting engine is unchanged across these corpora; its declared digest "
                "is recorded above and is never re-derived or substituted"
            ),
        },
    }

    out_path = Path(a.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps(artifact, indent=2, allow_nan=False, default=str) + "\n")
    tmp.replace(out_path)
    print(
        f"[cash-capacity] active_cells={len(cells)} cash0_cells={len(cash0_keys)} riders={riders} "
        f"books={int(daily.height)} legs={legs_total} closed_pairs={corpus['closed_pairs']} "
        f"legs_over_full_bar_volume={corpus['legs_over_full_bar_volume']} "
        f"max_identity_residual={f(worst)} evidence={evidence} -> {out_path}",
        flush=True,
    )
    if failing:
        print(f"[cash-capacity] WARNING identity failures: {failing[:5]}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
