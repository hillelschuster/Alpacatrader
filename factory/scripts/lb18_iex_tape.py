#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["alpaca-py", "pandas", "pyarrow", "python-dotenv"]
# ///
# ─── How to run ───
# uv run --quiet --python 3.11 --with pandas --with pyarrow --with alpaca-py \
#   --with python-dotenv python factory/scripts/lb18_iex_tape.py [--day YYYY-MM-DD] [--max-days N]
# ──────────────────
"""Cache causal Alpaca IEX bars for the frozen leaderboard candidate universe."""

from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Sequence
from datetime import date, datetime, time as dt_time
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from alpaca.common.exceptions import APIError
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from dotenv import load_dotenv
from requests.exceptions import RequestException

ROOT: Final = Path(__file__).resolve().parents[2]
LEADERBOARD: Final = ROOT / "data" / "leaderboard"
OUT: Final = ROOT / "data" / "iex_tape"
MISSING_PATH: Final = OUT / "_missing.jsonl"
NY: Final = ZoneInfo("America/New_York")
MIN_GRID: Final = np.arange(570, 960)
DECISION_MINUTES: Final = np.arange(571, 960)
FIELDS: Final = ("open", "high", "low", "close", "volume")
SCHEMA: Final = ["date", "t", "ticker", "o", "h", "l", "c", "v", "gain_c", "n_bars"]
RETRIES: Final = 5


def empty_tape() -> pd.DataFrame:
    return pd.DataFrame({column: pd.Series(dtype="float64") for column in SCHEMA}).astype(
        {"date": "str", "t": "int64", "ticker": "str", "n_bars": "int64"}
    )


def construct_tape(
    day: str, symbols: Sequence[str], bars: pd.DataFrame
) -> tuple[pd.DataFrame, list[str]]:
    """Build the exact lagged, forward-filled lb18 path representation."""
    present = set(bars["symbol"].unique()) if len(bars) else set()
    missing = [symbol for symbol in symbols if symbol not in present]
    if not present:
        return empty_tape(), missing

    usable = bars[bars["symbol"].isin(present)].drop_duplicates(
        ["symbol", "et"], keep="last"
    )
    wide: dict[str, pd.DataFrame] = {}
    raw: dict[str, pd.DataFrame] = {}
    for field in FIELDS:
        frame = usable.pivot(index="et", columns="symbol", values=field).reindex(MIN_GRID)
        raw[field] = frame
        wide[field] = frame.ffill()

    idx = DECISION_MINUTES - 571
    causal = {field: frame.iloc[idx] for field, frame in wide.items()}
    n_bars = raw["close"].notna().cumsum(axis=0).iloc[idx]
    parts: list[pd.DataFrame] = []
    for symbol in symbols:
        if symbol not in present:
            continue
        valid = causal["close"][symbol].notna().to_numpy()
        part = pd.DataFrame(
            {
                "date": day,
                "t": DECISION_MINUTES[valid],
                "ticker": symbol,
                "o": causal["open"][symbol].to_numpy()[valid],
                "h": causal["high"][symbol].to_numpy()[valid],
                "l": causal["low"][symbol].to_numpy()[valid],
                "c": causal["close"][symbol].to_numpy()[valid],
                "v": causal["volume"][symbol].to_numpy()[valid],
                "gain_c": np.nan,
                "n_bars": n_bars[symbol].to_numpy(dtype=int)[valid],
            }
        )
        parts.append(part)
    return pd.concat(parts, ignore_index=True)[SCHEMA], missing


def fetch_day(
    client: StockHistoricalDataClient, day: date, symbols: Sequence[str]
) -> pd.DataFrame:
    """Fetch one session; alpaca-py follows every next_page_token when limit is unset."""
    request = StockBarsRequest(
        symbol_or_symbols=list(symbols),
        timeframe=TimeFrame.Minute,
        start=datetime.combine(day, dt_time(9, 30), NY),
        end=datetime.combine(day, dt_time(16, 0), NY),
        feed=DataFeed.IEX,
    )
    for attempt in range(RETRIES):
        try:
            frame = client.get_stock_bars(request).df
            break
        except APIError as error:
            transient = error.status_code is None or error.status_code == 429 or error.status_code >= 500
            if not transient or attempt == RETRIES - 1:
                raise
            time.sleep(2**attempt)
        except RequestException:
            if attempt == RETRIES - 1:
                raise
            time.sleep(2**attempt)
    else:
        raise RuntimeError("unreachable retry state")

    if frame.empty:
        return pd.DataFrame(columns=["symbol", "et", *FIELDS])
    clean = frame.reset_index()[["symbol", "timestamp", *FIELDS]]
    timestamp = pd.to_datetime(clean["timestamp"], utc=True).dt.tz_convert(NY)
    clean["et"] = timestamp.dt.hour * 60 + timestamp.dt.minute
    return clean[clean["et"].between(570, 959)][["symbol", "et", *FIELDS]]


def candidate_days(selected_day: str | None) -> list[tuple[date, Path, list[str]]]:
    days: list[tuple[date, Path, list[str]]] = []
    for path in sorted(LEADERBOARD.glob("path_*.parquet")):
        day_text = path.stem.removeprefix("path_")
        if selected_day is not None and day_text != selected_day:
            continue
        frame = pd.read_parquet(path, columns=None)
        if "ticker" not in frame.columns:
            continue
        symbols = frame["ticker"].drop_duplicates().astype(str).tolist()
        if symbols:
            days.append((date.fromisoformat(day_text), path, symbols))
    return days


def read_missing() -> set[tuple[str, str]]:
    if not MISSING_PATH.exists():
        return set()
    with MISSING_PATH.open(encoding="utf-8") as handle:
        return {(row["day"], row["ticker"]) for line in handle if (row := json.loads(line))}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day")
    parser.add_argument("--max-days", type=int)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    api_key = os.environ["ALPACA_API_KEY"]
    secret_key = os.environ["ALPACA_SECRET_KEY"]
    client = StockHistoricalDataClient(api_key, secret_key)
    OUT.mkdir(parents=True, exist_ok=True)
    known_missing = read_missing()
    written = 0
    requested = 0
    days = candidate_days(args.day)

    for day, _path, symbols in days:
        output = OUT / f"path_{day.isoformat()}.parquet"
        if output.exists():
            continue
        if args.max_days is not None and requested >= args.max_days:
            break
        bars = fetch_day(client, day, symbols)
        tape, missing = construct_tape(day.isoformat(), symbols, bars)
        tape.to_parquet(output, index=False)
        fresh = [(day.isoformat(), symbol) for symbol in missing if (day.isoformat(), symbol) not in known_missing]
        if fresh:
            with MISSING_PATH.open("a", encoding="utf-8") as handle:
                for missing_day, symbol in fresh:
                    handle.write(json.dumps({"day": missing_day, "ticker": symbol}) + "\n")
                    known_missing.add((missing_day, symbol))
        written += 1
        requested += 1
        if written % 50 == 0:
            print(f"progress written={written} cached={sum((OUT / f'path_{d.isoformat()}.parquet').exists() for d, _, _ in days)}", flush=True)
        time.sleep(0.2)

    cached = sum((OUT / f"path_{day.isoformat()}.parquet").exists() for day, _, _ in days)
    print(f"final days_written={written} days_cached={cached}/{len(days)} missing_pairs={len(known_missing)}")


if __name__ == "__main__":
    main()
