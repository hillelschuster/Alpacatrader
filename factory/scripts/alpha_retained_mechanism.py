#!/usr/bin/env python3
"""Descriptive mechanism profile of the retained sparse h60 candidate.

Joins the two immutable trade artifacts of the sparse exploratory probe
(2023 validation: 61 known fills; 2025-02..2026-05 late block: 143 known fills,
h=60, thr=0.03, model fit 2021-2022 only) to the CAUSAL panel state (day, ticker, t)
and to the FIXED stored h60 forecast, then records, per trade:

* past-only features (26) and the entry model score / forecast / native SHAP contribs;
* entry/exit model PnL: the stored forecast is a GROSS prediction, so it is calibrated
  against the realized gross proxy it was fit on (calibration_error_gross), while the
  realized net after the trades' own round-trip cost model is a separate economic
  result (net_after_proxy_friction) and never a calibration label;
* matched as-of quote economics for the quoted pairs: the touch price return
  (exit_bid/entry_ask-1) vs the fixed bar-price gross proxy, the integer-share idle
  rounding effect and the cost-model difference are separately named and sum exactly,
  row by row, to the touch-minus-proxy net difference.

DESCRIPTIVE ONLY. No pick is filtered, dropped or re-scored using any outcome; band
tables characterize states already taken, they never veto or select. The late block
was previously explored, so this is DISCOVERY-NOT-VALIDATED, not a confirmation.

Quote handling reuses the immutable lead audit rules (alpha_quote_audit.asof_quote,
250 ms arrival latency, 2.0 s max quote age): the LATE block reads the frozen
learned_sparse_extension/quote_confirmation artifact unchanged; the VALIDATION block
has no lead artifact, so the same function is applied read-only to local 2023 quotes
and labelled worker_asof_same_rules. Unresolved quotes are NEVER assumed zero.
No news/catalyst column exists in the panel and none is inferred anywhere.

Cost models are kept separate and named: the immutable trades charge a flat 100 bps
round-trip on the bar-price gross return, while the immutable quote audit prices the
as-of ASK/BID touch on a 0/25/50/100 bps residual ladder (25 bps is the smallest
declared surcharge reported here). Their difference is a cost-model difference, not a
price effect, and the quoted spread is charged exactly once, inside the touch prices,
never re-charged as full-width legs.

Outputs: <out>/profile.json (concise profile) and <out>/paired_features.parquet
(all joined rows with state, forecast, contribs and quote fields).
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

import joblib
import numpy as np
import polars as pl
from alpha_open_learned import FEATURES_ALL, PANEL_ROOT, feature_matrix, sha256_file
from alpha_open_panel import ROOT, allowed
from alpha_open_sim import causal_liquidity, load_panel
from alpha_quote_audit import asof_quote, clock_us

# ----- fixed configuration ------------------------------------------------------
STUDY = "alpha_retained_mechanism"
STATUS = "DISCOVERY-NOT-VALIDATED"
HORIZON = 60
EXPECTED_PANEL_DAYS = 1066
EXPECTED_TRADES = {"VALIDATION": 61, "LATE": 143}
ORDER_BUDGET = 1000.0
LATENCY_MS = 250
MAX_AGE_S = 2.0
PERIODS = ("VALIDATION", "LATE")
QUOTED_STATUS = "quoted_capacity_supported_not_fill_guaranteed"
# smallest declared residual rung of the immutable quote audit's 0/25/50/100 bps ladder,
# priced as side = bps/20_000 on both legs (mirrors alpha_quote_audit exactly)
TOUCH_RESIDUAL_BPS = 25.0

# fixed, declared broad bands (never optimized on outcomes); (name, lo_inclusive, hi_exclusive)
PRICE_BANDS = (("1-3", None, 3.0), ("3-5", 3.0, 5.0), ("5-10", 5.0, 10.0), (">=10", 10.0, None))
TOD_BANDS = (("morning", 570, 600), ("midday", 600, 720), ("late", 720, None))
DD_BANDS = ((">-2%", -0.02, None), ("-2..-10%", -0.10, -0.02), ("<=-10%", None, -0.10))
VWAP_BANDS = (("<=0%", None, 0.0), ("0..+5%", 0.0, 0.05), (">+5%", 0.05, None))
RET3_BANDS = (("<=0%", None, 0.0), ("0..+3%", 0.0, 0.03), (">+3%", 0.03, None))
RET15_BANDS = (("<=0%", None, 0.0), ("0..+5%", 0.0, 0.05), (">+5%", 0.05, None))
DV_BANDS = (("<1 slowing", None, 1.0), ("1..2", 1.0, 2.0), (">=2 accelerating", 2.0, None))
RANK_BANDS = (("<=3", None, 3.0), ("4..7", 3.0, 8.0), (">=8", 8.0, None))
# (band column, causal source column, bounds)
BAND_DIMS = (
    ("price_band", "entry_open", PRICE_BANDS),
    ("tod", "t", TOD_BANDS),
    ("dd_band", "dd_day_high", DD_BANDS),
    ("vwap_band", "vwap_dist", VWAP_BANDS),
    ("ret3_band", "ret3", RET3_BANDS),
    ("ret15_band", "ret15", RET15_BANDS),
    ("dv_accel_band", "dv_accel", DV_BANDS),
    ("rank_band", "rank_snapshot", RANK_BANDS),
)
# mechanism verdict thresholds (fixed, declared; description, not a decision rule)
CHASE_RET3 = 0.02
DEEP_DD = -0.10
DEEP_VWAP = 0.05

MECH_COLS = (
    "gain_open",
    "admit_gain",
    "ret1",
    "ret3",
    "ret15",
    "dd_high15",
    "dd_day_high",
    "vwap_dist",
    "dv_accel",
    "range15",
    "since_high",
    "admission_age",
    "rank_snapshot",
    "log_price",
    "log_cum_dv",
)


def band_expr(source: str, bands: tuple) -> pl.Expr:
    expr = pl.lit(None)
    for name, lo, hi in reversed(bands):
        test = pl.lit(True)
        if lo is not None:
            test = test & (pl.col(source) >= lo)
        if hi is not None:
            test = test & (pl.col(source) < hi)
        expr = pl.when(test).then(pl.lit(name)).otherwise(expr)
    return expr


def with_bands(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns([band_expr(src, bands).alias(col) for col, src, bands in BAND_DIMS])


# ----- immutable trade artifacts --------------------------------------------------
def load_trades(panel: Path) -> tuple[pl.DataFrame, dict]:
    ext = panel / "learned_sparse_extension"
    files = {
        "VALIDATION": ext / "trades_validation.parquet",
        "LATE": ext / "trades_confirmation_100.parquet",
    }
    loaded, stats = [], {}
    for tag in PERIODS:
        path = files[tag]
        if not path.exists():
            raise SystemExit(f"[{STUDY}] immutable trades missing: {path}")
        frame = pl.read_parquet(path)
        if frame.height != EXPECTED_TRADES[tag]:
            raise SystemExit(
                f"[{STUDY}] {path} has {frame.height} rows, expected {EXPECTED_TRADES[tag]}"
            )
        if any(not allowed(d) for d in frame["day"].to_list()):
            raise SystemExit(f"[{STUDY}] protected day inside {path}")
        if set(frame["status"].unique().to_list()) != {"known_open_proxy"}:
            raise SystemExit(f"[{STUDY}] unexpected trade statuses in {path}")
        if frame["net"].null_count() or frame["gross"].null_count():
            raise SystemExit(f"[{STUDY}] null outcomes in {path}")
        stats[tag] = {
            "path": str(path),
            "sha256": sha256_file(path),
            "rows": int(frame.height),
            "days": int(frame["day"].n_unique()),
            "months": int(frame["day"].str.slice(0, 7).n_unique()),
            "day_min": frame["day"].min(),
            "day_max": frame["day"].max(),
            "horizon": int(frame["horizon"].unique().to_list()[0]),
            "cost_bps": float(frame["cost_bps"].unique().to_list()[0]),
        }
        loaded.append(frame.with_columns(pl.lit(tag).alias("period")))
    trades = pl.concat(loaded).with_row_index("source_row")
    return trades.sort(["day", "ticker", "t"]).with_row_index("row_id"), stats


# ----- quote coverage -------------------------------------------------------------
def lead_quote_join(trades: pl.DataFrame, panel: Path) -> pl.DataFrame:
    """LATE block: read the frozen lead audit rows unchanged (never recompute)."""
    path = panel / "learned_sparse_extension" / "quote_confirmation" / "rows.parquet"
    if not path.exists():
        raise SystemExit(f"[{STUDY}] lead quote audit missing: {path}")
    return (
        pl.read_parquet(path)
        .select(
            [
                "day",
                "ticker",
                "entry_et",
                "status",
                "entry_ask",
                "exit_bid",
                "entry_spread_bps",
                "quantity",
                "quoted_net_0",
                "quoted_net_25",
            ]
        )
        .rename({"status": "quote_status"})
    ), path


def quoted_net_residual(ask: float, bid: float, residual_bps: float) -> float:
    """$ORDER_BUDGET ROI at one residual rung, mirroring alpha_quote_audit exactly:
    paid shares sized at ask*(1+side), proceeds at bid*(1-side), side = bps/20_000."""
    side = residual_bps / 20_000.0
    paid = int(ORDER_BUDGET // (ask * (1 + side)))
    return (paid * bid * (1 - side) - paid * ask * (1 + side)) / ORDER_BUDGET


def worker_quote_audit(trades: pl.DataFrame, data: Path) -> pl.DataFrame:
    """VALIDATION block: the same immutable as-of rules, applied read-only.

    Mirrors alpha_quote_audit parameters exactly (250 ms latency, 2.0 s max age,
    regular-condition quotes only, displayed-size capacity check after the
    2025-11-03 Alpaca unit change, which maps 2023 displayed sizes x100).
    """
    val = trades.filter(pl.col("period") == "VALIDATION")
    requests: dict[str, dict[str, set[int]]] = {}
    for r in val.iter_rows(named=True):
        requests.setdefault(r["day"], {}).setdefault(r["ticker"], set()).add(r["entry_et"])
        if r["exit_et"] is not None:
            requests[r["day"]][r["ticker"]].add(r["exit_et"])
    quotes = {}
    for day, tickers in sorted(requests.items()):
        if not allowed(day):
            raise SystemExit(f"[{STUDY}] protected quote read refused: {day}")
        path = data / "sip" / "net" / "quotes" / f"{day}.parquet"
        if not path.exists():
            continue
        q = pl.read_parquet(path).filter(pl.col("symbol").is_in(list(tickers)))
        groups = q.sort("symbol", "ts_utc").partition_by("symbol", as_dict=True)
        for key, f in groups.items():
            ticker = key[0]
            for minute in tickers[ticker]:
                quotes[(day, ticker, minute)] = asof_quote(
                    f, clock_us(day, minute, LATENCY_MS), day, MAX_AGE_S
                )
        del q, groups
    rows = []
    for r in val.iter_rows(named=True):
        rec = {
            "row_id": r["row_id"],
            "quote_status": None,
            "entry_ask": None,
            "exit_bid": None,
            "entry_spread_bps": None,
            "quantity": None,
            "quoted_net_0": None,
            "quoted_net_25": None,
        }
        entry, ewhy = quotes.get(
            (r["day"], r["ticker"], r["entry_et"]), (None, "quote_not_acquired")
        )
        exit_q, xwhy = quotes.get(
            (r["day"], r["ticker"], r["exit_et"]), (None, "quote_not_acquired")
        )
        if entry is None or exit_q is None:
            rec["quote_status"] = f"entry:{ewhy};exit:{xwhy}"
            rows.append(rec)
            continue
        quantity = int(ORDER_BUDGET // entry["ask"])
        rec.update(
            {
                "entry_ask": entry["ask"],
                "exit_bid": exit_q["bid"],
                "entry_spread_bps": entry["spread_bps"],
                "quantity": quantity,
            }
        )
        if quantity < 1 or quantity > entry["ask_shares"] or quantity > exit_q["bid_shares"]:
            rec["quote_status"] = "unknown_top_of_book_capacity"
        else:
            rec["quote_status"] = QUOTED_STATUS
            rec["quoted_net_0"] = (quantity * (exit_q["bid"] - entry["ask"])) / ORDER_BUDGET
            rec["quoted_net_25"] = quoted_net_residual(
                entry["ask"], exit_q["bid"], TOUCH_RESIDUAL_BPS
            )
        rows.append(rec)
    return pl.DataFrame(rows, infer_schema_length=None)


# ----- paired quote economics -------------------------------------------------------
def with_pair_economics(frame: pl.DataFrame) -> pl.DataFrame:
    """Row-wise touch-vs-proxy split for matched quoted pairs; unknown stays null.

    Every quantity keeps its own name: `actual_price_return` is the per-share touch
    ratio exit_bid/entry_ask-1, `quoted_net_0` is the $ORDER_BUDGET ROI at the integer
    share count (idle cash earns nothing) and the two are never equated. The identity
    actual_touch_net_25 - net == pure_price_effect + idle_rounding_effect + fee_effect
    holds row by row; unresolved pairs stay null, never zero.
    """
    quoted = pl.col("quote_status") == QUOTED_STATUS
    touch = pl.col("exit_bid") / pl.col("entry_ask") - 1
    return frame.with_columns(
        [
            pl.when(quoted).then(touch).otherwise(None).alias("actual_price_return"),
            pl.when(quoted)
            .then(touch - pl.col("gross"))
            .otherwise(None)
            .alias("pure_price_effect"),
            pl.when(quoted)
            .then(pl.col("quoted_net_0") - touch)
            .otherwise(None)
            .alias("idle_rounding_effect"),
            pl.when(quoted)
            .then(pl.col("quoted_net_25"))
            .otherwise(None)
            .alias("actual_touch_net_25"),
            pl.when(quoted)
            .then(pl.col("quoted_net_25") - pl.col("net"))
            .otherwise(None)
            .alias("net_difference"),
            pl.when(quoted)
            .then(
                (pl.col("gross") - pl.col("net"))
                - (pl.col("quoted_net_0") - pl.col("quoted_net_25"))
            )
            .otherwise(None)
            .alias("fee_effect"),
        ]
    )


def pair_economics_block(frame: pl.DataFrame) -> dict:
    """Means of the exact split over the period's quoted pairs (n=0 when none)."""
    cov = frame.filter(
        (pl.col("quote_status") == QUOTED_STATUS) & pl.col("quoted_net_25").is_not_null()
    )
    if not cov.height:
        return {"n": 0, "note": "no quoted pairs in this period"}

    def mean(col: str) -> float:
        return round(float(cov[col].mean()), 6)

    residual = (
        cov["actual_touch_net_25"]
        - cov["net"]
        - (cov["pure_price_effect"] + cov["idle_rounding_effect"] + cov["fee_effect"])
    )
    return {
        "n": int(cov.height),
        "mean_actual_price_return": mean("actual_price_return"),
        "mean_proxy_gross_bar_prices": mean("gross"),
        "mean_pure_price_effect": mean("pure_price_effect"),
        "mean_quoted_budget_return_at_0_integer_shares": mean("quoted_net_0"),
        "mean_idle_rounding_effect": mean("idle_rounding_effect"),
        "mean_actual_touch_net_25": mean("actual_touch_net_25"),
        "mean_net_after_proxy_friction": mean("net"),
        "mean_net_difference_touch_minus_proxy": mean("net_difference"),
        "mean_fee_effect": mean("fee_effect"),
        "dollars_proxy_net_at_order_budget": round(float(cov["net"].sum()) * ORDER_BUDGET, 2),
        "dollars_actual_touch_net_25_at_order_budget": round(
            float(cov["actual_touch_net_25"].sum()) * ORDER_BUDGET, 2
        ),
        "identity_max_abs_residual": round(float(residual.abs().max()), 12),
        "cost_models": {
            "proxy": (
                f"immutable trades' own round-trip cost (cost_bps="
                f"{cov['cost_bps'][0]}) on the bar-price gross return"
            ),
            "actual_touch": (
                f"as-of ASK/BID touch at the immutable audit's "
                f"{TOUCH_RESIDUAL_BPS:.0f} bps residual rung; the quoted "
                "spread is charged once, inside the touch prices"
            ),
            "fee_effect": (
                "proxy gross-to-net friction wedge minus the touch rung "
                "surcharge: a cost-model difference, not a price effect"
            ),
        },
        "note": (
            "quoted pairs only; unresolved pairs stay UNKNOWN and are never "
            "assumed zero, so this cannot certify the whole period portfolio"
        ),
    }


# ----- descriptive tables -----------------------------------------------------------
def band_table(frame: pl.DataFrame, col: str, bands: tuple) -> list[dict]:
    out = []
    for name, _, _ in bands:
        sub = frame.filter(pl.col(col) == name)
        out.append(
            {
                "band": name,
                "n": int(sub.height),
                "days": int(sub["day"].n_unique()),
                "months": int(sub["day"].str.slice(0, 7).n_unique()),
                "dollars": round(float(sub["net"].sum()) * ORDER_BUDGET, 2),
                "mean_net": round(float(sub["net"].mean()), 5) if sub.height else None,
                "median_net": round(float(sub["net"].median()), 5) if sub.height else None,
            }
        )
    return out


def win_loss(frame: pl.DataFrame) -> dict:
    wins = frame.filter(pl.col("net") > 0)
    losses = frame.filter(pl.col("net") <= 0)
    sw, sl = float(wins["net"].sum()), float(-losses["net"].sum())
    return {
        "n": int(frame.height),
        "mean_net": round(float(frame["net"].mean()), 5),
        "median_net": round(float(frame["net"].median()), 5),
        "mean_gross": round(float(frame["gross"].mean()), 5),
        "wins": {
            "n": int(wins.height),
            "mean_net": round(float(wins["net"].mean()), 5) if wins.height else None,
        },
        "losses": {
            "n": int(losses.height),
            "mean_net": round(float(losses["net"].mean()), 5) if losses.height else None,
        },
        "win_rate": round(wins.height / frame.height, 4),
        "payoff_ratio": round(sw / sl, 4) if sl > 0 else None,
        "worst_fill_net": round(float(frame["net"].min()), 5),
        "best_fill_net": round(float(frame["net"].max()), 5),
    }


def feature_medians(frame: pl.DataFrame, cols: tuple[str, ...]) -> dict:
    return {c: round(float(frame[c].median()), 6) for c in cols} if frame.height else {}


def mechanism_verdict(med: dict) -> dict:
    """Fixed-rule descriptive classification; economics stay [INFERENCE], not proof."""
    if med["ret3"] >= CHASE_RET3 and med["ret15"] >= 0.0:
        label = "positive_chase"
    elif med["dd_day_high"] <= DEEP_DD and med["vwap_dist"] >= DEEP_VWAP:
        label = "deep_drawdown_recovery_in_high_volatility_uptrend"
    else:
        label = "other_state"
    return {
        "label": label,
        "evidence": med,
        "interpretation": (
            f"[INFERENCE] Entry states sit {med['gain_open']:+.1%} above the open, "
            f"{med['dd_day_high']:+.1%} off the day high, {med['vwap_dist']:+.1%} vs VWAP, "
            f"with short-window returns {med['ret3']:+.2%} (3m) / {med['ret15']:+.2%} (15m) "
            f"and volume acceleration {med['dv_accel']:.2f}x; classified as {label} by the "
            "fixed declared rules, not by outcome filtering."
        ),
    }


def day_contributions(frame: pl.DataFrame, k: int = 5) -> dict:
    per_day = (
        frame.group_by("day")
        .agg((pl.col("net") * ORDER_BUDGET).sum().alias("dollars"), pl.len().alias("fills"))
        .sort("dollars", descending=True)
    )
    top = [
        {"day": r["day"], "dollars": round(r["dollars"], 2), "fills": r["fills"]}
        for r in per_day.head(k).iter_rows(named=True)
    ]
    worst = [
        {"day": r["day"], "dollars": round(r["dollars"], 2), "fills": r["fills"]}
        for r in per_day.tail(k).sort("dollars").iter_rows(named=True)
    ]
    gaps = (
        frame.sort("net")
        .head(k)
        .select(
            [
                "day",
                "ticker",
                "t",
                "entry_open",
                "exit_open_proxy",
                "gross",
                "net",
                "score",
                "dd_day_high",
                "ret3",
                "vwap_dist",
            ]
        )
    )
    return {
        "top_days": top,
        "worst_days": worst,
        "worst_gaps_fills": [dict(r) for r in gaps.iter_rows(named=True)],
    }


def selection_bias(frame: pl.DataFrame) -> dict:
    quoted = frame.filter(pl.col("quote_status") == QUOTED_STATUS)
    unresolved = frame.filter(pl.col("quote_status") != QUOTED_STATUS)

    def view(sub: pl.DataFrame) -> dict:
        return {
            "n": int(sub.height),
            "proxy_net_mean": round(float(sub["net"].mean()), 5) if sub.height else None,
            "proxy_net_median": round(float(sub["net"].median()), 5) if sub.height else None,
            "proxy_net_sd": round(float(sub["net"].std()), 5) if sub.height > 1 else None,
            "dollars": round(float(sub["net"].sum()) * ORDER_BUDGET, 2),
            "mean_score": round(float(sub["score"].mean()), 5) if sub.height else None,
        }

    bias = {
        "quoted": view(quoted),
        "unresolved": view(unresolved),
        "unresolved_treated_as_zero": False,
        "proxy_mean_gap_quoted_minus_unresolved": (
            round(float(quoted["net"].mean() - unresolved["net"].mean()), 5)
            if quoted.height and unresolved.height
            else None
        ),
        "feature_medians": {
            "quoted": feature_medians(
                quoted, ("log_cum_dv", "log_price", "dv_accel", "range15", "entry_open")
            ),
            "unresolved": feature_medians(
                unresolved, ("log_cum_dv", "log_price", "dv_accel", "range15", "entry_open")
            ),
        },
    }
    slip = frame.filter(pl.col("entry_ask").is_not_null()).with_columns(
        (pl.col("entry_ask") / pl.col("entry_open") - 1).alias("entry_slip")
    )
    bias["entry_ask_vs_entry_bar"] = {
        "n": int(slip.height),
        "mean": round(float(slip["entry_slip"].mean()), 6),
        "median": round(float(slip["entry_slip"].median()), 6),
        "p90": round(float(slip["entry_slip"].quantile(0.9)), 6),
        "max": round(float(slip["entry_slip"].max()), 6),
        "unit": (
            "fraction of entry minute-open bar; positive means the ask sits ABOVE "
            "the bar open, i.e. paying up at the touch, never cheaper than the bar "
            "unless a paired quote proves the particular case"
        ),
    }
    cov = quoted.filter(pl.col("quoted_net_0").is_not_null())
    if cov.height:
        delta = float((cov["quoted_net_0"] - cov["net"]).mean())
        bias["covered_quote_vs_proxy_delta_mean"] = round(delta, 6)
        bias["covered_quote_vs_proxy_delta_note"] = (
            "quoted budget return at integer shares minus the proxy net; the two use "
            "different cost models (proxy flat fee vs touch residual rung) on top of "
            "the price and idle effects, so this is NOT pure favorable price "
            "improvement - the split is in pair_economics"
        )
        if unresolved.height:
            blended = (
                float(cov["net"].mean()) * cov.height
                + (float(unresolved["net"].mean()) + delta) * unresolved.height
            ) / frame.height
            bias["sensitivity_if_unresolved_carry_covered_delta"] = {
                "value": round(blended, 6),
                "assumption": (
                    "unresolved proxy mean shifted by the covered group's "
                    "quote-minus-proxy delta; [INFERENCE] only, unresolved are "
                    "never set to zero"
                ),
            }
    return bias


def quote_block(frame: pl.DataFrame) -> dict:
    counts = (
        frame.group_by("quote_status")
        .agg(pl.len().alias("n"), pl.col("net").mean().alias("proxy_net_mean"))
        .sort("quote_status")
    )
    n_quoted = int((frame["quote_status"] == QUOTED_STATUS).sum())
    return {
        "status_counts": [
            {
                "status": r["quote_status"],
                "n": r["n"],
                "proxy_net_mean": round(r["proxy_net_mean"], 5),
            }
            for r in counts.iter_rows(named=True)
        ],
        "quoted_pairs": n_quoted,
        "unresolved_pairs": int(frame.height - n_quoted),
        "selection_bias": selection_bias(frame),
        "pair_economics": pair_economics_block(frame),
    }


def examples(
    frame: pl.DataFrame, contrib_cols: list[str], tag: str, source: dict, k: int = 3
) -> list[dict]:
    if tag == "strongest":
        subset = frame.sort("net", descending=True).head(k)
    elif tag == "weakest":
        subset = frame.sort("net").head(k)
    else:
        subset = frame.sort(["day", "ticker", "t"]).head(k)
    out = []
    for r in subset.iter_rows(named=True):
        contribs = sorted(((c, r[c]) for c in contrib_cols), key=lambda kv: -abs(kv[1]))[:3]
        state_val = {c: r[c] for c in ("ret3", "ret15", "dd_day_high", "vwap_dist", "dv_accel")}
        out.append(
            {
                "day": r["day"],
                "ticker": r["ticker"],
                "t": r["t"],
                "entry_open": r["entry_open"],
                "exit_open_proxy": r["exit_open_proxy"],
                "gross": round(float(r["gross"]), 5),
                "net": round(float(r["net"]), 5),
                "score_h60_pred": round(float(r["score"]), 5),
                "quote_status": r["quote_status"],
                "causal_state": {k2: round(float(v), 5) for k2, v in state_val.items()},
                "top_native_contribs": [
                    {"feature": c.removeprefix("contrib_"), "contrib": round(float(v), 6)}
                    for c, v in contribs
                ],
                "pointer": {
                    "paired_features_row_id": int(r["row_id"]),
                    "immutable_source": source["path"],
                    "immutable_source_row": int(r["source_row"]),
                },
                "news_or_catalyst": "not present in panel; not inferred",
            }
        )
    return out


# ----- main -------------------------------------------------------------------------
def run(args: argparse.Namespace) -> None:
    t0 = time.time()
    panel, out, data = args.panel, args.out or args.panel / STUDY, args.data
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), out / "producer_snapshot.py")

    trades, trade_stats = load_trades(panel)
    model_path = panel / "learned" / "models" / f"payoff_h{HORIZON}.joblib"
    if not model_path.exists():
        raise SystemExit(f"[{STUDY}] stored model missing: {model_path}")
    bundle = joblib.load(model_path)
    if list(bundle.get("feature_order") or []) != list(FEATURES_ALL):
        raise SystemExit(f"[{STUDY}] stored feature_order != current FEATURES_ALL")
    model = bundle["lgbm"]

    frame, calendar = load_panel(panel)
    if len(calendar) != EXPECTED_PANEL_DAYS:
        raise SystemExit(
            f"[{STUDY}] requires the full {EXPECTED_PANEL_DAYS}-day corpus, got {len(calendar)}"
        )
    panel_contract = panel / "contract.json"
    prov = {
        "panel_root": str(panel),
        "panel_contract_sha256": sha256_file(panel_contract),
        "panel_days": len(calendar),
        "panel_rows": int(frame.height),
        "rows_liquidity_qualified": int(frame.filter(causal_liquidity(frame)).height),
        "trades": trade_stats,
        "model": {
            "path": str(model_path),
            "sha256": sha256_file(model_path),
            "horizon": int(bundle["horizon"]),
            "feature_order": list(bundle["feature_order"]),
            "params": bundle.get("params"),
            "clip_fit": bundle.get("clip_fit"),
            "seed": bundle.get("seed"),
        },
        "producer_sha256": sha256_file(Path(__file__)),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "causality": {
            "features_use_bars_strictly_before_t": True,
            "last_actual_bar_for_features": "t-1",
            "entry": "minute-t open proxy; entry_et == t",
            "exit_rule": "first actual open >= min(t+60, session_end); absent => UNKNOWN",
            "model_target_clip_fit_only": bundle.get("clip_fit"),
            "peer_context_aggregate_includes_own": True,
            "outcome_filtering": "none; every immutable trade retained",
        },
    }

    joined = trades.join(
        frame.select(["day", "ticker", "t", "admit_t", "session_end", *FEATURES_ALL]),
        on=["day", "ticker", "t"],
        how="left",
    )
    unmatched = int(joined["log_price"].null_count())
    if unmatched:
        raise SystemExit(f"[{STUDY}] {unmatched} trades did not join the causal panel state")
    x = feature_matrix(joined)
    pred = np.asarray(model.predict(x), dtype=float)
    contrib = np.asarray(model.booster_.predict(x, pred_contrib=True), dtype=float)
    if contrib.shape[1] != len(FEATURES_ALL) + 1:
        raise SystemExit(f"[{STUDY}] unexpected pred_contrib width {contrib.shape}")
    joined = joined.with_columns(
        pl.Series("h60_pred_gross", pred),
        pl.Series("pred_residual_gross", joined["gross"].to_numpy() - pred),
    )
    for i, c in enumerate(FEATURES_ALL):
        joined = joined.with_columns(pl.Series(f"contrib_{c}", contrib[:, i]))
    score_gap = float(np.max(np.abs(joined["score"].to_numpy() - pred)))
    prov["join_integrity"] = {
        "joined_rows": int(joined.height),
        "unmatched": unmatched,
        "score_vs_stored_h60_pred_max_abs_diff": score_gap,
        "stored_score_is_h60_forecast": bool(score_gap < 1e-6),
    }

    lead_rows, lead_path = lead_quote_join(trades, panel)
    prov["quote_artifact_late"] = {"path": str(lead_path), "sha256": sha256_file(lead_path)}
    late_q = joined.filter(pl.col("period") == "LATE").join(
        lead_rows, on=["day", "ticker", "entry_et"], how="left"
    )
    worker_rows = worker_quote_audit(joined, data)
    val_q = joined.filter(pl.col("period") == "VALIDATION").join(
        worker_rows, on="row_id", how="left"
    )
    full = pl.concat([late_q, val_q], how="diagonal_relaxed")
    full = full.with_columns(
        pl.when(pl.col("entry_ask").is_not_null())
        .then(pl.col("entry_ask") / pl.col("entry_open") - 1)
        .otherwise(None)
        .alias("entry_slip")
    )
    full = (
        with_pair_economics(with_bands(full))
        .drop("row_id")
        .sort(["day", "ticker", "t"])
        .with_row_index("row_id")
    )

    paired_cols = [
        "row_id",
        "period",
        "day",
        "ticker",
        "t",
        "admit_t",
        "entry_et",
        "entry_open",
        "exit_et",
        "exit_open_proxy",
        "horizon",
        "gross",
        "net",
        "cost_bps",
        "order_budget",
        "score",
        "h60_pred_gross",
        "pred_residual_gross",
        "status",
        "price_band",
        "tod",
        "dd_band",
        "vwap_band",
        "ret3_band",
        "ret15_band",
        "dv_accel_band",
        "rank_band",
        "quote_status",
        "entry_ask",
        "exit_bid",
        "entry_spread_bps",
        "quantity",
        "quoted_net_0",
        "actual_price_return",
        "pure_price_effect",
        "idle_rounding_effect",
        "actual_touch_net_25",
        "net_difference",
        "fee_effect",
        "entry_slip",
    ] + [f"contrib_{c}" for c in FEATURES_ALL]
    paired_path = out / "paired_features.parquet"
    full.select([c for c in paired_cols if c in full.columns]).write_parquet(paired_path)

    profile = {
        "study": STUDY,
        "status": STATUS,
        "purpose": (
            "descriptive mechanism profile of the retained sparse h60/.03 candidate; "
            "no filtering, no refit, no promotion"
        ),
        "calibration": {
            "fit_label": "realized gross proxy (bar open to exit bar open, no friction)",
            "rule": (
                "predict_gross is compared with realized_gross only; "
                "calibration_error_gross = predict_gross - realized_gross"
            ),
            "economics_separate": (
                "net_after_proxy_friction is the immutable trades' own "
                "round-trip cost model and is never the calibration label"
            ),
        },
        "provenance": prov,
        "band_definitions": {
            col: [(n, lo, hi) for n, lo, hi in bands] for col, _, bands in BAND_DIMS
        },
        "periods": {},
        "mechanism": {},
        "quote": {},
        "examples": {},
        "native_contribs": {
            "method": "lightgbm booster_.predict(pred_contrib=True) on the fixed h60 model",
            "note": "feature attribution on selected states; NOT causal proof",
            "expected_value_bias": float(contrib[0, -1]),
            "median_by_feature": {
                c: round(float(full[f"contrib_{c}"].median()), 6) for c in FEATURES_ALL
            },
            "mean_abs_top5": dict(
                sorted(
                    (
                        (c, round(float(np.abs(full[f"contrib_{c}"].to_numpy()).mean()), 6))
                        for c in FEATURES_ALL
                    ),
                    key=lambda kv: -kv[1],
                )[:5]
            ),
        },
    }

    for tag in PERIODS:
        sub = full.filter(pl.col("period") == tag)
        med = feature_medians(sub, MECH_COLS)
        profile["mechanism"][tag] = {
            "all": mechanism_verdict(med),
            "win_medians": feature_medians(sub.filter(pl.col("net") > 0), MECH_COLS),
            "loss_medians": feature_medians(sub.filter(pl.col("net") <= 0), MECH_COLS),
            "months": int(sub["day"].str.slice(0, 7).n_unique()),
        }
        profile["periods"][tag] = {
            "n": int(sub.height),
            "traded_days": int(sub["day"].n_unique()),
            "months": int(sub["day"].str.slice(0, 7).n_unique()),
            "dollars": round(float(sub["net"].sum()) * ORDER_BUDGET, 2),
            "predict_gross": round(float(sub["h60_pred_gross"].mean()), 5),
            "realized_gross": round(float(sub["gross"].mean()), 5),
            "calibration_error_gross": round(
                float((sub["h60_pred_gross"] - sub["gross"]).mean()), 5
            ),
            "net_after_proxy_friction": round(float(sub["net"].mean()), 5),
            "win_loss": win_loss(sub),
            "bands": {col: band_table(sub, col, bands) for col, _, bands in BAND_DIMS},
            "days": day_contributions(sub),
            "quote": quote_block(sub),
        }
        profile["examples"][tag] = {
            "strongest": examples(
                sub, [f"contrib_{c}" for c in FEATURES_ALL], "strongest", trade_stats[tag]
            ),
            "weakest": examples(
                sub, [f"contrib_{c}" for c in FEATURES_ALL], "weakest", trade_stats[tag]
            ),
        }
        if tag == "LATE":
            profile["examples"][tag]["chronological_prefix"] = examples(
                sub, [f"contrib_{c}" for c in FEATURES_ALL], "prefix", trade_stats[tag], k=6
            )

    profile["quote"]["late_77_vs_66"] = {
        "source": "immutable learned_sparse_extension/quote_confirmation artifact",
        **profile["periods"]["LATE"]["quote"],
    }
    profile["quote"]["validation_worker_asof_same_rules"] = profile["periods"]["VALIDATION"][
        "quote"
    ]
    profile["hypotheses"] = [
        {
            "id": "H1",
            "priced_without_retuning": True,
            "text": (
                "[INFERENCE] The earned head buys a deep-drawdown-recovery state inside "
                "extreme intraday uptrends (entry ~+60% vs open, ~-15% off day high, "
                "~+13% vs VWAP, short-window returns near zero/negative), not fresh "
                "positive chase: wins show cooler ret3/ret15 and lower volume "
                "acceleration than losses in BOTH periods."
            ),
        },
        {
            "id": "H2",
            "priced_without_retuning": True,
            "text": (
                "[INFERENCE] Quote availability is endogenous to state: the 77 quote-covered "
                "late trades differ from the 66 unresolved on proxy return and on "
                "liquidity/price features, so the covered mean cannot certify the "
                "143-trade portfolio; the bounded sensitivity is in "
                "quote.late_77_vs_66.selection_bias."
            ),
        },
        {
            "id": "H3",
            "priced_without_retuning": True,
            "text": (
                "[INFERENCE] TOD and price-band heterogeneity is descriptive only (late-TOD "
                "and 3-5/>=10 bands carry the late dollars; the 1-3 band is negative); "
                "pricing any subset needs no model change, just replay of the immutable "
                "trades."
            ),
        },
    ]
    profile["data_questions"] = [
        "Are 2023 quotes acquirable at other arrival latencies / max ages for the "
        "VALIDATION block under the same as-of rules (worker-side, no audit changes)?",
        "Do the dd-recovery states persist on longer causal lookbacks (ret30/ret45, 45m "
        "drawdown) absent from the frozen 26 features? Would need a panel rebuild, not a refit.",
        "Does entry ask-vs-bar slippage scale with the displayed-depth shortfall of the 30 "
        "capacity-unknown late trades (rows.parquet carries the sizes)?",
    ]
    profile["runtime_s"] = round(time.time() - t0, 1)
    profile_path = out / "profile.json"
    profile_path.write_text(json.dumps(profile, indent=2, default=str) + "\n")

    for tag in PERIODS:
        p = profile["periods"][tag]
        print(
            f"[{tag}] n={p['n']} days={p['traded_days']} months={p['months']} "
            f"dollars={p['dollars']} net_after_friction={p['net_after_proxy_friction']} "
            f"pred_gross={p['predict_gross']} realized_gross={p['realized_gross']} "
            f"calib_err_gross={p['calibration_error_gross']} "
            f"payoff={p['win_loss']['payoff_ratio']}",
            flush=True,
        )
        print(f"  mechanism: {profile['mechanism'][tag]['all']['label']}", flush=True)
        for dim in ("price_band", "tod"):
            cells = ", ".join(f"{r['band']}:{r['n']}/{r['dollars']}" for r in p["bands"][dim])
            print(f"  {dim}: {cells}", flush=True)
        slip = p["quote"]["selection_bias"]["entry_ask_vs_entry_bar"]
        print(f"  quote ask-vs-bar slip: n={slip['n']} mean={slip['mean']}", flush=True)
    sb = profile["quote"]["late_77_vs_66"]["selection_bias"]
    print(
        f"[late-quote] quoted_proxy_mean={sb['quoted']['proxy_net_mean']} "
        f"unresolved_proxy_mean={sb['unresolved']['proxy_net_mean']} "
        f"gap={sb['proxy_mean_gap_quoted_minus_unresolved']}",
        flush=True,
    )
    pe = profile["quote"]["late_77_vs_66"]["pair_economics"]
    if pe.get("n"):
        print(
            f"[late-quote-econ] n={pe['n']} touch_net25_minus_proxy_net="
            f"{pe['mean_net_difference_touch_minus_proxy']} "
            f"price={pe['mean_pure_price_effect']} "
            f"idle={pe['mean_idle_rounding_effect']} "
            f"fee={pe['mean_fee_effect']} "
            f"identity_residual={pe['identity_max_abs_residual']}",
            flush=True,
        )
    print(f"[done] {profile_path} + {paired_path} ({profile['runtime_s']}s)", flush=True)


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--panel",
        type=Path,
        default=PANEL_ROOT,
        help="panel root containing learned/ and learned_sparse_extension/ (default: %(default)s)",
    )
    p.add_argument(
        "--out", type=Path, default=None, help="output root (default: <panel>/retained_mechanism)"
    )
    p.add_argument(
        "--data",
        type=Path,
        default=ROOT / "data",
        help="SIP data root for the worker as-of VALIDATION quotes (default: %(default)s)",
    )
    args = p.parse_args()
    run(args)


if __name__ == "__main__":
    main()
