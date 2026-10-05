#!/usr/bin/env python3
"""CASH-FIRST OWNED-CLAIM REPLAY -- start cash on the same original roster.

What this is
------------
The full-minute replay (``owned_claim_minute_replay``) pays the entry fee up front for
every original claim and then only decides whether to STOP.  That forces every book to
own its roster, and its measured net was negative: the gross increment
(``+.474685%``/day) does not cover the round-trip fee (``.985146%``/day).  That is a
statement about the ALLOCATION, not yet about the PREDICTION, because upfront admission
also commits capital to the claims a forecast likes least and forces an exit on any
negative number regardless of whether the exit itself costs money.

This replay changes capital admission and sizing on the frozen original roster and scores:

  * the account starts as CASH on the SAME original roster -- no initial buy, no
    reservation, no entry fee;
  * every original claim owns its own ``1/n`` cash slot from ``t=0``, and buys only when
    the forecast's marginal money clears the ACTUAL round-trip hurdle;
  * a position already held is still sold on the SIGN of the forecast alone.

That is COST HYSTERESIS, the economically correct asymmetry: once entry is paid, the
entry cost is SUNK and only the marginal continuation matters, so the held threshold is
zero; before entry, the round-trip fee is still ahead of the decision maker, so the fresh
threshold is the derived hurdle ``h = 2*s/(1-s)``. Both legs pay half of the declared
25/50/100/150bps total round-trip friction; no time or return-count rule is introduced.

This is distinct from the already-tested forced-upfront 8-event/EOD-PI admission and from
the static first+5 reserve: here the same minute grid, the same frozen OOF forecasts and
the same roster decide BOTH whether to enter and when to leave.

Contracts
---------
* Engine: ``owned_claim_replay.simulate`` is imported and called with the approved
  keyword-only ``admission='cash'``. Actual fills, the cash ledger, the execution-minute
  +1 proceeds-reuse delay, per-claim segregation and UNKNOWN accounting are canonical,
  never re-implemented or copied here.
* Units: the frozen scores are incremental dollars per ORIGINAL CLAIM CASH dollar at the
  modeled 50bps fee, i.e. ``cash = GROSS delta * F`` with ``F = 0.995/1.005``.  The fresh
  hurdle is a GROSS-magnitude comparison, so this consumer converts
  ``gross = cash / F`` BEFORE the engine sees it.  Nothing else is rescaled, and the sign
  -- which is all a held exit consumes -- is unchanged by a positive divisor.
* Forecast price approximation: the marginal-money hurdle is evaluated at the COMPLETED
  CAUSAL MARK of the decision minute, not at the next open, because the next open is not
  known at decision time.  This is a disclosed approximation, not an execution
  certificate.  The ACTUAL fill always happens at the next observed open, at a quantity
  already fixed from that mark, with both legs' fees paid on the actual fill prices.
* Calendar exits: ``CALENDAR_EXIT`` is an ACTION code, never a financial forecast.  It is
  identified as an action, passed through WITHOUT the ``1/F`` rescale (rescaling an action
  code into a "dollar" magnitude would fabricate a financial forecast), and kept on its
  negative sign, so it can sell a held claim and can never be a fresh positive.
* Shadow reference: the original virtual fill price is a shadow reference usable only once
  that fill has become PAST (``t > fill_et``); the engine enforces this, and the caller
  reports how many cells were shadow-eligible.
* Missing forecasts: a non-finite forecast aborts the run (it would silently read as HOLD).
  A MISSING PARTITION is never treated as "this claim simply never enters": the caller
  requires every published TEST day/N file to exist and to carry its declared rows, so an
  incomplete OOF corpus can never masquerade as a cash decision.
* Cash for a claim that never traded because it had no eligible forecast is a KNOWN 0, not
  an UNKNOWN: no order was attempted, so there is nothing unresolved.  Any attempted
  order with a bad price, a bad ET, an unaffordable cost or a cancel stays UNKNOWN.
* N3 and N5 are never crossed: N3 books read ``scores_n3/`` and N5 books read
  ``scores_n5/``.
* No book date is ever dropped: an absent roster at a clock is recorded as a nonexistent
  book, a book with no shadow-eligible claim is replayed and counted as known cash 0.

Outputs (under --out)
  days/<day>.{daily,members,fills,grid}.parquet   staged per day, resumable
  daily/summary/folds/months/paired/concentration/timing/decision_grid*.parquet
  <evidence>.json

``--limit-days`` (or a non-evidence upstream score set) publishes under ``<out>/smoke`` and
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
ROUND_TRIP_BPS = (25, 50, 100, 150)
SIDES = tuple(bps / 20000.0 for bps in ROUND_TRIP_BPS)
# The two predeclared cycle controls, reported side by side.  Neither is selected on TEST.
CYCLES = ("once", "repeat")
# The producer's published fee factor: cash = GROSS * F.  Read from the score metadata
# when it is published and cross-checked against this literal, never silently assumed.
FEE_SIDE_PUBLISHER = 0.005
FEE_FACTOR_PUBLISHER = (1.0 - FEE_SIDE_PUBLISHER) / (1.0 + FEE_SIDE_PUBLISHER)
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


# A replayed day that executed nothing must still be WRITTEN, or a previous run's fills
# survive on disk and feed this run's aggregation and participation report under views and
# cycle controls that did not produce them.  These are the typed empty frames written in
# that case, so the column set on disk is the column set the reader expects either way.
EMPTY_SCHEMAS = {
    "fills": {
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
        "admission": pl.String,
        "cycles": pl.String,
    },
    "daily": {
        "day": pl.String,
        "clock": pl.Int64,
        "n": pl.Int64,
        "side": pl.Float64,
        "policy": pl.String,
        "ret": pl.Float64,
        "unknown": pl.Boolean,
        "orders": pl.Int64,
        "fees": pl.Float64,
        "gross_pnl": pl.Float64,
        "affordability_unknown": pl.Int64,
        "max_positions": pl.Int64,
        "exit_times": pl.List(pl.Int64),
        "unknown_tickers": pl.List(pl.String),
        "exposure_585": pl.Float64,
        "exposure_630": pl.Float64,
        "exposure_690": pl.Float64,
        "exposure_780": pl.Float64,
        "admission": pl.String,
        "cycles": pl.String,
        "hurdle": pl.Float64,
        "fresh_entries": pl.Int64,
        "claims_with_entry": pl.Int64,
        "roster_claims": pl.Int64,
        "slot_total": pl.Float64,
        "unassigned_at_start": pl.Float64,
        "slots_idle_end": pl.Float64,
        "settled_receipts_total": pl.Float64,
        "claims_shadow_eligible": pl.Int64,
        "unresolved_fresh": pl.Boolean,
        "unresolved_fresh_tickers": pl.List(pl.String),
    },
    "members": {
        "day": pl.String,
        "clock": pl.Int64,
        "n": pl.Int64,
        "side": pl.Float64,
        "policy": pl.String,
        "ticker": pl.String,
        "rank": pl.Int64,
        "net_pnl": pl.Float64,
        "gross_pnl": pl.Float64,
        "fees": pl.Float64,
        "remaining_shares": pl.Float64,
        "reentries": pl.Int64,
        "admission": pl.String,
        "cycles": pl.String,
        "fresh_entries": pl.Int64,
        "settled_receipt": pl.Float64,
        "cash_slot_end": pl.Float64,
    },
}


def pred_cols_all(meta: dict) -> list[str]:
    """Every published ACTION column, whether or not this run replays its view."""
    return [f"pred_{v}" for v in meta["views"]]


def to_engine_action(value, sentinel, fee_factor) -> float:
    """Translate ONE published ACTION cell into what ``core.simulate`` consumes.

    Two kinds of cell arrive here and they are NOT interchangeable:

    * a genuine financial forecast, published in ORIGINAL CLAIM CASH dollars.  The fresh
      hurdle is a GROSS-magnitude comparison, so it is divided by ``fee_factor``
      (= 0.995/1.005).  Because the divisor is positive, the sign a held exit reads is
      unchanged.
    * the CALENDAR_EXIT action code, which is not a dollar magnitude at all.  It is
      forwarded with its EXACT published value: never rescaled, and -- critically -- never
      mapped to ``None`` or zero, because core reads a missing or zero forecast as HOLD.
      Replacing it with missingness silently RETAINS a claim whose only admissible action
      is to exit, deferring it to terminal liquidation at a different price, with
      different exposure and different fees.

    The returned action is therefore scale-invariant in the sentinel: it equals the
    published sentinel bit for bit.  A non-finite forecast is a hard fail, never a
    missingness fallback.
    """
    if value is None or not np.isfinite(float(value)):
        raise SystemExit(
            f"published forecast is non-finite ({value!r}); a non-finite forecast would read "
            "as a silent HOLD and a missing one as a missing ACTION -- refusing"
        )
    if value == sentinel:
        return float(sentinel)
    return float(value) / fee_factor


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
    short_rows = sorted(row_keys - set(ROW_COLS) - {"fill_volume"})
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


def resolve_fee_factor(meta: dict) -> tuple[float, str]:
    """The published cash->GROSS factor, cross-checked against the producer's literal.

    The fresh hurdle is a GROSS-magnitude comparison, so the caller must divide the
    published CASH forecasts by exactly the factor the producer published.  A factor that
    disagrees with the literal is a producer change, and the run refuses rather than
    silently converting with the wrong number.
    """
    literal = FEE_FACTOR_PUBLISHER
    units = str(meta.get("prediction_units", ""))
    hit = re.search(r"0\.995/1\.005|\(1-s\)\(/\(1\+s\)\)", units)
    if hit:
        return literal, "publisher literal 0.995/1.005 confirmed in prediction_units"
    gross_equiv = str(meta.get("gross_unit_equivalent", ""))
    hit = re.search(r"C/([0-9.]+)", gross_equiv)
    if hit:
        # "gross = C/<F>" publishes F itself, the cash-per-gross divisor.
        published = float(hit.group(1))
        if abs(published - literal) > 1e-5:
            raise SystemExit(
                f"published cash/gross factor {published} disagrees with the producer "
                f"literal {literal}; refusing to convert cash forecasts with the wrong factor"
            )
        return literal, (f"publisher gross_unit_equivalent publishes gross = cash/{published:.6f}")
    raise SystemExit(
        "the score set publishes no unit factor (prediction_units/gross_unit_equivalent); "
        "the cash->GROSS conversion cannot be justified, refusing"
    )


def concentration(per_date: np.ndarray, names: list[str]) -> dict:
    """How concentrated a policy's positive DATE mass is. Reported, never optimized."""
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
        description="replay the same roster from pure cash: pay the fresh-entry fee only "
        "where predicted marginal money clears the actual hurdle"
    )
    ap.add_argument("--data-root", default=None)
    ap.add_argument(
        "--scores-root",
        required=True,
        help="owned_claim_minute_value fit --out (frozen full-minute OOF scores)",
    )
    ap.add_argument("--out", required=True)
    ap.add_argument("--evidence", required=True, help="path of the JSON evidence artifact")
    ap.add_argument(
        "--views",
        default=None,
        help="comma-separated score views to replay (default: every published view)",
    )
    ap.add_argument(
        "--cycles",
        default=",".join(CYCLES),
        help="comma-separated cycle controls (default: both, reported side by side)",
    )
    ap.add_argument(
        "--limit-days",
        type=int,
        default=0,
        help="tiny non-evidence run over the first N published test days",
    )
    a = ap.parse_args(argv)

    scores_root = Path(a.scores_root)
    meta_path = scores_root / "metadata.json"
    smoke_meta = scores_root / "smoke" / "metadata.json"
    notes: list[str] = []
    if a.limit_days and smoke_meta.exists():
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
    if a.limit_days:
        evidence = False
        notes.append(f"non-evidence replay over the first {int(a.limit_days)} published test days")
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
    cycles = [c.strip() for c in str(a.cycles).split(",") if c.strip()]
    bad_cycles = [c for c in cycles if c not in CYCLES]
    if bad_cycles or not cycles:
        raise SystemExit(f"--cycles must be a subset of {list(CYCLES)}; rejected {bad_cycles}")

    fee_factor, fee_source = resolve_fee_factor(meta)
    sentinel = meta.get("calendar_exit_sentinel")
    if sentinel is None:
        raise SystemExit("the score set publishes no calendar_exit_sentinel; refusing")
    policies = [f"{v}:cash" for v in views]
    # The cash0 reference is the SAME book with the forecast withheld: no claim can clear
    # a gate it never saw, so every book must earn exactly 0.  It is a ledger identity
    # check, not a policy claim.
    cash0_policies = ["cash0:cash"]

    days = sorted({d for w in meta["folds"] for d in w["test_days"]})
    all_days = list(days)
    if a.limit_days:
        days = days[: int(a.limit_days)]
    disc = set(ls.discovery_days(ls.bps.resolve_data_root(a.data_root)))
    if not set(all_days) <= disc:
        raise SystemExit("published score days are not all discovery days; refusing")
    engine_contract = assert_engine_columns()

    fold_of = {day: w["fold"] for w in meta["folds"] for day in w["test_days"]}
    evidence_path = Path(a.evidence)
    if not evidence and evidence_path.stem == "evidence":
        evidence_path = evidence_path.with_name(evidence_path.stem + ".non_evidence")
    pin = sha256_file(SCRIPT)
    core_pin = sha256_file(CORE_SRC)
    # ---- PUBLISHED PARTITION MANIFEST -------------------------------------------
    # Hashing the files on disk proves only that they exist.  What makes an empty book
    # "known cash" rather than "a score that was never published" is the PRODUCER's own
    # declaration: its rows, its sha256 and its empty_typed_partition flag.  So every
    # (day, N) actually read here is checked against that declaration before any of it
    # can become an observation.
    written = meta.get("test_days_written")
    if not isinstance(written, dict) or not written:
        raise SystemExit(
            "the score set publishes no test_days_written manifest; a partition cannot "
            "be distinguished from a missing one, refusing"
        )
    manifest: dict[tuple[str, int], dict] = {}
    for n in NS:
        rows = written.get(f"n{n}")
        if not isinstance(rows, list) or not rows:
            raise SystemExit(f"the score set publishes no n{n} partition manifest; refusing")
        for entry in rows:
            manifest[(entry["day"], n)] = entry
    undeclared = [(day, n) for day in days for n in NS if (day, n) not in manifest]
    if undeclared:
        raise SystemExit(
            f"{len(undeclared)} replayed day/N partitions are not declared by the "
            f"published manifest (first: {undeclared[:6]}); refusing"
        )
    score_digest = {day: {} for day in days}
    partition_audit: dict[str, dict] = {}
    for day in days:
        for n in NS:
            path = scores_root / f"scores_n{n}" / f"{day}.parquet"
            entry = manifest[(day, n)]
            if not path.exists():
                raise SystemExit(
                    f"{path} is declared in the published manifest but absent on disk; a "
                    "missing partition is never a claim that simply never entered"
                )
            digest = sha256_file(path)
            if digest != entry.get("sha256"):
                raise SystemExit(
                    f"{path} sha256 {digest} does not match the published "
                    f"{entry.get('sha256')}; the OOF corpus on disk is not the published one"
                )
            height = pl.read_parquet(path, columns=["clock"]).height
            if height != int(entry.get("rows", -1)):
                raise SystemExit(
                    f"{path} holds {height} rows but the manifest declares "
                    f"{entry.get('rows')}; refusing"
                )
            if height == 0 and not entry.get("empty_typed_partition"):
                raise SystemExit(
                    f"{path} is empty but the manifest does NOT declare it a legitimate "
                    "empty partition; an undeclared empty partition is an incomplete OOF "
                    "corpus, not a known-cash book"
                )
            score_digest[day][f"n{n}"] = digest
            partition_audit[f"{day}|n{n}"] = {
                "rows": height,
                "sha256_matches_manifest": True,
                "empty_typed_partition": bool(entry.get("empty_typed_partition")),
            }
    data_root = ls.bps.resolve_data_root(a.data_root)
    v2 = ls.v2_dir(data_root)
    # TRANSFORMATION PROVENANCE: the immutable split and the v2 manifest decide which days
    # exist and how the frozen panel/roster are canonicalised.  They are pinned once.
    provenance = {}
    for name in ("split.json", "manifest.json"):
        path = v2 / name
        provenance[name] = sha256_file(path) if path.exists() else None

    # EXECUTION INPUTS: the forecasts are frozen, but the ACTUAL opens, volumes, causal
    # marks and shadow reference prices come from the per-day panel/roster bytes.  A resume
    # that only re-hashes the score corpus would happily reuse a stale ledger after an
    # executable open or roster price was corrected under identical keys, and would then
    # publish those stale cashflows under the CURRENT data_root name.  So each day's real
    # consumed bytes are hashed BEFORE the resume decision is taken.
    def execution_pin(day: str) -> dict:
        out = {}
        for kind in ("panel", "roster"):
            path = v2 / kind / f"{day}.parquet"
            if not path.exists():
                raise SystemExit(
                    f"{path} is missing; the execution inputs cannot be pinned, so no day "
                    "may be resumed or replayed"
                )
            out[kind] = sha256_file(path)
        marker = v2 / "_done" / f"{day}.json"
        out["done_marker"] = sha256_file(marker) if marker.exists() else None
        return out

    exec_pins = {day: execution_pin(day) for day in days}
    base_pin = hashlib.sha256(
        (
            pin
            + core_pin
            + sha256_file(meta_path)
            + json.dumps(score_digest, sort_keys=True)
            + json.dumps({"views": views, "cycles": cycles}, sort_keys=True)
            + json.dumps(provenance, sort_keys=True)
        ).encode()
    ).hexdigest()
    resume_pins = {
        day: hashlib.sha256(
            (base_pin + json.dumps(exec_pins[day], sort_keys=True)).encode()
        ).hexdigest()
        for day in days
    }

    days_dir = out / "days"
    done_dir = out / "_done"
    days_dir.mkdir(parents=True, exist_ok=True)
    done_dir.mkdir(parents=True, exist_ok=True)
    grid_rows: list[dict] = []

    for i, day in enumerate(days):
        marker = done_dir / f"{day}.json"
        paths = {k: days_dir / f"{day}.{k}.parquet" for k in ("daily", "members", "fills")}
        grid_path = days_dir / f"{day}.grid.parquet"
        if marker.exists() and paths["daily"].exists() and paths["members"].exists():
            info = json.loads(marker.read_text())
            if (
                info.get("resume_pin") == resume_pins[day]
                and info.get("execution_pin") == exec_pins[day]
            ):
                if grid_path.exists():
                    grid_rows.extend(pl.read_parquet(grid_path).to_dicts())
                if i % 25 == 0:
                    print(f"[cash-first] {i + 1}/{len(days)} {day} (resumed)", flush=True)
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
            frame = pl.read_parquet(
                path,
                columns=[
                    "clock",
                    "rank",
                    "ticker",
                    "t",
                    "session_end",
                    "fill_et",
                    *pred_cols_all(meta),
                ],
            )
            # Only keys and predictions may cross into the engine: no label, status or
            # outcome is read from disk, so none can ever reach an action.
            allowed = {"clock", "rank", "ticker", "t", "session_end", "fill_et"}
            stray = [
                c for c in frame.columns if c not in allowed and c not in set(pred_cols_all(meta))
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
                # No roster at this admission clock: the book does not exist and owns no
                # cash.  Recorded explicitly so the day is not silently short a book.
                for n in NS:
                    day_grid.append(
                        {
                            "day": day,
                            "clock": clock,
                            "n": n,
                            "score_rows": 0,
                            "shadow_eligible_cells": 0,
                            "shadow_eligible_coverage": None,
                            "calendar_action_cells": 0,
                            "positive_forecast_cells": 0,
                            "duplicate_score_keys": 0,
                            # A nonexistent book is neither a known-cash book nor a scored
                            # one; False keeps it out of the known-cash count without
                            # claiming the producer published anything for it.
                            "known_cash_clock": False,
                            "partition_rows_whole_file": int(manifest[(day, n)]["rows"]),
                            "partition_empty_declared_whole_file": bool(
                                manifest[(day, n)].get("empty_typed_partition")
                            ),
                            "note": "no roster at this admission clock: book does not exist",
                        }
                    )
                continue
            rows_clock = panel.filter(pl.col("clock") == clock).select(list(ROW_COLS)).to_dicts()
            for n in NS:
                score = score_frames[n].filter(pl.col("clock") == clock)
                roster = roster_clock.filter(pl.col("rank") <= n).select(list(ROSTER_COLS))
                roster = roster.to_dicts()
                if not roster:
                    raise SystemExit(
                        f"{day} clock {clock} n{n}: empty rank<=n roster; refusing to "
                        "silently lose the book date"
                    )
                roster_tickers = {r["ticker"] for r in roster}
                fill_et_of = {
                    r["ticker"]: int(r["fill_et"])
                    for r in roster
                    if _finite(r["fill_et"]) and _finite(r["fill_px"])
                }
                # FULL past-shadow coverage, measured from the SOURCE panel rather than
                # from the score file: for every roster claim whose virtual fill exists,
                # every panel minute after that fill is a cell in which the shadow
                # reference is readable.  The published score file must carry EXACTLY
                # those keys -- no missing cell (an incomplete OOF corpus) and no
                # duplicate key (an ambiguous forecast).  A partially covered book is
                # never read as a claim that simply had nothing to forecast.
                eligible = {
                    (r["ticker"], int(r["t"]))
                    for r in rows_clock
                    if r["ticker"] in fill_et_of and r["t"] > fill_et_of[r["ticker"]]
                }
                owned_cells = len(eligible)
                score_keys = score.select(["ticker", "t"]).to_dicts() if score.height else []
                seen = set()
                duplicates = 0
                for row in score_keys:
                    key = (row["ticker"], int(row["t"]))
                    if key in seen:
                        duplicates += 1
                    seen.add(key)
                if duplicates:
                    raise SystemExit(
                        f"{day} clock {clock} n{n}: {duplicates} duplicated (ticker, minute) "
                        "score keys; an ambiguous forecast is never resolved by picking one"
                    )
                if owned_cells and not (eligible <= seen):
                    raise SystemExit(
                        f"{day} clock {clock} n{n}: {len(eligible - seen)} of {owned_cells} "
                        "past-shadow-eligible cells carry no published forecast; the OOF "
                        "corpus is incomplete for this book"
                    )
                shadow_eligible = len(eligible & seen)
                if owned_cells == 0 and seen:
                    raise SystemExit(
                        f"{day} clock {clock} n{n}: {len(seen)} score rows exist but no roster "
                        "claim has a past shadow reference; they can never become decisions"
                    )
                in_roster_rows = score.filter(pl.col("ticker").is_in(list(roster_tickers)))
                action_cells = 0
                positive_cells = 0
                predictions = {}
                if in_roster_rows.height:
                    for view in views:
                        col = f"pred_{view}"
                        series = in_roster_rows[col]
                        n_actions = int((series == sentinel).sum())
                        n_positive = int(((series > sentinel) & (series > 0)).sum())
                        action_cells += n_actions
                        positive_cells += n_positive
                        col_rows = in_roster_rows.select(["ticker", "t"]).to_dicts()
                        for key, published in zip(col_rows, series.to_list(), strict=True):
                            predictions[(key["ticker"], int(key["t"]), view)] = to_engine_action(
                                published, sentinel, fee_factor
                            )
                # `empty_typed_partition` describes the WHOLE (day, N) file and was already
                # enforced against that whole file in the pre-audit.  It says nothing about
                # ONE admission clock inside it: a partition can be non-empty (flag false)
                # and still carry zero rows for a clock whose claims were all blocked,
                # while another clock in the same file is fully scored.  Deciding this
                # clock's known-cash status from the whole-file flag aborted a legitimate
                # empty book.  The correct clock-level test is the ELIGIBLE SET: a book is
                # known cash exactly when no claim in it has a past shadow reference, and
                # the checks above already hard-fail a non-empty eligible set that carries
                # no forecast, or forecast rows that no eligible claim can ever use.
                known_cash_clock = owned_cells == 0 and not seen
                day_grid.append(
                    {
                        "day": day,
                        "clock": clock,
                        "n": n,
                        "score_rows": int(score.height),
                        "shadow_eligible_cells": int(shadow_eligible),
                        "shadow_eligible_coverage": (
                            shadow_eligible / owned_cells if owned_cells else None
                        ),
                        "duplicate_score_keys": int(duplicates),
                        "known_cash_clock": known_cash_clock,
                        "partition_rows_whole_file": int(manifest[(day, n)]["rows"]),
                        "partition_empty_declared_whole_file": bool(
                            manifest[(day, n)].get("empty_typed_partition")
                        ),
                        "calendar_action_cells": int(action_cells),
                        "positive_forecast_cells": int(positive_cells),
                        "note": ""
                        if not known_cash_clock
                        else "known-cash book: no claim at this clock has a past shadow reference",
                    }
                )
                for side in SIDES:
                    for view in views:
                        view_preds = {
                            (tk, t): {"pred_" + view: v}
                            for (tk, t, vv), v in predictions.items()
                            if vv == view
                        }
                        for cyc in cycles:
                            d, m, f = core.simulate(
                                rows_clock,
                                roster,
                                view_preds,
                                side,
                                n,
                                f"{view}:cash",
                                admission="cash",
                                cycles=cyc,
                            )
                            daily.append(d)
                            members.extend(m)
                            fills.extend(f)
                    # cash0: the same book with every forecast withheld.  No gate can be
                    # cleared by a number that was never published, so each book must earn
                    # exactly 0.  This is the ledger identity check, not a policy claim.
                    for cyc in ("once",):
                        d, m, f = core.simulate(
                            rows_clock,
                            roster,
                            {},
                            side,
                            n,
                            "cash0:cash",
                            admission="cash",
                            cycles=cyc,
                        )
                        daily.append(d)
                        members.extend(m)
                        fills.extend(f)
                del predictions
            del rows_clock
        del panel, roster_all, score_frames
        gc.collect()
        # Every replayed day is written on EVERY run, including a day that executed no
        # orders: skipping an empty frame would leave a previous run's fills on disk, and
        # those stale legs would then feed the aggregation and the participation report
        # under views/cycles that did not produce them.
        for key, values in (("daily", daily), ("members", members), ("fills", fills)):
            if values:
                frame = pl.DataFrame(values, infer_schema_length=None)
                if key == "daily":
                    frame = frame.with_columns(
                        pl.col("unknown_tickers").cast(pl.List(pl.String)),
                        pl.col("exit_times").cast(pl.List(pl.Int64)),
                        pl.col("unresolved_fresh_tickers").cast(pl.List(pl.String)),
                    )
            else:
                frame = pl.DataFrame(schema=EMPTY_SCHEMAS[key])
            tmp = paths[key].with_suffix(".tmp.parquet")
            frame.write_parquet(tmp)
            tmp.replace(paths[key])
        if day_grid:
            tmp = grid_path.with_suffix(".tmp.parquet")
            pl.DataFrame(day_grid).write_parquet(tmp)
            tmp.replace(grid_path)
            grid_rows.extend(day_grid)
        marker.write_text(
            json.dumps(
                {
                    "status": "ok",
                    "resume_pin": resume_pins[day],
                    "execution_pin": exec_pins[day],
                    "daily_rows": len(daily),
                    "members": len(members),
                    "fills": len(fills),
                }
            )
            + "\n"
        )
        if i % 25 == 0:
            print(
                f"[cash-first] {i + 1}/{len(days)} {day} daily={len(daily)} fills={len(fills)}",
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
    # Every replayed day now has a fills file (typed-empty when nothing executed), so the
    # aggregate is rebuilt from THIS run alone.  A prior run's aggregate is never left in
    # place and no foreign leg can enter current evidence.
    fill_frames = [pl.read_parquet(days_dir / f"{d}.fills.parquet") for d in days]
    if fill_frames:
        fills_all = pl.concat(fill_frames, how="diagonal_relaxed")
    else:
        fills_all = pl.DataFrame(schema=EMPTY_SCHEMAS["fills"])
    fills_all.write_parquet(out / "fills.parquet")
    grid = pl.concat(
        [pl.read_parquet(days_dir / f"{d}.grid.parquet") for d in days],
        how="diagonal_relaxed",
    )
    grid.write_parquet(out / "decision_grid.parquet")

    group_cols = ["clock", "n", "side", "policy", "cycles"]

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
            pl.col("fresh_entries").mean().alias("mean_fresh_entries"),
            pl.col("claims_with_entry").mean().alias("mean_claims_with_entry"),
            pl.col("unassigned_at_start").mean().alias("mean_unassigned_at_start"),
            pl.col("slots_idle_end").mean().alias("mean_slots_idle_end"),
            pl.col("roster_claims").min().alias("min_roster_claims"),
        )

    summary = agg(daily, group_cols)
    summary.write_parquet(out / "summary.parquet")
    fold_summary = agg(daily.drop_nulls("fold"), ["fold", *group_cols])
    fold_summary.write_parquet(out / "folds.parquet")
    month_summary = agg(daily, ["month", *group_cols])
    month_summary.write_parquet(out / "months.parquet")

    # Paired supports: each cash policy against the SAME book's cash0 reference, and each
    # cycle control against the other on the identical books.  Jointly known days only.
    paired: list[dict] = []
    cash0 = daily.filter(pl.col("policy") == "cash0:cash").select(
        ["day", "clock", "n", "side", pl.col("ret").alias("_ref")]
    )
    cmp0 = (
        daily.filter(pl.col("policy") != "cash0:cash")
        .join(cash0, on=["day", "clock", "n", "side"])
        .filter(pl.col("ret").is_not_null() & pl.col("_ref").is_not_null())
    )
    if cmp0.height:
        for row in (
            cmp0.group_by(group_cols)
            .agg(
                pl.len().alias("paired_days"),
                (pl.col("ret") - pl.col("_ref")).mean().alias("mean_delta"),
                ((pl.col("ret") - pl.col("_ref")).std() / pl.len().sqrt()).alias("delta_se"),
                ((pl.col("ret") - pl.col("_ref")) > 0).mean().alias("share_beating_reference"),
            )
            .to_dicts()
        ):
            paired.append(dict(row, reference="cash0:cash"))
    # Pair each ADDITIONAL control actually run against the first one actually run, not
    # against the module's predeclared tuple: a run invoked with --cycles once must not
    # report a comparison against a control it never executed.
    for cyc in cycles[1:]:
        baseline = cycles[0]
        ref = daily.filter(pl.col("cycles") == baseline).select(
            ["day", "clock", "n", "side", "policy", pl.col("ret").alias("_ref")]
        )
        cmpx = (
            daily.filter(pl.col("cycles") == cyc)
            .join(ref, on=["day", "clock", "n", "side", "policy"], suffix="_r")
            .filter(pl.col("ret").is_not_null() & pl.col("_ref").is_not_null())
        )
        if cmpx.height:
            for row in (
                cmpx.group_by(group_cols)
                .agg(
                    pl.len().alias("paired_days"),
                    (pl.col("ret") - pl.col("_ref")).mean().alias("mean_delta"),
                    ((pl.col("ret") - pl.col("_ref")).std() / pl.len().sqrt()).alias("delta_se"),
                    ((pl.col("ret") - pl.col("_ref")) > 0).mean().alias("share_beating_reference"),
                )
                .to_dicts()
            ):
                paired.append(dict(row, reference=f"cycles={baseline}"))
    if not paired:
        raise SystemExit(
            "no paired comparison was possible: the cash books share no jointly known day"
        )
    pl.DataFrame(paired, infer_schema_length=None).write_parquet(out / "paired.parquet")

    per_date = (
        daily.filter(pl.col("ret").is_not_null())
        .group_by(["clock", "n", "side", "policy", "cycles", "day", "month"])
        .agg(
            pl.col("ret").first().alias("book_date_ret"),
            pl.col("unknown").first().alias("unknown_book"),
            pl.len().alias("rows"),
        )
    )
    per_date.write_parquet(out / "per_date.parquet")
    conc: dict[str, dict] = {}
    # These locals are PER-BOOK KEYS, not configuration.  Unpacking them into the same
    # names the run configuration uses (notably `cycles`) silently overwrote the requested
    # cycle list with whichever book happened to be visited last, so the artifact reported
    # one cycle control while the run had executed both.
    for key, part in per_date.group_by(
        ["clock", "n", "side", "policy", "cycles"], maintain_order=True
    ):
        book_clock, book_n, book_side, book_policy, book_cycles = (
            key if isinstance(key, tuple) else (key,)
        )
        names = part["day"].to_list()
        vals = part["book_date_ret"].to_numpy().astype(np.float64)
        entry = concentration(vals, names)
        entry["dates_with_an_unknown_book"] = int(part.filter(pl.col("unknown_book")).height)
        entry["month_positive_mass"] = {}
        label_key = f"clock{book_clock}|n{book_n}|{book_side}|{book_policy}|{book_cycles}"
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
        "concentration is per ACTUAL BOOK per date: keys are (clock, n, side, policy, "
        "cycles, day) and the admission clock is never averaged away. Day and month counts "
        "are dates of that book, not book-rows pooled across clocks."
    )

    timing = (
        daily.filter(pl.col("policy") != "cash0:cash")
        .explode("exit_times", empty_as_null=False)
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
        pl.col("score_rows").mean().alias("mean_score_rows"),
        pl.col("shadow_eligible_cells").mean().alias("mean_shadow_eligible_cells"),
        pl.col("shadow_eligible_coverage").mean().alias("mean_shadow_coverage"),
        pl.col("shadow_eligible_coverage").min().alias("min_shadow_coverage"),
        pl.col("calendar_action_cells").sum().alias("calendar_action_cells"),
        pl.len().filter(pl.col("known_cash_clock")).alias("known_cash_books"),
        pl.col("duplicate_score_keys").sum().alias("duplicate_score_keys"),
    )
    grid_summary.write_parquet(out / "decision_grid_summary.parquet")

    # The cash0 identity: withholding every forecast must leave every book at exactly 0.
    cash0_rows = daily.filter(pl.col("policy") == "cash0:cash")
    cash0_nonzero = int(
        cash0_rows.filter(pl.col("ret").is_not_null() & (pl.col("ret").abs() > 1e-12)).height
    )
    cash0_orders = int(cash0_rows["orders"].sum() or 0)
    if cash0_nonzero or cash0_orders:
        raise SystemExit(
            f"cash0 identity failed: {cash0_nonzero} cash0 books have a nonzero return and "
            f"{cash0_orders} cash0 orders were placed; the cash ledger does not conserve"
        )

    # Rebuilt from THIS run's own aggregate.  The group keys include the cycle control,
    # because once and repeat share every other fill key: without it the first cycle is
    # not attributable to its originating book and the table pools two alternative
    # experiments into one participation figure.
    fills_frame = fills_all
    participation = (
        fills_frame.group_by(["clock", "n", "side_cost", "policy", "cycles"])
        .agg(
            pl.len().alias("fills"),
            pl.col("participation").median().alias("median_participation"),
            pl.col("participation").quantile(0.95).alias("p95_participation"),
            pl.col("participation").max().alias("max_participation"),
            pl.col("gross_fraction").sum().alias("gross_deployed"),
            pl.col("fee_fraction").sum().alias("fees_paid"),
        )
        .to_dicts()
        if fills_frame.height and "participation" in fills_frame.columns
        else []
    )

    artifact = {
        "kind": "DISCOVERY-CASH-FIRST-OWNED-CLAIM-PORTFOLIO-PROXY-NOT-VALIDATED-EDGE",
        "evidence": evidence,
        "non_evidence_notes": notes,
        "source_sha256": pin,
        "engine": "factory/scripts/owned_claim_replay.simulate (admission='cash' keyword)",
        "engine_sha256": core_pin,
        "engine_projection_contract": engine_contract,
        "engine_projection_note": (
            "the panel/roster are projected to the columns the unchanged engine reads; "
            "assert_engine_columns() refuses the run if the engine ever reads another"
        ),
        "admission_api": {
            "signature": (
                "simulate(rows, roster, predictions, side, n, policy, notional=10000.0, *, "
                "admission='upfront', cycles='once')"
            ),
            "upfront_is_default": True,
            "upfront_parity": (
                "admission='upfront' reproduces the historical engine exactly: same initial "
                "orders, same 1/n reservation, same allocate/reentry branches, same return "
                "frames with no added columns. MODES is untouched, so the old benchmark's "
                "176 cells are unaffected."
            ),
            "cash_reached_only_by": "<view>:cash with admission='cash'",
            "modes_unchanged": list(core.MODES),
        },
        "hypothesis": (
            "start cash on the SAME original roster and pay the fresh entry fee only where "
            "predicted marginal money clears the ACTUAL round-trip hurdle. Entry is SUNK "
            "once held, so the held threshold is the forecast's sign while the fresh "
            "threshold is the derived fee hurdle. Distinct from the tested forced-upfront "
            "8-event/EOD-PI admission and from a static first+5 reserve: the same minute "
            "grid decides both entry and exit."
        ),
        "fee_contract": {
            "per_leg_side_costs": list(SIDES),
            "total_round_trip_bps": list(ROUND_TRIP_BPS),
            "split": "half of the declared round-trip friction on each actual leg",
            "both_legs_paid": True,
            "hurdle_formula": "h = 2*s/(1-s), the round-trip fee on one account dollar",
            "hurdle_values": {str(s): 2 * s / (1 - s) for s in SIDES},
            "hurdle_is_derived_not_tuned": True,
            "cost_model": "modeled 25/50/100/150bps total round-trip, equally split between "
            "actual legs; not quote/queue certified",
        },
        "unit_contract": {
            "prediction_units": meta.get("prediction_units"),
            "gross_unit_equivalent": meta.get("gross_unit_equivalent"),
            "fee_factor_cash_over_gross": fee_factor,
            "fee_factor_source": fee_source,
            "conversion": "gross = cash / F before the fresh hurdle; F is a POSITIVE "
            "divisor, so the held-exit sign is unchanged and no forecast is otherwise "
            "rescaled",
        },
        "forecast_price_assumption": {
            "hurdle_mark": (
                "the marginal-money hurdle is evaluated at the COMPLETED CAUSAL MARK of the "
                "decision minute (the last observed close), not at the next open, because "
                "the next open is not known when the decision is taken. This is a declared "
                "approximation of the expected return, not an execution price."
            ),
            "actual_fill": (
                "the ACTUAL fill always happens at the next observed open, at a quantity "
                "already fixed from the causal mark (90% fixed execution headroom), with "
                "both legs' fees charged on the actual fill prices."
            ),
            "unknown_next_open": (
                "a decision whose next open is missing or non-finite is an UNKNOWN attempt, "
                "never a resized order and never a silent skip."
            ),
        },
        "calendar_action_contract": {
            "sentinel": sentinel,
            "meaning": meta.get("calendar_exit_sentinel_meaning"),
            "converter": "owned_claim_cash_first_replay.to_engine_action(value, sentinel, "
            "fee_factor)",
            "handling": (
                "CALENDAR_EXIT is identified as an ACTION code and forwarded with its EXACT "
                "published value: no cash/F rescale, and never mapped to None or zero. "
                "Mapping it to a missing forecast is a real defect, because core reads a "
                "missing or zero forecast as HOLD: the action would be silently dropped, "
                "the claim would stay held past the point where exit-now is the only "
                "admissible action, and it would be liquidated at the terminal price "
                "instead, with different exposure and different fees. Its negative sign "
                "drives the held exit and, being far below the hurdle, it can never be a "
                "fresh positive."
            ),
            "action_cells_total": int(grid["calendar_action_cells"].sum()),
            "non_finite_forecast": "hard fail in to_engine_action; never a missingness fallback",
        },
        "shadow_reference_contract": {
            "rule": (
                "the original virtual fill price may be read for a fresh entry only once "
                "that fill has become PAST (t > fill_et); the engine refuses otherwise."
            ),
            "shadow_eligible_cells": int(grid["shadow_eligible_cells"].sum()),
            "mean_shadow_coverage": grid_summary["mean_shadow_coverage"].to_list(),
        },
        "cash_ledger_contract": {
            "initial_admission": "none: no initial buy, no reservation, no entry fee",
            "per_claim_slot": "each original claim owns its own 1/n cash slot from t=0",
            "slot_sum": "len(roster)/n <= 1; any remainder stays UNASSIGNED global cash",
            "reported": "daily.slot_total = len(roster)/n at t=0, "
            "daily.unassigned_at_start = 1 - slot_total, and daily.slots_idle_end is what "
            "remains undeployed in the per-claim slots when the book closes",
            "borrowing": "never: a fresh buy spends only its own claim's settled slot",
            "proceeds_source": (
                "a sale's ACTUAL net receipt returns to ITS OWN slot at execution minute +1, "
                "never a lifetime-profit figure; not broker business-day settlement"
            ),
            "unaffordable_gap": "UNKNOWN; the order is never resized and never levered",
            "terminal_unresolved_fresh_buy": (
                "audited explicitly. A fresh BUY with a FINITE price and FINITE exec_et that "
                "cannot execute before the session closes is a real funding attempt with an "
                "unresolved outcome: the affected tickers are captured BEFORE the pending "
                "orders are discarded and joined to attempted_unknown, so the book is "
                "UNKNOWN (ret None), daily.unknown_tickers names them and their member "
                "net_pnl is None rather than a laundered known 0. Claims that never traded "
                "are untouched and stay known."
            ),
            "never_traded": (
                "a claim that never traded because no forecast was eligible is KNOWN cash 0; "
                "a claim with a MISSING score partition is never reported as that"
            ),
            "missing_partition_contract": (
                "every published TEST day/N score file is checked against the PRODUCER's own "
                "test_days_written manifest before any of it can become an observation: the "
                "file must exist, its sha256 must equal the published sha256, and its row "
                "count must equal the published rows. Hashing the file on disk alone would "
                "only prove it exists; it is the manifest that makes an empty book 'known "
                "cash' rather than 'a score that was never published'."
            ),
            "empty_partition_rule": (
                "TWO SEPARATE SCOPES, which must not be confused. (1) WHOLE (day, N) file: "
                "empty_typed_partition describes the entire partition, so a zero-row file "
                "counts as a legitimate known-cash book ONLY when the manifest sets that "
                "flag; an undeclared empty partition is an incomplete OOF corpus and "
                "aborts. (2) ONE ADMISSION CLOCK inside a NON-empty partition: the flag says "
                "nothing about it, because a file can be fully scored for clock 560 while "
                "clock 540's claims were all blocked. Clock-level known-cash status is "
                "decided by the ELIGIBLE SET, not by the whole-file flag: a book is known "
                "cash exactly when no claim in it has a past shadow reference "
                "(known_cash_clock). Deciding it from the flag would abort a legitimate "
                "empty book while a sibling clock in the same file is scored."
            ),
            "source_coverage_rule": (
                "past-shadow coverage is measured from the SOURCE panel, not from the score "
                "file: for every roster claim whose virtual fill exists, every minute after "
                "that fill is a cell where the shadow reference is readable. The score "
                "file must carry EXACTLY those (ticker, minute) keys -- a missing eligible "
                "cell is an incomplete corpus and a duplicated key is an ambiguous "
                "forecast; neither is ever resolved by picking or by defaulting to hold."
            ),
            "partition_audit_rows": len(partition_audit),
            "partition_audit_duplicates": int(grid["duplicate_score_keys"].sum()),
            "source_coverage_min": float(grid["shadow_eligible_coverage"].drop_nulls().min())
            if grid["shadow_eligible_coverage"].drop_nulls().len()
            else None,
        },
        "cycle_controls": {
            "controls": list(CYCLES),
            "counts": "actual executed buys/sales, never attempted orders",
            "once": "at most one fresh cycle per claim, irreversible after the first executed buy",
            "repeat": "a claim may re-enter after its sale has actually settled, and the "
            "re-entry must clear the same hurdle",
            "first_fresh_buy_is": "an ENTRY, not a re-entry (reported as fresh_entries)",
        },
        "test_discipline": (
            "predictions are the only learned action input; no roster status, label_status "
            "or TEST outcome gated any entry or exit; the two cycle controls are reported "
            "side by side rather than selected"
        ),
        "no_roster_change": (
            "the roster, the clocks, N3/N5 isolation, the minute grid and the frozen OOF "
            "scores are exactly the upstream ones; no outsider was admitted"
        ),
        "n_isolation": (
            "N3 books are replayed exclusively from scores_n3 and N5 books exclusively from "
            "scores_n5; no view trained on one roster size is applied to the other"
        ),
        "horizon_views_run": views,
        "horizon_assumptions": {
            "opportunistic_max": (
                "pred_max takes a max over the calendar-feasible horizons and therefore buys "
                "an optimism premium: it assumes SOME horizon is worth holding to. That is "
                "an assumption, not a claim of optimality."
            ),
            "all_horizon_views_shown": (
                "every published fixed-horizon view is shipped beside pred_max, so the "
                "comparison without the max-selection premium is always present."
            ),
            "no_claim_optimality": "no view, cycle control or clock is declared optimal",
        },
        "scores_root": str(scores_root),
        "scores_metadata": str(meta_path),
        "scores_metadata_sha256": sha256_file(meta_path),
        "scores_run_id": meta.get("run_id"),
        "score_day_sha256": score_digest,
        "execution_input_sha256": exec_pins,
        "transform_provenance_sha256": provenance,
        "resume_identity": (
            "each day is resumed only when its marker carries BOTH the global resume pin "
            "(script + core + score metadata + per-day/N score sha + views/cycles) and that "
            "day's own execution pin (the consumed panel and roster parquet bytes and its "
            "_done marker). Pinning the forecast corpus alone would let a corrected "
            "executable open, volume, causal mark or roster shadow price -- under identical "
            "keys -- reuse a stale cashflow or affordability decision and then publish it "
            "under the current data_root name."
        ),
        "data_root": str(data_root),
        "test_days": days,
        "views_run": views,
        "policies": policies,
        "cash0_reference": cash0_policies,
        "cycles_run": cycles,
        "requested_controls": {
            "views_requested": list(views),
            "cycles_requested": list(cycles),
            "note": (
                "these are the controls REQUESTED on the command line and the ones "
                "executed. Per-book keys unpacked out of an aggregation are deliberately "
                "named book_* so they can never overwrite this configuration. Both cycle "
                "controls are run and reported side by side; neither is selected. The "
                "requested pair is folded into every per-day resume pin, so a marker "
                "written under a different --views/--cycles combination is never resumed "
                "as if it were this run."
            ),
            "cycle_cells_executed": (
                int(daily.filter(pl.col("policy") != "cash0:cash")["cycles"].n_unique())
                if daily.height
                else 0
            ),
            "cycles_present_in_books": sorted(
                daily.filter(pl.col("policy") != "cash0:cash")["cycles"].unique().to_list()
            )
            if daily.height
            else [],
            "cash0_reference_cycles": "once",
            "cash0_reference_note": (
                "the cash0 control runs a single cycles value because it executes no "
                "orders at all, so a cycle bound cannot bind; it is the ledger identity "
                "reference, not a second experiment"
            ),
        },
        "clocks": list(CLOCKS),
        "ns": list(NS),
        "sides": list(SIDES),
        "cash0_identity": {
            "books": int(cash0_rows.height),
            "books_with_nonzero_return": cash0_nonzero,
            "orders_placed": cash0_orders,
            "meaning": "withholding every forecast leaves every book at exactly 0",
        },
        "decision_cadence": grid_summary.to_dicts(),
        "summary": summary.to_dicts(),
        "folds": fold_summary.to_dicts(),
        "months": month_summary.to_dicts(),
        "paired": paired,
        "concentration": conc,
        "timing": timing.to_dicts(),
        "participation_proxy": {
            "rows": participation,
            "meaning": (
                "filled order size against that minute's traded volume -- a PARTICIPATION "
                "PROXY ONLY. It is explicitly NOT an execution certificate and not "
                "quote/queue validated."
            ),
        },
        "caveats": [
            "cash-first changes the ALLOCATION only: same roster, same frozen OOF minute "
            "forecasts, same grids, same fees on both legs. It is not a lower cost "
            "assumption and not a static reserve.",
            "the fresh hurdle is evaluated at the completed causal mark while actual fills "
            "happen at the next open; that price approximation is declared, not optimized.",
            "per-claim slots sum to len(roster)/n <= 1; the remainder stays unassigned cash "
            "and is never borrowed, so a book is not allowed to concentrate in one name.",
            "UNKNOWN cashflows are counted and excluded; paired comparisons use jointly "
            "known days only; no protected-half read.",
            "pred_max carries an optimism premium over noisy forecasts; the per-horizon "
            "views are shipped beside it and no view is claimed optimal.",
            "participation is a size-vs-volume proxy, not an execution certificate.",
        ],
    }
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(artifact, indent=2, allow_nan=False, default=str) + "\n")
    print(f"[cash-first] evidence artifact -> {evidence_path}", flush=True)
    print(
        summary.filter((pl.col("side") == SIDES[0]) & (pl.col("n") == NS[0])).sort(
            ["clock", "ev"], descending=[False, True]
        ),
        flush=True,
    )
    print(
        f"[cash-first] complete days={len(days)} policies={len(policies)} "
        f"cycles={cycles} evidence={evidence} -> {out}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
