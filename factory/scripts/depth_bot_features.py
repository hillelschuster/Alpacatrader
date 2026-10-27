from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import joblib
import numpy as np
import polars as pl

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from alpha_open_learned import FEATURES_ALL, feature_matrix  # noqa: E402
from alpha_open_panel import SNAPSHOTS, symbol_rows  # noqa: E402
from alpha_open_sim import causal_liquidity  # noqa: E402
from alpha_sparse_daily import day_context  # noqa: E402

MODEL_SHA256 = "c5493c6c043a22e544ca98aa4fb5620292d4a1aeb370f63f6492907334f0bf96"
MODEL_PATH = Path.home() / "alpha-data/open-search-v1/learned/models/payoff_h60.joblib"
SIGNAL_THRESHOLD = 0.030
BAR_COLUMNS = ("ticker", "et", "open", "high", "low", "close", "volume")


def load_model(path: Path = MODEL_PATH):
    """Load the immutable h60 model only after its bytes and feature contract are checked."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != MODEL_SHA256:
        raise ValueError(f"payoff_h60 sha256 mismatch: {digest}")
    bundle = joblib.load(path)
    if list(bundle.get("feature_order") or []) != list(FEATURES_ALL):
        raise ValueError("payoff_h60 feature order differs from FEATURES_ALL")
    if int(bundle.get("horizon", -1)) != 60:
        raise ValueError("payoff_h60 horizon is not 60")
    booster = bundle["lgbm"]
    names = list(getattr(booster, "feature_name_", []) or [])
    generic = [f"Column_{i}" for i in range(len(FEATURES_ALL))]
    if names and names not in (list(FEATURES_ALL), generic):
        raise ValueError("payoff_h60 booster feature names do not match its frozen order")
    if int(getattr(booster, "n_features_in_", len(FEATURES_ALL))) != len(FEATURES_ALL):
        raise ValueError("payoff_h60 feature count differs from FEATURES_ALL")
    return booster


class IncrementalDepthFeatures:
    """Causal day state; callers add only bars known at the decision instant."""

    def __init__(self, day: str, session_end: int = 959, model=None):
        self.day = day
        self.session_end = int(session_end)
        self.model = model
        self._bars: dict[str, list[dict]] = {}
        self._admissions: dict[str, list[tuple[int, float]]] = {}
        self._ranks: dict[int, dict[str, int]] = {}
        self._snapshots_done: set[int] = set()

    @property
    def admissions(self) -> dict[str, list[tuple[int, float]]]:
        return {ticker: list(values) for ticker, values in self._admissions.items()}

    @property
    def ranks(self) -> dict[int, dict[str, int]]:
        return {minute: dict(values) for minute, values in self._ranks.items()}

    def add_bars(self, bars: pl.DataFrame) -> None:
        """Append a minute of raw bars; duplicate (ticker, et) observations are rejected."""
        missing = set(BAR_COLUMNS) - set(bars.columns)
        if missing:
            raise ValueError(f"bars missing columns: {sorted(missing)}")
        for row in bars.select(BAR_COLUMNS).sort(["ticker", "et"]).to_dicts():
            ticker = row["ticker"]
            series = self._bars.setdefault(ticker, [])
            if series and int(row["et"]) <= int(series[-1]["et"]):
                raise ValueError(f"bars must arrive once in increasing order: {ticker} {row['et']}")
            series.append(row)

    def _snapshot(self, minute: int) -> None:
        if minute in self._snapshots_done:
            return
        # The panel ranks stale last closes too; freshness gates admission, not top-10 rank.
        observations = []
        for ticker, rows in self._bars.items():
            if not rows or int(rows[0]["et"]) != 570:
                continue
            eligible = [row for row in rows if 570 <= int(row["et"]) <= minute - 1]
            if not eligible:
                continue
            last = eligible[-1]
            et, price = int(last["et"]), float(last["close"])
            if price < 1.0:
                continue
            gain = price / float(rows[0]["open"]) - 1.0
            observations.append((ticker, gain, price, et))
        observations.sort(key=lambda item: (-item[1], item[0]))
        top = observations[:10]
        ranks = {ticker: rank for rank, (ticker, *_rest) in enumerate(top, start=1)}
        self._ranks[minute] = ranks
        for ticker, gain, price, et in top:
            # Candidate snapshots serialize scores to six decimals; the panel consumes that value.
            score = round(gain, 6)
            if score >= 0.05 and price >= 1.0 and et >= minute - 2:
                self._admissions.setdefault(ticker, []).append((minute, score))
        self._snapshots_done.add(minute)

    def feature_frame(self, t: int) -> pl.DataFrame:
        """Return all panel feature rows at t, before the liquidity state filter."""
        t = int(t)
        if t in SNAPSHOTS:
            self._snapshot(t)
        path_rows = []
        for ticker, admissions in self._admissions.items():
            frame = pl.DataFrame(self._bars[ticker]).sort("et")
            row = next(
                (
                    r
                    for r in reversed(
                        symbol_rows(
                            self.day, ticker, frame, admissions, self._ranks, self.session_end
                        )
                    )
                    if int(r["t"]) == t
                ),
                None,
            )
            if row is not None:
                path_rows.append(row)
        return day_context(pl.DataFrame(path_rows)) if path_rows else pl.DataFrame()

    def decision(self, t: int) -> pl.DataFrame:
        """Build liquidity-qualified features and h60 predictions for lattice time t."""
        features = self.feature_frame(t)
        if not features.height:
            return features
        qualified = features.filter(causal_liquidity(features))
        if qualified.is_empty():
            return qualified.with_columns(pl.Series("pred_60", [], dtype=pl.Float64))
        if self.model is None:
            raise ValueError("a loaded h60 model is required to score rows")
        predictions = np.asarray(self.model.predict(feature_matrix(qualified)), dtype=np.float64)
        return qualified.with_columns(pl.Series("pred_60", predictions))

    def signals(self, t: int) -> pl.DataFrame:
        scored = self.decision(t)
        return scored.filter(pl.col("pred_60") >= SIGNAL_THRESHOLD) if scored.height else scored
