#!/usr/bin/env python3
"""Independent open-anchored top-gainer research panel; never reads protected outcomes.

Admission: full-PIT SIP top-ten B snapshots, completed-bar gain >=5%, $1 floor,
fresh price. Watch only names already admitted; features use bars stamped <t.
Execution proxy: market entry at minute t open, expires if that minute has no bar.
Fixed-time exits use first observed open at/after actual entry+h, capped at the
session's last minute. No high-touch profit credit. Unresolved exits stay UNKNOWN.
This is a discovery substrate, not a validated strategy or an exchange-fill model.

Outputs deliberately default to Linux storage, not the nearly full data mount.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOTS = (575, 585, 600, 630, 660, 720)
FEATURES = (
    "minute",
    "admission_age",
    "rank_snapshot",
    "admit_gain",
    "gain_open",
    "log_price",
    "ret1",
    "ret3",
    "ret5",
    "ret15",
    "dd_high15",
    "dd_day_high",
    "rebound_low5",
    "range5",
    "range15",
    "log_cum_dv",
    "log_dv5",
    "dv_accel",
    "efficiency15",
    "bars15",
    "since_high",
    "vwap_dist",
)
CONTRACT = {
    "version": 1,
    "admission": "full-PIT SIP B snapshot top10, score>=0.05, px>=1, px_et>=T-2",
    "snapshots": SNAPSHOTS,
    "features": FEATURES,
    "decision": "5-minute clock; OHLCV strictly et<t; last actual bar must be t-1",
    "entry": "t open; no bar at t => expired unfilled signal, cash retained",
    "exit": (
        "first actual open >=min(entry+h,session_end); absent => unknown, "
        "never discarded silently"
    ),
    "costs_bps_round_trip": [100, 150, 200],
    "model_fit": "2021-02..2022-12",
    "model_validation": "2023-01..2023-12",
    "out_of_fit_confirmation": (
        "2025-02..2026-05; previously explored market periods, "
        "NOT pristine holdout"
    ),
    "protected_unread": ["2024", "2025-01", "2026-06", "2026-07", "2026-08"],
    "no_previous_close_features": True,
    "execution_limit": (
        "minute open proxy; any promising candidate needs as-of "
        "side-aware quotes and size review"
    ),
}


def allowed(day: str) -> bool:
    return "2021-02-01" <= day <= "2023-12-31" or "2025-02-01" <= day <= "2026-05-31"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def symbol_rows(
    day: str, ticker: str, frame: pl.DataFrame, admissions: list, ranks: dict, session_end: int
) -> list[dict]:
    et = frame["et"].to_numpy()
    o, h, low, c, v = (frame[x].to_numpy() for x in ("open", "high", "low", "close", "volume"))
    if len(et) == 0 or et[0] != 570:
        return []  # B admission explicitly uses the 09:30 opening anchor.
    if not np.all(np.isfinite(np.column_stack([o, h, low, c, v]))) or np.any(c <= 0):
        raise ValueError(f"nonfinite/nonpositive tape: {day} {ticker}")
    first = min(admissions, key=lambda x: x[0])
    cumdv = np.cumsum(c * v)
    cumvol = np.cumsum(v)
    runhigh = np.maximum.accumulate(h)
    result = []
    for t in range(first[0], min(895, session_end - 1) + 1, 5):
        i = int(np.searchsorted(et, t)) - 1
        if i < 0 or et[i] != t - 1 or c[i] < 1:
            continue
        old = {
            k: max(0, int(np.searchsorted(et, t - 1 - k, side="right")) - 1) for k in (1, 3, 5, 15)
        }
        s5, s15 = int(np.searchsorted(et, t - 5)), int(np.searchsorted(et, t - 15))
        s10 = int(np.searchsorted(et, t - 10))
        if i < s5 or i < s15:
            continue
        # Past-only dollar-volume/shape features; no full-day quality flags.
        dv5 = float(np.sum(c[s5 : i + 1] * v[s5 : i + 1]))
        dvprev5 = float(np.sum(c[s10:s5] * v[s10:s5]))
        diffs = np.diff(c[s15 : i + 1])
        travel = float(np.abs(diffs).sum())
        current = float(c[i])
        max15, min15 = float(h[s15 : i + 1].max()), float(low[s15 : i + 1].min())
        last_peak = int(np.flatnonzero(h[: i + 1] == runhigh[i])[-1])
        checkpoint = max(x for x in SNAPSHOTS if x <= t)
        entry_idx = int(np.searchsorted(et, t))
        filled = entry_idx < len(et) and et[entry_idx] == t
        entry = float(o[entry_idx]) if filled else None
        row = {
            "day": day,
            "ticker": ticker,
            "t": t,
            "admit_t": first[0],
            "entry_et": t if filled else None,
            "entry_open": entry,
            "entry_status": "filled_proxy" if filled else "unfilled_expired",
            "session_end": session_end,
            "minute": float(t - 570),
            "admission_age": float(t - first[0]),
            "rank_snapshot": float(ranks.get(checkpoint, {}).get(ticker, 11)),
            "admit_gain": float(first[1]),
            "gain_open": current / float(o[0]) - 1,
            "log_price": float(np.log(current)),
            **{f"ret{k}": current / float(c[j]) - 1 for k, j in old.items()},
            "dd_high15": current / max15 - 1,
            "dd_day_high": current / float(runhigh[i]) - 1,
            "rebound_low5": current / float(low[s5 : i + 1].min()) - 1,
            "range5": float(h[s5 : i + 1].max() - low[s5 : i + 1].min()) / current,
            "range15": (max15 - min15) / current,
            "log_cum_dv": float(np.log1p(cumdv[i])),
            "log_dv5": float(np.log1p(dv5)),
            "dv_accel": dv5 / max(dvprev5, 1.0),
            "efficiency15": (current - float(c[s15])) / travel if travel > 0 else 0.0,
            "bars15": float(i - s15 + 1),
            "since_high": float(t - int(et[last_peak]) - 1),
            "vwap_dist": current / (float(cumdv[i]) / max(float(cumvol[i]), 1.0)) - 1,
        }
        for horizon in (15, 60, 390):
            key = f"gross_{horizon}"
            target = min(t + horizon, session_end)
            j = int(np.searchsorted(et, target))
            if not filled:
                row[key], row[f"exit_et_{horizon}"] = 0.0, None
                row[f"exit_status_{horizon}"] = "unfilled_cash"
            elif j < len(et) and et[j] <= session_end:
                row[key] = float(o[j]) / entry - 1
                row[f"exit_et_{horizon}"] = int(et[j])
                row[f"exit_status_{horizon}"] = "observed_open_proxy"
            else:
                row[key], row[f"exit_et_{horizon}"] = None, None
                row[f"exit_status_{horizon}"] = "unknown_pending"
        result.append(row)
    return result


def build_day(args: tuple) -> dict:
    day, data_dir, out_dir, session_end, force = args
    if not allowed(day):
        raise ValueError(f"protected/out-of-scope day refused: {day}")
    data, out = Path(data_dir), Path(out_dir)
    dest, manifest = out / "days" / f"{day}.parquet", out / "days" / f"{day}.json"
    if not force and dest.exists() and manifest.exists():
        return {"day": day, "status": "cached"}
    start = time.monotonic()
    cp, bp = (
        data / "sip" / "candidates" / f"{day}.json",
        data / "sip" / "net" / "bars" / f"{day}.parquet",
    )
    candidate = json.loads(cp.read_text())
    admit, ranks = {}, {}
    for snap in candidate["snapshots"]:
        if snap["pop"] != "B" or snap["T"] not in SNAPSHOTS:
            continue
        t = snap["T"]
        ranks[t] = {r["symbol"]: r["rank"] for r in snap["top"]}
        for r in snap["top"]:
            if r["score"] >= 0.05 and r.get(f"px_{t}", 0) >= 1 and r.get(f"px_{t}_et", -1) >= t - 2:
                admit.setdefault(r["symbol"], []).append((t, r["score"]))
    tape = pl.read_parquet(bp).filter(pl.col("ticker").is_in(list(admit))).sort(["ticker", "et"])
    if tape.select(pl.struct("ticker", "et").is_duplicated().any()).item():
        raise ValueError(f"duplicate tape bars: {day}")
    rows, missing = [], sorted(set(admit) - set(tape["ticker"].to_list()))
    for key, f in tape.partition_by("ticker", as_dict=True).items():
        ticker = key[0]
        rows.extend(symbol_rows(day, ticker, f, admit[ticker], ranks, session_end))
    out.joinpath("days").mkdir(parents=True, exist_ok=True)
    panel = (
        pl.DataFrame(rows)
        if rows
        else pl.DataFrame(
            schema={
                "day": pl.String,
                "ticker": pl.String,
                "t": pl.Int64,
                **{f: pl.Float64 for f in FEATURES if f != "t"},
            }
        )
    )
    temp = dest.with_suffix(".parquet.tmp")
    panel.write_parquet(temp)
    temp.replace(dest)
    info = {
        "day": day,
        "status": "built",
        "rows": len(rows),
        "admitted_names": len(admit),
        "missing_admitted_tapes": missing,
        "session_end": session_end,
        "source_candidate_sha256": digest(cp),
        "source_bar_sha256": digest(bp),
        "producer_sha256": digest(Path(__file__)),
        "contract_sha256": digest(out / "contract.json"),
        "runtime_s": round(time.monotonic() - start, 3),
    }
    if rows:
        info["unfilled"] = sum(r["entry_status"] == "unfilled_expired" for r in rows)
        info["unknown_exit_60"] = sum(r["exit_status_60"] == "unknown_pending" for r in rows)
    manifest.write_text(json.dumps(info, indent=2) + "\n")
    return info


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--out", type=Path, default=Path.home() / "alpha-data" / "open-search-v1")
    parser.add_argument("--days", nargs="+")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    contract_path = args.out / "contract.json"
    contract = json.dumps(CONTRACT, indent=2) + "\n"
    if contract_path.exists() and contract_path.read_text() != contract:
        raise ValueError("output root contract mismatch; use a new versioned output root")
    contract_path.write_text(contract)
    calendar_path = (
        ROOT / "factory" / "artifacts" / "basket" / "sip" / "phase2_session_calendar.json"
    )
    calendar = json.loads(calendar_path.read_text())
    days = args.days or sorted(
        p.stem
        for p in (args.data / "sip" / "candidates").glob("????-??-??.json")
        if allowed(p.stem)
    )
    ends = {day: int(calendar["evidence"][day]["session_end"]) for day in days}
    tasks = [(d, str(args.data), str(args.out), ends[d], args.force) for d in days]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for n, info in enumerate(pool.map(build_day, tasks), 1):
            if n % 25 == 0 or args.days:
                print(f"{n}/{len(tasks)} {json.dumps(info)}", flush=True)
    print(f"complete: {len(tasks)} development days -> {args.out}", flush=True)


if __name__ == "__main__":
    main()
