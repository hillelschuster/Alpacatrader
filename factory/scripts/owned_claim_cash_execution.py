#!/usr/bin/env python3
"""CASH-FIRST OWNED-CLAIM EXECUTION READER -- observed legs only, no new decisions.

The cash-first replay (``owned_claim_cash_first_replay``) published its economics.  This
reader publishes its EXECUTION and nothing else: when legs actually fired, what they cost,
how long capital was held, how much of the ledger is UNKNOWN, what participation the
filled size implies against the traded volume of the bar it filled on, and how far the
actual fill sat from the causal mark the order was sized on.

It is a READER.  No threshold, gate, filter, sizing rule, policy or bot is introduced,
tuned or suggested anywhere in this file.  Every number describes a leg that already
executed.

Boundaries, each also published in the artifact rather than merely claimed here
------------------------------------------------------------------------
* ALTERNATIVE BOOKS, NEVER A PORTFOLIO.  A cell is one (clock, N, side_cost, policy,
  cycles) alternative book over the same days.  ``net_sum_account`` is a within-cell
  figure: it is never added across cells, no cell is ranked, and a policy is comparable
  only at an identical day set, clock, N and side_cost with the ``cycles`` control named.
* UNKNOWN STAYS UNKNOWN.  An UNKNOWN book or claim contributes no mean, no median, no fee
  average and no EV.  Money is derived on the KNOWN book set only and says so on every
  block.  UNKNOWN is counted, named and never imputed as zero.
* PROXY, NOT QUOTE.  Participation is order size over the traded volume of the bar whose
  open is the fill.  The fill is a modelled canonical-bar open.  Nothing here certifies a
  broker, a venue, a route, a queue position, a fill probability or extended-hours
  behaviour, and no quote acquisition or network read is required or performed.
* MINUTE-LAG SLOT, NOT BROKER SETTLEMENT -- AND IT IS CYCLE-SCOPED.  The engine returns a
  sale's net receipt to the claim's own slot one MINUTE after the fill (``exec_et + 1``),
  not on a business-day T+1 cash-account cycle.  That mechanic only ever binds under the
  REPEAT control, where a claim re-enters on its own settled sale proceeds.  Under ONCE a
  claim buys at most once ever, so no sale proceeds are redeployed and the delay cannot
  bind at all; it is NOT a blocker for an ONCE book, and it is not broker-certified for a
  REPEAT one.  Each cell's ``cycle_ledger`` reports the ACTUAL fresh entries and re-entries
  that put it in one case or the other.
* NO RETROACTIVE ENTRY RULES.  Outlier dependence measures how concentrated an already
  published mean is.  No cut, cap or exclusion is derived from it.

Units
-----
Every money-like field is a FRACTION OF ONE ACCOUNT DOLLAR: the engine's ledger starts at
``cash = 1.0``, so ``ret``, ``gross_pnl``, ``fees`` and every fills fraction are account
fractions, with ``gross_pnl - fees = ret`` per book.  ``participation`` is the only figure
that needs a dollar size; it is the engine's PUBLISHED ``notional`` default, which this
reader REPRODUCES from the source panel rather than trusting.  No dollar P&L is published:
choosing an account size would be an unstated assumption, so account fractions are the
terminal unit.

Optional source-panel read
--------------------------
The causal-mark / actual-next-open gap needs the panel row behind each leg.  That read is
OPTIONAL: it is attempted only from files the replay artifact already pins, it is staged
per day so an interrupted run continues, and if anything about it is unavailable or would
cost a network or quote fetch the gap is published as ``status: not_read`` with the
reason -- never as zeros, never guessed.  ``daily``/``members``/``fills`` alone are
sufficient for the complete bounded observed-dollar read.

Journal rule
------------
A missing or empty fills journal is admissible only where the corresponding books report
an actual zero order count.  A book with a nonzero order count and no legs, and a leg
whose book is absent from ``daily``, is a hard failure rather than a dropped row.

Usage
-----
    .venv/bin/python factory/scripts/owned_claim_cash_execution.py \
        --evidence factory/artifacts/owned_claim_cash_first_discovery.json \
        --out factory/artifacts/owned_claim_cash_execution.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path

for _v in ("POLARS_MAX_THREADS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "2")

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lifecycle_study as ls  # noqa: E402

SCRIPT = Path(__file__).resolve()
PRODUCER = SCRIPT.with_name("owned_claim_cash_first_replay.py")
ENGINE = SCRIPT.with_name("owned_claim_replay.py")
REPLAY_SUBDIR = ("owned_claim", "replay_cash_first")

# The engine's PUBLISHED ``notional`` default.  The producer calls simulate() without that
# argument, so this default is the size behind every participation figure.  Verified here.
PUBLISHED_NOTIONAL = 10000.0
# The engine's sale-proceeds re-use delay: a receipt matures at exec_et + 1 MINUTE.
SETTLEMENT_LAG_MINUTES = 1

PANEL_COLS = ("day", "clock", "rank", "ticker", "t", "px", "sell_et", "sell_px", "sell_volume")
BOOK_KEYS = ("day", "clock", "n", "side_cost", "policy", "cycles")
CELL_KEYS = ("clock", "n", "side_cost", "policy", "cycles")
MARK_TAG = "owned_claim_cash_execution.mark.v1"
PCT = (0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99)
TOL_IDENTITY = 1e-8  # the engine's own cash-conservation tolerance
TOL_RESUM = 1e-9  # reader-side sum reordering tolerance
REL_TOL = 1e-9  # participation reproduction
CLOSE = 1e-10


# ------------------------------------------------------------------------------- helpers
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def jnum(x):
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def hhmm(et):
    """ET is ABSOLUTE minutes since midnight, so 540 = 09:00.

    Rounding is explicit and the day boundary is enforced: anything outside
    ``0 <= m < 1440`` is a non-time value and returns null rather than a fabricated clock.
    """
    if et is None or not math.isfinite(float(et)):
        return None
    m = int(round(float(et)))
    return f"{m // 60:02d}:{m % 60:02d}" if 0 <= m < 1440 else None


def dist(s: pl.Series) -> dict:
    a = s.drop_nulls().cast(pl.Float64).to_numpy()
    if a.size == 0:
        out = {"n": 0, "mean": None, "min": None, "max": None}
        out.update({f"p{int(q * 100):02d}": None for q in PCT})
        return out
    out = {"n": int(a.size), "mean": float(a.mean()), "min": float(a.min()), "max": float(a.max())}
    out.update({f"p{int(q * 100):02d}": float(np.quantile(a, q, method="linear")) for q in PCT})
    return out


def dist_et(s: pl.Series) -> dict:
    out = dist(s)
    if out["n"]:
        out["min_hhmm"] = hhmm(out["min"])
        out["max_hhmm"] = hhmm(out["max"])
    else:
        out["min_hhmm"] = None
        out["max_hhmm"] = None
    return out


def share(mask) -> float | None:
    m = np.asarray(mask)
    return float(m.sum()) / m.size if m.size else None


def ratio(num, den):
    try:
        d = float(den)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(d) or abs(d) < 1e-15:
        return None
    v = float(num) / d
    return v if math.isfinite(v) else None


def cell_label(k: dict) -> str:
    return f"clock{int(k['clock'])}|n{int(k['n'])}|s{k['side_cost']}|{k['policy']}|{k['cycles']}"


def split_cells(frame: pl.DataFrame) -> dict:
    """group_by the cell keys -> {cell_label: subframe}. Boring, ordered, no framework."""
    out = {}
    if frame.height == 0:
        return out
    for key, sub in frame.group_by(list(CELL_KEYS), maintain_order=True):
        out[cell_label(dict(zip(CELL_KEYS, key, strict=True)))] = sub
    return out


def empty_like(cols: dict) -> pl.DataFrame:
    return pl.DataFrame({k: pl.Series([], dtype=v) for k, v in cols.items()})


# ------------------------------------------------------------------------- journal check
def check_journal(daily: pl.DataFrame, fills: pl.DataFrame, root: Path) -> dict:
    if fills.height == 0 and int(daily["orders"].sum() or 0) > 0:
        raise SystemExit("fills journal is empty while books report orders; refusing")
    if daily.group_by(list(BOOK_KEYS)).len().filter(pl.col("len") > 1).height:
        raise SystemExit("daily carries duplicated book keys; refusing")
    # A GLOBALLY EMPTY journal is legitimate when no book reports an order, so every
    # vocabulary check below is guarded on the journal actually carrying legs. cash0 places
    # no order, so its policy legitimately never appears, and both cycle controls do.
    if fills.height:
        for c in ("policy", "cycles"):
            if not set(fills[c].unique().to_list()) <= set(daily[c].unique().to_list()):
                raise SystemExit(
                    f"fills.{c} carries a value the books never ran; journal is foreign"
                )
        if fills["admission"].n_unique() != 1 or fills["admission"][0] != daily["admission"][0]:
            raise SystemExit("fills.admission disagrees with daily.admission; journal is foreign")

    orders = daily.group_by(list(BOOK_KEYS)).agg(
        pl.len().alias("books"), pl.col("orders").sum().alias("orders_sum")
    )
    legs = fills.group_by(list(BOOK_KEYS)).agg(pl.len().alias("fill_rows"))
    m = orders.join(legs, on=list(BOOK_KEYS), how="full", coalesce=True)
    orphan = m.filter(pl.col("books").is_null())
    if orphan.height:
        raise SystemExit(f"{orphan.height} fills book(s) have no daily row; journal not a subset")
    # A book that executed nothing has no group on the journal side at all, so its missing
    # leg row IS the correct record of zero orders, not an absence. Only a book that claims
    # a nonzero order count and carries no legs is a failure.
    missing = m.filter(pl.col("orders_sum") > 0, pl.col("fill_rows").is_null())
    if missing.height:
        raise SystemExit(
            f"{missing.height} book(s) report orders the fills journal does not carry; "
            f"first {missing.head(1).to_dicts()}"
        )
    bad = m.filter(pl.col("fill_rows").fill_null(0) != pl.col("orders_sum"))
    if bad.height:
        raise SystemExit(
            f"{bad.height} book(s) have a leg count different from their order count; "
            f"first {bad.head(1).to_dicts()}"
        )

    # A per-day journal that is ABSENT or EMPTY is admissible exactly where that day reports
    # an actual zero order count, and is a failure anywhere else. No day is dropped either
    # way: an absent file simply contributes zero legs.
    staged = empty_days = absent_days = 0
    for day in sorted(daily["day"].unique().to_list()):
        p = root / "days" / f"{day}.fills.parquet"
        claimed = int(daily.filter(pl.col("day") == day)["orders"].sum() or 0)
        if not p.exists():
            absent_days += 1
            if claimed > 0:
                raise SystemExit(
                    f"{p} is absent while the books report {claimed} orders for {day}; a "
                    "missing journal is admissible only at an actual zero orders"
                )
            empty_days += 1
            continue
        n = int(pl.scan_parquet(p).select(pl.len()).collect().item())
        if n == 0 and claimed > 0:
            raise SystemExit(f"{day}: empty staged fills journal but {claimed} orders reported")
        empty_days += 1 if n == 0 else 0
        staged += n
    if staged != fills.height:
        raise SystemExit(
            f"staged per-day fills sum to {staged} legs; the aggregate journal carries "
            f"{fills.height}; one of them is stale"
        )
    return {
        "books": int(orders.height),
        "books_with_legs": int(m.filter(pl.col("orders_sum") > 0).height),
        "books_zero_orders": int(m.filter(pl.col("orders_sum") == 0).height),
        "daily_orders_sum": int(daily["orders"].sum() or 0),
        "fills_rows": int(fills.height),
        "staged_leg_rows_sum": staged,
        "days_journal_empty_at_zero_orders": empty_days,
        "days_journal_absent_at_zero_orders": absent_days,
        "per_book_leg_residual_max": 0,
        "orphan_leg_books": 0,
        "rules": [
            "a missing or empty fills journal is admissible ONLY where the books report an "
            "actual zero order count",
            "a nonzero order count with no legs is a hard failure",
            "a leg whose book is absent from daily is a hard failure",
            "the staged per-day journals must sum to the aggregate journal, so a stale "
            "per-day file cannot survive under new keys",
        ],
    }


# ---------------------------------------------------------------------- optional panel read
def read_marks(fills: pl.DataFrame, v2: Path, stage: Path, declared: dict, days: list[str]):
    """Recover the causal mark behind each leg from the ONE panel row that made it.

    ``px`` is the close of the last COMPLETED bar with et <= t-1 and ``sell_px`` is the
    open of the first valid bar with et >= t.  The engine sets its mark from ``px`` at the
    decision minute before submitting the order, and a decision minute always carries that
    ticker's forecast, so for a FINITE mark on the order's own row the causal mark is
    exactly that ``px``.  A non-finite mark there is NOT guessed -- the engine would have
    carried an earlier mark this reader cannot see -- so the leg is marked inexact and left
    out of the gap.

    Entirely optional: any missing file, changed bytes, join fan-out or disagreement with
    the journal returns ``(None, reason)`` instead of failing, because daily/members/fills
    already carry the complete bounded observed-dollar read.
    """
    (stage / "days").mkdir(parents=True, exist_ok=True)
    (stage / "_done").mkdir(parents=True, exist_ok=True)
    pin_script = sha256_file(SCRIPT)
    frames, stats = (
        [],
        {
            "days_read": 0,
            "days_resumed": 0,
            "days_not_needed": 0,
            "legs": 0,
            "legs_mark_exact": 0,
            "legs_mark_not_exact": 0,
            "panel_rows_read": 0,
            "fill_px_vs_panel_sell_px_max_abs_diff": 0.0,
            "exec_et_vs_panel_sell_et_mismatch": 0,
            "participation_legs_checked": 0,
            "participation_reproduction_max_abs_diff": 0.0,
        },
    )
    try:
        for day in days:
            sub = fills.filter(pl.col("day") == day)
            marker, out = stage / "_done" / f"{day}.json", stage / "days" / f"{day}.mark.parquet"
            # The RESUME PIN must cover the legs actually being consumed, not just the panel
            # bytes: a different replay run against an unchanged panel would otherwise resume
            # marks that were computed for other legs. So the digest of THIS day's projected
            # legs goes into the pin and into the marker.
            legs_digest = hashlib.sha256(
                json.dumps(
                    sub.sort(
                        ["clock", "n", "side_cost", "policy", "cycles", "ticker", "exec_et", "side"]
                    ).to_dicts(),
                    sort_keys=True,
                    default=str,
                ).encode()
            ).hexdigest()
            if sub.height == 0:
                stats["days_not_needed"] += 1
                typed = _typed_mark_frame(sub.head(0))
                frames.append(typed)
                tmp = out.with_suffix(".tmp.parquet")
                typed.write_parquet(tmp)
                tmp.replace(out)
                marker.write_text(
                    json.dumps(
                        {
                            "resume_pin": "no-legs",
                            "day": day,
                            "legs": 0,
                            "legs_sha256": legs_digest,
                            "exact": 0,
                            "inexact": 0,
                            "panel_rows": 0,
                        }
                    )
                    + "\n"
                )
                continue
            path = v2 / "panel" / f"{day}.parquet"
            if not path.exists():
                return None, f"declared source panel is missing: {path}", stats
            panel_sha = sha256_file(path)
            pin_sha = (declared.get(day) or {}).get("panel")
            if not pin_sha:
                # Not permissive: without the replay's own pin there is no proof these are
                # the panel bytes the executed legs were sized against.
                return (
                    None,
                    (
                        f"the replay pinned no panel sha256 for {day}; an unpinned panel cannot be "
                        "shown to be the bytes these legs were executed against"
                    ),
                    stats,
                )
            if pin_sha != panel_sha:
                return (
                    None,
                    (
                        f"{path} hashes {panel_sha} but the replay pinned {pin_sha} for {day}: the "
                        "panel bytes changed after replay; this read would describe different legs"
                    ),
                    stats,
                )
            pin = hashlib.sha256(
                (
                    pin_script + panel_sha + legs_digest + day + MARK_TAG + str(PUBLISHED_NOTIONAL)
                ).encode()
            ).hexdigest()
            if marker.exists() and out.exists():
                info = json.loads(marker.read_text())
                if info.get("resume_pin") == pin and info.get("legs_sha256") == legs_digest:
                    frames.append(pl.read_parquet(out))
                    stats["days_resumed"] += 1
                    stats["legs"] += int(info["legs"])
                    stats["legs_mark_exact"] += int(info["exact"])
                    stats["legs_mark_not_exact"] += int(info["inexact"])
                    stats["panel_rows_read"] += int(info["panel_rows"])
                    continue
            panel = pl.read_parquet(path, columns=list(PANEL_COLS)).rename({"px": "mark_px"})
            stats["panel_rows_read"] += panel.height
            j = sub.join(
                panel,
                left_on=["day", "clock", "rank", "ticker", "decision_et"],
                right_on=["day", "clock", "rank", "ticker", "t"],
                how="left",
            )
            if j.height != sub.height:
                return None, f"{day}: the mark join fanned out ({sub.height} -> {j.height})", stats
            if int(j["mark_px"].is_null().sum() or 0):
                return (
                    None,
                    f"{day}: an executed leg has no panel row at its decision minute",
                    stats,
                )
            px_diff = float(
                (j["px"].cast(pl.Float64) - j["sell_px"].cast(pl.Float64)).abs().max() or 0.0
            )
            et_bad = int((j["exec_et"] != j["sell_et"]).sum() or 0)
            repro = j.select(
                pl.when(pl.col("sell_volume") > 0)
                .then(
                    pl.col("shares_per_capital")
                    * PUBLISHED_NOTIONAL
                    / pl.col("sell_volume").cast(pl.Float64)
                )
                .otherwise(None)
                .alias("participation_reproduced")
            ).to_series()
            p_diff = float((repro - j["participation"].cast(pl.Float64)).abs().max() or 0.0)
            if px_diff > 10 * TOL_RESUM or et_bad or p_diff > REL_TOL:
                return (
                    None,
                    (
                        f"{day}: the panel disagrees with the journal (fill px vs sell_px max "
                        f"diff {px_diff:g}, exec_et mismatches {et_bad}, participation "
                        f"reproduction max diff {p_diff:g})"
                    ),
                    stats,
                )
            # A mark the engine could actually have sized from must be FINITE and POSITIVE.
            # is_not_null() alone would admit NaN and +/-Inf and call them exact.
            mark = j["mark_px"].cast(pl.Float64)
            exact = mark.is_not_null() & mark.is_finite() & (mark > 0)
            keep = sub.with_columns(
                pl.when(exact)
                .then(j["px"].cast(pl.Float64) / mark - 1.0)
                .alias("mark_to_next_open_gap"),
                exact.cast(pl.Int64).alias("mark_exact"),
                repro.alias("participation_reproduced"),
            )
            frames.append(keep)
            stats["days_read"] += 1
            stats["legs"] += keep.height
            stats["legs_mark_exact"] += int(exact.sum() or 0)
            stats["legs_mark_not_exact"] += int((~exact).sum() or 0)
            stats["fill_px_vs_panel_sell_px_max_abs_diff"] = max(
                stats["fill_px_vs_panel_sell_px_max_abs_diff"], px_diff
            )
            stats["exec_et_vs_panel_sell_et_mismatch"] += et_bad
            stats["participation_legs_checked"] += int(j["participation"].is_not_null().sum() or 0)
            stats["participation_reproduction_max_abs_diff"] = max(
                stats["participation_reproduction_max_abs_diff"], p_diff
            )
            tmp = out.with_suffix(".tmp.parquet")
            keep.write_parquet(tmp)
            tmp.replace(out)
            marker.write_text(
                json.dumps(
                    {
                        "resume_pin": pin,
                        "day": day,
                        "legs": int(keep.height),
                        "legs_sha256": legs_digest,
                        "exact": int(exact.sum() or 0),
                        "inexact": int((~exact).sum() or 0),
                        "panel_rows": int(panel.height),
                        "panel_sha256": panel_sha,
                    }
                )
                + "\n"
            )
    except (OSError, pl.exceptions.PolarsError) as exc:
        return None, f"source panel read failed: {exc}", stats
    return pl.concat(frames, how="diagonal_relaxed"), None, stats


def _typed_mark_frame(f: pl.DataFrame) -> pl.DataFrame:
    return f.with_columns(
        pl.lit(None, pl.Float64).alias("mark_to_next_open_gap"),
        pl.lit(None, pl.Int64).alias("mark_exact"),
        pl.lit(None, pl.Float64).alias("participation_reproduced"),
    )


# --------------------------------------------------------------------------- round trips
def build_pairs(fills: pl.DataFrame) -> pl.DataFrame:
    """Entry/exit pairs at the FIXED quantity the engine actually used.

    In cash mode a sell closes the whole position and a buy is never resized after its
    decision mark, so a pair's buy and sell quantities are identical BY CONSTRUCTION --
    which this builder VERIFIES rather than assumes.  The first executed buy of a
    (book, ticker) is the ENTRY; every later one is a RE-ENTRY and is reported separately.

    Both legs carry the SAME positive ``cycle_index``: it is the running count of buys for
    that (book, ticker), and because a sell never precedes its own buy, the k-th sell also
    sits at count k.  The pair key is ``(keys, cycle_index)`` on BOTH sides.  Giving the buy
    ``+count`` and the sell ``-count`` would make the key unsatisfiable, so no sell could
    ever match its buy and every closed position would misreport as still open.
    """
    keys = ["day", "clock", "n", "side_cost", "policy", "cycles", "ticker"]
    if fills.height == 0:
        return empty_like(
            {
                **{k: fills.schema[k] for k in keys},
                "entry_et": pl.Float64,
                "exit_et": pl.Float64,
                "entry_gross": pl.Float64,
                "entry_fee": pl.Float64,
                "exit_gross": pl.Float64,
                "exit_fee": pl.Float64,
                "entry_shares": pl.Float64,
                "exit_shares": pl.Float64,
                "cycle_index": pl.Int64,
                "cycle_kind": pl.String,
                "hold_minutes": pl.Float64,
                "fee_burden_on_entry_gross": pl.Float64,
                "net_account": pl.Float64,
                "qty_mismatch": pl.Float64,
            }
        )
    f = fills.sort(keys + ["exec_et", "side"]).with_columns(
        pl.col("side").eq("buy").cast(pl.Int64).cum_sum().over(keys).alias("cycle_index")
    )
    buys = (
        f.filter(pl.col("side") == "buy")
        .drop("side")
        .rename(
            {
                "exec_et": "entry_et",
                "decision_et": "entry_decision_et",
                "px": "entry_px",
                "gross_fraction": "entry_gross",
                "fee_fraction": "entry_fee",
                "shares_per_capital": "entry_shares",
                "reason": "entry_reason",
            }
        )
    )
    sells = (
        f.filter(pl.col("side") == "sell")
        .drop("side")
        .rename(
            {
                "exec_et": "exit_et",
                "decision_et": "exit_decision_et",
                "px": "exit_px",
                "gross_fraction": "exit_gross",
                "fee_fraction": "exit_fee",
                "shares_per_capital": "exit_shares",
                "reason": "exit_reason",
            }
        )
    )
    return buys.join(sells, on=keys + ["cycle_index"], how="left").with_columns(
        pl.when(pl.col("exit_et").is_null())
        .then(pl.lit("open_at_close"))
        .when(pl.col("cycle_index") == 1)
        .then(pl.lit("entry"))
        .otherwise(pl.lit("reentry"))
        .alias("cycle_kind"),
        (pl.col("exit_et") - pl.col("entry_et")).alias("hold_minutes"),
        ((pl.col("entry_fee") + pl.col("exit_fee")) / pl.col("entry_gross")).alias(
            "fee_burden_on_entry_gross"
        ),
        (
            pl.col("exit_gross") - pl.col("exit_fee") - pl.col("entry_gross") - pl.col("entry_fee")
        ).alias("net_account"),
        (pl.col("exit_shares") - pl.col("entry_shares")).abs().alias("qty_mismatch"),
    )


# ---------------------------------------------------------------------------- cell blocks
def money(d: pl.DataFrame, srow: dict | None) -> dict:
    """Money on the KNOWN book set, and only the KNOWN book set."""
    k = d.filter(pl.col("ret").is_not_null())
    n = k.height
    net = float(k["ret"].sum() or 0.0)
    gross = float(k["gross_pnl"].sum() or 0.0)
    fees = float(k["fees"].sum() or 0.0)
    out = {
        "book_set": "KNOWN books only (ret not null); UNKNOWN books are excluded entirely",
        "known_books": n,
        "unknown_books_excluded": int(d.height - n),
        "net_sum_account": jnum(net),
        "net_mean_per_known_book_day": jnum(net / n) if n else None,
        "net_median_per_known_book_day": jnum(k["ret"].median()) if n else None,
        "gross_sum_account": jnum(gross),
        "fees_sum_account": jnum(fees),
        "identity_gross_minus_fees_minus_net": jnum(gross - fees - net),
        "identity_tolerance": TOL_IDENTITY,
        "identity_holds": bool(n == 0 or abs(gross - fees - net) <= TOL_IDENTITY),
    }
    if srow:
        out["producer_summary_crosscheck"] = {
            "ev": jnum(srow.get("ev")),
            "ev_minus_reader_known_mean": jnum(
                (jnum(srow.get("ev")) or 0.0) - (net / n if n else 0.0)
            ),
            "fees_mean_known_minus_reader": jnum(
                (jnum(srow.get("fees")) or 0.0) - (fees / n if n else 0.0)
            ),
            "orders_mean_ALL_books": jnum(srow.get("orders")),
            "book_set_note": (
                "the producer's summary.ev, summary.fees and summary.gross_pnl are means over "
                "the SAME known book set, while summary.orders and the mean_* columns are "
                "means over ALL books; quoting an all-book order mean beside a known-set fee "
                "would attribute money to a different day set"
            ),
        }
    return out


def recon(d: pl.DataFrame, f: pl.DataFrame, m: pl.DataFrame, p: pl.DataFrame) -> dict:
    """Every identity is compared on ONE exact book set, and never across sets.

    Fees and gross are comparable against every book. NET is compared only on KNOWN books;
    UNKNOWN books remain excluded, including their otherwise known members. A KNOWN book
    must carry every original claim exactly once, with finite net and no open pair.
    Contradictory known support fails verification rather than disappearing from the set.
    """
    fees_d = float(d["fees"].sum() or 0.0)
    gross_d = float(d["gross_pnl"].sum() or 0.0)
    fees_f = float(f["fee_fraction"].sum() or 0.0) if f.height else 0.0
    fees_m = float(m["fees"].sum() or 0.0)
    gross_m = float(m["gross_pnl"].sum() or 0.0)
    sgn = np.where(f["side"].to_numpy() == "buy", -1.0, 1.0) if f.height else np.zeros(0)
    gross_f = float((f["gross_fraction"].to_numpy() * sgn).sum()) if f.height else 0.0
    closed = p.filter(pl.col("exit_et").is_not_null())
    qty_bad = int(closed["qty_mismatch"].gt(CLOSE).sum() or 0) if closed.height else 0

    # ---- per KNOWN book: member net and closed-pair net against that book's own ret -----
    mk = m.group_by(list(BOOK_KEYS)).agg(
        pl.len().alias("member_rows"),
        pl.col("ticker").n_unique().alias("member_tickers"),
        pl.col("net_pnl").sum().alias("member_net"),
        (~pl.col("net_pnl").is_finite()).fill_null(True).sum().alias("member_invalid"),
    )
    pk = (
        p.group_by(list(BOOK_KEYS)).agg(
            pl.col("net_account").sum().alias("pair_net"),
            (pl.col("exit_et").is_null().sum()).alias("open_pairs"),
        )
        if p.height
        else pl.DataFrame(
            {k: pl.Series([], dtype=p.schema[k]) for k in BOOK_KEYS}
            | {
                "pair_net": pl.Series([], dtype=pl.Float64),
                "open_pairs": pl.Series([], dtype=pl.Int64),
            }
        )
    )
    bk = (
        d.filter(pl.col("ret").is_not_null())
        .select(list(BOOK_KEYS) + ["ret", "roster_claims"])
        .join(mk, on=list(BOOK_KEYS), how="left")
        .join(pk, on=list(BOOK_KEYS), how="left")
        .with_columns(
            pl.col("member_rows").fill_null(0),
            pl.col("member_tickers").fill_null(0),
            pl.col("member_invalid").fill_null(0),
            pl.col("open_pairs").fill_null(0),
        )
    )
    comparable = bk.filter(
        (pl.col("member_rows") == pl.col("roster_claims"))
        & (pl.col("member_tickers") == pl.col("roster_claims"))
        & (pl.col("member_invalid") == 0)
        & (pl.col("open_pairs") == 0)
    )
    member_resid = (
        (comparable["member_net"] - comparable["ret"]).abs().max() if comparable.height else 0.0
    )
    pair_resid = (
        (comparable["pair_net"].fill_null(0.0) - comparable["ret"]).abs().max()
        if comparable.height
        else 0.0
    )
    member_resid = float(member_resid or 0.0)
    pair_resid = float(pair_resid or 0.0)
    not_comparable = int(bk.height - comparable.height)

    net_k = float(d.filter(pl.col("ret").is_not_null())["ret"].sum() or 0.0)
    net_m = float(m["net_pnl"].sum() or 0.0)
    net_p = float(closed["net_account"].sum() or 0.0) if closed.height else 0.0
    worst = max(
        abs(fees_f - fees_d),
        abs(gross_f - gross_d),
        abs(gross_m - gross_d),
        abs(fees_m - fees_d),
        member_resid,
        pair_resid,
    )
    return {
        "daily_orders": int(d["orders"].sum() or 0),
        "fills_rows": int(f.height),
        "orders_residual": int(f.height) - int(d["orders"].sum() or 0),
        "fees_daily_account": jnum(fees_d),
        "fees_fills_account": jnum(fees_f),
        "fees_members_account": jnum(fees_m),
        "fees_residual_fills_minus_daily": jnum(fees_f - fees_d),
        "fees_residual_members_minus_daily": jnum(fees_m - fees_d),
        "gross_daily_account": jnum(gross_d),
        "gross_fills_signed_account": jnum(gross_f),
        "gross_members_account": jnum(gross_m),
        "gross_residual_fills_minus_daily": jnum(gross_f - gross_d),
        "gross_residual_members_minus_daily": jnum(gross_m - gross_d),
        "gross_fills_definition": "buys negative, sells positive, matching the engine's flow",
        "known_book_net_account": jnum(net_k),
        "members_net_account_all_rows": jnum(net_m),
        "member_net_vs_book_net_max_abs_residual_known_books": jnum(member_resid),
        "closed_pair_net_account": jnum(net_p),
        "pair_net_vs_book_net_max_abs_residual_known_books": jnum(pair_resid),
        "net_comparisons": {
            "book_set": (
                "KNOWN books only, and only those with every member known and no position "
                "left open, so member net and closed-pair net are each compared against that "
                "book's OWN ret"
            ),
            "known_books_compared": int(comparable.height),
            "known_books_not_comparable": not_comparable,
            "unknown_books_excluded": int(d["unknown"].sum() or 0),
            "known_support_rule": (
                "every KNOWN book must carry its complete unique roster with no UNKNOWN "
                "member and no open pair; known_books_not_comparable is a verification "
                "failure, not an exclusion that can hide contradictory support"
            ),
        },
        "member_rows": int(m.height),
        "books_all_members_known": int(bk.filter(pl.col("member_invalid") == 0).height),
        "books_unknown": int(d["unknown"].sum() or 0),
        "closed_pair_qty_mismatches": qty_bad,
        "closed_pair_qty_rule": (
            "a closed pair's buy and sell quantities must be equal; a mismatch means the "
            "engine resized or partially filled, which this reader does not model"
        ),
        "max_abs_residual": jnum(worst),
        "tolerance": TOL_RESUM,
        "all_checks_pass": bool(
            int(f.height) == int(d["orders"].sum() or 0)
            and worst <= TOL_RESUM
            and qty_bad == 0
            and not_comparable == 0
        ),
    }


def ownership(d: pl.DataFrame, m: pl.DataFrame) -> dict:
    roster = int(d["roster_claims"].sum() or 0)
    entered = int(m.filter(pl.col("fresh_entries") > 0).height)
    unk = int(m["net_pnl"].is_null().sum() or 0)
    openc = int(m["remaining_shares"].gt(CLOSE).sum() or 0)
    slot = float(d["slot_total"].sum() or 0.0)
    idle = float(d["slots_idle_end"].sum() or 0.0)
    return {
        "roster_claims": roster,
        "claims_that_ever_entered": entered,
        "claims_never_entered_known_cash_zero": roster - entered,
        "claim_entry_share": ratio(entered, roster),
        "members_known": int(m.height) - unk,
        "members_unknown": unk,
        "member_unknown_share": share(m["net_pnl"].is_null().to_numpy()),
        "unknown_open_at_close": openc,
        "unknown_attempted_no_fill": unk - openc,
        "unassigned_at_start_mean": jnum(d["unassigned_at_start"].mean()),
        "slot_total_mean": jnum(d["slot_total"].mean()),
        "slots_idle_end_mean": jnum(d["slots_idle_end"].mean()),
        "slots_idle_end_total": jnum(idle),
        "idle_share_of_assigned_slots": ratio(idle, slot),
        "books_zero_orders": int(d.filter(pl.col("orders") == 0).height),
        "books_zero_orders_and_zero_net": int(
            d.filter((pl.col("orders") == 0) & (pl.col("ret") == 0.0)).height
        ),
        "meanings": {
            "claim_entry_share": "claims that executed at least one fresh buy / roster claims",
            "member_unknown_share": "members with net_pnl null / all members; UNKNOWN, never 0",
            "idle_share_of_assigned_slots": (
                "sum of per-claim cash still idle at the close / sum of the 1/n slots assigned "
                "at t=0; the unassigned remainder is reported separately and is never borrowed "
                "across claims"
            ),
        },
    }


def uncertainty(d: pl.DataFrame, m: pl.DataFrame) -> dict:
    unk = int(m["net_pnl"].is_null().sum() or 0)
    openc = int(m["remaining_shares"].gt(CLOSE).sum() or 0)
    unres = d.explode("unresolved_fresh_tickers", empty_as_null=False).filter(
        pl.col("unresolved_fresh_tickers").is_not_null()
    )
    named = d.explode("unknown_tickers", empty_as_null=False).filter(
        pl.col("unknown_tickers").is_not_null()
    )
    aff = int(d["affordability_unknown"].sum() or 0)
    attempted = unk - openc

    def top(frame: pl.DataFrame, col: str, key: str, k: int = 12) -> dict:
        if frame.height == 0:
            return {}
        return {
            str(r[col]): int(r["len"])
            for r in frame.group_by(col).len().sort("len", descending=True).head(k).to_dicts()
        }

    return {
        "books": int(d.height),
        "books_unknown": int(d["unknown"].sum() or 0),
        "book_unknown_share": share(d["unknown"].to_numpy()),
        "books_unresolved_fresh": int(d["unresolved_fresh"].sum() or 0),
        "affordability_unknown_ticker_count": aff,
        "affordability_names_published": False,
        "named_unresolved_fresh_ticker_days": int(unres.height),
        "engine_named_unknown_ticker_days": int(named.height),
        "attempted_no_fill_claims": attempted,
        "open_at_close_claims": openc,
        "unattributed_attempted_lower_bound": max(0, attempted - max(aff, int(unres.height))),
        "named_unresolved_fresh_tickers_top": top(
            unres, "unresolved_fresh_tickers", "unresolved_fresh_tickers"
        ),
        "engine_named_unknown_tickers_top": top(named, "unknown_tickers", "unknown_tickers"),
        "engine_named_unknown_days_top": top(named, "day", "day"),
        "disjoint_member_classification": {
            "known": int(m.height) - unk,
            "unknown_open_at_close": openc,
            "unknown_attempted_no_fill": attempted,
            "note": (
                "this is a disjoint partition of the member frame: a member is UNKNOWN either "
                "because its position was still held at the terminal boundary or because an "
                "attempted order never produced a leg. It partitions members, not books"
            ),
        },
        "reason_meanings": {
            "open_at_close": (
                "held when the terminal boundary arrived, so no exit open existed inside the "
                "session; UNKNOWN, not marked out"
            ),
            "unresolved_fresh_buy": (
                "a fresh buy with a finite price and a finite exec_et that falls outside the "
                "session: a real funding attempt whose outcome never resolved. The tickers are "
                "named by the producer BEFORE the pending orders are discarded"
            ),
            "affordability_gap": (
                "the fixed quantity cost more than the claim's own settled slot or than cash "
                "on hand. The order is never resized and never levered; it stays UNKNOWN"
            ),
            "unattributed_attempted_lower_bound": (
                "attempted orders whose causal mark was unusable, or whose next open was "
                "missing or non-finite. The engine names those tickers only inside its own "
                "union, so this reader reports the REMAINDER as a COUNTED LOWER BOUND and "
                "never splits it by guesswork"
            ),
        },
        "unknown_is_never_imputed": (
            "UNKNOWN books and claims contribute no mean, no median, no fee average and no EV "
            "anywhere in this artifact"
        ),
    }


def et_block(f: pl.DataFrame, p: pl.DataFrame, clock: int) -> dict:
    b = f.filter(pl.col("side") == "buy")
    s = f.filter(pl.col("side") == "sell")
    ent, ext = b["exec_et"].cast(pl.Float64), s["exec_et"].cast(pl.Float64)
    lag = (
        (b["exec_et"] - b["decision_et"]).cast(pl.Float64)
        if b.height
        else pl.Series("_empty", [], dtype=pl.Float64)
    )
    slag = (
        (s["exec_et"] - s["decision_et"]).cast(pl.Float64)
        if s.height
        else pl.Series("_empty", [], dtype=pl.Float64)
    )

    def counts(frame: pl.DataFrame, col: str) -> dict:
        return (
            {str(r[col]): int(r["len"]) for r in frame.group_by(col).len().to_dicts()}
            if frame.height
            else {}
        )

    return {
        "et_units": (
            "ET is ABSOLUTE minutes since midnight on the canonical grid, so 540 = 09:00, "
            "600 = 10:00, 780 = 13:00 and 900 = 15:00. The *_before_* / *_after_* shares "
            "below are literal clock-time thresholds on that same scale"
        ),
        "entry_exec_et": dist_et(ent),
        "exit_exec_et": dist_et(ext),
        "entry_decision_to_exec_lag_minutes": dist(lag),
        "exit_decision_to_exec_lag_minutes": dist(slag),
        "entry_lead_from_admission_clock_minutes": dist(
            ent - float(clock) if ent.len() else pl.Series("_empty", [], dtype=pl.Float64)
        ),
        "hold_minutes_closed_pairs": dist(p["hold_minutes"]),
        "share_entry_before_1000": share((ent < 600).to_numpy()),
        "share_entry_before_1100": share((ent < 660).to_numpy()),
        "share_entry_after_1230": share((ent >= 750).to_numpy()),
        "share_exit_before_1300": share((ext < 780).to_numpy()),
        "share_exit_after_1500": share((ext >= 900).to_numpy()),
        "entry_reasons": counts(b, "reason"),
        "exit_reasons": counts(s, "reason"),
        "exit_reason_meanings": {
            "state_release": "the frozen forecast turned negative, so the HELD claim was sold",
            "terminal": "the session reached its boundary with the claim still held",
        },
    }


def exposure_block(d: pl.DataFrame) -> dict:
    e = {t: d[f"exposure_{t}"].cast(pl.Float64).to_numpy() for t in (585, 630, 690, 780)}
    entered = d["claims_with_entry"].to_numpy() > 0
    both = (e[585] > 0) & (e[780] > 0)
    return {
        "definition": (
            "engine snapshot = sum over claims of shares x the engine's last-finite causal "
            "mark, as an ACCOUNT FRACTION. That mark reads 0 for a claim that never had one, "
            "so a snapshot can UNDERSTATE a held position; the understating counts are "
            "published below rather than corrected"
        ),
        "exposure_585_mean": jnum(e[585].mean() if e[585].size else None),
        "exposure_630_mean": jnum(e[630].mean() if e[630].size else None),
        "exposure_690_mean": jnum(e[690].mean() if e[690].size else None),
        "exposure_780_mean": jnum(e[780].mean() if e[780].size else None),
        "books_with_exposure_585": int((e[585] > 0).sum()),
        "books_with_exposure_780": int((e[780] > 0).sum()),
        "books_held_at_both_585_and_780": int(both.sum()),
        "retained_ratio_780_over_585_mean": jnum(
            float((e[780][both] / e[585][both]).mean()) if both.any() else None
        ),
        "share_of_held_books_lower_at_780": share(e[780][both] < e[585][both]),
        "mean_change_585_to_780": jnum(float((e[780] - e[585]).mean()) if e[585].size else None),
        "books_that_entered_but_flat_at_585": int((entered & (e[585] <= 0)).sum()),
        "early_vs_afternoon_reading": (
            "585 is the early-window mark and 690/780 the afternoon marks. A book that traded "
            "but is flat at 585 was flat BEFORE the afternoon, so the absence of afternoon "
            "exposure is not evidence that a morning trade failed"
        ),
    }


def turnover_block(d: pl.DataFrame, f: pl.DataFrame, p: pl.DataFrame) -> dict:
    if f.height:
        sgn = np.where(f["side"].to_numpy() == "buy", -1.0, 1.0)
        g = f["gross_fraction"].to_numpy()
        buy, sell = float(g[sgn < 0].sum()), float(g[sgn > 0].sum())
        fees = float(f["fee_fraction"].sum())
    else:
        buy = sell = fees = 0.0
    traded = int(d.filter(pl.col("orders") > 0).height)
    closed = int((p["exit_et"].is_not_null()).sum() or 0) if p.height else 0
    return {
        "definitions": {
            "buy_gross_deployed": "sum of fills.gross_fraction over buys, ACCOUNT FRACTIONS",
            "gross_traded_two_legs": "that plus the same sum over sells, ACCOUNT FRACTIONS",
            "gross_traded_per_deployed_dollar": (
                "gross_traded_two_legs / buy_gross_deployed: the two-legged turnover multiple "
                "per deployed account dollar. It is a ratio of sums inside THIS cell and never "
                "a portfolio figure"
            ),
        },
        "books_total": int(d.height),
        "books_that_traded": traded,
        "share_books_that_traded": share((d["orders"] > 0).to_numpy()),
        "legs_total": int(f.height),
        "buy_gross_deployed_account": jnum(buy),
        "sell_gross_recovered_account": jnum(sell),
        "gross_traded_two_legs_account": jnum(buy + sell),
        "fees_account": jnum(fees),
        "fees_per_deployed_dollar": ratio(fees, buy),
        "gross_traded_per_book_day": ratio(buy + sell, traded),
        "gross_traded_per_deployed_dollar": ratio(buy + sell, buy),
        "legs_per_traded_book_day": ratio(int(f.height), traded),
        "closed_round_trips": closed,
        "positions_open_at_close": int(p["exit_et"].is_null().sum() or 0) if p.height else 0,
        "round_trips_per_traded_book_day": ratio(closed, traded),
    }


def participation_block(f: pl.DataFrame) -> dict:
    p = f["participation"].cast(pl.Float64)
    a = p.drop_nulls().to_numpy()
    out = {
        "definition": (
            "participation = fills.shares_per_capital * 10000.0 / panel.sell_volume, where "
            "shares_per_capital is SHARES PER ONE ACCOUNT DOLLAR (the ledger starts at "
            "cash = 1.0), 10000.0 USD is the engine's PUBLISHED notional argument default "
            "which the producer never overrides, and sell_volume is the traded share volume of "
            "the canonical bar row whose OPEN is the fill price"
        ),
        "unit": "fraction of that minute's traded volume (0.01 = 1% of the bar's volume)",
        "certification": (
            "SIZE-OVER-VOLUME PROXY ONLY. Not a quote-level or queue-position participation "
            "rate; no venue, no route, no probability of fill, and nothing about a broker"
        ),
        "legs_with_volume": int(p.is_not_null().sum() or 0),
        "legs_without_volume": int(p.is_null().sum() or 0),
        "all_legs": dist(p),
        "buy_legs": dist(f.filter(pl.col("side") == "buy")["participation"].cast(pl.Float64)),
        "sell_legs": dist(f.filter(pl.col("side") == "sell")["participation"].cast(pl.Float64)),
    }
    for name, t in (
        ("share_above_1pct", 0.01),
        ("share_above_5pct", 0.05),
        ("share_above_25pct", 0.25),
        ("share_above_100pct", 1.0),
    ):
        out[name] = share(a > t) if a.size else None
    return out


def cycle_ledger_block(d: pl.DataFrame, m: pl.DataFrame, cycles: str) -> dict:
    """ACTUAL fresh entries and re-entries, so the cycle control is read not assumed.

    This block exists because the settlement caveat is CYCLE-SCOPED.  Under ``once`` a
    claim buys at most once ever, so no sale proceeds are ever redeployed and the
    ``exec_et + 1`` receipt-availability mechanic cannot bind at all: every buy is funded
    from the claim's own INITIAL capital.  The intraday cash-reuse assumption is therefore
    not a blocker for a ``once`` book, and reporting it as one would be wrong.  Under
    ``repeat`` a claim re-enters on its own settled sale proceeds, and that is exactly the
    case the minute-lag mechanic does gate -- and does not certify against a broker.
    """
    fresh = int(d["fresh_entries"].sum() or 0)
    claims = int(d["claims_with_entry"].sum() or 0)
    roster = int(d["roster_claims"].sum() or 0)
    reentries = int(m["reentries"].sum() or 0)
    fresh_m = int(m["fresh_entries"].sum() or 0)
    once_expected = cycles != "repeat"
    return {
        "cycles_control": cycles,
        "actual_fresh_entries_including_first": fresh,
        "actual_claims_with_an_entry": claims,
        "actual_reentries_after_first": reentries,
        "actual_fresh_entries_from_member_frame": fresh_m,
        "roster_claims": roster,
        "max_fresh_entries_per_claim": int(m["fresh_entries"].max() or 0) if m.height else 0,
        "max_reentries_per_claim": int(m["reentries"].max() or 0) if m.height else 0,
        "share_claims_with_a_reentry": share(m["reentries"].gt(0).to_numpy()),
        "fresh_entries_le_roster_claims": bool(claims <= roster),
        "daily_vs_member_fresh_entry_residual": fresh - fresh_m,
        "once_control_respected": bool(reentries > 0)
        if not once_expected
        else bool(reentries == 0 and (m["fresh_entries"].max() or 0) <= 1),
        "sale_proceeds_reuse_is_reachable_here": {
            "reachable": bool(reentries > 0),
            "why": (
                "every executed fresh buy beyond a claim's FIRST is funded from that claim's "
                "own previously SOLD, SETTLED net receipt; the receipt only becomes spendable "
                f"at exec_et + {SETTLEMENT_LAG_MINUTES} minute"
                if reentries > 0
                else "this cell executed NO re-entry, so no sale proceeds were ever redeployed: "
                "every buy came from the claim's own initial segregated 1/n slot and the "
                "intraday cash-re-use delay could not bind. The minute-lag caveat is therefore "
                "not a blocker for this book"
            ),
            "segregation": (
                "a claim's slot is funded only from ITS OWN cash; no slot is ever funded from "
                "another claim's proceeds and nothing is resized onto one"
            ),
            "segregation_is_auditable_not_certified": (
                "the no-borrow rule is verified from the published cash_slot_end / "
                "settled_receipt columns, but it is an in-model ledger rule; no broker "
                "account, order-routing remainder or partial-fill quantity is certified, and "
                "pre-market order qty remaining is likewise not certified"
            ),
        },
    }


def pairs_block(p: pl.DataFrame) -> dict:
    c = p.filter(pl.col("exit_et").is_not_null())
    out = {
        "definition": (
            "an entry/exit pair at the FIXED quantity the engine used: a sell closes the whole "
            "position and a buy is never resized after its decision mark, so pair quantities "
            "are identical by construction and VERIFIED here per pair. net_account = sell "
            "gross - sell fee - buy gross - buy fee, in ACCOUNT FRACTIONS"
        ),
        "pairs": int(c.height),
        "unpaired_buys_open_at_close": int(p["exit_et"].is_null().sum() or 0) if p.height else 0,
        "qty_mismatches": int(c["qty_mismatch"].gt(CLOSE).sum() or 0) if c.height else 0,
        "hold_minutes": dist(c["hold_minutes"]),
        "fee_burden_on_entry_gross": dist(c["fee_burden_on_entry_gross"]),
        "net_account_per_pair": dist(c["net_account"]),
        "share_pairs_positive": share(c["net_account"].gt(0).to_numpy()),
        "by_cycle_kind": {},
    }
    for kind, meaning in (
        ("entry", "the FIRST executed fresh buy of a (book, ticker): a fresh entry cost"),
        (
            "reentry",
            "every later executed fresh buy of the same claim, allowed only under the "
            "repeat cycle control; reported separately, never pooled with entry",
        ),
    ):
        s = c.filter(pl.col("cycle_kind") == kind)
        out["by_cycle_kind"][kind] = {
            "meaning": meaning,
            "pairs": int(s.height),
            "hold_minutes": dist(s["hold_minutes"]),
            "fee_burden_on_entry_gross": dist(s["fee_burden_on_entry_gross"]),
            "net_account_per_pair": dist(s["net_account"]),
            "share_pairs_positive": share(s["net_account"].gt(0).to_numpy()),
        }
    return out


def concentration(a: np.ndarray, unit: str) -> dict:
    a = np.asarray(a, dtype=float)
    n = int(a.size)
    total = float(a.sum()) if n else 0.0
    out = {
        "unit": unit,
        "n": n,
        "sum": jnum(total),
        "mean": jnum(total / n) if n else None,
        "median": jnum(float(np.median(a))) if n else None,
        "share_positive_units": share(a > 0) if n else None,
    }
    if n == 0 or total <= 0:
        out["top_k_share_of_total"] = None
        out["share_note"] = (
            "total is not positive, so share-of-total concentration is undefined; it is "
            "reported as null rather than as a signed or absolute substitute"
        )
    else:
        srt = np.sort(a)[::-1]
        out["top_k_share_of_total"] = {
            f"top{k}": jnum(float(srt[: min(k, n)].sum()) / total) for k in (1, 5, 10, 25)
        }
        out["share_note"] = (
            "share of this cell's total net carried by its k largest book-days or closed pairs, "
            "on the KNOWN set only: a concentration measure, not a rule"
        )
        cum = np.cumsum(srt)
        out["units_for_50pct_of_total"] = int(min(np.searchsorted(cum, 0.5 * total) + 1, n))
        out["units_for_80pct_of_total"] = int(min(np.searchsorted(cum, 0.8 * total) + 1, n))
    for k in (1, 5, 10):
        if n > k:
            rest = np.delete(np.sort(a)[::-1], np.arange(k))
            mean_rest = float(rest.sum()) / rest.size
            out[f"mean_excluding_top{k}"] = jnum(mean_rest)
            out[f"sign_after_dropping_top{k}"] = (
                "positive" if mean_rest > 0 else ("zero" if mean_rest == 0 else "negative")
            )
            out[f"sign_flip_on_dropping_top{k}"] = bool(mean_rest <= 0)
        else:
            out[f"mean_excluding_top{k}"] = None
            out[f"sign_after_dropping_top{k}"] = None
            out[f"sign_flip_on_dropping_top{k}"] = None
    return out


def outlier_block(d: pl.DataFrame, p: pl.DataFrame) -> dict:
    return {
        "purpose": (
            "measure how much of an ALREADY PUBLISHED mean rests on a handful of book-days or "
            "a handful of closed pairs. This is a CONCENTRATION DIAGNOSTIC only: no cut, cap, "
            "exclusion, entry rule or exit rule is derived from it, none is proposed here, and "
            "nothing was optimized on it"
        ),
        "dates": concentration(
            d.filter(pl.col("ret").is_not_null())["ret"].cast(pl.Float64).to_numpy(),
            "known_book_days",
        ),
        "trades": concentration(
            p.filter(pl.col("exit_et").is_not_null())["net_account"].cast(pl.Float64).to_numpy()
            if p.height
            else np.zeros(0),
            "closed_round_trip_pairs",
        ),
        "date_trade_note": (
            "the two blocks are NOT expected to tie: a book-day nets every claim it touched, "
            "including positions still open at the close, while the trade block counts only "
            "CLOSED pairs. The difference is the open-position UNKNOWN mass, reported in the "
            "reconciliation block"
        ),
    }


def gap_block(g: pl.DataFrame) -> dict:
    exact = g.filter(pl.col("mark_exact") == 1)
    b = exact.filter(pl.col("side") == "buy")
    s = exact.filter(pl.col("side") == "sell")
    gb = b["mark_to_next_open_gap"].cast(pl.Float64)
    a = gb.drop_nulls().to_numpy()
    w = b["gross_fraction"].cast(pl.Float64).to_numpy() if b.height else np.zeros(0)
    out = {
        "status": "read",
        "measures": (
            "the ACTUAL next-open fill price against the CAUSAL mark the order was sized on, "
            "both from the source panel row of the order itself: mark = close of the last "
            "COMPLETED bar with et <= decision_et-1, fill = open of the first valid bar with "
            "et >= decision_et"
        ),
        "gap_definition": "mark_to_next_open_gap = fill_px / mark_px - 1 (simple return)",
        "sign": "positive = the actual open was HIGHER than the causal mark",
        "legs_total": int(g.height),
        "legs_mark_exact": int(exact.height),
        "legs_mark_not_exact": int(g["mark_exact"].ne(1).sum() or 0),
        "mark_not_exact_meaning": (
            "the mark on the order's own panel row was not finite, so the engine would have "
            "carried an EARLIER mark this reader cannot see. Those legs are excluded from the "
            "gap rather than filled with a guess"
        ),
        "buy_legs": int(b.height),
        "sell_legs": int(s.height),
        "buy_gap": dist(gb),
        "sell_gap": dist(s["mark_to_next_open_gap"].cast(pl.Float64)),
        "sell_gap_is_descriptive_only": (
            "a sell quantity is fixed by the shares held and is never sized off a mark, so the "
            "sell gap is exit context, not a sizing error"
        ),
        "share_buy_gap_adverse": share(a > 0) if a.size else None,
    }
    for bps in (25, 50, 100, 200):
        out[f"share_buy_gap_above_{bps}bps"] = share(a > bps / 10000.0) if a.size else None
    out["gross_weighted_buy_gap"] = jnum(
        float((w * a).sum() / w.sum()) if a.size and w.sum() > 0 else None
    )
    return out


# ---------------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="read the observed execution of the cash-first owned-claim replay: ET, "
        "exposure, turnover, ownership, UNKNOWN, participation, closed pairs and, when the "
        "declared source panel is available, the causal mark to actual next-open gap"
    )
    ap.add_argument("--data-root", default=None)
    ap.add_argument(
        "--replay-root",
        default=None,
        help="default: <data-root>/harvest01/lifecycle/v2/owned_claim/replay_cash_first",
    )
    ap.add_argument("--evidence", required=True, help="the replay's own evidence artifact")
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--stage",
        default=None,
        help=(
            "resumable per-day panel stage; a cache only. Defaults to "
            "<data-root>/harvest01/lifecycle/owned_claim/cash_execution_stage, deliberately "
            "OUTSIDE the repo so a per-day cache is never published into factory/artifacts"
        ),
    )
    ap.add_argument(
        "--limit-days", type=int, default=0, help="tiny NON-EVIDENCE read over the first N days"
    )
    ap.add_argument(
        "--skip-mark-gap",
        action="store_true",
        help="do not attempt the optional source-panel read at all",
    )
    ap.add_argument("--fresh", action="store_true", help="ignore the stage and re-read panels")
    a = ap.parse_args(argv)

    data_root = ls.bps.resolve_data_root(a.data_root)
    v2 = ls.v2_dir(data_root)
    root = Path(a.replay_root) if a.replay_root else v2.joinpath(*REPLAY_SUBDIR)
    out_path = Path(a.out)
    stage = Path(a.stage) if a.stage else v2.parent / "owned_claim" / "cash_execution_stage"
    notes: list[str] = []

    ev_path = Path(a.evidence)
    if not ev_path.exists():
        raise SystemExit(f"replay evidence artifact missing: {ev_path}")
    up = json.loads(ev_path.read_text())
    evidence = bool(up.get("evidence"))
    if not evidence:
        notes.append(
            "upstream replay artifact declares itself NON-EVIDENCE: "
            + "; ".join(up.get("non_evidence_notes") or ["unspecified"])
        )
    if a.limit_days:
        evidence = False
        notes.append(f"non-evidence read over the first {int(a.limit_days)} days")

    src = {}
    for name, path, declared in (
        ("producer_script", PRODUCER, up.get("source_sha256")),
        ("engine_script", ENGINE, up.get("engine_sha256")),
    ):
        if not path.exists():
            raise SystemExit(f"{path} is missing; this reader cannot name its producer")
        got = sha256_file(path)
        if declared and got != declared:
            raise SystemExit(f"{path} hashes {got}, not the pinned {declared}")
        src[name] = {"path": str(path), "sha256": got}

    def load(name: str):
        p = root / f"{name}.parquet"
        if not p.exists():
            raise SystemExit(f"{p} is missing; the reader refuses to describe absent frames")
        return pl.read_parquet(p)

    # daily and members spell the modelled per-leg cost `side`; fills calls it `side_cost`.
    # One name from here on, so a cell key means the same thing whichever frame it is built
    # from -- BOOK_KEYS and CELL_KEYS both use side_cost.
    daily = load("daily").rename({"side": "side_cost"})
    members = load("members").rename({"side": "side_cost"})
    fills = load("fills")
    files = {
        p.name: {
            "sha256": sha256_file(p),
            "rows": int(pl.scan_parquet(p).select(pl.len()).collect().item()),
        }
        for p in sorted(root.glob("*.parquet"))
    }

    days = sorted(daily["day"].unique().to_list())
    published = list(up.get("test_days") or days)
    if a.limit_days:
        days = days[: int(a.limit_days)]
    extra = sorted(set(days) - set(published))
    if extra:
        raise SystemExit(f"{len(extra)} replayed day(s) are not published test days; refusing")

    integrity = check_journal(daily, fills, root)

    # ------------------------------------------------------------- optional panel read
    if a.skip_mark_gap:
        marks, gap_reason = None, "--skip-mark-gap was passed"
        mstats = {"status": "not_read"}
    else:
        if a.fresh and stage.exists():
            for sub in ("_done", "days"):
                for f_ in (stage / sub).glob("*") if (stage / sub).exists() else []:
                    f_.unlink()
        marks, gap_reason, mstats = read_marks(
            fills.filter(pl.col("day").is_in(days)),
            v2,
            stage,
            up.get("execution_input_sha256") or {},
            days,
        )
        mstats = {"status": "read" if marks is not None else "not_read", **mstats}
        if marks is None:
            mstats["reason"] = gap_reason
            notes.append("optional source-panel gap read not used: " + str(gap_reason))
        else:
            mstats.update(
                {
                    "path_template": str(v2 / "panel" / "<day>.parquet"),
                    "columns_read": list(PANEL_COLS),
                    "engine_columns_it_read": (up.get("engine_projection_contract") or {}).get(
                        "engine_row_keys"
                    ),
                    "day_sha256_pinned_by_the_replay": bool(up.get("execution_input_sha256")),
                    "counts": "per-run panel read counts are reported per cell below",
                }
            )
    mark_cells = split_cells(marks) if marks is not None else {}

    # Every per-cell frame must cover the SAME day set, or the reconciliation compares a
    # three-day book against a 383-day journal.
    day_mask = pl.col("day").is_in(days)
    fills_in = fills.filter(day_mask)
    fills_cells = split_cells(fills_in)
    pairs_all = build_pairs(fills_in)
    pair_cells = split_cells(pairs_all)
    daily_cells = split_cells(daily.filter(day_mask))
    members_in = members.filter(day_mask)
    member_cells = split_cells(members_in)
    # ``recon`` derives the fully-known book count from the per-cell member slice itself, so
    # no whole-frame regroup happens inside the cell loop.
    smap = {}
    for r in load("summary").rename({"side": "side_cost"}).to_dicts():
        smap[cell_label(r)] = r
    timing_labels = (
        {cell_label(r) for r in load("timing").rename({"side": "side_cost"}).to_dicts()}
        if (root / "timing.parquet").exists()
        else set()
    )
    fold_ids = sorted(int(x) for x in daily["fold"].drop_nulls().unique().to_list())

    cells, worst, failing = [], 0.0, []
    for label, d in daily_cells.items():
        first = d.head(1).to_dicts()[0]
        f = fills_cells.get(label, fills.head(0))
        m = member_cells.get(label, members.head(0))
        p = pair_cells.get(label, pairs_all.head(0))
        g = mark_cells.get(label)
        rc = recon(d, f, m, p)
        worst = max(worst, float(rc["max_abs_residual"] or 0.0))
        if not rc["all_checks_pass"]:
            failing.append(label)
        by_fold = {}
        for fid in fold_ids:
            sub = d.filter(pl.col("fold") == fid)
            known = sub.filter(pl.col("ret").is_not_null())
            by_fold[str(fid)] = {
                "books": int(sub.height),
                "known_books": int(known.height),
                "unknown_books": int(sub["unknown"].sum() or 0),
                "orders": int(sub["orders"].sum() or 0),
                "net_sum_account_known": jnum(known["ret"].sum()),
                "net_mean_account_known": jnum(known["ret"].mean()),
                "affordability_unknown": int(sub["affordability_unknown"].sum() or 0),
            }
        cells.append(
            {
                "cell_key": list(CELL_KEYS),
                "clock": int(first["clock"]),
                "n": int(first["n"]),
                "side_cost": jnum(first["side_cost"]),
                "policy": first["policy"],
                "cycles": first["cycles"],
                "admission": first["admission"],
                "cell_label": label,
                "days": int(d.height),
                "comparison_guard": (
                    "compare only against a cell with the identical day set, clock, n and "
                    "side_cost; `cycles` is the predeclared control and is named, never collapsed. "
                    "These cells are ALTERNATIVE books, not sleeves of one portfolio, and their "
                    "money is never summed"
                ),
                "money_known_set": money(d, smap.get(label)),
                "reconciliation": rc,
                "ownership": ownership(d, m),
                "uncertainty": uncertainty(d, m),
                "et": et_block(f, p, int(first["clock"])),
                "exposure": exposure_block(d),
                "turnover": turnover_block(d, f, p),
                "participation": participation_block(f),
                "round_trip_pairs": pairs_block(p),
                "cycle_ledger": cycle_ledger_block(d, m, str(first["cycles"])),
                "outlier_dependence": outlier_block(d, p),
                "mark_to_next_open_gap": (
                    gap_block(g)
                    if g is not None
                    else {"status": "not_read", "reason": mstats.get("reason", gap_reason)}
                ),
                "by_fold": by_fold,
                "in_producer_timing_table": label in timing_labels,
            }
        )
    cells.sort(key=lambda c: c["cell_label"])

    expected = len(up.get("summary") or []) or len(cells)
    if len(cells) != expected:
        raise SystemExit(
            f"reader produced {len(cells)} cells; the producer published {expected} cell rows"
        )
    integrity["reader_cell_count"] = len(cells)
    integrity["producer_cell_count"] = expected
    integrity["max_abs_reconciliation_residual"] = jnum(worst)
    integrity["cells_failing_reconciliation"] = failing
    integrity["fold_assignment"] = {
        "source": "the replay's own daily.fold column, written from the score set's folds",
        "folds_present": fold_ids,
        "guessed_calendar_used": False,
    }

    known_all = daily.filter(pl.col("ret").is_not_null())
    artifact = {
        "kind": "CASH-FIRST-OWNED-CLAIM-OBSERVED-EXECUTION-READER-NOT-VALIDATED-EDGE",
        "evidence": evidence,
        "non_evidence_notes": notes,
        "reader_script": str(SCRIPT),
        "reader_script_sha256": sha256_file(SCRIPT),
        "scope": (
            "an observed-execution READER over the cash-first owned-claim replay. It describes "
            "legs that already executed and adds no threshold, gate, sizing rule, policy or "
            "bot, and it validates nothing"
        ),
        "source_provenance": {
            "producer_artifact": {
                "path": str(ev_path),
                "sha256": sha256_file(ev_path),
                "kind": up.get("kind"),
            },
            **src,
            "replay_root": str(root),
            "replay_files": files,
            "replay_outputs_read_only": "the replay's days/ journals are verified, never written",
            "stage_dir": str(stage),
            "stage_is_a_cache": (
                "the per-day panel stage is a resumable cache only; deleting it changes no "
                "published number"
            ),
        },
        "declared_source_panel": {
            "status": mstats.get("status"),
            "optional": True,
            "required_for_a_complete_read": False,
            "reason_not_read": mstats.get("reason"),
            "availability_basis": (
                "the replay artifact declares data_root and, under "
                "engine_projection_contract.engine_row_keys, the panel columns the engine "
                "itself consumed (px, sell_et, sell_px, sell_volume, t). No quote source, no "
                "network read and no prerequisite acquisition is involved"
            ),
            "data_root": str(data_root),
            "path_template": str(v2 / "panel" / "<day>.parquet"),
            "columns_read": list(PANEL_COLS),
            "engine_columns_it_read": (up.get("engine_projection_contract") or {}).get(
                "engine_row_keys"
            ),
            "per_day_sha256_pinned_by_the_replay": bool(up.get("execution_input_sha256")),
            "stats": mstats,
        },
        "financial_units": {
            "account_unit": (
                "every money-like field is a FRACTION OF ONE ACCOUNT DOLLAR: the ledger starts "
                "at cash = 1.0, so daily.ret = cash - 1, daily.gross_pnl = sum of signed gross "
                "flows, daily.fees = sum of per-leg fees, and fills.gross_fraction / "
                "fills.fee_fraction are in the same unit"
            ),
            "identity": "gross_pnl - fees = ret, per book, on the KNOWN set",
            "net_definition": "ret is a NET return AFTER both modelled legs' fees",
            "published_notional_usd": PUBLISHED_NOTIONAL,
            "published_notional_source": (
                "the engine's `notional` argument default; the producer calls simulate() "
                "without it, so this default is the size behind every participation figure"
            ),
            "published_notional_verification": {
                "method": (
                    "every leg's published participation is recomputed as "
                    "shares_per_capital * 10000.0 / panel.sell_volume from the source panel"
                ),
                "legs_checked": mstats.get("participation_legs_checked"),
                "max_abs_diff": mstats.get("participation_reproduction_max_abs_diff"),
                "relative_tolerance": REL_TOL,
                "verified_not_assumed": True,
            },
            "fee_model": (
                "MODELLED 100/150bps per actual traded leg, charged on that leg's actual fill "
                "price. A cost assumption, not an observed broker fee, rebate or venue schedule"
            ),
            "no_dollar_pnl_is_published": (
                "a dollar P&L would require choosing an account size; that choice is not made "
                "here, so account fractions are the terminal unit and the only dollar figure "
                "anywhere is the PUBLISHED notional inside the participation definition"
            ),
        },
        "contracts": {
            "alternative_books_never_summed": (
                "a cell is one alternative book over the same days; net_sum_account is a "
                "within-cell figure, never added across cells, never averaged across policies "
                "and never weighted into a portfolio"
            ),
            "comparison_is_exact": (
                "a policy comparison must hold day set, clock, n and side_cost identical and "
                "must name the cycles control; this reader publishes the keys and ranks nothing"
            ),
            "unknown_stays_unknown": (
                "UNKNOWN books and claims contribute no mean, median, fee average or EV. Money "
                "blocks are derived on the KNOWN book set only and say so; UNKNOWN is counted "
                "and named, never imputed and never folded into 0"
            ),
            "money_on_one_book_set": (
                "money is derived on the SAME known book set everywhere in a cell. The "
                "producer's summary.orders is an ALL-book mean and is never added to or "
                "compared against a known-set fee or EV"
            ),
            "no_new_thresholds": (
                "no hurdle, gate, quantile, score cut, sizing rule or filter is introduced, "
                "tuned or suggested; every field describes executed legs"
            ),
            "no_retroactive_entry_rules": (
                "the outlier block measures dependence only; no cut, cap or exclusion is "
                "derived from it and no leg was re-decided"
            ),
            "folds_from_score_metadata": (
                "day/clock/N/cost keys and the fold label come from the replay's own frames and "
                "the score set's folds; no calendar was guessed"
            ),
            "no_broker_certification": (
                "nothing here certifies a broker, venue, account, route, fill probability, queue "
                "position, limit-price fill or extended-hours behaviour"
            ),
        },
        "execution_limitations": {
            "proxy_not_quote": (
                "participation is order size over the traded volume of the bar whose open is "
                "the fill: a SIZE PROXY. The fill price is the OPEN of the next valid canonical "
                "bar, not a bid/ask quote, not a VWAP, not a mid and not an observed broker "
                "execution"
            ),
            "extended_hours": (
                "orders execute at the next valid bar open on the canonical grid inside the "
                "session window. There is no pre-market or after-hours routing, no overnight or "
                "24h order handling, no auction or opening-bell print and no dark or off-exchange "
                "pool in these frames; nothing here describes how such an order would fill"
            ),
            "fill_model": (
                "a modelled open at a FIXED quantity decided from the causal mark with 90% "
                "fixed execution headroom. No partial fill, queue, slippage model, borrow or "
                "locate, short-side handling or order-type choice"
            ),
            "settlement_lag_is_minutes_not_T_plus_1": {
                "engine_rule": (
                    "a sale's net receipt is queued to mature at exec_et + "
                    f"{SETTLEMENT_LAG_MINUTES} MINUTE and may fund another order only after that"
                ),
                "minute_lag_settled_slot": True,
                "broker_business_day_T_plus_1": False,
                "why_it_matters": (
                    "the replay artifact's cash_ledger_contract calls this 'T+1 settlement'. In "
                    "the engine it is a ONE-MINUTE in-model cash-reuse delay, not a business-day "
                    "cycle; reading it as broker T+1 would understate real cash availability by "
                    "a whole trading day"
                ),
                "CYCLE_SCOPED_it_binds_repeat_not_once": (
                    "under the ONCE control a claim buys at most once ever, so no sale proceeds "
                    "are ever redeployed and this mechanic cannot bind: every buy is funded from "
                    "the claim's own INITIAL segregated 1/n slot. Under the REPEAT control a "
                    "claim re-enters on its own settled proceeds, and THAT is the case this "
                    "caveat gates. It is not a blocker for an ONCE book, and it is not "
                    "broker-certified for a REPEAT book. The per-cell cycle_ledger block "
                    "reports the ACTUAL fresh_entries and reentries that decide which case each "
                    "cell is in"
                ),
                "no_broker_account_claim": (
                    "no cash account, broker ledger, real settlement, order-routing remainder or "
                    "pre-market order qty remaining is modelled or certified. The only cash rule "
                    "is the engine's segregated per-claim 1/n slot, audited from the published "
                    "cash_slot_end / settled_receipt columns and never borrowed across claims"
                ),
            },
            "forecast_price_approximation": (
                "the fresh hurdle is judged at the completed causal mark while the fill happens "
                "at the next open; mark_to_next_open_gap measures exactly that declared "
                "approximation and is not a broker slippage certificate"
            ),
            "exposure_marks": (
                "exposure snapshots are shares x the engine's last-finite causal mark, which "
                "reads 0 for a claim that never had one, so a snapshot can understate a held "
                "position; the understating counts are published, not corrected"
            ),
        },
        "integrity": integrity,
        "census": {
            "books": int(daily.height),
            "known_books": int(known_all.height),
            "unknown_books": int(daily["unknown"].sum() or 0),
            "member_rows": int(members.height),
            "members_unknown_net": int(members["net_pnl"].is_null().sum() or 0),
            "members_open_at_close": int(members["remaining_shares"].gt(CLOSE).sum() or 0),
            "legs": int(fills.height),
            "days": len(days),
            "census_only": (
                "these are COUNTS over the corpus. No net, fee or gross figure is added across "
                "cells anywhere: a sum over alternative books would be a portfolio nobody traded"
            ),
        },
        "cell_key_definition": {
            "keys": list(CELL_KEYS),
            "label": "clock{clock}|n{n}|s{side_cost}|{policy}|{cycles}",
            "cells": len(cells),
            "active_cells": sum(1 for c in cells if c["policy"] != "cash0:cash"),
            "cash0_reference_cells": sum(1 for c in cells if c["policy"] == "cash0:cash"),
            "cell_count_note": (
                "9 active views x 2 cycle controls x 4 clocks x 2 roster sizes x 2 modelled cost "
                "sides = 288 active cells, plus 16 cash0 reference cells (4 clocks x 2 N x 2 "
                "cost sides, once only) = 304"
            ),
            "cash0_meaning": (
                "the literal policy string is cash0:cash -- the SAME book with every forecast "
                "withheld, which must place no order and earn exactly 0. A ledger identity "
                "reference, not an experiment; it runs one cycles value because a cycle bound "
                "cannot bind when nothing executes"
            ),
            "side_cost_note": (
                "side_cost is the modelled per-leg cost in decimal: 0.005 = 100bps, 0.0075 = "
                "150bps; it is the replay's daily.side and fills.side_cost under one name"
            ),
        },
        "cells": cells,
        "caveats": [
            "every book here is a SIMULATED book on modelled opens with modelled per-leg costs; "
            "nothing was broker executed and nothing is broker certified",
            "participation is size-over-volume: not a fill probability, and silent on queue "
            "position, venue and route",
            "the sale-proceeds re-use delay is one minute, not broker business-day T+1, and it "
            "binds only under the repeat cycle control: a once book redeploys no sale "
            "proceeds at all, so the delay cannot bind there",
            "cash0:cash is the ledger identity that must place zero orders and earn exactly zero; "
            "it is not a return claim",
            "UNKNOWN books and claims are excluded from every mean and never imputed",
            "cells are alternative books of the same days and are never summed into a portfolio",
            "the outlier block is a concentration read of an existing result; no rule is derived "
            "from it and none was optimized",
            "the mark-to-next-open gap is a declared price approximation measured after the fact, "
            "not a quote-level slippage certificate",
        ],
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps(artifact, indent=2, allow_nan=False, default=str) + "\n")
    tmp.replace(out_path)
    print(
        f"[cash-exec] cells={len(cells)} books={integrity['books']} legs={integrity['fills_rows']} "
        f"unknown_books={artifact['census']['unknown_books']} max_recon_residual="
        f"{artifact['integrity']['max_abs_reconciliation_residual']:g} gap={mstats.get('status')} "
        f"evidence={evidence} -> {out_path}",
        flush=True,
    )
    if failing:
        print(f"[cash-exec] WARNING cells failing reconciliation: {failing[:5]}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
