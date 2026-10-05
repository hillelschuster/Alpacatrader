#!/usr/bin/env python3
"""Lane verification sweep for PRE-REG-ENTRY-EV-01 (discovery block only).

For every discovery month 2021-02..2023-03 this script proves, per month, that the board's
recorded provenance and the board's price column both come from `data/ohlcv_<month>.parquet`:

  1. the month lane file's sha256, computed once (streamed, cached in memory);
  2. the board's per-day `source_sha256` re-derived through the PRODUCTION
     `combined_lane_sha(plan)` of `basket_tape_atlas_observation` (the lane digest covers
     {day, sorted [[kind, role, path, file_sha]], alias_map_sha256}; json sort_keys, compact
     separators) on two board days per month -- first and mid;
  3. the board's `px` / `px_et` re-derived empirically on one board day per month from
     ~15k sampled board rows: px = close of the last completed bar with ET minute <= t-1,
     where the ET minute-of-day is hour*60+minute with each part CAST to Int32 (an uncast
     Int64 product overflows Int32 on afternoon minutes).

Board = data/atlas/observation/v0/race.minute_full/month=YYYY-MM/<day>.parquet.

HARD BOUNDARY: only board days inside the discovery window 2021-02-01..2023-03-14 are ever
opened. Sample-day selection is filtered to that window BEFORE any file is read, and
`basket_tape_atlas_observation.guard` (which refuses sealed 2024-*/2025-01 and reserved
2026-06..08) is applied to every day this script touches. No protected/sealed/reserved byte
is read by this script or by the module it imports.

Usage:  python factory/scripts/entry_ev/verify_lane_months.py [--out PATH] [--hash-workers N]
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory.scripts import basket_tape_atlas_observation as T  # noqa: E402

# The discovery block of PRE-REG-ENTRY-EV-01 section 3. Nothing outside it may be opened.
DISCOVERY_FIRST_DAY = "2021-02-01"
DISCOVERY_LAST_DAY = "2023-03-14"
DISCOVERY_MONTHS = tuple(
    f"{y}-{m:02d}"
    for y, m in [(2021, mo) for mo in range(2, 13)]
    + [(2022, mo) for mo in range(1, 13)]
    + [(2023, mo) for mo in range(1, 4)]
)  # 2021-02..2023-03 inclusive, 26 months

BOARD_REL = Path("atlas/observation/v0/race.minute_full")
OUT_REL = Path("factory/artifacts/entry_ev/lane_verification.json")

PX_SAMPLE_ROWS = 15_000
PX_SAMPLE_SEED = 7
SHA_DAYS_PER_MONTH = 2  # first + mid board day of the month
PX_TOLERANCE = 1e-6


def lane_file_sha256(path: Path) -> str:
    """Streamed sha256 of a (large) lane file."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 24), b""):
            h.update(chunk)
    return h.hexdigest()


def board_root() -> Path:
    return T.DATA / BOARD_REL


def board_days(month: str) -> list[str]:
    """Board day files of `month` INSIDE the discovery window, sorted.

    Selection happens on file NAMES only; nothing outside the window is ever opened.
    """
    d = board_root() / f"month={month}"
    if not d.is_dir():
        return []
    return sorted(
        p.stem
        for p in d.glob("*.parquet")
        if DISCOVERY_FIRST_DAY <= p.stem <= DISCOVERY_LAST_DAY and len(p.stem) == 10
    )


def board_source_sha(day: str) -> str:
    """The board's own per-day combined lane digest (first row)."""
    import pyarrow.parquet as pq

    f = board_root() / f"month={day[:7]}" / f"{day}.parquet"
    return pq.read_table(f, columns=["source_sha256"]).slice(0, 1).to_pydict()["source_sha256"][0]


def lane_plan_with_sha(day: str, file_sha: str) -> dict:
    """`day_lane_plan(day)` with the baseline lane's already-computed file sha attached.

    `day_lane_plan` does NOT hash (its lane carries sha256=None/_pending); hashing is deferred
    to `lane_sha` inside `combined_lane_sha`. Attaching the memoised month sha therefore keeps
    the production digest function in the loop while hashing each month exactly once.
    """
    T.guard(day)  # refuses sealed/reserved days before any path is opened
    plan = T.day_lane_plan(day)
    lanes = plan["lanes"]
    if len(lanes) != 1 or lanes[0]["role"] != "baseline":
        raise ValueError(f"{day}: unexpected lane set {[ln['role'] for ln in lanes]}")
    lanes[0]["sha256"] = file_sha
    lanes[0].pop("_pending", None)
    return plan


def reproduce_board_px(day: str, month: str, session_end: int, sample_rows: int, seed: int) -> dict:
    """Empirically re-derive board px / px_et for `sample_rows` sampled board rows.

    px = close of the last completed bar with ET minute-of-day <= t-1, exactly as
    `build_minute_board` computes it (searchsorted(et, t-1, side='right') - 1) over the
    day+RTH-trimmed, alias-canonicalised lane rows sorted by (ticker, etm).
    """
    import pyarrow.parquet as pq

    board_file = board_root() / f"month={month}" / f"{day}.parquet"
    bt = pl.from_arrow(pq.read_table(board_file, columns=["t", "px", "px_et", "ticker"]))
    n_board_rows = bt.height
    sampled = bt.sample(n=min(sample_rows, bt.height), with_replacement=False, seed=seed)

    tickers = sorted(set(sampled["ticker"].to_list()))
    lane = (
        pl.scan_parquet(T.DATA / f"ohlcv_{month}.parquet")
        .select(["timestamp", "ticker", "close"])
        .with_columns(pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("ts_et"))
        .with_columns(pl.col("ts_et").dt.date().cast(pl.Utf8).alias("dt"))
        .with_columns(
            # each part CAST to Int32 before the product: hour*60+minute overflows Int32 otherwise
            (pl.col("ts_et").dt.hour().cast(pl.Int32) * 60 + pl.col("ts_et").dt.minute().cast(pl.Int32)).alias(
                "etm"
            )
        )
        .filter((pl.col("dt") == day) & (pl.col("etm") >= T.RTH_LO) & (pl.col("etm") <= session_end))
        .collect()
    )
    alias = T.acquisition_alias_map()
    if alias:
        lane = lane.with_columns(
            pl.col("ticker")
            .replace_strict(alias, default=pl.col("ticker"), return_dtype=pl.Utf8)
            .alias("ticker")
        )
    lane = lane.filter(pl.col("ticker").is_in(tickers)).select(["ticker", "etm", "close"])

    grouped = (
        lane.sort(["ticker", "etm"])
        .group_by("ticker", maintain_order=True)
        .agg([pl.col("etm"), pl.col("close")])
        .select(
            pl.col("ticker"),
            pl.col("etm").cast(pl.List(pl.Int64)),
            pl.col("close").cast(pl.List(pl.Float64)),
        )
    )
    per_ticker = {
        name: (np.asarray(et, dtype=np.int64), np.asarray(cl, dtype=np.float64))
        for name, et, cl in grouped.iter_rows()
    }

    ts = sampled["t"].to_numpy().astype(np.int64)
    want_tk = sampled["ticker"].to_numpy()
    want_px = sampled["px"].to_numpy()
    want_et = sampled["px_et"].to_numpy()

    got_px = np.full(ts.shape, np.nan)
    got_et = np.full(ts.shape, np.nan)
    for i in range(ts.shape[0]):
        a = per_ticker.get(want_tk[i])
        if a is None:
            continue
        et, cl = a
        j = np.searchsorted(et, ts[i] - 1, side="right") - 1
        if j >= 0:
            got_px[i] = cl[j]
            got_et[i] = et[j]

    px_both_null = np.isnan(got_px) & np.isnan(want_px)
    px_exact = px_both_null | (got_px == want_px)
    px_tol = px_both_null | (np.abs(got_px - want_px) <= PX_TOLERANCE)
    et_both_null = np.isnan(got_et) & np.isnan(want_et)
    px_et_exact = et_both_null | (got_et == want_et)

    both_known = ~np.isnan(want_px) & ~np.isnan(got_px)
    worst = (
        float(np.max(np.abs(got_px[both_known] - want_px[both_known]))) if both_known.any() else 0.0
    )
    return {
        "n_board_rows": n_board_rows,
        "n_sampled": int(ts.shape[0]),
        "n_sampled_px_null_board": int(np.isnan(want_px).sum()),
        "n_sampled_px_both_null": int(px_both_null.sum()),
        "n_sampled_px_known_both": int(both_known.sum()),
        "n_distinct_tickers": len(tickers),
        "px_exact_match": int(px_exact.sum()),
        "px_within_tol_match": int(px_tol.sum()),
        "px_et_exact_match": int(px_et_exact.sum()),
        "px_exact_rate": float(px_exact.mean()),
        "px_within_tol_rate": float(px_tol.mean()),
        "px_et_match_rate": float(px_et_exact.mean()),
        "max_abs_px_diff_known_both": worst,
    }


def verify_month(month: str, file_sha: str | None) -> dict:
    row: dict = {
        "month": month,
        "lane_kind": None,
        "lane_path": None,
        "lane_feed_era": None,
        "file_sha256": file_sha,
        "board_days_in_discovery_window": None,
        "days_checked": [],
        "sha_match": None,
        "sha_mismatches": [],
        "px_day": None,
        "px_session_end": None,
        "px_exact_rate": None,
        "px_within_1e6_rate": None,
        "px_et_match_rate": None,
        "px_detail": None,
        "status": "pending",
        "notes": "",
    }
    t0 = time.time()

    days = board_days(month)
    row["board_days_in_discovery_window"] = len(days)
    if not days:
        row["status"] = "anomaly"
        row["notes"] = f"no board day parquet inside {DISCOVERY_FIRST_DAY}..{DISCOVERY_LAST_DAY}"
        return row

    day = days[len(days) // 2]  # mid board day of the month
    try:
        _path, kind, era = T.baseline_raw_lane(day)
        plan = lane_plan_with_sha(day, file_sha)
        row["lane_kind"] = plan["kind"]
        row["lane_feed_era"] = plan["feed_era"]
        row["lane_path"] = str(T.baseline_raw_lane(day)[0])
    except Exception as exc:  # noqa: BLE001 -- reported, never smoothed
        row["status"] = "anomaly"
        row["notes"] = f"lane plan failed: {type(exc).__name__}: {exc}"
        return row

    # --- (2) board source_sha256 vs production combined_lane_sha, first + mid board day
    sha_days = [days[0]] if len(days) == 1 else [days[0], day]
    for d in sha_days:
        try:
            p = lane_plan_with_sha(d, file_sha)
            got = T.combined_lane_sha(p)
            want = board_source_sha(d)
            row["days_checked"].append(
                {
                    "day": d,
                    "board_source_sha256": want,
                    "combined_lane_sha256": got,
                    "match": bool(got == want),
                    "alias_map_sha256": p["alias_map_sha256"],
                }
            )
        except Exception as exc:  # noqa: BLE001
            row["days_checked"].append({"day": d, "match": False, "error": f"{type(exc).__name__}: {exc}"})
            row["sha_mismatches"].append(d)
    row["sha_match"] = bool(row["days_checked"]) and all(
        c.get("match") for c in row["days_checked"]
    )

    # --- (3) empirical px / px_et reproduction on the mid board day
    import pyarrow.parquet as pq

    board_file = board_root() / f"month={month}" / f"{day}.parquet"
    se = pl.from_arrow(pq.read_table(board_file, columns=["session_end"]))["session_end"]
    lo_se, hi_se = int(se.min()), int(se.max())
    if lo_se != hi_se:
        raise ValueError(f"{day}: session_end is not constant on the board ({lo_se}..{hi_se})")
    session_end = lo_se
    row["px_day"] = day
    row["px_session_end"] = session_end
    try:
        detail = reproduce_board_px(day, month, session_end, PX_SAMPLE_ROWS, PX_SAMPLE_SEED)
        row["px_detail"] = detail
        row["px_exact_rate"] = detail["px_exact_rate"]
        row["px_within_1e6_rate"] = detail["px_within_tol_rate"]
        row["px_et_match_rate"] = detail["px_et_match_rate"]
    except Exception as exc:  # noqa: BLE001
        row["status"] = "anomaly"
        row["notes"] = f"px reproduction failed on {day}: {type(exc).__name__}: {exc}"
        return row

    row["seconds"] = round(time.time() - t0, 2)
    if not row["sha_match"]:
        row["status"] = "anomaly"
        row["notes"] = "board source_sha256 != combined_lane_sha on: " + ",".join(row["sha_mismatches"])
    elif row["px_exact_rate"] < 1.0 or row["px_et_match_rate"] < 1.0:
        row["status"] = "anomaly"
        row["notes"] = (
            f"px reproduction not exact on {day}: "
            f"px_exact={row['px_exact_rate']:.9f} px_et={row['px_et_match_rate']:.9f}"
        )
    else:
        row["status"] = "ok"
        row["notes"] = (
            f"lane sha + {len(row['days_checked'])} day digests verified; "
            f"px exact on {detail['n_sampled']}/{detail['n_sampled']} sampled rows of {day}"
        )
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ROOT / OUT_REL)
    ap.add_argument("--hash-workers", type=int, default=4)
    ap.add_argument("--months", type=str, default="", help="comma list override (debug only)")
    args = ap.parse_args()

    months = tuple(m.strip() for m in args.months.split(",") if m.strip()) or DISCOVERY_MONTHS

    for m in months:
        if not (DISCOVERY_FIRST_DAY[:7] <= m <= DISCOVERY_LAST_DAY[:7]):
            raise SystemExit(f"REFUSED: {m} is outside the discovery block {months}")

    t_start = time.time()
    print(f"data root   : {T.DATA}")
    print(f"board root  : {board_root()}")
    print(f"alias map   : {T.ACQ_ALIAS_MAP}")
    print(f"alias sha256: {T.alias_map_sha256()}")
    print(f"discovery   : {DISCOVERY_FIRST_DAY}..{DISCOVERY_LAST_DAY} ({len(months)} months)")
    print(f"guard       : sealed={T.sim.SEALED_PREFIXES} reserved={T.sim.RESERVED_MONTHS}")

    # --- (1a) one streamed sha256 per month lane file
    lane_paths = {m: T.DATA / f"ohlcv_{m}.parquet" for m in months}
    t_hash = time.time()
    with cf.ThreadPoolExecutor(max_workers=max(1, args.hash_workers)) as ex:
        futs = {
            m: ex.submit(lane_file_sha256, p) if p.exists() else None for m, p in lane_paths.items()
        }
        file_shas: dict[str, str | None] = {}
        for m in months:
            f = futs[m]
            file_shas[m] = f.result() if f is not None else None
    print(f"hashed {sum(v is not None for v in file_shas.values())}/{len(months)} lane files "
          f"in {time.time() - t_hash:.1f}s")

    rows = []
    for m in months:
        if file_shas[m] is None:
            rows.append(
                {
                    "month": m,
                    "file_sha256": None,
                    "days_checked": [],
                    "sha_match": None,
                    "px_exact_rate": None,
                    "px_et_match_rate": None,
                    "status": "anomaly",
                    "notes": f"lane file missing: {lane_paths[m]}",
                }
            )
            print(f"{m}  ANOMALY  lane file missing")
            continue
        row = verify_month(m, file_shas[m])
        rows.append(row)
        print(
            f"{m}  {row['status'].upper():8s} sha_match={row['sha_match']} "
            f"px_exact={row['px_exact_rate']} px_et={row['px_et_match_rate']} "
            f"({row.get('seconds', 0)}s)"
            + (f"  :: {row['notes']}" if row["status"] != "ok" else "")
        )

    anomalies = [
        {"month": r["month"], "status": r["status"], "notes": r["notes"]}
        for r in rows
        if r["status"] != "ok"
    ]
    ok = [r for r in rows if r["status"] == "ok"]
    doc = {
        "schema": "entry_ev/lane_verification/v0",
        "study": "PRE-REG-ENTRY-EV-01",
        "discovery_block": {"first_day": DISCOVERY_FIRST_DAY, "last_day": DISCOVERY_LAST_DAY},
        "sealed_reserved_untouched": {
            "sealed_prefixes": list(T.sim.SEALED_PREFIXES),
            "reserved_months": list(T.sim.RESERVED_MONTHS),
            "guard": "basket_sim.guard_day applied to every day before any path was opened",
        },
        "board_root": str(board_root()),
        "lane_rule": (
            "baseline_raw_lane: month 2021-02..2023-12 -> data/ohlcv_<month>.parquet "
            "(kind ohlcv_raw, era hf_era)"
        ),
        "alias_map_path": str(T.ACQ_ALIAS_MAP),
        "alias_map_sha256": T.alias_map_sha256(),
        "combined_lane_sha_recipe": (
            "sha256(json.dumps({day, lanes: sorted([[kind, role, str(path), file_sha256], ...]), "
            "alias_map_sha256}, sort_keys=True, separators=(',',':')).encode()); digest function is "
            "the production basket_tape_atlas_observation.combined_lane_sha"
        ),
        "px_rule": (
            "px = close of the last completed lane bar with ET minute-of-day <= t-1 "
            "(searchsorted(et, t-1, 'right')-1); etm = hour*60+minute, each part CAST Int32; "
            "lane rows trimmed to (ET date == day, RTH_LO <= etm <= session_end) and "
            "alias-canonicalised, exactly as build_minute_board"
        ),
        "px_sample_rows_requested": PX_SAMPLE_ROWS,
        "px_sample_seed": PX_SAMPLE_SEED,
        "px_tolerance": PX_TOLERANCE,
        "sha_days_per_month": SHA_DAYS_PER_MONTH,
        "months": rows,
        "summary": {
            "months_checked": len(rows),
            "months_ok": len(ok),
            "months_anomalous": len(rows) - len(ok),
            "months_with_lane_sha_match": sum(1 for r in rows if r["sha_match"]),
            "months_with_px_exact_rate_1": sum(1 for r in rows if r["px_exact_rate"] == 1.0),
            "months_with_px_et_match_rate_1": sum(1 for r in rows if r["px_et_match_rate"] == 1.0),
            "total_board_rows_sampled_for_px": sum(
                (r["px_detail"] or {}).get("n_sampled", 0) for r in rows
            ),
            "total_px_exact_matches": sum((r["px_detail"] or {}).get("px_exact_match", 0) for r in rows),
            "anomalies": anomalies,
        },
        "runtime_seconds": round(time.time() - t_start, 1),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    s = doc["summary"]
    print(
        f"\nSUMMARY months_checked={s['months_checked']} ok={s['months_ok']} "
        f"anomalies={s['months_anomalous']} px_rows={s['total_board_rows_sampled_for_px']} "
        f"px_exact={s['total_px_exact_matches']} -> {out} ({doc['runtime_seconds']}s)"
    )
    return 0 if s["months_anomalous"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
