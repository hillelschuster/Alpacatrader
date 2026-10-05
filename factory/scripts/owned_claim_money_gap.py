#!/usr/bin/env python3
"""OWNED-CLAIM MONEY GAP — dollar obstacles and break-even arithmetic per cell.

This producer is READ-ONLY over already-computed realized books and the corrected
loss-source artifact. It writes exactly one JSON and nothing else.

What it consolidates, per (book, clock, N, side cost, policy) cell, on dates that
BOTH books resolve for every policy (an exact same-date paired set):

  * dud / no-excursion losses   -- claims whose observed-window MFE class is MFElt10
  * fees                        -- entry and exit fee dollars actually charged
  * tail proceeds               -- MFE30_100 / MFE100 claims: preserved vs destroyed
  * surrendered upside          -- ex-post ruler gap between the observed bar-high
                                   excursion scaled by the deployed notional and the
                                   dollars the book actually realized
  * concentration               -- top/bottom 5 and 10 days and names
  * release-before signals      -- harvestability first-push ruler vs the book's own
                                   first discretionary release execution

Definitions are taken verbatim from factory/scripts/owned_claim_loss_sources.py and
its artifact; this script asserts that the staged claim books and the artifact pins
still match before using them, and it reproduces the artifact's own 352 cells and
2112 release cells as a cross-check.

Break-even arithmetic uses DISJOINT levers, not a sequential ladder. Since
net = gross_move - fees, the PRICE lever (the no-excursion claims' GROSS price
loss, fees held constant) and the COST lever (all modeled fees, gross held
constant) act on disjoint parts of the same identity and add without overlap.
The no-excursion NET loss is never used as a lever: it already contains those
claims' fees, so a net-then-fees ladder would remove them twice. Each lever is
also reported as an independent one-lever scenario against the same deficit.

The binding contract is the tail rent required with EVERY other outcome held
fixed, which equals the cell deficit. No threshold is tuned, no hypothetical
zero-dud or zero-fee world is assumed, and no hindsight MFE class is asserted to
be tradable. Cells are alternative correlated books on a shared date set, so a
sum across cells is an arithmetic cross-case aggregate, never portfolio PnL.

Money unit: original portfolio capital dollars (member net_pnl). Never a per-claim
return, never mixed across books, never mixed across N.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

for _v in ("POLARS_MAX_THREADS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "2")

import polars as pl  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lifecycle_study as ls  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "owned_claim_loss_sources.py"
ARTIFACT_NAME = "owned_claim_loss_sources.json"
BOOKS = ("fvi", "policy_return")
BOOK_ROOT = {"fvi": "replay", "policy_return": "replay_policy"}
BOOK_MODEL = {"fvi": "value", "policy_return": "value_policy"}
CLOCKS = (540, 560, 569, 571)
VIEWS = (3, 5)
SIDES = (0.005, 0.0075)
POLICY_COUNT = 11
N_TEST = 383
KIND = "DISCOVERY-DIAGNOSTIC-NOT-ALPHA"

# MFE classes exactly as owned_claim_loss_sources._class_expr emits them.
DUD_CLASS = "MFElt10"
MID_CLASS = "MFE10_30"
TAIL_CLASSES = ("MFE30_100", "MFE100")
MASKED_CLASSES = ("incomplete_window", "unknown_mfe")  # right edge stale / no peak

REL_ENDS = (780, 900)
REL_THRESHOLDS = (0.05, 0.15, 0.30)
KEYS = ["day", "clock", "rank", "ticker"]
CELL = ["clock", "n", "side", "policy"]

CLAIM_COLS = [
    "day",
    "clock",
    "n",
    "side",
    "policy",
    "ticker",
    "rank",
    "net",
    "gross",
    "fees",
    "entry_fee",
    "exit_fee",
    "known",
    "unfilled",
    "book",
    "book_unknown",
    "reentries",
    "mfe_full",
    "mfe_class_full",
    "initial_q",
    "roster_fill_px",
    "release_exec",
    "n_sells",
    "n_buys",
]

TAIL_K = (5, 10)


# ------------------------------------------------------------------ small helpers
def v2(data_root: Path) -> Path:
    return ls.v2_dir(data_root)


def owned(data_root: Path) -> Path:
    return v2(data_root) / "owned_claim"


def stage_dir(data_root: Path) -> Path:
    return owned(data_root) / "loss_sources_stage"


def book_dir(data_root: Path, book: str) -> Path:
    return owned(data_root) / BOOK_ROOT[book]


def model_dir(data_root: Path, book: str) -> Path:
    return owned(data_root) / BOOK_MODEL[book]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def f(x):
    """JSON-safe float: NaN is UNKNOWN, never a silent zero."""
    if x is None:
        return None
    x = float(x)
    return None if x != x or x in (float("inf"), float("-inf")) else x


def ratio(num, den):
    if num is None or den is None or den == 0:
        return None
    return f(num / den)


# ------------------------------------------------------------------ pin verification
def verify_pins(data_root: Path, artifacts_root: Path, days: list[str]) -> dict:
    art_path = artifacts_root / ARTIFACT_NAME
    if not art_path.exists():
        raise SystemExit(f"corrected loss-source artifact missing: {art_path}")
    art = json.loads(art_path.read_text())
    if art.get("kind") != KIND:
        raise SystemExit(f"unexpected artifact kind {art.get('kind')!r}")

    src_sha = sha256_file(SCRIPT)
    if art.get("producer_sha256") != src_sha:
        raise SystemExit(
            "loss-source producer has changed since the artifact was written "
            f"({art.get('producer_sha256')} != {src_sha}); refusing to reuse stale definitions"
        )

    sd = stage_dir(data_root)
    if not sd.is_dir():
        raise SystemExit(f"staged realized claim books missing: {sd}")
    markers = {}
    for day in days:
        m = sd / f"{day}.json"
        p = sd / f"{day}.claims.parquet"
        if not m.exists() or not p.exists():
            raise SystemExit(f"staged claim book incomplete for {day} (missing {m.name}/{p.name})")
        markers[day] = json.loads(m.read_text())
    bad = [
        d
        for d, v in markers.items()
        if v.get("run_id") != art["run_id"] or v.get("producer_sha256") != src_sha
    ]
    if bad:
        raise SystemExit(
            f"{len(bad)} staged day books carry a foreign run_id/producer (first: {bad[:5]})"
        )

    pins = {
        "artifact_sha256": sha256_file(art_path),
        "loss_sources_producer_sha256": src_sha,
        "artifact_run_id": art["run_id"],
        "books": {},
    }
    for book in BOOKS:
        bdir = book_dir(data_root, book)
        daily = bdir / "daily.parquet"
        if not daily.exists():
            raise SystemExit(f"{book} realized book missing: {daily}")
        rec = art["inputs"]["books"][book]
        for key, path in (
            ("daily_sha256", daily),
            ("summary_sha256", bdir / "summary.parquet"),
            ("model_metadata_sha256", model_dir(data_root, book) / "metadata.json"),
        ):
            got = sha256_file(path)
            if rec.get(key) != got:
                raise SystemExit(
                    f"{book} {key} drifted from the artifact "
                    f"({rec.get(key)} != {got}); refusing a mixed panel"
                )
        pins["books"][book] = {
            "daily_sha256": rec["daily_sha256"],
            "summary_sha256": rec["summary_sha256"],
            "model_metadata_sha256": rec["model_metadata_sha256"],
        }
    harv = v2(data_root) / "harvestability" / "events.parquet"
    if art["inputs"]["harvestability_events_sha256"] != sha256_file(harv):
        raise SystemExit("harvestability events drifted from the artifact")
    pins["harvestability_events_sha256"] = art["inputs"]["harvestability_events_sha256"]
    pins["split_sha256"] = art["inputs"]["split_sha256"]
    pins["stage_dir"] = str(sd)
    pins["stage_marker_count"] = len(markers)
    return art, pins


def fold_map(data_root: Path, art: dict, days: list[str]) -> dict:
    """day -> fold id, from the model metadata the books were replayed under."""
    per_book = {}
    for book in BOOKS:
        meta = json.loads((model_dir(data_root, book) / "metadata.json").read_text())
        per_book[book] = {d: f["fold"] for f in meta["folds"] for d in f["test_days"]}
    if per_book["fvi"] != per_book["policy_return"]:
        raise SystemExit("the two books disagree on fold test days; refusing a mixed panel")
    fm = per_book["fvi"]
    missing = [d for d in days if d not in fm]
    if missing:
        raise SystemExit(
            f"{len(missing)} test days carry no fold assignment (first: {missing[:5]})"
        )
    if sorted(fm) != sorted(art["test_days"]):
        raise SystemExit("model fold test days differ from the artifact test days")
    return fm


# ------------------------------------------------------------------ date supports
def paired_dates(daily: dict, days: list[str]) -> pl.DataFrame:
    """Dates where EVERY policy resolves for that clock/N/cost in BOTH books."""
    sel = None
    for book in BOOKS:
        d = daily[book].filter(pl.col("day").is_in(days))
        ok = (
            d.group_by(["day", "clock", "n", "side"])
            .agg(
                (pl.col("ret").is_not_null().all() & (pl.len() == POLICY_COUNT)).alias("all_known"),
            )
            .filter(pl.col("all_known"))
            .select(["day", "clock", "n", "side"])
        )
        sel = ok if sel is None else sel.join(ok, on=["day", "clock", "n", "side"], how="inner")
    return sel.sort(["clock", "n", "side", "day"])


def own_dates(daily: dict, book: str, days: list[str]) -> pl.DataFrame:
    d = daily[book].filter(pl.col("day").is_in(days))
    return (
        d.group_by(["day", "clock", "n", "side"])
        .agg((pl.col("ret").is_not_null().all() & (pl.len() == POLICY_COUNT)).alias("all_known"))
        .filter(pl.col("all_known"))
        .select(["day", "clock", "n", "side"])
    )


# ------------------------------------------------------------------ tail concentration
def _tail_concentration(frame: pl.DataFrame, group_col: str, total: float, k: int) -> dict:
    g = (
        frame.group_by(group_col)
        .agg(pl.col("net").sum().alias("net"), pl.len().alias("slots"))
        .sort("net", descending=True)
    )
    nets, slots = g["net"], g["slots"]
    n_groups, total_slots = g.height, frame.height
    top = float(nets.head(k).sum()) if n_groups else 0.0
    bottom = float(nets.tail(k).sum()) if n_groups else 0.0
    top_slots = int(slots.head(k).sum()) if n_groups else 0
    bottom_slots = int(slots.tail(k).sum()) if n_groups else 0
    return {
        "groups": n_groups,
        "slots": total_slots,
        "k": k,
        "top_sum": f(top),
        "top_slots": top_slots,
        "bottom_sum": f(bottom),
        "bottom_slots": bottom_slots,
        "top_share": f(top / total) if total else None,
        "bottom_share": f(bottom / total) if total else None,
        "ev_ex_top_k": f((total - top) / (total_slots - top_slots))
        if total_slots - top_slots > 0
        else None,
        "ev_ex_bottom_k": f((total - bottom) / (total_slots - bottom_slots))
        if total_slots - bottom_slots > 0
        else None,
    }


def _day_sensitivity(g: pl.DataFrame, total: float, k: int) -> dict:
    """Both-sided day sensitivity on ONE fixed date set.

    Removing the best k days from an already-negative cell necessarily pushes the mean
    further down, so that number alone cannot distinguish a broad-based loss from a few
    bad days. Removing the WORST k days is the informative direction here: if the
    remaining mean turns non-negative, the deficit is carried by a small number of bad
    days rather than being broad-based.

    The removed net dollars and the removed CLAIM COUNTS must come from the SAME sorted
    rows, otherwise the numerator and the denominator describe different populations.
    One descending-sorted day table is therefore built once, and the worst k days are
    taken from its TAIL in both columns; the best k days come from its HEAD in both.

    Unit: the PRIMARY means are ORIGINAL PORTFOLIO DOLLARS PER RETAINED BOOK-DATE,
    i.e. sum(net) / (n_unique_dates - k). Each member net is already capital-weighted,
    so dividing by claim-slots and calling it a per-day portfolio mean would be the
    wrong unit. The per-claim-slot mean is reported separately and explicitly labelled;
    the two units are never mixed or aliased.
    """
    days = g.group_by("day").agg(pl.col("net").sum().alias("net"), pl.len().alias("slots"))
    if not days.height:
        return {"days": 0, "k": k, "unit": "dollars per retained claim-slot"}
    # ONE consistently sorted table: descending by net. head(k) = best k, tail(k) = worst k.
    srt = days.sort("net", descending=True)
    nets, slots = srt["net"], srt["slots"]
    n_days = srt.height
    total_slots = int(slots.sum())
    kk = min(k, n_days)

    # best k: head of the same sorted table, for BOTH dollars and counts
    top = float(nets.head(kk).sum())
    top_slots = int(slots.head(kk).sum())
    top_days = srt.head(kk)["day"].to_list()
    # worst k: tail of the SAME sorted table, for BOTH dollars and counts
    bottom = float(nets.tail(kk).sum())
    bottom_slots = int(slots.tail(kk).sum())
    bottom_days = srt.tail(kk)["day"].to_list()

    # PRIMARY: original portfolio dollars per retained book-date.
    kept_days = n_days - kk
    ex_top = (total - top) / kept_days if kept_days > 0 else None
    ex_bottom = (total - bottom) / kept_days if kept_days > 0 else None
    # SECONDARY, explicitly a different unit: dollars per retained claim-slot.
    kept_slots_ex_top = total_slots - top_slots
    kept_slots_ex_bottom = total_slots - bottom_slots
    ex_top_slot = (total - top) / kept_slots_ex_top if kept_slots_ex_top > 0 else None
    ex_bottom_slot = (total - bottom) / kept_slots_ex_bottom if kept_slots_ex_bottom > 0 else None

    return {
        "days": int(n_days),
        "k": kk,
        "unit": "original portfolio dollars per retained book-date",
        "total_net": f(total),
        "total_slots": total_slots,
        "best_k_days_sum": f(top),
        "best_k_days_slots": top_slots,
        "best_k_dates_removed": top_days,
        "worst_k_days_sum": f(bottom),
        "worst_k_days_slots": bottom_slots,
        "worst_k_dates_removed": bottom_days,
        "worst_k_days_share_of_total": f(bottom / total) if total else None,
        "mean_ex_best_k_days": f(ex_top),
        "mean_ex_best_k_days_unit": "original portfolio dollars per retained book-date",
        "mean_ex_worst_k_days": f(ex_bottom),
        "mean_ex_worst_k_days_unit": "original portfolio dollars per retained book-date",
        "secondary_mean_ex_best_k_days_per_claim_slot": f(ex_top_slot),
        "secondary_mean_ex_worst_k_days_per_claim_slot": f(ex_bottom_slot),
        "secondary_unit": "dollars per retained claim-slot; a DIFFERENT denominator from the "
        "primary book-date mean and never to be read as a per-day portfolio mean",
        "removed_dollars_and_counts_share_the_same_rows": True,
        "deficit_carried_by_few_bad_days": bool(ex_bottom is not None and ex_bottom >= 0.0),
        "reading": "mean_ex_best_k_days is mechanically worse for an already-negative cell "
        "and proves nothing on its own; mean_ex_worst_k_days is the diagnostic "
        "direction. The primary unit is original portfolio dollars per retained "
        "book-date, consistent with cash_net / paired_days. Both directions use "
        "the same cell dates and the same claim population, and the removed "
        "dollars and the removed claim counts are taken from the same sorted rows.",
    }


# ------------------------------------------------------------------ per-cell build
def _cell_bucket(frame: pl.DataFrame, classes: tuple) -> dict:
    b = frame.filter(pl.col("mfe_class_full").is_in(list(classes)))
    return {
        "claims": b.height,
        "net": f(b["net"].sum()),
        "gross_move": f(b["gross"].sum()),
        "entry_fee": f(b["entry_fee"].sum()),
        "exit_fee": f(b["exit_fee"].sum()),
        "fees": f(b["fees"].sum()),
        "gross_loss_dollars": f(-b["gross"].clip(upper_bound=0.0).sum()),
        "net_loss_dollars": f(-b["net"].clip(upper_bound=0.0).sum()),
        "win_claims": int((b["net"] > 0).sum()),
        "lose_claims": int((b["net"] <= 0).sum()),
    }


def build_cells(pc: pl.DataFrame, book: str) -> tuple[dict, dict]:
    """pc = that book's KNOWN claims on the paired dates. Returns (cells, recon)."""
    cells: dict[tuple, dict] = {}
    agg = pc.group_by(CELL).agg(
        pl.len().alias("known_claims"),
        pl.col("day").n_unique().alias("paired_days"),
        pl.col("net").sum().alias("net"),
        pl.col("gross").sum().alias("gross_move"),
        pl.col("entry_fee").sum().alias("entry_fee"),
        pl.col("exit_fee").sum().alias("exit_fee"),
        pl.col("fees").sum().alias("fees"),
        pl.col("unfilled").sum().alias("unfilled_known_claims"),
        (pl.col("n_sells") > 0).sum().alias("claims_with_a_sale"),
        (pl.col("n_buys") > 1).sum().alias("claims_with_a_reentry"),
        pl.col("reentries").sum().alias("reentries"),
        # total avoidable loss dollars over EVERY class (a lever independent of class)
        (-pl.col("net").clip(upper_bound=0.0)).sum().alias("total_net_loss_dollars"),
        (-pl.col("gross").clip(upper_bound=0.0)).sum().alias("total_gross_loss_dollars"),
    )
    for r in agg.iter_rows(named=True):
        key = (r["clock"], r["n"], r["side"], r["policy"])
        cells[key] = {
            "book": book,
            "clock": r["clock"],
            "n": r["n"],
            "side": r["side"],
            "policy": r["policy"],
            "paired_days": int(r["paired_days"]),
            "known_claims": int(r["known_claims"]),
            "unfilled_known_claims": int(r["unfilled_known_claims"]),
            "claims_with_a_sale": int(r["claims_with_a_sale"]),
            "claims_with_a_reentry": int(r["claims_with_a_reentry"]),
            "reentries": int(r["reentries"]),
            "cash_net": f(r["net"]),
            "gross_move": f(r["gross_move"]),
            "entry_fee": f(r["entry_fee"]),
            "exit_fee": f(r["exit_fee"]),
            "fees": f(r["fees"]),
            "total_gross_loss_dollars": f(r["total_gross_loss_dollars"]),
            "total_net_loss_dollars": f(r["total_net_loss_dollars"]),
        }

    # class buckets
    for key in list(cells):
        mask = (
            (pl.col("clock") == key[0])
            & (pl.col("n") == key[1])
            & (pl.col("side") == key[2])
            & (pl.col("policy") == key[3])
        )
        g = pc.filter(mask)
        cells[key]["dud_no_excursion"] = _cell_bucket(g, (DUD_CLASS,))
        cells[key]["mid_excursion"] = _cell_bucket(g, (MID_CLASS,))
        cells[key]["tail"] = _cell_bucket(g, TAIL_CLASSES)
        cells[key]["masked_attribution"] = _cell_bucket(g, MASKED_CLASSES)

        t = cells[key]["tail"]
        t["preserved_net"] = None
        t["destroyed_net"] = None
        t["preserved_claims"] = 0
        t["destroyed_claims"] = 0
        tb = g.filter(pl.col("mfe_class_full").is_in(list(TAIL_CLASSES)))
        if tb.height:
            pos, neg = tb.filter(pl.col("net") > 0), tb.filter(pl.col("net") <= 0)
            t["preserved_claims"], t["preserved_net"] = pos.height, f(pos["net"].sum())
            t["destroyed_claims"], t["destroyed_net"] = neg.height, f(neg["net"].sum())
            mean_mfe = tb["mfe_full"].filter(tb["mfe_full"].is_finite()).mean()
            mean_ret = (tb["net"] * tb["n"]).mean()
            t["mean_mfe_full"] = f(mean_mfe)
            t["mean_net_return"] = f(mean_ret)
            t["capture_ratio"] = f(mean_ret / mean_mfe) if mean_mfe and mean_mfe > 0 else None
        else:
            t["mean_mfe_full"] = t["mean_net_return"] = t["capture_ratio"] = None

        # surrender ruler: observed bar-high excursion vs dollars actually realized.
        su = g.filter(
            (pl.col("reentries") == 0)
            & (pl.col("mfe_full").is_finite())
            & (pl.col("initial_q").is_finite())
            & (pl.col("roster_fill_px").is_finite())
            & (pl.col("initial_q") > 0)
            & (pl.col("roster_fill_px") > 0)
            & ~pl.col("mfe_class_full").is_in(list(MASKED_CLASSES))
        )
        paper = (
            (su["mfe_full"].clip(lower_bound=0.0) * su["initial_q"] * su["roster_fill_px"])
            if su.height
            else pl.Series([], dtype=pl.Float64)
        )
        gap = (paper - su["gross"]) if su.height else pl.Series([], dtype=pl.Float64)
        cells[key]["surrendered_upside"] = {
            "claims_ruled": su.height,
            "paper_excursion_dollars": f(paper.sum()) if su.height else 0.0,
            "realized_gross_dollars": f(su["gross"].sum()) if su.height else 0.0,
            "surrender_gap_dollars_raw": f(gap.sum()) if su.height else 0.0,
            "surrender_gap_dollars_floored": f(gap.clip(lower_bound=0.0).sum())
            if su.height
            else 0.0,
            "claims_where_realized_exceeds_paper": int((gap < -1e-12).sum()) if su.height else 0,
            "ruler": "mfe_full x deployed notional (initial_q x fill_px), no re-entry claims, "
            "unmasked windows only; a bar-high observation, NOT an executable fill",
            "tradable_claim": False,
        }

        # concentration
        tot = float(g["net"].sum()) if g.height else 0.0
        cells[key]["concentration"] = {
            f"{lbl}{k}": _tail_concentration(g, col, tot, k)
            for k in TAIL_K
            for lbl, col in (("day", "day"), ("name", "ticker"))
        }
        cells[key]["concentration_total_net"] = f(tot)
        cells[key]["day_sensitivity"] = {f"k{k}": _day_sensitivity(g, tot, k) for k in TAIL_K}
    return cells, {}


def add_break_even(cell: dict) -> dict:
    """Disjoint dollar levers from cash to break-even. No tuned threshold anywhere.

    net = gross_move - fees, so a PRICE lever and a COST lever act on disjoint parts
    of the same identity. The price lever is therefore the DUD GROSS loss (price move
    only, fees held constant) and the cost lever is the fee total. Subtracting the dud
    NET loss and then the fee total would remove the dud claims' fees twice; that
    double-count is why the net-loss form is not used here.

    Every lever is also reported as an INDEPENDENT one-lever scenario against the same
    deficit, so no reader has to rely on a sequential path.
    """
    net = cell["cash_net"] or 0.0
    deficit = max(0.0, -net)
    dud = cell["dud_no_excursion"]
    dud_gross_loss = dud["gross_loss_dollars"] or 0.0
    dud_net_loss = dud["net_loss_dollars"] or 0.0
    dud_fees = dud["fees"] or 0.0
    fees = cell["fees"] or 0.0
    total_loss = cell["total_net_loss_dollars"] or 0.0
    preserved_tail = cell["tail"]["preserved_net"] or 0.0
    preserved_tail_claims = cell["tail"]["preserved_claims"] or 0
    days = cell["paired_days"] or 0

    # Independent one-lever scenarios, each applied alone to the same deficit.
    price_only_residual = max(0.0, deficit - dud_gross_loss)
    cost_only_residual = max(0.0, deficit - fees)
    # Disjoint joint scenario: net = gross - fees, so removing dud GROSS losses (price)
    # and all fees (cost) improve cash by exactly the sum, with no overlap.
    joint_residual = max(0.0, deficit - (dud_gross_loss + fees))

    # Tail rent with EVERY other outcome held fixed: the whole deficit must be earned
    # by additional tail dollars, because nothing else is allowed to change.
    tail_other_fixed = deficit

    cell["break_even"] = {
        "deficit_to_cash_dollars": f(deficit),
        "deficit_per_paired_day_dollars": f(deficit / days) if days else None,
        "levers_are_disjoint": (
            "price lever acts on gross_move, cost lever acts on fees; net = gross - fees, "
            "so the two sum without overlap. The dud NET loss is NOT used as a lever "
            "because it already contains the dud claims' fees."
        ),
        "price_lever": {
            "what": "dud (no-excursion) claims' GROSS price loss removed, fees held constant",
            "dollars": f(dud_gross_loss),
            "covers_deficit_alone": bool(dud_gross_loss >= deficit) if deficit > 0 else None,
            "residual_if_applied_alone_dollars": f(price_only_residual),
        },
        "cost_lever": {
            "what": "all modeled entry+exit fees removed, gross_move held constant",
            "dollars": f(fees),
            "covers_deficit_alone": bool(fees >= deficit) if deficit > 0 else None,
            "residual_if_applied_alone_dollars": f(cost_only_residual),
            "not_a_recommendation": "this is the arithmetic size of the fee the realized "
            "turnover paid. It is NOT a proposal to lower the per-side "
            "cost below the modeled 50/75bps; any such change would be "
            "a different, unfalsified cost assumption.",
        },
        "joint_disjoint_scenario": {
            "what": "price lever and cost lever applied together",
            "improvement_dollars": f(dud_gross_loss + fees),
            "residual_dollars": f(joint_residual),
        },
        "tail_rent_other_outcomes_fixed": {
            "what": "additional tail dollars required with EVERY other outcome held fixed; "
            "this is the honest binding requirement and does not depend on any "
            "imaginary zero-dud / zero-fee world",
            "additional_tail_dollars_needed": f(tail_other_fixed),
            "as_multiple_of_current_preserved_tail": f(tail_other_fixed / preserved_tail)
            if preserved_tail > 0
            else None,
            "dollars_per_preserved_tail_claim": f(tail_other_fixed / preserved_tail_claims)
            if preserved_tail_claims > 0
            else None,
            "residual_after_joint_disjoint_levers_dollars": f(joint_residual),
        },
        "decomposition_check": {
            "dud_net_loss_dollars": f(dud_net_loss),
            "dud_fees_dollars": f(dud_fees),
            "note": "Each claim has net = gross_move - fees, so its net loss already "
            "includes its fees. Clipped gross-loss and net-loss totals can cover "
            "different claims; no bucket-wide loss-minus-fees identity is asserted. "
            "Price-loss and fee levers act on separate components, unlike a "
            "net-loss-then-fees ladder.",
        },
        "loss_reduction_fraction_of_all_losses_needed": f(min(1.0, deficit / total_loss))
        if total_loss > 0
        else None,
        "price_lever_share_of_deficit": ratio(dud_gross_loss, deficit),
        "cost_lever_share_of_deficit": ratio(fees, deficit),
        "surrender_gap_share_of_deficit": ratio(
            cell["surrendered_upside"]["surrender_gap_dollars_raw"], deficit
        ),
        "arithmetic_only": "each lever is an exact dollar quantity on the measured cell; "
        "no threshold was tuned and no hindsight class is claimed tradable",
    }
    return cell


# ------------------------------------------------------------------ release-before
def release_before(claims_book: pl.DataFrame, harv: pl.DataFrame) -> pl.DataFrame:
    """Per-ruler release-before counts (reproduces the artifact's 2112 cells)."""
    out = []
    for n in VIEWS:
        sign = harv.filter(pl.col("rank") <= n)
        rawn = sign.group_by(["clock", "end", "threshold"]).agg(
            pl.len().alias("raw_signals"), pl.col("day").n_unique().alias("signal_days")
        )
        rel = sign.join(
            claims_book.filter(pl.col("n") == n).select(
                KEYS + ["n", "side", "policy", "release_exec"]
            ),
            on=KEYS,
            how="left",
        )
        rel = rel.with_columns(
            pl.when(pl.col("release_exec").is_not_null() & (pl.col("release_exec") < pl.col("t")))
            .then(True)
            .otherwise(False)
            .alias("before")
        )
        g = (
            rel.group_by(["clock", "end", "threshold", "n", "side", "policy"])
            .agg(
                pl.len().alias("signals"),
                pl.col("before").sum().alias("released_before"),
                (pl.col("before") & (pl.col("release_exec") <= pl.col("t") - 5))
                .sum()
                .alias("released_before_5"),
                (pl.col("before") & (pl.col("release_exec") <= pl.col("t") - 30))
                .sum()
                .alias("released_before_30"),
            )
            .join(rawn, on=["clock", "end", "threshold"], how="left")
        )
        out.append(g)
    return pl.concat(out, how="diagonal_relaxed")


def release_dollars(pc: pl.DataFrame, first_push: pl.DataFrame) -> dict:
    """Dollars of claims the book released before the EARLIEST first-push signal.

    One row per claim key, so a claim is counted at most once; the per-ruler counts
    above are the artifact's own repeated-measure view and do not sum this way.
    """
    j = pc.join(first_push, on=KEYS, how="left")
    has = pl.col("first_push_t").is_not_null()
    before = (
        has
        & pl.col("release_exec").is_not_null()
        & (pl.col("release_exec") < pl.col("first_push_t"))
    )
    a = j.group_by(CELL).agg(
        pl.len().alias("claims"),
        has.sum().alias("claims_with_a_push_signal"),
        (~has).sum().alias("claims_without_a_push_signal"),
        before.sum().alias("released_before_claims"),
        pl.col("net").filter(before).sum().alias("released_before_net"),
        pl.col("fees").filter(before).sum().alias("released_before_fees"),
        pl.col("net").filter(before & (pl.col("net") < 0)).sum().alias("released_before_net_loss"),
    )
    return {tuple(r[c] for c in CELL): r for r in a.iter_rows(named=True)}


# ------------------------------------------------------------------ period splits
def period_splits(pc: pl.DataFrame, fm: dict, days: list[str]) -> dict:
    fold_of = pl.DataFrame({"day": list(fm), "fold": [str(fm[d]) for d in fm]})
    p = pc.join(
        pl.DataFrame({"day": days}).with_columns(pl.col("day").str.slice(0, 7).alias("month")),
        on="day",
        how="left",
    ).join(fold_of, on="day", how="left")
    out = {"by_month": {}, "by_fold": {}}
    for label, col in (("by_month", "month"), ("by_fold", "fold")):
        agg = p.group_by(CELL + [col]).agg(
            pl.len().alias("claims"),
            pl.col("day").n_unique().alias("days"),
            pl.col("net").sum().alias("net"),
            pl.col("fees").sum().alias("fees"),
            (-pl.col("net").clip(upper_bound=0.0))
            .filter(pl.col("mfe_class_full") == DUD_CLASS)
            .sum()
            .alias("dud_net_loss"),
        )
        for r in agg.iter_rows(named=True):
            key = (r["clock"], r["n"], r["side"], r["policy"])
            out[label].setdefault(key, {})[str(r[col])] = {
                "days": int(r["days"]),
                "claims": int(r["claims"]),
                "net": f(r["net"]),
                "fees": f(r["fees"]),
                "dud_net_loss": f(r["dud_net_loss"]),
            }
    return out


# ------------------------------------------------------------------ mechanisms
def _top(rows: list, k: int = 5) -> list:
    return rows[:k]


def mechanisms(cells: list[dict]) -> tuple[list[dict], dict]:
    """Data-backed candidates, each with the exact measured numbers behind it."""

    def rank(fn):
        return sorted([c for c in cells if fn(c) is not None], key=lambda c: fn(c), reverse=True)

    def be(c):
        return c["break_even"] or {}

    def refs(cs):
        return [
            {
                "clock": c["clock"],
                "n": c["n"],
                "side": c["side"],
                "policy": c["policy"],
                "book": c["book"],
                "deficit": be(c).get("deficit_to_cash_dollars"),
                "dud_gross_price_loss": c["dud_no_excursion"]["gross_loss_dollars"],
                "fees": c["fees"],
                "reentries": c.get("reentries"),
                "claims_with_a_reentry": c.get("claims_with_a_reentry"),
                "preserved_tail": c["tail"]["preserved_net"],
                "tail_multiple_other_outcomes_fixed": be(c)
                .get("tail_rent_other_outcomes_fixed", {})
                .get("as_multiple_of_current_preserved_tail"),
                "surrender_gap": c["surrendered_upside"]["surrender_gap_dollars_raw"],
            }
            for c in cs
        ]

    def tail_block(c):
        return be(c).get("tail_rent_other_outcomes_fixed", {}) or {}

    cost_cells = rank(lambda c: be(c).get("cost_lever_share_of_deficit"))
    price_cells = rank(lambda c: be(c).get("price_lever_share_of_deficit"))
    tail_cells = rank(lambda c: tail_block(c).get("as_multiple_of_current_preserved_tail"))
    loss_frac = rank(lambda c: be(c).get("loss_reduction_fraction_of_all_losses_needed"))
    surr = rank(lambda c: c["surrendered_upside"]["surrender_gap_dollars_raw"])
    rb_loss = sorted(
        [c for c in cells if c.get("release_before", {}).get("released_before_net_loss")],
        key=lambda c: c["release_before"]["released_before_net_loss"],
    )

    # Turnover can only fall where the book actually re-entered or reallocated. A
    # stop-only book that buys once and sells once has no round trip to remove, so a
    # longer minimum hold cannot reduce its fee.
    turnover_bearing = [
        c
        for c in cells
        if (c.get("reentries") or 0) > 0 or (c.get("claims_with_a_reentry") or 0) > 0
    ]
    single_round_trip = [
        c
        for c in cells
        if (c.get("reentries") or 0) == 0 and (c.get("claims_with_a_reentry") or 0) == 0
    ]

    # Concentration: the informative direction is removing the WORST days. Removing the
    # best days from an already-negative cell is mechanically worse and says nothing.
    bad_day_driven = [
        c
        for c in cells
        if (c.get("day_sensitivity", {}).get("k5") or {}).get("deficit_carried_by_few_bad_days")
    ]

    panel_facts = {
        "cells": len(cells),
        "cells_with_positive_cash": len([c for c in cells if (c["cash_net"] or 0.0) > 0.0]),
        "cells_with_deficit": len([c for c in cells if (c["cash_net"] or 0.0) <= 0.0]),
        "cells_where_price_lever_alone_covers_deficit": len(
            [c for c in cells if (be(c).get("price_lever") or {}).get("covers_deficit_alone")]
        ),
        "cells_where_cost_lever_alone_covers_deficit": len(
            [c for c in cells if (be(c).get("cost_lever") or {}).get("covers_deficit_alone")]
        ),
        "cells_where_joint_disjoint_levers_still_leave_a_residual": len(
            [
                c
                for c in cells
                if ((be(c).get("joint_disjoint_scenario") or {}).get("residual_dollars") or 0.0)
                > 0.0
            ]
        ),
        "cells_with_reentry_or_reallocation_turnover": len(turnover_bearing),
        "cells_with_a_single_round_trip": len(single_round_trip),
        "cells_where_deficit_is_carried_by_few_bad_days": len(bad_day_driven),
        "tail_rent_contract": "additional tail dollars required with every other outcome held "
        "fixed = the cell deficit; this does not depend on any joint "
        "lever scenario",
        "aggregate_note": "cells are ALTERNATIVE correlated books on a shared date set. "
        "Summing deficits ACROSS cells is an arithmetic cross-case aggregate, "
        "NOT portfolio PnL and not additive across policies. Per-cell and "
        "per-paired-day dollars are the reportable quantities.",
    }

    out = [
        {
            "id": "cost_lever_share_of_deficit",
            "statement": "Sizes the modeled entry+exit friction actually paid on filled capital, "
            "as a fraction of the cell deficit. This is the cost of the turnover "
            "the book generated, NOT a fee overcharge.",
            "measured": {
                "cells": len(cost_cells),
                "median_cost_share": f(
                    sorted([be(c)["cost_lever_share_of_deficit"] for c in cost_cells])[
                        len(cost_cells) // 2
                    ]
                )
                if cost_cells
                else None,
                "cells_with_reentry_or_reallocation_turnover": len(turnover_bearing),
                "cells_with_a_single_round_trip": len(single_round_trip),
            },
            "top_cells": refs(_top(cost_cells)),
            "hypothesis": "in cells that actually re-entered or reallocated, fewer filled round "
            "trips would leave more cash. In single-round-trip cells there is no "
            "second round trip to remove, so a longer minimum hold cannot lower "
            "their fee at all.",
            "test": "scope any turnover test to the reentry and allocate policies only, where "
            "re-entries were actually observed; leave stop-only books unchanged and "
            "report their fees unchanged. The per-side cost assumption may not change.",
            "explicitly_not_proposed": "lowering per-side friction below the modeled 50/75bps. "
            "Any price/exposure effect of lower turnover must be "
            "MEASURED on the realized books, not assumed away.",
            "evidence_source": [
                "owned_claim_money_gap.json: cells[].break_even.cost_lever",
                "owned_claim_money_gap.json: cells[].reentries",
                "owned_claim_loss_sources.json: decomposition[].fees",
            ],
            "status": "hypothesis_needs_test",
        },
        {
            "id": "no_excursion_price_loss",
            "statement": "Sizes the GROSS price loss on claims whose observed excursion never "
            "reached +10%. This is the price lever and is disjoint from the fee "
            "lever, so the two may be added without double counting.",
            "measured": {
                "cells": len(price_cells),
                "median_price_share": f(
                    sorted([be(c)["price_lever_share_of_deficit"] for c in price_cells])[
                        len(price_cells) // 2
                    ]
                )
                if price_cells
                else None,
                "cells_where_price_lever_alone_covers_deficit": len(
                    [
                        c
                        for c in cells
                        if (be(c).get("price_lever") or {}).get("covers_deficit_alone")
                    ]
                ),
            },
            "top_cells": refs(_top(price_cells)),
            "hypothesis": "an entry-side screen observable at the decision bar removes part of "
            "this price-loss bucket",
            "test": "measure, on discovery days only, whether a pre-decision observable "
            "separates the MFElt10 bucket; the class itself is a hindsight label and "
            "is NOT asserted to be tradable",
            "evidence_source": [
                "owned_claim_money_gap.json: cells[].dud_no_excursion.gross_loss_dollars",
                "owned_claim_loss_sources.json: low_excursion_tax",
            ],
            "status": "hypothesis_needs_test",
        },
        {
            "id": "required_tail_rent_other_outcomes_fixed",
            "statement": "The binding break-even requirement: additional tail dollars needed "
            "with EVERY other outcome held fixed. That equals the cell deficit, "
            "expressed as a multiple of the tail rent the cell actually "
            "preserved. This contract does not depend on any hypothetical "
            "zero-dud or zero-fee world, and it does not nominate a cell to trade.",
            "measured": {
                "cells_measured": len(cells),
                "contract": "additional_tail_dollars_needed = deficit_to_cash_dollars",
                "median_multiple_of_preserved_tail": f(
                    sorted(
                        [tail_block(c)["as_multiple_of_current_preserved_tail"] for c in tail_cells]
                    )[len(tail_cells) // 2]
                )
                if tail_cells
                else None,
                "max_multiple_of_preserved_tail": f(
                    max(
                        [tail_block(c)["as_multiple_of_current_preserved_tail"] for c in tail_cells]
                    )
                )
                if tail_cells
                else None,
                "cross_case_aggregate_deficit_NOT_portfolio_pnl": f(
                    sum((be(c).get("deficit_to_cash_dollars") or 0.0) for c in cells)
                ),
                "aggregate_warning": "cells are alternative correlated books on a shared date "
                "set; this cross-case sum is an arithmetic aggregate, "
                "NOT portfolio PnL and not additive across policies",
                "joint_disjoint_levers_still_leaving_residual_cells": len(
                    [
                        c
                        for c in cells
                        if (
                            (be(c).get("joint_disjoint_scenario") or {}).get("residual_dollars")
                            or 0.0
                        )
                        > 0.0
                    ]
                ),
            },
            "top_cells": refs(_top(tail_cells)),
            "hypothesis": "meeting break-even requires earning this much additional tail rent, "
            "with everything else held fixed. Whether that rent is obtainable is "
            "NOT established here.",
            "test": "report per-excursion-bucket realized capture on the realized books using "
            "only completed-bar states and next-observed-open fills",
            "evidence_source": [
                "owned_claim_money_gap.json: cells[].break_even.tail_rent_other_outcomes_fixed",
                "owned_claim_loss_sources.json: paper_tail",
            ],
            "status": "hypothesis_needs_test",
        },
        {
            "id": "partial_loss_reduction_needed",
            "statement": "These cells do not need every losing claim to be fixed; the "
            "measured fraction of all losing dollars that must not be lost is "
            "well below one.",
            "measured": {"cells": len(loss_frac)},
            "top_cells": refs(_top(loss_frac, 8)),
            "hypothesis": "a partial, measurable reduction in losing dollars suffices",
            "test": "bound the achievable reduction with a fixed-cost screen on discovery "
            "days before any policy claim",
            "evidence_source": [
                "owned_claim_money_gap.json: cells[].break_even."
                "loss_reduction_fraction_of_all_losses_needed"
            ],
            "status": "hypothesis_needs_test",
        },
        {
            "id": "unrealized_excursion_ruler",
            "statement": "The ex-post excursion ruler shows how many dollars of observed "
            "bar-high excursion were not realized. This is an upper bound on "
            "already-observed opportunity, never a claim that the excursion was "
            "fillable and never an entry rule.",
            "measured": {"cells": len(surr)},
            "top_cells": refs(_top(surr)),
            "hypothesis": "part of the ruler gap is preserved by a state-based exit",
            "test": "compare realized exits against the recorded first-push ruler on the "
            "same claim; the bar-high ruler may not be an executable print",
            "evidence_source": ["owned_claim_money_gap.json: cells[].surrendered_upside"],
            "status": "hypothesis_needs_test",
            "tradable_claim": False,
        },
        {
            "id": "release_before_first_push",
            "statement": "These cells released a claim before the harvestability first-push "
            "ruler and the released claims lost dollars in aggregate.",
            "measured": {"cells": len(rb_loss)},
            "top_cells": [
                {
                    "clock": c["clock"],
                    "n": c["n"],
                    "side": c["side"],
                    "policy": c["policy"],
                    "book": c["book"],
                    "released_before_claims": c["release_before"]["released_before_claims"],
                    "released_before_net": c["release_before"]["released_before_net"],
                    "released_before_net_loss": c["release_before"]["released_before_net_loss"],
                }
                for c in _top(rb_loss)
            ],
            "hypothesis": "waiting for the first-push state on these claims is worth testing",
            "test": "the first-push ruler is ex-post; any test must use a state observable at "
            "the release decision bar, not the ruler itself",
            "evidence_source": [
                "owned_claim_money_gap.json: release_before_dollars",
                "owned_claim_loss_sources.json: release_before",
            ],
            "status": "hypothesis_needs_test",
        },
        {
            "id": "day_sensitivity_both_directions",
            "statement": "Day-concentration read in BOTH directions on the same cell dates and "
            "the same claim population. Removing the best k days from an already "
            "negative cell mechanically lowers the remaining mean, so that "
            "direction cannot distinguish a broad loss from a few bad days and is "
            "NOT reported as a finding. The informative direction is removing the "
            "WORST k days: if the remaining mean turns non-negative, the deficit "
            "is carried by a small number of bad days.",
            "measured": {
                "cells_measured": len(
                    [c for c in cells if (c.get("day_sensitivity", {}).get("k5") or {}).get("days")]
                ),
                "cells_where_deficit_is_carried_by_few_bad_days": len(bad_day_driven),
                "cells_with_positive_cash_to_stress": len(
                    [c for c in cells if (c["cash_net"] or 0.0) > 0.0]
                ),
                "no_original_positive_candidate_note": (
                    "no cell in this panel has positive cash, so there is NO original-positive "
                    "candidate to stress. Best-day removal on a negative cell is a tautology, "
                    "not evidence, and is reported only alongside the worst-day direction."
                ),
                "worst_day_direction_finding": (
                    f"in {len(bad_day_driven)} of {len(cells)} cells the remaining mean turns "
                    "non-negative once the worst 5 days are removed, i.e. those deficits are "
                    "carried by a handful of bad days rather than being broad-based."
                    if bad_day_driven
                    else "no cell becomes non-negative after removing the worst 5 days, so the "
                    "deficit is not explained by a small number of bad days."
                ),
            },
            "top_cells": [
                {
                    "clock": c["clock"],
                    "n": c["n"],
                    "side": c["side"],
                    "policy": c["policy"],
                    "book": c["book"],
                    "total_net": c["day_sensitivity"]["k5"]["total_net"],
                    "best5_sum": c["day_sensitivity"]["k5"]["best_k_days_sum"],
                    "worst5_sum": c["day_sensitivity"]["k5"]["worst_k_days_sum"],
                    "mean_ex_best5_per_book_date": c["day_sensitivity"]["k5"][
                        "mean_ex_best_k_days"
                    ],
                    "mean_ex_worst5_per_book_date": c["day_sensitivity"]["k5"][
                        "mean_ex_worst_k_days"
                    ],
                    "mean_unit": "original portfolio dollars per retained book-date",
                    "carried_by_few_bad_days": c["day_sensitivity"]["k5"][
                        "deficit_carried_by_few_bad_days"
                    ],
                }
                for c in _top(
                    sorted(
                        bad_day_driven,
                        key=lambda c: -((c["day_sensitivity"]["k5"]["worst_k_days_sum"]) or 0.0),
                    )
                )
            ],
            "hypothesis": "see worst_day_direction_finding; the best-day direction is reported "
            "for completeness and carries no evidential weight here",
            "test": "day-level cash decomposition with per-day support stated, reporting both "
            "directions on identical dates",
            "evidence_source": ["owned_claim_money_gap.json: cells[].day_sensitivity"],
            "status": "hypothesis_needs_test",
        },
    ]
    return out, panel_facts


# ------------------------------------------------------------------ report
def build_report(data_root: Path, artifacts_root: Path, days: list[str], full_panel: bool) -> dict:
    art, pins = verify_pins(data_root, artifacts_root, days)
    fm = fold_map(data_root, art, days)
    sd = stage_dir(data_root)

    discovery = set(ls.discovery_days(data_root))
    if not set(days) <= discovery:
        raise SystemExit("non-discovery test dates in the staged books")
    ls.assert_coverage(days, data_root, "panel")
    ls.assert_coverage(days, data_root, "roster")

    daily = {b: pl.read_parquet(book_dir(data_root, b) / "daily.parquet") for b in BOOKS}
    for b in BOOKS:
        got = sorted(daily[b]["day"].unique().to_list())
        if got != list(art["test_days"]):
            raise SystemExit(f"{b} daily.parquet dates differ from the artifact test days")
    if not set(days) <= set(art["test_days"]):
        raise SystemExit("staged book dates are not a subset of the artifact test days")
    if full_panel and sorted(days) != list(art["test_days"]):
        raise SystemExit("staged book dates differ from the artifact test days")

    claims = pl.concat(
        [pl.read_parquet(sd / f"{d}.claims.parquet", columns=CLAIM_COLS) for d in days],
        how="diagonal_relaxed",
    )
    if set(claims["book"].unique().to_list()) != set(BOOKS):
        raise SystemExit("staged claim books do not carry both replay books")

    paired = paired_dates(daily, days)
    paired_counts = {
        (r["clock"], r["n"], r["side"]): int(r["d"])
        for r in paired.group_by(["clock", "n", "side"])
        .agg(pl.len().alias("d"))
        .iter_rows(named=True)
    }

    # ---- exact-date paired claims
    pkey = paired.select(["day", "clock", "n", "side"])
    pc = claims.join(pkey, on=["day", "clock", "n", "side"], how="semi")
    known = pc.filter(pl.col("known"))
    excluded = pc.filter(~pl.col("known"))

    # ---- book cash cross-check on the identical paired dates
    recon = []
    for b in BOOKS:
        d = daily[b].join(pkey, on=["day", "clock", "n", "side"], how="semi")
        dr = (
            d.group_by(CELL)
            .agg(
                pl.col("ret").sum().alias("ret_sum"),
                pl.col("ret").is_not_null().sum().alias("known_days"),
            )
            .with_columns(pl.lit(b).alias("book"))
        )
        cr = (
            known.filter(pl.col("book") == b)
            .group_by(CELL)
            .agg(pl.col("net").sum().alias("net_sum"), pl.len().alias("claims"))
        )
        j = dr.join(cr, on=CELL, how="full", coalesce=True)
        for r in j.iter_rows(named=True):
            diff = (
                None
                if r["ret_sum"] is None or r["net_sum"] is None
                else abs(r["net_sum"] - r["ret_sum"])
            )
            recon.append(
                {
                    "book": b,
                    "clock": r["clock"],
                    "n": r["n"],
                    "side": r["side"],
                    "policy": r["policy"],
                    "member_net_sum": f(r["net_sum"]),
                    "daily_ret_sum": f(r["ret_sum"]),
                    "abs_diff": f(diff),
                }
            )
    max_recon = max([r["abs_diff"] for r in recon if r["abs_diff"] is not None] or [0.0])
    if max_recon > 1e-9:
        raise SystemExit(f"paired-date cash reconciliation broke: {max_recon}")

    # ---- independent cross-check of the staged books against the CORE day members/fills.
    # The staged claim parquet is a derived convenience; the core per-day members are the
    # realized books themselves. Read them directly on the paired dates and require the
    # same per-cell dollars, so this file cannot silently inherit a staging bug.
    core = {}
    for b in BOOKS:
        parts = []
        for d in days:
            p = book_dir(data_root, b) / "days" / f"{d}.members.parquet"
            if not p.exists():
                raise SystemExit(f"core {b} members missing for {d}: {p}")
            parts.append(
                pl.read_parquet(
                    p, columns=["day", "clock", "n", "side", "policy", "ticker", "net_pnl"]
                )
            )
        core[b] = pl.concat(parts, how="diagonal_relaxed")
    core_recon = []
    for b in BOOKS:
        cm = core[b].filter(pl.col("net_pnl").is_not_null())
        cm = cm.join(pkey, on=["day", "clock", "n", "side"], how="semi")
        a = cm.group_by(CELL).agg(
            pl.col("net_pnl").sum().alias("core_net"), pl.len().alias("core_claims")
        )
        c2 = (
            known.filter(pl.col("book") == b)
            .group_by(CELL)
            .agg(pl.col("net").sum().alias("staged_net"), pl.len().alias("staged_claims"))
        )
        jj = a.join(c2, on=CELL, how="full", coalesce=True)
        for r in jj.iter_rows(named=True):
            dn = (
                None
                if r["core_net"] is None or r["staged_net"] is None
                else abs(r["core_net"] - r["staged_net"])
            )
            core_recon.append(
                {
                    "book": b,
                    "clock": r["clock"],
                    "n": r["n"],
                    "side": r["side"],
                    "policy": r["policy"],
                    "core_members_net_sum": f(r["core_net"]),
                    "staged_claims_net_sum": f(r["staged_net"]),
                    "core_claims": None if r["core_claims"] is None else int(r["core_claims"]),
                    "staged_claims": None
                    if r["staged_claims"] is None
                    else int(r["staged_claims"]),
                    "abs_diff": f(dn),
                }
            )
    max_core = max([r["abs_diff"] for r in core_recon if r["abs_diff"] is not None] or [0.0])
    claim_count_gap = [
        r
        for r in core_recon
        if r["core_claims"] is not None
        and r["staged_claims"] is not None
        and r["core_claims"] != r["staged_claims"]
    ]
    if max_core > 1e-9 or claim_count_gap:
        raise SystemExit(
            f"staged claim books disagree with the core day members "
            f"(max cash diff {max_core}, {len(claim_count_gap)} claim-count gaps)"
        )

    # ---- per-book cells on the paired dates
    all_cells: list[dict] = []
    for b in BOOKS:
        cs, _ = build_cells(known.filter(pl.col("book") == b), b)
        all_cells.extend(cs.values())

    # ---- release-before dollars
    harv = pl.read_parquet(
        v2(data_root) / "harvestability" / "events.parquet",
        columns=["day", "clock", "rank", "ticker", "end", "threshold", "t"],
    )
    harv = harv.filter(
        pl.col("day").is_in(days)
        & pl.col("clock").is_in(list(CLOCKS))
        & pl.col("end").is_in(list(REL_ENDS))
        & pl.col("threshold").is_in(list(REL_THRESHOLDS))
    )
    first_push = harv.group_by(KEYS).agg(pl.col("t").min().alias("first_push_t"))
    for b in BOOKS:
        rb = release_dollars(known.filter(pl.col("book") == b), first_push)
        for key, row in rb.items():
            for c in all_cells:
                if c["book"] == b and (c["clock"], c["n"], c["side"], c["policy"]) == key:
                    c["release_before"] = {
                        "claims_with_a_push_signal": int(row["claims_with_a_push_signal"]),
                        "claims_without_a_push_signal": int(row["claims_without_a_push_signal"]),
                        "released_before_claims": int(row["released_before_claims"]),
                        "released_before_net": f(row["released_before_net"]),
                        "released_before_fees": f(row["released_before_fees"]),
                        "released_before_net_loss": f(row["released_before_net_loss"]),
                        "definition": "claim released before the EARLIEST first-push t across the "
                        "2 ends x 3 thresholds ruler; one count per claim",
                    }

    splits = period_splits(known, fm, days)
    for c in all_cells:
        add_break_even(c)
        key = (c["clock"], c["n"], c["side"], c["policy"])
        c["periods"] = {
            "by_month": splits["by_month"].get(key, {}),
            "by_fold": splits["by_fold"].get(key, {}),
        }

    # ---- UNKNOWN census, never zeroed.
    # Censoring is BOOK level: one censored claim makes the whole day/clock/N/cost cell
    # resolve as unknown, so a censored cell can never satisfy the all-11-policies-known
    # paired test and drops out of every dollar sum here. That is why the paired exclusion
    # count is expected to be zero. The whole-panel census below states exactly how many
    # cells and claims that removed, so the loss of support is visible, not hidden.
    all_excluded = claims.filter(~pl.col("known"))
    censored_cells = all_excluded.select(["day", "clock", "n", "side", "book"]).unique()
    paired_cell_keys = (
        paired.select(["day", "clock", "n", "side"])
        .unique()
        .with_columns(pl.lit(True).alias("in_paired"))
    )
    censored_vs_paired = (
        censored_cells.join(paired_cell_keys, on=["day", "clock", "n", "side"], how="left")
        .group_by("book")
        .agg(
            pl.len().alias("censored_cells"),
            pl.col("day").n_unique().alias("censored_days"),
            pl.col("in_paired").fill_null(False).sum().alias("censored_cells_in_paired_set"),
        )
    )
    unknown_rows = (
        excluded.group_by(["book"] + CELL)
        .agg(pl.len().alias("excluded_claims"), pl.col("day").n_unique().alias("excluded_days"))
        .with_columns(pl.col("excluded_days").cast(pl.Int64))
    )
    unknown_by_key = {}
    for r in unknown_rows.iter_rows(named=True):
        key = (r["book"], r["clock"], r["n"], r["side"], r["policy"])
        unknown_by_key[key] = {
            "excluded_claims": int(r["excluded_claims"]),
            "excluded_days": int(r["excluded_days"]),
        }

    # ---- supports
    support = []
    for b in BOOKS:
        od = own_dates(daily, b, days)
        rows = (
            daily[b]
            .filter(pl.col("day").is_in(days))
            .group_by(["clock", "n", "side", "policy"])
            .agg(
                pl.len().alias("days_total"),
                pl.col("ret").is_not_null().sum().alias("known_days"),
                pl.col("unknown").sum().alias("unknown_days"),
                pl.col("affordability_unknown").sum().alias("affordability_unknown_sum"),
                pl.col("max_positions").max().alias("max_positions_max"),
            )
        )
        rows = rows.join(
            od.group_by(["clock", "n", "side"]).agg(pl.len().alias("own_common_days")),
            on=["clock", "n", "side"],
            how="left",
        )
        for r in rows.iter_rows(named=True):
            key = (r["clock"], r["n"], r["side"], r["policy"])
            support.append(
                {
                    "book": b,
                    "clock": r["clock"],
                    "n": r["n"],
                    "side": r["side"],
                    "policy": r["policy"],
                    "days_total": int(r["days_total"]),
                    "known_days": int(r["known_days"]),
                    "unknown_days": int(r["unknown_days"]),
                    "affordability_unknown_sum": int(r["affordability_unknown_sum"] or 0),
                    "max_positions_max": int(r["max_positions_max"] or 0),
                    "own_common_days": int(r["own_common_days"] or 0),
                    "paired_common_days": paired_counts.get((r["clock"], r["n"], r["side"]), 0),
                }
            )

    # ---- reproduce the artifact's own per-ruler release cells
    rb_artifact = {
        (r["book"], r["clock"], r["end"], r["threshold"], r["n"], r["side"], r["policy"]): r
        for r in art["release_before"]
    }
    rb_mine = {}
    # The artifact's per-ruler counts run over the book's WHOLE claim set (every staged
    # day, known or censored), not the paired subset. Reproduce that population exactly;
    # the dollar view below stays on paired KNOWN claims.
    for b in BOOKS:
        g = release_before(claims.filter(pl.col("book") == b), harv)
        for r in g.iter_rows(named=True):
            rb_mine[(b, r["clock"], r["end"], r["threshold"], r["n"], r["side"], r["policy"])] = r
    rb_check = {
        "artifact_cells": len(rb_artifact),
        "recomputed_cells": len(rb_mine),
        "enforced": full_panel,
        "note": "the artifact's per-ruler counts cover all 383 staged days; this "
        "reproduction is enforced only on a full-panel run",
        "mismatches": [],
    }
    if full_panel:
        for k, a in rb_artifact.items():
            m = rb_mine.get(k)
            if m is None or (
                a["signals"],
                a["released_before"],
                a["released_before_5"],
                a["released_before_30"],
                a["raw_signals"],
            ) != (
                m["signals"],
                m["released_before"],
                m["released_before_5"],
                m["released_before_30"],
                m["raw_signals"],
            ):
                rb_check["mismatches"].append(
                    {
                        "key": list(map(str, k)),
                        "artifact": a,
                        "recomputed": None if m is None else dict(m),
                    }
                )
        if len(rb_mine) != len(rb_artifact) or rb_check["mismatches"]:
            raise SystemExit(
                f"release-before cells do not reproduce the corrected artifact "
                f"({len(rb_mine)} vs {len(rb_artifact)}, "
                f"{len(rb_check['mismatches'])} mismatched)"
            )

    # ---- book-to-book paired delta on identical dates and keys
    bykey = {(c["book"], c["clock"], c["n"], c["side"], c["policy"]): c for c in all_cells}
    paired_rows = []
    for clock, n, side, policy in {(c["clock"], c["n"], c["side"], c["policy"]) for c in all_cells}:
        a = bykey.get(("fvi", clock, n, side, policy))
        p = bykey.get(("policy_return", clock, n, side, policy))
        if a is None or p is None:
            paired_rows.append(
                {
                    "clock": clock,
                    "n": n,
                    "side": side,
                    "policy": policy,
                    "paired_days": (a or p or {}).get("paired_days"),
                    "both_books_present": False,
                }
            )
            continue
        paired_rows.append(
            {
                "clock": clock,
                "n": n,
                "side": side,
                "policy": policy,
                "paired_days": a["paired_days"],
                "both_books_present": True,
                "fvi_cash_net": a["cash_net"],
                "policy_return_cash_net": p["cash_net"],
                "delta_policy_minus_fvi": f((p["cash_net"] or 0.0) - (a["cash_net"] or 0.0)),
                "fvi_dud_net_loss": a["dud_no_excursion"]["net_loss_dollars"],
                "policy_dud_net_loss": p["dud_no_excursion"]["net_loss_dollars"],
                "fvi_fees": a["fees"],
                "policy_fees": p["fees"],
                "fvi_preserved_tail": a["tail"]["preserved_net"],
                "policy_preserved_tail": p["tail"]["preserved_net"],
            }
        )

    mech_list, panel_facts = mechanisms(all_cells)

    return {
        "kind": KIND,
        "producer": "factory/scripts/owned_claim_money_gap.py",
        "producer_sha256": sha256_file(Path(__file__)),
        "reads": [
            "factory/artifacts/owned_claim_loss_sources.json (corrected, pinned)",
            "data/harvest01/lifecycle/v2/owned_claim/loss_sources_stage/<day>.claims.parquet",
            "data/harvest01/lifecycle/v2/owned_claim/replay{,_policy}/daily.parquet",
            "data/harvest01/lifecycle/v2/owned_claim/replay{,_policy}/days/<day>.members.parquet",
            "data/harvest01/lifecycle/v2/owned_claim/value{,_policy}/metadata.json",
            "data/harvest01/lifecycle/v2/harvestability/events.parquet",
        ],
        "inputs": {"data_root": str(data_root), "artifacts_root": str(artifacts_root), **pins},
        "test_days": days,
        "test_days_count": len(days),
        "money_unit": "original portfolio capital dollars (member net_pnl); a per-book sum "
        "over selected ranks equals that book's daily ret on known dates",
        "definitions": {
            "cell": "(book, clock, N, side cost, policy); N is never pooled across 3 and 5",
            "paired_dates": "dates where all 11 policies resolve for that clock/N/cost in BOTH "
            "books; every paired comparison uses exactly this date set",
            "dud_no_excursion": f"mfe_class_full == {DUD_CLASS} (observed-window peak excursion "
            "below +10% with a fresh right edge)",
            "tail": f"mfe_class_full in {list(TAIL_CLASSES)}; preserved = net > 0, "
            "destroyed = net <= 0",
            "masked_attribution": f"mfe_class_full in {list(MASKED_CLASSES)}; dollars are real but "
            "the class is UNKNOWN, never recoded as a dud",
            "surrendered_upside": "mfe_full x deployed notional minus realized gross, over "
            "no-re-entry unmasked known claims; a bar-high ruler, not an "
            "executable fill and not a policy promise",
            "release_before_dollars": "one count per claim, released before the earliest "
            "harvestability first-push t on the 2x3 ruler",
            "break_even_levers": "net = gross_move - fees, so the PRICE lever (dud gross "
            "price loss, fees held constant) and the COST lever (all "
            "modeled fees, gross held constant) act on disjoint parts of "
            "the identity and sum without overlap. The dud NET loss is "
            "never used as a lever because it already contains fees.",
            "tail_rent_other_outcomes_fixed": "additional tail dollars required with every other "
            "outcome held fixed = the cell deficit; reported "
            "as a multiple of the tail rent actually "
            "preserved. This is the binding contract and is "
            "independent of any joint lever scenario.",
            "day_sensitivity": "best-k and worst-k day removal on the same cell dates and same "
            "claim population. Removing the best days from an already "
            "negative cell is mechanically worse and is not evidence; the "
            "worst-day direction is the diagnostic one. Removed dollars and "
            "removed claim counts always come from the same sorted rows.",
            "day_sensitivity_unit": "the primary means are ORIGINAL PORTFOLIO DOLLARS PER "
            "RETAINED BOOK-DATE, sum(net) / (n_unique_dates - k), "
            "consistent with cash_net / paired_days. A per-claim-slot "
            "mean is reported separately and labelled; the two units "
            "are never mixed.",
            "book_unknown": "a censored book is excluded from every dollar sum and counted here",
            "blocked_entry": "a blocked entry slot is KNOWN cash at zero dollars, "
            "distinct from UNKNOWN",
        },
        "supports": support,
        "unknown": {
            "note": "censored claims are excluded from all dollar sums and counted; they are "
            "never treated as zero",
            "paired_excluded_claims": int(excluded.height),
            "paired_excluded_days": int(excluded["day"].n_unique()),
            "whole_panel_censored_claims": int(all_excluded.height),
            "whole_panel_censored_cells_by_book": [
                {
                    "book": r["book"],
                    "censored_cells": int(r["censored_cells"]),
                    "censored_days": int(r["censored_days"]),
                    "censored_cells_in_paired_set": int(r["censored_cells_in_paired_set"]),
                }
                for r in sorted(censored_vs_paired.iter_rows(named=True), key=lambda r: r["book"])
            ],
            "why_paired_exclusion_is_zero": "censoring is book level, so a censored cell cannot "
            "satisfy the all-11-policies-known paired test; it is "
            "dropped whole rather than contributing a zero",
            "by_key": [
                {"book": k[0], "clock": k[1], "n": k[2], "side": k[3], "policy": k[4], **v}
                for k, v in sorted(unknown_by_key.items(), key=lambda kv: str(kv[0]))
            ],
        },
        "reconciliation": {
            "identity": "sum(member net) == sum(daily ret) per cell on the paired dates",
            "max_abs_diff": f(max_recon),
            "core_members_cross_check": {
                "identity": "the staged claim books equal the core per-day members books, "
                "per cell, on the same paired dates",
                "max_abs_diff": f(max_core),
                "claim_count_gaps": len(claim_count_gap),
                "by_key": core_recon,
            },
            "by_key": recon,
        },
        "release_before_check": {
            "note": "per-ruler counts recomputed here must reproduce the corrected artifact's "
            "own 2112 release cells exactly",
            **rb_check,
        },
        "cells": sorted(
            all_cells, key=lambda c: (c["clock"], c["n"], c["side"], c["policy"], c["book"])
        ),
        "cells_count": len(all_cells),
        "paired_book_comparison": sorted(
            paired_rows, key=lambda r: (r["clock"], r["n"], r["side"], r["policy"])
        ),
        "panel_facts": panel_facts,
        "mechanisms_needing_tests": mech_list,
        "caveats": [
            "Every dollar here is a measured realized-book quantity on discovery-half dates; "
            "nothing is projected.",
            "Break-even uses disjoint levers. The price lever is the no-excursion claims' GROSS "
            "price loss with fees held constant; the cost lever is all modeled fees with gross "
            "held constant. They sum without overlap because net = gross_move - fees. The "
            "no-excursion NET loss is never used as a lever, since it already contains those "
            "fees.",
            "The binding requirement is the tail rent needed with every other outcome held "
            "fixed, which equals the cell deficit. This does not depend on any hypothetical "
            "zero-dud or zero-fee world. The best retrospective cell is NOT presented as a "
            "strategy, and no ex-post class is turned into an entry rule.",
            "Observed-window MFE is an ex-post class on the discovery panel. It is used here as "
            "a loss-attribution label and a ruler only. No cell in this file asserts that an "
            "MFE class is tradable, and no policy promise is derived from oracle MFE.",
            "The 50/75bps per-side cost is the modeled entry+exit friction on the filled "
            "capital the replay actually traded. It is a cost of realized turnover, not a fee "
            "overcharge, and it is not evidence that the asset itself is unprofitable.",
            "The fee lever is the arithmetic size of friction actually paid. It is NOT a "
            "proposal to lower per-side friction below the modeled level, and no lower-turnover "
            "price or exposure effect is assumed: any such effect must be measured. A stop-only "
            "book that buys once and sells once has no round trip to remove, so a longer minimum "
            "hold cannot reduce its fee.",
            "Cells are ALTERNATIVE correlated books on a shared date set. Any sum across cells "
            "is an arithmetic cross-case aggregate, NOT portfolio PnL, and is not additive "
            "across policies. The reportable quantities are the per-cell and per-paired-day "
            "dollars.",
            "Removing the best days from an already negative cell mechanically lowers the "
            "remaining mean and is a tautology, not evidence. Day sensitivity is therefore read "
            "in the worst-day direction, on identical dates and the same claim population, and "
            "no best-day result is reported as a finding. The removed dollars and the removed "
            "claim counts are always taken from the same sorted rows, and the primary mean is "
            "original portfolio dollars per retained book-date, not per claim-slot.",
            "The harvestability first-push ruler is ex-post; release-before counts and dollars "
            "are a diagnostic, never a deployable signal, and never a survivor-conditioning "
            "argument about a prior book.",
            "Cells are grouped only by the fixed ruler (clock, N, cost, policy). No "
            "max-order label, no name classifier, and no whole-roster verdict is asserted.",
            "Only the discovery half is read; no validation outcome or FREEZE marker was touched.",
        ],
    }


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument(
        "--artifacts-root",
        type=Path,
        default=root / "factory/artifacts",
        help="directory holding owned_claim_loss_sources.json",
    )
    ap.add_argument(
        "--out", type=Path, default=root / "factory/artifacts/owned_claim_money_gap.json"
    )
    ap.add_argument("--limit", type=int, help="smoke: use only the first N staged test days")
    a = ap.parse_args()

    art = json.loads((a.artifacts_root / ARTIFACT_NAME).read_text())
    days = list(art["test_days"])
    if a.limit:
        days = days[: a.limit]

    report = build_report(a.data_root, a.artifacts_root, days, full_panel=not a.limit)
    if a.limit:
        report["caveats"].insert(
            0,
            f"SMOKE RUN: only the first {a.limit} staged test days were "
            "consolidated. Not a full-panel result.",
        )
    a.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = a.out.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    tmp.replace(a.out)
    print(
        "money gap written",
        a.out,
        "days",
        report["test_days_count"],
        "cells",
        report["cells_count"],
        "max_paired_recon",
        report["reconciliation"]["max_abs_diff"],
        "paired_excluded",
        report["unknown"]["paired_excluded_claims"],
        "whole_panel_censored",
        report["unknown"]["whole_panel_censored_claims"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
