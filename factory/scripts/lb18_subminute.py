#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pandas", "pyarrow", "alpaca-py", "python-dotenv"]
# ///
# ─── How to run ───
# uv run --quiet --python 3.11 --with pandas --with pyarrow --with alpaca-py --with python-dotenv python factory/scripts/lb18_subminute.py
# uv run --quiet --python 3.11 --with pandas --with pyarrow --with alpaca-py --with python-dotenv python factory/scripts/lb18_subminute.py --self-test
# ──────────────────
# noqa: SIZE_OK — PRE-REG-MICRO-01 requires one script to fetch, measure, and report.
"""PRE-REG-MICRO-01 Study B decision-anchored microstructure measurement."""
# pyright: basic, reportArgumentType=false, reportAttributeAccessIssue=false, reportReturnType=false, reportAssignmentType=false, reportOperatorIssue=false, reportIndexIssue=false, reportCallIssue=false, reportOptionalMemberAccess=false, reportGeneralTypeIssues=false

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd  # noqa: PANDAS_OK — repository research stack and mandated command.
from alpaca.common.exceptions import APIError
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockQuotesRequest, StockTradesRequest
from dotenv import load_dotenv
from requests.exceptions import RequestException

ROOT: Final = Path(__file__).resolve().parents[2]
ART: Final = ROOT / "factory" / "artifacts"
CACHE: Final = ROOT / "data" / "subminute"
FILLS_PATH: Final = ART / "lb18_oos_oos.parquet"
OUT_JSON: Final = ART / "lb18_subminute.json"
OUT_PARQUET: Final = ART / "lb18_subminute.parquet"
DELTAS: Final = (5, 15, 30)
LAGS: Final = (0, 60)
TRADE_CAP: Final = 200_000
QUOTE_CAP: Final = 100_000
FRICTION: Final = 0.01
STOP_FRACTION: Final = 0.10
TARGET_EROSION_LIMIT: Final = 0.10
NUMERIC_FEATURES: Final = (
    "touch_trade_count", "touch_volume", "touch_size_median", "below_span_s",
    "touch_episodes", "max_depth_below", "snapback",
)
BOOLEAN_FEATURES: Final = ("snapped_back", "sip_bid_ge_B", "iex_above_B")


def _et_timestamp(day: str, minute: int) -> pd.Timestamp:
    return pd.Timestamp(
        f"{day} {minute // 60:02d}:{minute % 60:02d}", tz="America/New_York",
    ).tz_convert("UTC")


def _first_anchor(trades: list[tuple[float, float]], bid: float) -> float | None:
    for seconds, price in trades:
        if price <= bid:
            return seconds
    return None


def _cache_path(row: pd.Series) -> Path:
    safe_ticker = "".join(character for character in str(row["ticker"]) if character.isalnum())
    return CACHE / f"{row['date']}_{safe_ticker}_{int(row['tf'])}.parquet"


def _frame(response_frame: pd.DataFrame) -> pd.DataFrame:
    if response_frame.empty:
        return pd.DataFrame()
    frame = response_frame.reset_index()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    return frame.sort_values("timestamp").reset_index(drop=True)


def _api_frame(
    client: StockHistoricalDataClient,
    request: StockTradesRequest | StockQuotesRequest,
    kind: str,
) -> tuple[pd.DataFrame, str | None]:
    for attempt in range(6):
        try:
            response = (
                client.get_stock_trades(request)
                if kind == "trade"
                else client.get_stock_quotes(request)
            )
            return _frame(response.df), None
        except (APIError, RequestException) as error:
            status = getattr(error, "status_code", None)
            if attempt == 5 or status not in (None, 429, 500, 502, 503, 504):
                return pd.DataFrame(), f"{type(error).__name__}: {str(error)[:200]}"
            time.sleep(min(60.0, 2.0 ** attempt))
    return pd.DataFrame(), "retry loop exhausted"


def _anchor_timestamp(trades: pd.DataFrame, start: pd.Timestamp, bid: float) -> pd.Timestamp | None:
    eligible = trades[
        (trades["timestamp"] >= start)
        & (trades["timestamp"] <= start + pd.Timedelta(minutes=30))
        & (trades["price"] <= bid)
    ]
    return None if eligible.empty else pd.Timestamp(eligible.iloc[0]["timestamp"])


def _fetch_fill(
    client: StockHistoricalDataClient,
    row: pd.Series,
    destination: Path,
) -> None:
    start = _et_timestamp(str(row["date"]), int(row["tf"]))
    end = start + pd.Timedelta(minutes=33)
    trade_request = StockTradesRequest(
        symbol_or_symbols=str(row["ticker"]), start=start.to_pydatetime(),
        end=end.to_pydatetime(), feed=DataFeed.SIP, limit=TRADE_CAP,
    )
    trades, trade_error = _api_frame(client, trade_request, "trade")
    anchor = None if trade_error or trades.empty else _anchor_timestamp(trades, start, float(row["B"]))
    quotes = pd.DataFrame()
    quote_error = None
    if anchor is not None:
        quote_request = StockQuotesRequest(
            symbol_or_symbols=str(row["ticker"]), start=anchor.to_pydatetime(),
            end=(anchor + pd.Timedelta(seconds=91)).to_pydatetime(),
            feed=DataFeed.SIP, limit=QUOTE_CAP,
        )
        quotes, quote_error = _api_frame(client, quote_request, "quote")

    records = []
    for trade in trades.itertuples(index=False):
        records.append({
            "record_type": "trade", "timestamp": trade.timestamp,
            "price": float(trade.price), "size": float(trade.size),
        })
    for quote in quotes.itertuples(index=False):
        records.append({
            "record_type": "quote", "timestamp": quote.timestamp,
            "bid_price": float(quote.bid_price), "ask_price": float(quote.ask_price),
            "bid_size": float(quote.bid_size), "ask_size": float(quote.ask_size),
        })
    records.append({
        "record_type": "status", "timestamp": pd.NaT,
        "trade_truncated": int(len(trades) >= TRADE_CAP),
        "quote_truncated": int(len(quotes) >= QUOTE_CAP),
        "trade_error": trade_error, "quote_error": quote_error,
        "trade_rows": len(trades), "quote_rows": len(quotes),
    })
    pd.DataFrame(records).to_parquet(destination, index=False)


def _attach_b(fills: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for day, day_fills in fills.groupby("date", sort=False):
        path = ROOT / "data" / "leaderboard" / f"path_{day}.parquet"
        bars = pd.read_parquet(path, columns=["ticker", "t", "c"])
        merged = day_fills.merge(
            bars, left_on=["ticker", "tf"], right_on=["ticker", "t"], how="left",
            validate="many_to_one",
        )
        frames.append(merged.drop(columns="t"))
    result = pd.concat(frames, ignore_index=True)
    if result["c"].isna().any():
        raise RuntimeError("fill-bar close missing while reconstructing B")
    result["B"] = result["c"] / (1.0 + result["fc"])
    result["c0"] = result["B"] / (1.0 - STOP_FRACTION)
    return result.sort_values(["date", "ticker", "tf"]).reset_index(drop=True)


def _split_cache(cache: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    trades = cache[cache["record_type"] == "trade"].copy()
    quotes = cache[cache["record_type"] == "quote"].copy()
    status = cache[cache["record_type"] == "status"].iloc[0]
    for frame in (trades, quotes):
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        frame.sort_values("timestamp", inplace=True)
    return trades, quotes, status


def _first_boundary(
    trades: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    stop: float,
    target: float,
) -> str | None:
    window = trades[(trades["timestamp"] > start) & (trades["timestamp"] <= end)]
    for trade in window.itertuples(index=False):
        if float(trade.price) <= stop:
            return "stop"
        if float(trade.price) >= target:
            return "target"
    return None


def _rescue_class(
    trades: pd.DataFrame,
    anchor: pd.Timestamp,
    cutoff: pd.Timestamp,
    horizon: pd.Timestamp,
    stop: float,
    target: float,
) -> str:
    early = _first_boundary(trades, anchor, cutoff, stop, target)
    if early is not None:
        return f"early-{early}"
    later = _first_boundary(trades, cutoff, horizon, stop, target)
    return "unresolved" if later is None else f"alive-later-{later}"


def _feature_row(
    trades: pd.DataFrame,
    quotes: pd.DataFrame,
    anchor: pd.Timestamp,
    cutoff: pd.Timestamp,
    bid: float,
) -> dict[str, float | int]:
    causal = trades[(trades["timestamp"] >= anchor) & (trades["timestamp"] <= cutoff)]
    touches = causal[causal["price"] <= bid]
    prices = causal["price"].to_numpy(float)
    touched = prices <= bid
    episodes = int((touched & ~np.concatenate(([False], touched[:-1]))).sum()) if len(prices) else 0
    last = float(causal.iloc[-1]["price"]) if len(causal) else np.nan
    sizes = touches["size"]
    immediate = quotes[quotes["timestamp"] >= cutoff].head(1)
    if immediate.empty:
        bid_px = ask_px = bid_size = ask_size = np.nan
    else:
        quote = immediate.iloc[0]
        bid_px, ask_px = float(quote["bid_price"]), float(quote["ask_price"])
        bid_size, ask_size = float(quote["bid_size"]), float(quote["ask_size"])
    return {
        "touch_trade_count": int(len(touches)),
        "touch_volume": float(sizes.sum()),
        "touch_size_p25": float(sizes.quantile(0.25)) if len(sizes) else np.nan,
        "touch_size_median": float(sizes.median()) if len(sizes) else np.nan,
        "touch_size_p75": float(sizes.quantile(0.75)) if len(sizes) else np.nan,
        "touch_size_max": float(sizes.max()) if len(sizes) else np.nan,
        "dwell_since_first_touch_s": float((cutoff - anchor).total_seconds()),
        "below_span_s": float((touches["timestamp"].iloc[-1] - anchor).total_seconds()) if len(touches) else 0.0,
        "touch_episodes": episodes,
        "max_depth_below": float(min(0.0, float(touches["price"].min()) / bid - 1.0)) if len(touches) else 0.0,
        "snapback": last / bid - 1.0 if np.isfinite(last) else np.nan,
        "snapped_back": int(last >= bid) if np.isfinite(last) else np.nan,
        "sip_bid": bid_px, "sip_ask": ask_px,
        "sip_bid_ge_B": int(bid_px >= bid) if np.isfinite(bid_px) else np.nan,
        "nbbo_spread": ask_px - bid_px if np.isfinite(bid_px + ask_px) else np.nan,
        "nbbo_spread_bps": (ask_px - bid_px) / bid_px * 10_000 if bid_px > 0 else np.nan,
        "sip_bid_size": bid_size, "sip_ask_size": ask_size,
    }


def _iex_lookup(fills: pd.DataFrame) -> dict[tuple[str, str, int], float]:
    lookup = {}
    for day, day_fills in fills.groupby("date", sort=False):
        path = ROOT / "data" / "iex_tape" / f"path_{day}.parquet"
        if not path.exists():
            continue
        tickers = set(day_fills["ticker"])
        bars = pd.read_parquet(path, columns=["ticker", "t", "c"])
        for bar in bars[bars["ticker"].isin(tickers)].itertuples(index=False):
            lookup[(str(day), str(bar.ticker), int(bar.t))] = float(bar.c)
    return lookup


def _iex_feature(
    lookup: dict[tuple[str, str, int], float],
    day: str,
    ticker: str,
    cutoff: pd.Timestamp,
    bid: float,
) -> tuple[float, float]:
    minute = cutoff.tz_convert("America/New_York").hour * 60 + cutoff.tz_convert("America/New_York").minute
    close = lookup.get((day, ticker, minute))
    if close is None:
        return np.nan, np.nan
    return close, float(close >= bid)


def _action_values(
    trades: pd.DataFrame,
    anchor: pd.Timestamp,
    action: pd.Timestamp,
    horizon: pd.Timestamp,
    bid: float,
    target: float,
    baseline: float,
) -> dict[str, float | int | str]:
    if action >= horizon:
        return {
            "action_alive": 0, "resolved_before_action": "time-exit",
            "action_price": np.nan, "exit_now_delta": 0.0, "tighten_delta": 0.0,
        }
    resolved = _first_boundary(trades, anchor, action, bid * 0.9, target)
    seen = trades[(trades["timestamp"] >= anchor) & (trades["timestamp"] <= action)]
    if resolved is not None or seen.empty:
        return {
            "action_alive": 0, "resolved_before_action": resolved or "no-action-price",
            "action_price": np.nan, "exit_now_delta": 0.0, "tighten_delta": 0.0,
        }
    action_price = float(seen.iloc[-1]["price"])
    exit_now = action_price / bid - 1.0 - FRICTION
    future = trades[(trades["timestamp"] > action) & (trades["timestamp"] <= horizon)]
    tightened = baseline
    for trade in future.itertuples(index=False):
        price = float(trade.price)
        if price <= action_price:
            tightened = price / bid - 1.0 - FRICTION
            break
        if price >= target:
            tightened = target / bid - 1.0 - FRICTION
            break
    return {
        "action_alive": 1, "resolved_before_action": "alive",
        "action_price": action_price, "exit_now_delta": exit_now - baseline,
        "tighten_delta": tightened - baseline,
    }


def _measure_fill(
    row: pd.Series,
    iex_lookup: dict[tuple[str, str, int], float],
) -> list[dict[str, float | int | str | bool]]:
    cache = pd.read_parquet(_cache_path(row))
    trades, quotes, status = _split_cache(cache)
    start = _et_timestamp(str(row["date"]), int(row["tf"]))
    anchor = None if trades.empty else _anchor_timestamp(trades, start, float(row["B"]))
    if anchor is None:
        output = []
        null_features = {
            feature: np.nan for feature in (
                "iex_close", "iex_above_B", "touch_trade_count", "touch_volume",
                "touch_size_p25", "touch_size_median", "touch_size_p75", "touch_size_max",
                "dwell_since_first_touch_s", "below_span_s", "touch_episodes",
                "max_depth_below", "snapback", "snapped_back", "sip_bid", "sip_ask",
                "sip_bid_ge_B", "nbbo_spread", "nbbo_spread_bps", "sip_bid_size",
                "sip_ask_size", "action_price",
            )
        }
        for delta in DELTAS:
            for lag in LAGS:
                output.append({
                    "population": "primary", "date": str(row["date"]),
                    "month": str(row["date"])[:7], "ticker": str(row["ticker"]),
                    "t0": int(row["t0"]), "tf": int(row["tf"]), "t_anchor": None,
                    "B": float(row["B"]), "delta_s": delta, "lag_s": lag,
                    "action_s": delta + lag, "prior_flush": int(row["prior_flush"]),
                    "rank": int(row["rank"]), "fc": float(row["fc"]),
                    "baseline_ret": float(row["ret"]),
                    "baseline_breach": bool(float(row["ret"]) < -0.11),
                    "rescue_class": "anchor-missing", "action_alive": 0,
                    "resolved_before_action": "anchor-missing", "exit_now_delta": 0.0,
                    "tighten_delta": 0.0,
                    "trade_truncated": int(status.get("trade_truncated", 0) or 0),
                    "quote_truncated": int(status.get("quote_truncated", 0) or 0),
                    **null_features,
                })
        return output
    horizon = _et_timestamp(str(row["date"]), int(row["exit_t"]) + 1)
    output = []
    for delta in DELTAS:
        cutoff = anchor + pd.Timedelta(seconds=delta)
        features = _feature_row(trades, quotes, anchor, cutoff, float(row["B"]))
        iex_close, iex_above = _iex_feature(
            iex_lookup, str(row["date"]), str(row["ticker"]), cutoff, float(row["B"]),
        )
        rescue = _rescue_class(
            trades, anchor, cutoff, horizon, float(row["B"]) * 0.9, float(row["c0"]),
        )
        for lag in LAGS:
            action = cutoff + pd.Timedelta(seconds=lag)
            actions = _action_values(
                trades, anchor, action, horizon, float(row["B"]),
                float(row["c0"]), float(row["ret"]),
            )
            output.append({
                "population": "primary", "date": str(row["date"]),
                "month": str(row["date"])[:7], "ticker": str(row["ticker"]),
                "t0": int(row["t0"]), "tf": int(row["tf"]),
                "t_anchor": anchor.isoformat(), "B": float(row["B"]),
                "delta_s": delta, "lag_s": lag, "action_s": delta + lag,
                "prior_flush": int(row["prior_flush"]), "rank": int(row["rank"]),
                "fc": float(row["fc"]), "baseline_ret": float(row["ret"]),
                "baseline_breach": bool(float(row["ret"]) < -0.11),
                "rescue_class": rescue, "iex_close": iex_close,
                "iex_above_B": iex_above,
                "trade_truncated": int(status.get("trade_truncated", 0) or 0),
                "quote_truncated": int(status.get("quote_truncated", 0) or 0),
                **features, **actions,
            })
    return output


def _distribution(series: pd.Series) -> dict[str, float | int]:
    clean = series.dropna()
    return {
        "n": int(len(clean)), "null_rate": round(float(series.isna().mean()), 4),
        "mean": round(float(clean.mean()), 6) if len(clean) else np.nan,
        "p25": round(float(clean.quantile(0.25)), 6) if len(clean) else np.nan,
        "median": round(float(clean.median()), 6) if len(clean) else np.nan,
        "p75": round(float(clean.quantile(0.75)), 6) if len(clean) else np.nan,
    }


def _feature_stats(rows: pd.DataFrame) -> dict[str, dict[str, dict[str, float | int]]]:
    columns = (
        "touch_trade_count", "touch_volume", "touch_size_p25", "touch_size_median",
        "touch_size_p75", "touch_size_max", "dwell_since_first_touch_s", "below_span_s",
        "touch_episodes", "max_depth_below", "snapback", "snapped_back", "sip_bid",
        "sip_ask", "sip_bid_ge_B", "nbbo_spread", "nbbo_spread_bps", "sip_bid_size",
        "sip_ask_size", "iex_close", "iex_above_B",
    )
    output = {}
    feature_rows = rows[rows["lag_s"] == 0]
    for delta, frame in feature_rows.groupby("delta_s"):
        output[str(delta)] = {column: _distribution(frame[column]) for column in columns}
    return output


def _rescue_counts(rows: pd.DataFrame) -> dict[str, dict[str, dict[str, int]]]:
    output = {}
    for (delta, lag), frame in rows.groupby(["delta_s", "lag_s"]):
        counts = frame["rescue_class"].value_counts().to_dict()
        counts["action_alive"] = int(frame["action_alive"].sum())
        counts["resolved_between_feature_and_action"] = int(
            ((frame["rescue_class"].str.startswith("alive")) & (frame["action_alive"] == 0)).sum()
        )
        output.setdefault(str(delta), {})[str(lag)] = {key: int(value) for key, value in counts.items()}
    return output


def _predicates(frame: pd.DataFrame) -> list[tuple[str, pd.Series, str]]:
    predicates = []
    for feature in NUMERIC_FEATURES:
        clean = frame[feature].dropna()
        for threshold in sorted(set(float(value) for value in clean.quantile([0.25, 0.5, 0.75]))):
            predicates.append((f"{feature}<={threshold:.8g}", frame[feature] <= threshold, feature))
            predicates.append((f"{feature}>={threshold:.8g}", frame[feature] >= threshold, feature))
    for feature in BOOLEAN_FEATURES:
        predicates.append((f"{feature}=0", frame[feature] == 0, feature))
        predicates.append((f"{feature}=1", frame[feature] == 1, feature))
    return predicates


def _candidate(
    frame: pd.DataFrame,
    selected: pd.Series,
    rule: str,
    mode: str,
) -> dict[str, float | int | str | bool]:
    eligible = selected.fillna(False).to_numpy(bool) & frame["action_alive"].to_numpy(bool)
    delta_column = "exit_now_delta" if mode == "exit_now" else "tighten_delta"
    baseline = frame["baseline_ret"].to_numpy(float)
    deltas = frame[delta_column].to_numpy(float)
    change = np.where(eligible, deltas, 0.0)
    counterfactual = baseline + change
    target = frame["rescue_class"].eq("alive-later-target").to_numpy(bool)
    target_base = np.clip(baseline[target], 0.0, None).sum()
    target_cost = -float(np.clip(deltas[target & eligible], None, 0.0).sum())
    erosion = target_cost / target_base if target_base > 0 else 0.0
    baseline_breach = baseline < -0.11
    month_codes = frame["_month_code"].to_numpy(int)
    month_counts = np.bincount(month_codes)
    month_change = np.bincount(month_codes, weights=change) / month_counts
    return {
        "rule": rule, "mode": mode, "n_selected": int(eligible.sum()),
        "baseline_ev": round(float(baseline.mean()), 8),
        "intervention_ev": round(float(counterfactual.mean()), 8),
        "ev_change": round(float(change.mean()), 8),
        "baseline_pnl_sum": round(float(baseline.sum()), 6),
        "intervention_pnl_sum": round(float(counterfactual.sum()), 6),
        "tail_before": int(baseline_breach.sum()),
        "tail_after": int((counterfactual < -0.11).sum()),
        "tail_reduced": int((baseline_breach & (counterfactual >= -0.11)).sum()),
        "target_hit_n": int(target.sum()), "target_hit_selected": int((target & eligible).sum()),
        "target_cost_sum": round(target_cost, 6),
        "target_economics_erosion": round(float(erosion), 6),
        "material_target_erosion": bool(erosion > TARGET_EROSION_LIMIT),
        "months_improved": int((month_change > 0).sum()),
        "n_months": int(frame.attrs["n_months"]),
    }


def _monthly_metrics(
    frame: pd.DataFrame,
    selected: pd.Series,
    mode: str,
) -> dict[str, dict[str, float | int]]:
    eligible = selected.fillna(False) & frame["action_alive"].eq(1)
    delta_column = "exit_now_delta" if mode == "exit_now" else "tighten_delta"
    monthly_frame = frame[["month", "baseline_ret"]].copy()
    monthly_frame["intervention_ret"] = frame["baseline_ret"] + frame[delta_column].where(eligible, 0.0)
    output = {}
    for month, group in monthly_frame.groupby("month", sort=True):
        output[str(month)] = {
            "n": int(len(group)),
            "baseline_ev": round(float(group["baseline_ret"].mean()), 6),
            "intervention_ev": round(float(group["intervention_ret"].mean()), 6),
            "ev_change": round(float((group["intervention_ret"] - group["baseline_ret"]).mean()), 6),
        }
    return output


def _search(rows: pd.DataFrame) -> tuple[dict[str, dict[str, dict[str, dict[str, object]]]], dict[str, int]]:
    results = {}
    counts = {"one_feature_rules": 0, "two_feature_rules": 0, "evaluations": 0}
    for population, population_rows in (
        ("all", rows), ("pf2", rows[rows["prior_flush"] >= 2]),
    ):
        results[population] = {}
        for (delta, lag), frame in population_rows.groupby(["delta_s", "lag_s"]):
            frame = frame.reset_index(drop=True)
            month_codes, months = pd.factorize(frame["month"], sort=True)
            frame["_month_code"] = month_codes
            frame.attrs["n_months"] = len(months)
            predicates = _predicates(frame)
            rules = [(name, mask) for name, mask, _ in predicates]
            counts["one_feature_rules"] += len(rules)
            for left_index, (left_name, left_mask, left_feature) in enumerate(predicates):
                for right_name, right_mask, right_feature in predicates[left_index + 1:]:
                    if left_feature != right_feature:
                        rules.append((f"({left_name}) AND ({right_name})", left_mask & right_mask))
                        counts["two_feature_rules"] += 1
            candidates = []
            for rule, mask in rules:
                if not mask.any():
                    continue
                for mode in ("exit_now", "tighten"):
                    candidates.append(_candidate(frame, mask, rule, mode))
                    counts["evaluations"] += 1
            candidates.sort(
                key=lambda item: (
                    bool(item["material_target_erosion"]),
                    -float(item["intervention_ev"]),
                    -int(item["tail_reduced"]),
                ),
            )
            valid = [
                item for item in candidates
                if not item["material_target_erosion"] and item["ev_change"] > 0 and item["tail_reduced"] > 0
            ]
            rule_masks = {name: mask for name, mask in rules}
            best = candidates[0] if candidates else None
            best_valid = valid[0] if valid else None
            comparators = (
                {"pf2_clean_touch_only": 0.0139}
                if population == "pf2"
                else {"stop08": 0.0036, "stop12": 0.0061, "stop15": 0.0062}
            )
            for candidate in (best, best_valid):
                if candidate is not None:
                    candidate["monthly"] = _monthly_metrics(
                        frame, rule_masks[str(candidate["rule"])], str(candidate["mode"]),
                    )
                    candidate["ev_vs_baselines"] = {
                        name: round(float(candidate["intervention_ev"]) - value, 8)
                        for name, value in comparators.items()
                    }
            results[population].setdefault(str(delta), {})[str(lag)] = {
                "n": int(len(frame)), "earliest_action_s": int(delta),
                "live_poll_action_s": int(delta + 60),
                "baseline_comparators": comparators,
                "best": best, "best_gate_eligible": best_valid,
            }
    return results, counts


def _baseline_payload() -> dict[str, object]:
    payload = json.loads((ART / "lb18_exec.json").read_text())
    return {
        "source": "factory/artifacts/lb18_exec.json; pooled dev+OOS, reused not re-derived",
        "baseline_stop10": payload["robust"]["base"],
        "stop08": payload["robust"]["stop08"],
        "stop12": payload["robust"]["stop12"],
        "stop15": payload["robust"]["stop15"],
        "pf2_clean_touch_only": payload["pf2"]["no_gap_fills"],
        "gap_share_among_breaches": payload["breach"]["gap_share_among_breaches"],
    }


def _gate(search: dict[str, dict[str, dict[str, dict[str, object]]]]) -> dict[str, object]:
    tighter_ev = max(0.0036, 0.0061, 0.0062)
    qualifying = []
    for population, deltas in search.items():
        comparator = 0.0139 if population == "pf2" else tighter_ev
        for delta, lags in deltas.items():
            for lag, result in lags.items():
                candidate = result["best_gate_eligible"]
                if candidate is not None and float(candidate["intervention_ev"]) > comparator:
                    qualifying.append({
                        "population": population, "delta_s": int(delta), "lag_s": int(lag),
                        "comparator_ev": comparator, **candidate,
                    })
    passed = bool(qualifying)
    verdict = (
        "A simple post-fill management rule clears the frozen Study B gate."
        if passed else
        "deprioritize the simple post-fill management approach and keep the baseline stop. The broader microstructure lane stays open."
    )
    return {
        "passed": passed, "verdict": verdict,
        "material_erosion_definition": "more than 10% loss of aggregate positive baseline P&L among alive→later-target cases",
        "comparison": "all-fill rules must exceed max(stop08, stop12, stop15) pooled EV; pf2 rules must exceed pf2 clean-touch-only pooled EV",
        "qualifying_rules": qualifying[:20],
    }


def _coverage(fills: pd.DataFrame, rows: pd.DataFrame) -> dict[str, object]:
    measured = rows[rows["t_anchor"].notna()][["date", "ticker", "tf"]].drop_duplicates()
    statuses = []
    for row in fills.to_dict("records"):
        cache = pd.read_parquet(_cache_path(pd.Series(row)))
        statuses.append(cache[cache["record_type"] == "status"].iloc[0])
    status_frame = pd.DataFrame(statuses)
    return {
        "population_n": int(len(fills)), "anchored_n": int(len(measured)),
        "anchor_missing_n": int(len(fills) - len(measured)),
        "trade_error_n": int(status_frame["trade_error"].notna().sum()),
        "quote_error_n": int(status_frame["quote_error"].notna().sum()),
        "trade_truncated_n": int(status_frame["trade_truncated"].fillna(0).sum()),
        "quote_truncated_n": int(status_frame["quote_truncated"].fillna(0).sum()),
        "quotes_requested_only_after_anchor": True,
        "trade_cap_total": TRADE_CAP, "quote_cap_total": QUOTE_CAP,
        "secondary_A3b": "not fetched: different B values imply different anchors/quote windows, so overlap is not a free cache reuse",
    }


def _write_outputs(fills: pd.DataFrame) -> None:
    records = []
    iex_lookup = _iex_lookup(fills)
    for _, row in fills.iterrows():
        records.extend(_measure_fill(row, iex_lookup))
    rows = pd.DataFrame(records)
    if rows.empty:
        raise RuntimeError("no anchored fills measured")
    rows.to_parquet(OUT_PARQUET, index=False)
    search, search_counts = _search(rows)
    output = {
        "study": "PRE-REG-MICRO-01 Study B — decision-anchored post-fill microstructure",
        "population": "frozen OOS fills; primary gate",
        "flags": {"measurement": True, "seen_data": True, "not_an_alpha_claim": True},
        "friction": FRICTION,
        "latency_ladder": {
            str(delta): {"earliest_possible_action_s": delta, "live_poll_action_s": delta + 60}
            for delta in DELTAS
        },
        "coverage": _coverage(fills, rows),
        "feature_stats": _feature_stats(rows),
        "rescue_2x2": _rescue_counts(rows),
        "baselines": _baseline_payload(),
        "rule_search": {
            "grid": "numeric features at empirical q25/q50/q75 with <= and >=; boolean 0/1; all cross-feature 2-rule ANDs; exit-now and tighten-to-action-price",
            "features": {"numeric": list(NUMERIC_FEATURES), "boolean": list(BOOLEAN_FEATURES)},
            "multiple_comparison_warning": "All rules were searched on fully seen OOS data; winners are descriptive and cannot be adopted without a new pre-registration and fresh OOS.",
            "counts": search_counts, "per_rung": search,
        },
        "gate": _gate(search),
    }
    OUT_JSON.write_text(json.dumps(output, indent=1, default=str, allow_nan=True) + "\n")
    _print_summary(output)


def _print_summary(output: dict[str, object]) -> None:
    print("delta lag pop    n selected  baseEV    ruleEV   dEV   tail↓ targetCost erosion rule")
    per_rung = output["rule_search"]["per_rung"]
    for population in ("all", "pf2"):
        for delta in map(str, DELTAS):
            for lag in map(str, LAGS):
                result = per_rung[population][delta][lag]
                best = result["best_gate_eligible"] or result["best"]
                if best is None:
                    continue
                print(
                    f"{delta:>5s} {lag:>3s} {population:4s} {result['n']:4d} {best['n_selected']:8d} "
                    f"{best['baseline_ev']:+.4f} {best['intervention_ev']:+.4f} "
                    f"{best['ev_change']:+.4f} {best['tail_reduced']:5d} "
                    f"{best['target_cost_sum']:10.3f} {best['target_economics_erosion']:6.1%} {best['rule']} [{best['mode']}]"
                )
    print(f"GATE: {output['gate']['verdict']}")


def _client() -> StockHistoricalDataClient:
    load_dotenv(ROOT / ".env")
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY missing")
    return StockHistoricalDataClient(key, secret)


def _self_test() -> None:
    trades = [(0.0, 10.2), (2.0, 10.0), (3.0, 9.9)]
    assert _first_anchor(trades, 10.0) == 2.0
    frame = pd.DataFrame({
        "timestamp": pd.to_datetime([
            "2099-01-02T14:30:00Z", "2099-01-02T14:30:02Z", "2099-01-02T14:30:06Z",
        ], utc=True),
        "price": [10.2, 10.0, 11.2], "size": [1.0, 2.0, 3.0],
    })
    anchor = _anchor_timestamp(frame, pd.Timestamp("2099-01-02T14:30:00Z"), 10.0)
    assert anchor == pd.Timestamp("2099-01-02T14:30:02Z")
    assert _rescue_class(
        frame, anchor, anchor + pd.Timedelta(seconds=3),
        anchor + pd.Timedelta(seconds=30), 9.0, 11.111,
    ) == "alive-later-target"


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
    fills = _attach_b(pd.read_parquet(FILLS_PATH))
    missing = [row for _, row in fills.iterrows() if not _cache_path(row).exists()]
    if missing:
        client = _client()
        limit = len(missing) if arguments.max_new <= 0 else min(arguments.max_new, len(missing))
        for index, row in enumerate(missing[:limit], start=1):
            _fetch_fill(client, row, _cache_path(row))
            if index % 20 == 0 or index == limit:
                print(f"cached {index}/{limit}; remaining {len(missing) - index}", flush=True)
    remaining = sum(not _cache_path(row).exists() for _, row in fills.iterrows())
    if remaining:
        print(f"cache incomplete: {remaining}/{len(fills)} fills remain")
        return
    if not arguments.fetch_only:
        _write_outputs(fills)


if __name__ == "__main__":
    main()
