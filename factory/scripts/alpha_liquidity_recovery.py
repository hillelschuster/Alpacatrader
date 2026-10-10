#!/usr/bin/env python3
"""MINUTE-scale liquidity recovery of liquid strong gainers after observable transient selling.

A NEW micro-entry family on the raw SIP tick/NBBO substrate (alpha_micro_core) with holds of
300 SECONDS (5 MINUTES) and 900 SECONDS (15 MINUTES). It is NOT the failed 60-second
momentum-reclaim family, NOT an H025 discount-limit fill, and it never targets a same-bar
high: the exit is a deterministic time-based MARKET BID at entry+hold, priced from the first
observable firm quote at/after the due clock.

ALL durations in this module are SECONDS (hold views are 300s and 900s = 5 and 15 MINUTES);
the "last 3 minute" dip window is 180 SECONDS; the aggressor-flow window is the core's
5-second classified imbalance; the post-exit cooldown is 300 SECONDS. No minute-valued
default horizon exists anywhere: every execution call takes an explicit horizon_seconds.

Signal (every quantity strictly past-only at the signal second, on the core one-second
states over the FULL RTH grid - admission minute through the last second whose next-second
intent arrival is inside the calendar session end, half days respected, no 13:00-style
framework cutoff; the next SECOND is the intent, so no feature ever sees the entry clock):
  setup:
    px              >= 5              causal last eligible print price
    cum_dv          >= 10_000_000     cumulative session eligible-print dollars
    day_gain        >= 0.10           px / same-day SIP open (o570) - 1
    fresh                           core: last print <=2s AND strictly-prior NBBO valid, R-only
    spread          <= 25 bps         displayed NBBO spread (bps of price)
    ret180s         <= -0.02          price down >=2% over the trailing 180 SECONDS
    imbalance_hist_min <= -0.10      trailing-180s minimum of the 5s classified aggressor-flow
                                     imbalance: the recent flow was net SELLING
    imbalance5s     >= +0.10          5-second classified aggressor flow now net BUYING (flip)
    bid_shares      >= 2x trailing-180s minimum displayed bid size   (bid replenishment)
    epoch           >= 180 SECONDS after admission (full trailing windows observable)
  confirmation (two FROZEN alternatives, each edge-triggered to the FIRST qualified second
  after the flow flip inside one flip episode):
    flip_first_positive_1s: first second of the episode with a positive 1-SECOND price change
    bid_depth_2x_ask:       first second of the episode with displayed bid_shares >= 2x ask_shares

Entry: IOC LIMIT buy. The intent is the NEXT SECOND after the signal second; limit and qty
are fixed from the intent-clock quote BEFORE the +250ms arrival (limit = ceil(ask*1.01) to
the cent as a marketability cap, qty funded at the 150bps rung). At arrival the latest RAW
NBBO is evaluated ONCE: fresh and ask<=limit with displayed depth covering qty -> conditional
L1 fill; ask>limit -> modeled no-L1-match (unfilled cash); partial depth, stale-but-valid,
invalid or nonfirm arrival -> execution UNKNOWN. An IOC entry NEVER rests to a later print
and is never resized. Exit: MARKET sell due at arrival+hold (bounded by the session-end
clock); fresh-quote-gated submission, priced from the latest RAW at submission+250ms; a
MARKET order rests only when the book is genuinely unavailable (invalid/nonregular), never
solely because a valid print is aged; an aged-but-valid arrival is UNKNOWN. No target, no
stop, no favourable future print, no cheapest-quote selection.

Account: equal $250 orders, 3 concurrent slots ($750 research sub-book, margin-style funded
reuse), RTH only, a ticker never overlaps itself, max 5 attempts per ticker/day, a 300 SECOND
(5 MINUTE) cooldown after an actual exit, UNKNOWN positions reserved to the session end and
charged a full-loss lower bound (never quiet cash). All intents sharing one clock are funded
BEFORE any arrival outcome is observed; no same-clock substitution after an earlier intent
is learned unfilled.

Periods (own power windows): development 2021-05..2021-10 (six fully R-covered months),
validation ALL 2023, confirmation 2025-09..2026-05 (nine months; previously explored by bar
studies, not a pristine holdout). Costs are residual round-trip bps ON TOP of the observed
ASK/BID prices on the ladder 0/5/10/25/50/75/100/125/150: 25 bps is the primary realistic
rung, the 0/5/10 rungs are actual provider-fee scenarios (not free fills), 25..150 are the
sensitivity ladder. There is no fixed 100bps hurdle: every rung is reported and a cell stays
a positive option if it is positive at a feasible rung.

Raw quote acquisition: the primary SIP net covers the watchlist symbols but with time gaps
(~54% of qualified 2023 / ~51% of late one-second states carry a fresh quote).

Universe: the shared alpha_micro_core tick loader is called ONCE per day with an EXPLICIT
causal watch (full_watch): every completed full-PIT B snapshot's top TEN ranks with
score>=0.05, px>=$1 and a causally-stamped price (T-2 <= px_et <= T), valued by the symbol's
FIRST admission minute. That is wider than the parent micro_core.admissions top-3 / px>=5
policy the legacy studies pin, and no admission narrowing is inherited: the family's own
px>=5, cum_dv>=$10M, day-gain>=10% and spread gates are evaluated on past-only per-second
SIGNAL state, and a candidate that never passes them remains a coverage-requested name
(the denominator counts requested names, never the streams that survived). A top-ten
candidate with no raw trade/quote stream is data-UNKNOWN for the whole date, never a quiet
cash day.

Optional
--supplement roots - plus the discovered ~/alpha-data/open-search-v1/quote_universe_acquire/
quotes cache owned by alpha_quote_universe_acquire.py - may ADD quote timestamps only, with
the original PRIMARY_KEEPFIRST (a supplement never replaces or revises a print) and a
per-file sha256 supplement digest inside the per-day resume hash. A historical stream no
source covers is acquisition UNKNOWN: the whole date is coverage-UNKNOWN (never known cash)
and an unobservable intent/exit clock is UNKNOWN, never a favourable print. Quote coverage
is never used as an entry filter (no future-availability filtering).

Usage:
    uv run --no-sync python factory/scripts/alpha_liquidity_recovery.py \
        --command run --out /home/hillel/alpha-data/open-search-v1/liquidity_recovery_five_min
    uv run --no-sync python factory/scripts/alpha_liquidity_recovery.py \
        --command report --out /home/hillel/alpha-data/open-search-v1/liquidity_recovery_five_min
    uv run --no-sync python factory/scripts/alpha_liquidity_recovery.py --command signalcondition
    (per-day build is resume-hash pinned: --days <day> re-checks single days, --force rebuilds)
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import itertools
import json
import math
import os
import time
from collections import Counter
from pathlib import Path

import alpha_micro_core
import numpy as np
import polars as pl
from alpha_micro_core import (
    COVERAGE_EPOCH,
    ORDER_BUDGET,
    coverage_is_complete,
    coverage_kind,
    load_day,
    qindex,
    states,
)
from alpha_open_panel import ROOT, SNAPSHOTS, allowed
from alpha_quote_audit import clock_us
from sip_bars import AUCTION_CODES, combine

DATA = ROOT / "data"
CALENDAR = ROOT / "factory" / "artifacts" / "basket" / "sip" / "phase2_session_calendar.json"
UNIT_EPOCH = "2025-11-03"  # raw quote sizes are round lots (x100) before this date
# Neutral ALL-PIT missing-symbol acquisition cache owned by alpha_quote_universe_acquire.py
# (a sibling producer): <root>/<day>.parquet with the standard SIP raw quote columns.
DEFAULT_SUPPLEMENT_ROOT = (
    Path.home() / "alpha-data" / "open-search-v1" / "quote_universe_acquire" / "quotes"
)
OUT_DEFAULT = Path.home() / "alpha-data" / "open-search-v1" / "liquidity_recovery_five_min"
VERSION = 1
BOOT_SEED = 20261009
BOOT_DRAWS = 1000

# --- account book (research sub-book) ------------------------------------------
MAX_SLOTS = 3
BOOK = ORDER_BUDGET * MAX_SLOTS  # 3 x $250 funded orders = $750 research sub-book
MAX_ATTEMPTS_PER_TICKER_DAY = 5
COOLDOWN_SECONDS = 300  # 5 MINUTES after an actual exit before the same ticker may re-enter

# --- execution timing -----------------------------------------------------------
INTENT_NEXT_SECOND_US = 1_000_000  # the intent is the NEXT SECOND after the signal second
LATENCY_US = 250_000  # +250 ms arrival after the intent/submission clock
MAX_AGE_S = 2.0  # operational freshness gate; NOT a claim that an older NBBO expired
TICK_DECIMALS = 2
LIMIT_CAP = 0.01  # pre-declared marketability cap: observed ASK * 1.01, cent-rounded UP

# --- declared hold views (SECONDS) ----------------------------------------------
HOLD_SECONDS = (300, 900)  # 300s = 5 MINUTES; 900s = 15 MINUTES; explicit, no default

# --- frozen confirmation variants ------------------------------------------------
CONFIRMATIONS = ("flip_first_positive_1s", "bid_depth_2x_ask")

# --- cost ladder: residual round-trip bps ON TOP of the observed ASK/BID --------
COSTS = (0.0, 5.0, 10.0, 25.0, 50.0, 75.0, 100.0, 125.0, 150.0)
PRIMARY_COST = 25.0  # primary realistic rung; 0/5/10 are actual provider-fee scenarios

# --- frozen family thresholds ----------------------------------------------------
PRICE_MIN = 5.0
CUM_DV_MIN = 10_000_000.0
DAY_GAIN_MIN = 0.10
SPREAD_MAX_BPS = 25.0  # displayed NBBO spread <= 25 bps
DIP_SECONDS = 180  # "last 3 minute" = 180 SECONDS
DIP_RET_MAX = -0.02  # trailing 180s return <= -2%
FLOW_WINDOW_SECONDS = 5  # core classified one-second imbalance5s window
FLOW_NEG_MAX = -0.10  # recent 5s aggressor flow was net selling (>=10% sell imbalance)
FLOW_POS_MIN = 0.10  # 5s aggressor flow now net buying (>=10% buy imbalance): the flip
BID_REPLENISH_MULT = 2.0  # displayed bid size >= 2x the trailing-180s minimum (replenishment)
CONFIRM_DEPTH_MULT = 2.0  # confirmation B: displayed bid_shares >= 2x ask_shares
MIN_EPOCH_SECONDS = 180  # signal seconds only at/after admission + 180 SECONDS

PERIODS = {
    "development": ("2021-05", "2021-06", "2021-07", "2021-08", "2021-09", "2021-10"),
    "validation": tuple(f"2023-{m:02d}" for m in range(1, 13)),
    "confirmation": (
        "2025-09",
        "2025-10",
        "2025-11",
        "2025-12",
        "2026-01",
        "2026-02",
        "2026-03",
        "2026-04",
        "2026-05",
    ),
}

FAMILY = {
    "price_min": PRICE_MIN,
    "cum_dv_min": CUM_DV_MIN,
    "day_gain_min": DAY_GAIN_MIN,
    "day_gain_anchor": "same-day SIP open o570 (data/sip/universe/rth/<day>.parquet)",
    "spread_max_bps": SPREAD_MAX_BPS,
    "dip_seconds": DIP_SECONDS,
    "dip_ret_max": DIP_RET_MAX,
    "flow_window_seconds": FLOW_WINDOW_SECONDS,
    "flow_neg_max": FLOW_NEG_MAX,
    "flow_pos_min": FLOW_POS_MIN,
    "bid_replenish_mult": BID_REPLENISH_MULT,
    "confirm_depth_mult": CONFIRM_DEPTH_MULT,
    "min_epoch_seconds": MIN_EPOCH_SECONDS,
}
ACCOUNT = {
    "order_budget": ORDER_BUDGET,
    "max_slots": MAX_SLOTS,
    "research_book": BOOK,
    "max_attempts_per_ticker_day": MAX_ATTEMPTS_PER_TICKER_DAY,
    "cooldown_seconds": COOLDOWN_SECONDS,
    "rth_only": True,
    "unknown_position_reserved": True,
}
CONFIRMATION_DEFS = {
    "flip_first_positive_1s": "first second inside a flow-flip episode with a positive "
    "1-SECOND price change (ret1s>0), past-only",
    "bid_depth_2x_ask": "first second inside a flow-flip episode with displayed bid_shares "
    ">= 2x ask_shares at the strictly-prior NBBO",
}

CONTRACT = {
    "version": VERSION,
    "hypothesis": "MINUTE-scale liquidity recovery: a liquid strong gainer (px>=5, cum_dv>=$10M, "
    "day gain>=10% from the same-day open, firm spread<=25bps) that sold off >=2% over the "
    "trailing 180 SECONDS with net-selling 5s aggressor flow flips to net-buying 5s aggressor "
    "flow while displayed bid depth replenishes; the FIRST qualified second after the flip "
    "(positive 1-SECOND price) or the first with bid depth >=2x ask depth is bought on an IOC "
    "limit and held 300/900 SECONDS to a deterministic time-based MARKET exit",
    "family": FAMILY,
    "family_novelty": "NEW 5/15-MINUTE recovery family; NOT the failed 60-second momentum "
    "reclaim, NOT an H025 discount-limit fill, and never a same-bar-high target",
    "confirmations": CONFIRMATION_DEFS,
    "qualification": "setup AND one frozen confirmation; first qualified second per flip "
    "episode; up to "
    + str(MAX_ATTEMPTS_PER_TICKER_DAY)
    + " attempts per ticker/day with a "
    + str(COOLDOWN_SECONDS)
    + "s post-exit cooldown",
    "feature_source": "alpha_micro_core one-second states (eligible trade classification and "
    "strictly-prior NBBO/quotes) plus past-only producer columns on the identical 1-second "
    "grid (cum_dv, day_gain vs o570, ret180s, ret1s, trailing-180s flow/depth minima)",
    "state_grid": "one second from the admission minute through the LAST second whose "
    "NEXT-SECOND intent arrival (+250ms) is still inside the calendar session_end (half-day "
    "calendar respected, maximal grid, RTH only); no preset time cap and no 13:00-style "
    "framework cutoff is inherited from the older micro studies",
    "state_extension_validation": "seconds past the core's own 13:00 grid are produced by "
    "core_second_columns (a verbatim continuation of alpha_micro_core.states: same eligible "
    "print classification, qindex strictly-prior quotes, 5/30/60s windows) and are only kept "
    "after a column-by-column validation against the core's frame on the overlap; a drifted "
    "core raises instead of silently diverging",
    "session_bound": "exits are due at arrival+hold SECONDS capped at the session's last "
    "regular clock; entry arrivals are inside RTH by grid construction",
    "supplement_rule": "optional --supplement roots plus the discovered "
    "~/alpha-data/open-search-v1/quote_universe_acquire/quotes cache add quote TIMESTAMPS "
    "only, original PRIMARY_KEEPFIRST (a supplement may add prints, never replace or revise "
    "one); the per-file sha256 supplement digest is part of the resume hash; a missing "
    "historical stream is acquisition UNKNOWN (the whole date is coverage-UNKNOWN and takes "
    "the -100% whole-book floor), never known cash and never a future-availability entry "
    "filter; quote time-gaps that no source covers stay UNKNOWN at the intent/exit clock",
    "watch": "alpha_micro_core.load_day with an EXPLICIT causal watch (full_watch): "
    "every completed full-PIT B snapshot's top TEN ranks with score>=0.05, px>=$1 and a "
    "causal snapshot price T-2<=px_et<=T (never stamped ahead of the snapshot); the "
    "value is the symbol's FIRST admission minute, which starts its one-second grid. "
    "Ranks 4..10 are genuine candidates: the parent alpha_micro_core.admissions top-3 / "
    "px>=5 policy is NOT inherited - this producer's price/dv/gain/spread gates apply on "
    "past-only per-second SIGNAL state, and a candidate that never passes them stays a "
    "coverage-requested name, never a filtered-out one",
    "admission": "explicit full-PIT B top-ten causal watch (see 'watch'), NOT the parent "
    "alpha_micro_core.admissions top-3 default the legacy micro studies pin",
    "selection": "both confirmation variants x both hold views are reported; the "
    "(variant, hold) cell is chosen by 2023 validation mean_daily_lower_bound at "
    f"{int(PRIMARY_COST)}bps residual ONLY, frozen before confirmation; every rung is "
    "reported and no fixed 100bps hurdle is applied",
    "simultaneous_priority": "score = imbalance5s (past-only classified 5s aggressor-flow "
    "imbalance) decides equal-clock capital priority only, never fill availability",
    "entry": f"intent at the NEXT SECOND after the signal second; IOC LIMIT: limit/qty fixed "
    f"from the intent-clock quote BEFORE the +{LATENCY_US // 1000}ms arrival "
    "(limit=ceil(ask*1.01) cent-rounded up marketability cap; qty funded at the "
    f"{int(COSTS[-1])}bps rung); single latest-RAW arrival evaluation: fresh & ask<=limit & "
    "displayed depth covers qty -> conditional L1 fill; ask>limit -> modeled no-L1-match "
    "(unfilled cash); partial/stale/invalid/nonfirm arrival -> UNKNOWN, never rested, never "
    "resized",
    "exit": "MARKET sell due at arrival+hold SECONDS (hold in "
    f"{list(HOLD_SECONDS)}), bounded by the session-end clock; fresh-quote-gated submission; "
    "priced from the latest RAW at submission+250ms; MARKET resting allowed only when the "
    "book is genuinely unavailable (invalid/nonregular), never solely for an aged-but-valid "
    "print; aged-but-valid arrival -> UNKNOWN; exit-side depth must cover qty",
    "declared_hold_views_seconds": list(HOLD_SECONDS),
    "hold_units": "SECONDS: 300s = 5 MINUTES, 900s = 15 MINUTES; horizon_seconds is a "
    "required explicit argument everywhere; there is no minute-valued default horizon",
    "costs_bps_residual_round_trip": list(COSTS),
    "primary_cost_bps": PRIMARY_COST,
    "cost_note": "residual round-trip bps on top of the actual observed ASK/BID quote prices; "
    "the observed spread is already inside the ASK/BID touch and is never double-charged; "
    "0/5/10bps are actual provider-fee scenarios, not free fills",
    "account": ACCOUNT,
    "periods": {k: {"months": list(v)} for k, v in PERIODS.items()},
    "protected_unread": ["2024", "2025-01", "2026-06", "2026-07", "2026-08"],
    "coverage_rule": "a date is complete only if every watched admitted name has usable regular "
    "(R) quotes and a present stream; any quote_condition_unknown ('?') or no-regular-quote "
    "or missing name => the WHOLE date is coverage-UNKNOWN and takes a -100% whole-book floor "
    "(never cash zero); known-complete-day slices are reported separately and never promoted",
    "unknown_rule": "entry/exit quote or L1 depth insufficient => UNKNOWN intent retained, "
    "reserved to the session end and charged a full loss per order; '?' quote conditions are "
    "data-unknown, not proof of no signal",
    "no_grid": "no hyperparameter grid beyond the two frozen confirmations x the two declared "
    "hold views x the cost ladder; no threshold tuning on outcomes",
    "development_window_rationale": "2021 May-Oct are the six fully R-regular-quote-covered "
    "months; 2021 Feb-Apr quotes are all '?' unknown conditions and are deliberately excluded",
    "out_of_fit_not_pristine": True,
    "sibling_isolation": "no other worker's results consulted for selection; shared core "
    "substrate only",
    "core_module": "alpha_micro_core",
    "calendar": str(CALENDAR.relative_to(ROOT)),
}

PANEL_FEATURE_KEYS = (
    "px",
    "ask",
    "bid",
    "ask_shares",
    "bid_shares",
    "spread",
    "fresh",
    "depth_imbalance",
    "ret1s",
    "ret5s",
    "ret30s",
    "ret60s",
    "imbalance5s",
    "imbalance30s",
    "imbalance60s",
    "classified_dv5s",
    "classified_dv30s",
    "classified_dv60s",
    "dv5s",
    "dv30s",
    "dv60s",
    "n5s",
    "n30s",
    "n60s",
    "dd120s",
    "rebound30s",
    "day_gain",
    "cum_dv",
    "ret180s",
    "imbalance_hist_min",
    "bid_hist_min",
    "epoch_index",
)
PANEL_SCHEMA = {
    "day": pl.String,
    "ticker": pl.String,
    "variant": pl.String,
    "signal_us": pl.Int64,
    "intent_us": pl.Int64,
    "score": pl.Float64,
    "selected": pl.Boolean,
    **{k: pl.Float64 for k in PANEL_FEATURE_KEYS if k != "epoch_index"},
    "epoch_index": pl.Int64,
}


# ---------------------------------------------------------------- helpers
def _plain(value):
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (float, np.floating)):
        v = float(value)
        return None if np.isnan(v) else v
    return value


def _default(obj):
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return None if np.isnan(obj) else float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    raise TypeError(f"not serializable: {type(obj)}")


def core_sha256() -> str:
    return hashlib.sha256(Path(alpha_micro_core.__file__).read_bytes()).hexdigest()


def contract_sha256() -> str:
    return hashlib.sha256(json.dumps(CONTRACT, sort_keys=True).encode()).hexdigest()


def period(day: str) -> str:
    for name, months in PERIODS.items():
        if day[:7] in months:
            return name
    raise ValueError(f"day outside fixed periods: {day}")


def resume_hash(day: str, core_sha: str, con_sha: str, sup_digest: str = "") -> str:
    return hashlib.sha256(
        json.dumps(
            [VERSION, COVERAGE_EPOCH, core_sha, con_sha, sup_digest, FAMILY, ACCOUNT, PERIODS, day],
            sort_keys=True,
        ).encode()
    ).hexdigest()


def _write_json_atomic(path: Path, payload: dict) -> None:
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=1, default=_default) + "\n")
    tmp.replace(path)


def _calendar() -> dict:
    return json.loads(CALENDAR.read_text())["evidence"]


def period_days(data: Path, subset: set[str] | None = None) -> dict[str, list[str]]:
    files = sorted(
        p.stem for p in (data / "sip" / "candidates").glob("????-??-??.json") if allowed(p.stem)
    )
    cal = _calendar()
    out: dict[str, list[str]] = {}
    for name, months in PERIODS.items():
        days = [d for d in files if d[:7] in months and d in cal]
        if subset is not None:
            days = [d for d in days if d in subset]
        missing = [d for d in days if d not in cal]
        if missing:
            raise ValueError(f"no session_end evidence for {missing[:3]} in {name}")
        out[name] = days
    return out


def day_open_prices(data_dir: Path, day: str, tickers: list[str]) -> dict[str, float]:
    """Same-day SIP open (o570) for watchlist tickers: the day-gain anchor, past-only."""
    if not tickers:
        return {}
    path = data_dir / "sip" / "universe" / "rth" / f"{day}.parquet"
    if not path.exists():
        return {}
    frame = (
        pl.scan_parquet(path)
        .select("symbol", "o570")
        .filter(pl.col("symbol").is_in(sorted(tickers)))
        .collect()
    )
    return {
        str(s): float(o)
        for s, o in zip(
            frame["symbol"].to_list(), frame["o570"].to_list(), strict=True
        )
        if o is not None and float(o) > 0
    }


# ---------------------------------------------------------------- watch
def full_watch(data_dir: Path, day: str) -> dict[str, int]:
    """FULL-PIT B top-ten causal watch for one day: this producer's universe policy.

    Every completed B snapshot contributes its top TEN ranks, and a rank
    qualifies at snapshot minute T when
        score >= 0.05,
        the snapshot price is >= $1 (the family's own PRICE_MIN=5 gate stays at
        SIGNAL time on past-only per-second state, never here), and
        the price is causally stamped inside the snapshot window: T-2 <= px_et <= T
        (fresh within 2 minutes and never ahead of the snapshot itself).
    The value is the symbol's FIRST admission minute across all declared
    snapshots, so the one-second grid starts at the earliest completed snapshot
    that already admitted it.

    This is deliberately wider than the parent ``alpha_micro_core.admissions``
    top-three / px>=5 policy: ranks 4..10 are genuine candidates and no admission
    narrowing is inherited - a $1-$4 candidate that never reaches the family's
    $5 signal gate stays a coverage-requested name, not a filtered-out one.
    """
    obj = json.loads((data_dir / "sip" / "candidates" / f"{day}.json").read_text())
    out: dict[str, int] = {}
    for snap in obj["snapshots"]:
        t = snap["T"]
        if snap["pop"] != "B" or t not in SNAPSHOTS:
            continue
        for r in snap["top"][:10]:
            et = r.get(f"px_{t}_et", -1)
            if r["score"] >= 0.05 and r.get(f"px_{t}", 0) >= 1 and t - 2 <= et <= t:
                out.setdefault(r["symbol"], t)
    return out


# ---------------------------------------------------------------- supplements
def resolve_supplement_roots(extra: list[list[str]] | None) -> tuple[Path, ...]:
    """Explicit --supplement roots plus the acquisition cache when it exists.

    A root that does not exist contributes nothing (discovered-when-present); explicit
    roots are kept even when empty so the run record shows what was requested.
    """
    roots: list[Path] = []
    for group in extra or []:
        roots.extend(Path(p) for p in group)
    if DEFAULT_SUPPLEMENT_ROOT.exists():
        roots.append(DEFAULT_SUPPLEMENT_ROOT)
    seen, out = set(), []
    for r in roots:
        key = str(r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return tuple(out)


def supplement_entries(roots: tuple[Path, ...]) -> list[dict]:
    """Per-file provenance (sha256 + bytes) of every existing supplement day file."""
    entries = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.glob("????-??-??.parquet")):
            h = hashlib.sha256()
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            entries.append(
                {"path": str(path), "sha256": h.hexdigest(), "bytes": path.stat().st_size}
            )
    return entries


def supplement_digest(roots: tuple[Path, ...]) -> tuple[str, list[dict]]:
    """Digest of the supplement caches that actually exist (drives resume invalidation)."""
    entries = supplement_entries(roots)
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest(), entries


def read_supplement_day(
    roots: tuple[Path, ...], day: str, tickers: list[str]
) -> pl.DataFrame | None:
    """Supplement-only raw quote rows for one day/ticker set (SIP raw quote columns)."""
    if not tickers or not roots:
        return None
    want = ("symbol", "ts_utc", "bid_price", "ask_price", "bid_size", "ask_size", "conditions")
    frames = []
    for root in roots:
        path = root / f"{day}.parquet"
        if not path.exists():
            continue
        frame = (
            pl.scan_parquet(path)
            .filter(pl.col("symbol").is_in(sorted(tickers)))
            .collect()
        )
        missing = [c for c in want if c not in frame.columns]
        if missing:
            raise ValueError(
                f"supplement quote frame {path} missing raw columns {missing}"
            )
        frames.append(frame.select(want))
    if not frames:
        return None
    merged = frames[0] if len(frames) == 1 else pl.concat(frames, how="vertical")
    return merged if merged.height else None


def primary_trade_arrays(data_dir: Path, day: str, ticker: str) -> dict | None:
    """One ticker's eligible-print arrays from the PRIMARY net, under the core's own rules.

    Same condition/tape classification the core's ``load_day`` applies (``sip_bars.combine``
    plus the auction-code exclusion), so a recovered stream's TRADE side is byte-identical
    to what the core itself would build.
    """
    tpath = data_dir / "sip" / "net" / "trades" / f"{day}.parquet"
    if not tpath.exists():
        return None
    frame = pl.scan_parquet(tpath).filter(pl.col("symbol") == ticker).collect()
    if not frame.height:
        return None
    frame = frame.with_columns(pl.col("conditions").list.join("|").alias("ck"))
    rules = []
    for ck, tape in frame.select("ck", "tape").unique().iter_rows():
        conds = ck.split("|") if ck else []
        oc, hl, volume, unknown = combine(conds, tape)
        eligible = oc == 2 and hl == 2 and volume == 2 and not unknown
        eligible = eligible and not any(c in AUCTION_CODES for c in conds)
        rules.append({"ck": ck, "tape": tape, "eligible": eligible})
    if not rules:
        return None
    frame = (
        frame.join(pl.DataFrame(rules), on=["ck", "tape"], how="left")
        .filter(pl.col("eligible"))
        .sort("ts_utc")
    )
    if not frame.height:
        return None
    px = frame["price"].to_numpy()
    if not np.all(np.isfinite(px)) or np.any(px <= 0):
        return None
    return {
        "ts": frame["ts_utc"].cast(pl.Int64).to_numpy(),
        "px": px,
        "size": frame["size"].to_numpy(),
    }


def supplement_quote_arrays(part: pl.DataFrame, day: str) -> dict:
    """Quote arrays derived from supplement prints with the core's mask derivation.

    ``regular`` is the R-only condition mask exactly as the core computes it, and
    ``valid`` adds uncrossed/positive/finite; the day's round-lot multiplier is applied
    to the displayed sizes. A supplement frame lacking the ``conditions`` column never
    reaches here (``read_supplement_day`` refuses it), so a condition field can always be
    derived or the day is reported UNKNOWN with that specific reason.
    """
    multiplier = 100 if day < UNIT_EPOCH else 1
    ts = part["ts_utc"].cast(pl.Int64).to_numpy()
    bid = part["bid_price"].to_numpy().astype(float)
    ask = part["ask_price"].to_numpy().astype(float)
    bs = part["bid_size"].to_numpy().astype(float) * multiplier
    az = part["ask_size"].to_numpy().astype(float) * multiplier
    regular = np.array(
        [bool(c) and all(x == "R" for x in c) for c in part["conditions"].to_list()],
        dtype=bool,
    )
    return {
        "ts": ts,
        "bid": bid,
        "ask": ask,
        "bs": bs,
        "az": az,
        "regular": regular,
        "valid": regular & np.isfinite(bid) & np.isfinite(ask) & (bid > 0) & (ask >= bid),
    }


def recover_supplement_stream(
    data_dir: Path, day: str, ticker: str, admit_t: int, sup_part: pl.DataFrame
) -> tuple[dict | None, str | None]:
    """CREATE a watchlist stream the primary net lacks: primary prints + supplement quotes.

    Used only for symbols the primary net does not carry at all, so PRIMARY_KEEPFIRST
    precedence for primary-covered symbols is untouched. Returns (None, reason) when the
    stream cannot be built - the symbol then stays in the coverage-UNKNOWN missing set
    with that specific reason instead of being silently counted as a partial stream.
    """
    trades = primary_trade_arrays(data_dir, day, ticker)
    if trades is None:
        return None, "primary_trades_unavailable_or_ineligible"
    quotes = supplement_quote_arrays(sup_part.sort("ts_utc"), day)
    if not quotes["regular"].any():
        return None, "supplement_quotes_without_regular_print"
    return (
        {"trades": trades, "quotes": quotes, "admit_t": int(admit_t)},
        None,
    )


def merge_supplement_quotes(q: dict, part: pl.DataFrame, day: str) -> dict:
    """PRIMARY_KEEPFIRST union of supplement prints into one ticker's quote arrays.

    The original print wins at any duplicated timestamp; a supplement may only ADD
    timestamps. The regular/valid masks are recomputed exactly as the core computes them
    (R-only conditions, uncrossed, positive, finite) with the day's round-lot multiplier.
    """
    if part is None or not part.height:
        return q
    multiplier = 100 if day < UNIT_EPOCH else 1
    ts = part["ts_utc"].cast(pl.Int64).to_numpy()
    bid = part["bid_price"].to_numpy().astype(float)
    ask = part["ask_price"].to_numpy().astype(float)
    bs = part["bid_size"].to_numpy().astype(float) * multiplier
    az = part["ask_size"].to_numpy().astype(float) * multiplier
    regular = np.array(
        [bool(c) and all(x == "R" for x in c) for c in part["conditions"].to_list()],
        dtype=bool,
    )
    pos = np.searchsorted(q["ts"], ts)
    inside = pos < len(q["ts"])
    dup = np.zeros(len(ts), dtype=bool)
    if inside.any():
        dup[inside] = q["ts"][pos[inside]] == ts[inside]
    keep = ~dup
    if not keep.any():
        return q
    out = {
        "ts": np.concatenate([q["ts"], ts[keep]]),
        "bid": np.concatenate([q["bid"], bid[keep]]),
        "ask": np.concatenate([q["ask"], ask[keep]]),
        "bs": np.concatenate([q["bs"], bs[keep]]),
        "az": np.concatenate([q["az"], az[keep]]),
        "regular": np.concatenate([q["regular"], regular[keep]]),
    }
    order = np.argsort(out["ts"], kind="stable")
    out = {k: v[order] for k, v in out.items()}
    out["valid"] = (
        out["regular"]
        & np.isfinite(out["bid"])
        & np.isfinite(out["ask"])
        & (out["bid"] > 0)
        & (out["ask"] >= out["bid"])
    )
    return out


# ---------------------------------------------------------------- family features
# Core one-second state columns, in the exact order alpha_micro_core.states emits them
# (ret/imbalance/classified_dv/dv/n interleaved per window, matching the core's dict order).
_CORE_STATE_COLUMNS = (
    "day",
    "ticker",
    "signal_us",
    "px",
    "ask",
    "bid",
    "ask_shares",
    "bid_shares",
    "depth_imbalance",
    "spread",
    "fresh",
    "dd120s",
    "rebound30s",
    "ret5s",
    "imbalance5s",
    "classified_dv5s",
    "dv5s",
    "n5s",
    "ret30s",
    "imbalance30s",
    "classified_dv30s",
    "dv30s",
    "n30s",
    "ret60s",
    "imbalance60s",
    "classified_dv60s",
    "dv60s",
    "n60s",
)


def signal_grid(day: str, stream: dict, session_end: int) -> np.ndarray:
    """The signal-second grid: admission minute through the last second whose NEXT-SECOND
    intent arrival (+250ms) is still inside the calendar session end.

    RTH-only by the calendar (half days respected): no preset time cap and no 13:00-style
    framework cutoff is inherited. The grid is maximal - the last second still leaves a
    valid inside-RTH arrival for its intent.
    """
    start = clock_us(day, stream["admit_t"], 0)
    session_end_us = clock_us(day, session_end, 0)
    last = session_end_us - INTENT_NEXT_SECOND_US - LATENCY_US
    if last < start:
        return np.array([], dtype=np.int64)
    end = start + ((int(last) - int(start)) // 1_000_000) * 1_000_000
    return np.arange(start, end + 1, 1_000_000, dtype=np.int64)


def core_second_columns(stream: dict, grid: np.ndarray, day: str, ticker: str) -> pl.DataFrame:
    """The core's one-second state columns recomputed for an ARBITRARY second grid.

    Verbatim continuation of ``alpha_micro_core.states`` for seconds past the core's own
    13:00 framework cap (which this study deliberately does not inherit): the same
    eligible-print classification, the same strictly-prior quote indexing (``qindex``),
    the same 5/30/60-second windows and the same depth/spread/freshness rules. The head of
    this computation is validated column-by-column against the core's own frame at build
    time, so a drifted core fails loudly instead of silently diverging.
    """
    t, q = stream["trades"], stream["quotes"]
    ti = np.searchsorted(t["ts"], grid, side="left") - 1
    safe = np.maximum(ti, 0)
    qi, qvalid = qindex(q, grid)
    fresh = (ti >= 0) & ((grid - t["ts"][safe]) <= 2_000_000)
    px = t["px"][safe]
    # Aggressor signs use the last quote STRICTLY before each print (core rule).
    tq, tqvalid = qindex(q, t["ts"])
    tqvalid = tqvalid & (q["ask"][tq] > q["bid"][tq])
    dollars = t["px"] * t["size"]
    buy = tqvalid & (t["px"] >= q["ask"][tq])
    sell = tqvalid & (t["px"] <= q["bid"][tq]) & ~buy
    cumulative = {
        "dv": np.r_[0.0, np.cumsum(dollars)],
        "buy": np.r_[0.0, np.cumsum(dollars * buy)],
        "sell": np.r_[0.0, np.cumsum(dollars * sell)],
    }
    features: dict[str, np.ndarray] = {}
    right = np.searchsorted(t["ts"], grid, side="left")
    for seconds in (5, 30, 60):
        left = np.searchsorted(t["ts"], grid - seconds * 1_000_000, side="left")
        old = np.searchsorted(t["ts"], grid - seconds * 1_000_000, side="right") - 1
        features[f"ret{seconds}s"] = px / t["px"][np.maximum(old, 0)] - 1
        bd = cumulative["buy"][right] - cumulative["buy"][left]
        sd = cumulative["sell"][right] - cumulative["sell"][left]
        features[f"imbalance{seconds}s"] = (bd - sd) / np.maximum(bd + sd, 1)
        features[f"classified_dv{seconds}s"] = bd + sd
        features[f"dv{seconds}s"] = cumulative["dv"][right] - cumulative["dv"][left]
        features[f"n{seconds}s"] = right - left
    high120 = pl.Series(px).rolling_max(window_size=120, min_samples=30).to_numpy()
    low30 = pl.Series(px).rolling_min(window_size=30, min_samples=10).to_numpy()
    # Displayed depth is read at the strictly-prior NBBO index (qi): past-only.
    depth_bid, depth_ask = q["bs"][qi], q["az"][qi]
    denom = depth_bid + depth_ask
    safe_denom = np.where(denom > 0, denom, 1.0)
    columns = {
        "day": [day] * len(grid),
        "ticker": [ticker] * len(grid),
        "signal_us": grid,
        "px": px,
        "ask": q["ask"][qi],
        "bid": q["bid"][qi],
        "ask_shares": q["az"][qi],
        "bid_shares": depth_bid,
        "depth_imbalance": np.where(denom > 0, (depth_bid - depth_ask) / safe_denom, 0.0),
        "spread": q["ask"][qi] / np.maximum(q["bid"][qi], 0.0001) - 1,
        "fresh": fresh & qvalid,
        "dd120s": px / high120 - 1,
        "rebound30s": px / low30 - 1,
        **features,
    }
    return pl.DataFrame({k: columns[k] for k in _CORE_STATE_COLUMNS})


def state_frame(day: str, ticker: str, stream: dict, session_end: int) -> pl.DataFrame:
    """Core one-second states over the FULL signal grid (validated past the core's cap).

    The core's own frame (admission..min(780, session_end-1)) stays authoritative for its
    range; seconds past it are produced by ``core_second_columns`` on the identical grid
    and only kept after the extension has been validated against the core's frame on the
    overlap. Early-close days whose grid ends inside the core's range are returned as-is.
    """
    core = states(day, ticker, stream, session_end)
    grid = signal_grid(day, stream, session_end)
    if not len(grid):
        return pl.DataFrame()
    if not core.height:
        return core
    if len(grid) <= core.height:
        # Guard: the signal grid never exceeds the core's own range on a real calendar
        # (it always runs to the last inside-RTH arrival second), but never truncate
        # silently if it ever does.
        return core.head(len(grid)) if len(grid) < core.height else core
    full = core_second_columns(stream, grid, day, ticker).select(core.columns)
    head = full.head(core.height)
    for name in _CORE_STATE_COLUMNS:
        if name in ("day", "ticker"):
            continue
        mine = head[name].to_numpy()
        theirs = core[name].to_numpy()
        if not np.allclose(
            np.asarray(mine, dtype=float),
            np.asarray(theirs, dtype=float),
            equal_nan=True,
            rtol=0,
            atol=1e-12,
        ):
            raise ValueError(
                f"core state extension drifted from alpha_micro_core on {name}: "
                f"{day} {ticker}"
            )
    return pl.concat([core, full.slice(core.height)], how="vertical")


def family_columns(
    day: str, stream: dict, session_end: int, o570: float, imbalance5s: np.ndarray
) -> dict[str, np.ndarray]:
    """Past-only per-second family columns on the IDENTICAL 1-second grid as the states.

    The grid comes from ``signal_grid`` (admission minute through the last second whose
    next-second intent arrival is inside RTH). Every value at second ``g`` uses
    strictly-earlier eligible prints and quotes: cumulative dollars count prints with
    ts < g, ret180s references the last print at/before g-180s, ret1s references the last
    print at/before g-1s, and the depth/flow trailing minima cover the 180 SECONDS
    strictly before g. Invalid/stale quotes are excluded from the bid-depth minimum
    (masked to +inf) instead of being replaced by an older one.
    """
    t, q = stream["trades"], stream["quotes"]
    grid = signal_grid(day, stream, session_end)
    n = len(grid)
    if n != len(imbalance5s):
        raise ValueError(f"family grid {n} != supplied states {len(imbalance5s)}: {day}")
    ts, px = t["ts"], t["px"]
    ti = np.searchsorted(ts, grid, side="left") - 1  # last print strictly before g
    px_g = px[np.maximum(ti, 0)]
    px_valid = ti >= 0
    # cumulative session eligible-print dollars strictly before g
    dollars = px * t["size"]
    cum = np.concatenate(([0.0], np.cumsum(dollars)))
    cum_dv = cum[np.searchsorted(ts, grid, side="left")]
    # trailing 180s return; the reference print must exist at/before g-180s
    ref_i = np.searchsorted(ts, grid - DIP_SECONDS * 1_000_000, side="right") - 1
    ref_px = px[np.maximum(ref_i, 0)]
    ret180s = px_g / ref_px - 1.0
    ret180s_valid = (ref_i >= 0) & px_valid
    # 1-second price change vs the last print at/before g-1s
    prev1_i = np.searchsorted(ts, grid - 1_000_000, side="right") - 1
    ret1s = px_g / px[np.maximum(prev1_i, 0)] - 1.0
    ret1s_valid = (prev1_i >= 0) & px_valid
    # strictly-prior quote state per second (same index rule as the core states)
    qi = np.searchsorted(q["ts"], grid, side="left") - 1
    safe_q = np.maximum(qi, 0)
    q_valid = (qi >= 0) & q["valid"][safe_q] & ((grid - q["ts"][safe_q]) <= 2_000_000)
    # trailing 180 SECONDS minima, strictly before g (rolling window shifted by one second)
    imb_hist = (
        pl.Series(np.asarray(imbalance5s, dtype=float))
        .rolling_min(window_size=DIP_SECONDS, min_samples=1)
        .shift(1)
        .fill_null(float("-inf"))
        .to_numpy()
    )
    bid_masked = np.where(q_valid, q["bs"][safe_q], np.inf)
    bid_hist = (
        pl.Series(bid_masked)
        .rolling_min(window_size=DIP_SECONDS, min_samples=1)
        .shift(1)
        .fill_null(float("inf"))
        .to_numpy()
    )
    return {
        "signal_us": grid,
        "day_gain": px_g / float(o570) - 1.0,
        "cum_dv": cum_dv,
        "ret180s": ret180s,
        "ret180s_valid": ret180s_valid,
        "ret1s": ret1s,
        "ret1s_valid": ret1s_valid,
        "px_valid": px_valid,
        "epoch_index": np.arange(n, dtype=np.int64),
        "imbalance_hist_min": imb_hist,
        "bid_hist_min": bid_hist,
    }


def setup_expr() -> pl.Expr:
    """Past-only setup gate (dip, liquidity, replenishment, spread, freshness) at a second."""
    return (
        (pl.col("px") >= PRICE_MIN)
        & pl.col("px_valid")
        & (pl.col("cum_dv") >= CUM_DV_MIN)
        & (pl.col("day_gain") >= DAY_GAIN_MIN)
        & pl.col("fresh")
        & (pl.col("spread") <= SPREAD_MAX_BPS / 10_000.0)
        & (pl.col("ret180s") <= DIP_RET_MAX)
        & pl.col("ret180s_valid")
        & pl.col("ret1s_valid")
        & (pl.col("bid_shares") >= BID_REPLENISH_MULT * pl.col("bid_hist_min"))
        & (pl.col("epoch_index") >= MIN_EPOCH_SECONDS)
    )


def flow_flip(imbalance5s: np.ndarray, imbalance_hist_min: np.ndarray) -> np.ndarray:
    """The 5-second classified aggressor-flow sign flip negative -> positive (past-only)."""
    return (imbalance5s >= FLOW_POS_MIN) & (imbalance_hist_min <= FLOW_NEG_MAX)


def first_after_flip(flip: np.ndarray, qualified: np.ndarray) -> np.ndarray:
    """Edge-triggered selection: the FIRST qualified second inside each flip episode."""
    n = len(flip)
    out = np.zeros(n, dtype=bool)
    if n == 0:
        return out
    turned = flip & ~np.r_[False, flip[:-1]]
    episode = np.cumsum(turned)
    last_episode = 0
    for i in np.flatnonzero(qualified):
        e = int(episode[i])
        if e != last_episode:
            out[i] = True
            last_episode = e
    return out


def variant_qualified(
    variant: str, flip: np.ndarray, setup: np.ndarray, frame: pl.DataFrame
) -> np.ndarray:
    """Frozen confirmation conjunction per variant (flow flip AND variant condition AND setup)."""
    if variant == "flip_first_positive_1s":
        cond = frame["ret1s"].to_numpy() > 0
    elif variant == "bid_depth_2x_ask":
        ask_shares = frame["ask_shares"].to_numpy()
        cond = frame["bid_shares"].to_numpy() >= CONFIRM_DEPTH_MULT * ask_shares
    else:
        raise ValueError(f"unknown confirmation variant: {variant}")
    return flip & np.asarray(cond, dtype=bool) & np.asarray(setup, dtype=bool)


# ---------------------------------------------------------------- execution
def limit_price_of(ask: float) -> float:
    """Pre-declared marketability cap: observed ASK * 1.01 rounded UP to the cent."""
    return math.ceil(float(ask) * (1.0 + LIMIT_CAP) * 10**TICK_DECIMALS) / 10**TICK_DECIMALS


def causal_quantity(limit_price: float) -> int:
    """Fee-funded integer quantity from the INTENT limit price (never the arrival price)."""
    return int(ORDER_BUDGET // (float(limit_price) * (1.0 + COSTS[-1] / 20_000.0)))


def _latest_raw_index(q: dict, target_us: int) -> int:
    """Engine latest_raw semantics: the latest print at/before target_us (at-or-before)."""
    return int(np.searchsorted(q["ts"], int(target_us), side="right")) - 1


def _is_valid_regular(q: dict, i: int) -> bool:
    # The core stream's `valid` mask already implies regular (R-only), uncrossed, positive.
    return i >= 0 and bool(q["valid"][i])


def _is_firm_fresh(q: dict, i: int, at_us: int) -> bool:
    if not _is_valid_regular(q, i):
        return False
    return (int(at_us) - int(q["ts"][i])) <= MAX_AGE_S * 1_000_000


def _first_eligible_at_or_after(q: dict, start_us: int, end_us: int) -> int:
    """First valid (R-only, uncrossed) print at/after start_us, bounded by end_us."""
    i = int(np.searchsorted(q["ts"], int(start_us), side="left"))
    n = len(q["ts"])
    while i < n and int(q["ts"][i]) <= int(end_us):
        if bool(q["valid"][i]):
            return i
        i += 1
    return -1


def entry_leg(q: dict, intent_us: int) -> dict:
    """IOC LIMIT buy: intent fixes limit/qty BEFORE arrival; one arrival evaluation, no rest."""
    arrival_us = int(intent_us) + LATENCY_US
    out = {
        "intent_status": None,
        "order_state": None,
        "intent_ask": None,
        "limit_price": None,
        "qty": 0,
        "entry_et": arrival_us,
        "entry_ask": None,
        "entry_bid": None,
        "entry_age_s": None,
        "entry_quote_us": None,
        "entry_status": None,
    }
    ii = _latest_raw_index(q, intent_us)
    if not _is_valid_regular(q, ii):
        out["intent_status"] = "no_firm_regular_quote_at_intent"
        out["order_state"] = "cash_skip"
        return out
    if (int(intent_us) - int(q["ts"][ii])) > MAX_AGE_S * 1_000_000:
        out["intent_status"] = "intent_not_firm_fresh"
        out["order_state"] = "cash_skip"
        return out
    intent_ask = float(q["ask"][ii])
    limit = limit_price_of(intent_ask)
    qty = causal_quantity(limit)
    out["intent_status"] = "quoted"
    out["intent_ask"] = intent_ask
    out["limit_price"] = limit
    out["qty"] = qty
    if qty < 1:
        out["entry_status"] = "no_order_min_capital"
        out["order_state"] = "unknown"
        return out
    ai = _latest_raw_index(q, arrival_us)
    if not _is_firm_fresh(q, ai, arrival_us):
        out["entry_status"] = (
            "unknown_no_prior_quote_at_arrival"
            if ai < 0
            else "unknown_stale_or_invalid_at_arrival"
        )
        out["order_state"] = "unknown"
        return out
    ask = float(q["ask"][ai])
    out["entry_ask"] = ask
    out["entry_bid"] = float(q["bid"][ai])
    out["entry_age_s"] = (arrival_us - int(q["ts"][ai])) / 1_000_000
    out["entry_quote_us"] = int(q["ts"][ai])
    if ask > limit + 1e-12:
        # The pre-declared marketability cap is violated at arrival: modeled no-L1-match.
        out["entry_status"] = "no_match_at_l1"
        out["order_state"] = "unfilled"
        return out
    if float(q["az"][ai]) < qty:
        out["entry_status"] = "unknown_entry_partial_depth"
        out["order_state"] = "unknown"
        return out
    out["entry_status"] = "conditional_ioc_fill"
    out["order_state"] = "filled"
    return out


def exit_leg(
    q: dict, entry_arrival_us: int, horizon_seconds: int, session_end_us: int, qty: int
) -> dict:
    """MARKET sell due at arrival+horizon_seconds (bounded by session end); fresh-gated submit."""
    h = int(horizon_seconds)
    due_us = min(int(entry_arrival_us) + h * 1_000_000, int(session_end_us))
    out = {
        f"exit_due_{h}": due_us,
        f"exit_submit_us_{h}": None,
        f"exit_submit_status_{h}": None,
        f"exit_us_{h}": None,
        f"exit_bid_{h}": None,
        f"exit_age_s_{h}": None,
        f"gross_{h}": None,
        f"exit_status_{h}": None,
        f"delayed_{h}": None,
    }
    si = _latest_raw_index(q, due_us)
    if _is_valid_regular(q, si):
        # A valid latest NBBO is a genuinely available book: the MARKET order is submitted at
        # the due intent regardless of age; the arrival evaluation decides the outcome.
        submit_us, status = due_us, "submit_at_due_intent_firm_regular"
    else:
        j = _first_eligible_at_or_after(q, due_us, session_end_us)
        if j < 0:
            out[f"exit_status_{h}"] = "unknown_exit_no_valid_regular_quote"
            return out
        submit_us, status = int(q["ts"][j]), "submit_at_first_regular_after_due_intent"
    out[f"exit_submit_us_{h}"] = submit_us
    out[f"exit_submit_status_{h}"] = status
    exit_arrival = submit_us + LATENCY_US
    ai = _latest_raw_index(q, exit_arrival)
    if _is_firm_fresh(q, ai, exit_arrival):
        priced_at, exit_us = ai, exit_arrival
    elif _is_valid_regular(q, ai):
        out[f"exit_status_{h}"] = "unknown_stale_but_valid_at_exit_arrival"
        return out
    else:
        k = _first_eligible_at_or_after(q, exit_arrival, session_end_us)
        if k < 0:
            out[f"exit_status_{h}"] = "unknown_no_valid_regular_at_or_after_exit_arrival"
            return out
        priced_at, exit_us = k, int(q["ts"][k])
    bid = float(q["bid"][priced_at])
    if float(q["bs"][priced_at]) < qty:
        out[f"exit_status_{h}"] = "unknown_exit_partial_depth"
        return out
    out[f"exit_us_{h}"] = exit_us
    out[f"exit_bid_{h}"] = bid
    out[f"exit_age_s_{h}"] = (exit_us - int(q["ts"][priced_at])) / 1_000_000
    out[f"exit_status_{h}"] = "conditional_market_fill"
    # delayed only when the order actually had to WAIT (submission rested past the due
    # intent, or pricing rested to a print after the arrival) - ordinary +250ms latency
    # against a fresh book is not a delay.
    out[f"delayed_{h}"] = bool(
        submit_us > due_us or int(q["ts"][priced_at]) > exit_arrival
    )
    return out


def resolve_intent(
    day: str, ticker: str, signal_us: int, stream: dict, session_end: int, hold_seconds: int
) -> dict:
    """One order's as-of execution record for ONE declared hold (horizon_seconds required)."""
    q = stream["quotes"]
    h = int(hold_seconds)  # SECONDS; no minute default, no implicit conversion
    intent_us = int(signal_us) + INTENT_NEXT_SECOND_US
    session_end_us = clock_us(day, session_end, 0)
    entry = entry_leg(q, intent_us)
    rec = {
        "day": day,
        "ticker": ticker,
        "signal_us": int(signal_us),
        "intent_us": intent_us,
        "horizon_seconds": h,
        "session_end": session_end_us,
        "session_end_minute": int(session_end),
        **entry,
    }
    if entry["order_state"] == "filled":
        for key, value in exit_leg(
            q, entry["entry_et"], h, session_end_us, entry["qty"]
        ).items():
            rec[key] = value
        if rec.get(f"exit_bid_{h}") is not None:
            rec[f"gross_{h}"] = float(rec[f"exit_bid_{h}"]) / float(entry["entry_ask"]) - 1.0
    return rec


# ---------------------------------------------------------------- account replay
def account_replay(
    intents: list[dict], days: list[str], horizon_seconds: int, cost_bps: float
) -> tuple[dict, list[dict]]:
    """Research sub-book replay for ONE declared hold view at ONE residual cost rung.

    Equal $250 orders, 3 concurrent funded slots, RTH only. All intents sharing one intent
    clock are funded BEFORE any arrival outcome is observed (two phases per clock), so a
    same-clock unfilled intent can never substitute a fourth slot. A ticker never overlaps
    itself; after an ACTUAL exit the same ticker must wait COOLDOWN_SECONDS (300s = 5 MIN)
    before re-entry; at most MAX_ATTEMPTS_PER_TICKER_DAY attempts per ticker/day. UNKNOWN
    (unpriced) positions stay reserved to the session end and are charged a full loss; a
    modeled no-L1-match IOC returns unfilled cash with no position.
    """
    h = int(horizon_seconds)
    side = cost_bps / 20_000.0
    rows = sorted(
        intents,
        key=lambda r: (
            r["day"],
            int(r["intent_us"]),
            -float(r.get("score") or 0.0),
            r["ticker"],
        ),
    )
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        by_day.setdefault(r["day"], []).append(r)
    trades: list[dict] = []
    daily: list[dict] = []
    total_attempts = 0
    skips: Counter = Counter()
    for day in days:
        cash = float(BOOK)
        queue: list[tuple] = []  # (release_us, seq, ticker, proceeds)
        active: set[str] = set()
        cooldown: dict[str, int] = {}
        attempted: Counter = Counter()
        unknown = 0
        known_pnl = 0.0
        seq = 0
        day_rows = by_day.get(day, [])
        i = 0
        while i < len(day_rows):
            clock = int(day_rows[i]["intent_us"])
            j = i
            while j < len(day_rows) and int(day_rows[j]["intent_us"]) == clock:
                j += 1
            group = day_rows[i:j]
            i = j
            while queue and queue[0][0] <= clock:
                _, _, sym, proceeds = heapq.heappop(queue)
                active.discard(sym)
                cash += proceeds
            admitted: list[dict] = []
            # phase 1: fund every admitted intent of this clock BEFORE any outcome is known
            for r in group:
                sym = r["ticker"]
                if r["order_state"] == "cash_skip":
                    skips["intent_not_firm_fresh"] += 1
                    continue
                if attempted[sym] >= MAX_ATTEMPTS_PER_TICKER_DAY:
                    skips["max_attempts_per_ticker_day"] += 1
                    continue
                if sym in active:
                    skips["ticker_overlap"] += 1
                    continue
                if cooldown.get(sym, -1) > clock:
                    skips["cooldown_300s"] += 1
                    continue
                if len(active) >= MAX_SLOTS:
                    skips["no_free_slot"] += 1
                    continue
                if cash + 1e-8 < ORDER_BUDGET:
                    skips["no_cash"] += 1
                    continue
                cash -= ORDER_BUDGET
                attempted[sym] += 1
                active.add(sym)
                total_attempts += 1
                admitted.append(r)
            # phase 2: resolve arrival outcomes; releases only affect LATER clocks
            for r in admitted:
                sym = r["ticker"]
                gross = r.get(f"gross_{h}")
                if r["order_state"] == "unfilled":
                    # the IOC's real execution clock is its +250ms arrival, never the
                    # pre-arrival intent clock: funding waits the real clock.
                    release = int(r.get("entry_et") or clock)
                    proceeds = ORDER_BUDGET
                    trades.append(
                        _trade_row(r, h, cost_bps, "unfilled_no_l1_match", None, None, None)
                    )
                elif r["order_state"] == "unknown" or gross is None:
                    unknown += 1
                    release, proceeds = int(r["session_end"]) + 1, 0.0
                    trades.append(
                        _trade_row(r, h, cost_bps, "unknown_reserved", None, None, None)
                    )
                else:
                    net = (1.0 + gross) * (1.0 - side) / (1.0 + side) - 1.0
                    proceeds = ORDER_BUDGET * (1.0 + net)
                    known_pnl += ORDER_BUDGET * net
                    release = int(r[f"exit_us_{h}"])
                    cooldown[sym] = release + COOLDOWN_SECONDS * 1_000_000
                    trades.append(
                        _trade_row(
                            r,
                            h,
                            cost_bps,
                            "known_actual_touch",
                            gross,
                            net,
                            r.get(f"exit_bid_{h}"),
                        )
                    )
                heapq.heappush(queue, (release, seq, sym, proceeds))
                seq += 1
        lower_pnl = known_pnl - ORDER_BUDGET * unknown
        daily.append(
            {
                "day": day,
                "known_pnl": known_pnl,
                "unknown": unknown,
                "lower_bound_pnl": lower_pnl,
                "lower_bound_return": lower_pnl / BOOK,
                "attempts": int(sum(attempted.values())),
            }
        )
    known = np.array(
        [t["net"] for t in trades if t["net"] is not None and t["status"] == "known_actual_touch"],
        dtype=float,
    )
    returns = np.array([r["lower_bound_return"] for r in daily], dtype=float)
    monthly: dict[str, list[float]] = {}
    for r in daily:
        monthly.setdefault(r["day"][:7], []).append(r["lower_bound_return"])
    wins = known[known > 0].sum()
    losses = -known[known < 0].sum()
    unknown_count = sum(r["unknown"] for r in daily)
    metrics = {
        "days": len(days),
        "attempts": total_attempts,
        "fills": int(sum(1 for t in trades if t["status"] != "unfilled_no_l1_match")),
        "known_fills": int(len(known)),
        "unknown_fills": int(unknown_count),
        "unfilled_fills": int(sum(1 for t in trades if t["status"] == "unfilled_no_l1_match")),
        "cash_or_slot_skips": int(sum(skips.values())),
        "skip_counts": dict(skips),
        "cost_bps": cost_bps,
        "horizon_seconds": h,
        "mean_net_known_fill": float(known.mean()) if len(known) else None,
        "mean_daily_lower_bound": float(returns.mean()) if len(returns) else None,
        "daily_se": (
            float(returns.std(ddof=1) / np.sqrt(len(returns))) if len(returns) > 1 else None
        ),
        "known_win_rate": float((known > 0).mean()) if len(known) else None,
        "known_profit_factor": float(wins / losses) if losses else None,
        "worst_known_fill": float(known.min()) if len(known) else None,
        "positive_months_lower_bound": int(sum(np.mean(v) > 0 for v in monthly.values())),
        "months": len(monthly),
        "monthly_mean_lower_bound": {k: float(np.mean(v)) for k, v in sorted(monthly.items())},
        "traded_days": len({t["day"] for t in trades if t["status"] == "known_actual_touch"}),
        "execution_proxy_only": True,
        "daily": daily,
    }
    return metrics, trades


def _trade_row(
    r: dict, horizon: int, cost_bps: float, status: str, gross, net, exit_bid
) -> dict:
    return {
        "day": r["day"],
        "ticker": r["ticker"],
        "t": int(r["intent_us"]),
        "signal_us": int(r["signal_us"]),
        "entry_et": r.get("entry_et"),
        "entry_open": r.get("entry_ask"),
        "exit_et": r.get(f"exit_us_{horizon}"),
        "exit_bid": exit_bid,
        "horizon": horizon,
        "gross": gross,
        "net": net,
        "cost_bps": cost_bps,
        "order_budget": ORDER_BUDGET,
        "score": r.get("score"),
        "status": status,
    }


# ---------------------------------------------------------------- build
def intent_record(
    row: dict, stream: dict, day: str, session_end: int, variant: str
) -> dict:
    """Execution records for one selected second; both declared holds kept, even UNKNOWN."""
    ticker = row["ticker"]
    sus = int(row["signal_us"])
    resolved = {h: resolve_intent(day, ticker, sus, stream, session_end, h) for h in HOLD_SECONDS}
    base = resolved[HOLD_SECONDS[0]]
    out = {
        "day": day,
        "ticker": ticker,
        "variant": variant,
        "signal_us": sus,
        "intent_us": int(sus) + INTENT_NEXT_SECOND_US,
        "score": _plain(row.get("imbalance5s")),
        "period": period(day),
        "admit_t": int(stream["admit_t"]),
        "session_end": _plain(base["session_end"]),
        "session_end_minute": session_end,
        "order_state": base["order_state"],
        "intent_status": base["intent_status"],
        "entry_status": base["entry_status"],
        "entry_et": _plain(base["entry_et"]),
        "entry_open": _plain(base["entry_ask"]),
        "limit_price": _plain(base["limit_price"]),
        "qty": int(base["qty"]),
    }
    for h in HOLD_SECONDS:
        rec = resolved[h]
        out[f"exit_due_{h}"] = _plain(rec.get(f"exit_due_{h}"))
        out[f"exit_us_{h}"] = _plain(rec.get(f"exit_us_{h}"))
        out[f"exit_bid_{h}"] = _plain(rec.get(f"exit_bid_{h}"))
        out[f"gross_{h}"] = _plain(rec.get(f"gross_{h}"))
        out[f"exit_status_{h}"] = rec.get(f"exit_status_{h}")
        out[f"exit_submit_status_{h}"] = rec.get(f"exit_submit_status_{h}")
        delayed = rec.get(f"delayed_{h}")
        out[f"delayed_{h}"] = bool(delayed) if delayed is not None else None
    for k in PANEL_FEATURE_KEYS:
        if k in row:
            out[f"q_{k}"] = _plain(row[k])
    return out


def build_day(job: tuple) -> dict:
    (
        day,
        data_dir,
        out_dir,
        session_end,
        force,
        producer,
        con_sha,
        core_sha,
        sup_roots,
        sup_digest,
    ) = job
    if not allowed(day):
        raise ValueError(f"protected/out-of-scope day refused: {day}")
    out = Path(out_dir)
    out.joinpath("days").mkdir(parents=True, exist_ok=True)
    dest_panel = out / "days" / f"{day}.parquet"
    man = out / "days" / f"{day}.json"
    rh = resume_hash(day, core_sha, con_sha, sup_digest)
    if not force and man.exists():
        try:
            prior = json.loads(man.read_text())
        except (json.JSONDecodeError, OSError):
            prior = None
        if (
            prior
            and prior.get("resume_hash") == rh
            and prior.get("producer_sha256") == producer
            and prior.get("core_sha256") == core_sha
        ):
            return {
                "day": day,
                "status": "cached",
                "coverage": prior.get("coverage_label"),
                "intents": len(prior.get("intents", [])),
                "coverage_complete": prior.get("coverage_complete"),
            }
    start = time.monotonic()
    error = None
    sup_error = None
    sup_created: list[str] = []
    sup_unrecovered: dict[str, str] = {}
    # Declared up front so a day-level failure still writes a well-formed manifest.
    streams: dict = {}
    cov: dict = {}
    watch: dict[str, int] = {}
    covered: list[str] = []
    still_missing: list[str] = []
    opens: dict[str, float] = {}
    supplement = None
    sup_added: dict[str, int] = {}
    no_reg_day = q_unknown_day = False
    watch_names = covered_names = no_reg = q_unknown = q_events = reg_prints = 0
    missing_streams: list[str] = []
    missing_file = False
    coverage_label = "day_error"
    coverage_complete = False
    anchor_missing: list[str] = []
    intents: list[dict] = []
    panel_rows: list[dict] = []
    try:
        # The universe is declared HERE and handed to the shared core loader once:
        # the loader never derives the watch, so the full top-ten candidate set (not
        # the parent top-3 policy, and not the streams that happen to survive) is
        # what the coverage denominator counts.
        watch = full_watch(Path(data_dir), day)
        streams, cov = load_day(Path(data_dir), day, watch=watch)
        covered = sorted(streams)
        missing_streams = list(cov.get("missing_symbol_streams", []))
        opens = day_open_prices(Path(data_dir), day, covered + missing_streams)
        # Supplemental quote acquisition: ADD timestamps only, PRIMARY_KEEPFIRST. A
        # malformed supplement file is recorded as its own reason and never silently
        # drops the primary ingest.
        supplement = None
        try:
            supplement = read_supplement_day(sup_roots, day, covered + missing_streams)
        except Exception as exc:
            sup_error = f"{type(exc).__name__}: {exc}"
        sup_added = {}
        if supplement is not None:
            for ticker in covered:
                part = supplement.filter(pl.col("symbol") == ticker).sort("ts_utc")
                if not part.height:
                    continue
                before = int(streams[ticker]["quotes"]["ts"].size)
                streams[ticker]["quotes"] = merge_supplement_quotes(
                    streams[ticker]["quotes"], part, day
                )
                added = int(streams[ticker]["quotes"]["ts"].size) - before
                if added:
                    sup_added[ticker] = added
            # CREATE the streams the primary net lacks: primary eligible prints (the
            # core's own classification) + supplement quotes whose condition fields are
            # derived with the same R-only/uncrossed rules the primary path uses.
            no_reg_day = q_unknown_day = False
            for ticker in missing_streams:
                part = supplement.filter(pl.col("symbol") == ticker).sort("ts_utc")
                if not part.height or ticker not in watch:
                    sup_unrecovered[ticker] = "no_supplement_quotes_or_admission"
                    continue
                stream, reason = recover_supplement_stream(
                    Path(data_dir), day, ticker, watch[ticker], part
                )
                if stream is None:
                    sup_unrecovered[ticker] = reason or "unrecoverable"
                    continue
                streams[ticker] = stream
                sup_created.append(ticker)
                sup_added[ticker] = int(stream["quotes"]["ts"].size)
                # Condition coverage over the now-observable set: the same fields the
                # primary path derives (raw '?' conditions; R-only regular absence).
                if part.filter(pl.col("conditions").list.contains("?")).height:
                    q_unknown_day = True
                elif not stream["quotes"]["regular"].any():
                    no_reg_day = True
            covered = sorted(streams)
            still_missing = [t for t in missing_streams if t not in streams]
        else:
            still_missing = missing_streams
        watch_names = int(cov.get("watch_names", 0))
        covered_names = len(streams)
        missing_file = bool(cov.get("missing_day_file", False))
        no_reg = int(cov.get("no_regular_quote_symbols", 0))
        q_unknown = int(cov.get("quote_condition_unknown_symbols", 0))
        if supplement is not None:
            no_reg += int(no_reg_day)
            q_unknown += int(q_unknown_day)
        q_events = int(cov.get("quote_events", 0))
        reg_prints = int(cov.get("regular_price_prints", 0))
        coverage_label = coverage_kind(
            watch_names, still_missing, missing_file, no_reg, q_unknown
        )
        coverage_complete = coverage_is_complete(coverage_label)
        setup = setup_expr()
        intents, panel_rows, anchor_missing = [], [], []
        for ticker in covered:
            stream = streams[ticker]
            o570 = opens.get(ticker)
            if not o570:
                anchor_missing.append(ticker)
                continue
            frame = state_frame(day, ticker, stream, session_end)
            if not frame.height:
                continue
            cols = family_columns(
                day, stream, session_end, float(o570), frame["imbalance5s"].to_numpy()
            )
            grid = cols.pop("signal_us")
            if not np.array_equal(grid, frame["signal_us"].to_numpy()):
                raise ValueError(f"family grid mismatch vs states grid: {day} {ticker}")
            frame = frame.with_columns([pl.Series(k, v) for k, v in cols.items()])
            setup_mask = frame.select(setup.alias("m"))["m"].to_numpy()
            if not setup_mask.any():
                continue
            flip = flow_flip(frame["imbalance5s"].to_numpy(), cols["imbalance_hist_min"])
            imb = frame["imbalance5s"].to_numpy()
            for variant in CONFIRMATIONS:
                selected = first_after_flip(
                    flip, variant_qualified(variant, flip, setup_mask, frame)
                )
                for idx in np.flatnonzero(selected):
                    r = frame.row(int(idx), named=True)
                    r["ticker"] = ticker
                    intents.append(intent_record(r, stream, day, session_end, variant))
                    row = {k: _plain(r.get(k)) for k in ("signal_us", *PANEL_FEATURE_KEYS)}
                    row.update(
                        {
                            "day": day,
                            "ticker": ticker,
                            "variant": variant,
                            "intent_us": int(r["signal_us"]) + INTENT_NEXT_SECOND_US,
                            "score": _plain(imb[idx]),
                            "selected": True,
                        }
                    )
                    panel_rows.append(row)
    except Exception as exc:  # calendar keeps running; day stays coverage-unknown
        error = f"{type(exc).__name__}: {exc}"
        streams, cov = {}, {}
        covered, still_missing, opens = [], [], []
        supplement, sup_added = None, {}
        no_reg_day = q_unknown_day = False
        watch_names = covered_names = no_reg = q_unknown = q_events = reg_prints = 0
        missing_streams, anchor_missing = [], []
        sup_created, sup_unrecovered = [], {}
        missing_file = False
        coverage_label = "day_error"
        coverage_complete = False
        intents, panel_rows = [], []
    panel = pl.DataFrame(panel_rows) if panel_rows else pl.DataFrame(schema=PANEL_SCHEMA)
    panel.write_parquet(str(dest_panel) + ".tmp")
    Path(str(dest_panel) + ".tmp").replace(dest_panel)
    manifest = {
        "day": day,
        "period": period(day),
        "session_end": session_end,
        "version": VERSION,
        "producer_sha256": producer,
        "core_sha256": core_sha,
        "contract_sha256": con_sha,
        "resume_hash": rh,
        "coverage": {
            "watch_names": watch_names,
            "covered_names": covered_names,
            "no_regular_quote_symbols": no_reg,
            "quote_condition_unknown_symbols": q_unknown,
            "missing_symbol_streams": still_missing,
            "missing_before_supplement": missing_streams,
            "missing_day_file": missing_file,
            "quote_events": q_events,
            "regular_price_prints": reg_prints,
            "gain_anchor_missing": anchor_missing,
        },
        "supplement": {
            "roots": [str(r) for r in sup_roots],
            "digest": sup_digest,
            "prints_added_by_ticker": sup_added,
            "prints_added_total": int(sum(sup_added.values())),
            "created_streams": list(sup_created),
            "unrecovered_by_ticker": sup_unrecovered,
            "error": sup_error,
        },
        "coverage_label": coverage_label,
        "coverage_complete": coverage_complete,
        "error": error,
        "intents": intents,
        "runtime_s": round(time.monotonic() - start, 3),
    }
    payload = json.dumps(manifest, indent=1, default=_default) + "\n"
    man.with_suffix(".json.tmp").write_text(payload)
    man.with_suffix(".json.tmp").replace(man)
    return {
        "day": day,
        "status": "built" if error is None else "error",
        "coverage": coverage_label,
        "intents": len(intents),
        "coverage_complete": coverage_complete,
        "error": error,
        "runtime_s": manifest["runtime_s"],
    }


def _map_jobs(jobs: list[tuple], workers: int):
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=workers) as pool:
            yield from pool.map(build_day, jobs)
    else:
        for job in jobs:
            yield build_day(job)


def run(args: argparse.Namespace) -> None:
    args.out.mkdir(parents=True, exist_ok=True)
    producer = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    core_sha, con_sha = core_sha256(), contract_sha256()
    contract = {
        **CONTRACT,
        "producer_sha256": producer,
        "core_sha256": core_sha,
        "contract_sha256": con_sha,
        "calendar": str(CALENDAR),
    }
    contract_path = args.out / "contract.json"
    if contract_path.exists():
        prior = json.loads(contract_path.read_text())
        semantic_now = {k: v for k, v in contract.items() if k != "producer_sha256"}
        semantic_prior = {k: v for k, v in prior.items() if k != "producer_sha256"}
        if semantic_prior != semantic_now:
            raise ValueError("output root contract mismatch; use a new versioned output root")
    else:
        contract_path.write_text(json.dumps(contract, indent=1) + "\n")
    cal = _calendar()
    subset = set(args.days) if args.days else None
    per = period_days(args.data, subset)
    if subset is not None:
        wanted = set(args.days)
        seen = set(itertools.chain.from_iterable(per.values()))
        outside = sorted(wanted - seen)
        if outside:
            raise ValueError(f"days outside fixed periods: {outside}")
    days = sorted(itertools.chain.from_iterable(per.values()))
    sup_roots = resolve_supplement_roots(args.supplement)
    sup_digest, sup_entries = supplement_digest(sup_roots)
    jobs = [
        (
            d,
            str(args.data),
            str(args.out),
            int(cal[d]["session_end"]),
            args.force,
            producer,
            con_sha,
            core_sha,
            sup_roots,
            sup_digest,
        )
        for d in days
    ]
    infos = []
    for n, info in enumerate(_map_jobs(jobs, args.workers), 1):
        infos.append(info)
        if args.days or n % 10 == 0 or n == len(jobs):
            shown = {k: info[k] for k in info if k != "day"}
            print(f"{n}/{len(jobs)} {json.dumps(shown, default=_default)}", flush=True)
    _write_json_atomic(
        args.out / "day_stage.json",
        {
            "producer_sha256": producer,
            "core_sha256": core_sha,
            "contract_sha256": con_sha,
            "resume_rule": "sha256[VERSION, COVERAGE_EPOCH, core_sha256, contract_sha256, "
            "supplement_digest, FAMILY, ACCOUNT, PERIODS, day]",
            "supplement_roots": [str(r) for r in sup_roots],
            "supplement_digest": sup_digest,
            "supplement_files": sup_entries,
            "days": [
                {
                    "day": i["day"],
                    "status": i["status"],
                    "coverage": i.get("coverage"),
                    "coverage_complete": i.get("coverage_complete"),
                    "intents": i.get("intents", 0),
                }
                for i in infos
            ],
        },
    )
    print(f"built/checked {len(days)} days -> {args.out}", flush=True)


# ---------------------------------------------------------------- report
def load_manifests(out: Path, days: list[str]) -> dict[str, dict]:
    manifests = {}
    for day in days:
        manifests[day] = json.loads((out / "days" / f"{day}.json").read_text())
    return manifests


def coverage_block(manifests: dict[str, dict], days: list[str]) -> dict:
    watch = covered = watch_with_regular = 0
    by_kind: dict[str, int] = {}
    unknown_names: dict[str, int] = {}
    exception_days: dict[str, list[str]] = {}
    for day in days:
        info = manifests[day]
        cov = info.get("coverage", {})
        watch += cov.get("watch_names", 0)
        covered += cov.get("covered_names", 0)
        watch_with_regular += max(
            0, cov.get("watch_names", 0) - cov.get("no_regular_quote_symbols", 0)
        )
        kind = info.get("coverage_label", "missing_manifest")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        unknown_names[day] = cov.get("quote_condition_unknown_symbols", 0)
        if kind not in ("complete", "no_watch_names"):
            exception_days.setdefault(kind, []).append(day)
    return {
        "days": len(days),
        "watch_names": watch,
        "covered_names": covered,
        "names_with_regular_quotes": watch_with_regular,
        "quote_condition_unknown_symbols_by_day": unknown_names,
        "by_kind": by_kind,
        "exception_days": exception_days,
        "complete_days": by_kind.get("complete", 0) + by_kind.get("no_watch_names", 0),
        "coverage_unknown_days": len(days)
        - by_kind.get("complete", 0)
        - by_kind.get("no_watch_names", 0),
    }


def _bootstrap(returns: list[float]) -> list[float] | None:
    values = np.array(list(returns), dtype=float)
    if not len(values):
        return None
    rng = np.random.default_rng(BOOT_SEED)
    sample = rng.choice(values, size=(BOOT_DRAWS, len(values)), replace=True).mean(axis=1)
    return [float(v) for v in np.quantile(sample, [0.025, 0.975])]


def summarise(
    manifests: dict[str, dict],
    days: list[str],
    variant: str,
    horizon: int,
    cost: float,
    label: str,
) -> dict:
    intents = [
        r
        for d in days
        for r in manifests[d].get("intents", [])
        if r.get("variant") == variant
    ]
    metrics, trades = account_replay(intents, days, horizon, cost)
    daily = metrics["daily"]
    incomplete = {d for d in days if not manifests[d].get("coverage_complete", False)}
    complete = set(days) - incomplete
    lb: list[float] = []
    for r in daily:
        if r["day"] in incomplete:
            r["lower_bound_return"] = -1.0  # coverage-unknown whole-book floor
            r["lower_bound_pnl"] = -BOOK
        lb.append(r["lower_bound_return"])
    known_complete = [r["lower_bound_return"] for r in daily if r["day"] in complete]
    monthly, yearly = {}, {}
    for r in daily:
        monthly.setdefault(r["day"][:7], []).append(r["lower_bound_return"])
        yearly.setdefault(r["day"][:4], []).append(r["lower_bound_return"])
    h = int(horizon)
    ordered = [r for r in intents if r.get("order_state") != "cash_skip"]
    sup = sum(1 for r in ordered if r.get(f"gross_{h}") is not None)
    unsup = sum(1 for r in ordered if r.get(f"gross_{h}") is None)
    delayed = sum(1 for r in intents if r.get(f"delayed_{h}"))
    return {
        "period": label,
        "period_role": label,
        "variant": variant,
        "horizon_seconds": h,
        "cost_bps_residual": cost,
        "cost_type": "residual round-trip bps on actual as-of ASK/BID quotes",
        "cost_unit": "bps",
        "order_usd": ORDER_BUDGET,
        "book_usd": BOOK,
        "execution_proxy_only": True,
        "period_days": len(days),
        "signals": len(intents),
        "tickers": len({r["ticker"] for r in intents}),
        "attempts": metrics["attempts"],
        "fills": metrics["fills"],
        "known_fills": metrics["known_fills"],
        "unknown_fills": metrics["unknown_fills"],
        "unfilled_fills": metrics["unfilled_fills"],
        "cash_or_slot_skips": metrics["cash_or_slot_skips"],
        "skip_counts": metrics["skip_counts"],
        "quote_supported_signals": sup,
        "quote_unsupported_signals": unsup,
        "delayed_exit_signals": delayed,
        "mean_net_known_fill": metrics["mean_net_known_fill"],
        "mean_net_known_fill_per_order_pct": (
            metrics["mean_net_known_fill"] * 100.0
            if metrics["mean_net_known_fill"] is not None
            else None
        ),
        "mean_daily_lower_bound": float(np.mean(lb)) if lb else None,
        "mean_daily_lower_bound_book_pct": (float(np.mean(lb)) * 100.0) if lb else None,
        "daily_se": (float(np.std(lb, ddof=1) / np.sqrt(len(lb))) if len(lb) > 1 else None),
        "day_bootstrap_ci95": _bootstrap(lb),
        "known_win_rate": metrics["known_win_rate"],
        "known_profit_factor": metrics["known_profit_factor"],
        "worst_known_fill": metrics["worst_known_fill"],
        "positive_months_lower_bound": int(sum(np.mean(v) > 0 for v in monthly.values())),
        "months": len(monthly),
        "monthly_mean_lower_bound": {k: float(np.mean(v)) for k, v in sorted(monthly.items())},
        "monthly_n": {k: len(v) for k, v in sorted(monthly.items())},
        "yearly_mean_lower_bound": {k: float(np.mean(v)) for k, v in sorted(yearly.items())},
        "yearly_n": {k: len(v) for k, v in sorted(yearly.items())},
        "traded_days": metrics["traded_days"],
        "known_complete_day_n": len(known_complete),
        "known_complete_day_mean_lower_bound": (
            float(np.mean(known_complete)) if known_complete else None
        ),
        "coverage_unknown_days": len(incomplete),
        "coverage_unknown_day_list": sorted(incomplete),
        "coverage_worst_case_mean_daily": float(np.mean(lb)) if lb else None,
        "coverage_worst_case_mean_daily_book_pct": (float(np.mean(lb)) * 100.0) if lb else None,
        "coverage_worst_case_ci95": _bootstrap(lb),
        "certified_complete_period": len(incomplete) == 0,
        "daily": [
            {
                "day": r["day"],
                "known_pnl": r["known_pnl"],
                "unknown": r["unknown"],
                "attempts": r["attempts"],
                "lower_bound_pnl": r["lower_bound_pnl"],
                "lower_bound_return": r["lower_bound_return"],
                "coverage_unknown": r["day"] in incomplete,
            }
            for r in daily
        ],
        "_trades": trades,
    }


def _thin(summary: dict) -> dict:
    keep = (
        "period",
        "variant",
        "horizon_seconds",
        "cost_bps_residual",
        "cost_type",
        "cost_unit",
        "order_usd",
        "book_usd",
        "period_days",
        "signals",
        "attempts",
        "fills",
        "known_fills",
        "unknown_fills",
        "unfilled_fills",
        "cash_or_slot_skips",
        "quote_supported_signals",
        "delayed_exit_signals",
        "mean_net_known_fill",
        "mean_daily_lower_bound",
        "day_bootstrap_ci95",
        "known_win_rate",
        "positive_months_lower_bound",
        "months",
        "monthly_n",
        "yearly_mean_lower_bound",
        "yearly_n",
        "traded_days",
        "known_complete_day_n",
        "known_complete_day_mean_lower_bound",
        "coverage_unknown_days",
        "coverage_worst_case_mean_daily",
        "certified_complete_period",
    )
    return {k: summary[k] for k in keep if k in summary}


def _write_trades(trades: list[dict], path: Path) -> None:
    if not trades:
        pl.DataFrame(
            schema={
                "day": pl.String,
                "ticker": pl.String,
                "t": pl.Int64,
                "net": pl.Float64,
                "gross": pl.Float64,
                "status": pl.String,
                "horizon": pl.Int64,
            }
        ).write_parquet(path)
        return
    pl.DataFrame([{k: _plain(v) for k, v in t.items()} for t in trades]).write_parquet(path)


def select_cell(val: dict[tuple[str, int], dict]) -> dict:
    """Frozen cell selection: max 2023 mean_daily_lower_bound at the primary 25bps rung."""
    surface = {
        f"{variant}|h{h}": cell["mean_daily_lower_bound"]
        for (variant, h), cell in val.items()
    }
    order = {(v, h): (h, i) for i, v in enumerate(CONFIRMATIONS) for h in HOLD_SECONDS}

    def key(item):
        (variant, h), cell = item
        value = cell["mean_daily_lower_bound"]
        rank = order[(variant, h)]
        return (
            value if value is not None else float("-inf"),
            -rank[0],
            -rank[1],
        )

    chosen_pair, chosen_cell = max(val.items(), key=key)
    chosen_variant, chosen_h = chosen_pair
    value = chosen_cell["mean_daily_lower_bound"]
    diag = not (value is not None and value > 0)
    return {
        "rule": "max(2023 mean_daily_lower_bound at "
        f"{int(PRIMARY_COST)}bps residual) over the frozen (confirmation, hold-seconds) cells; "
        "ties broken by the shorter hold then the declared confirmation order; frozen before "
        "confirmation",
        "validation_mean_daily_lower_bound_at_primary_by_cell": surface,
        "chosen_variant": chosen_variant,
        "chosen_horizon_seconds": chosen_h,
        "chosen_validation_value_at_primary": value,
        "selection_positive_on_validation": not diag,
        "diagnostic_not_promotion": diag,
    }


def report(args: argparse.Namespace) -> None:
    args.out.mkdir(parents=True, exist_ok=True)
    producer = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    core_sha, con_sha = core_sha256(), contract_sha256()
    contract = {
        **CONTRACT,
        "producer_sha256": producer,
        "core_sha256": core_sha,
        "contract_sha256": con_sha,
        "calendar": str(CALENDAR),
    }
    (args.out / "contract.json").write_text(json.dumps(contract, indent=1, default=_default) + "\n")
    subset = set(args.days) if args.days else None
    per = period_days(args.data, subset)
    sup_roots = resolve_supplement_roots(args.supplement)
    sup_digest, sup_entries = supplement_digest(sup_roots)
    blocks = ("development", "validation", "confirmation")
    wanted = sorted(itertools.chain.from_iterable(per.values()))
    manifests = load_manifests(args.out, wanted)
    missing = sorted(set(wanted) - set(manifests))
    if missing:
        raise ValueError(
            f"report refused: {len(missing)} study days have no manifest "
            f"({missing[:5]}...); run the build first - a covered subset is never "
            f"promoted to the whole portfolio"
        )
    intents = [r for m in manifests.values() for r in m.get("intents", [])]
    cols = sorted({k for r in intents for k in r})
    frame = (
        pl.DataFrame([{k: _plain(r.get(k)) for k in cols} for r in intents])
        if intents
        else pl.DataFrame(schema={"day": pl.String, "ticker": pl.String})
    )
    frame.write_parquet(args.out / "intents.parquet")

    results: list[dict] = []
    summaries: dict[tuple[str, str, int, float], dict] = {}
    for block in blocks:
        days = per[block]
        for variant in CONFIRMATIONS:
            for horizon in HOLD_SECONDS:
                for cost in COSTS:
                    s = summarise(manifests, days, variant, horizon, cost, block)
                    trades = s.pop("_trades")
                    _write_trades(
                        trades,
                        args.out / f"trades_{block}_{variant}_h{horizon}_c{int(cost)}.parquet",
                    )
                    results.append(s)
                    summaries[(block, variant, horizon, cost)] = s

    # Known-complete-days-only slices for any incomplete period (never promoted to whole book).
    for block in blocks:
        days = per[block]
        complete_days = [d for d in days if manifests[d].get("coverage_complete")]
        if complete_days and len(complete_days) < len(days):
            for variant in CONFIRMATIONS:
                for horizon in HOLD_SECONDS:
                    for cost in (PRIMARY_COST, COSTS[-1]):
                        s = summarise(
                            manifests,
                            complete_days,
                            variant,
                            horizon,
                            cost,
                            f"{block}_complete_days_only",
                        )
                        _write_trades(
                            s.pop("_trades"),
                            args.out
                            / f"trades_{block}_complete_{variant}_h{horizon}_c{int(cost)}.parquet",
                        )
                        results.append(s)

    # Frozen cell selection uses ONLY validation (2023) daily lower bound at the primary rung.
    val = {
        (variant, h): summaries[("validation", variant, h, PRIMARY_COST)]
        for variant in CONFIRMATIONS
        for h in HOLD_SECONDS
    }
    selection = select_cell(val)
    chosen_variant = selection["chosen_variant"]
    chosen = selection["chosen_horizon_seconds"]

    chosen_by_cost = {}
    for block in blocks:
        chosen_by_cost[block] = {
            str(int(c)): _thin(summaries[(block, chosen_variant, chosen, c)]) for c in COSTS
        }
    conf = {c: summaries[("confirmation", chosen_variant, chosen, c)] for c in COSTS}
    data_support = {
        str(int(c)): {
            "known_fills": conf[c]["known_fills"],
            "unknown_fills": conf[c]["unknown_fills"],
            "n_no_fill_or_skips": conf[c]["cash_or_slot_skips"],
            "delayed_exit_signals": conf[c]["delayed_exit_signals"],
            "quote_supported_signals": conf[c]["quote_supported_signals"],
            "signals": conf[c]["signals"],
            "coverage_unknown_days": conf[c]["coverage_unknown_days"],
            "certified_complete_period": conf[c]["certified_complete_period"],
        }
        for c in COSTS
    }
    ci25, ci150 = conf[PRIMARY_COST]["day_bootstrap_ci95"], conf[COSTS[-1]]["day_bootstrap_ci95"]
    certified = all(
        len({d for d in per[b] if not manifests[d].get("coverage_complete")}) == 0 for b in blocks
    )
    positive_at = [
        int(c) for c in COSTS if (conf[c]["mean_daily_lower_bound"] or 0) > 0
    ]
    conf_pos_primary = bool((conf[PRIMARY_COST]["mean_daily_lower_bound"] or 0) > 0)
    if selection["diagnostic_not_promotion"]:
        status = (
            "DIAGNOSTIC: no (confirmation, hold) cell positive on 2023 validation at "
            f"{int(PRIMARY_COST)}bps; least-bad kept, not promoted"
        )
    elif conf_pos_primary:
        status = (
            f"QUOTE_SUPPORTED_CANDIDATE_AT_{int(PRIMARY_COST)}BPS (touch, not a fill "
            "guarantee; every rung reported, no fixed hurdle)"
        )
    else:
        status = (
            f"VALIDATION_POSITIVE_CONFIRMATION_NOT_POSITIVE_AT_{int(PRIMARY_COST)}BPS"
        )
    conf_days = max(conf[PRIMARY_COST]["period_days"], 1)
    verdict = {
        "status": status,
        "chosen_variant": chosen_variant,
        "chosen_horizon_seconds": chosen,
        "candidate": status.startswith("QUOTE_SUPPORTED"),
        "certified_complete_all_periods": certified,
        "validation_at_primary": {
            "mean_daily_lower_bound": val[(chosen_variant, chosen)]["mean_daily_lower_bound"],
            "day_bootstrap_ci95": val[(chosen_variant, chosen)]["day_bootstrap_ci95"],
            "coverage_unknown_days": val[(chosen_variant, chosen)]["coverage_unknown_days"],
        },
        "confirmation_at_primary": {
            "mean_daily_lower_bound": conf[PRIMARY_COST]["mean_daily_lower_bound"],
            "day_bootstrap_ci95": ci25,
            "coverage_unknown_days": conf[PRIMARY_COST]["coverage_unknown_days"],
        },
        "confirmation_at_max_rung": {
            "mean_daily_lower_bound": conf[COSTS[-1]]["mean_daily_lower_bound"],
            "day_bootstrap_ci95": ci150,
        },
        "positive_cost_rungs_confirmation_bps": positive_at,
        "opportunity_confirmation": {
            "signals": conf[PRIMARY_COST]["signals"],
            "attempts": conf[PRIMARY_COST]["attempts"],
            "known_fills": conf[PRIMARY_COST]["known_fills"],
            "unknown_fills": conf[PRIMARY_COST]["unknown_fills"],
            "traded_days": conf[PRIMARY_COST]["traded_days"],
            "days": conf[PRIMARY_COST]["period_days"],
            "months": conf[PRIMARY_COST]["months"],
            "positive_months": conf[PRIMARY_COST]["positive_months_lower_bound"],
            "fills_per_252_rth_days": (
                round(conf[PRIMARY_COST]["fills"] * 252.0 / conf_days, 1)
                if conf[PRIMARY_COST]["fills"]
                else 0.0
            ),
        },
        "out_of_fit_not_pristine": True,
        "data_support_confirmation_by_cost": data_support,
    }

    summary = {
        "contract": contract,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "core_module": "alpha_micro_core",
        "core_sha256": core_sha,
        "producer_sha256": producer,
        "contract_sha256": con_sha,
        "cost_type": "residual round-trip bps on top of actual as-of ASK/BID quote prices",
        "cost_rungs_bps_round_trip": [int(c) for c in COSTS],
        "primary_cost_bps_residual": PRIMARY_COST,
        "hold_views_seconds": list(HOLD_SECONDS),
        "hold_units": "SECONDS (300s = 5 MINUTES, 900s = 15 MINUTES)",
        "confirmations": list(CONFIRMATIONS),
        "periods_months": {k: list(v) for k, v in PERIODS.items()},
        "selection": selection,
        "chosen_variant": chosen_variant,
        "chosen_horizon_seconds": chosen,
        "chosen_horizon_by_cost": chosen_by_cost,
        "verdict": verdict,
        "coverage": {b: coverage_block(manifests, per[b]) for b in blocks},
        "certified_complete": certified,
        "execution_proxy_only": True,
        "supplements": {
            "roots": [str(r) for r in sup_roots],
            "digest": sup_digest,
            "files": sup_entries,
            "rule": CONTRACT["supplement_rule"],
        },
        "results": results,
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1, default=_default) + "\n")
    print(f"report -> {args.out}", flush=True)
    chosen_val = selection["chosen_validation_value_at_primary"]
    print(
        f"chosen {chosen_variant} hold {chosen}s | validation@{int(PRIMARY_COST)} "
        f"lb={chosen_val:.5f}"
        if chosen_val is not None
        else f"chosen {chosen_variant} hold {chosen}s | validation@{int(PRIMARY_COST)} lb=None",
        flush=True,
    )
    print(f"verdict: {status}", flush=True)


# ---------------------------------------------------------------- signalcondition
def signalcondition(args: argparse.Namespace) -> dict:
    decl = {
        "family": FAMILY,
        "account": ACCOUNT,
        "confirmations": CONFIRMATION_DEFS,
        "combination": "px>=5 AND cum_dv>=10M AND day_gain>=0.10 AND fresh AND spread<=25bps "
        "AND ret180s<=-0.02 AND trailing-180s min(imbalance5s)<=-0.10 AND imbalance5s>=+0.10 "
        "AND bid_shares>=2x trailing-180s min bid size AND epoch>=180s, then one frozen "
        "confirmation edge-triggered to the first qualified second after the flow flip "
        "(flip_first_positive_1s: ret1s>0; bid_depth_2x_ask: bid_shares>=2x ask_shares)",
        "feature_source": CONTRACT["feature_source"],
        "state_grid": CONTRACT["state_grid"],
        "state_extension_validation": CONTRACT["state_extension_validation"],
        "session_bound": CONTRACT["session_bound"],
        "watch": CONTRACT["watch"],
        "admission": CONTRACT["admission"],
        "selection": CONTRACT["selection"],
        "entry": CONTRACT["entry"],
        "exit": CONTRACT["exit"],
        "declared_hold_views_seconds": list(HOLD_SECONDS),
        "hold_units": CONTRACT["hold_units"],
        "costs_bps_residual_round_trip": list(COSTS),
        "primary_cost_bps_residual": PRIMARY_COST,
        "cost_note": CONTRACT["cost_note"],
        "periods": {k: list(v) for k, v in PERIODS.items()},
        "protected_unread": CONTRACT["protected_unread"],
        "coverage_rule": CONTRACT["coverage_rule"],
        "unknown_rule": CONTRACT["unknown_rule"],
        "supplement_rule": CONTRACT["supplement_rule"],
        "default_supplement_root": str(DEFAULT_SUPPLEMENT_ROOT),
    }
    print(json.dumps(decl, indent=2), flush=True)
    return decl


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--out", type=Path, default=OUT_DEFAULT)
    parser.add_argument(
        "--days", nargs="+", help="restrict build/report to these days (smoke runs)"
    )
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--force", action="store_true", help="recompute days, ignore resume cache")
    parser.add_argument(
        "--supplement",
        action="append",
        nargs="+",
        metavar="ROOT",
        help="additional raw-quote supplement roots (<root>/<day>.parquet, SIP quote "
        "columns); the quote_universe_acquire cache is discovered automatically when "
        "present. Supplements may only ADD quote timestamps (original PRIMARY_KEEPFIRST) "
        "and enter the per-day resume hash through their per-file sha256 digest.",
    )
    parser.add_argument(
        "--command",
        choices=("run", "report", "signalcondition"),
        default="run",
        help="run: build per-day intents/executions; report: replay periods/variants/holds/costs; "
        "signalcondition: print the pre-registered qualification",
    )
    args = parser.parse_args()
    ({"run": run, "report": report, "signalcondition": signalcondition})[args.command](args)


PRODUCER_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
CORE_SHA = core_sha256()
CONTRACT_SHA = contract_sha256()

if __name__ == "__main__":
    main()
