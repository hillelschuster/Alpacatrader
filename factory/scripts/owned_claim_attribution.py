#!/usr/bin/env python3
"""Owned-claim dollar attribution over the discovery harvestability cashflows.

Exact, additive decomposition of the 13:00 (end=780) ruler for the three paired
policies ``hold`` / ``fade`` / ``bank5_f75_fade`` at N3 and N5, each clock
(540/560/569/571), on *common known days* only (all three policies resolve).

Everything is reported in ORIGINAL-DOLLAR denominators (one claim = one unit of
original entry notional, N = fill_px * shares = 1), so

    net_orig = gross_move - entry_fee - exit_fee
    gross_move = gross           (fraction of original notional)
    entry_fee  = side
    exit_fee   = (1 + gross) * side
    net_orig   = gross * (1 - side) - 2 * side

The reported harvestability member return is on the *entry-cash* denominator
``N * (1 + side)``, hence ``net_member = net_orig / (1 + side)`` reproduces the
stored ``members.ret`` exactly and the claim dollars recompose the published
daily portfolio net (``daily.pnl_sum``) once per N. Both are emitted and
reconciled; the scalar link is explicit, never silently folded.

MFE classes use the OBSERVED panel window ``peak_gain`` with ``t <= 780`` only.
The roster ``mfe_day`` (post-session bars) and any personality routes are NEVER
read. UNKNOWN cashflows are excluded from dollar sums and counted, never zeroed.
Only discovery days are touched; validation outcomes and FREEZE are never read.

Outputs are staged per day (bounded memory, resumable); the compact artifact is
written only after every requested day has landed. No API or data acquisition.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

os.environ.setdefault("POLARS_MAX_THREADS", "2")
import lifecycle_study as ls  # noqa: E402
import polars as pl  # noqa: E402

CLOCKS = (540, 560, 569, 571)
END = 780  # 13:00 ruler
POLICIES = ("hold", "fade", "bank5_f75_fade")
SIDES = (0.005, 0.0075)
VIEWS = (3, 5)
SNAPSHOTS = {585: "0945", 630: "1030", 690: "1130"}
THRESHOLDS = (0.05, 0.15, 0.30)
KEYS = ["day", "clock", "rank", "ticker"]
MFE_EDGES = ((0.10, "MFElt10"), (0.30, "MFE10_30"), (1.00, "MFE30_100"))
MFE_TOP = "MFE100"
ENDPOINT_GAP_MAX = 1  # fresh right edge only; not certification of interior tape coverage

KIND = "DISCOVERY-DIAGNOSTIC-NOT-ALPHA"


def hdir(data_root: Path) -> Path:
    return ls.v2_dir(data_root) / "harvestability"


def _finite(name: str) -> pl.Expr:
    c = pl.col(name)
    return c.filter(c.is_finite())


def mfe_class(mfe: float | None) -> str:
    if (
        mfe is None
        or not (isinstance(mfe, float) and mfe == mfe)
        or mfe in (float("inf"), float("-inf"))
    ):
        return "unknown_mfe"
    if mfe < MFE_EDGES[0][0]:
        return MFE_EDGES[0][1]
    if mfe < MFE_EDGES[1][0]:
        return MFE_EDGES[1][1]
    if mfe < MFE_EDGES[2][0]:
        return MFE_EDGES[2][1]
    return MFE_TOP


# --------------------------------------------------------------------- per-day staging
def process_day(day: str, data_root: Path, stage: Path) -> tuple[pl.DataFrame, pl.DataFrame]:
    h = hdir(data_root)
    pan = ls.load_panel([day], data_root, clocks=CLOCKS, with_roster=False, with_tape=False)
    pan = pan.filter(pl.col("clock").is_in(list(CLOCKS)))
    mem = pl.read_parquet(h / "days" / f"{day}.members.parquet").filter(
        (pl.col("end") == END)
        & pl.col("policy").is_in(list(POLICIES))
        & pl.col("clock").is_in(list(CLOCKS))
    )
    fill = pl.read_parquet(h / "days" / f"{day}.fills.parquet")
    if fill.height == 0 and fill.width == 0:
        if mem.filter((pl.col("status") == "filled") & ~pl.col("unknown")).height:
            raise ValueError(f"{day}: known filled cashflows without execution records")
        fill = pl.DataFrame(
            schema={
                "day": pl.String,
                "clock": pl.Int64,
                "rank": pl.Int64,
                "ticker": pl.String,
                "end": pl.Int64,
                "policy": pl.String,
                "reason": pl.String,
                "fraction": pl.Float64,
                "px": pl.Float64,
                "exec_et": pl.Int64,
            }
        )
    fill = fill.filter(
        (pl.col("end") == END)
        & pl.col("policy").is_in(list(POLICIES))
        & pl.col("clock").is_in(list(CLOCKS))
    ).unique()

    # ---- original execution price / entry notional per claim (panel is native, no roster labels)
    exec_anchor = pan.group_by(KEYS).agg(
        pl.col("fill_px").drop_nulls().first().alias("fill_px"),
        pl.col("entry_px").drop_nulls().first().alias("entry_px"),
        pl.col("status").first().alias("panel_status"),
        pl.col("session_end").first().alias("session_end"),
    )

    # Observed-window MFE, never roster mfe_day. A fresh right edge is required
    # before calling a low-excursion claim low; this does not prove interior bars
    # or quotes are complete. The last completed bar is effective_end-1.
    win = (
        pan.filter(pl.col("t") <= END)
        .group_by(KEYS)
        .agg(
            _finite("peak_gain").max().alias("mfe_window"),
            pl.col("px_et").filter(pl.col("px_et").is_finite()).max().alias("obs_end"),
            pl.when(pl.col("session_end").first() < END)
            .then(pl.col("session_end").first())
            .otherwise(END)
            .alias("_window_end"),
            # best executable open whose execution also lands within the ruler
            pl.col("sell_px")
            .filter(
                pl.col("sell_px").is_finite()
                & (
                    pl.col("sell_et")
                    <= pl.when(pl.col("session_end") < END)
                    .then(pl.col("session_end"))
                    .otherwise(END)
                )
            )
            .max()
            .alias("sell_max"),
        )
    )
    win = win.with_columns((pl.col("_window_end") - pl.col("obs_end")).alias("obs_gap"))
    win = win.with_columns(
        pl.when(pl.col("mfe_window").is_null() | ~pl.col("mfe_window").is_finite())
        .then(pl.lit("unknown_mfe"))
        .when(pl.col("obs_end").is_null() | (pl.col("obs_gap") > ENDPOINT_GAP_MAX))
        .then(pl.lit("incomplete_window"))
        .when(pl.col("mfe_window") < MFE_EDGES[0][0])
        .then(pl.lit(MFE_EDGES[0][1]))
        .when(pl.col("mfe_window") < MFE_EDGES[1][0])
        .then(pl.lit(MFE_EDGES[1][1]))
        .when(pl.col("mfe_window") < MFE_EDGES[2][0])
        .then(pl.lit(MFE_EDGES[2][1]))
        .otherwise(pl.lit(MFE_TOP))
        .alias("mfe_class")
    )

    # ---- residual snapshots: executed sales drive owned quantity; panel px marks the rest
    marks = pan.filter(pl.col("t") <= max(SNAPSHOTS)).sort(["day", "clock", "rank", "ticker", "t"])
    mark_frames = []
    for s in SNAPSHOTS:
        mf = (
            marks.filter(pl.col("t") <= s)
            .group_by(KEYS, maintain_order=True)
            .agg(_finite("px").last().alias(f"px_{s}"))
        )
        mark_frames.append(mf)
    marks_df = mark_frames[0]
    for mf in mark_frames[1:]:
        marks_df = marks_df.join(mf, on=KEYS, how="left")

    sold = fill.group_by(KEYS + ["policy"]).agg(
        pl.col("fraction").sum().alias("frac_sold"),
        pl.col("reason").unique().alias("reasons"),
        pl.col("exec_et").min().alias("first_exec"),
        pl.col("exec_et").max().alias("last_exec"),
        pl.len().alias("n_fills"),
        pl.col("reason").eq("bank").sum().alias("n_bank_fills"),
        pl.col("reason").eq("fade").sum().alias("n_fade_fills"),
        pl.col("reason").eq("window").sum().alias("n_window_fills"),
        (pl.col("fraction") < 1 - 1e-9).sum().alias("n_partial_fills"),
        *[
            pl.col("fraction").filter(pl.col("exec_et") <= s).sum().alias(f"sold_{s}")
            for s in SNAPSHOTS
        ],
    )
    # gross sale value per original notional share
    gross = fill.group_by(KEYS + ["policy"]).agg(
        (pl.col("fraction") * pl.col("px")).sum().alias("gross_val"),
        pl.col("fraction").sum().alias("frac_sum"),
    )
    gross = gross.join(exec_anchor.select(KEYS + ["fill_px"]), on=KEYS, how="left")
    gross = gross.with_columns(
        pl.when(pl.col("fill_px").is_finite() & (pl.col("fill_px") > 0))
        .then(pl.col("gross_val") / pl.col("fill_px") - 1)
        .otherwise(None)
        .alias("gross")
    )

    claims = (
        mem.select(KEYS + ["policy", "side", "status", "unknown", "ret"])
        .drop("day")
        .with_columns(pl.lit(day).alias("day"))
        .join(
            gross.select(KEYS + ["policy", "gross", "frac_sum"]), on=KEYS + ["policy"], how="left"
        )
        .join(sold, on=KEYS + ["policy"], how="left")
        .join(win, on=KEYS, how="left")
        .join(marks_df, on=KEYS, how="left")
        .join(exec_anchor.select(KEYS + ["fill_px", "session_end"]), on=KEYS, how="left")
    )

    # blocked slots never enter: KNOWN cash, zero dollars. Censored (UNKNOWN) claims keep
    # their partial sales in the fill record but their dollar fields stay NULL so they can
    # never leak into a sum; they are counted instead.
    cash = pl.col("status") == "blocked"
    censored = pl.col("unknown") == True  # noqa: E712
    claims = (
        claims.with_columns(
            [
                pl.when(cash)
                .then(0.0)
                .when(censored)
                .then(None)
                .otherwise(pl.col("gross"))
                .alias("gross"),
            ]
        )
        .with_columns(
            [
                pl.when(cash)
                .then(0.0)
                .when(censored)
                .then(None)
                .otherwise(pl.col("side"))
                .alias("entry_fee"),
                pl.when(cash)
                .then(0.0)
                .when(censored)
                .then(None)
                .otherwise((1.0 + pl.col("gross")) * pl.col("side"))
                .alias("exit_fee"),
                pl.when(cash)
                .then(0.0)
                .when(censored)
                .then(None)
                .otherwise(pl.col("gross") * (1 - pl.col("side")) - 2 * pl.col("side"))
                .alias("net_orig"),
                pl.when(cash)
                .then(pl.lit("blocked_cash"))
                .otherwise(pl.col("mfe_class"))
                .alias("mfe_class"),
            ]
        )
        .with_columns((pl.col("net_orig") / (1 + pl.col("side"))).alias("net_member"))
    )
    for s in SNAPSHOTS:
        claims = claims.with_columns(
            pl.when(cash)
            .then(0.0)
            .when(censored)
            .then(None)
            .otherwise(1.0 - pl.col(f"sold_{s}").fill_null(0.0))
            .alias(f"resfrac_{s}"),
            pl.when(cash)
            .then(0.0)
            .when(censored)
            .then(None)
            .otherwise(
                (1.0 - pl.col(f"sold_{s}").fill_null(0.0)) * pl.col(f"px_{s}") / pl.col("fill_px")
            )
            .alias(f"resmark_{s}"),
        )
    claims = claims.with_columns(
        (pl.col("frac_sold") >= 1 - 1e-9).fill_null(False).alias("full_exit"),
        (pl.col("frac_sold") < 1 - 1e-9).fill_null(False).alias("partial"),
        cash.alias("blocked_cash"),
        (pl.col("net_member") - pl.col("ret")).abs().alias("ret_check"),
    )
    # retrospective, explicitly UNATTAINABLE upper bound: best executable open within the ruler
    claims = claims.with_columns(
        pl.when(cash | censored | ~pl.col("fill_px").is_finite() | ~pl.col("sell_max").is_finite())
        .then(None)
        .otherwise(pl.col("sell_max") / pl.col("fill_px") - 1)
        .alias("upper_bound_ret")
    )

    # internal exactness guard: derived member return must reproduce stored member return
    chk = claims.filter(pl.col("ret").is_not_null() & pl.col("gross").is_not_null())
    if chk.height:
        bad = float(chk["ret_check"].max())
        if not (bad < 1e-9):
            raise AssertionError(f"{day}: derived member return mismatch {bad}")
    # share conservation only applies to RESOLVED cashflows; censored partial banks are legal
    bad_frac = claims.filter(
        (pl.col("unknown") == False)  # noqa: E712
        & pl.col("frac_sum").is_not_null()
        & ((pl.col("frac_sum") - 1).abs() > 1e-9)
    )
    if bad_frac.height:
        raise AssertionError(f"{day}: share conservation violated on {bad_frac.height} rows")
    ident = claims.filter(pl.col("net_orig").is_not_null())
    if ident.height:
        bad_id = float(
            (ident["gross"] - ident["entry_fee"] - ident["exit_fee"] - ident["net_orig"])
            .abs()
            .max()
        )
        if not (bad_id < 1e-9):
            raise AssertionError(f"{day}: fee identity broken {bad_id}")
    claims = claims.drop("ret_check")

    # ---- released-before-signal ex-post opportunity (never a policy signal)
    ev = pl.read_parquet(h / "days" / f"{day}.events.parquet")
    if ev.height == 0 and ev.width == 0:
        source_marker = json.loads((h / "days" / f"{day}.json").read_text())
        if source_marker.get("events") != 0:
            raise ValueError(f"{day}: zero-schema event file without a certified empty marker")
        ev = pl.DataFrame(
            schema={
                "day": pl.String,
                "clock": pl.Int64,
                "rank": pl.Int64,
                "ticker": pl.String,
                "end": pl.Int64,
                "threshold": pl.Float64,
                "t": pl.Int64,
                "continuation_5": pl.Float64,
                "continuation_30": pl.Float64,
            }
        )
    ev = ev.filter(
        (pl.col("end") == END)
        & pl.col("clock").is_in(list(CLOCKS))
        & pl.col("threshold").is_in(list(THRESHOLDS))
    )
    fade = fill.filter((pl.col("policy") == "fade") & (pl.col("reason") == "fade")).select(
        KEYS + [pl.col("exec_et").alias("fade_exec"), pl.col("px").alias("fade_px")]
    )
    ev = ev.drop("day").with_columns(pl.lit(day).alias("day")).join(fade, on=KEYS, how="left")
    ev = ev.with_columns(
        (pl.col("fade_exec") < pl.col("t")).fill_null(False).alias("released_before"),
        (pl.col("fade_exec") <= pl.col("t") - 5).fill_null(False).alias("released_before_5"),
        (pl.col("fade_exec") <= pl.col("t") - 30).fill_null(False).alias("released_before_30"),
    )
    pxmap = pan.select(KEYS + ["t", "px"]).drop_nulls("px")
    for h in (5, 30):
        ph = (
            ev.select(KEYS + ["fade_exec"])
            .join(pxmap, on=KEYS, how="left")
            .filter(pl.col("t") <= pl.col("fade_exec") + h)
            .sort(["day", "clock", "rank", "ticker", "t"])
            .group_by(KEYS + ["fade_exec"], maintain_order=True)
            .agg(pl.col("px").last().alias(f"px_rel_{h}"))
        )
        ev = ev.join(ph, on=KEYS + ["fade_exec"], how="left")
    ev = ev.with_columns(
        (pl.col("px_rel_5") / pl.col("fade_px") - 1).alias("missed_release_5"),
        (pl.col("px_rel_30") / pl.col("fade_px") - 1).alias("missed_release_30"),
        pl.col("continuation_5").alias("missed_signal_5"),
        pl.col("continuation_30").alias("missed_signal_30"),
    )
    ev = ev.select(
        [
            "day",
            "clock",
            "rank",
            "ticker",
            "threshold",
            "t",
            "fade_exec",
            "fade_px",
            "released_before",
            "released_before_5",
            "released_before_30",
            "missed_release_5",
            "missed_release_30",
            "missed_signal_5",
            "missed_signal_30",
        ]
    )
    return claims, ev


def write_day(
    stage: Path, day: str, claims: pl.DataFrame, ev: pl.DataFrame, run_id: str, h: Path
) -> None:
    for label, frame in (("claims", claims), ("events", ev)):
        p = stage / f"{day}.{label}.parquet"
        tmp = p.with_suffix(".tmp.parquet")
        frame.write_parquet(tmp)
        tmp.replace(p)
    (stage / f"{day}.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "day": day,
                "claims": claims.height,
                "events": ev.height,
                "producer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            }
        )
        + "\n"
    )


# --------------------------------------------------------------------- reporting helpers
def _tail_concentration(frame: pl.DataFrame, group_col: str, total: float, k: int) -> dict:
    """Tail removal with explicit retained-slot denominators.

    ``net_member`` is per one-slot entry cash, so the retained mean is
    ``(total - removed) / (slots - removed_slots)``; for day groups each day holds
    exactly N slots, reproducing the N*(days-k) portfolio-day denominator."""
    g = (
        frame.group_by(group_col)
        .agg(pl.col("net_member").sum().alias("net"), pl.len().alias("slots"))
        .sort("net", descending=True)
    )
    nets, slots = g["net"], g["slots"]
    n_groups = g.height
    total_slots = int(frame.height)
    top_groups = min(k, n_groups)
    worst_groups = min(k, n_groups)
    top = nets.head(k).sum() if n_groups else 0.0
    bottom = nets.tail(k).sum() if n_groups else 0.0
    top_slots = int(slots.head(k).sum()) if n_groups else 0
    worst_slots = int(slots.tail(k).sum()) if n_groups else 0
    return {
        "groups": n_groups,
        "slots": total_slots,
        "k": k,
        "top_groups": top_groups,
        "top_slots": top_slots,
        "top_sum": float(top),
        "top_share": None if not total else float(top) / total,
        "ev_ex_top_k": (
            None if total_slots - top_slots <= 0 else (total - top) / (total_slots - top_slots)
        ),
        "worst_groups": worst_groups,
        "worst_slots": worst_slots,
        "worst_sum": float(bottom),
        "worst_share": None if not total else float(bottom) / total,
        "ev_ex_worst_k": (
            None
            if total_slots - worst_slots <= 0
            else (total - bottom) / (total_slots - worst_slots)
        ),
        "denominator": "retained claim-slots (day groups hold N slots each)",
    }


def _safe_float(v):
    if v is None:
        return None
    v = float(v)
    return None if v != v else v


def build_report(stage: Path, days: list[str], data_root: Path, run_id: str) -> dict:
    claims = pl.concat(
        [pl.read_parquet(stage / f"{d}.claims.parquet") for d in days], how="diagonal_relaxed"
    )
    ev = pl.concat(
        [pl.read_parquet(stage / f"{d}.events.parquet") for d in days], how="diagonal_relaxed"
    )
    daily = pl.read_parquet(hdir(data_root) / "daily.parquet").filter(
        (pl.col("end") == END)
        & pl.col("policy").is_in(list(POLICIES))
        & pl.col("day").is_in(days)
        & pl.col("clock").is_in(list(CLOCKS))
    )

    # common known day = all three paired policies resolve for that clock/side/N
    per_day = daily.group_by(["day", "clock", "side", "n"]).agg(
        pl.col("ret").is_not_null().all().alias("trio_known"),
        pl.col("pnl_sum").sum().alias("pnl_sum"),
        pl.col("ret").sum().alias("ret_sum"),
        pl.len().alias("policies"),
    )
    common = per_day.filter(pl.col("trio_known") & (pl.col("policies") == 3))

    # UNKNOWN is never zeroed: account for every censored claim in the staged window,
    # both inside and outside the common-known decomposition set.
    unknown_overall = (
        claims.filter(pl.col("unknown") == True)  # noqa: E712
        .group_by(["clock", "policy", "side", "status"])
        .agg(pl.len().alias("claims"), pl.col("day").n_unique().alias("days"))
    ).to_dicts()

    by_key: list[dict] = []
    by_mfe: list[dict] = []
    recon: list[dict] = []
    rank_contrib: list[dict] = []
    peer_contrib: list[dict] = []
    residual: list[dict] = []
    matched: list[dict] = []
    concentration: dict = {}
    low_excursion: list[dict] = []
    terminal: list[dict] = []
    upper: list[dict] = []

    for n in VIEWS:
        cn = common.filter(pl.col("n") == n).select(["day", "clock", "side"])
        sub = claims.filter(pl.col("rank") <= n).join(cn, on=["day", "clock", "side"], how="semi")
        known = sub.filter(pl.col("unknown") == False)  # noqa: E712
        unk = (
            sub.filter(pl.col("unknown") == True)  # noqa: E712
            .group_by(["clock", "side", "policy"])
            .agg(pl.len().alias("unknown_claims"), pl.col("day").n_unique().alias("unknown_days"))
        )
        # ---- decomposition + reconciliation (exact)
        dec = (
            known.group_by(["clock", "side", "policy"])
            .agg(
                pl.len().alias("claims"),
                pl.col("gross").sum().alias("gross_move"),
                pl.col("entry_fee").sum().alias("entry_fee"),
                pl.col("exit_fee").sum().alias("exit_fee"),
                pl.col("net_orig").sum().alias("net_orig"),
                pl.col("net_member").sum().alias("net_member"),
                pl.col("day").n_unique().alias("known_days"),
                ((pl.col("n_partial_fills") == 0) & (pl.col("status") == "filled"))
                .sum()
                .alias("full_exits"),
                (pl.col("n_partial_fills") > 0).sum().alias("partial_claims"),
                pl.col("n_bank_fills").sum().alias("bank_fills"),
                pl.col("n_fade_fills").sum().alias("fade_fills"),
                pl.col("n_window_fills").sum().alias("window_fills"),
                pl.col("first_exec").mean().alias("mean_first_exec"),
                pl.col("first_exec").median().alias("median_first_exec"),
                pl.col("first_exec")
                .filter(pl.col("status") == "filled")
                .min()
                .alias("earliest_first_exec"),
                pl.col("last_exec")
                .filter(pl.col("status") == "filled")
                .max()
                .alias("latest_last_exec"),
            )
            .join(unk, on=["clock", "side", "policy"], how="left")
            .with_columns(pl.col("unknown_claims").fill_null(0))
        )
        rep = (
            daily.filter(pl.col("n") == n)
            .join(cn, on=["day", "clock", "side"], how="semi")
            .group_by(["clock", "side", "policy"])
            .agg(
                pl.col("pnl_sum").sum().alias("reported_pnl_sum"),
                pl.col("ret").sum().alias("reported_ret_sum"),
                pl.col("day").n_unique().alias("reported_days"),
            )
        )
        rec = dec.join(rep, on=["clock", "side", "policy"], how="left").with_columns(
            pl.lit(n).alias("n"),
            (pl.col("net_member") - pl.col("reported_pnl_sum")).alias("recon_abs_diff"),
            (pl.col("reported_pnl_sum") / n).alias("reported_ret_sum_check"),
        )
        for r in rec.to_dicts():
            r["n"] = n
            r["dollar_denominator"] = "original_entry_notional"
            r["net_member_denominator"] = "entry_cash (= net_orig/(1+side))"
            by_key.append(r)
        recon.extend(
            rec.select(
                ["n", "clock", "side", "policy", "net_member", "reported_pnl_sum", "recon_abs_diff"]
            ).to_dicts()
        )

        # ---- MFE-class decomposition (gross/fee split + low-excursion tax)
        mf = known.group_by(["clock", "side", "policy", "mfe_class"]).agg(
            pl.len().alias("claims"),
            pl.col("gross").sum().alias("gross_move"),
            pl.col("entry_fee").sum().alias("entry_fee"),
            pl.col("exit_fee").sum().alias("exit_fee"),
            pl.col("net_orig").sum().alias("net_orig"),
            pl.col("net_member").sum().alias("net_member"),
        )
        for r in mf.to_dicts():
            r["n"] = n
            by_mfe.append(r)
        tax = (
            known.filter(pl.col("mfe_class") == "MFElt10")
            .group_by(["clock", "side", "policy"])
            .agg(
                pl.len().alias("claims"),
                pl.col("entry_fee").sum().alias("entry_fee"),
                pl.col("exit_fee").sum().alias("exit_fee"),
                (pl.col("entry_fee") + pl.col("exit_fee")).sum().alias("friction_tax"),
                pl.col("gross").sum().alias("gross_move"),
                (-pl.col("gross").clip(upper_bound=0)).sum().alias("gross_loss_dollars"),
                pl.col("gross").clip(lower_bound=0).sum().alias("gross_gain_dollars"),
                (-pl.col("net_member").clip(upper_bound=0)).sum().alias("net_loss_dollars"),
                pl.col("net_member").sum().alias("net_member"),
            )
        )
        for r in tax.to_dicts():
            r["n"] = n
            r["kind"] = "low_excursion_tax"
            low_excursion.append(r)

        # ---- rank contribution
        rk = known.group_by(["clock", "side", "policy", "rank"]).agg(
            pl.len().alias("claims"),
            pl.col("net_member").sum().alias("net_member"),
            pl.col("net_orig").sum().alias("net_orig"),
        )
        for r in rk.to_dicts():
            r["n"] = n
            rank_contrib.append(r)

        # ---- peer contribution: realized sibling cohort (ex-post co-movement, not a signal)
        for (clock, pol, sd, d), g in known.group_by(["clock", "policy", "side", "day"]):
            tot = g["net_member"].sum()
            cnt = g.height
            for row in g.iter_rows(named=True):
                others = (tot - row["net_member"]) / (cnt - 1) if cnt > 1 else None
                peer_contrib.append(
                    {
                        "n": n,
                        "clock": clock,
                        "policy": pol,
                        "side": sd,
                        "day": d,
                        "rank": row["rank"],
                        "ticker": row["ticker"],
                        "net_member": row["net_member"],
                        "peer_mean_realized": _safe_float(others),
                        "peer_class": (
                            "na" if others is None else ("peer_up" if others > 0 else "peer_down")
                        ),
                        "ex_post": True,
                    }
                )

        # ---- residual snapshots at 09:45 / 10:30 / 11:30 (executed sales + panel mark)
        for s, lab in SNAPSHOTS.items():
            rs = known.group_by(["clock", "side", "policy"]).agg(
                pl.len().alias("claims"),
                (pl.col(f"resfrac_{s}") > 1e-9).sum().alias("still_owned"),
                pl.col(f"resfrac_{s}").mean().alias("mean_resfrac"),
                pl.col(f"resmark_{s}")
                .filter(pl.col(f"resmark_{s}").is_finite())
                .mean()
                .alias("mean_resmark"),
                pl.col(f"resfrac_{s}").mean().alias("mean_rescost"),
                pl.col(f"resmark_{s}").sum().alias("sum_resmark"),
                pl.col(f"resfrac_{s}").sum().alias("sum_resfrac"),
                (pl.col(f"resmark_{s}") - pl.col(f"resfrac_{s}"))
                .filter(pl.col(f"resmark_{s}").is_finite())
                .mean()
                .alias("mean_unrealized"),
            )
            for r in rs.to_dicts():
                r.update({"n": n, "snapshot": s, "snapshot_label": lab})
                residual.append(r)

        # ---- retrospective upper bound (explicitly unattainable)
        ub = known.group_by(["clock", "side", "policy"]).agg(
            pl.len().alias("claims"),
            pl.col("upper_bound_ret")
            .filter(pl.col("upper_bound_ret").is_finite())
            .mean()
            .alias("mean_upper_bound_ret"),
            pl.col("upper_bound_ret").is_finite().sum().alias("upper_bound_known"),
            pl.col("net_member").mean().alias("mean_net_member"),
        )
        for r in ub.to_dicts():
            r["n"] = n
            ubv = r["mean_upper_bound_ret"]
            r["capture_ratio"] = None if ubv in (None, 0) else r["mean_net_member"] / ubv
            r["label"] = "retrospective_unattainable_upper_bound"
            upper.append(r)

        # ---- matched claim-level policy effects (fade vs hold, bank vs fade)
        piv = (
            known.filter(pl.col("policy").is_in(list(POLICIES)))
            .select(KEYS + ["side", "policy", "net_member", "mfe_class", "entry_fee", "exit_fee"])
            .pivot(
                index=KEYS + ["side"], on="policy", values="net_member", aggregate_function="first"
            )
        )
        piv = piv.drop_nulls(["hold", "fade", "bank5_f75_fade"])
        piv = piv.with_columns(
            (pl.col("fade") - pl.col("hold")).alias("delta_fade_hold"),
            (pl.col("bank5_f75_fade") - pl.col("fade")).alias("delta_bank_fade"),
            (pl.col("bank5_f75_fade") - pl.col("hold")).alias("delta_bank_hold"),
        )
        ms = piv.group_by(["clock", "side"]).agg(
            pl.len().alias("matched_claims"),
            pl.col("delta_fade_hold").mean().alias("mean_fade_hold"),
            pl.col("delta_fade_hold").median().alias("median_fade_hold"),
            pl.col("delta_fade_hold").sum().alias("sum_fade_hold"),
            pl.col("delta_bank_fade").mean().alias("mean_bank_fade"),
            pl.col("delta_bank_fade").sum().alias("sum_bank_fade"),
            pl.col("delta_bank_hold").mean().alias("mean_bank_hold"),
            pl.col("delta_bank_hold").sum().alias("sum_bank_hold"),
        )
        for r in ms.to_dicts():
            r["n"] = n
            r["interpretation"] = (
                "fade_hold>0 means fade beat hold; bank_fade>0 means bank beat fade"
            )
            matched.append(r)
        # terminal-return loss: matched gross given up vs hold on non-low-excursion claims
        tm = (
            piv.join(
                known.select(KEYS + ["side", "policy", "mfe_class"])
                .filter(pl.col("policy") == "hold")
                .drop("policy"),
                on=KEYS + ["side"],
                how="left",
            )
            .filter(pl.col("mfe_class") != "MFElt10")
            .group_by(["clock", "side", "mfe_class"])
            .agg(
                pl.len().alias("matched_claims"),
                pl.col("delta_fade_hold").sum().alias("fade_gives_up"),
                pl.col("delta_bank_hold").sum().alias("bank_gives_up"),
            )
        )
        for r in tm.to_dicts():
            r["n"] = n
            r["kind"] = "terminal_return_loss_vs_hold"
            terminal.append(r)

    # ---- concentration / tail removal with counts (known cashflows only)
    for n in VIEWS:
        cn = common.filter(pl.col("n") == n).select(["day", "clock", "side"])
        sub = claims.filter((pl.col("rank") <= n) & (pl.col("unknown") == False)).join(  # noqa: E712
            cn, on=["day", "clock", "side"], how="semi"
        )
        for (clock, pol, sd), g in sub.group_by(["clock", "policy", "side"]):
            tot = g["net_member"].sum()
            key = f"n{n}_c{clock}_{pol}_{sd}"
            concentration[key] = {
                "n": n,
                "clock": clock,
                "policy": pol,
                "side": sd,
                "total_net_member": _safe_float(tot),
                "day": _tail_concentration(g, "day", tot, 5),
                "day10": _tail_concentration(g, "day", tot, 10),
                "name": _tail_concentration(g, "ticker", tot, 5),
                "name10": _tail_concentration(g, "ticker", tot, 10),
            }

    # ---- released-before signal missed value (ex-post only), split by N view
    raw_ev = (
        pl.read_parquet(hdir(data_root) / "events.parquet")
        .filter(
            (pl.col("end") == END)
            & pl.col("clock").is_in(list(CLOCKS))
            & pl.col("threshold").is_in(list(THRESHOLDS))
            & pl.col("day").is_in(days)
        )
        .select(["day", "clock", "rank", "ticker", "threshold", "t"])
    )
    rel_rows: list[dict] = []
    for n in VIEWS:
        evn = ev.filter(pl.col("rank") <= n)
        rawn = (
            raw_ev.filter(pl.col("rank") <= n)
            .group_by(["clock", "threshold"])
            .agg(pl.len().alias("raw_signals"))
        )
        g = (
            evn.filter(pl.col("released_before"))
            .group_by(["clock", "threshold", "released_before_5", "released_before_30"])
            .agg(
                pl.len().alias("signals"),
                pl.col("day").n_unique().alias("signal_days"),
                pl.col("missed_release_5")
                .filter(pl.col("missed_release_5").is_finite())
                .mean()
                .alias("missed_release_5_mean"),
                pl.col("missed_release_30")
                .filter(pl.col("missed_release_30").is_finite())
                .mean()
                .alias("missed_release_30_mean"),
                pl.col("missed_signal_5")
                .filter(pl.col("missed_signal_5").is_finite())
                .mean()
                .alias("missed_signal_5_mean"),
                pl.col("missed_signal_30")
                .filter(pl.col("missed_signal_30").is_finite())
                .mean()
                .alias("missed_signal_30_mean"),
                pl.col("missed_signal_5").is_finite().sum().alias("missed_signal_5_known"),
                pl.col("missed_signal_30").is_finite().sum().alias("missed_signal_30_known"),
            )
        )
        g = g.join(rawn, on=["clock", "threshold"], how="left").with_columns(
            pl.lit(n).alias("n"),
            (pl.col("signals") / pl.col("raw_signals")).alias("released_before_share"),
            pl.lit("ex_post_opportunity_not_policy_signal").alias("label"),
        )
        rel_rows.extend(g.to_dicts())
    rel = pl.DataFrame(rel_rows)

    max_recon = max(
        (abs(r["recon_abs_diff"]) for r in recon if r["recon_abs_diff"] is not None), default=0.0
    )
    pd_ = (
        pl.DataFrame(peer_contrib)
        if peer_contrib
        else pl.DataFrame(schema={"peer_class": pl.String, "net_member": pl.Float64, "n": pl.Int64})
    )
    peer_summary = (
        (
            pd_.group_by(["n", "clock", "policy", "side", "peer_class"]).agg(
                pl.len().alias("claims"),
                pl.col("net_member").sum().alias("net_member"),
                pl.col("net_member").mean().alias("mean_net_member"),
            )
        )
        .sort(["n", "clock", "policy", "side", "peer_class"])
        .to_dicts()
        if pd_.height
        else []
    )

    manifest_path = hdir(data_root) / "manifest.json"
    hman = json.loads(manifest_path.read_text())
    artifact = {
        "kind": KIND,
        "producer": "factory/scripts/owned_claim_attribution.py",
        "producer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "run_id": run_id,
        "ruler": {
            "end": END,
            "wall": "13:00",
            "clocks": list(CLOCKS),
            "policies": list(POLICIES),
            "views": list(VIEWS),
            "sides_bps_per_side": [50.0, 75.0],
        },
        "denominators": {
            "original_entry_notional": "one claim N = fill_px*shares = 1",
            "entry_cash": (
                "N*(1+side); net_member = net_orig/(1+side) reproduces stored members.ret"
            ),
            "identity": (
                "net_orig = gross_move - entry_fee - exit_fee; "
                "sum(net_member) = n*sum(daily.ret) = sum(daily.pnl_sum)"
            ),
        },
        "discovery_days": len(days),
        "harvestability_manifest": hman,
        "input_manifest": ls.load_manifest(data_root),
        "decomposition": by_key,
        "mfe_decomposition": by_mfe,
        "low_excursion_tax": low_excursion,
        "terminal_return_loss": terminal,
        "matched_policy_effects": matched,
        "residual_snapshots": residual,
        "rank_contributions": rank_contrib,
        "peer_contributions": peer_summary,
        "concentration": concentration,
        "release_signal_missed_value": rel.to_dicts(),
        "opportunity_upper_bound": upper,
        "mfe_coverage": {
            "rule": (
                "Observed-window MFE with a fresh right edge: max past px_et must reach "
                "min(780,session_end)-1. This does not certify interior bar/trade/quote "
                "completeness. Stale-endpoint claims are incomplete_window, not duds."
            ),
            "endpoint_gap_max": ENDPOINT_GAP_MAX,
            "buckets": (
                claims.select(KEYS + ["policy", "mfe_class"])
                .unique()
                .group_by("mfe_class")
                .agg(pl.len().alias("claim_policies"))
                .sort("mfe_class")
                .to_dicts()
            ),
            "obs_gap_histogram": (
                claims.select(KEYS + ["policy", "obs_gap"])
                .unique()
                .group_by("obs_gap")
                .agg(pl.len().alias("claim_policies"))
                .sort("obs_gap")
                .to_dicts()
            ),
        },
        "timing": {
            "window_sales_after_ruler": {
                "claim_policies": claims.filter(
                    (~pl.col("unknown"))
                    & (pl.col("n_window_fills") > 0)
                    & (pl.col("last_exec") > END)
                )
                .select(KEYS + ["policy"])
                .unique()
                .height,
                "max_last_exec": _safe_float(
                    claims.filter(pl.col("n_window_fills") > 0)["last_exec"].max()
                ),
                "note": (
                    "window liquidation executes at the next observed open, "
                    "which may land after 13:00"
                ),
            },
        },
        "reconciliation": {
            "rows": recon,
            "max_abs_diff_net_member_vs_daily_pnl_sum": _safe_float(max_recon),
            "sum_net_member_by_view": (
                pl.DataFrame(by_key)
                .group_by("n")
                .agg(pl.col("net_member").sum().alias("net_member"))
                .to_dicts()
            ),
            "sum_reported_pnl_by_view": (
                pl.DataFrame(recon)
                .group_by("n")
                .agg(pl.col("reported_pnl_sum").sum().alias("reported"))
                .to_dicts()
            ),
        },
        "unknown": {
            "note": (
                "UNKNOWN cashflows excluded from dollar sums and counted, never zeroed; "
                "blocked-before-fill slots are known cash (zero dollars), not UNKNOWN"
            ),
            "unknown_claims_total": int(sum(r.get("unknown_claims") or 0 for r in by_key)),
            "unknown_by_view": (
                pl.DataFrame(by_key)
                .group_by("n")
                .agg(pl.col("unknown_claims").sum().alias("unknown_claims"))
                .to_dicts()
            ),
            "unknown_claims_by_key": [r for r in by_key if r.get("unknown_claims")],
            "overall_excluded_from_dollar_sums": unknown_overall,
            "overall_total_claims": int(sum(r["claims"] for r in unknown_overall)),
            "overall_days": int(claims.filter(pl.col("unknown") == True)["day"].n_unique()),  # noqa: E712
        },
        "caveats": [
            "13:00/end=780 is a ruler, not a discovered optimal window.",
            "Window MFE uses observed panel peak_gain t<=780 only; roster mfe_day "
            "(post-session) is never read.",
            "MFE classes describe observed bars with a fresh right edge at "
            "min(780,session_end)-1; this does not certify interior tape "
            "completeness. Stale endpoints are incomplete_window, never low-MFE duds.",
            "Residual marks use the last completed-bar px<=snapshot; 50/75bps per "
            "side is modeled, not quote-certified.",
            "Peer buckets use realized sibling cashflows (ex-post co-movement), "
            "not a deployable signal.",
            "Released-before+5/+30 missed value is ex-post opportunity "
            "(max/observed marks), explicitly unattainable.",
            "Opportunity upper bound = max observed sell_px within the ruler; "
            "retrospective and explicitly unattainable, never an executable payoff.",
            "Attributable dollars are original-entry-notional dollars; net_member "
            "divides by (1+side) to match daily net.",
            "UNKNOWN excluded and counted; no protected/validation outcomes read; "
            "discovery search disclosed.",
        ],
    }
    return artifact


# --------------------------------------------------------------------- main
def main() -> int:
    root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument(
        "--out", type=Path, default=root / "factory/artifacts/owned_claim_attribution.json"
    )
    ap.add_argument(
        "--stage", type=Path, default=root / "factory/artifacts/owned_claim_attribution_days"
    )
    ap.add_argument("--limit", type=int, help="smoke: process only the first N discovery days")
    a = ap.parse_args()

    days = ls.discovery_days(a.data_root)
    hman = json.loads((hdir(a.data_root) / "manifest.json").read_text())
    if hman.get("protected_half_read"):
        raise SystemExit("harvestability manifest flags protected-half reads; refusing")
    if list(hman.get("discovery_days", [])) != days:
        raise SystemExit("harvestability manifest is not exactly the guarded discovery half")
    if a.limit:
        days = days[: a.limit]
    ls.assert_coverage(days, a.data_root, "panel")
    ls.assert_coverage(days, a.data_root, "roster")

    run_id = hashlib.sha256(
        (Path(__file__).read_text() + json.dumps(days) + json.dumps(sorted(POLICIES))).encode()
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
        claims, ev = process_day(day, a.data_root, a.stage)
        write_day(a.stage, day, claims, ev, run_id, hdir(a.data_root))
        if index % 25 == 0:
            print(f"{index + 1}/{len(days)} {day}", flush=True)

    artifact = build_report(a.stage, days, a.data_root, run_id)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = a.out.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(artifact, indent=2, allow_nan=False) + "\n")
    tmp.replace(a.out)
    print(
        "Attribution written",
        a.out,
        "days",
        len(days),
        "max_recon_diff",
        artifact["reconciliation"]["max_abs_diff_net_member_vs_daily_pnl_sum"],
    )
    print(
        pl.DataFrame(artifact["decomposition"])
        .filter(pl.col("policy") == "bank5_f75_fade")
        .select(
            ["n", "clock", "side", "claims", "net_member", "reported_pnl_sum", "recon_abs_diff"]
        )
        .sort(["n", "clock", "side"])
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
