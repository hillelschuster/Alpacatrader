#!/usr/bin/env python3
"""OWNED-CLAIM LOSS SOURCES — exact per-day dollar decomposition of the two realized books.

The same original causal top-gainer roster is replayed twice:
  * OLD  fixed value-iteration book          -> owned_claim/replay
  * NEW  full-policy-return book             -> owned_claim/replay_policy   (mandatory)

This producer is a READ-ONLY diagnostic over already-computed books. It discovers the
out-of-fold test dates from each book's own ``daily.parquet`` (never globbing the day
directory and never scanning another date), validates them against the locked discovery
split and against the model metadata's fold test days, and stages per-day claim records
before emitting one compact JSON.

Definitions fixed here (no new strategy, no threshold grid):
  * money unit = ORIGINAL PORTFOLIO capital, as recorded by the replay. Each member's
    ``net_pnl`` is a portfolio-dollar cashflow whose sum over the selected ranks equals the
    book's daily ``ret`` on every known date (asserted, not assumed). Class/rank/fee
    contributions are sums of those dollars, so they are additive and never mix per-claim
    returns back into a portfolio number.
  * fee split is read from the fill record (``fee_fraction`` on buy vs sell legs), never a
    copied benchmark; ``net = gross - fees`` and the member identities are asserted.
  * observed-window MFE is taken ONLY from the discovery-day panel ``peak_gain <= window``,
    with a fresh right edge (last observed ``px_et`` must reach ``window_end - 1``). A stale
    right edge is ``incomplete_window`` (masked), never a low-excursion dud. The roster
    ``mfe_day`` (post-session) and personality/route labels are never read.
  * unfilled cash (a blocked entry slot) is KNOWN cash at zero dollars; UNKNOWN cashflows
    (a censored book) are excluded from every dollar sum and counted instead.
  * exposure snapshots: a recorded ``exposure_<minute>`` column from the corrected source
    is authoritative and used unchanged. When the source omits one because that minute is
    calendar-invalid for the session (a half day ending before the 13:00 ruler), exposure at
    the requested minute is reconstructed from executed fills (remaining signed shares) x the
    last observed past mark; a fully liquidated book is a valid 0, an unresolved/unobserved
    remaining asset is UNKNOWN (never 0), and every case is counted by provenance.
  * release-before first +5/+15/+30 uses the harvestability first-push signals (``t``) and
    the policy's first discretionary release execution (``state_release``/``fade``).
  * common-known comparison days are days where ALL policies resolve for that
    clock/N/cost; paired-date support is reported, never hidden.

No protected (validation) price is read; no FREEZE marker is touched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from functools import reduce
from pathlib import Path

os.environ.setdefault("POLARS_MAX_THREADS", "2")
import lifecycle_study as ls  # noqa: E402
import polars as pl  # noqa: E402

CLOCKS = (540, 560, 569, 571)
END = 780  # 13:00 ruler for the <13 observed window
SNAPSHOTS = (585, 630, 690, 780)  # 09:45 / 10:30 / 11:30 / 13:00
KEYS = ["day", "clock", "rank", "ticker"]
VIEWS = (3, 5)
SIDES = (0.005, 0.0075)
POLICIES = (
    "hold",
    "fade",
    "state:stop",
    "state:reentry",
    "state:allocate",
    "history:stop",
    "history:reentry",
    "history:allocate",
    "tape:stop",
    "tape:reentry",
    "tape:allocate",
)
BOOKS = ("fvi", "policy_return")
BOOK_ROOT = {"fvi": "replay", "policy_return": "replay_policy"}
BOOK_MODEL = {"fvi": "value", "policy_return": "value_policy"}
MFE_EDGES = ((0.10, "MFElt10"), (0.30, "MFE10_30"), (1.00, "MFE30_100"))
MFE_TOP = "MFE100"
MFE_WINDOWS = {"lt13": ("mfe_lt13", "gap_lt13"), "full": ("mfe_full", "gap_full")}
ENDPOINT_GAP_MAX = 1  # fresh right edge only; not interior-tape certification
REL_ENDS = (780, 900)  # 13:00 and the study's late-day ruler
REL_THRESHOLDS = (0.05, 0.15, 0.30)
RELEASE_REASONS = ("state_release", "fade")
N_DISCOVERY = 533
N_TEST = 383
KIND = "DISCOVERY-DIAGNOSTIC-NOT-ALPHA"
# Provenance of a snapshot exposure in ORIGINAL PORTFOLIO capital. Recorded values stay
# authoritative; an absent column is reconstructed from executed fills x past marks when
# that is legitimate, otherwise it is explicitly UNKNOWN (never fabricated as 0).
EXPOSURE_STATUSES = (
    "recorded",
    "reconstructed_flat_zero",
    "reconstructed_from_fills",
    "unknown_calendar_not_applicable",
    "unknown_unresolved_asset",
)


# --------------------------------------------------------------------- helpers
def v2(data_root: Path) -> Path:
    return ls.v2_dir(data_root)


def book_dir(data_root: Path, book: str) -> Path:
    return v2(data_root) / "owned_claim" / BOOK_ROOT[book]


def model_dir(data_root: Path, book: str) -> Path:
    return v2(data_root) / "owned_claim" / BOOK_MODEL[book]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _finite(name: str) -> pl.Expr:
    c = pl.col(name)
    return c.filter(c.is_finite())


def _safe_float(v):
    if v is None:
        return None
    v = float(v)
    return None if v != v else v


def _class_expr(mfe: str, gap: str) -> pl.Expr:
    return (
        pl.when(pl.col(mfe).is_null() | ~pl.col(mfe).is_finite())
        .then(pl.lit("unknown_mfe"))
        .when(pl.col(gap).is_null() | (pl.col(gap) > ENDPOINT_GAP_MAX))
        .then(pl.lit("incomplete_window"))
        .when(pl.col(mfe) < MFE_EDGES[0][0])
        .then(pl.lit(MFE_EDGES[0][1]))
        .when(pl.col(mfe) < MFE_EDGES[1][0])
        .then(pl.lit(MFE_EDGES[1][1]))
        .when(pl.col(mfe) < MFE_EDGES[2][0])
        .then(pl.lit(MFE_EDGES[2][1]))
        .otherwise(pl.lit(MFE_TOP))
    )


# --------------------------------------------------------------------- day discovery
def discover_days(data_root: Path, book: str) -> list[str]:
    """Exact test dates from the book's own daily.parquet; no glob, no other-date scan."""
    root = book_dir(data_root, book)
    daily = root / "daily.parquet"
    if not daily.exists():
        raise SystemExit(f"{book} realized book missing: {daily} (no fallback while pending)")
    days = sorted(pl.read_parquet(daily, columns=["day"])["day"].unique().to_list())
    if len(days) != N_TEST:
        raise SystemExit(f"{book}: daily.parquet has {len(days)} test dates, expected {N_TEST}")
    locked = set(ls.discovery_days(data_root))
    if not set(days) <= locked:
        raise SystemExit(f"{book}: non-discovery test dates {sorted(set(days) - locked)[:5]}")
    meta_path = model_dir(data_root, book) / "metadata.json"
    if not meta_path.exists():
        raise SystemExit(f"{book}: model metadata missing: {meta_path}")
    meta = json.loads(meta_path.read_text())
    if meta.get("status") != "complete":
        raise SystemExit(f"{book}: model metadata status={meta.get('status')!r}, not complete")
    fold_days = sorted({d for fold in meta["folds"] for d in fold["test_days"]})
    if fold_days != days:
        raise SystemExit(f"{book}: daily.parquet dates do not match the model fold test days")
    return days


# --------------------------------------------------------------------- per-day staging
def process_day(day: str, data_root: Path) -> pl.DataFrame:
    roster = (
        pl.read_parquet(
            v2(data_root) / "roster" / f"{day}.parquet",
            columns=["day", "clock", "rank", "ticker", "status", "fill_px"],
        )
        .filter(pl.col("clock").is_in(list(CLOCKS)))
        .select(KEYS + ["status", "fill_px"])
        .rename({"status": "roster_status", "fill_px": "roster_fill_px"})
    )

    pan = ls.load_panel([day], data_root, clocks=CLOCKS, with_roster=False, with_tape=False)
    pan = pan.select(KEYS + ["t", "session_end", "peak_gain", "px", "px_et"]).with_columns(
        pl.col("session_end").cast(pl.Int64)
    )
    pan13 = pan.with_columns(
        pl.min_horizontal(pl.lit(END, dtype=pl.Int64), pl.col("session_end")).alias("end_lt13")
    )

    # ---- observed-window MFE, fresh right edge, NEVER the roster mfe_day label
    w13 = (
        pan13.filter(pl.col("t") <= pl.col("end_lt13"))
        .group_by(KEYS)
        .agg(
            _finite("peak_gain").max().alias("mfe_lt13"),
            pl.col("px_et").filter(pl.col("px_et").is_finite()).max().alias("obs_lt13"),
            pl.col("end_lt13").first().alias("end_lt13"),
        )
    )
    w13 = w13.with_columns((pl.col("end_lt13") - pl.col("obs_lt13")).alias("gap_lt13"))
    wf = (
        pan.filter(pl.col("t") <= pl.col("session_end"))
        .group_by(KEYS)
        .agg(
            _finite("peak_gain").max().alias("mfe_full"),
            pl.col("px_et").filter(pl.col("px_et").is_finite()).max().alias("obs_full"),
            pl.col("session_end").first().alias("end_full"),
        )
    )
    wf = wf.with_columns((pl.col("end_full") - pl.col("obs_full")).alias("gap_full"))
    win = w13.join(wf, on=KEYS, how="left")
    win = win.with_columns(
        [_class_expr(m, g).alias(f"mfe_class_{w}") for w, (m, g) in MFE_WINDOWS.items()]
    )

    # ---- last completed-bar mark at each recorded snapshot
    mark_frames = [
        pan.filter(pl.col("t") <= s)
        .group_by(KEYS, maintain_order=True)
        .agg(_finite("px").last().alias(f"px_{s}"))
        for s in SNAPSHOTS
    ]
    marks = reduce(lambda a, b: a.join(b, on=KEYS, how="full", coalesce=True), mark_frames)

    out = []
    for book in BOOKS:
        members = pl.read_parquet(book_dir(data_root, book) / "days" / f"{day}.members.parquet")
        daily = pl.read_parquet(book_dir(data_root, book) / "days" / f"{day}.daily.parquet")
        # fills carry BOTH the per-leg cost ("side_cost") and the direction ("side"); rename
        # to the members convention so the join/group keys are the cost parameter.
        fills = pl.read_parquet(book_dir(data_root, book) / "days" / f"{day}.fills.parquet").rename(
            {"side_cost": "side", "side": "leg"}
        )
        gk = ["day", "clock", "n", "side", "policy", "ticker"]

        if fills.height:
            split = fills.group_by(gk).agg(
                pl.col("fee_fraction").filter(pl.col("leg") == "buy").sum().alias("entry_fee"),
                pl.col("fee_fraction").filter(pl.col("leg") == "sell").sum().alias("exit_fee"),
                pl.col("gross_fraction").filter(pl.col("leg") == "buy").sum().alias("buy_gross"),
                pl.col("gross_fraction").filter(pl.col("leg") == "sell").sum().alias("sell_gross"),
                pl.col("shares_per_capital")
                .filter(pl.col("reason") == "initial")
                .sum()
                .alias("initial_q"),
                pl.col("exec_et")
                .filter((pl.col("leg") == "sell") & pl.col("reason").is_in(list(RELEASE_REASONS)))
                .min()
                .alias("release_exec"),
                (pl.col("leg") == "sell").sum().alias("n_sells"),
                (pl.col("leg") == "buy").sum().alias("n_buys"),
            )
            res = [
                fills.group_by(gk).agg(
                    (
                        pl.col("shares_per_capital")
                        * pl.when(pl.col("leg") == "buy").then(1.0).otherwise(-1.0)
                    )
                    .filter(pl.col("exec_et") <= s)
                    .sum()
                    .alias(f"res_shares_{s}")
                )
                for s in SNAPSHOTS
            ]
            split = reduce(lambda a, b: a.join(b, on=gk, how="full", coalesce=True), [split] + res)
        else:
            # zero-schema fill file is a legal empty book only if nothing filled
            if members.filter(pl.col("reentries") > 0).height:
                raise ValueError(f"{day} {book}: re-entries without execution records")
            split = None

        # A corrected source may omit a snapshot that is calendar-invalid for its session
        # (e.g. the 13:00 ruler on a half day ending before 13:00). Project the exposure
        # columns that ACTUALLY EXIST by name and reconstruct the absent ones below; never
        # demand a column by assumption and never pretend a missing date was recorded.
        recorded_exposure = tuple(s for s in SNAPSHOTS if f"exposure_{s}" in daily.columns)
        book_daily = daily.select(
            [
                "day",
                "clock",
                "n",
                "side",
                "policy",
                "ret",
                "unknown",
                "affordability_unknown",
                "max_positions",
                *[f"exposure_{s}" for s in recorded_exposure],
            ]
        )
        claims = members.join(book_daily, on=["day", "clock", "n", "side", "policy"], how="left")
        # Keep the schema identical across days: an absent snapshot column becomes an
        # explicit null in its natural position, never a positional slip or a fake date.
        if len(recorded_exposure) != len(SNAPSHOTS):
            claims = claims.with_columns(
                *[
                    pl.lit(None, dtype=pl.Float64).alias(f"exposure_{s}")
                    for s in SNAPSHOTS
                    if s not in recorded_exposure
                ]
            )
        if split is not None:
            claims = claims.join(split, on=gk, how="left")
        else:
            claims = claims.with_columns(
                *[
                    pl.lit(None, dtype=pl.Float64).alias(c)
                    for c in (
                        "entry_fee",
                        "exit_fee",
                        "buy_gross",
                        "sell_gross",
                        "initial_q",
                        "release_exec",
                    )
                ],
                *[pl.lit(0, dtype=pl.Int64).alias(c) for c in ("n_sells", "n_buys")],
                *[pl.lit(0.0, dtype=pl.Float64).alias(f"res_shares_{s}") for s in SNAPSHOTS],
            )
        claims = (
            claims.drop("day")
            .with_columns(pl.lit(day).alias("day"))
            .join(roster, on=KEYS, how="left")
            .join(win, on=KEYS, how="left")
            .join(marks, on=KEYS, how="left")
        )
        claims = claims.rename({"net_pnl": "net", "gross_pnl": "gross"}).with_columns(
            pl.lit(book).alias("book"), pl.col("unknown").alias("book_unknown")
        )

        # residual share accounting and per-claim mark value (original-notional fraction)
        for s in SNAPSHOTS:
            claims = claims.with_columns(
                ((pl.col(f"res_shares_{s}") > 1e-12).fill_null(False)).alias(f"holding_{s}"),
                pl.when(pl.col("initial_q").is_finite() & (pl.col("initial_q") > 0))
                .then(pl.col(f"res_shares_{s}") / pl.col("initial_q"))
                .otherwise(None)
                .alias(f"resfrac_{s}"),
                pl.when(
                    (pl.col("initial_q") > 0)
                    & pl.col("roster_fill_px").is_finite()
                    & pl.col(f"px_{s}").is_finite()
                )
                .then(
                    pl.col(f"res_shares_{s}")
                    / pl.col("initial_q")
                    * pl.col(f"px_{s}")
                    / pl.col("roster_fill_px")
                )
                .otherwise(None)
                .alias(f"resmark_{s}"),
            )

        # ---- exposure provenance (ORIGINAL PORTFOLIO capital).
        # A recorded snapshot column is authoritative and kept as-is. Where the corrected
        # source omits one (calendar-invalid for that session), reconstruct the book's
        # exposure at the requested minute from executed fills (remaining signed shares)
        # x the last observed past mark. A book with no remaining quantity is a valid 0; a
        # remaining asset with no observed mark is UNKNOWN (null), NEVER 0.
        grp = ["day", "clock", "n", "side", "policy"]
        recon_aggs: list[pl.Expr] = []
        for s in SNAPSHOTS:
            held = pl.col(f"res_shares_{s}").fill_null(0.0)
            marked = held > 1e-12
            recon_aggs.append(
                pl.when(marked)
                .then(held * pl.col(f"px_{s}"))
                .otherwise(0.0)
                .sum()
                .alias(f"_recon_sum_{s}")
            )
            recon_aggs.append(
                (marked & ~pl.col(f"px_{s}").is_finite()).any().alias(f"_recon_unmarked_{s}")
            )
        claims = claims.join(claims.group_by(grp).agg(recon_aggs), on=grp, how="left")
        for s in SNAPSHOTS:
            if s in recorded_exposure:
                status = pl.lit("recorded")
            else:
                recon = (
                    pl.when(pl.col(f"_recon_unmarked_{s}"))
                    .then(None)
                    .otherwise(pl.col(f"_recon_sum_{s}"))
                )
                late = pl.col("end_full").is_not_null() & (pl.col("end_full") < s)
                claims = claims.with_columns(recon.alias(f"exposure_{s}"))
                status = (
                    pl.when(recon.is_null() & late)
                    .then(pl.lit("unknown_calendar_not_applicable"))
                    .when(recon.is_null())
                    .then(pl.lit("unknown_unresolved_asset"))
                    .when(recon.abs() <= 1e-12)
                    .then(pl.lit("reconstructed_flat_zero"))
                    .otherwise(pl.lit("reconstructed_from_fills"))
                )
            claims = claims.with_columns(status.alias(f"exposure_{s}_status"))
        claims = claims.drop(
            [f"_recon_sum_{s}" for s in SNAPSHOTS] + [f"_recon_unmarked_{s}" for s in SNAPSHOTS]
        )

        # UNKNOWN cashflows are censored at the book level and can never leak into a sum.
        for col in ("net", "gross", "fees", "entry_fee", "exit_fee", "buy_gross", "sell_gross"):
            claims = claims.with_columns(
                pl.when(pl.col("book_unknown")).then(None).otherwise(pl.col(col)).alias(col)
            )
        claims = claims.with_columns(
            (pl.col("roster_status") == "blocked").fill_null(False).alias("unfilled"),
            (pl.col("net").is_not_null()).alias("known"),
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
        # ---- exact identities (gross/fee additive in original portfolio capital)
        chk = claims.filter(pl.col("known"))
        if chk.height:
            bad_net = float(
                chk.select((pl.col("net") - pl.col("gross") + pl.col("fees")).abs().max()).item()
            )
            bad_gross = float(
                chk.select(
                    (
                        pl.col("gross")
                        - pl.col("sell_gross").fill_null(0.0)
                        + pl.col("buy_gross").fill_null(0.0)
                    )
                    .abs()
                    .max()
                ).item()
            )
            bad_fee = float(
                chk.select(
                    (
                        pl.col("fees")
                        - pl.col("entry_fee").fill_null(0.0)
                        - pl.col("exit_fee").fill_null(0.0)
                    )
                    .abs()
                    .max()
                ).item()
            )
            if not (bad_net < 1e-9 and bad_gross < 1e-9 and bad_fee < 1e-9):
                raise AssertionError(
                    f"{day} {book}: identity break net={bad_net} gross={bad_gross} fee={bad_fee}"
                )
        out.append(claims)

    return pl.concat(out, how="diagonal_relaxed")


def write_day(stage: Path, day: str, claims: pl.DataFrame, run_id: str) -> None:
    p = stage / f"{day}.claims.parquet"
    tmp = p.with_suffix(".tmp.parquet")
    claims.write_parquet(tmp)
    tmp.replace(p)
    (stage / f"{day}.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "day": day,
                "claims": claims.height,
                "producer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            }
        )
        + "\n"
    )


# --------------------------------------------------------------------- reporting helpers
def _tail_concentration(frame: pl.DataFrame, group_col: str, total: float, k: int) -> dict:
    """Top/bottom tail removal over claim-slots; day groups hold exactly N slots each."""
    g = (
        frame.group_by(group_col)
        .agg(pl.col("net").sum().alias("net"), pl.len().alias("slots"))
        .sort("net", descending=True)
    )
    nets, slots = g["net"], g["slots"]
    n_groups = g.height
    total_slots = int(frame.height)
    top = nets.head(k).sum() if n_groups else 0.0
    bottom = nets.tail(k).sum() if n_groups else 0.0
    top_slots = int(slots.head(k).sum()) if n_groups else 0
    bottom_slots = int(slots.tail(k).sum()) if n_groups else 0
    return {
        "groups": n_groups,
        "slots": total_slots,
        "k": k,
        "top_groups": min(k, n_groups),
        "top_slots": top_slots,
        "top_sum": _safe_float(top),
        "top_share": None if not total else float(top) / total,
        "ev_ex_top_k": (
            None if total_slots - top_slots <= 0 else (total - top) / (total_slots - top_slots)
        ),
        "bottom_groups": min(k, n_groups),
        "bottom_slots": bottom_slots,
        "bottom_sum": _safe_float(bottom),
        "bottom_share": None if not total else float(bottom) / total,
        "ev_ex_bottom_k": (
            None
            if total_slots - bottom_slots <= 0
            else (total - bottom) / (total_slots - bottom_slots)
        ),
        "denominator": "retained claim-slots (day groups hold N slots each)",
    }


# --------------------------------------------------------------------- report
def build_report(stage: Path, days: list[str], data_root: Path, pins: dict, run_id: str) -> dict:
    claimed = {
        b: pl.concat(
            [
                pl.read_parquet(stage / f"{d}.claims.parquet").filter(pl.col("book") == b)
                for d in days
            ],
            how="diagonal_relaxed",
        )
        for b in BOOKS
    }
    daily = {
        b: pl.read_parquet(book_dir(data_root, b) / "daily.parquet").filter(
            pl.col("day").is_in(days)
        )
        for b in BOOKS
    }
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

    support: list[dict] = []
    decomposition: list[dict] = []
    mfe_decomposition: list[dict] = []
    low_excursion: list[dict] = []
    tail: list[dict] = []
    rank_contrib: list[dict] = []
    residual: list[dict] = []
    exposure_provenance: list[dict] = []
    release_before: list[dict] = []
    concentration: dict = {}
    unknown_summary: list[dict] = []
    recon: list[dict] = []

    for book in BOOKS:
        claims = claimed[book]
        d = daily[book]
        # common-known comparison days: every policy resolves for that clock/N/cost
        common = (
            d.group_by(["day", "clock", "n", "side"])
            .agg(pl.col("ret").is_not_null().all().alias("all_known"), pl.len().alias("policies"))
            .filter(pl.col("all_known") & (pl.col("policies") == len(POLICIES)))
            .select(["day", "clock", "n", "side"])
        )

        rep = (
            d.group_by(["clock", "n", "side", "policy"])
            .agg(
                pl.len().alias("days_total"),
                pl.col("ret").is_not_null().sum().alias("known_days"),
                pl.col("unknown").sum().alias("unknown_days"),
                pl.col("affordability_unknown").sum().alias("affordability_unknown_sum"),
                pl.col("max_positions").max().alias("max_positions_max"),
                pl.col("ret").filter(pl.col("ret").is_not_null()).sum().alias("net_known_sum"),
                pl.col("ret").filter(pl.col("ret").is_not_null()).mean().alias("ev_known"),
            )
            .with_columns(pl.lit(book).alias("book"))
        )
        cm = (
            d.join(common, on=["day", "clock", "n", "side"], how="semi")
            .group_by(["clock", "n", "side"])
            .agg(pl.len().alias("rows"), pl.col("day").n_unique().alias("common_days"))
        )
        rep = rep.join(cm, on=["clock", "n", "side"], how="left")
        support.extend(rep.to_dicts())

        common_claims = claims.join(common, on=["day", "clock", "n", "side"], how="semi")
        known = common_claims.filter(pl.col("known"))
        unk = (
            claims.filter(~pl.col("known"))
            .group_by(["clock", "n", "side", "policy"])
            .agg(
                pl.len().alias("unknown_claims"),
                pl.col("day").n_unique().alias("unknown_claim_days"),
                pl.col("unfilled").sum().alias("unfilled_claims_all"),
            )
        )
        unfilled_all = (
            claims.filter(pl.col("unfilled") & pl.col("known"))
            .group_by(["clock", "n", "side", "policy"])
            .agg(
                pl.len().alias("unfilled_known_claims"),
                pl.col("reentries").sum().alias("unfilled_reentries"),
            )
        )

        dec = known.group_by(["clock", "n", "side", "policy"]).agg(
            pl.len().alias("known_claims"),
            pl.col("day").n_unique().alias("common_days"),
            pl.col("net").sum().alias("net"),
            pl.col("gross").sum().alias("gross_move"),
            pl.col("entry_fee").sum().alias("entry_fee"),
            pl.col("exit_fee").sum().alias("exit_fee"),
            pl.col("fees").sum().alias("fees"),
            pl.col("unfilled").sum().alias("unfilled_claims"),
            (pl.col("n_sells") > 0).sum().alias("claims_with_a_sale"),
            (pl.col("n_buys") > 1).sum().alias("claims_with_reentry"),
        )
        dec = (
            dec.join(unk, on=["clock", "n", "side", "policy"], how="left")
            .join(unfilled_all, on=["clock", "n", "side", "policy"], how="left")
            .with_columns(
                pl.col("unknown_claims").fill_null(0),
                pl.col("unknown_claim_days").fill_null(0),
                pl.col("unfilled_claims_all").fill_null(0),
                pl.col("unfilled_known_claims").fill_null(0),
                pl.col("unfilled_reentries").fill_null(0),
            )
        )
        dec_rep = (
            d.join(common, on=["day", "clock", "n", "side"], how="semi")
            .group_by(["clock", "n", "side", "policy"])
            .agg(
                pl.col("ret").sum().alias("reported_ret_sum"),
                pl.col("ret").is_not_null().sum().alias("reported_known_days"),
            )
        )
        dec = dec.join(dec_rep, on=["clock", "n", "side", "policy"], how="left").with_columns(
            pl.lit(book).alias("book"),
            (pl.col("net") - pl.col("reported_ret_sum")).alias("recon_abs_diff"),
        )
        decomposition.extend(dec.to_dicts())
        recon.extend(
            dec.select(
                [
                    "book",
                    "clock",
                    "n",
                    "side",
                    "policy",
                    "net",
                    "reported_ret_sum",
                    "recon_abs_diff",
                ]
            ).to_dicts()
        )

        # exact full-support reconciliation: sum member dollars == sum daily ret on known dates
        allrep = (
            d.filter(pl.col("ret").is_not_null())
            .group_by(["clock", "n", "side", "policy"])
            .agg(
                pl.col("ret").sum().alias("ret_sum"), pl.col("day").n_unique().alias("n_known_days")
            )
        )
        allclaims = (
            claims.filter(pl.col("known"))
            .group_by(["clock", "n", "side", "policy"])
            .agg(pl.col("net").sum().alias("net_sum"), pl.len().alias("n_claims"))
        )
        joined = allclaims.join(
            allrep, on=["clock", "n", "side", "policy"], how="full", coalesce=True
        )
        joined = joined.filter(pl.col("ret_sum").is_not_null() | pl.col("net_sum").is_not_null())
        for r in joined.to_dicts():
            r["book"] = book
            r["abs_diff"] = (
                None
                if r["net_sum"] is None or r["ret_sum"] is None
                else abs(r["net_sum"] - r["ret_sum"])
            )
            recon.append(
                {
                    k: v
                    for k, v in r.items()
                    if k
                    in ("book", "clock", "n", "side", "policy", "net_sum", "ret_sum", "abs_diff")
                }
            )

        # ---- MFE-class decomposition over both observed windows
        for win in MFE_WINDOWS:
            ccol = f"mfe_class_{win}"
            mf = (
                known.group_by(["clock", "n", "side", "policy", ccol])
                .agg(
                    pl.len().alias("claims"),
                    pl.col("net").sum().alias("net"),
                    pl.col("gross").sum().alias("gross_move"),
                    pl.col("entry_fee").sum().alias("entry_fee"),
                    pl.col("exit_fee").sum().alias("exit_fee"),
                    pl.col("fees").sum().alias("fees"),
                    (pl.col("net") > 0).sum().alias("win_claims"),
                    (pl.col("net") <= 0).sum().alias("lose_claims"),
                )
                .rename({ccol: "mfe_class"})
            )
            mf = mf.with_columns(pl.lit(win).alias("window"), pl.lit(book).alias("book"))
            mfe_decomposition.extend(mf.to_dicts())

        # ---- low-excursion tax (full observed window): duds that still cost dollars
        low = (
            known.filter(pl.col("mfe_class_full") == "MFElt10")
            .group_by(["clock", "n", "side", "policy"])
            .agg(
                pl.len().alias("claims"),
                pl.col("gross").sum().alias("gross_move"),
                (-pl.col("gross").clip(upper_bound=0)).sum().alias("gross_loss_dollars"),
                (-pl.col("net").clip(upper_bound=0)).sum().alias("net_loss_dollars"),
                pl.col("fees").sum().alias("friction_tax"),
                pl.col("net").sum().alias("net"),
            )
            .with_columns(pl.lit(book).alias("book"), pl.lit("low_excursion_tax").alias("kind"))
        )
        low_excursion.extend(low.to_dicts())

        # ---- paper tail: big observed MFE destroyed (net<=0) vs preserved (net>0)
        tl = (
            known.filter(pl.col("mfe_class_full").is_in(["MFE30_100", "MFE100"]))
            .group_by(["clock", "n", "side", "policy"])
            .agg(
                pl.len().alias("claims"),
                (pl.col("net") <= 0).sum().alias("destroyed_claims"),
                pl.col("net").filter(pl.col("net") <= 0).sum().alias("destroyed_net"),
                (pl.col("net") > 0).sum().alias("preserved_claims"),
                pl.col("net").filter(pl.col("net") > 0).sum().alias("preserved_net"),
                pl.col("net").sum().alias("net"),
                (pl.col("net") * pl.col("n")).mean().alias("mean_net_return"),
                _finite("mfe_full").mean().alias("mean_mfe_full"),
                (pl.col("entry_fee") + pl.col("exit_fee")).mean().alias("mean_friction"),
            )
            .with_columns(pl.lit(book).alias("book"))
        )
        tl = tl.with_columns(
            pl.when(pl.col("mean_mfe_full") > 0)
            .then(pl.col("mean_net_return") / pl.col("mean_mfe_full"))
            .otherwise(None)
            .alias("capture_ratio")
        )
        tail.extend(tl.to_dicts())

        # ---- rank dollars on common-known days
        rk = (
            known.group_by(["clock", "n", "side", "policy", "rank"])
            .agg(
                pl.len().alias("claims"),
                pl.col("net").sum().alias("net"),
                pl.col("gross").sum().alias("gross_move"),
                pl.col("fees").sum().alias("fees"),
            )
            .with_columns(pl.lit(book).alias("book"))
        )
        rank_contrib.extend(rk.to_dicts())

        # ---- residual exposure / holding counts (known claims; exposure_<s> is the
        # recorded value where present, else the reconstructed one, with status counts
        # exposing which). The full-census provenance over all claims is below.
        for s in SNAPSHOTS:
            rs = (
                known.group_by(["clock", "n", "side", "policy"])
                .agg(
                    pl.len().alias("claims"),
                    pl.col(f"holding_{s}").sum().alias("still_owned"),
                    pl.col(f"resfrac_{s}").mean().alias("mean_resfrac"),
                    pl.col(f"resmark_{s}")
                    .filter(pl.col(f"resmark_{s}").is_finite())
                    .mean()
                    .alias("mean_resmark"),
                    pl.col(f"resmark_{s}").sum().alias("sum_resmark"),
                    pl.col(f"exposure_{s}").mean().alias("mean_recorded_exposure"),
                    pl.col(f"exposure_{s}").max().alias("max_recorded_exposure"),
                    *[
                        (pl.col(f"exposure_{s}_status") == st).sum().alias(f"n_exposure_{st}")
                        for st in EXPOSURE_STATUSES
                    ],
                )
                .with_columns(pl.lit(book).alias("book"), pl.lit(s).alias("snapshot"))
            )
            residual.extend(rs.to_dicts())

        # ---- exposure provenance census over ALL claims (known and censored alike).
        # Recorded, reconstructed and UNKNOWN are never conflated; a null exposure is an
        # UNKNOWN asset, explicitly NOT known cash at zero.
        for s in SNAPSHOTS:
            ep = (
                claims.group_by(["clock", "n", "side", "policy"])
                .agg(
                    pl.len().alias("claims"),
                    *[
                        (pl.col(f"exposure_{s}_status") == st).sum().alias(f"n_{st}")
                        for st in EXPOSURE_STATUSES
                    ],
                    pl.col(f"exposure_{s}").is_not_null().sum().alias("n_valued"),
                    pl.col(f"exposure_{s}").null_count().alias("n_unknown"),
                    pl.col(f"exposure_{s}")
                    .filter(pl.col(f"exposure_{s}").is_not_null())
                    .mean()
                    .alias("mean_exposure"),
                    pl.col(f"exposure_{s}").max().alias("max_exposure"),
                )
                .with_columns(pl.lit(book).alias("book"), pl.lit(s).alias("snapshot"))
            )
            exposure_provenance.extend(ep.to_dicts())

        # ---- concentration: top/bottom 5 and 10 days and names
        for (clock, n, pol, sd), g in known.group_by(["clock", "n", "policy", "side"]):
            tot = float(g["net"].sum())
            key = f"{book}_c{clock}_n{n}_{pol}_{sd}"
            concentration[key] = {
                "book": book,
                "clock": clock,
                "n": n,
                "policy": pol,
                "side": sd,
                "total_net": _safe_float(tot),
                "day5": _tail_concentration(g, "day", tot, 5),
                "day10": _tail_concentration(g, "day", tot, 10),
                "name5": _tail_concentration(g, "ticker", tot, 5),
                "name10": _tail_concentration(g, "ticker", tot, 10),
            }

        # ---- release-before first +5/+15/+30 push signals (harvestability first-push `t`)
        for n in VIEWS:
            sign = harv.filter(pl.col("rank") <= n)
            rawn = sign.group_by(["clock", "end", "threshold"]).agg(
                pl.len().alias("raw_signals"), pl.col("day").n_unique().alias("signal_days")
            )
            rel = sign.join(
                claims.filter(pl.col("n") == n).select(
                    KEYS + ["n", "side", "policy", "release_exec"]
                ),
                on=KEYS,
                how="left",
            )
            g = rel.group_by(["clock", "end", "threshold", "n", "side", "policy"]).agg(
                pl.len().alias("signals"),
                (pl.col("release_exec").is_not_null() & (pl.col("release_exec") < pl.col("t")))
                .sum()
                .alias("released_before"),
                (pl.col("release_exec").is_not_null() & (pl.col("release_exec") <= pl.col("t") - 5))
                .sum()
                .alias("released_before_5"),
                (
                    pl.col("release_exec").is_not_null()
                    & (pl.col("release_exec") <= pl.col("t") - 30)
                )
                .sum()
                .alias("released_before_30"),
            )
            g = g.join(rawn, on=["clock", "end", "threshold"], how="left").with_columns(
                pl.lit(book).alias("book"),
                (pl.col("released_before") / pl.col("raw_signals")).alias("released_before_share"),
            )
            release_before.extend(g.to_dicts())

        # ---- UNKNOWN census (never zeroed)
        unk_rows = (
            claims.filter(~pl.col("known"))
            .group_by(["clock", "n", "side", "policy"])
            .agg(pl.len().alias("excluded_claims"), pl.col("day").n_unique().alias("excluded_days"))
            .with_columns(pl.lit(book).alias("book"))
        )
        unknown_summary.extend(unk_rows.to_dicts())

    max_recon = max([abs(r["abs_diff"]) for r in recon if r.get("abs_diff") is not None] or [0.0])
    max_common_recon = max(
        [abs(r["recon_abs_diff"]) for r in decomposition if r.get("recon_abs_diff") is not None]
        or [0.0]
    )

    artifact = {
        "kind": KIND,
        "producer": "factory/scripts/owned_claim_loss_sources.py",
        "producer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "run_id": run_id,
        "ruler": {
            "clocks": list(CLOCKS),
            "views": list(VIEWS),
            "sides_bps_per_side": [50.0, 75.0],
            "policies": list(POLICIES),
            "end_lt13": END,
            "snapshots": list(SNAPSHOTS),
            "mfe_edges": {name: edge for edge, name in MFE_EDGES},
            "mfe_windows": list(MFE_WINDOWS),
            "endpoint_gap_max": ENDPOINT_GAP_MAX,
            "release_ends": list(REL_ENDS),
            "release_thresholds": list(REL_THRESHOLDS),
            "new_buy_quantity": "shares fixed from the completed decision mark at 90% of "
            "reserved settled budget; price-gap funding failures are "
            "UNKNOWN, never resized or leveraged",
        },
        "definitions": {
            "money_unit": "original portfolio capital: member net_pnl dollars; "
            "sum over selected ranks == daily ret on known dates (asserted)",
            "book_unknown": "a censored book (missing/attempted-unknown price) is excluded "
            "from every dollar sum and counted; never zeroed",
            "unfilled_cash": "a blocked entry slot is KNOWN cash at zero dollars, distinct "
            "from UNKNOWN",
            "fee_split": "entry_fee from buy-leg fee_fraction, exit_fee from sell-leg "
            "fee_fraction; net = gross_move - fees",
            "mfe_observed": "discovery-day panel peak_gain within the window; stale right "
            "edge (obs gap > 1 min) is incomplete_window, never a dud",
            "exposure_recorded": "a recorded exposure_<snapshot> column from the corrected "
            "source is authoritative and used unchanged; units are original portfolio capital",
            "exposure_missing": "where the corrected source omits a snapshot column that is "
            "calendar-invalid for that session (e.g. the 13:00 ruler on a half day ending "
            "earlier), exposure is reconstructed at the requested minute from executed fills "
            "(remaining signed shares) x the last observed past mark; a book with no "
            "remaining quantity is a valid 0, while an unresolved/unobserved remaining asset "
            "is UNKNOWN (null), never 0",
            "release_before": "policy's first state_release/fade execution before the "
            "harvestability first-push signal time t",
            "common_days": (
                f"days where all {len(POLICIES)} policies resolve for that clock/N/cost"
            ),
        },
        "inputs": {"data_root": str(data_root), **pins},
        "test_days": days,
        "support": support,
        "reconciliation": {
            "books": recon,
            "max_abs_diff_sum_member_net_vs_sum_daily_ret": _safe_float(max_recon),
            "max_abs_diff_common_net_vs_daily_ret": _safe_float(max_common_recon),
            "identity": "sum(member net) = sum(daily ret) per (book,clock,N,cost,policy) "
            "on known dates; common-day decomposition is a subset",
        },
        "decomposition": decomposition,
        "mfe_decomposition": mfe_decomposition,
        "low_excursion_tax": low_excursion,
        "paper_tail": tail,
        "rank_contributions": rank_contrib,
        "residual": residual,
        "exposure_provenance": {
            "note": "per snapshot: how the exposure of every claim was obtained. Recorded "
            "columns are authoritative; absent calendar-invalid columns are reconstructed "
            "from executed fills x last past mark when legitimate (a fully liquidated book "
            "is a valid 0), otherwise UNKNOWN (null). n_unknown is never known cash at zero.",
            "statuses": list(EXPOSURE_STATUSES),
            "by_key": exposure_provenance,
        },
        "concentration": concentration,
        "release_before": release_before,
        "unknown": {
            "note": "censored books are excluded from dollar sums and counted; blocked entry "
            "slots are known cash (zero dollars), not UNKNOWN",
            "by_key": unknown_summary,
            "excluded_claims_total": int(sum(r["excluded_claims"] for r in unknown_summary)),
        },
        "caveats": [
            "Two realized books are replayed on the same original roster; both are mandatory "
            "(no fallback if a book is pending).",
            "13:00 (780) is a declared observed-window ruler, not a discovered optimum; the "
            "full window ends at each claim's session_end.",
            "Observed-window MFE uses discovery-day panel peak_gain only; roster mfe_day "
            "(post-session) and personality/route labels are never read.",
            "Stale right edges are incomplete_window, masked from dud/loss attribution.",
            "A snapshot exposure is read from the corrected source when recorded and only "
            "reconstructed from executed fills x last past mark when the column is absent "
            "for a calendar-invalid minute (half day vs the 13:00 ruler); a missing exposure "
            "is UNKNOWN, never confused with a blocked/known-cash zero.",
            "50/75bps per side is modeled from the replay fill record, not quote-certified.",
            "release-before uses harvestability first-push signals as an ex-post reference, "
            "never a deployable signal.",
            "Rank/class/fee contributions are portfolio-capital dollars and additive; they "
            "are not per-claim returns and must not be averaged across policies.",
            "Only discovery-half data is read; no validation outcome or FREEZE marker was touched.",
        ],
    }
    return artifact


# --------------------------------------------------------------------- main
def main() -> int:
    global BOOKS
    root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument(
        "--out", type=Path, default=root / "factory/artifacts/owned_claim_loss_sources.json"
    )
    ap.add_argument(
        "--stage", type=Path, default=root / "factory/artifacts/owned_claim_loss_sources_days"
    )
    ap.add_argument(
        "--books",
        default=",".join(BOOKS),
        help="comma list of realized books (default both, mandatory)",
    )
    ap.add_argument("--limit", type=int, help="smoke: process only the first N test days")
    a = ap.parse_args()

    chosen = tuple(x for x in a.books.split(",") if x)
    if not chosen or any(b not in BOOK_ROOT for b in chosen):
        raise SystemExit(f"--books must be a non-empty subset of {list(BOOK_ROOT)}")
    BOOKS = chosen

    hman_path = v2(a.data_root) / "harvestability" / "manifest.json"
    hman = json.loads(hman_path.read_text())
    if hman.get("protected_half_read"):
        raise SystemExit("harvestability manifest flags protected-half reads; refusing")
    if list(hman.get("discovery_days", [])) != ls.discovery_days(a.data_root):
        raise SystemExit("harvestability manifest is not exactly the guarded discovery half")

    days = discover_days(a.data_root, BOOKS[0])
    for b in BOOKS[1:]:
        bd = discover_days(a.data_root, b)
        if bd != days:
            raise SystemExit(f"{b} test dates differ from {BOOKS[0]}; refusing a mixed panel")
    if a.limit:
        days = days[: a.limit]
    ls.assert_coverage(days, a.data_root, "panel")
    ls.assert_coverage(days, a.data_root, "roster")

    def _meta(b: str) -> dict:
        m = json.loads((model_dir(a.data_root, b) / "metadata.json").read_text())
        return {
            "algorithm": m.get("algorithm") or "value_iteration",
            "run_id": m.get("run_id"),
            "script_sha256": m.get("script_sha256"),
            "objective": m.get("objective"),
            "folds": [
                {
                    "fold": f["fold"],
                    "train_days": len(f["train_days"]),
                    "test_days": len(f["test_days"]),
                }
                for f in m["folds"]
            ],
        }

    pins = {
        "split_sha256": sha256_file(v2(a.data_root) / "split.json"),
        "discovery_days_total": len(ls.discovery_days(a.data_root)),
        "harvestability": {
            "run_id": hman.get("run_id"),
            "source_sha256": hman.get("source_sha256"),
            "protected_half_read": bool(hman.get("protected_half_read")),
        },
        "harvestability_events_sha256": sha256_file(
            v2(a.data_root) / "harvestability" / "events.parquet"
        ),
        "books": {
            b: {
                "root": str(book_dir(a.data_root, b)),
                "daily_sha256": sha256_file(book_dir(a.data_root, b) / "daily.parquet"),
                "summary_sha256": sha256_file(book_dir(a.data_root, b) / "summary.parquet"),
                "model_metadata_path": str(model_dir(a.data_root, b) / "metadata.json"),
                "model_metadata_sha256": sha256_file(model_dir(a.data_root, b) / "metadata.json"),
                **_meta(b),
            }
            for b in BOOKS
        },
    }

    run_id = hashlib.sha256(
        (Path(__file__).read_text() + json.dumps(days) + json.dumps(sorted(BOOKS))).encode()
    ).hexdigest()
    a.stage.mkdir(parents=True, exist_ok=True)
    for index, day in enumerate(days):
        marker = a.stage / f"{day}.json"
        if marker.exists():
            try:
                if json.loads(marker.read_text()).get("run_id") == run_id:
                    continue
            except Exception:
                pass
        claims = process_day(day, a.data_root)
        write_day(a.stage, day, claims, run_id)
        if index % 25 == 0:
            print(f"{index + 1}/{len(days)} {day}", flush=True)

    artifact = build_report(a.stage, days, a.data_root, pins, run_id)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = a.out.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(artifact, indent=2, allow_nan=False) + "\n")
    tmp.replace(a.out)
    print(
        "Loss sources written",
        a.out,
        "days",
        len(days),
        "max_recon",
        artifact["reconciliation"]["max_abs_diff_sum_member_net_vs_sum_daily_ret"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
