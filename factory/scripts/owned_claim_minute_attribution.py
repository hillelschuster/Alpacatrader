#!/usr/bin/env python3
"""FULL-MINUTE OWNED-CLAIM DOLLAR ATTRIBUTION — one independent money read per cell.

What this consolidates
----------------------
The COMPLETE full-minute actual books under ``owned_claim/replay_minute`` (every
published OOF date, every admission clock, both roster sizes, both modeled leg costs,
all eleven policies) are decomposed into ORIGINAL-PORTFOLIO-CAPITAL dollars, cell by
cell, and compared on EXACTLY THE SAME DATES against the two prior realized books
(``replay`` = old fixed value-iteration book, ``replay_policy`` = full-policy-return
book), against the two core rulers ``hold`` and ``fade``, and against ``cash0`` — the
zero-dollar reference line that deploying no capital earns by construction.

Per cell ``(clock, N, side cost, policy)`` it answers, in dollars:

  * ``roundtrip_tax``        entry fee actually sunk at admission + exit fee actually
                             charged, split so the SUNK part can never be confused with
                             a decision-addressable one.
  * ``winner_tail``          net-positive vs net-negative claims, and the observed-
                             window MFE class split (dud / mid / tail / masked) with
                             preserved vs destroyed tail rent.
  * ``release_before``       how many claims the book sold BEFORE the harvestability
                             first-push ruler, and by more than 5 / 30 minutes.
  * ``exit_timing``          release minute and minutes-held against the session window.
  * ``exposure``             recorded exposure snapshots (with UNKNOWN, never a fake 0).
  * ``concentration``        top/bottom-k day and name concentration plus both-sided
                             day sensitivity on one fixed date set.

Coarse vs minute: an observed difference, not a cadence attribution
-----------------------------------------------------------------
This file reports how the money DIFFERED between the coarse books and the full-minute
grid on matched dates, and which quantities are common to both sides. It does not
attribute that difference to decision FREQUENCY. The coarse and minute books differ in
the decision algorithm and in the value contract itself (targets, training density,
history), so frequency is not isolated by any comparison here. Isolating it would
require replaying the SAME forecasts at thinned versus dense decision times, which is not
done and not claimed. The decomposition carries the measured quantities it does support:
whether the entry fee is invariant across all eleven policies (it is — the entry price and
the equal-dollar 1/N admission are policy-independent), how many round trips each policy
pays, how wide the fee spread across policies is on a fixed date, and how each paired delta
splits into ``d_gross`` and ``d_fees``. Because the admission is common to every policy,
the sunk entry tax cancels in every paired comparison.

Contracts
---------
* Money unit is ORIGINAL PORTFOLIO CAPITAL: a member ``net_pnl`` is a portfolio-dollar
  cashflow and ``net = gross_move - fees`` holds per claim and per cell (asserted, not
  assumed). ``sum(member net) == sum(daily ret)`` per cell on the paired dates is
  asserted too.
* Nothing is pooled across clocks, across N (3 and 5 are separate ownership universes),
  or across side costs. Concentration is per ACTUAL BOOK per date.
* UNKNOWN is preserved: a censored book-date is EXCLUDED from every dollar sum and
  COUNTED. A blocked entry slot is KNOWN cash at zero dollars and is distinct from
  UNKNOWN. A missing recorded exposure is UNKNOWN, never 0.
* The MFE class and the first-push ruler are LOOKAHEAD ATTRIBUTION LABELS read off the
  discovery-day panel. They classify dollars that were already realized. They are never
  a feature, never an entry or exit rule, and no cell is asserted to be tradable.
* Different horizons are POLICY VIEWS on the same roster. Every view is reported
  beside every other one. No post-TEST best view is selected, named or ranked.
* Cost arithmetic never removes an ALREADY-SUNK entry fee. The exit-timing lever is
  the exit fee and the exit price; the sunk entry fee is reported as common to all
  policies and is excluded from the addressable cost lever.
* Only the discovery half is read. No protected outcome, no bot order, no deploy flag.

Outputs under ``--out``: one JSON artifact plus a resumable per-day claim stage beside
it. ``--limit N`` runs a NON-EVIDENCE smoke over the first N published dates and
labels every number in it as non-evidence.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
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

SCRIPT = Path(__file__).resolve()

MINUTE_BOOK = "replay_minute"
# The KEY of each mapping is the upstream artifact's own book label, because
# loss.inputs.books and the money-gap artifact index these books by exactly that name. It
# is the declared lineage record that must be looked up, so the label here is the official
# one and is never aliased to something more readable.
PRIOR_BOOKS = {"fvi": "replay", "policy_return": "replay_policy"}
LOSS_STAGE = "loss_sources_stage"
HARVESTABILITY = "harvestability/events.parquet"

LOSS_ARTIFACT = "owned_claim_loss_sources.json"
GAP_ARTIFACT = "owned_claim_money_gap.json"

CLOCKS = (540, 560, 569, 571)
NS = (3, 5)
SIDES = (0.005, 0.0075)
N_POLICY = 11  # hold, fade, and nine published minute views, all resolved or none

SNAPSHOTS = (585, 630, 690, 780)

MFE_EDGES = ((0.10, "MFElt10"), (0.30, "MFE10_30"), (1.00, "MFE30_100"))
MFE_TOP = "MFE100"
DUD_CLASS = MFE_EDGES[0][1]
MID_CLASS = MFE_EDGES[1][1]
TAIL_CLASSES = (MFE_EDGES[2][1], MFE_TOP)
MASKED_CLASSES = ("incomplete_window", "unknown_mfe")

REL_ENDS = (780, 900)
REL_THRESHOLDS = (0.05, 0.15, 0.30)
PUSH_THRESHOLD = 0.05  # the +5% first-push ruler; +5/+30 are MINUTE margins on it
RELEASE_REASONS = ("state_release", "fade")
TERMINAL_REASON = "terminal"

TAIL_K = (5, 10)
KIND = "DISCOVERY-DIAGNOSTIC-NOT-ALPHA"

KEYS = ["day", "clock", "rank", "ticker"]
CELL = ["clock", "n", "side", "policy"]
CLAIM_KEY = ["day", "clock", "n", "side", "policy", "ticker"]

# The producer's execution-journal schema, declared here so a legitimately empty date is
# represented exactly instead of as an ad-hoc frame with different columns. It mirrors the
# engine's own fills record; the rename to (side, leg) happens at the read site.
FILL_SCHEMA = {
    "day": pl.String,
    "clock": pl.Int64,
    "n": pl.Int64,
    "side_cost": pl.Float64,
    "policy": pl.String,
    "ticker": pl.String,
    "rank": pl.Int64,
    "side": pl.String,
    "decision_et": pl.Int64,
    "exec_et": pl.Int64,
    "px": pl.Float64,
    "shares_per_capital": pl.Float64,
    "gross_fraction": pl.Float64,
    "fee_fraction": pl.Float64,
    "reason": pl.String,
    "participation": pl.Float64,
}

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
    "buy_gross",
    "sell_gross",
    "initial_q",
    "entry_et",
    "release_exec",
    "exit_et",
    "n_sells",
    "n_buys",
    "reentries",
    "remaining_shares",
    "known",
    "unfilled",
    "mfe_full",
    "gap_full",
    "mfe_class_full",
    "mfe_class_lt13",
    "end_full",
    "roster_status",
    "roster_fill_px",
    "first_push_t",
]


# --------------------------------------------------------------------- helpers
def f(x):
    """JSON-safe float: NaN/inf is UNKNOWN, never a silent zero."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if (x != x or x in (float("inf"), float("-inf"))) else x


def ratio(num, den):
    if num is None or den is None or den == 0:
        return None
    return f(num / den)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def owned(data_root: Path) -> Path:
    return ls.v2_dir(data_root) / "owned_claim"


def book_dir(data_root: Path, book: str) -> Path:
    return owned(data_root) / book


def stage_dir(data_root: Path) -> Path:
    return owned(data_root) / LOSS_STAGE


def median(values) -> float | None:
    arr = np.asarray(
        [v for v in values if v is not None and np.isfinite(float(v))], dtype=np.float64
    )
    return float(np.median(arr)) if arr.size else None


def _num(x) -> float | None:
    """A scalar as float, or None when it is missing/NaN. Never a silent zero."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if (v != v or v in (float("inf"), float("-inf"))) else v


def to_floats(values) -> np.ndarray:
    """A nullable column as float64 with NaN where the value is missing.

    The minute-time columns come off the staged parquet as nullable Int64, so a missing
    value is None rather than NaN and a bare ``np.isfinite`` would raise on it. Every
    minute-time comparison in this file goes through here, so a null is never read as a
    zero minute.
    """
    return np.asarray(
        [np.nan if _num(v) is None else float(_num(v)) for v in values], dtype=np.float64
    )


# ------------------------------------------------------------------ tail / day tools
def tail_concentration(frame: pl.DataFrame, group_col: str, total: float, k: int) -> dict:
    """Top/bottom-k dollar mass along one grouping. Reported, never optimized."""
    if not frame.height:
        return {"groups": 0, "slots": 0, "k": k}
    g = (
        frame.group_by(group_col)
        .agg(pl.col("net").sum().alias("net"), pl.len().alias("slots"))
        .sort("net", descending=True)
    )
    nets, slots = g["net"].to_numpy(), g["slots"].to_numpy()
    n_groups, total_slots = g.height, int(frame.height)
    top = float(nets[:k].sum())
    bottom = float(nets[-k:].sum())
    top_slots = int(slots[:k].sum())
    bottom_slots = int(slots[-k:].sum())
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
        "ev_ex_top_k": f((total - top) / (n_groups - min(k, n_groups)))
        if n_groups - min(k, n_groups) > 0
        else None,
        "ev_ex_bottom_k": f((total - bottom) / (n_groups - min(k, n_groups)))
        if n_groups - min(k, n_groups) > 0
        else None,
    }


def day_sensitivity(frame: pl.DataFrame, total: float, k: int) -> dict:
    """Both-sided day removal on ONE fixed date set and ONE claim population.

    Removing the best days from an already-negative cell mechanically lowers the mean
    and proves nothing, so the worst-k direction is the diagnostic one. Removed
    dollars and removed claim counts always come from the SAME sorted rows.

    Primary unit: original portfolio dollars per RETAINED BOOK-DATE, consistent with
    cash_net / paired_days. The per-claim-slot mean is reported separately and never
    mixed with it.
    """
    if not frame.height:
        return {"days": 0, "k": 0, "unit": "original portfolio dollars per retained book-date"}
    d = frame.group_by("day").agg(pl.col("net").sum().alias("net"), pl.len().alias("slots"))
    srt = d.sort("net", descending=True)
    nets, slots = srt["net"].to_numpy(), srt["slots"].to_numpy()
    n_days = srt.height
    kk = min(k, n_days)
    top, bottom = float(nets[:kk].sum()), float(nets[-kk:].sum())
    top_slots, bottom_slots = int(slots[:kk].sum()), int(slots[-kk:].sum())
    kept = n_days - kk
    kept_slots_top = int(slots.sum()) - top_slots
    kept_slots_bottom = int(slots.sum()) - bottom_slots
    return {
        "days": int(n_days),
        "k": kk,
        "unit": "original portfolio dollars per retained book-date",
        "total_net": f(total),
        "best_k_days_sum": f(top),
        "best_k_days_slots": top_slots,
        "worst_k_days_sum": f(bottom),
        "worst_k_days_slots": bottom_slots,
        "mean_ex_best_k_days": f((total - top) / kept) if kept > 0 else None,
        "mean_ex_worst_k_days": f((total - bottom) / kept) if kept > 0 else None,
        "mean_ex_best_k_days_per_claim_slot": f((total - top) / kept_slots_top)
        if kept_slots_top > 0
        else None,
        "mean_ex_worst_k_days_per_claim_slot": f((total - bottom) / kept_slots_bottom)
        if kept_slots_bottom > 0
        else None,
        "removed_dollars_and_counts_share_the_same_rows": True,
        # Only meaningful for a cell that actually has a deficit: on a positive cell the
        # remaining mean is non-negative by construction, so the flag would be vacuously
        # true and would read as a finding where there is nothing to explain.
        "cell_has_a_deficit": bool(total < 0),
        "deficit_carried_by_few_bad_days": (
            bool(kept > 0 and (total - bottom) / kept >= 0.0) if total < 0 else None
        ),
        "reading": "mean_ex_best_k_days is mechanically worse for an already-negative cell "
        "and is a tautology, not evidence; mean_ex_worst_k_days is the diagnostic "
        "direction. Both use the same cell dates and the same claim population.",
    }


def class_bucket(frame: pl.DataFrame, classes: tuple) -> dict:
    b = frame.filter(pl.col("mfe_class_full").is_in(list(classes)))
    return {
        "claims": b.height,
        "net": f(b["net"].sum()) if b.height else 0.0,
        "gross_move": f(b["gross"].sum()) if b.height else 0.0,
        "fees": f(b["fees"].sum()) if b.height else 0.0,
        "gross_loss_dollars": f(-b["gross"].clip(upper_bound=0.0).sum()) if b.height else 0.0,
        "net_loss_dollars": f(-b["net"].clip(upper_bound=0.0).sum()) if b.height else 0.0,
        "win_claims": int((b["net"] > 0).sum()) if b.height else 0,
        "lose_claims": int((b["net"] <= 0).sum()) if b.height else 0,
    }


# ------------------------------------------------------------------- pin verification
def verify_inputs(
    data_root: Path, artifacts_root: Path, days: list[str]
) -> tuple[dict, dict, dict]:
    """Refuse to mix panels: every consumed book must match the lineage that produced it.

    Lineage is validated against the EXECUTION VINTAGE recorded inside the artifacts, not
    against the current core source hash. The core is expected to keep evolving after a
    book was executed; re-deriving a book's identity from today's engine source would
    invalidate correct archived books, and that is not what a pin is for.

    Three checks are performed and they are not interchangeable:
      * declared -- the artifact states a hash for this exact file; we recompute it.
      * linked   -- the money-gap artifact names the loss artifact it was built on.
      * staged   -- every per-day ruler marker carries that loss run_id/producer pair.
    A book with no declared upstream hash (the minute book postdates the loss-source stage
    it reads) is recorded as RECORDED_NOT_LINEAGE_VERIFIED and says so in the artifact.
    """
    pins: dict = {"books": {}, "lineage_checks": {}}
    for book in (MINUTE_BOOK, *PRIOR_BOOKS.values()):
        bdir = book_dir(data_root, book)
        daily = bdir / "daily.parquet"
        if not daily.exists():
            raise SystemExit(f"{book} realized book missing: {daily}")
        pins["books"][book] = {
            "daily_sha256": sha256_file(daily),
            "days_dir": str(bdir / "days"),
        }

    loss_path = artifacts_root / LOSS_ARTIFACT
    gap_path = artifacts_root / GAP_ARTIFACT
    for p in (loss_path, gap_path):
        if not p.exists():
            raise SystemExit(f"required upstream artifact missing: {p}")
    loss = json.loads(loss_path.read_text())
    gap = json.loads(gap_path.read_text())
    if loss.get("kind") != KIND or gap.get("kind") != KIND:
        raise SystemExit("upstream artifacts carry an unexpected kind")

    # ---- declared: the prior books' files, against the loss artifact that consumed them.
    # The artifact states WHERE each book lived and WHICH metadata it was replayed under, so
    # the paths are taken from that declaration rather than re-derived from a naming
    # convention. A convention would silently drift from the record this gate exists to check.
    declared_books = loss["inputs"]["books"]
    unknown_books = sorted(set(declared_books) - set(PRIOR_BOOKS))
    if unknown_books:
        raise SystemExit(
            f"{LOSS_ARTIFACT} declares books this producer does not read: {unknown_books}; "
            "refusing to publish a lineage that silently omits a declared book"
        )
    for name in PRIOR_BOOKS:
        rec = declared_books.get(name)
        if rec is None:
            raise SystemExit(f"{LOSS_ARTIFACT} declares no hash record for the {name} book")
        declared_root = rec.get("root")
        declared_meta = rec.get("model_metadata_path")
        if not declared_root or not declared_meta:
            raise SystemExit(
                f"{LOSS_ARTIFACT} does not declare where the {name} book or its model metadata "
                "lives; refusing to guess a path for a file this gate must verify"
            )
        root = Path(declared_root)
        targets = {
            "daily_sha256": root / "daily.parquet",
            "summary_sha256": root / "summary.parquet",
            "model_metadata_sha256": Path(declared_meta),
        }
        for field, path in targets.items():
            if not path.exists():
                raise SystemExit(f"{name} {field} target missing: {path}")
            got = sha256_file(path)
            if rec.get(field) != got:
                raise SystemExit(
                    f"{name} {field} has drifted from the vintage recorded in {LOSS_ARTIFACT} "
                    f"({rec.get(field)} != {got}); refusing to pair a re-executed book with an "
                    "older claim decomposition"
                )
            pins["lineage_checks"][f"{name}.{field}"] = {
                "declared": rec.get(field),
                "observed": got,
            }
        consumed = pins["books"][PRIOR_BOOKS[name]]["daily_sha256"]
        if consumed != rec["daily_sha256"]:
            raise SystemExit(
                f"{name} consumed daily book differs from the declared execution vintage "
                f"({rec['daily_sha256']} != {consumed}); refusing to verify a different copy"
            )
        pins["lineage_checks"][f"{name}.consumed_daily_sha256"] = {
            "declared": rec["daily_sha256"],
            "observed": consumed,
            "path": str(book_dir(data_root, PRIOR_BOOKS[name]) / "daily.parquet"),
        }

    # ---- linked: the money-gap artifact must name the exact loss artifact we read
    loss_sha = sha256_file(loss_path)
    declared_loss = gap["inputs"].get("artifact_sha256")
    if declared_loss != loss_sha:
        raise SystemExit(
            f"{GAP_ARTIFACT} was not built on the {LOSS_ARTIFACT} now on disk "
            f"({declared_loss} != {loss_sha}); refusing a mixed lineage"
        )
    pins["lineage_checks"]["money_gap_links_loss_artifact"] = {
        "declared": declared_loss,
        "observed": loss_sha,
    }

    sd = stage_dir(data_root)
    if not sd.is_dir():
        raise SystemExit(f"loss-source claim stage missing: {sd}")

    # ---- staged: every per-day ruler marker must carry that loss run_id/producer pair
    loss_run = loss.get("run_id")
    loss_producer = loss.get("producer_sha256")
    staged_checked = 0
    for day in days:
        marker = sd / f"{day}.json"
        claims_path = sd / f"{day}.claims.parquet"
        if not marker.exists() or not claims_path.exists():
            raise SystemExit(f"staged ruler book incomplete for {day} (missing marker or claims)")
        meta = json.loads(marker.read_text())
        if meta.get("run_id") != loss_run or meta.get("producer_sha256") != loss_producer:
            raise SystemExit(
                f"{day}: staged ruler marker carries a foreign run_id/producer "
                f"({meta.get('run_id')}/{meta.get('producer_sha256')}); refusing stale definitions"
            )
        staged_checked += 1
    pins["lineage_checks"]["staged_ruler_markers"] = {
        "days_checked": staged_checked,
        "run_id": loss_run,
        "producer_sha256": loss_producer,
        "rule": "every staged day's marker must carry the loss artifact's own run_id and "
        "producer hash",
    }

    harv = ls.v2_dir(data_root) / HARVESTABILITY
    if not harv.exists():
        raise SystemExit(f"harvestability events missing: {harv}")
    rec = loss["inputs"]["harvestability_events_sha256"]
    got = sha256_file(harv)
    if rec != got:
        raise SystemExit(
            f"harvestability events drifted from {LOSS_ARTIFACT} ({rec} != {got}); refusing"
        )

    pins["books"][MINUTE_BOOK]["lineage_status"] = "RECORDED_NOT_LINEAGE_VERIFIED"
    pins["books"][MINUTE_BOOK]["lineage_note"] = (
        "no upstream artifact declares a hash for the minute book, because it was produced "
        "after the loss-source stage it reads. Its sha256 is recorded here so this report is "
        "reproducible, but it is NOT verified against a declared vintage and must not be read "
        "as if it were."
    )
    for bdir_name in PRIOR_BOOKS.values():
        pins["books"][bdir_name]["lineage_status"] = "DECLARED_VINTAGE_VERIFIED"

    pins.update(
        {
            "loss_artifact_sha256": loss_sha,
            "money_gap_artifact_sha256": sha256_file(gap_path),
            "loss_producer_sha256": loss_producer,
            "money_gap_producer_sha256": gap.get("producer_sha256"),
            "loss_run_id": loss_run,
            "harvestability_events_sha256": got,
            "split_sha256": loss["inputs"]["split_sha256"],
            "stage_dir": str(sd),
            "validation_target": (
                "the execution vintage recorded inside the upstream artifacts. The live core "
                "source is deliberately NOT hashed here: the engine keeps evolving, and "
                "re-deriving a book's identity from today's source would invalidate correct "
                "archived books."
            ),
        }
    )
    return loss, gap, pins


def published_days(data_root: Path) -> tuple[list[str], dict[str, int]]:
    """Exact OOF dates from the minute book's own daily.parquet. No glob, no other scan."""
    daily = pl.read_parquet(book_dir(data_root, MINUTE_BOOK) / "daily.parquet", columns=["day"])
    days = sorted(daily["day"].unique().to_list())
    locked = set(ls.discovery_days(data_root))
    if not set(days) <= locked:
        raise SystemExit("minute book carries non-discovery dates; refusing")
    fold_of: dict[str, int] = {}
    monthly = pl.read_parquet(
        book_dir(data_root, MINUTE_BOOK) / "daily.parquet", columns=["day", "fold"]
    )
    for d, fo in monthly.select(["day", "fold"]).unique().iter_rows():
        if fo is not None:
            fold_of[d] = int(fo)
    return days, fold_of


# -------------------------------------------------------------------- per-day staging
def stage_day(day: str, data_root: Path) -> pl.DataFrame:
    """One date's minute-book claim records, with its fill split and claim-level rulers.

    The MFE class map and the first-push signal are CLAIM-LEVEL quantities: they do not
    depend on the policy, so they are read once per claim key from the pinned loss-source
    stage and the harvestability events, never re-derived per policy.
    """
    md = book_dir(data_root, MINUTE_BOOK) / "days"
    daily = pl.read_parquet(md / f"{day}.daily.parquet")
    members = pl.read_parquet(md / f"{day}.members.parquet")
    fills_path = md / f"{day}.fills.parquet"
    if fills_path.exists():
        fills = pl.read_parquet(fills_path).rename({"side_cost": "side", "side": "leg"})
    else:
        # The producer deliberately skips writing an empty journal when a date executes
        # nothing, so an absent file is a legitimate producer output for a known-cash /
        # censored date. It is accepted ONLY after the day's own books are shown to
        # report zero executions and no contradictory member money; a missing journal for
        # a book that reports fills is a hard failure, not a silent empty book.
        orders = int(daily["orders"].fill_null(0).sum()) if "orders" in daily.columns else None
        if orders != 0:
            raise SystemExit(
                f"{day}: execution journal {fills_path} is absent but the day reports "
                f"{orders} executed orders; refusing to invent a fill record"
            )
        for col in ("gross_pnl", "fees"):
            if col in members.columns and members[col].abs().fill_null(0.0).sum() > 0:
                raise SystemExit(
                    f"{day}: execution journal absent but member {col} is non-zero; "
                    "the date's money and its executions contradict each other"
                )
        if (
            "remaining_shares" in members.columns
            and members["remaining_shares"].abs().fill_null(0.0).sum() > 0
        ):
            raise SystemExit(
                f"{day}: execution journal absent but members carry remaining shares; "
                "a position cannot exist without its execution records"
            )
        fills = pl.DataFrame(schema=FILL_SCHEMA)

    recorded = tuple(s for s in SNAPSHOTS if f"exposure_{s}" in daily.columns)
    # A source may omit a snapshot that is calendar-invalid for the session. Project the
    # columns that EXIST by name and add the absent ones as explicit nulls in their
    # natural position, so the schema is identical every day and a missing date is never
    # silently read as 0.
    want = ["day", "clock", "n", "side", "policy", "ret", "unknown", "orders"]
    want += [f"exposure_{s}" for s in recorded]
    absent = [s for s in SNAPSHOTS if s not in recorded]
    book_daily = daily.select(want)
    if absent:
        book_daily = book_daily.with_columns(
            *[pl.lit(None, dtype=pl.Float64).alias(f"exposure_{s}") for s in absent]
        )

    claims = members.join(book_daily, on=["day", "clock", "n", "side", "policy"], how="left")

    if fills.height:
        split = fills.group_by(CLAIM_KEY).agg(
            pl.col("fee_fraction").filter(pl.col("leg") == "buy").sum().alias("entry_fee"),
            pl.col("fee_fraction").filter(pl.col("leg") == "sell").sum().alias("exit_fee"),
            pl.col("gross_fraction").filter(pl.col("leg") == "buy").sum().alias("buy_gross"),
            pl.col("gross_fraction").filter(pl.col("leg") == "sell").sum().alias("sell_gross"),
            pl.col("shares_per_capital")
            .filter(pl.col("reason") == "initial")
            .sum()
            .alias("initial_q"),
            pl.col("exec_et").filter(pl.col("leg") == "buy").min().alias("entry_et"),
            pl.col("exec_et")
            .filter((pl.col("leg") == "sell") & pl.col("reason").is_in(list(RELEASE_REASONS)))
            .min()
            .alias("release_exec"),
            pl.col("exec_et").filter(pl.col("leg") == "sell").max().alias("exit_et"),
            (pl.col("leg") == "sell").sum().alias("n_sells"),
            (pl.col("leg") == "buy").sum().alias("n_buys"),
        )
        claims = claims.join(split, on=CLAIM_KEY, how="left")
    else:
        claims = claims.with_columns(
            *[
                pl.lit(None, dtype=pl.Float64).alias(c)
                for c in ("entry_fee", "exit_fee", "buy_gross", "sell_gross", "initial_q")
            ],
            pl.lit(None, dtype=pl.Int64).alias("entry_et"),
            pl.lit(None, dtype=pl.Int64).alias("release_exec"),
            pl.lit(None, dtype=pl.Int64).alias("exit_et"),
            *[pl.lit(0, dtype=pl.Int64).alias(c) for c in ("n_sells", "n_buys")],
        )

    claims = claims.rename({"net_pnl": "net", "gross_pnl": "gross"}).with_columns(
        pl.col("unknown").alias("book_unknown")
    )

    # A claim-level ruler map, deduped from the pinned loss-source stage. It is the same
    # panel MFE the money-gap artifact was built on, so the classes here are the classes
    # there; nothing is recomputed and nothing is re-labelled.
    ruler_src = stage_dir(data_root) / f"{day}.claims.parquet"
    if not ruler_src.exists():
        raise SystemExit(f"pinned loss-source claim stage missing for {day}: {ruler_src}")
    ruler = pl.read_parquet(
        ruler_src,
        columns=[
            "day",
            "clock",
            "rank",
            "ticker",
            "mfe_full",
            "gap_full",
            "mfe_class_full",
            "mfe_class_lt13",
            "end_full",
            "roster_status",
            "roster_fill_px",
        ],
    ).unique(subset=KEYS, keep="first")
    conflicted = (
        ruler.group_by(KEYS)
        .agg(pl.col("mfe_class_full").n_unique().alias("u"))
        .filter(pl.col("u") > 1)
    )
    if conflicted.height:
        raise SystemExit(f"{day}: claim-level MFE class is not policy-invariant; refusing")

    claims = claims.join(ruler, on=KEYS, how="left")
    missing_ruler = claims.join(ruler.select(KEYS), on=KEYS, how="anti").height
    if missing_ruler:
        raise SystemExit(f"{day}: {missing_ruler} minute claims carry no pinned ruler row")

    # First-push signal: the earliest discovery-panel minute a claim touched +5% on any
    # published ruler. An attribution label read after the fact, never an action input.
    harv = pl.read_parquet(
        ls.v2_dir(data_root) / HARVESTABILITY, columns=["day", "clock", "rank", "ticker", "t"]
    )
    first_push = (
        harv.filter(pl.col("day") == day)
        .group_by(KEYS)
        .agg(pl.col("t").min().alias("first_push_t"))
    )
    claims = claims.join(first_push, on=KEYS, how="left")

    # UNKNOWN cashflows are censored at the BOOK level and can never leak into a sum.
    for c in ("net", "gross", "entry_fee", "exit_fee", "buy_gross", "sell_gross"):
        claims = claims.with_columns(
            pl.when(pl.col("book_unknown")).then(None).otherwise(pl.col(c)).alias(c)
        )
    # A claim with no fill at all (a blocked or missing roster slot) carries no fee legs;
    # for a KNOWN claim those legs are real and must total. Only known claims get a fee.
    claims = claims.with_columns(
        (pl.col("net").is_not_null()).alias("known"),
        (pl.col("roster_status") == "blocked").fill_null(False).alias("unfilled"),
    )
    claims = claims.with_columns(
        pl.when(pl.col("known"))
        .then(pl.col("entry_fee").fill_null(0.0))
        .otherwise(None)
        .alias("entry_fee"),
        pl.when(pl.col("known"))
        .then(pl.col("exit_fee").fill_null(0.0))
        .otherwise(None)
        .alias("exit_fee"),
    )
    claims = claims.with_columns(
        pl.when(pl.col("known"))
        .then(pl.col("entry_fee") + pl.col("exit_fee"))
        .otherwise(None)
        .alias("fees")
    )
    keep = CLAIM_COLS + [f"exposure_{s}" for s in SNAPSHOTS] + ["ret", "unknown", "orders"]
    return claims.select([c for c in keep if c in claims.columns])


def assert_claim_identities(claims: pl.DataFrame, day: str) -> float:
    chk = claims.filter(pl.col("known"))
    if not chk.height:
        return 0.0
    worst = 0.0
    for expr in (
        pl.col("net") - pl.col("gross") + pl.col("fees"),
        pl.col("gross") - pl.col("sell_gross").fill_null(0.0) + pl.col("buy_gross").fill_null(0.0),
        pl.col("fees") - pl.col("entry_fee").fill_null(0.0) - pl.col("exit_fee").fill_null(0.0),
    ):
        worst = max(worst, float(chk.select(expr.abs().max()).item()))
    if worst >= 1e-9:
        raise AssertionError(f"{day}: claim money identity breaks by {worst}")
    return worst


# ------------------------------------------------------------------- paired date sets
def date_sets(
    minute_daily: pl.DataFrame, prior_daily: dict[str, pl.DataFrame], days: list[str]
) -> dict:
    """Exact same-date pairings. Three sets, each used for exactly one comparison.

    own   -- all eleven minute policies resolve for that (clock, N, cost). Used for the
             minute book's own money decomposition.
    cross -- the same, AND all eleven policies resolve in BOTH prior books. Used only for
             the minute-vs-prior paired deltas, so no comparison borrows a date one side
             could not resolve.
    """

    def ok(frame: pl.DataFrame) -> pl.DataFrame:
        return (
            frame.filter(pl.col("day").is_in(days))
            .group_by(["day", "clock", "n", "side"])
            .agg((pl.col("ret").is_not_null().all() & (pl.len() == N_POLICY)).alias("all_known"))
            .filter(pl.col("all_known"))
            .select(["day", "clock", "n", "side"])
        )

    own = ok(minute_daily)
    cross = own
    for frame in prior_daily.values():
        cross = cross.join(ok(frame), on=["day", "clock", "n", "side"], how="inner")
    return {
        "own": own.sort(["clock", "n", "side", "day"]),
        "cross": cross.sort(["clock", "n", "side", "day"]),
    }


def ownership_state(frame: pl.DataFrame) -> dict:
    """Three ownership states from ACTUAL executed legs, not from a missing release field.

    A roster slot that never filled has no entry leg. It is KNOWN CASH at exactly zero
    dollars and was never a position, so counting it as "held to terminal" would describe a
    holding that never existed:

      never_owned              no buy leg at all
      entered_released_preSE  bought, sold strictly before the session-end boundary
      entered_held_throughSE  bought, still held to the boundary -- INCLUDING a discretionary
                               sell whose execution minute IS the boundary, because the
                               shares were owned right up to it

    The source of each zero-dollar slot is reported from the roster status rather than
    assumed, so a blocked admission stays distinguishable from any other known zero claim.
    """
    if not frame.height:
        return {
            "never_owned_known_cash": 0,
            "entered_released_before_session_end": 0,
            "entered_held_through_session_end": 0,
            "entered_claims": 0,
            "known_claims": 0,
            "entered_without_a_resolved_exit_leg": 0,
            "states_sum_to_known_claims": True,
            "never_owned_by_roster_status": {},
        }
    buys = frame["n_buys"].fill_null(0).to_numpy() > 0
    exit_f = to_floats(frame["exit_et"])
    se = to_floats(frame["end_full"])
    ever_owned = buys & np.isfinite(exit_f)
    released_pre = ever_owned & np.isfinite(se) & (exit_f < se)
    held_through = ever_owned & ~released_pre
    never = ~buys
    unresolved = buys & ~ever_owned
    by_status: dict[str, int] = {}
    if "roster_status" in frame.columns and never.any():
        sub = frame.filter(pl.Series(never))
        for row in sub.group_by("roster_status").agg(pl.len().alias("c")).to_dicts():
            by_status[str(row["roster_status"])] = int(row["c"])
    return {
        "never_owned_known_cash": int(never.sum()),
        "entered_released_before_session_end": int(released_pre.sum()),
        "entered_held_through_session_end": int(held_through.sum()),
        "entered_claims": int(ever_owned.sum()),
        "known_claims": int(frame.height),
        "entered_without_a_resolved_exit_leg": int(unresolved.sum()),
        "states_sum_to_known_claims": (
            int(never.sum()) + int(ever_owned.sum()) + int(unresolved.sum()) == int(frame.height)
        ),
        "never_owned_by_roster_status": by_status,
        "boundary_rule": "a discretionary sell executing AT session_end counts as held through "
        "session_end: the shares were owned right up to that minute",
        "never_owned_meaning": "known cash at exactly zero dollars, never a position and never "
        "a zero-minute holding period",
    }


# --------------------------------------------------------------------- cell builds
def build_cells(claims: pl.DataFrame, datekeys: pl.DataFrame) -> dict:
    """Per-cell dollar decomposition on ONE fixed date set."""
    on = claims.join(
        datekeys.select(["day", "clock", "n", "side"]), on=["day", "clock", "n", "side"], how="semi"
    )
    known = on.filter(pl.col("known"))
    agg = known.group_by(CELL).agg(
        pl.len().alias("known_claims"),
        pl.col("day").n_unique().alias("paired_days"),
        pl.col("net").sum().alias("net"),
        pl.col("gross").sum().alias("gross_move"),
        pl.col("fees").sum().alias("fees"),
        pl.col("entry_fee").sum().alias("entry_fee"),
        pl.col("exit_fee").sum().alias("exit_fee"),
        pl.col("unfilled").sum().alias("unfilled_known_claims"),
        (pl.col("n_sells") > 0).sum().alias("claims_with_a_sale"),
        (pl.col("release_exec").is_not_null()).sum().alias("claims_released_early"),
        pl.col("reentries").sum().alias("reentries"),
        pl.col("n_buys").sum().alias("buy_legs"),
        pl.col("n_sells").sum().alias("sell_legs"),
    )
    # The date set is fixed by construction for every cell at this (clock, N, cost). A
    # book-date whose every slot was blocked still belongs to it and still earns exactly
    # zero, so the count comes from the date keys, never from the surviving claims.
    counts = datekeys.group_by(["clock", "n", "side"]).agg(pl.len().alias("book_date_paired_days"))
    agg = agg.join(counts, on=["clock", "n", "side"], how="left").rename(
        {"paired_days": "days_with_a_known_claim"}
    )

    cells: dict[tuple, dict] = {}
    keys = {(c["clock"], c["n"], c["side"], c["policy"]) for c in agg.to_dicts()}
    for key in sorted(keys):
        clock, n, side, policy = key
        mask = (
            (pl.col("clock") == clock)
            & (pl.col("n") == n)
            & (pl.col("side") == side)
            & (pl.col("policy") == policy)
        )
        g = known.filter(mask)
        row = next(r for r in agg.to_dicts() if (r["clock"], r["n"], r["side"], r["policy"]) == key)
        gtotal = on.filter(mask)
        days = int(row["book_date_paired_days"] or 0)
        net = float(row["net"] or 0.0)
        entry_fee = float(row["entry_fee"] or 0.0)
        exit_fee = float(row["exit_fee"] or 0.0)
        fees = float(row["fees"] or 0.0)
        gross = float(row["gross_move"] or 0.0)
        fill_count = g.filter(pl.col("initial_q").fill_null(0.0) > 0).height

        cell = {
            "clock": clock,
            "n": n,
            "side": side,
            "policy": policy,
            "paired_days": days,
            "known_claims": int(row["known_claims"] or 0),
            "filled_claims": fill_count,
            "unfilled_known_claims": int(row["unfilled_known_claims"] or 0),
            "claims_with_a_sale": int(row["claims_with_a_sale"] or 0),
            "claims_released_early": int(row["claims_released_early"] or 0),
            "reentries": int(row["reentries"] or 0),
            "buy_legs": int(row["buy_legs"] or 0),
            "sell_legs": int(row["sell_legs"] or 0),
            "cash_net": f(net),
            "gross_move": f(gross),
            "fees": f(fees),
            "entry_fee": f(entry_fee),
            "exit_fee": f(exit_fee),
            "cash_net_per_paired_day": f(net / days) if days else None,
            "net_identity_holds": f(abs(net - (gross - fees))) < 1e-9,
        }

        # ---- round-trip tax, with the SUNK part separated from the addressable part.
        cell["roundtrip_tax"] = {
            "claims_with_a_complete_round_trip": int((g["n_sells"] > 0).sum()) if g.height else 0,
            "round_trips": int(g["n_sells"].sum()) if g.height else 0,
            "sunk_entry_fee_dollars": f(entry_fee),
            "exit_fee_dollars": f(exit_fee),
            "total_tax_dollars": f(fees),
            "tax_per_round_trip": f(fees / g["n_sells"].sum())
            if g.height and g["n_sells"].sum()
            else None,
            "sunk_share_of_tax": ratio(entry_fee, fees),
            "addressable_share_of_tax": ratio(exit_fee, fees),
            "tax_share_of_gross_move": ratio(abs(fees), abs(gross)),
            "sunk_entry_fee_is_policy_invariant": (
                "the entry price, the equal-dollar 1/N admission and the entry fee are fixed "
                "by the admission itself, so the sunk entry fee is IDENTICAL for every "
                "policy on the same claim and cancels in every paired comparison. No exit "
                "decision can recover it and no cost lever here claims to."
            ),
            "modeled_cost_not_a_recommendation": (
                "50/75bps per traded leg is the modeled friction on the filled capital the "
                "book actually traded. Removing it is arithmetic, not a proposal to assume "
                "a cheaper fill."
            ),
        }

        # ---- winner / loser / tail
        win = g.filter(pl.col("net") > 0)
        lose = g.filter(pl.col("net") <= 0)
        tail = g.filter(pl.col("mfe_class_full").is_in(list(TAIL_CLASSES)))
        preserved = tail.filter(pl.col("net") > 0)
        destroyed = tail.filter(pl.col("net") <= 0)
        mfe_mean = (
            float(tail["mfe_full"].mean())
            if tail.height and tail["mfe_full"].is_finite().any()
            else None
        )
        ret_mean = float((tail["net"] * tail["n"]).mean()) if tail.height else None
        cell["winner_tail"] = {
            "win_claims": win.height,
            "win_dollars": f(win["net"].sum()) if win.height else 0.0,
            "lose_claims": lose.height,
            "lose_dollars": f(lose["net"].sum()) if lose.height else 0.0,
            "lose_net_loss_dollars": f(-lose["net"].sum()) if lose.height else 0.0,
            "dud_no_excursion": class_bucket(g, (DUD_CLASS,)),
            "mid_excursion": class_bucket(g, (MID_CLASS,)),
            "tail": {
                **class_bucket(g, TAIL_CLASSES),
                "preserved_claims": preserved.height,
                "preserved_net": f(preserved["net"].sum()) if preserved.height else 0.0,
                "destroyed_claims": destroyed.height,
                "destroyed_net": f(destroyed["net"].sum()) if destroyed.height else 0.0,
                "mean_mfe_full": f(mfe_mean),
                "mean_net_return": f(ret_mean),
                "capture_ratio": ratio(ret_mean, mfe_mean) if mfe_mean and mfe_mean > 0 else None,
                "not_a_tradable_class": "an observed-window MFE class is a hindsight label on "
                "dollars already realized; no cell here asserts it is reachable.",
            },
            "masked_attribution": class_bucket(g, MASKED_CLASSES),
        }

        # ---- release-before the first +5% push, on the published minute margins
        # One row per claim, so a claim is counted at most once per margin. Boolean masks
        # are built on the frame so the dollar sums and the counts use the SAME rows.
        if g.height:
            push_f = to_floats(g["first_push_t"])
            rel_f = to_floats(g["release_exec"])
            netv = g["net"].to_numpy()
            has_push_m = np.isfinite(push_f)
            has_rel_m = np.isfinite(rel_f)
            before_m = has_push_m & has_rel_m & (rel_f < push_f)
            before5_m = before_m & (rel_f <= push_f - 5)
            before30_m = before_m & (rel_f <= push_f - 30)
            early_m = np.where(before_m, push_f - rel_f, np.nan)
        else:
            netv = None
            has_push_m = before_m = before5_m = before30_m = np.zeros(0, dtype=bool)
            early_m = np.zeros(0, dtype=np.float64)

        cell["release_before_first_push"] = {
            "ruler_threshold": PUSH_THRESHOLD,
            "ruler_ends": list(REL_ENDS),
            "claims_with_a_push_signal": int(has_push_m.sum()),
            "claims_without_a_push_signal": int((~has_push_m).sum()),
            "released_before_claims": int(before_m.sum()),
            "released_before_dollars": f(float(netv[before_m].sum()) if netv is not None else 0.0),
            "released_before_net_loss_dollars": f(
                -float(netv[before_m & (netv < 0)].sum()) if netv is not None else 0.0
            ),
            "released_before_5min_claims": int(before5_m.sum()),
            "released_before_5min_dollars": f(
                float(netv[before5_m].sum()) if netv is not None else 0.0
            ),
            "released_before_30min_claims": int(before30_m.sum()),
            "released_before_30min_dollars": f(
                float(netv[before30_m].sum()) if netv is not None else 0.0
            ),
            "median_minutes_released_early_of_a_push": median(early_m.tolist()),
            "one_count_per_claim": True,
            "ex_post_ruler": "the first-push minute is read off the discovery panel after the "
            "book already traded. It measures premature realization; it is never a signal.",
        }

        # ---- exit timing against the session window. Ownership comes from the legs:
        # a never-filled slot is known cash and is not a zero-minute hold, and a sell
        # executing AT the session-end boundary is still a hold through that boundary.
        own = ownership_state(g)
        if g.height:
            entry_f = to_floats(g["entry_et"])
            exit_f = to_floats(g["exit_et"])
            rel_f = to_floats(g["release_exec"])
            both = np.isfinite(entry_f) & np.isfinite(exit_f)
            rel_mask = np.isfinite(rel_f)
            held = (exit_f - entry_f)[both].tolist()
            releases_before_13 = int((rel_mask & (rel_f < 780)).sum())
        else:
            held = []
            both = np.zeros(0, dtype=bool)
            releases_before_13 = 0
        cell["exit_timing"] = {
            "median_minutes_held_from_entry_to_exit": median(held),
            "mean_minutes_held": f(float(np.mean(held))) if held else None,
            "claims_with_a_both_legs_timing": int(both.sum()) if g.height else 0,
            "claims_without_a_recorded_exit_leg": int(g.height - both.sum()) if g.height else 0,
            "median_discretionary_release_et": median(
                g["release_exec"].to_list() if g.height else []
            ),
            "median_final_exit_et": median(g["exit_et"].to_list() if g.height else []),
            "claims_released_before_13": releases_before_13,
            "median_session_end": median(g["end_full"].to_list() if g.height else []),
            "ownership": own,
            "no_universal_exit_asserted": "these are the realized exit minutes of one book. "
            "They are not a recommended exit curve and no cell is selected as best.",
        }

        # ---- exposure with UNKNOWN preserved
        cell["exposure"] = exposure_block(gtotal)

        # ---- concentration, per actual book per date
        cell["concentration"] = {
            **{f"day{k}": tail_concentration(g, "day", net, k) for k in TAIL_K},
            **{f"name{k}": tail_concentration(g, "ticker", net, k) for k in TAIL_K},
        }
        cell["day_sensitivity"] = {f"k{k}": day_sensitivity(g, net, k) for k in TAIL_K}

        # ---- disjoint levers; the cost lever NEVER touches the sunk entry fee
        deficit = max(0.0, -net)
        dud_gross_loss = cell["winner_tail"]["dud_no_excursion"]["gross_loss_dollars"] or 0.0
        cell["break_even"] = {
            "deficit_to_cash_dollars": f(deficit),
            "deficit_per_paired_day_dollars": f(deficit / days) if days else None,
            "price_lever_dollars": f(dud_gross_loss),
            "price_lever_covers_deficit_alone": (
                bool(dud_gross_loss >= deficit) if deficit > 0 else None
            ),
            "price_lever_residual_if_applied_alone_dollars": f(max(0.0, deficit - dud_gross_loss)),
            "cost_lever_dollars": f(exit_fee),
            "cost_lever_covers_deficit_alone": (bool(exit_fee >= deficit) if deficit > 0 else None),
            "cost_lever_residual_if_applied_alone_dollars": f(max(0.0, deficit - exit_fee)),
            "cost_lever_is_exit_fee_only": (
                "net = gross_move - fees. The addressable cost of an EXIT decision is the exit "
                "fee and the exit price. The entry fee was already charged at admission, is "
                "identical for every policy on the same claim, and is deliberately NOT counted "
                "as an avoidable lever here: subtracting it would remove a sunk dollar twice. "
                "The exit-fee-only lever is therefore strictly smaller than an all-fees lever "
                "and is the honest ceiling for what a later decision could recover."
            ),
            "joint_disjoint_improvement_dollars": f(dud_gross_loss + exit_fee),
            "joint_disjoint_residual_dollars": f(max(0.0, deficit - (dud_gross_loss + exit_fee))),
            "tail_rent_other_outcomes_fixed_dollars": f(deficit),
            "tail_rent_as_multiple_of_preserved_tail": ratio(
                deficit, cell["winner_tail"]["tail"]["preserved_net"] or 0.0
            ),
            "arithmetic_only": "every lever is an exact dollar quantity on the measured cell; "
            "nothing here is tuned and no hindsight class is claimed tradable.",
        }
        cells[key] = cell
    return cells


def exposure_block(gtotal: pl.DataFrame) -> dict:
    """Recorded exposure snapshots in original portfolio capital; a gap is UNKNOWN.

    Exposure is a BOOK-DATE quantity that the per-day file repeats on every claim row of
    that book, so it is collapsed to one row per (day, clock, N, cost, policy) before any
    count or mean. Averaging the repeated rows would silently weight a book by its claim
    count, which is a different quantity.
    """
    out: dict = {
        "note": "recorded exposure_<minute> columns are authoritative and used unchanged. "
        "A session that ends before the snapshot minute has no valid value there: it is "
        "UNKNOWN, never 0 and never silently reconstructed.",
        "unit": "original portfolio capital, one value per book-date",
        "grain": "collapsed to one row per (day, clock, N, cost, policy) before counting",
        "snapshots": {},
    }
    if not gtotal.height:
        for s in SNAPSHOTS:
            out["snapshots"][str(s)] = {
                "book_dates": 0,
                "recorded_book_dates": 0,
                "unknown_book_dates": 0,
            }
        return out

    books = gtotal.unique(subset=["day", "clock", "n", "side", "policy"])
    for s in SNAPSHOTS:
        col = f"exposure_{s}"
        if col not in books.columns:
            out["snapshots"][str(s)] = {"book_dates": books.height, "column_absent": True}
            continue
        v = books[col]
        rec = v.filter(v.is_not_null())
        late = books.filter(
            pl.col(col).is_null() & pl.col("end_full").is_not_null() & (pl.col("end_full") < s)
        )
        out["snapshots"][str(s)] = {
            "book_dates": int(books.height),
            "recorded_book_dates": int(rec.len()),
            "unknown_book_dates": int(v.is_null().sum()),
            "unknown_because_session_ended_early": int(late.height),
            "mean_exposure": f(rec.mean()) if rec.len() else None,
            "median_exposure": median(rec.to_list()),
            "max_exposure": f(rec.max()) if rec.len() else None,
            "min_exposure": f(rec.min()) if rec.len() else None,
        }
    return out


# ------------------------------------------------- matched-date comparisons + cash0
def _paired_rows(
    left: pl.DataFrame,
    right: pl.DataFrame,
    on: pl.DataFrame,
    left_id: str,
    right_id: str,
) -> list[dict]:
    """Pair two policy families on IDENTICAL dates, clock, N and cost.

    The two sides keep separate policy identifiers. Joining on the policy NAME would pair
    only the policies that happen to share a name, which silently drops every learned view
    on one side and leaves a "reference distribution" with a single member. Here the join
    key is (day, clock, N, cost) alone and each row states which policy it came from on
    each side. Pairing differently-named algorithms on identical dates is a comparison of
    two books' realized cashflows; it is NOT a claim that they are the same policy, and
    every row carries that distinction.
    """
    j = (
        left.join(on, on=["day", "clock", "n", "side"], how="semi")
        .filter(pl.col("ret").is_not_null())
        .join(
            right.join(on, on=["day", "clock", "n", "side"], how="semi").filter(
                pl.col("ret").is_not_null()
            ),
            on=["day", "clock", "n", "side"],
            how="inner",
            suffix="_ref",
        )
        .with_columns(
            (pl.col("ret") - pl.col("ret_ref")).alias("delta"),
            (pl.col("gross_pnl") - pl.col("gross_pnl_ref")).alias("d_gross"),
            (pl.col("fees") - pl.col("fees_ref")).alias("d_fees"),
            pl.col("policy").alias(left_id),
            pl.col("policy_ref").alias(right_id),
        )
    )
    return (
        j.group_by(["clock", "n", "side", left_id, right_id])
        .agg(
            pl.len().alias("paired_days"),
            pl.col("delta").mean().alias("mean_delta"),
            (pl.col("delta").std() / pl.col("delta").count().sqrt()).alias("delta_se"),
            pl.col("delta").median().alias("median_delta"),
            (pl.col("delta") > 0).mean().alias("share_of_paired_days_above_reference"),
            pl.col("d_gross").mean().alias("mean_d_gross"),
            pl.col("d_fees").mean().alias("mean_d_fees"),
            pl.col("ret").mean().alias("minute_policy_mean_ret"),
            pl.col("ret_ref").mean().alias("reference_policy_mean_ret"),
        )
        .sort(["clock", "n", "side", left_id, right_id])
        .to_dicts()
    )


def matched_vs_prior(
    minute_daily: pl.DataFrame,
    prior_daily: dict[str, pl.DataFrame],
    cross: pl.DataFrame,
    days: list[str],
) -> dict:
    """Matched-date comparisons in three independent blocks, plus the cash0 line.

    A. against the minute book's OWN rulers (hold, fade) -- a RULER-RELATIVE MONEY
       COMPARISON. Both sides share an engine, a roster, a clock, an admission and a ledger,
       so admission and cost differences are held fixed. They do NOT share a decision
       algorithm: a learned minute view and an unconditional ruler differ in the algorithm
       and in the value contract itself. This block therefore measures money relative to the
       ruler; it does not isolate decision frequency, and it is not a causal attribution to
       cadence.
    B. against every NAMED policy of both prior books on identical dates -- this is the
       requested reference distribution, published as a distribution.
    C. against cash0, the zero-dollar line.

    NOTHING is selected. Every horizon appears in every block beside every other. No
    reference is named as the one to beat, no view is ranked, and no post-TEST optimum is
    implied by any row.
    """
    cols = ["day", "clock", "n", "side", "policy", "ret", "unknown", "gross_pnl", "fees"]
    m = minute_daily.filter(pl.col("day").is_in(days)).select(cols)
    on = cross.select(["day", "clock", "n", "side"])

    # ---- A. minute views vs the minute book's own rulers, same book on both sides
    rulers = m.filter(pl.col("policy").is_in(["hold", "fade"]))
    own_rows = _paired_rows(m, rulers, on, "minute_policy", "reference_policy")
    for row in own_rows:
        row["reference_book"] = MINUTE_BOOK

    # ---- B. minute views vs every named policy of both prior books
    prior_rows: list[dict] = []
    for name, frame in prior_daily.items():
        ref = frame.filter(pl.col("day").is_in(days)).select(cols)
        for row in _paired_rows(m, ref, on, "minute_policy", "reference_policy"):
            prior_rows.append({"reference_book": name, **row})

    buckets: dict[tuple, list[float]] = {}
    for row in prior_rows:
        key = (row["clock"], row["n"], row["side"], row["minute_policy"], row["reference_book"])
        buckets.setdefault(key, []).append(float(row["mean_delta"]))
    distribution = sorted(
        (
            {
                "clock": clock,
                "n": n,
                "side": side,
                "minute_policy": policy,
                "reference_book": book,
                "reference_policies_in_distribution": int(np.asarray(vals).size),
                "mean_delta_worst_reference": f(float(np.min(vals))),
                "mean_delta_median_reference": f(float(np.median(vals))),
                "mean_delta_best_reference": f(float(np.max(vals))),
                "selection": "none. The whole reference set is published; no reference is "
                "named as the one to beat and no view is selected on TEST.",
            }
            for (clock, n, side, policy, book), vals in buckets.items()
        ),
        key=lambda d: (d["clock"], d["n"], d["side"], d["minute_policy"], d["reference_book"]),
    )

    # ---- C. cash0: deploying no capital earns exactly zero on each of these dates, by
    # construction rather than by measurement. A reference LINE, never an observed book.
    cash = (
        m.join(on, on=["day", "clock", "n", "side"], how="semi")
        .filter(pl.col("ret").is_not_null())
        .group_by(CELL)
        .agg(
            pl.len().alias("paired_days"),
            pl.col("ret").mean().alias("mean_excess_over_cash0"),
            (pl.col("ret") < 0).mean().alias("share_of_days_losing_to_cash0"),
            (pl.col("ret") > 0).mean().alias("share_of_days_beating_cash0"),
            pl.col("ret").median().alias("median_excess_over_cash0"),
            (pl.col("ret") < 0).sum().alias("days_losing_to_cash0"),
        )
        .sort(CELL)
        .to_dicts()
    )
    zero_rows = (
        m.join(on, on=["day", "clock", "n", "side"], how="semi")
        .filter(pl.col("ret").is_not_null() & (pl.col("ret").abs() < 1e-12))
        .height
    )
    exact_name_pairs = sorted(
        {r["minute_policy"] for r in prior_rows if r["minute_policy"] == r["reference_policy"]}
    )
    minute_policies = sorted(m["policy"].unique().to_list())
    return {
        "join_key": "day/clock/N/side ONLY. The two sides keep separate policy identifiers "
        "and are never matched on policy name.",
        "paired_vs_own_rulers": own_rows,
        "own_rulers_label": "ruler-relative money comparison",
        "own_rulers_note": "both sides come from the minute book, so admission, ledger and "
        "cost are held fixed and the comparison reads as money RELATIVE TO THE RULER. It is "
        "not a causal attribution to decision frequency: a learned minute view and an "
        "unconditional ruler differ in the decision algorithm and in the value contract "
        "itself (targets, training density, history). Isolating frequency alone would need "
        "the SAME forecasts replayed at thinned versus dense times, which is not done here.",
        "paired_vs_prior_books": prior_rows,
        "reference_distribution": distribution,
        "policy_name_correspondence": {
            "minute_policies": minute_policies,
            "prior_book_policies": {
                name: sorted(frame.filter(pl.col("day").is_in(days))["policy"].unique().to_list())
                for name, frame in prior_daily.items()
            },
            "policies_sharing_a_name": exact_name_pairs,
            "rule": "a minute policy is paired with EVERY prior-book policy on identical "
            "dates, not only with one that happens to share its name. Sharing a name is "
            "recorded as a fact about the two label sets; it never restricts the pairing, and "
            "a name match is not evidence that two differently-implemented policies are "
            "equivalent.",
            "no_selection": "every pairing is published. No reference is nominated and no "
            "view is selected.",
        },
        "vs_cash0": cash,
        "cash0_reference": {
            "definition": "cash0 is the zero-dollar line: a book that deploys no capital "
            "earns exactly 0 on every date. It is a reference by construction, never an "
            "observed book, and it is the only comparison arm here that was not measured.",
            "observed_book_dates_realizing_exactly_zero": zero_rows,
            "why_zero_book_dates_are_rare_here": "every published minute book-date has at "
            "least one filled claim, so the zero-dollar arm is realized by the blocked "
            "entry SLOTS inside a book (known cash at zero), not by whole book-dates.",
            "known_cash_slots_are_counted_separately": True,
        },
    }


def cadence_answer(
    cells: dict,
    claims: pl.DataFrame,
    minute_daily: pl.DataFrame,
    grid: pl.DataFrame,
    days: list[str],
) -> dict:
    """Observed coarse-vs-minute money differences, and what both sides share.

    Every number here is a measurement on realized books. None of it attributes an observed
    difference to decision frequency: the two book sets differ in the decision algorithm and
    in the value contract itself, and frequency is not isolated by this comparison.
    """
    m = minute_daily.filter(pl.col("day").is_in(days))
    keys = ["day", "clock", "n", "side"]

    # Cross-policy spread of the TOTAL fee inside one (day, clock, N, cost) book-date.
    tot = (
        m.filter(pl.col("ret").is_not_null())
        .group_by(keys)
        .agg(pl.col("fees").min().alias("lo"), pl.col("fees").max().alias("hi"))
    )
    spread = (tot["hi"] - tot["lo"]).to_numpy()
    level = m.filter(pl.col("ret").is_not_null())["fees"].to_numpy()

    # Cross-policy spread of the ENTRY fee alone on one claim slot. The admission price,
    # the equal-dollar 1/N amount and the entry leg are policy-independent, so this must
    # be exactly zero. Measuring it makes the invariance a fact of these books rather than
    # a claim about them, and it is what lets the sunk entry tax cancel in every pairing.
    by_claim = (
        claims.filter(pl.col("known"))
        .group_by(["day", "clock", "n", "side", "rank", "ticker", "policy"])
        .agg(pl.col("entry_fee").first().alias("e"))
    )
    # N belongs in the key: a rank-1 slot is funded with 1/3 under an N3 book and 1/5
    # under an N5 book, so pooling the two compares two different admission sizes and
    # would report a spread that is really the roster-size difference.
    per_slot = by_claim.group_by(["day", "clock", "n", "side", "rank", "ticker"]).agg(
        (pl.col("e").max() - pl.col("e").min()).abs().alias("spread")
    )
    max_entry_spread = float(per_slot["spread"].max()) if per_slot.height else None

    sunk = np.asarray([c["entry_fee"] or 0.0 for c in cells.values()], dtype=np.float64)
    exits = np.asarray([c["exit_fee"] or 0.0 for c in cells.values()], dtype=np.float64)
    rounds = [c["roundtrip_tax"]["round_trips"] for c in cells.values()]
    reentries = [c["reentries"] for c in cells.values()]
    # Round trips per CLAIM, not per cell: a cell total scales with the date set and says
    # nothing about whether any single lot turned over more than once.
    filled = claims.filter(pl.col("known") & pl.col("n_sells").is_not_null())
    max_rt_per_claim = int(filled["n_sells"].max()) if filled.height else 0
    min_rt_per_claim = int(filled["n_sells"].min()) if filled.height else 0

    g = grid.filter(pl.col("minute_coverage").is_not_null())
    return {
        "label": "ruler-relative money comparison -- observed differences, not causal "
        "attribution to decision frequency",
        "question": "how did the money differ between the coarse books and the full-minute "
        "grid, and what in those books is common to both?",
        "what_this_is_not": (
            "These are OBSERVED differences between two sets of realized books measured on "
            "matched dates. They are NOT a causal attribution of the difference to decision "
            "FREQUENCY alone. The coarse and minute books differ in the decision algorithm and "
            "in the value contract itself (targets, training density, history), so frequency "
            "is not isolated by this comparison. Identifying frequency alone would require "
            "replaying the SAME forecasts at thinned versus dense decision times, which is not "
            "done here and is not claimed."
        ),
        "answer_from_the_books": [
            "The admission is policy-independent within a book. Every policy buys the same "
            "roster slots at the same clock for the same equal-dollar 1/N amount at the same "
            "fill price, so the entry fee is identical across policies and cancels in every "
            "paired delta. The measured cross-policy entry-fee spread is reported below "
            "rather than assumed.",
            "Every realized claim in this book completes at most one buy and one sell leg, so "
            "the round-trip COUNT is the same under every policy.",
            "What remains free to vary between policies is therefore the exit price of an "
            "already-admitted lot, and the fee that scales with it. The measured cross-policy "
            "fee spread inside a book-date is far smaller than the fee level itself, and every "
            "paired delta is split into d_gross and d_fees so the size of each channel is "
            "visible.",
            "The minute grid is genuinely dense (coverage and gap statistics below), so the "
            "extra decision points were available. Whether they were the binding constraint "
            "is not established by these numbers and is not asserted.",
        ],
        "entry_fee_cross_policy_spread_dollars": f(max_entry_spread),
        "entry_fee_is_policy_invariant_measured": max_entry_spread == 0.0,
        "max_cross_policy_fee_spread_within_date_key": f(spread.max()) if spread.size else None,
        "mean_cross_policy_fee_spread_within_date_key": f(spread.mean()) if spread.size else None,
        "mean_fee_level_per_book_date": f(level.mean()) if level.size else None,
        "spread_over_fee_level": ratio(
            float(spread.mean()) if spread.size else None,
            float(level.mean()) if level.size else None,
        ),
        "max_round_trips_on_any_single_claim": max_rt_per_claim,
        "min_round_trips_on_any_filled_claim": min_rt_per_claim,
        "total_reentries_across_cells": int(sum(reentries)),
        "round_trip_unit": "a round trip is one buy leg plus one sell leg on a single claim; "
        "these per-claim bounds are what a turnover or cost lever could act on, and the "
        "per-cell total below only scales with the date set",
        "total_round_trips_all_cells": int(sum(rounds)),
        "sunk_entry_fee_share_of_all_cell_tax": ratio(
            float(sunk.sum()) if sunk.size else None,
            float((sunk + exits).sum()) if sunk.size else None,
        ),
        "decision_cadence": {
            "books": int(g.height),
            "mean_minute_coverage": f(g["minute_coverage"].mean()) if g.height else None,
            "min_minute_coverage": f(g["minute_coverage"].min()) if g.height else None,
            "worst_median_gap_between_decision_minutes": f(
                g["median_gap_between_decision_minutes"].max()
            )
            if g.height
            else None,
            "mean_distinct_decision_minutes": f(g["distinct_decision_minutes"].mean())
            if g.height
            else None,
            "books_with_a_missing_score_set": int(g.filter(pl.col("note") != "").height),
        },
        "not_a_verdict_on_the_asset": "this is a decomposition of realized cashflows on one "
        "causal roster family at one modeled cost. It is not a claim that the underlying "
        "names are unprofitable, and the entry tax is a cost of realized turnover on "
        "admitted capital, not a fee overcharge.",
    }


def ownership_transitions(cells: dict, claims: pl.DataFrame, datekeys: pl.DataFrame) -> dict:
    """Observable ownership / capital transitions that could change ACTUAL dollars.

    Measured from the books, with no threshold chosen to produce a result.
    """
    on = claims.join(
        datekeys.select(["day", "clock", "n", "side"]), on=["day", "clock", "n", "side"], how="semi"
    )
    known = on.filter(pl.col("known"))
    # Ownership is decided by executed legs, never by the presence of a release field.
    # A slot with no buy leg was never a position: it is known cash at zero dollars.
    entered = known.filter(pl.col("n_buys").fill_null(0) > 0)
    never_owned = known.filter(pl.col("n_buys").fill_null(0) == 0)

    def released_pre_se(frame: pl.DataFrame) -> pl.DataFrame:
        if not frame.height:
            return frame
        return frame.filter(
            pl.col("exit_et").is_not_null()
            & pl.col("end_full").is_not_null()
            & (pl.col("exit_et") < pl.col("end_full"))
        )

    released = released_pre_se(entered)
    held_through = entered.filter(
        pl.col("exit_et").is_null()
        | pl.col("end_full").is_null()
        | (pl.col("exit_et") >= pl.col("end_full"))
    )

    rows = []
    for key, c in sorted(cells.items()):
        clock, n, side, policy = key
        mask = (
            (pl.col("clock") == clock)
            & (pl.col("n") == n)
            & (pl.col("side") == side)
            & (pl.col("policy") == policy)
        )
        g = known.filter(mask)
        g_entered = entered.filter(mask)
        r = released_pre_se(g_entered)
        g_held = g_entered.filter(
            pl.col("exit_et").is_null()
            | pl.col("end_full").is_null()
            | (pl.col("exit_et") >= pl.col("end_full"))
        )
        net = float(c["cash_net"] or 0.0)
        days = c["paired_days"] or 0
        rows.append(
            {
                "clock": clock,
                "n": n,
                "side": side,
                "policy": policy,
                "paired_days": days,
                "known_claims": g.height,
                "never_owned_known_cash": g.height - g_entered.height,
                "entered_claims": g_entered.height,
                "entered_released_before_session_end": r.height,
                "entered_held_through_session_end": g_held.height,
                "released_claims_per_book_day": f(r.height / days) if days else None,
                "gross_move_of_released_claims_dollars": f(r["gross"].sum()) if r.height else 0.0,
                "released_entry_fee_dollars": f(r["entry_fee"].sum()) if r.height else 0.0,
                "sold_notional_that_became_idle_cash_dollars": f(r["sell_gross"].sum())
                if r.height
                else 0.0,
                "units_note": "gross_move is signed realized P&L; sold_notional is the "
                "positive notional that left the position and sat in cash for the rest of "
                "the session. They are different quantities and are never added.",
                "median_minutes_held": c["exit_timing"]["median_minutes_held_from_entry_to_exit"],
                "cash_net": f(net),
                "release_share_of_entered_claims": ratio(r.height, g_entered.height),
                "no_reentry_redeployment": bool(c["reentries"] == 0),
                "state_source": "actual executed legs; a never-filled slot is known cash and "
                "is never counted as a held position",
            }
        )
    totals = {
        "known_claims": int(known.height),
        "never_owned_known_cash": int(never_owned.height),
        "entered_claims": int(entered.height),
        "entered_released_before_session_end": int(released.height),
        "entered_held_through_session_end": int(held_through.height),
        "states_sum_to_known_claims": (
            int(never_owned.height) + int(released.height) + int(held_through.height)
            == int(known.height)
        ),
        "never_owned_by_roster_status": (
            {
                str(r["roster_status"]): int(r["c"])
                for r in never_owned.group_by("roster_status").agg(pl.len().alias("c")).to_dicts()
            }
            if never_owned.height
            else {}
        ),
        "idle_gross_dollars_after_release": f(released["sell_gross"].sum())
        if released.height
        else 0.0,
        "entry_fee_already_sunk_on_released_claims": f(released["entry_fee"].sum())
        if released.height
        else 0.0,
        "entry_fee_already_sunk_on_held_through_claims": f(held_through["entry_fee"].sum())
        if held_through.height
        else 0.0,
        "total_entry_fee_already_sunk": f(known["entry_fee"].sum()) if known.height else 0.0,
        "total_reentries": int(known["reentries"].sum()) if known.height else 0,
        "state_source": "actual executed legs; the source of each never-owned slot is named "
        "from the roster status rather than assumed to be blocked",
    }
    return {
        "what_is_observable": [
            "RELEASE-then-hold-cash: the engine settles sale proceeds at T+1 and the "
            "stop-only book never redeploys them, so every early release converts a position "
            "into idle cash for the rest of the session. The idle gross dollars are measured "
            "per cell; the return on that idle cash is NOT assumed to be anything.",
            "ADMISSION: which claim slots receive the equal-dollar 1/N allocation is the only "
            "decision made before capital is committed, and it is where the sunk entry fee is "
            "created. It is identical across every policy here, which is exactly why the "
            "entry tax cannot be recovered downstream.",
            "ROUND-TRIP COUNT: measured at one buy and one sell per admitted claim, so there "
            "is no turnover channel for a cost lever to act on in these books.",
        ],
        "what_is_not_claimed": "no universal exit curve, no recommended clock, no selected "
        "horizon, and no top-day effect is presented as a finding. A negative cell is a "
        "statement about that book's realized cashflows, not about the asset class.",
        "totals": totals,
        "by_cell": rows,
    }


# ----------------------------------------------------------------------- artifact body
def build_artifact(data_root: Path, artifacts_root: Path, out: Path, limit: int | None) -> dict:
    notes: list[str] = []
    evidence = True
    days_all, fold_of = published_days(data_root)
    days = days_all[:limit] if limit else days_all
    loss, gap, pins = verify_inputs(data_root, artifacts_root, days)
    if limit:
        evidence = False
        notes.append(f"NON-EVIDENCE smoke run over the first {limit} published dates")
        # A smoke run must never overwrite, or be mistaken for, a real evidence artifact.
        out = out.with_name(f"{out.stem}.non_evidence{out.suffix}")

    minute_daily = pl.read_parquet(book_dir(data_root, MINUTE_BOOK) / "daily.parquet").filter(
        pl.col("day").is_in(days)
    )
    prior_daily = {
        name: pl.read_parquet(book_dir(data_root, bdir) / "daily.parquet").filter(
            pl.col("day").is_in(days)
        )
        for name, bdir in PRIOR_BOOKS.items()
    }
    grid = pl.read_parquet(book_dir(data_root, MINUTE_BOOK) / "decision_grid.parquet").filter(
        pl.col("day").is_in(days)
    )

    sets = date_sets(minute_daily, prior_daily, days)
    own, cross = sets["own"], sets["cross"]
    if not own.height:
        raise SystemExit("no paired date survived the all-policies-known contract; refusing")

    # ---- incremental per-day claim stage, resumable and pinned
    stage = out.parent / (out.stem + "_stage")
    stage.mkdir(parents=True, exist_ok=True)
    pin = hashlib.sha256(
        (sha256_file(SCRIPT) + json.dumps(pins, sort_keys=True) + "|".join(days)).encode()
    ).hexdigest()

    parts: list[pl.DataFrame] = []
    worst_identity = 0.0
    for i, day in enumerate(days):
        sp = stage / f"{day}.claims.parquet"
        mp = stage / f"{day}.json"
        if sp.exists() and mp.exists():
            meta = json.loads(mp.read_text())
            if meta.get("stage_pin") == pin:
                parts.append(pl.read_parquet(sp))
                continue
        claims = stage_day(day, data_root)
        worst_identity = max(worst_identity, assert_claim_identities(claims, day))
        tmp = sp.with_suffix(".tmp.parquet")
        claims.write_parquet(tmp)
        tmp.replace(sp)
        mp.write_text(json.dumps({"stage_pin": pin, "day": day, "claims": claims.height}) + "\n")
        parts.append(claims)
        del claims
        gc.collect()
        if i % 25 == 0:
            print(f"[minute attribution] staged {i + 1}/{len(days)} {day}", flush=True)

    all_claims = pl.concat(parts, how="diagonal_relaxed")
    del parts
    gc.collect()

    # ---- the minute book's own money decomposition
    cells = build_cells(all_claims, own)

    # ---- identities and supports, per cell
    cell_daily = minute_daily.filter(pl.col("day").is_in(own["day"].to_list())).join(
        own.select(["day", "clock", "n", "side"]), on=["day", "clock", "n", "side"], how="semi"
    )
    reported = cell_daily.group_by(CELL).agg(pl.col("ret").sum().alias("reported_ret_sum"))
    recon_rows = []
    max_recon = 0.0
    for r in reported.to_dicts():
        key = (r["clock"], r["n"], r["side"], r["policy"])
        c = cells.get(key)
        if c is None:
            continue
        diff = abs((c["cash_net"] or 0.0) - (r["reported_ret_sum"] or 0.0))
        max_recon = max(max_recon, diff)
        recon_rows.append(
            {"clock": key[0], "n": key[1], "side": key[2], "policy": key[3], "abs_diff": f(diff)}
        )
    if max_recon >= 1e-9:
        raise SystemExit(f"sum(member net) != sum(daily ret): max abs diff {max_recon}")

    support = []
    for name, frame in [("minute", minute_daily), *prior_daily.items()]:
        g = (
            frame.filter(pl.col("day").is_in(days))
            .group_by(CELL)
            .agg(
                pl.len().alias("days_total"),
                pl.col("ret").is_not_null().sum().alias("known_days"),
                pl.col("unknown").sum().alias("unknown_days"),
            )
            .sort(CELL)
        )
        for r in g.to_dicts():
            support.append({"book": name, **r})

    censored = all_claims.filter(~pl.col("known"))
    unknown_block = {
        "note": "a censored book-date is excluded from every dollar sum and counted here; it "
        "is never treated as a zero and never imputed",
        "censored_claims": int(censored.height),
        "censored_claims_by_cell": [
            {
                "clock": r["clock"],
                "n": r["n"],
                "side": r["side"],
                "policy": r["policy"],
                "censored_claims": int(r["c"]),
                "censored_days": int(r["d"]),
            }
            for r in censored.group_by(CELL)
            .agg(pl.len().alias("c"), pl.col("day").n_unique().alias("d"))
            .sort(CELL)
            .to_dicts()
        ],
        "known_cash_slots": int(all_claims.filter(pl.col("known") & pl.col("unfilled")).height),
        "known_cash_slots_are_zero_dollars": True,
        "known_cash_note": "a blocked entry slot is KNOWN cash at exactly zero dollars and is "
        "distinct from UNKNOWN; it is counted, not dropped",
        "no_fill_known_claims": int(
            all_claims.filter(pl.col("known") & (pl.col("n_buys").fill_null(0) == 0)).height
        ),
    }

    # ---- exposure provenance across the paired dates
    exposure_rows = []
    for c in sorted(cells.values(), key=lambda c: (c["clock"], c["n"], c["side"], c["policy"])):
        for s, blk in c["exposure"]["snapshots"].items():
            exposure_rows.append(
                {
                    "clock": c["clock"],
                    "n": c["n"],
                    "side": c["side"],
                    "policy": c["policy"],
                    "snapshot": int(s),
                    **blk,
                }
            )

    # Ownership is computed once and reused by both the cell-level and the total-level
    # report, so the three states cannot drift between them.
    ownership = ownership_transitions(cells, all_claims, own)
    ownership_totals = ownership["totals"]
    own_partition_ok = bool(ownership_totals["states_sum_to_known_claims"]) and all(
        c["exit_timing"]["ownership"]["states_sum_to_known_claims"] for c in cells.values()
    )
    if not own_partition_ok:
        raise SystemExit(
            "ownership states do not partition the known claims; refusing to publish a "
            "held-position count that would include never-owned cash"
        )

    artifact = {
        "kind": KIND,
        "producer": "factory/scripts/owned_claim_minute_attribution.py",
        "producer_sha256": sha256_file(SCRIPT),
        "evidence": evidence,
        "non_evidence_notes": notes,
        "reads": [
            "data/harvest01/lifecycle/v2/owned_claim/replay_minute/days/<day>.{daily,members,fills}.parquet",
            "data/harvest01/lifecycle/v2/owned_claim/replay_minute/{daily,decision_grid}.parquet",
            "data/harvest01/lifecycle/v2/owned_claim/replay/daily.parquet (prior FVI book)",
            "data/harvest01/lifecycle/v2/owned_claim/replay_policy/daily.parquet "
            "(prior full-policy book)",
            "data/harvest01/lifecycle/v2/owned_claim/loss_sources_stage/<day>.claims.parquet "
            "(pinned claim-level MFE class + session end)",
            "data/harvest01/lifecycle/v2/harvestability/events.parquet (first-push ruler)",
            f"factory/artifacts/{LOSS_ARTIFACT}, factory/artifacts/{GAP_ARTIFACT} "
            "(pins and definitions)",
        ],
        "inputs": {
            "data_root": str(data_root),
            "artifacts_root": str(artifacts_root),
            **pins,
            "upstream_artifacts": {
                LOSS_ARTIFACT: {
                    "kind": loss.get("kind"),
                    "producer": loss.get("producer"),
                    "run_id": loss.get("run_id"),
                    "test_days_count": len(loss.get("test_days") or []),
                },
                GAP_ARTIFACT: {
                    "kind": gap.get("kind"),
                    "producer": gap.get("producer"),
                    "cells_count": gap.get("cells_count"),
                    "scope_note": "read for its pinned definitions and cell lineage. Its "
                    "cells are the PRIOR books' cells, not the minute book's, so no dollar "
                    "is carried over from it into this file.",
                },
            },
        },
        "test_days": days,
        "test_days_count": len(days),
        "test_days_total_published": len(days_all),
        "folds": {d: fold_of[d] for d in days if d in fold_of},
        "money_unit": "original portfolio capital dollars (member net_pnl). A per-book sum over "
        "selected ranks equals that book's daily ret on every known date. Per-book-date means "
        "are in the same capital; per-claim-slot means are labelled separately and never mixed.",
        "definitions": {
            "cell": "(clock, N, side cost, policy). Nothing is pooled across clocks; N3 and N5 "
            "are separate ownership universes and are never combined.",
            "own_paired_dates": f"dates where all {N_POLICY} minute policies resolve for that "
            "(clock, N, cost). Every minute-book dollar figure uses exactly this set.",
            "cross_paired_dates": f"the same, and all {N_POLICY} policies resolve in BOTH prior "
            "books. Only the minute-vs-prior comparisons use this set.",
            "cash0": "the zero-dollar reference line: deploying no capital earns exactly 0 on "
            "every date, by construction. It is a reference, never an observed book.",
            "sunk_entry_fee": "the fee charged on the initial admission buy. It is paid before "
            "any exit decision exists, is identical for every policy on the same claim, and is "
            "never counted as an avoidable cost lever.",
            "roundtrip_tax": "sunk entry fee plus exit fee actually charged, both in original "
            "portfolio capital, with the addressable (exit) share reported separately.",
            "dud_no_excursion": f"mfe_class_full == {DUD_CLASS} (observed-window peak excursion "
            "below +10% with a fresh right edge).",
            "tail": f"mfe_class_full in {list(TAIL_CLASSES)}; preserved = net > 0, "
            "destroyed = net <= 0.",
            "masked_attribution": f"mfe_class_full in {list(MASKED_CLASSES)}: real dollars, "
            "UNKNOWN class, never recoded as a dud.",
            "release_before_first_push": f"the harvestability first +{int(PUSH_THRESHOLD * 100)}% "
            "push minute on the published rulers, against the book's own first discretionary "
            "release execution; the +5 and +30 figures are MINUTE margins before that push, "
            "one count per claim.",
            "exposure": "recorded exposure_<minute> columns in original portfolio capital; an "
            "absent or calendar-invalid snapshot is UNKNOWN, never 0.",
            "ownership_states": "read from ACTUAL executed legs, never from a missing release "
            "field. never_owned = no buy leg (known cash at exactly zero, never a position); "
            "entered_released_preSE = bought and sold strictly before the session-end "
            "boundary; entered_held_throughSE = bought and still held to that boundary, "
            "INCLUDING a discretionary sell whose execution minute IS the boundary. The "
            "three partition the known claims exactly, and the roster status naming each "
            "never-owned slot is reported rather than assumed.",
            "matched_date_pairing": "the join key is (day, clock, N, side) ONLY. The two "
            "sides carry separate minute_policy and reference_policy identifiers and are "
            "never matched on policy name; a shared name is recorded as a fact about the two "
            "label sets, not used to restrict the pairing, and not evidence that two "
            "differently-implemented policies are equivalent.",
            "day_sensitivity": "best-k and worst-k day removal on one fixed date set and one "
            "claim population. Removing the best days from a negative cell is a tautology, not "
            "evidence; the worst-k direction is the diagnostic one.",
            "policy_views": "eight fixed horizons plus max are POLICY VIEWS of the same roster. "
            "Every view is reported beside every other. No view is selected, ranked or named "
            "best, and no post-TEST optimum is claimed.",
            "break_even_levers": "net = gross_move - fees. The PRICE lever is the dud claims' "
            "GROSS price loss with fees held constant; the COST lever is the EXIT FEE ONLY "
            "with gross held constant. They act on disjoint parts of the identity. The dud "
            "NET loss is never used as a lever because it already contains fees, and the "
            "sunk entry fee is never used as a lever because it is already paid.",
            "lookahead_use": "the MFE class and the first-push ruler are read off the "
            "discovery panel AFTER the book traded. They classify realized dollars. They are "
            "never a feature, an entry rule, an exit rule or a tradable-class claim.",
        },
        "support": support,
        "reconciliation": {
            "identity": "net = gross_move - fees per claim and per cell; sum(member net) == "
            "sum(daily ret) per cell on the paired dates; gross_move = sell_gross - buy_gross; "
            "fees = entry_fee + exit_fee",
            "max_abs_claim_identity_diff": f(worst_identity),
            "max_abs_cell_recon_diff": f(max_recon),
            "by_cell": recon_rows,
            "ownership_partition": {
                "identity": "never_owned + entered_released_before_session_end + "
                "entered_held_through_session_end + entered_without_a_resolved_exit == known "
                "claims, per cell and in total",
                "all_cells_partition_cleanly": own_partition_ok,
                "never_owned_source_counts": ownership_totals["never_owned_by_roster_status"],
                "boundary_rule": "a discretionary sell executing AT session_end is held "
                "through session_end; a never-filled slot is known cash and is never counted "
                "as a held position",
            },
            "engine_untouched": "no accounting rule is reimplemented here. The realized books "
            "are read as written by the unchanged engine.",
        },
        "unknown": unknown_block,
        "cells": sorted(cells.values(), key=lambda c: (c["clock"], c["n"], c["side"], c["policy"])),
        "cells_count": len(cells),
        "exposure_by_cell": exposure_rows,
        "matched_date_comparisons": matched_vs_prior(minute_daily, prior_daily, cross, days),
        "why_coarse_vs_minute": cadence_answer(cells, all_claims, minute_daily, grid, days),
        "ownership_and_capital_transitions": ownership,
        "caveats": [
            "Every dollar here is a measured realized-book quantity on discovery-half dates. "
            "Nothing is projected, and no protected outcome, bot order or deploy flag was read.",
            "Different horizons are policy views of one roster, reported side by side. No "
            "post-TEST best view is selected, and the reference distribution across the prior "
            "books' policies is published as a distribution, never as a target.",
            "The paired blocks join on (day, clock, N, side) ONLY, with separate "
            "minute_policy and reference_policy identifiers. A minute view and a prior-book "
            "policy that share a date set are TWO BOOKS' realized cashflows being compared; "
            "they are not asserted to be the same policy, and a shared label is not evidence "
            "of equivalence.",
            "paired_vs_own_rulers is a RULER-RELATIVE MONEY COMPARISON. Holding the engine, "
            "roster, clock, admission, ledger and cost fixed is NOT the same as isolating "
            "decision frequency: a learned minute view and an unconditional ruler differ in "
            "the decision algorithm and in the value contract itself (targets, training "
            "density, history). No comparison in this file identifies frequency as the cause "
            "of an observed difference. Doing so would require replaying the SAME forecasts "
            "at thinned versus dense decision times, which is not done and is not proposed.",
            "The coarse-vs-minute block reports OBSERVED differences between realized book "
            "sets on matched dates plus the quantities common to both sides. It is not a "
            "causal attribution to decision cadence.",
            "Ownership states are read from executed legs. A roster slot that never filled is "
            "known cash at exactly zero and is never counted as a held position, and a "
            "discretionary sell executing AT session_end is held through that boundary.",
            "The MFE class and the first-push ruler are hindsight attribution labels. No cell "
            "asserts that a class is reachable, and none is offered as an entry or exit rule.",
            "A cost lever never removes an already-sunk entry fee. The addressable cost of an "
            "exit decision is the exit fee and the exit price; the entry fee was charged at "
            "admission and is common to all policies.",
            "Removing the best days from an already-negative cell mechanically lowers the mean "
            "and is a tautology. Day sensitivity is therefore read in the worst-day direction "
            "on identical dates and the same claim population, and no best-day result is "
            "reported as a finding.",
            "The 50/75bps per-leg cost is modeled friction on the filled capital the book "
            "actually traded. It is the cost of realized turnover, not a fee overcharge, and "
            "it is not evidence that the underlying names are unprofitable.",
            "No universal exit curve, no recommended clock and no selected horizon is asserted "
            "anywhere in this file. Each cell is a statement about one book's realized "
            "cashflows on one date set.",
            "Cells are alternative correlated books sharing a date set. Any sum across cells "
            "is an arithmetic cross-case aggregate, not portfolio PnL and not additive across "
            "policies.",
        ],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(artifact, indent=2, allow_nan=False) + "\n")
    tmp.replace(out)
    print(
        "[minute attribution] wrote",
        out,
        "days",
        len(days),
        "cells",
        len(cells),
        "own_paired_datekeys",
        own.height,
        "cross_paired_datekeys",
        cross.height,
        "max_recon",
        f(max_recon),
        flush=True,
    )
    return artifact


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description="full-minute owned-claim dollar attribution")
    ap.add_argument("--data-root", type=Path, default=None)
    ap.add_argument("--artifacts-root", type=Path, default=root / "factory/artifacts")
    ap.add_argument(
        "--out", type=Path, default=root / "factory/artifacts/owned_claim_minute_attribution.json"
    )
    ap.add_argument(
        "--limit", type=int, default=None, help="NON-EVIDENCE smoke over the first N dates"
    )
    a = ap.parse_args(argv)
    build_artifact(ls.bps.resolve_data_root(a.data_root), a.artifacts_root, a.out, a.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
