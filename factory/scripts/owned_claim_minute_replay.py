#!/usr/bin/env python3
"""FULL-MINUTE OWNED-CLAIM REPLAY — minute prediction points through the unchanged engine.

What this is
------------
The eight-event-hop machine (``owned_claim_events`` -> thin -> ``owned_claim_value`` ->
``owned_claim_replay``) can only act on the handful of minutes where a flag fired.  This
replay feeds ``owned_claim_minute_value``'s predictions to the UNCHANGED
``owned_claim_replay.simulate`` at EVERY actual minute of every owned claim, so a claim
that is held is re-decided one minute later rather than at the next sampled event.  That
is the whole point: observation thinning can miss the death of a continuation, and only a
minute grid can show it.

Contracts
---------
* Engine: ``owned_claim_replay.simulate`` is imported and called unchanged.  Actual shares,
  equal-dollar admission, entry sunk, the common exit fee, the T+1 settlement delay, the
  cash ledger and the UNKNOWN accounting are the canonical ones, not re-implemented here.
* Units: predictions are mean-dollar increments per original GROSS entry dollar — the same
  scale as ``owned_claim_events``' ``increment_{h}_original_dollar``.  ``simulate`` releases
  when ``pred_<view> < 0``; no predicted quantity is ever rescaled, clipped or calibrated.
* No TEST outcome decides an action.  The score files carry no label, status or outcome
  column at all, and the only learned action inputs are the predicted dollars.
* Units: the published predictions are already denominated per ORIGINAL CLAIM CASH dollar,
  which is exactly what ``simulate`` spends (``amount = 1/n`` cash, ``q = (1/n)/(fill_px*(1+s))``).
  They are handed to the engine UNCHANGED -- no fee-ratio rescaling, because none is needed.
  A gross-unit score set would have to be divided by ``(1-s)/(1+s)`` first; this consumer reads
  the unit declaration from the producer metadata instead of assuming one.
* Calendar exits and UNKNOWN forecasts: where a fixed-horizon view's target is calendar-
  infeasible the producer publishes a declared ACTION sentinel (``CALENDAR_EXIT``), because a
  zero increment would read as HOLD under the engine's ``pred < 0`` release rule.  This replay
  counts those cells (``calendar_forced_exit_*``) rather than silently treating them as
  forecasts.  A non-finite prediction would make the engine hold silently, so the run refuses.
* No book date is ever dropped: a filled book with no published scores aborts the run; a book
  with no filled claim is replayed and counted as known cash; a clock with no roster is recorded
  as a nonexistent book.  Concentration statistics are computed per DATE (mean across that
  date's clock books), never by treating four clock books as four independent dates.
* N3 and N5 are never crossed: the N3 book reads ``scores_n3/`` and the N5 book reads
  ``scores_n5/``.  A view trained on one roster size is never applied to the other.
* Rulers kept identical to core so the comparison is about decision cadence, not plumbing:
  ``hold`` (no decisions), ``fade`` (core's full-minute state ruler), both at the modeled
  100/150bps per traded leg, with the four admission clocks and N3/N5 reported separately.
* Start point is stop-only: no classifier, no roster substitution, no re-entry/rotation
  modes.  A minute policy sells the whole inherited position the first minute its predicted
  increment is negative, and otherwise holds and re-decides one minute later.

Outputs (under --out)
  days/<day>.{daily,members,fills}.parquet  staged per day, resumable
  daily.parquet / summary.parquet / folds.parquet / months.parquet
  paired.parquet / concentration.json / timing.parquet / decision_grid.parquet
  <evidence>.json

``--smoke-days`` (or a non-evidence upstream score set) publishes under ``<out>/smoke`` and
is labelled ``"evidence": false`` everywhere.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import re
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
import owned_claim_replay as core  # noqa: E402  (unchanged accounting engine)

SCRIPT = Path(__file__).resolve()
CORE_SRC = Path(core.__file__).resolve()

CLOCKS = tuple(core.CLOCKS)  # (540, 560, 569, 571)
NS = (3, 5)
SIDES = tuple(core.SIDES)  # 0.005 / 0.0075 -> the modeled 100/150bps traded leg pair
RULERS = ("hold", "fade")
# columns the engine actually reads; everything else is dropped before the per-clock dicts
ROSTER_COLS = (
    "day",
    "clock",
    "rank",
    "ticker",
    "decision_px",
    "fill_et",
    "fill_px",
    "fill_volume",
    "status",
    "session_end",
)
ROW_COLS = (
    "day",
    "clock",
    "rank",
    "ticker",
    "t",
    "px",
    "sell_et",
    "sell_px",
    "sell_volume",
    "fill_et",
    "ret_fill",
    "dd_from_high",
    "minutes_since_high",
    "ret5",
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _finite(x) -> bool:
    return x is not None and np.isfinite(float(x))


def assert_engine_columns() -> dict:
    """Guard this projection against the unchanged engine.

    The replay projects the panel/roster down to the columns ``simulate`` actually reads
    instead of materialising the full frame.  If the engine ever starts reading another
    column, the run refuses rather than silently handing it a missing key.
    """
    src = CORE_SRC.read_text()
    row_keys = set(re.findall(r'(?<![A-Za-z_0-9])row(?:\.get\(|\[)"([A-Za-z_0-9]+)"', src))
    roster_keys = set(re.findall(r'(?<![A-Za-z_0-9])r(?:\.get\(|\[)"([A-Za-z_0-9]+)"', src))
    short_rows = sorted(row_keys - set(ROW_COLS))
    short_roster = sorted(roster_keys - set(ROSTER_COLS))
    if short_rows or short_roster:
        raise SystemExit(
            "owned_claim_replay.simulate reads columns this replay does not project: "
            f"rows={short_rows} roster={short_roster}; re-audit the projection"
        )
    return {
        "engine_row_keys": sorted(row_keys),
        "engine_roster_keys": sorted(roster_keys),
    }


def concentration(per_date: np.ndarray, names: list[str]) -> dict:
    """How concentrated a policy's positive DATE mass is. Reported, never optimized.

    `per_date` holds ONE figure per actual (clock, n, side, policy) book-date -- the admission
    clock is never averaged away -- so day counts and top-k shares are statements about that
    book's dates.
    """
    x = np.asarray(per_date, dtype=np.float64)
    pos = x[x > 0]
    out = {
        "days": int(x.size),
        "positive_days": int(pos.size),
        "negative_days": int((x < 0).sum()),
        "total": float(x.sum()),
        "mean": float(x.mean()) if x.size else None,
        "best": float(x.max()) if x.size else None,
        "best_day": names[int(x.argmax())] if x.size else None,
        "worst": float(x.min()) if x.size else None,
        "worst_day": names[int(x.argmin())] if x.size else None,
        "losing_day_share": float((x < 0).mean()) if x.size else None,
    }
    if pos.size == 0:
        out.update(
            {
                "top1_day_share_of_positive_mass": None,
                "top5_day_share_of_positive_mass": None,
                "top10pct_day_share_of_positive_mass": None,
                "days_without_top10pct_meaning": True,
            }
        )
        return out
    order = np.sort(pos)[::-1]
    mass = float(order.sum())
    k10 = max(1, int(round(0.10 * order.size)))
    out.update(
        {
            "top1_day_share_of_positive_mass": float(order[0] / mass),
            "top5_day_share_of_positive_mass": float(order[: min(5, order.size)].sum() / mass),
            "top10pct_day_share_of_positive_mass": float(order[:k10].sum() / mass),
            "days_without_top10pct_meaning": False,
        }
    )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="replay full-minute owned-claim predictions through the unchanged engine"
    )
    ap.add_argument("--data-root", default=None)
    ap.add_argument("--scores-root", required=True, help="owned_claim_minute_value fit --out")
    ap.add_argument("--out", required=True)
    ap.add_argument("--evidence", required=True, help="path of the JSON evidence artifact")
    ap.add_argument(
        "--views",
        default=None,
        help="comma-separated score views to replay (default: every view the fit published)",
    )
    ap.add_argument(
        "--smoke-days",
        type=int,
        default=0,
        help="tiny non-evidence run over the first N published test days",
    )
    a = ap.parse_args(argv)

    # A non-evidence fit publishes under <scores-root>/smoke.  Resolve to whichever metadata
    # actually exists: the requested one, else the smoke sibling.  Nothing is invented.
    scores_root = Path(a.scores_root)
    meta_path = scores_root / "metadata.json"
    smoke_meta = scores_root / "smoke" / "metadata.json"
    notes: list[str] = []
    if a.smoke_days and smoke_meta.exists():
        scores_root, meta_path = scores_root / "smoke", smoke_meta
    elif not meta_path.exists() and smoke_meta.exists():
        scores_root, meta_path = scores_root / "smoke", smoke_meta
        notes.append("only a non-evidence (smoke) score set exists under this root")
    if not meta_path.exists():
        raise SystemExit(
            f"minute score metadata missing at {meta_path} and {smoke_meta}; "
            "run the fit command first"
        )
    meta = json.loads(meta_path.read_text())
    if meta.get("status") != "complete":
        raise SystemExit("minute score set is not complete")
    up_evidence = bool(meta.get("evidence", False))
    out = Path(a.out)
    evidence = True
    if not up_evidence:
        evidence = False
        notes.append(
            "upstream minute score set is non-evidence ("
            + "; ".join(meta.get("non_evidence_notes") or ["unspecified"])
            + ")"
        )
    if a.smoke_days:
        evidence = False
        notes.append(f"non-evidence replay over the first {int(a.smoke_days)} published test days")
        out = out / "smoke"
    views_all = list(meta["views"])
    if a.views:
        views = [v.strip() for v in str(a.views).split(",") if v.strip()]
        bad = [v for v in views if v not in views_all]
        if bad or not views:
            raise SystemExit(f"--views must be a subset of {views_all}; rejected {bad}")
        if views != views_all:
            evidence = False
            notes.append(f"view subset {views} of {views_all} (diagnostic only)")
    else:
        views = views_all
    pred_cols = [f"pred_{v}" for v in views]
    sentinel = meta.get("calendar_exit_sentinel")
    policies = list(RULERS) + [f"{v}:stop" for v in views]

    days = sorted({d for w in meta["folds"] for d in w["test_days"]})
    if a.smoke_days:
        days = days[: int(a.smoke_days)]
    disc = set(ls.discovery_days(ls.bps.resolve_data_root(a.data_root)))
    if not set(days) <= disc:
        raise SystemExit("published score days are not all discovery days; refusing")
    engine_contract = assert_engine_columns()

    fold_of = {day: w["fold"] for w in meta["folds"] for day in w["test_days"]}
    evidence_path = Path(a.evidence)
    if not evidence and evidence_path.stem == "evidence":
        # a non-evidence run must never be able to overwrite a real evidence artifact
        evidence_path = evidence_path.with_name(evidence_path.stem + ".non_evidence")
    pin = sha256_file(SCRIPT)
    core_pin = sha256_file(CORE_SRC)
    score_digest = {
        day: {f"n{n}": sha256_file(scores_root / f"scores_n{n}" / f"{day}.parquet") for n in NS}
        for day in days
    }
    resume_pin = hashlib.sha256(
        (
            pin + core_pin + sha256_file(meta_path) + json.dumps(score_digest, sort_keys=True)
        ).encode()
    ).hexdigest()

    days_dir = out / "days"
    done_dir = out / "_done"
    days_dir.mkdir(parents=True, exist_ok=True)
    done_dir.mkdir(parents=True, exist_ok=True)
    grid_rows: list[dict] = []
    data_root = ls.bps.resolve_data_root(a.data_root)

    for i, day in enumerate(days):
        marker = done_dir / f"{day}.json"
        paths = {k: days_dir / f"{day}.{k}.parquet" for k in ("daily", "members", "fills")}
        if marker.exists() and paths["daily"].exists() and paths["members"].exists():
            info = json.loads(marker.read_text())
            if info.get("resume_pin") == resume_pin:
                grid_path = days_dir / f"{day}.grid.parquet"
                if grid_path.exists():
                    grid_rows.extend(pl.read_parquet(grid_path).to_dicts())
                if i % 25 == 0:
                    print(
                        f"[minute replay] {i + 1}/{len(days)} {day} (resumed)",
                        flush=True,
                    )
                continue
        roster_all = ls.load_roster([day], data_root, clocks=CLOCKS)
        missing_roster = [c for c in ROSTER_COLS if c not in roster_all.columns]
        if missing_roster:
            raise SystemExit(f"{day}: roster lacks {missing_roster}")
        panel = ls.load_panel([day], data_root, clocks=CLOCKS, with_tape=False)
        missing_rows = [c for c in ROW_COLS if c not in panel.columns]
        if missing_rows:
            raise SystemExit(f"{day}: panel lacks {missing_rows}")
        score_frames = {}
        for n in NS:
            path = scores_root / f"scores_n{n}" / f"{day}.parquet"
            if not path.exists():
                raise SystemExit(f"missing {path}; run the fit command")
            frame = pl.read_parquet(path, columns=["clock", "rank", "ticker", "t", *pred_cols])
            # Only keys and predictions may cross into the engine: no label, status or outcome
            # is read from disk, so none can ever reach an action.
            stray = [
                c
                for c in frame.columns
                if c not in ("clock", "rank", "ticker", "t") and c not in pred_cols
            ]
            if stray:
                raise SystemExit(f"{path} carries non-prediction columns {stray}; refusing")
            score_frames[n] = frame
        daily: list[dict] = []
        members: list[dict] = []
        fills: list[dict] = []
        day_grid: list[dict] = []
        for clock in CLOCKS:
            roster_clock = roster_all.filter(pl.col("clock") == clock)
            if roster_clock.height == 0:
                # No roster at this admission clock: there is no book to own, hence no cash
                # exposure.  Recorded explicitly so the day is not silently short a book.
                for n in NS:
                    day_grid.append(
                        {
                            "day": day,
                            "clock": clock,
                            "n": n,
                            "score_rows": 0,
                            "decision_cells_in_roster": 0,
                            "owned_post_fill_cells": 0,
                            "distinct_decision_minutes": 0,
                            "minute_coverage": None,
                            "median_gap_between_decision_minutes": None,
                            "max_gap_between_decision_minutes": None,
                            "first_decision_minute": None,
                            "last_decision_minute": None,
                            "calendar_exit_cells": 0,
                            "note": "no roster at this admission clock: book does not exist",
                        }
                    )
                continue
            rows_clock = panel.filter(pl.col("clock") == clock).select(list(ROW_COLS)).to_dicts()
            # every post-fill minute the engine can visit for a roster ticker is an owned cell
            for n in NS:
                score = score_frames[n].filter(pl.col("clock") == clock)
                roster = roster_clock.filter(pl.col("rank") <= n)
                roster = roster.select(list(ROSTER_COLS)).to_dicts()
                if not roster:
                    raise SystemExit(
                        f"{day} clock {clock} n{n}: empty rank<=n roster; refusing to "
                        "silently lose the book date"
                    )
                roster_tickers = {r["ticker"] for r in roster}
                fill_et_of = {
                    r["ticker"]: int(r["fill_et"])
                    for r in roster
                    if r["status"] == "filled" and _finite(r["fill_et"])
                }
                owned_cells = sum(
                    1
                    for r in rows_clock
                    if r["ticker"] in fill_et_of and r["t"] > fill_et_of[r["ticker"]]
                )
                if score.height == 0 and owned_cells:
                    # A FILLED book with no published prediction is a corpus/engine gap, not a
                    # state: hold, UNKNOWN or cash may each be defensible, so the run stops
                    # instead of choosing one.  Missing scores are never a fallback to hold.
                    raise SystemExit(
                        f"{day} clock {clock} n{n}: {owned_cells} owned post-fill minutes but no "
                        f"published N{n} scores; refusing to replay a filled book without them"
                    )
                if score.height == 0:
                    # Genuinely no filled claim in this book: it is a KNOWN-CASH book (nothing is
                    # bought, so every policy earns exactly 0).  It is still replayed and still
                    # counted, so the date is not lost from any summary.
                    day_grid.append(
                        {
                            "day": day,
                            "clock": clock,
                            "n": n,
                            "score_rows": 0,
                            "decision_cells_in_roster": 0,
                            "owned_post_fill_cells": 0,
                            "distinct_decision_minutes": 0,
                            "minute_coverage": None,
                            "median_gap_between_decision_minutes": None,
                            "max_gap_between_decision_minutes": None,
                            "first_decision_minute": None,
                            "last_decision_minute": None,
                            "calendar_exit_cells": 0,
                            "note": "known-cash book: no filled claim at this clock/roster size",
                        }
                    )
                    empty_predictions: dict = {}
                    for side in SIDES:
                        for policy in policies:
                            d, m, f = core.simulate(
                                rows_clock, roster, empty_predictions, side, n, policy
                            )
                            daily.append(d)
                            members.extend(m)
                            fills.extend(f)
                    continue
                # ---- published predictions must be finite: an UNKNOWN forecast would make the
                # ---- engine hold silently, which is exactly the silent-zero this project bans.
                bad_pred = [
                    c
                    for c in pred_cols
                    if (~score[c].cast(pl.Float64, strict=False).is_finite()).any()
                ]
                if bad_pred:
                    raise SystemExit(
                        f"{scores_root / f'scores_n{n}' / f'{day}.parquet'} has non-finite "
                        f"predictions in {bad_pred}; a non-finite forecast would read as a "
                        "silent HOLD -- refusing"
                    )
                # one pass: the engine's (ticker, minute) -> prediction map for this roster only
                predictions = {
                    (r["ticker"], int(r["t"])): r
                    for r in score.to_dicts()
                    if r["ticker"] in roster_tickers
                }
                in_roster = list(predictions)
                # calendar-forced exits actually published in this book (action sentinel only,
                # never a forecast), counted from the score file rather than inferred
                exit_cells = (
                    int(sum(int((score[c] == sentinel).sum()) for c in pred_cols))
                    if sentinel is not None
                    else None
                )
                # decision-cadence audit: per-ticker re-decision gaps must be one minute
                per_ticker: dict[str, list[int]] = {}
                for tk, t in in_roster:
                    per_ticker.setdefault(tk, []).append(t)
                gaps = np.concatenate(
                    [np.diff(sorted(v)) for v in per_ticker.values() if len(v) > 1]
                    or [np.zeros(0, dtype=np.int64)]
                )
                minutes = sorted({t for _, t in in_roster})
                day_grid.append(
                    {
                        "day": day,
                        "clock": clock,
                        "n": n,
                        "score_rows": int(score.height),
                        "decision_cells_in_roster": len(in_roster),
                        "owned_post_fill_cells": owned_cells,
                        "distinct_decision_minutes": len(minutes),
                        "minute_coverage": (len(in_roster) / owned_cells) if owned_cells else None,
                        "median_gap_between_decision_minutes": float(np.median(gaps))
                        if gaps.size
                        else None,
                        "max_gap_between_decision_minutes": int(gaps.max()) if gaps.size else None,
                        "first_decision_minute": minutes[0] if minutes else None,
                        "last_decision_minute": minutes[-1] if minutes else None,
                        "calendar_exit_cells": exit_cells,
                        "note": "",
                    }
                )
                for side in SIDES:
                    for policy in policies:
                        d, m, f = core.simulate(rows_clock, roster, predictions, side, n, policy)
                        daily.append(d)
                        members.extend(m)
                        fills.extend(f)
                del predictions, per_ticker
            del rows_clock
        del panel, roster_all, score_frames
        gc.collect()
        for key, values in (("daily", daily), ("members", members), ("fills", fills)):
            if not values:
                continue
            frame = pl.DataFrame(values, infer_schema_length=None)
            if key == "daily":
                frame = frame.with_columns(
                    pl.col("unknown_tickers").cast(pl.List(pl.String)),
                    pl.col("exit_times").cast(pl.List(pl.Int64)),
                )
            tmp = paths[key].with_suffix(".tmp.parquet")
            frame.write_parquet(tmp)
            tmp.replace(paths[key])
        if day_grid:
            tmp = (days_dir / f"{day}.grid.parquet").with_suffix(".tmp.parquet")
            pl.DataFrame(day_grid).write_parquet(tmp)
            tmp.replace(days_dir / f"{day}.grid.parquet")
            grid_rows.extend(day_grid)
        marker.write_text(
            json.dumps(
                {
                    "status": "ok",
                    "resume_pin": resume_pin,
                    "daily_rows": len(daily),
                    "members": len(members),
                    "fills": len(fills),
                }
            )
            + "\n"
        )
        if i % 25 == 0:
            print(
                f"[minute replay] {i + 1}/{len(days)} {day} daily={len(daily)} fills={len(fills)}",
                flush=True,
            )

    daily = pl.concat(
        [pl.read_parquet(days_dir / f"{day}.daily.parquet") for day in days],
        how="diagonal_relaxed",
    ).with_columns(
        pl.col("day").str.slice(0, 7).alias("month"),
        pl.col("day").replace_strict(fold_of, default=None).alias("fold"),
    )
    daily.write_parquet(out / "daily.parquet")
    members = pl.concat(
        [pl.read_parquet(days_dir / f"{day}.members.parquet") for day in days],
        how="diagonal_relaxed",
    )
    members.write_parquet(out / "members.parquet")
    fill_days = [d for d in days if (days_dir / f"{d}.fills.parquet").exists()]
    if fill_days:
        pl.concat(
            [pl.read_parquet(days_dir / f"{d}.fills.parquet") for d in fill_days],
            how="diagonal_relaxed",
        ).write_parquet(out / "fills.parquet")
    grid = pl.concat(
        [pl.read_parquet(days_dir / f"{d}.grid.parquet") for d in days],
        how="diagonal_relaxed",
    )
    grid.write_parquet(out / "decision_grid.parquet")

    group_cols = ["clock", "n", "side", "policy"]

    def agg(frame: pl.DataFrame, keys: list[str]) -> pl.DataFrame:
        return frame.group_by(keys).agg(
            pl.len().alias("days"),
            pl.col("ret").count().alias("known_days"),
            pl.col("unknown").sum().alias("unknown_days"),
            pl.col("ret").mean().alias("ev"),
            pl.col("ret").median().alias("median"),
            (pl.col("ret").std() / pl.col("ret").count().sqrt()).alias("day_se"),
            pl.col("fees").filter(pl.col("ret").is_not_null()).mean().alias("fees"),
            pl.col("gross_pnl").filter(pl.col("ret").is_not_null()).mean().alias("gross_pnl"),
            pl.col("affordability_unknown").sum().alias("affordability_unknown"),
            pl.col("orders").mean().alias("orders"),
            pl.col("max_positions").mean().alias("mean_positions"),
        )

    summary = agg(daily, group_cols)
    summary.write_parquet(out / "summary.parquet")
    fold_summary = agg(daily.drop_nulls("fold"), ["fold", *group_cols])
    fold_summary.write_parquet(out / "folds.parquet")
    month_summary = agg(daily, ["month", *group_cols])
    month_summary.write_parquet(out / "months.parquet")

    paired: list[dict] = []
    for ruler in RULERS:
        ref = daily.filter(pl.col("policy") == ruler).select(
            ["day", "clock", "n", "side", pl.col("ret").alias("_ref")]
        )
        cmp = (
            daily.join(ref, on=["day", "clock", "n", "side"])
            .filter(pl.col("ret").is_not_null() & pl.col("_ref").is_not_null())
            .with_columns((pl.col("ret") - pl.col("_ref")).alias("_delta"))
        )
        rows = cmp.group_by(group_cols).agg(
            pl.len().alias("paired_days"),
            pl.col("_delta").mean().alias("mean_delta"),
            (pl.col("_delta").std() / pl.col("_delta").count().sqrt()).alias("delta_se"),
            pl.col("_delta").median().alias("median_delta"),
            (pl.col("_delta") > 0).mean().alias("share_beating_reference"),
        )
        paired.extend(dict(r, reference=ruler) for r in rows.to_dicts())
    paired_frame = pl.DataFrame(paired, infer_schema_length=None)
    paired_frame.write_parquet(out / "paired.parquet")

    # Concentration is a statement about DATES of an ACTUAL BOOK.  The admission clock is part
    # of the book identity, so it stays a grouping key: (clock, n, side, policy, day) is one
    # real book on one date.  Averaging across the four clocks would invent an ensemble of
    # admission clocks that was never the strategy tested, so it is not done.
    per_date = (
        daily.filter(pl.col("ret").is_not_null())
        .group_by(["clock", "n", "side", "policy", "day", "month"])
        .agg(
            pl.col("ret").first().alias("book_date_ret"),
            pl.col("unknown").first().alias("unknown_book"),
            pl.len().alias("rows"),
        )
    )
    per_date.write_parquet(out / "per_date.parquet")
    conc: dict[str, dict] = {}
    for key, part in per_date.group_by(["clock", "n", "side", "policy"], maintain_order=True):
        clock, n, side, policy = key if isinstance(key, tuple) else (key,)
        names = part["day"].to_list()
        vals = part["book_date_ret"].to_numpy().astype(np.float64)
        entry = concentration(vals, names)
        entry["dates_with_an_unknown_book"] = int(part.filter(pl.col("unknown_book")).height)
        entry["month_positive_mass"] = {}
        label_key = f"clock{clock}|n{n}|{side}|{policy}"
        conc[label_key] = entry
        m = (
            part.group_by("month")
            .agg(pl.col("book_date_ret").sum().alias("mass"))
            .sort("mass", descending=True)
        )
        positive_mass = float(m["mass"].sum())
        if positive_mass > 0:
            slot = conc[label_key]["month_positive_mass"]
            for row in m.to_dicts():
                slot[row["month"]] = {
                    "mass": row["mass"],
                    "share_of_net": row["mass"] / positive_mass,
                }
    conc["note"] = (
        "concentration is per ACTUAL BOOK per date: keys are (clock, n, side, policy, day) and "
        "the admission clock is never averaged away, because averaging the four clocks would "
        "describe an ensemble of admission clocks that was not the strategy tested. Day and "
        "month counts are dates of that book, not book-rows pooled across clocks."
    )

    timing = (
        daily.explode("exit_times", empty_as_null=False)
        .filter(pl.col("exit_times").is_not_null())
        .group_by(group_cols)
        .agg(
            pl.col("exit_times").median().alias("median_exit_et"),
            pl.col("exit_times").min().alias("first_exit_et"),
            (pl.col("exit_times") < 780).mean().alias("share_exits_before_13"),
            pl.len().alias("exits"),
        )
    )
    timing.write_parquet(out / "timing.parquet")

    grid_summary = grid.group_by(["n", "clock"]).agg(
        pl.len().alias("books"),
        pl.col("distinct_decision_minutes").mean().alias("mean_decision_minutes"),
        pl.col("minute_coverage").mean().alias("mean_minute_coverage"),
        pl.col("minute_coverage").min().alias("min_minute_coverage"),
        pl.col("median_gap_between_decision_minutes").max().alias("worst_median_gap"),
        pl.col("max_gap_between_decision_minutes").max().alias("worst_gap"),
        pl.col("decision_cells_in_roster").mean().alias("mean_decision_cells"),
        pl.len().filter(pl.col("note") != "").alias("books_without_scores"),
    )
    grid_summary.write_parquet(out / "decision_grid_summary.parquet")

    artifact = {
        "kind": "DISCOVERY-FULL-MINUTE-OWNED-CLAIM-PORTFOLIO-PROXY-NOT-VALIDATED-EDGE",
        "evidence": evidence,
        "non_evidence_notes": notes,
        "source_sha256": pin,
        "engine": "factory/scripts/owned_claim_replay.simulate (imported unchanged)",
        "engine_sha256": core_pin,
        "engine_projection_contract": engine_contract,
        "engine_projection_note": (
            "the panel/roster are projected to the columns the unchanged engine reads; "
            "assert_engine_columns() refuses the run if the engine ever reads another"
        ),
        "scores_root": str(scores_root),
        "scores_metadata": str(meta_path),
        "scores_metadata_sha256": sha256_file(meta_path),
        "scores_run_id": meta.get("run_id"),
        "score_day_sha256": score_digest,
        "data_root": str(data_root),
        "test_days": days,
        "views_run": views,
        "policies": policies,
        "modes_run": ["stop"],
        "clocks": list(CLOCKS),
        "ns": list(NS),
        "sides": list(SIDES),
        "friction": "modeled 100/150bps per actual traded leg pair, not quote/queue certified",
        "prediction_units": meta.get("prediction_units"),
        "unit_boundary": (
            "scores are ORIGINAL CLAIM CASH dollars at the modeled 50bps fee, i.e. the canonical "
            "owned_claim_events increment unit: cash = GROSS delta x (1-s)/(1+s). The raw GROSS "
            "equivalent of any cash figure is cash/0.990049. NO magnitude unit identity is "
            "claimed against the engine and nothing is rescaled: the unchanged stop-only engine "
            "consumes the SIGN of pred_* only (release when pred < 0), so any positive constant "
            "transform produces identical actions. Only the stop mode is run here; a magnitude-"
            "consuming mode (core's reentry/allocate hurdle arithmetic) is NOT exercised by this "
            "replay and would require an explicit unit decision."
        ),
        "calendar_exit_sentinel": meta.get("calendar_exit_sentinel"),
        "calendar_exit_sentinel_meaning": meta.get("calendar_exit_sentinel_meaning"),
        "calendar_forced_exit_books": int(grid.filter(pl.col("calendar_exit_cells") > 0).height),
        "calendar_forced_exit_cells": int(grid["calendar_exit_cells"].sum()),
        "policy_assumptions": meta.get("policy_assumptions"),
        "n_isolation": (
            "N3 books are replayed exclusively from scores_n3 and N5 books exclusively from "
            "scores_n5; no view trained on one roster size is applied to the other"
        ),
        "test_discipline": (
            "predictions are the only learned action input; the score files contain no label, "
            "status or outcome column, and no TEST outcome gated any action"
        ),
        "decision_cadence": grid_summary.to_dicts(),
        "summary": summary.to_dicts(),
        "folds": fold_summary.to_dicts(),
        "months": month_summary.to_dicts(),
        "paired": paired,
        "concentration": conc,
        "timing": timing.to_dicts(),
        "missing_scores_contract": (
            "a book with a filled claim and no published prediction aborts the run; a book with "
            "no filled claim is replayed and counted as known cash (ret exactly 0); an absent "
            "roster at a clock is recorded as a nonexistent book. No book date is ever dropped "
            "silently and no missing score is ever treated as HOLD."
        ),
        "unknown_forecast_contract": (
            "non-finite predictions abort the run: a NaN would make the engine hold silently, "
            "which is the silent-zero this project bans."
        ),
        "caveats": [
            "stop-only start: a minute policy exits on the first negative predicted increment "
            "and never re-enters; re-entry/rotation are out of scope here.",
            "hold/fade are core's own rulers, replayed on the identical books, so the "
            "comparison isolates decision cadence.",
            "100/150bps per traded leg pair is modeled, not quote/queue certified.",
            "equal-dollar admission and the terminal session boundary are disclosed rulers.",
            "UNKNOWN cashflows are counted and excluded; paired comparisons use jointly known "
            "days only; no protected-half read.",
            "pred_max is an optimism assumption (a max over noisy forecasts), not an optimum; "
            "the per-horizon views are shipped beside it.",
        ],
    }
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(artifact, indent=2, allow_nan=False, default=str) + "\n")
    print(f"[minute replay] evidence artifact -> {evidence_path}", flush=True)
    print(
        summary.filter((pl.col("side") == SIDES[0]) & (pl.col("n") == NS[0])).sort(
            ["clock", "ev"], descending=[False, True]
        ),
        flush=True,
    )
    print(
        f"[minute replay] complete days={len(days)} policies={len(policies)} "
        f"evidence={evidence} -> {out}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
