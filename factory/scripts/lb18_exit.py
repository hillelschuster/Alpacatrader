#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pandas", "pyarrow", "alpaca-py", "python-dotenv"]
# ///
# ─── How to run ───
# uv run --quiet --python 3.11 --with pandas --with pyarrow --with alpaca-py --with python-dotenv python factory/scripts/lb18_exit.py
# uv run --quiet --python 3.11 --with pandas --with pyarrow --with alpaca-py --with python-dotenv python factory/scripts/lb18_exit.py --self-test
# ──────────────────
# noqa: SIZE_OK — frozen contract requires one producer script.

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd  # noqa: PANDAS_OK — mandated repository research stack.
from alpaca.common.exceptions import APIError
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockQuotesRequest
from dotenv import load_dotenv
from requests.exceptions import RequestException

ROOT: Final = Path(__file__).resolve().parents[2]
ART: Final = ROOT / "factory" / "artifacts"
CACHE: Final = ROOT / "data" / "subminute"
FILLS_PATH: Final = ART / "lb18_oos_oos.parquet"
MICRO_PATH: Final = ART / "lb18_subminute.parquet"
A3B_PATH: Final = ART / "lb18_iex_hybrid.parquet"
EXEC_PATH: Final = ART / "lb18_exec.json"
OUT_JSON: Final = ART / "lb18_exit.json"
OUT_PARQUET: Final = ART / "lb18_exit.parquet"
HORIZONS: Final = (0, 60, 120, 300, 600, 900, 1800)
SLIPPAGES: Final = (0, 25, 50, 100)
FRICTION: Final = 0.01
QUOTE_CAP: Final = 1_000_000
QUOTE_GRACE_SECONDS: Final = 5
TARGET_RET: Final = 1.0 / 0.9 - 1.0 - FRICTION


@dataclass(frozen=True, slots=True)
class HorizonResult:
    bid: float
    exit_price: float
    source: str
    r1_ret: float
    r2_ret: float
    resolved: bool


def _source_cache_path(row: pd.Series) -> Path:
    ticker = "".join(character for character in str(row["ticker"]) if character.isalnum())
    return CACHE / f"{row['date']}_{ticker}_{int(row['tf'])}.parquet"


def _quote_cache_path(row: pd.Series) -> Path:
    return _source_cache_path(row).with_name(f"{_source_cache_path(row).stem}_exit_quotes.parquet")


def _frame(response_frame: pd.DataFrame) -> pd.DataFrame:
    if response_frame.empty:
        return pd.DataFrame()
    frame = response_frame.reset_index()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    return frame.sort_values("timestamp").reset_index(drop=True)


def _api_quotes(
    client: StockHistoricalDataClient, request: StockQuotesRequest
) -> tuple[pd.DataFrame, str | None]:
    for attempt in range(6):
        try:
            return _frame(client.get_stock_quotes(request).df), None
        except (APIError, RequestException) as error:
            status = getattr(error, "status_code", None)
            if attempt == 5 or status not in (None, 429, 500, 502, 503, 504):
                return pd.DataFrame(), f"{type(error).__name__}: {str(error)[:200]}"
            time.sleep(min(60.0, 2.0**attempt))
    return pd.DataFrame(), "retry loop exhausted"


def _split_source(path: Path) -> tuple[pd.DataFrame, pd.Series]:
    cache = pd.read_parquet(path)
    trades = cache[cache["record_type"] == "trade"].copy()
    trades["timestamp"] = pd.to_datetime(trades["timestamp"], utc=True)
    trades.sort_values("timestamp", inplace=True)
    return trades, cache[cache["record_type"] == "status"].iloc[0]


def _et_timestamp(day: str, minute: int) -> pd.Timestamp:
    return pd.Timestamp(
        f"{day} {minute // 60:02d}:{minute % 60:02d}", tz="America/New_York"
    ).tz_convert("UTC")


def _anchor(trades: pd.DataFrame, start: pd.Timestamp, bid: float) -> pd.Timestamp | None:
    if trades.empty:
        return None
    eligible = trades[
        (trades["timestamp"] >= start)
        & (trades["timestamp"] <= start + pd.Timedelta(minutes=30))
        & (trades["price"] <= bid)
    ]
    return None if eligible.empty else pd.Timestamp(eligible.iloc[0]["timestamp"])


def _fetch_quote_cache(
    client: StockHistoricalDataClient, row: pd.Series, anchor: pd.Timestamp
) -> None:
    request = StockQuotesRequest(
        symbol_or_symbols=str(row["ticker"]),
        start=anchor.to_pydatetime(),
        end=(anchor + pd.Timedelta(seconds=max(HORIZONS) + QUOTE_GRACE_SECONDS)).to_pydatetime(),
        feed=DataFeed.SIP,
        limit=QUOTE_CAP,
    )
    quotes, error = _api_quotes(client, request)
    if quotes.empty:
        output = pd.DataFrame(columns=["record_type", "timestamp", "bid_price", "ask_price"])
    else:
        output = quotes[["timestamp", "bid_price", "ask_price", "bid_size", "ask_size"]].copy()
        output.insert(0, "record_type", "quote")
    status = pd.DataFrame(
        [{
            "record_type": "status", "timestamp": pd.NaT,
            "quote_error": error, "quote_rows": len(quotes),
            "quote_truncated": int(len(quotes) >= QUOTE_CAP),
        }]
    )
    pd.concat([output, status], ignore_index=True).to_parquet(_quote_cache_path(row), index=False)


def _read_quotes(path: Path) -> tuple[pd.DataFrame, pd.Series]:
    cache = pd.read_parquet(path)
    quotes = cache[cache["record_type"] == "quote"].copy()
    if not quotes.empty:
        quotes["timestamp"] = pd.to_datetime(quotes["timestamp"], utc=True)
        quotes.sort_values("timestamp", inplace=True)
    return quotes, cache[cache["record_type"] == "status"].iloc[0]


def _measure_horizon(
    quotes: pd.DataFrame,
    trades: pd.DataFrame,
    anchor: pd.Timestamp,
    horizon_s: int,
    bid: float,
    baseline: float,
    slip_bps: int,
) -> HorizonResult:
    action = anchor + pd.Timedelta(seconds=horizon_s)
    after = quotes[(quotes["timestamp"] >= action) & (quotes["bid_price"] > 0)]
    if not after.empty:
        nbbo_bid = float(after.iloc[0]["bid_price"])
        exit_price, source = nbbo_bid, "nbbo_bid"
    else:
        before = trades[(trades["timestamp"] >= anchor) & (trades["timestamp"] <= action)]
        if before.empty:
            return HorizonResult(np.nan, np.nan, "unresolved", baseline, baseline, False)
        nbbo_bid, exit_price, source = np.nan, float(before.iloc[-1]["price"]), "sip_trade_fallback"
    r1 = exit_price / bid - 1.0 - FRICTION - slip_bps / 10_000
    r2 = r1 if exit_price < bid * (1.0 - FRICTION) else baseline
    return HorizonResult(nbbo_bid, exit_price, source, r1, r2, True)


def _load_fills() -> pd.DataFrame:
    fills = pd.read_parquet(FILLS_PATH)
    micro = pd.read_parquet(MICRO_PATH)
    levels = micro[(micro["delta_s"] == 5) & (micro["lag_s"] == 0)][
        ["date", "ticker", "t0", "tf", "B"]
    ].drop_duplicates()
    result = fills.merge(levels, on=["date", "ticker", "t0", "tf"], how="left", validate="one_to_one")
    if len(result) != 541 or result["B"].isna().any():
        raise RuntimeError("frozen 541-fill population or B reconstruction mismatch")
    return result.sort_values(["date", "ticker", "tf"]).reset_index(drop=True)


def _client() -> StockHistoricalDataClient:
    load_dotenv(ROOT / ".env")
    key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY missing")
    return StockHistoricalDataClient(key, secret)


def _cache_quotes(fills: pd.DataFrame, max_new: int) -> None:
    pending: list[tuple[pd.Series, pd.Timestamp]] = []
    for _, row in fills.iterrows():
        trades, _ = _split_source(_source_cache_path(row))
        anchor = _anchor(trades, _et_timestamp(str(row["date"]), int(row["tf"])), float(row["B"]))
        if anchor is not None and not _quote_cache_path(row).exists():
            pending.append((row, anchor))
    if not pending:
        return
    client = _client()
    limit = len(pending) if max_new <= 0 else min(max_new, len(pending))
    for index, (row, anchor) in enumerate(pending[:limit], start=1):
        _fetch_quote_cache(client, row, anchor)
        if index % 20 == 0 or index == limit:
            print(f"cached {index}/{limit}; remaining {len(pending) - index}", flush=True)


def _measure(fills: pd.DataFrame) -> tuple[pd.DataFrame, list[pd.Series], list[pd.Series]]:
    records: list[dict[str, float | int | str | bool | None]] = []
    source_statuses, quote_statuses = [], []
    for _, row in fills.iterrows():
        trades, source_status = _split_source(_source_cache_path(row))
        source_statuses.append(source_status)
        anchor = _anchor(trades, _et_timestamp(str(row["date"]), int(row["tf"])), float(row["B"]))
        quotes = pd.DataFrame(columns=["timestamp", "bid_price"])
        quote_status = pd.Series({"quote_error": None, "quote_truncated": 0, "quote_rows": 0})
        if anchor is not None and _quote_cache_path(row).exists():
            quotes, quote_status = _read_quotes(_quote_cache_path(row))
            quote_statuses.append(quote_status)
        for horizon in HORIZONS:
            measured = (
                HorizonResult(np.nan, np.nan, "anchor_missing", float(row["ret"]), float(row["ret"]), False)
                if anchor is None
                else _measure_horizon(
                    quotes, trades, anchor, horizon, float(row["B"]), float(row["ret"]), 50
                )
            )
            records.append({
                "date": str(row["date"]), "month": str(row["date"])[:7],
                "ticker": str(row["ticker"]), "t_anchor": None if anchor is None else anchor.isoformat(),
                "B": float(row["B"]), "H": horizon, "exit_price_src": measured.source,
                "exit_price": measured.exit_price, "bid_H": measured.bid,
                "base_ret": float(row["ret"]), "R1_ret": measured.r1_ret,
                "R2_ret": measured.r2_ret, "resolved": measured.resolved,
                "prior_flush": int(row["prior_flush"]),
            })
    return pd.DataFrame(records), source_statuses, quote_statuses


def _rule_returns(frame: pd.DataFrame, rule: str, slip: int) -> pd.Series:
    market = frame["exit_price"] / frame["B"] - 1.0 - FRICTION - slip / 10_000
    resolved_market = market.where(frame["resolved"], frame["base_ret"])
    if rule == "R1":
        return resolved_market
    selected = frame["resolved"] & (frame["exit_price"] < frame["B"] * (1.0 - FRICTION))
    return resolved_market.where(selected, frame["base_ret"])


def _metrics(frame: pd.DataFrame, rule: str, slip: int) -> dict[str, float | int]:
    returns = _rule_returns(frame, rule, slip)
    monthly = pd.DataFrame({"month": frame["month"], "ret": returns, "base": frame["base_ret"]}).groupby("month").mean()
    target = np.isclose(frame["base_ret"].to_numpy(float), TARGET_RET, atol=1e-8)
    sacrificed = float(np.maximum(frame.loc[target, "base_ret"].to_numpy(float) - returns[target].to_numpy(float), 0).sum())
    target_pnl = float(frame.loc[target, "base_ret"].sum())
    return {
        "n": int(len(frame)), "resolved_n": int(frame["resolved"].sum()),
        "pooled_ev": round(float(returns.mean()), 8), "median": round(float(returns.median()), 8),
        "months_positive": int((monthly["ret"] > 0).sum()),
        "months_improved": int((monthly["ret"] > monthly["base"]).sum()),
        "worst_month": round(float(monthly["ret"].min()), 8),
        "std": round(float(returns.std()), 8), "tail_count": int((returns < -0.11).sum()),
        "target_hit_n": int(target.sum()), "target_pnl": round(target_pnl, 8),
        "target_pnl_sacrificed": round(sacrificed, 8),
        "target_pnl_sacrifice_fraction": round(sacrificed / target_pnl if target_pnl else 0.0, 8),
    }


def _gate(metric: dict[str, float | int], baseline: float, comparators: dict[str, float]) -> dict[str, bool | float]:
    return {
        "pf2_ev_gt_frozen_and_stop08_12_15": float(metric["pooled_ev"]) > max(baseline, *comparators.values()),
        "months_improved_gte_12_of_14": int(metric["months_improved"]) >= 12,
        "worst_month_within_0_5pp_of_frozen": float(metric["worst_month"]) >= -0.0203 - 0.005,
        "target_hit_sacrifice_lt_10pct": float(metric["target_pnl_sacrifice_fraction"]) < 0.10,
    }


def _halves(frame: pd.DataFrame, rule: str, horizon: int) -> dict[str, dict[str, float | int]]:
    selected = frame[frame["H"] == horizon]
    months = sorted(selected["month"].unique())
    output = {}
    for label, half_months in (("first_7_months", months[:7]), ("last_7_months", months[7:])):
        half = selected[selected["month"].isin(half_months)]
        metric = _metrics(half, rule, 50)
        output[label] = {"months": ",".join(half_months), **metric}
    return output


def _write_outputs(rows: pd.DataFrame, source_statuses: list[pd.Series], quote_statuses: list[pd.Series]) -> dict[str, object]:
    populations = {"all": rows, "pf2": rows[rows["prior_flush"] >= 2]}
    grid: list[dict[str, object]] = []
    for population, population_rows in populations.items():
        for horizon in HORIZONS:
            horizon_rows = population_rows[population_rows["H"] == horizon].reset_index(drop=True)
            for slip in SLIPPAGES:
                for rule in ("R1", "R2"):
                    grid.append({"population": population, "H": horizon, "slip_bps": slip, "rule": rule, **_metrics(horizon_rows, rule, slip)})

    baseline_pf2 = float(rows.loc[(rows["H"] == 0) & (rows["prior_flush"] >= 2), "base_ret"].mean())
    execution = json.loads(EXEC_PATH.read_text())
    comparators = {name: float(execution["robust"][name]["mean"]) for name in ("stop08", "stop12", "stop15")}
    gates = []
    for item in grid:
        if item["population"] == "pf2" and item["slip_bps"] == 50:
            checks = _gate(item, baseline_pf2, comparators)
            gates.append({**item, "checks": checks, "passed": all(checks.values())})

    split = {}
    pf2 = populations["pf2"]
    for rule in ("R1", "R2"):
        candidates = [item for item in grid if item["population"] == "pf2" and item["slip_bps"] == 50 and item["rule"] == rule]
        best = max(candidates, key=lambda item: float(item["pooled_ev"]))
        split[rule] = {"fixed_best_H": int(best["H"]), "selection": "descriptive full-period EV; no fold reselection", "halves": _halves(pf2, rule, int(best["H"]))}

    source_status = pd.DataFrame(source_statuses)
    quote_status = pd.DataFrame(quote_statuses)
    coverage_by_h = {}
    for horizon in HORIZONS:
        horizon_rows = rows[rows["H"] == horizon]
        coverage_by_h[str(horizon)] = horizon_rows["exit_price_src"].value_counts().astype(int).to_dict()
    a3b = pd.read_parquet(A3B_PATH)
    a3b = a3b[a3b["variant"] == "A3b"]
    available = sum(_source_cache_path(row).exists() for _, row in a3b.iterrows())
    payload: dict[str, object] = {
        "study": "PRE-REG-EXIT-01 — exit timing",
        "flags": {"measurement": True, "seen_data": True, "not_an_alpha_claim": True, "pre_reg": "PRE-REG-EXIT-01"},
        "design": {"friction": FRICTION, "slippage_bps": list(SLIPPAGES), "horizons_s": list(HORIZONS), "rules": ["R1", "R2"], "unresolved_handling": "retain frozen exit and report coverage"},
        "coverage": {
            "population_n": 541, "anchored_n": int(rows[rows["H"] == 0]["t_anchor"].notna().sum()),
            "anchor_missing_n": int(rows[rows["H"] == 0]["t_anchor"].isna().sum()),
            "trade_truncated_n": int(source_status["trade_truncated"].fillna(0).sum()),
            "trade_error_n": int(source_status["trade_error"].notna().sum()),
            "quote_cache_n": len(quote_statuses),
            "quote_error_n": int(quote_status["quote_error"].notna().sum()) if len(quote_status) else 0,
            "quote_truncated_n": int(quote_status["quote_truncated"].fillna(0).sum()) if len(quote_status) else 0,
            "quote_cap_total": QUOTE_CAP, "strict_exit_quote_at_or_after_action": True,
            "by_horizon_source": coverage_by_h,
            "A3b_secondary": {"status": "not_run", "reason": "not cheap: only existing trade caches reused; fetching new trade windows is prohibited", "population_n": int(len(a3b)), "existing_trade_cache_n": int(available)},
        },
        "baselines": {"frozen_pf2": baseline_pf2, "source": "frozen OOS fill returns; stop diagnostics reused from lb18_exec.json", **comparators},
        "grid": grid, "gates_at_50bps": gates, "seven_seven_split": split,
    }
    output_rows = pd.concat([rows.assign(rule=rule, slip_bps=50) for rule in ("R1", "R2")], ignore_index=True)
    output_rows[["date", "ticker", "t_anchor", "B", "H", "exit_price_src", "bid_H", "base_ret", "R1_ret", "R2_ret", "resolved", "rule", "slip_bps", "exit_price", "prior_flush"]].to_parquet(OUT_PARQUET, index=False)
    OUT_JSON.write_text(json.dumps(payload, indent=1, allow_nan=True) + "\n")
    return payload


def _print_summary(payload: dict[str, object]) -> None:
    print("pop H slip  R1_EV R1_med R1_m+ R1_imp R1_worst R1_std R1_tail R1_sac | R2_EV R2_med R2_m+ R2_imp R2_worst R2_std R2_tail R2_sac")
    grid = payload["grid"]
    for population in ("all", "pf2"):
        for horizon in HORIZONS:
            for slip in SLIPPAGES:
                pair = [item for item in grid if item["population"] == population and item["H"] == horizon and item["slip_bps"] == slip]
                values = {item["rule"]: item for item in pair}
                left, right = values["R1"], values["R2"]
                print(f"{population:3s} {horizon:4d} {slip:4d} {left['pooled_ev']:+.4f} {left['median']:+.4f} {left['months_positive']:2d} {left['months_improved']:2d} {left['worst_month']:+.4f} {left['std']:.4f} {left['tail_count']:3d} {left['target_pnl_sacrifice_fraction']:.1%} | {right['pooled_ev']:+.4f} {right['median']:+.4f} {right['months_positive']:2d} {right['months_improved']:2d} {right['worst_month']:+.4f} {right['std']:.4f} {right['tail_count']:3d} {right['target_pnl_sacrifice_fraction']:.1%}")
    gates = payload["gates_at_50bps"]
    for rule in ("R1", "R2"):
        rule_gates = [item for item in gates if item["rule"] == rule]
        passed = [int(item["H"]) for item in rule_gates if item["passed"]]
        print(f"GATE {rule}: {'PASS' if passed else 'FAIL'}" + (f" at H={passed}" if passed else ""))
        for item in rule_gates:
            print(f"  H={item['H']:4d}s {'PASS' if item['passed'] else 'FAIL'} {item['checks']}")
    print("7/7 FIXED-H TRANSFER")
    for rule, item in payload["seven_seven_split"].items():
        print(f"  {rule} H={item['fixed_best_H']}s {item['halves']}")


def _self_test() -> None:
    anchor = pd.Timestamp("2099-01-02T14:30:02Z")
    quotes = pd.DataFrame({
        "timestamp": pd.to_datetime(["2099-01-02T14:30:01.000Z", "2099-01-02T14:31:02.100Z"], utc=True),
        "bid_price": [10.10, 9.80],
    })
    trades = pd.DataFrame({
        "timestamp": pd.to_datetime(["2099-01-02T14:30:02Z", "2099-01-02T14:31:02Z"], utc=True),
        "price": [10.00, 9.85],
    })
    assert _anchor(pd.DataFrame(columns=["timestamp"]), anchor, 10.0) is None
    measured = _measure_horizon(quotes, trades, anchor, 60, 10.0, 0.04, 50)
    assert measured.bid == 9.80
    assert abs(measured.r1_ret - (-0.035)) < 1e-12
    assert abs(measured.r2_ret - (-0.035)) < 1e-12
    evs = [_measure_horizon(quotes, trades, anchor, 60, 10.0, 0.04, slip).r1_ret for slip in SLIPPAGES]
    assert all(left >= right for left, right in zip(evs, evs[1:]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--max-new", type=int, default=0)
    parser.add_argument("--fetch-only", action="store_true")
    arguments = parser.parse_args()
    if arguments.self_test:
        _self_test()
        print("self-test PASS")
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    fills = _load_fills()
    _cache_quotes(fills, arguments.max_new)
    remaining = sum(
        _anchor(
            _split_source(_source_cache_path(row))[0],
            _et_timestamp(str(row["date"]), int(row["tf"])),
            float(row["B"]),
        ) is not None
        and not _quote_cache_path(row).exists()
        for _, row in fills.iterrows()
    )
    if remaining:
        print(f"quote cache incomplete: {remaining} anchored fills remain")
        return
    if not arguments.fetch_only:
        rows, source_statuses, quote_statuses = _measure(fills)
        _print_summary(_write_outputs(rows, source_statuses, quote_statuses))


if __name__ == "__main__":
    main()
