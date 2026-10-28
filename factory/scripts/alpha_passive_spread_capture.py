#!/usr/bin/env python3
"""PASSIVE near-touch spread capture on wide-spread TOP-GAINER panel states (honest lane).

The venue study (``factory/artifacts/overnight/venue_edges.json`` -> ``d_spread_capture``)
measured a passive near-touch edge that rises with the displayed spread (+31.3/+46.5/+80.3/
+173.6/+438.1 bps per fill for the 50-75/75-100/100-200/200-500/500+ bps spread buckets)
with the adverse-selection rate falling 46.2% -> 2.0% as the spread widens. Two things made
that an UPPER BOUND, in the study's own words: the fills were QUOTE-TOUCH fills ("not
convertible to $/session without a fill-rate model") and the exit was assumed AT MID, so
no queue, no partial-fill risk and no spread was ever paid.

This producer is the honest version of that lane on the SAME causal substrate:

* UNIVERSE: the causal PIT panel states of the open-anchored top-gainer panel (the
  ``alpha_open_panel`` output at ``~/alpha-data/open-search-v1/days``), restricted to
  (a) the day's top gainers: ``rank_snapshot <= 10`` at the decision state (the causal
  top-10 ranking of the panel's own PIT mover snapshots; a name that has fallen out of the
  top-10 at that state's checkpoint - rank 11 - is excluded) and (b) a displayed spread
  ``>= 50 bps`` at the decision clock. The decision state is the panel minute ``t``; the
  ORDER is placed at ``minute_us(day, t+1)`` 00.000s ET - the same (t+1) intent-clock
  convention every reference producer in this repo uses - observed from the latest RAW
  NBBO state at/before that clock (valid, un-crossed, R-only, age <= 2s: the reference
  producers' operational freshness gate, never a claim that an older NBBO expired).
* ENTRY: a RESTING BUY LIMIT at the near touch = the observed BID at the placement clock.
  The brief allows "at (or inside) the near touch"; an inside limit would raise the fill
  probability at the same fill price, so the deliberately most conservative price of that
  range is used. The order rests for TTL in {15s, 60s, 300s} from the send clock; the
  quantity ``q = floor($1,000 / (limit * (1 + 100bps/2)))`` is causal, sized at the
  HIGHEST reported rung so every reported rung stays funded on the ticket.
* FILL RULE (conservative, stated): a fill is recorded ONLY IF (1) a later consolidated
  TRADE PRINT trades AT OR THROUGH the limit (price <= limit) inside the TTL window (from
  send + 250ms arrival latency to send + TTL), AND (2) the displayed size at the touch at
  the PLACEMENT instant was >= our quantity. A mere quote touch (the quote crossing the
  limit without a print) is NEVER a fill. A resting limit order trades at ITS OWN limit
  price when hit, so the fill price is the observed bid - never a later, more favourable
  print. The STRICT variant additionally requires the displayed touch size at placement
  to be >= 3x our quantity (queue pessimism). A print hit that fails the size gate
  resolves to NO RECORDED FILL: the conservative rule never credits a fill it cannot
  defend, and the placement expires with no position and no cooldown.
* EXIT: a MARKETABLE sell at the opposite touch - the best BID facing the market order,
  the same real-touch convention as the reference producers - after a hold in
  {30s, 60s, 300s} AND {30m, 60m, 300m} from the fill print's timestamp, capped at the
  session's last regular minute; every hold is reported. The submission is
  FRESH-QUOTE-GATED exactly like the reference MARKET exit leg (submit at the due intent
  if the latest raw state is firm and fresh, else hold to the first eligible regular print
  at/after the due intent inside RTH; price +250ms after the ACTUAL submission; a
  valid-but-STALE print at arrival is an UNKNOWN, never a later favourable print; a
  genuinely unavailable book lets the already-working market order rest to the first
  eligible print at/after arrival, and THAT print's clock is the real execution clock).
  The exit BID depth must be >= q, else the fill is an UNKNOWN partial capacity. The
  MID-EXIT variant prices the same fills at the mid of the same exit quote with NO depth
  gate; it mirrors the venue study's exit-at-mid assumption, is labelled UPPER BOUND
  everywhere it appears and is never selectable.
* BOOK: 3 slots x $1,000 = $3,000 nominal research allocation, no leverage, ONE position
  per ticker, flat 15-minute cooldown after an ACTUAL exit. A live resting order occupies
  its slot until its TTL expires, its fill resolves, or its position closes; a later
  signal may use cash released by an earlier actual exit; simultaneous candidates are
  ranked by placement-time spread (widest first, ticker asc) and funded BEFORE any outcome
  is observed. Fees are an extra residual rung (0/25/100 bps round trip) charged on BOTH
  legs on top of the actual touch prices - the touches already carry the spread, so no
  spread is double-charged.
* UNKNOWNS: a placement whose outcome cannot be resolved from the observed tape (the exit
  is not priceable from an eligible regular quote before the session end, the exit print
  is valid-but-stale at arrival, or the exit bid depth is below the causal quantity) is an
  UNKNOWN. Per the programme contract UNKNOWNs are charged the FULL BUDGET (the
  placement's reserved cash - the whole ticket) in a clearly labelled LOWER BOUND and are
  never dropped and never zero; the selection objective itself is the PARTIAL basis
  (known executions excluded from the numerator and reported beside it).

SELECTION (pre-registered before any late file is read): the single grid cell - one (fill
rule, TTL, hold, exit mode) - is chosen ONLY by the 2023 validation block's
KNOWN-contribution dollars per panel session at the 25 bps residual rung on the PARTIAL
basis; ties break by (- objective, - known_fills, + attempts, cell key). The MID exit
mode is an upper bound and is never selectable. The grid, the rules and the choice are
frozen to ``factory/artifacts/overnight/passive_spread_capture.json`` AFTER the
dev+validation replay and BEFORE any late panel, quote or trade file is read; the late
block (2025-02..2026-05, 332 sessions, previously explored, NOT pristine) is then replayed
once, for transparency, with the choice immutable. Protected blocks (2024, 2025-01,
2026-06..08) are never read.

This is a research replay of historical consolidated tape. No live order, no broker call,
no account write. Prints measure what a hypothetical resting order would have seen; they
are not exchange fills and never a fill guarantee.

Usage:
  uv run --no-sync python factory/scripts/alpha_passive_spread_capture.py --smoke --days 2023-05-15
  uv run --no-sync python factory/scripts/alpha_passive_spread_capture.py --stage dev_val
  uv run --no-sync python factory/scripts/alpha_passive_spread_capture.py --stage late
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import os
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))

from alpha_open_panel import allowed
from alpha_quote_aware_frequency import (
    LATENCY_US,
    MAX_AGE_S,
    SymbolQuotes,
    arrival_us,
    minute_us,
    priced_leg,
    quote_day_frame,
    submission_clock,
    symbol_quotes,
)

# ----- fixed configuration (no HPO, no refit, no grid search) ------------------
TTLS_SEC = (15, 60, 300)  # resting-order time-to-live grid, seconds from the send clock
HOLDS = (
    ("30s", 30),
    ("60s", 60),
    ("300s", 300),
    ("30m", 30 * 60),
    ("60m", 60 * 60),
    ("300m", 300 * 60),
)  # exit holds from the fill print; "seconds / minutes" BOTH reported
FILL_RULES = ("base", "strict")  # base: bid size >= q; strict: bid size >= 3q (queue pess.)
EXIT_MODES = ("touch", "mid")  # touch: marketable sell at the bid; mid: UPPER BOUND only
RUNG_BPS = (0, 25, 100)  # extra residual round-trip rungs, charged on BOTH legs
SELECT_RUNG = 25  # the pre-registered selection rung: ONE scenario, never a hurdle
SIZE_RUNG_BPS = 100.0  # causal quantity sized at the HIGHEST reported rung
SPREAD_MIN_BPS = 50.0  # universe gate: displayed spread at the decision clock
RANK_MAX = 10  # universe gate: the day's top-10 gainers at the state's checkpoint
TICKET_USD = 1000.0  # per-position ticket
BOOK_USD = 3 * TICKET_USD  # 3 slots
MAX_SLOTS = 3
COOLDOWN_MIN = 15  # flat minutes after the ACTUAL exit clock
ORDER_LATENCY_US = LATENCY_US  # the resting order is live from send + 250ms
STRICT_SIZE_MULT = 3.0  # queue pessimism multiplier of the strict fill rule
SPREAD_BUCKETS = ((50.0, 75.0), (75.0, 100.0), (100.0, 200.0), (200.0, 500.0), (500.0, None))
BUCKET_KEYS = ("50-75", "75-100", "100-200", "200-500", "500+")
TAIL_N = 5  # best/worst fills retained per cell per block
PROTECTED_UNREAD = ["2024", "2025-01", "2026-06", "2026-07", "2026-08"]
BLOCKS = ("dev", "validation", "late")
BLOCK_RANGES = {
    "dev": ("2021-05-01", "2022-12-31"),
    "validation": ("2023-01-01", "2023-12-31"),
    "late": ("2025-02-01", "2026-05-31"),
}
BLOCK_LABELS = {
    "dev": "2021-05..2022-12 (historical dev block; context only, never the selection basis)",
    "validation": "2023 (the pre-registered selection block)",
    "late": (
        "2025-02..2026-05 (previously explored, NOT pristine; read exactly once and only "
        "after the freeze)"
    ),
}
PANEL_ROOT = Path.home() / "alpha-data" / "open-search-v1"
PANEL_DAYS = PANEL_ROOT / "days"
DATA_ROOT = Path("/home/hillel/projects/Alpacatrader") / "data"
REPO = Path(__file__).resolve().parents[2]
ARTIFACT = REPO / "factory" / "artifacts" / "overnight" / "passive_spread_capture.json"
# The ranked SIP day cache is authoritative; a supplement may only ADD prints.
SUPPLEMENT_ROOTS = (
    PANEL_ROOT / "quote_universe_acquire" / "quotes",
    PANEL_ROOT / "quote_completeness_audit" / "quotes",
    PANEL_ROOT / "sparse_execution_frontier" / "fetched_quotes" / "quotes",
)
STATUS = "DISCOVERY-NOT-VALIDATED"
VERSION = 1
SELECTABLE_MODES = ("touch",)  # the mid exit is an upper bound and never selectable
# The venue study's structural upper bound, quoted verbatim from
# factory/artifacts/overnight/venue_edges.json -> d_spread_capture.table (20 seeded days
# per block, quote-touch fills, exit AT MID, 60s horizon). NOT like-for-like with this
# lane: no queue, no print requirement, mid exit; reported only as the EV this lane has
# to survive.
VENUE_UPPER_BOUND = {
    "source": "factory/artifacts/overnight/venue_edges.json:d_spread_capture",
    "bucket": ["50-75", "75-100", "100-200", "200-500", "500+"],
    "buy_ev_bps_mean": [31.3, 46.53, 80.26, 173.56, 438.1],
    "buy_ev_bps_median": [31.19, 43.96, 73.17, 155.0, 356.51],
    "adverse_either_rate_mean": [0.462, 0.3592, 0.2073, 0.0683, 0.0199],
    "n_minutes": [51913, 36017, 59741, 63532, 28280],
    "fill_basis": "quote-touch fill; EV = mid(t+60s) vs the fill touch, in bps of mid",
    "exit_basis": "at MID (upper bound)",
    "bucket_note": (
        "the venue study's headline quotes 50-75/75-125/125-250/250-500/500+ while its "
        "detailed d_spread_capture table uses 50-75/75-100/100-200/200-500/500+; this lane "
        "reports the detailed table's edges so the bucket split is like-for-like with the "
        "source numbers quoted here"
    ),
    "not_like_for_like": (
        "the venue fills are quote touches with no queue model, no print requirement and "
        "no hold-time choice; this lane requires a print at/through the limit and pays "
        "the marketable exit touch, so the two per-fill EVs are only comparable as bounds"
    ),
}


# ----- small helpers ----------------------------------------------------------
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def producer_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def digest_bytes(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _default(obj):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (set, tuple)):
        return list(obj)
    raise TypeError(f"not serializable: {type(obj)}")


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=1, default=_default) + "\n")
    os.replace(tmp, path)


def cell_key(fill_rule: str, ttl: int, hold: str, mode: str) -> str:
    return f"{fill_rule}|ttl{ttl}s|hold{hold}|{mode}"


def block_days(block: str) -> list[str]:
    """All allowed panel days of one block (panel day NAMES only; no file content read)."""
    lo, hi = BLOCK_RANGES[block]
    out = []
    for path in sorted(PANEL_DAYS.glob("????-??-??.parquet")):
        day = path.stem
        if not allowed(day) or not (lo <= day <= hi):
            continue
        out.append(day)
    return out


def bucket_of(spread_bps: float) -> int:
    for i, (lo, hi) in enumerate(SPREAD_BUCKETS):
        if spread_bps >= lo and (hi is None or spread_bps < hi):
            return i
    return -1


def grid_sort_key(key: str) -> tuple:
    rule, ttl, hold, mode = key.split("|")
    hold = hold[len("hold"):]
    unit = 0 if hold.endswith("s") else 1
    return (rule, int(ttl[3:-1]), unit, int(hold[:-1]), mode)


# ----- the pre-registered specification (frozen before any outcome) -----------
PREREG_SPEC = {
    "study": "alpha_passive_spread_capture",
    "hypothesis": (
        "a passive near-touch spread-capture lane on wide-spread (>=50 bps) top-gainer "
        "causal panel states - resting buy at the observed bid, TTL 15s/60s/300s, "
        "print-required size-gated fills, marketable bid exit after a 30s/60s/300s/30m/"
        "60m/300m hold - clears a 25 bps and a 100 bps extra residual round-trip rung "
        "once queue and partial-fill risk are modelled conservatively"
    ),
    "universe": {
        "states": "the causal PIT open-anchored top-gainer panel (alpha_open_panel days)",
        "top_gainer_cap": (
            "rank_snapshot <= 10 at the decision state (the panel's causal top-10 "
            "mover-snapshot ranking; the pool is the day's top gainers by construction)"
        ),
        "spread_gate": "displayed spread >= 50 bps at the decision clock",
        "decision_clock": (
            "panel state minute t; the order is placed at minute_us(day, t+1) 00.000s ET "
            "(this repo's (t+1) intent-clock convention), observed from the latest RAW "
            "NBBO state at/before that clock (valid, un-crossed, R-only, age <= 2s)"
        ),
        "liquidity_gate": (
            "NONE beyond the panel's own admission: no causal_liquidity filter, no model "
            "score; the venue study's structural population is mirrored causally instead"
        ),
    },
    "entry": {
        "side": "BUY (long only; no leverage, no short borrow)",
        "limit_price": (
            "the observed BID at the placement clock - the near touch; the brief's allowed "
            "'at (or inside)' range is deliberately reduced to its most conservative price "
            "(an inside limit would only raise the fill probability at the same fill price)"
        ),
        "ttl_seconds": list(TTLS_SEC),
        "quantity": (
            "q = floor($1,000 / (limit * (1 + 100bps/2))), causal, sized at the highest "
            "reported rung so every reported rung is funded on the ticket"
        ),
        "liveness": (
            "the order is live from send + 250ms arrival latency to send + TTL (truncated "
            "at the session's last regular minute); a live resting order occupies its book "
            "slot until it expires, fills or its position closes"
        ),
    },
    "fill_rule_base": (
        "a fill is recorded ONLY IF (1) a later consolidated trade print trades at or "
        "through the limit (price <= limit) inside the TTL window AND (2) the displayed "
        "size at the touch at the placement instant was >= q; the fill price is the "
        "resting order's own limit. A mere quote touch is NEVER a fill."
    ),
    "fill_rule_strict": (
        "the base rule AND the displayed touch size at placement was >= 3*q (queue "
        "pessimism)"
    ),
    "exit": {
        "holds": [h for h, _ in HOLDS],
        "units": (
            "seconds and minutes both reported; each hold measured from the fill print and "
            "capped at the session's last regular minute"
        ),
        "touch_mode": (
            "marketable sell at the best BID (fresh-quote-gated submission with the "
            "reference MARKET-leg semantics; exit bid depth >= q else UNKNOWN)"
        ),
        "mid_mode": (
            "sell at the MID of the same priced exit quote, NO depth gate: the UPPER-BOUND "
            "leg (mirrors the venue study's exit-at-mid assumption), labelled upper bound "
            "everywhere and never selectable"
        ),
    },
    "rungs_bps": list(RUNG_BPS),
    "book": {
        "slots": MAX_SLOTS,
        "ticket_usd": TICKET_USD,
        "book_usd": BOOK_USD,
        "one_position_per_ticker": True,
        "cooldown_min_after_actual_exit": COOLDOWN_MIN,
        "fees": (
            "extra residual rung 0/25/100 bps round trip charged on BOTH legs on top of "
            "the actual touch prices (the touches already carry the spread; no double "
            "charge)"
        ),
    },
    "unknown_rule": (
        "an unresolvable placement (the exit is not priceable from an eligible regular "
        "quote before the session end, the exit print is valid-but-stale at arrival, or "
        "the exit bid depth < q) is an UNKNOWN: charged the full budget (its reserved cash, "
        "the whole ticket) in a labelled LOWER BOUND, never dropped, never zero; the "
        "selection objective is the PARTIAL basis (known executions only, unknowns "
        "reported beside it)"
    ),
    "selection_rule": {
        "objective": (
            "2023 validation block KNOWN-contribution dollars per panel session at the "
            "25 bps residual rung, PARTIAL basis"
        ),
        "grid": (
            "fill rule {base, strict} x TTL {15s, 60s, 300s} x hold {30s, 60s, 300s, 30m, "
            "60m, 300m} x exit mode {touch, mid} = 72 cells; only the 36 TOUCH-exit cells "
            "are selectable (mid is the labelled upper bound)"
        ),
        "tie_break": "(- objective $/session, - known_fills, + attempts, cell key)",
        "floors": (
            "no power floor, no median gate, no bootstrap gate, no cost/count kill, no "
            "full-loss veto on the SELECTION"
        ),
        "frozen": (
            "factory/artifacts/overnight/passive_spread_capture.json is written with this "
            "pre-registration and the chosen cell AFTER the dev+validation replay and "
            "BEFORE any late panel, quote or trade file is read; the late block is then "
            "replayed once and the choice is immutable"
        ),
    },
    "blocks": {
        "dev": BLOCK_LABELS["dev"],
        "validation": BLOCK_LABELS["validation"],
        "late": BLOCK_LABELS["late"],
        "sealed_never_read": PROTECTED_UNREAD,
    },
    "status_at_registration": (
        "PRE-REGISTERED BEFORE ANY BLOCK OUTCOME EXISTS: no replay, no aggregate and no "
        "artifact number has been produced by this lane at registration time; the parent "
        "brief and the venue study's structural table are the only inputs"
    ),
}
PREREG_SHA = digest_bytes(PREREG_SPEC)


# ----- per-day substrate ------------------------------------------------------
class Trades:
    """One ticker's consolidated trade prints for one ET session (raw, unfiltered)."""

    __slots__ = ("ts", "price", "size")

    def __init__(self, ts: np.ndarray, price: np.ndarray, size: np.ndarray):
        self.ts = ts
        self.price = price
        self.size = size


def load_trades(day: str, tickers: set[str]) -> tuple[dict[str, Trades], bool]:
    """The day's consolidated trade prints for ``tickers`` (one day resident)."""
    path = DATA_ROOT / "sip" / "net" / "trades" / f"{day}.parquet"
    if not path.exists():
        return {}, False
    frame = (
        pl.scan_parquet(path)
        .filter(pl.col("symbol").is_in(sorted(tickers)))
        .select(["symbol", "ts_utc", "price", "size"])
        .collect()
    )
    out: dict[str, Trades] = {}
    if frame.height:
        frame = frame.sort(["symbol", "ts_utc"])
        for (symbol,), part in frame.partition_by("symbol", as_dict=True).items():
            out[str(symbol)] = Trades(
                part["ts_utc"].cast(pl.Int64).to_numpy().astype(np.int64),
                part["price"].to_numpy().astype(float),
                part["size"].to_numpy().astype(float),
            )
    return out, True


def build_candidates(day: str, cov: Counter) -> tuple[list[dict], int]:
    """The day's universe: panel states -> placement observations at the (t+1) clock."""
    panel = pl.read_parquet(PANEL_DAYS / f"{day}.parquet")
    cov["panel_rows"] += int(panel.height)
    session_end = int(panel["session_end"][0])
    session_end_us = minute_us(day, session_end)
    rows = []
    for r in panel.iter_rows(named=True):
        rank = r["rank_snapshot"]
        if rank is None or not math.isfinite(float(rank)) or float(rank) > RANK_MAX:
            cov["universe_rank_above_10"] += 1
            continue
        rows.append(r)
    tickers = {r["ticker"] for r in rows}
    quote_frame = quote_day_frame(DATA_ROOT, day, tickers, SUPPLEMENT_ROOTS)
    streams = symbol_quotes(quote_frame, day)
    del quote_frame
    cands: list[dict] = []
    for r in rows:
        t = int(r["t"])
        send_us = minute_us(day, t + 1)
        sq = streams.get(r["ticker"])
        if sq is None:
            cov["quote_not_acquired"] += 1
            continue
        q, status = sq.latest_raw(send_us)
        if q is None:
            cov[f"intent_{status}"] += 1
            continue
        if q["age_s"] is None or not math.isfinite(q["age_s"]) or q["age_s"] > MAX_AGE_S:
            cov["intent_not_firm_fresh"] += 1
            continue
        spread = q["spread_bps"]
        if spread is None or not math.isfinite(spread) or spread < SPREAD_MIN_BPS:
            cov["universe_spread_below_50bps"] += 1
            continue
        limit = float(q["bid"])
        qty = int(TICKET_USD // (limit * (1.0 + SIZE_RUNG_BPS / 20_000.0)))
        if qty < 1:
            cov["no_order_min_capital"] += 1
            continue
        cands.append(
            {
                "day": day,
                "ticker": r["ticker"],
                "t": t,
                "send_us": int(send_us),
                "limit": limit,
                "qty": int(qty),
                "reserved": float(qty) * limit * (1.0 + SIZE_RUNG_BPS / 20_000.0),
                "spread_bps": float(spread),
                "bid_shares": float(q["bid_shares"]),
                "quote_us": int(q["quote_us"]),
                "rank": float(r["rank_snapshot"]),
                "admit_t": int(r["admit_t"]) if r["admit_t"] is not None else None,
                "admit_gain": float(r["admit_gain"]) if r["admit_gain"] is not None else None,
                "bucket": bucket_of(float(spread)),
                "sq": sq,
                "session_end_us": int(session_end_us),
                "fills": {},
                "exits": {},
            }
        )
    cov["universe_candidates"] += len(cands)
    cov["universe_names"] += len({c["ticker"] for c in cands})
    cov["universe_quoted_names"] += len(streams)
    cov["cand_size_ge_q"] += int(sum(1 for c in cands if c["bid_shares"] >= c["qty"]))
    cov["cand_size_ge_3q"] += int(
        sum(1 for c in cands if c["bid_shares"] >= STRICT_SIZE_MULT * c["qty"])
    )
    cov["cand_round_lot_epoch"] += int(sum(1 for c in cands if day < "2025-11-03"))
    cov["cand_single_share_epoch"] += int(sum(1 for c in cands if day >= "2025-11-03"))
    return cands, session_end


def resolve_fills(
    cands: list, trades: dict, session_end_us: int, resolvable: bool
) -> None:
    """Per candidate and TTL: the first print at/through the limit inside the TTL.

    A day whose consolidated trade tape is not observable leaves every placement
    UNRESOLVED (state ``"unresolved"``): the replay then charges it the full budget as an
    UNKNOWN instead of silently recording an unfilled expiry.
    """
    for c in cands:
        tr = trades.get(c["ticker"]) if resolvable else None
        arrival = c["send_us"] + ORDER_LATENCY_US
        for ttl in TTLS_SEC:
            hit = False
            fill_us = None
            if tr is not None:
                end = min(c["send_us"] + ttl * 1_000_000, session_end_us)
                if end > arrival:
                    i0 = int(np.searchsorted(tr.ts, arrival))
                    j = int(np.searchsorted(tr.ts, end))
                    if j > i0:
                        idx = np.flatnonzero(tr.price[i0:j] <= c["limit"] + 1e-9)
                        if idx.size:
                            hit = True
                            fill_us = int(tr.ts[i0 + int(idx[0])])
            if not resolvable:
                state = "unresolved"  # the tape is not observable: never a silent no-fill
            else:
                state = "hit" if hit else "miss"  # an observable tape with no print expires
            c["fills"][ttl] = (state, fill_us)


def resolve_exits(cands: list[dict]) -> None:
    """Per candidate, per TTL-with-a-print-hit, hold: the exit leg (mode-independent)."""
    for c in cands:
        sq = c["sq"]
        for ttl, (state, fill_us) in c["fills"].items():
            if state != "hit":
                continue
            for hold_key, hold_s in HOLDS:
                hold_us = int(hold_s) * 1_000_000
                due = min(int(fill_us) + hold_us, c["session_end_us"])
                submit_us, _ = submission_clock(sq, due, c["session_end_us"])
                rec: dict = {
                    "status": None,
                    "touch_px": None,
                    "mid_px": None,
                    "bid_shares": None,
                    "quote_us": None,
                    "priced_at": None,
                }
                if submit_us is None:
                    rec["status"] = "unknown_no_valid_regular_quote_before_session_end"
                else:
                    q2, st2, priced_at = priced_leg(
                        sq, arrival_us(submit_us), c["session_end_us"]
                    )
                    if q2 is None:
                        rec["status"] = f"unknown_exit:{st2}"
                    else:
                        if priced_at is None:
                            raise RuntimeError(
                                f"priced exit without a price clock: {c['day']} {c['ticker']}"
                            )
                        rec.update(
                            {
                                "status": "priced",
                                "touch_px": float(q2["bid"]),
                                "mid_px": 0.5 * (float(q2["bid"]) + float(q2["ask"])),
                                "bid_shares": float(q2["bid_shares"]),
                                "quote_us": int(q2["quote_us"]),
                                "priced_at": int(priced_at),
                            }
                        )
                c["exits"][(ttl, hold_key)] = rec


def size_gate_ok(cand: dict, fill_rule: str) -> bool:
    need = cand["qty"] if fill_rule == "base" else STRICT_SIZE_MULT * cand["qty"]
    return float(cand["bid_shares"]) >= float(need)


def nets_usd(qty: int, limit: float, exit_px: float) -> list[float]:
    out = []
    for r in RUNG_BPS:
        side = r / 20_000.0
        out.append(float(qty) * (exit_px * (1.0 - side) - limit * (1.0 + side)))
    return out


# ----- one session, all 72 cells under the $3,000 / 3-slot book ----------------
def replay_day_configs(cands: list[dict], session_end_us: int) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    if not cands:
        return rows
    ordered = sorted(cands, key=lambda c: (c["send_us"], -c["spread_bps"], c["ticker"]))
    for fill_rule in FILL_RULES:
        for ttl in TTLS_SEC:
            ttl_us = ttl * 1_000_000
            for hold_key, _ in HOLDS:
                for mode in EXIT_MODES:
                    row = {
                        "placements": 0,
                        "unfilled_no_print": 0,
                        "unfilled_size_gate": 0,
                        "fills": 0,
                        "unknowns": 0,
                        "unknown_no_quote": 0,
                        "unknown_stale": 0,
                        "unknown_partial_depth": 0,
                        "unknown_unresolved_print": 0,
                        "known_usd": {r: 0.0 for r in RUNG_BPS},
                        "reserved_unknown": 0.0,
                        "reserved_deployed": 0.0,
                        "bucket_placements": {b: 0 for b in BUCKET_KEYS},
                        "bucket_fills": {b: 0 for b in BUCKET_KEYS},
                        "bucket_known_usd": {
                            b: {r: 0.0 for r in RUNG_BPS} for b in BUCKET_KEYS
                        },
                        "fill_nets": [],
                        "fill_buckets": [],
                        "fill_rows": [],
                    }
                    cash = BOOK_USD
                    active: set[str] = set()
                    cooldown_until: dict[str, int] = {}
                    queue: list[tuple[int, int, str, float]] = []
                    seq = 0
                    for c in ordered:
                        send_us = c["send_us"]
                        while queue and queue[0][0] <= send_us:
                            _, _, sym, proceeds = heapq.heappop(queue)
                            active.discard(sym)
                            cash += proceeds
                        ticker = c["ticker"]
                        if ticker in active:
                            continue
                        if send_us < int(cooldown_until.get(ticker, -(1 << 62))):
                            continue
                        if len(active) >= MAX_SLOTS or cash + 1e-9 < c["reserved"]:
                            continue
                        # The placement is funded BEFORE any outcome is observed.
                        cash -= c["reserved"]
                        active.add(ticker)
                        row["placements"] += 1
                        row["reserved_deployed"] += c["reserved"]
                        bk = BUCKET_KEYS[c["bucket"]] if c["bucket"] >= 0 else None
                        if bk:
                            row["bucket_placements"][bk] += 1
                        hit, fill_us = c["fills"][ttl]
                        expiry = min(send_us + ttl_us, session_end_us)
                        if hit == "unresolved":
                            # A placement with no resolvable print is an UNKNOWN: charged
                            # the full budget in the lower bound, never dropped, never zero.
                            row["unknowns"] += 1
                            row["reserved_unknown"] += c["reserved"]
                            row["unknown_unresolved_print"] += 1
                            heapq.heappush(
                                queue, (session_end_us + 1, seq, ticker, c["reserved"])
                            )
                            cooldown_until[ticker] = (
                                session_end_us + 1 + COOLDOWN_MIN * 60_000_000
                            )
                            seq += 1
                            continue
                        if hit != "hit":
                            row["unfilled_no_print"] += 1
                            heapq.heappush(queue, (expiry, seq, ticker, c["reserved"]))
                            seq += 1
                            continue
                        if not size_gate_ok(c, fill_rule):
                            # A print traded through the limit but the displayed touch size
                            # at placement cannot defend the fill: NO RECORDED FILL. The
                            # order expired; no position and no cooldown.
                            row["unfilled_size_gate"] += 1
                            heapq.heappush(queue, (expiry, seq, ticker, c["reserved"]))
                            seq += 1
                            continue
                        ex = c["exits"][(ttl, hold_key)]
                        notional = float(c["qty"]) * float(c["limit"])
                        if ex["status"] != "priced":
                            row["unknowns"] += 1
                            row["reserved_unknown"] += c["reserved"]
                            row["unknown_stale" if "unknown_exit" in ex["status"] else "unknown_no_quote"] += 1
                            release = session_end_us + 1
                        elif mode == "touch" and ex["bid_shares"] < c["qty"]:
                            row["unknowns"] += 1
                            row["reserved_unknown"] += c["reserved"]
                            row["unknown_partial_depth"] += 1
                            release = session_end_us + 1
                        else:
                            exit_px = ex["touch_px"] if mode == "touch" else ex["mid_px"]
                            nets = nets_usd(c["qty"], c["limit"], exit_px)
                            row["fills"] += 1
                            if bk:
                                row["bucket_fills"][bk] += 1
                            for i, r in enumerate(RUNG_BPS):
                                row["known_usd"][r] += nets[i]
                                if bk:
                                    row["bucket_known_usd"][bk][r] += nets[i]
                            row["fill_nets"].append(list(nets))
                            row["fill_buckets"].append(bk)
                            row["fill_rows"].append(
                                {
                                    "day": c["day"],
                                    "ticker": c["ticker"],
                                    "t": c["t"],
                                    "fill_us": int(fill_us),
                                    "limit": c["limit"],
                                    "qty": int(c["qty"]),
                                    "notional": notional,
                                    "spread_bps": c["spread_bps"],
                                    "bid_shares": c["bid_shares"],
                                    "exit_px": float(exit_px),
                                    "held_s": (int(ex["priced_at"]) - int(fill_us)) / 1e6,
                                    "net_usd": {r: nets[i] for i, r in enumerate(RUNG_BPS)},
                                }
                            )
                            release = int(ex["priced_at"])
                        heapq.heappush(queue, (release, seq, ticker, c["reserved"]))
                        cooldown_until[ticker] = release + COOLDOWN_MIN * 60_000_000
                        seq += 1
                    rows[cell_key(fill_rule, ttl, hold_key, mode)] = row
    return rows


# ----- per-cell accumulation across a block ------------------------------------
class CellAcc:
    def __init__(self, fill_rule: str, ttl: int, hold: str, mode: str):
        self.fill_rule = fill_rule
        self.ttl = ttl
        self.hold = hold
        self.mode = mode
        self.key = cell_key(fill_rule, ttl, hold, mode)
        self.days = 0
        self.days_with_placement = 0
        self.placements = 0
        self.unfilled_no_print = 0
        self.unfilled_size_gate = 0
        self.fills = 0
        self.unknowns = 0
        self.unknown_no_quote = 0
        self.unknown_stale = 0
        self.unknown_partial_depth = 0
        self.unknown_unresolved_print = 0
        self.known_usd = {r: 0.0 for r in RUNG_BPS}
        self.reserved_unknown = 0.0
        self.reserved_deployed = 0.0
        self.bucket_placements = {b: 0 for b in BUCKET_KEYS}
        self.bucket_fills = {b: 0 for b in BUCKET_KEYS}
        self.bucket_known_usd = {b: {r: 0.0 for r in RUNG_BPS} for b in BUCKET_KEYS}
        self.fill_rows: list[dict] = []
        self.bucket_fill_rows: dict[str, list[dict]] = {b: [] for b in BUCKET_KEYS}
        self.month_known = {r: {} for r in RUNG_BPS}
        self.month_days: dict[str, int] = {}

    def merge_day(self, month: str, row: dict) -> None:
        self.days += 1
        if row["placements"]:
            self.days_with_placement += 1
        self.placements += row["placements"]
        self.unfilled_no_print += row["unfilled_no_print"]
        self.unfilled_size_gate += row["unfilled_size_gate"]
        self.fills += row["fills"]
        self.unknowns += row["unknowns"]
        self.unknown_no_quote += row["unknown_no_quote"]
        self.unknown_stale += row["unknown_stale"]
        self.unknown_partial_depth += row["unknown_partial_depth"]
        self.unknown_unresolved_print += row["unknown_unresolved_print"]
        self.reserved_unknown += row["reserved_unknown"]
        self.reserved_deployed += row["reserved_deployed"]
        for r in RUNG_BPS:
            self.known_usd[r] += row["known_usd"][r]
            self.month_known[r][month] = self.month_known[r].get(month, 0.0) + row["known_usd"][r]
        for b in BUCKET_KEYS:
            self.bucket_placements[b] += row["bucket_placements"][b]
            self.bucket_fills[b] += row["bucket_fills"][b]
            for r in RUNG_BPS:
                self.bucket_known_usd[b][r] += row["bucket_known_usd"][b][r]
        for fr, bk in zip(row["fill_rows"], row["fill_buckets"]):
            self.fill_rows.append(fr)
            if bk:
                self.bucket_fill_rows[bk].append(fr)
        self.month_days[month] = self.month_days.get(month, 0) + 1

    def view(self, sessions: int) -> dict:
        sel = int(SELECT_RUNG)
        known_per_session = {r: self.known_usd[r] / sessions for r in RUNG_BPS}
        lower = {r: known_per_session[r] - self.reserved_unknown / sessions for r in RUNG_BPS}

        def col(rows: list[dict], r: int) -> np.ndarray:
            return np.asarray([x["net_usd"][r] for x in rows], dtype=float) if rows else np.array([])

        nets = {r: col(self.fill_rows, r) for r in RUNG_BPS}
        notional = np.asarray([x["notional"] for x in self.fill_rows], dtype=float)
        months_pos = {r: sum(1 for v in self.month_known[r].values() if v > 0) for r in RUNG_BPS}
        out = {
            "cell_key": self.key,
            "fill_rule": self.fill_rule,
            "ttl_s": self.ttl,
            "hold": self.hold,
            "exit_mode": self.mode,
            "selectable": self.mode in SELECTABLE_MODES,
            "days_replayed": self.days,
            "days_with_placement": self.days_with_placement,
            "placements": self.placements,
            "fills": self.fills,
            "unknowns": self.unknowns,
            "unknown_no_quote": self.unknown_no_quote,
            "unknown_stale_exit": self.unknown_stale,
            "unknown_partial_depth": self.unknown_partial_depth,
            "unknown_unresolved_print": self.unknown_unresolved_print,
            "unfilled_no_print": self.unfilled_no_print,
            "unfilled_size_gate": self.unfilled_size_gate,
            "fill_rate": (self.fills / self.placements) if self.placements else None,
            "unknown_share_of_placements": (
                self.unknowns / self.placements if self.placements else None
            ),
            "known_usd": {r: self.known_usd[r] for r in RUNG_BPS},
            "usd_per_panel_session": {r: known_per_session[r] for r in RUNG_BPS},
            "full_loss_lower_bound_usd_per_panel_session": lower,
            "reserved_deployed_usd": self.reserved_deployed,
            "reserved_unknown_usd": self.reserved_unknown,
            "months_positive": {
                r: {
                    "months_positive": months_pos[r],
                    "months_with_sessions": len(self.month_days),
                }
                for r in RUNG_BPS
            },
            "monthly": {
                r: {
                    m: {
                        "sessions": self.month_days.get(m, 0),
                        "known_usd": v,
                        "usd_per_session": (
                            v / self.month_days[m] if self.month_days.get(m) else None
                        ),
                    }
                    for m, v in sorted(self.month_known[r].items())
                }
                for r in RUNG_BPS
            },
            "trade_stats": {
                "known_fills": int(self.fills),
                "median_fill_usd": {
                    r: (float(np.median(nets[r])) if nets[r].size else None) for r in RUNG_BPS
                },
                "mean_fill_usd": {
                    r: (float(np.mean(nets[r])) if nets[r].size else None) for r in RUNG_BPS
                },
                "median_fill_bps_of_limit": {
                    r: (
                        float(np.median(nets[r] / notional) * 1e4)
                        if nets[r].size and notional.size
                        else None
                    )
                    for r in RUNG_BPS
                },
                "mean_fill_bps_of_limit": {
                    r: (
                        float(np.mean(nets[r] / notional) * 1e4)
                        if nets[r].size and notional.size
                        else None
                    )
                    for r in RUNG_BPS
                },
                "win_rate": {
                    r: (float(np.mean(nets[r] > 0)) if nets[r].size else None) for r in RUNG_BPS
                },
                "p10_fill_usd": {
                    r: (float(np.percentile(nets[r], 10)) if nets[r].size else None)
                    for r in RUNG_BPS
                },
                "p90_fill_usd": {
                    r: (float(np.percentile(nets[r], 90)) if nets[r].size else None)
                    for r in RUNG_BPS
                },
                "mean_held_s": (
                    float(np.mean([x["held_s"] for x in self.fill_rows]))
                    if self.fill_rows
                    else None
                ),
                "top5_share_of_known_usd": {
                    r: (
                        float(np.sum(np.sort(nets[r])[-5:]) / np.sum(nets[r]))
                        if nets[r].size and float(np.sum(nets[r])) != 0.0
                        else None
                    )
                    for r in RUNG_BPS
                },
                "best_fills": sorted(
                    self.fill_rows, key=lambda x: x["net_usd"][sel], reverse=True
                )[:TAIL_N],
                "worst_fills": sorted(self.fill_rows, key=lambda x: x["net_usd"][sel])[:TAIL_N],
                "fill_basis_note": (
                    "per-fill net USD = q * (exit_px * (1 - r/2e4) - limit * (1 + r/2e4)); q "
                    "is the causal quantity sized at the 100 bps rung, so one fill is "
                    "funded at every rung; bps are of the entry (limit) price"
                ),
            },
            "buckets": {
                b: self._bucket_view(b, sessions) for b in BUCKET_KEYS
            },
        }
        return out

    def _bucket_view(self, b: str, sessions: int) -> dict:
        sel = int(SELECT_RUNG)
        rows = self.bucket_fill_rows[b]
        nets = {r: np.asarray([x["net_usd"][r] for x in rows], dtype=float) if rows else np.array([]) for r in RUNG_BPS}
        notional = np.asarray([x["notional"] for x in rows], dtype=float)
        return {
            "placements": self.bucket_placements[b],
            "fills": self.bucket_fills[b],
            "fill_rate": (
                self.bucket_fills[b] / self.bucket_placements[b]
                if self.bucket_placements[b]
                else None
            ),
            "known_usd": {r: self.bucket_known_usd[b][r] for r in RUNG_BPS},
            "usd_per_panel_session": {
                r: self.bucket_known_usd[b][r] / sessions for r in RUNG_BPS
            },
            "median_fill_usd": {
                r: (float(np.median(nets[r])) if nets[r].size else None) for r in RUNG_BPS
            },
            "mean_fill_usd": {
                r: (float(np.mean(nets[r])) if nets[r].size else None) for r in RUNG_BPS
            },
            "median_fill_bps_of_limit": {
                r: (
                    float(np.median(nets[r] / notional) * 1e4)
                    if nets[r].size and notional.size
                    else None
                )
                for r in RUNG_BPS
            },
            "mean_fill_bps_of_limit": {
                r: (
                    float(np.mean(nets[r] / notional) * 1e4)
                    if nets[r].size and notional.size
                    else None
                )
                for r in RUNG_BPS
            },
            "win_rate": {
                r: (float(np.mean(nets[r] > 0)) if nets[r].size else None) for r in RUNG_BPS
            },
        }


def replay_block(block: str, days: list[str], verbose: bool = True) -> tuple[dict, dict]:
    """One block, all 72 cells, one day resident at a time."""
    cells = {
        cell_key(fr, ttl, hold, mode): CellAcc(fr, ttl, hold, mode)
        for fr in FILL_RULES
        for ttl in TTLS_SEC
        for hold, _ in HOLDS
        for mode in EXIT_MODES
    }
    cov = Counter(
        {
            "block": block,
            "days": len(days),
            "sessions": len(days),
        }
    )
    t0 = time.time()
    for n, day in enumerate(days, 1):
        cands, session_end = build_candidates(day, cov)
        if cands:
            trades, trades_file = load_trades(day, {c["ticker"] for c in cands})
            cov[
                "days_trades_file_present" if trades_file else "days_trades_file_missing"
            ] += 1
            resolve_fills(cands, trades, minute_us(day, session_end), trades_file)
            resolve_exits(cands)
            del trades
        rows = replay_day_configs(cands, minute_us(day, session_end))
        month = day[:7]
        for key, row in rows.items():
            cells[key].merge_day(month, row)
        del cands, rows
        if verbose and (n % 25 == 0 or n == len(days)):
            ints = " ".join(f"{k}={cov[k]}" for k in sorted(cov) if isinstance(cov[k], int))
            print(f"[{block}] {n}/{len(days)} {day} elapsed={time.time() - t0:.0f}s {ints}", flush=True)
    return cells, cov


# ----- selection, verdicts and artifact ---------------------------------------
def choose_cell(cells: dict, sessions: int) -> tuple[CellAcc, list[dict]]:
    sel = int(SELECT_RUNG)
    ranked = sorted(
        (c for c in cells.values() if c.selectable),
        key=lambda c: (-(c.known_usd[sel] / sessions), -c.fills, c.placements, c.key),
    )
    ranking = [
        {
            "rank": i + 1,
            "cell_key": c.key,
            "fill_rule": c.fill_rule,
            "ttl_s": c.ttl,
            "hold": c.hold,
            "exit_mode": c.mode,
            "validation_usd_per_panel_session_at_25": c.known_usd[sel] / sessions,
            "known_fills": c.fills,
            "placements": c.placements,
            "unknowns": c.unknowns,
            "months_positive_at_25": sum(
                1 for v in c.month_known[sel].values() if v > 0
            ),
        }
        for i, c in enumerate(ranked)
    ]
    return ranked[0], ranking


def grid_table(cells: dict, sessions: int) -> list[dict]:
    return [cells[k].view(sessions) for k in sorted(cells, key=grid_sort_key)]


def strict_axis(cells_by_block: dict, sessions: dict) -> dict:
    """The acceptance axis: every strictly-filled touch cell, per block, at both rungs."""
    sel = int(SELECT_RUNG)
    out: dict = {}
    for block, cells in cells_by_block.items():
        rows = []
        for ttl in TTLS_SEC:
            for hold, _ in HOLDS:
                c = cells[cell_key("strict", ttl, hold, "touch")]
                nets = np.asarray([x["net_usd"][sel] for x in c.fill_rows], dtype=float)
                rows.append(
                    {
                        "cell_key": c.key,
                        "fill_rule": "strict",
                        "ttl_s": ttl,
                        "hold": hold,
                        "exit_mode": "touch",
                        "usd_per_panel_session_25": c.known_usd[sel] / sessions[block],
                        "usd_per_panel_session_100": c.known_usd[100] / sessions[block],
                        "placements": c.placements,
                        "known_fills": c.fills,
                        "unknowns": c.unknowns,
                        "unknown_share": (c.unknowns / c.placements) if c.placements else None,
                        "fill_rate": (c.fills / c.placements) if c.placements else None,
                        "median_fill_usd_25": float(np.median(nets)) if nets.size else None,
                        "win_rate_25": float(np.mean(nets > 0)) if nets.size else None,
                        "months_positive_25": sum(1 for v in c.month_known[sel].values() if v > 0),
                        "months_with_sessions": len(c.month_days),
                        "full_loss_lower_bound_usd_per_session_25": (
                            c.known_usd[sel] / sessions[block]
                            - c.reserved_unknown / sessions[block]
                        ),
                    }
                )
        out[block] = rows
    return out


def cell_bps(cell: CellAcc, rung: int) -> dict:
    """Per-fill bps of the entry limit and the $/session of one cell at one rung."""
    rows = cell.fill_rows
    if not rows:
        return {"mean_fill_bps": None, "median_fill_bps": None, "n_fills": 0}
    nets = np.asarray([x["net_usd"][rung] for x in rows], dtype=float)
    notional = np.asarray([x["notional"] for x in rows], dtype=float)
    return {
        "mean_fill_bps": float(np.mean(nets / notional) * 1e4),
        "median_fill_bps": float(np.median(nets / notional) * 1e4),
        "n_fills": int(rows and len(rows)),
    }


def destruction_block(blocks_cells: dict, chosen: CellAcc, sessions: dict) -> dict:
    """How much of this lane's own mid-exit UPPER BOUND the conservative rules destroy."""
    sel = int(SELECT_RUNG)
    return {
        "chosen_cell": chosen.key,
        "selection_rung_bps": sel,
        "note": (
            "UPPER BOUND = the SAME cell with the MID exit (exit at the mid of the exit "
            "quote, no depth gate) - the venue study's exit assumption; the honest cells "
            "are the marketable-touch exits. Upper-bound and honest rows of one fill rule "
            "share the same fill records, so the shares are within-lane. The venue study's "
            "own numbers are quoted separately under venue_reference and are NOT "
            "like-for-like (quote-touch fills, 60s horizon, mid exit, no queue)."
        ),
        "blocks": {
            block: _destruction_rows(
                cells,
                chosen.key,
                chosen.fill_rule,
                chosen.ttl,
                chosen.hold,
                sessions[block],
            )
            for block, cells in blocks_cells.items()
        },
    }


def bucket_destruction(cells: dict, rule: str, ttl: int, hold: str) -> dict:
    """Per spread bucket: the venue upper bound vs this lane's mid/touch/base/strict EVs."""
    sel = int(SELECT_RUNG)
    out = {}
    for i, b in enumerate(BUCKET_KEYS):
        base_mid = cells[cell_key(rule, ttl, hold, "mid")]
        base_touch = cells[cell_key(rule, ttl, hold, "touch")]
        strict_touch = cells[cell_key("strict", ttl, hold, "touch")]
        strict_mid = cells[cell_key("strict", ttl, hold, "mid")]

        def bucket_bps(cell: CellAcc) -> float | None:
            rows = cell.bucket_fill_rows[b]
            if not rows:
                return None
            nets = np.asarray([x["net_usd"][sel] for x in rows], dtype=float)
            notional = np.asarray([x["notional"] for x in rows], dtype=float)
            return float(np.mean(nets / notional) * 1e4)

        ub = bucket_bps(base_mid)
        honest = bucket_bps(base_touch)
        strict = bucket_bps(strict_touch)
        row = {
            "venue_upper_bound_buy_ev_bps_mean": VENUE_UPPER_BOUND["buy_ev_bps_mean"][i],
            "venue_adverse_either_rate_mean": VENUE_UPPER_BOUND["adverse_either_rate_mean"][i],
            "venue_fill_basis": VENUE_UPPER_BOUND["fill_basis"],
            "lane_upper_bound_base_mid_exit_mean_fill_bps": ub,
            "lane_honest_base_touch_exit_mean_fill_bps": honest,
            "lane_strict_touch_exit_mean_fill_bps": strict,
            "lane_strict_mid_exit_mean_fill_bps": bucket_bps(strict_mid),
            "base_placements": base_touch.bucket_placements[b],
            "base_known_fills": base_touch.bucket_fills[b],
            "strict_placements": strict_touch.bucket_placements[b],
            "strict_known_fills": strict_touch.bucket_fills[b],
            "base_known_usd": base_touch.bucket_known_usd[b][sel],
            "strict_known_usd": strict_touch.bucket_known_usd[b][sel],
        }
        if ub:
            if honest is not None:
                row["base_share_of_lane_upper_bound_bps"] = honest / ub
            if strict is not None:
                row["strict_share_of_lane_upper_bound_bps"] = strict / ub
        out[b] = row
    return out


def chosen_cell_block(cell: CellAcc, sessions: int) -> dict:
    sel = int(SELECT_RUNG)
    nets = np.asarray([x["net_usd"][sel] for x in cell.fill_rows], dtype=float)
    return {
        "cell_key": cell.key,
        "fill_rule": cell.fill_rule,
        "ttl_s": cell.ttl,
        "hold": cell.hold,
        "exit_mode": cell.mode,
        "usd_per_panel_session_25": cell.known_usd[sel] / sessions,
        "usd_per_panel_session_100": cell.known_usd[100] / sessions,
        "placements": cell.placements,
        "known_fills": cell.fills,
        "unknowns": cell.unknowns,
        "unfilled_no_print": cell.unfilled_no_print,
        "unfilled_size_gate": cell.unfilled_size_gate,
        "fill_rate": (cell.fills / cell.placements) if cell.placements else None,
        "median_fill_usd_25": float(np.median(nets)) if nets.size else None,
        "win_rate_25": float(np.mean(nets > 0)) if nets.size else None,
        "months_positive_25": sum(1 for v in cell.month_known[sel].values() if v > 0),
        "months_with_sessions": len(cell.month_days),
        "full_loss_lower_bound_usd_per_session_25": (
            cell.known_usd[sel] / sessions - cell.reserved_unknown / sessions
        ),
        "reserved_deployed_usd": cell.reserved_deployed,
        "reserved_unknown_usd": cell.reserved_unknown,
    }


def coverage_view(cov: Counter, cells: dict, sessions: int) -> dict:
    selectable = [c for c in cells.values() if c.selectable]
    return {
        "sessions": int(cov.get("sessions", 0)),
        "panel_rows": int(cov.get("panel_rows", 0)),
        "universe_rank_above_10": int(cov.get("universe_rank_above_10", 0)),
        "universe_spread_below_50bps": int(cov.get("universe_spread_below_50bps", 0)),
        "intent_not_firm_fresh": int(cov.get("intent_not_firm_fresh", 0)),
        "intent_no_prior_quote": int(cov.get("intent_no_prior_quote", 0)),
        "intent_invalid_or_crossed_quote": int(cov.get("intent_invalid_or_crossed_quote", 0)),
        "intent_nonregular_quote": int(cov.get("intent_nonregular_quote", 0)),
        "quote_not_acquired": int(cov.get("quote_not_acquired", 0)),
        "no_order_min_capital": int(cov.get("no_order_min_capital", 0)),
        "universe_candidate_states": int(cov.get("universe_candidates", 0)),
        "candidates_size_ge_q": int(cov.get("cand_size_ge_q", 0)),
        "candidates_size_ge_3q": int(cov.get("cand_size_ge_3q", 0)),
        "candidates_round_lot_epoch": int(cov.get("cand_round_lot_epoch", 0)),
        "candidates_single_share_epoch": int(cov.get("cand_single_share_epoch", 0)),
        "universe_names": int(cov.get("universe_names", 0)),
        "universe_quoted_names": int(cov.get("universe_quoted_names", 0)),
        "days_trades_file_present": int(cov.get("days_trades_file_present", 0)),
        "days_trades_file_missing": int(cov.get("days_trades_file_missing", 0)),
        "placements_over_36_selectable_cells": sum(c.placements for c in selectable),
        "known_fills_over_36_selectable_cells": sum(c.fills for c in selectable),
        "unknowns_over_36_selectable_cells": sum(c.unknowns for c in selectable),
        "reserved_deployed_usd_over_36_selectable_cells": sum(
            c.reserved_deployed for c in selectable
        ),
        "reserved_unknown_usd_over_36_selectable_cells": sum(
            c.reserved_unknown for c in selectable
        ),
    }


def run_smoke(days: list) -> None:
    sel = int(SELECT_RUNG)
    for day in days:
        cov = Counter()
        t0 = time.time()
        cands, session_end = build_candidates(day, cov)
        if cands:
            trades, trades_file = load_trades(day, {c["ticker"] for c in cands})
            resolve_fills(cands, trades, minute_us(day, session_end), trades_file)
            resolve_exits(cands)
        cells = {
            cell_key(fr, ttl, hold, mode): CellAcc(fr, ttl, hold, mode)
            for fr in FILL_RULES
            for ttl in TTLS_SEC
            for hold, _ in HOLDS
            for mode in EXIT_MODES
        }
        rows = replay_day_configs(cands, minute_us(day, session_end))
        for key, row in rows.items():
            cells[key].merge_day(day[:7], row)
        print("[smoke] %s elapsed=%.1fs" % (day, time.time() - t0))
        print(
            "[smoke] %s panel_rows=%d rank_above_10=%d spread_below_50=%d "
            "intent_not_fresh=%d no_quote=%d candidates=%d names=%d"
            % (
                day,
                cov["panel_rows"],
                cov["universe_rank_above_10"],
                cov["universe_spread_below_50bps"],
                cov["intent_not_firm_fresh"],
                cov["quote_not_acquired"],
                cov["universe_candidates"],
                cov["universe_names"],
            )
        )
        print(
            "%-34s%8s%7s%6s%8s%7s%10s%9s%7s%5s"
            % ("cell", "place", "fill", "unk", "nprint", "sizeg", "$/sess25", "medfill", "win", "mo+")
        )
        keys = sorted(cells, key=grid_sort_key)
        prefixes = (
            "base|ttl60s|hold60s|",
            "strict|ttl60s|hold60s|",
            "base|ttl15s|hold30s|",
            "strict|ttl15s|hold30s|",
        )
        for k in keys:
            if not any(k.startswith(p) for p in prefixes):
                continue
            v = cells[k].view(1)
            med = v["trade_stats"]["median_fill_usd"][sel]
            win = v["trade_stats"]["win_rate"][sel]
            sess = v["usd_per_panel_session"][sel]
            mo = v["months_positive"][sel]["months_positive"]
            print(
                "%-34s%8d%7d%6d%8d%7d%10.2f%9.1f%7.2f%5d"
                % (
                    k,
                    v["placements"],
                    v["fills"],
                    v["unknowns"],
                    v["unfilled_no_print"],
                    v["unfilled_size_gate"],
                    sess,
                    med if med is not None else 0.0,
                    win if win is not None else 0.0,
                    mo,
                )
            )
        if cands:
            sp = np.asarray([c["spread_bps"] for c in cands])
            qs = np.asarray([c["qty"] for c in cands], dtype=float)
            sz = np.asarray([c["bid_shares"] for c in cands], dtype=float)
            print(
                "[smoke] %s spread_bps med=%.0f p90=%.0f max=%.0f; qty med=%.0f; "
                "bid_shares med=%.0f; size>=q %.0f%%; size>=3q %.0f%%"
                % (
                    day,
                    np.median(sp),
                    np.percentile(sp, 90),
                    sp.max(),
                    np.median(qs),
                    np.median(sz),
                    100 * np.mean(sz >= qs),
                    100 * np.mean(sz >= 3 * qs),
                )
            )


def run_dev_val() -> None:
    t0 = time.time()
    days_dev = block_days("dev")
    days_val = block_days("validation")
    if not days_dev or not days_val:
        raise SystemExit(
            "[passive] empty block: dev=%d val=%d" % (len(days_dev), len(days_val))
        )
    sessions = {"dev": len(days_dev), "validation": len(days_val)}
    print(
        "[blocks] dev=%d days, validation=%d days (late deferred until after the freeze)"
        % (len(days_dev), len(days_val)),
        flush=True,
    )
    dev_cells, dev_cov = replay_block("dev", days_dev)
    val_cells, val_cov = replay_block("validation", days_val)
    chosen, ranking = choose_cell(val_cells, sessions["validation"])
    sel = int(SELECT_RUNG)
    print(
        "[freeze] chosen %s (%+.2f $/panel session @25bps, %d known fills of %d "
        "placements, %d UNKNOWN) AFTER dev+validation, BEFORE any late file is read"
        % (
            chosen.key,
            chosen.known_usd[sel] / sessions["validation"],
            chosen.fills,
            chosen.placements,
            chosen.unknowns,
        ),
        flush=True,
    )
    payload = {
        "artifact": "passive_spread_capture",
        "study": "alpha_passive_spread_capture",
        "producer": "factory/scripts/alpha_passive_spread_capture.py",
        "producer_sha256": producer_sha256(),
        "created_et": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "status": STATUS,
        "preregistration": {
            **PREREG_SPEC,
            "spec_sha256": PREREG_SHA,
            "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "frozen_before_late_file_access": True,
            "chosen_cell": {
                "cell_key": chosen.key,
                "fill_rule": chosen.fill_rule,
                "ttl_s": chosen.ttl,
                "hold": chosen.hold,
                "exit_mode": chosen.mode,
                "validation_usd_per_panel_session_at_25": (
                    chosen.known_usd[sel] / sessions["validation"]
                ),
            },
            "validation_ranking": ranking,
        },
        "selection": {
            "objective": (
                "2023 validation KNOWN-contribution dollars per panel session at the 25 bps "
                "residual rung, PARTIAL basis (unknowns excluded from the numerator, "
                "reported beside it)"
            ),
            "selection_rung_bps": sel,
            "tie_break": "(- objective, - known_fills, + placements, cell key)",
            "selectable": (
                "the 36 touch-exit cells; the mid exit is the labelled upper bound and is "
                "never selectable"
            ),
            "chosen_cell_key": chosen.key,
            "validation_ranking": ranking,
        },
        "sessions": sessions,
        "coverage": {
            "dev": coverage_view(dev_cov, dev_cells, sessions["dev"]),
            "validation": coverage_view(val_cov, val_cells, sessions["validation"]),
        },
        "grid": {
            "dev": grid_table(dev_cells, sessions["dev"]),
            "validation": grid_table(val_cells, sessions["validation"]),
            "late": None,
        },
        "chosen_cell_results": {
            "dev": chosen_cell_block(dev_cells[chosen.key], sessions["dev"]),
            "validation": chosen_cell_block(chosen, sessions["validation"]),
            "late": None,
        },
        "upper_bound_destruction": destruction_block(
            {"dev": dev_cells, "validation": val_cells}, chosen, sessions
        ),
        "upper_bound_destruction_by_bucket": {
            "dev": bucket_destruction(dev_cells, chosen.fill_rule, chosen.ttl, chosen.hold),
            "validation": bucket_destruction(
                val_cells, chosen.fill_rule, chosen.ttl, chosen.hold
            ),
            "late": None,
        },
        "strict_axis": strict_axis({"dev": dev_cells, "validation": val_cells}, sessions),
        "venue_reference": VENUE_UPPER_BOUND,
        "late_read_performed": False,
        "late_read": None,
        "verdict": (
            "PRE-REGISTERED AND FROZEN BEFORE ANY LATE FILE WAS READ: chosen %s by "
            "validation $/panel session at 25 bps (%+.2f over %d known fills of %d "
            "placements, %d UNKNOWN). The late block has not been run at freeze time; run "
            "'--stage late' once to append it." % (
                chosen.key,
                chosen.known_usd[sel] / sessions["validation"],
                chosen.fills,
                chosen.placements,
                chosen.unknowns,
            )
        ),
        "runtime_s": round(time.time() - t0, 1),
    }
    write_json_atomic(ARTIFACT, payload)
    print("[done] dev_val artifact -> %s (%.0fs)" % (ARTIFACT, payload["runtime_s"]), flush=True)


def run_late() -> None:
    t0 = time.time()
    if not ARTIFACT.exists():
        raise SystemExit(
            "[passive] %s missing: run --stage dev_val first; the late block may only be "
            "read after the pre-registration is on disk" % ARTIFACT
        )
    prior = json.loads(ARTIFACT.read_text())
    if prior.get("late_read_performed"):
        raise SystemExit(
            "[passive] the late block was already read once; the choice is immutable and "
            "the artifact is final"
        )
    if prior.get("preregistration", {}).get("spec_sha256") != PREREG_SHA:
        raise SystemExit(
            "[passive] pre-registration spec drift: the artifact was frozen by a different "
            "producer/spec; refusing to mix records"
        )
    days_late = block_days("late")
    if not days_late:
        raise SystemExit("[passive] empty late block")
    print(
        "[blocks] late=%d days (FIRST and ONLY access, after the freeze)" % len(days_late),
        flush=True,
    )
    late_cells, late_cov = replay_block("late", days_late)
    sel = int(SELECT_RUNG)
    chosen_key = prior["preregistration"]["chosen_cell"]["cell_key"]
    rule = prior["preregistration"]["chosen_cell"]["fill_rule"]
    ttl = int(prior["preregistration"]["chosen_cell"]["ttl_s"])
    hold = prior["preregistration"]["chosen_cell"]["hold"]
    sessions = dict(prior.get("sessions") or {})
    sessions["late"] = len(days_late)
    dev_grid = {c["cell_key"]: c for c in prior["grid"]["dev"]}
    val_grid = {c["cell_key"]: c for c in prior["grid"]["validation"]}

    # The late rows of the derived tables are recomputed from the late replay; the frozen
    # dev/validation rows are carried over verbatim (never recomputed, never re-selected).
    dest = dict(prior.get("upper_bound_destruction") or {})
    dest_blocks = dict(dest.get("blocks") or {})
    dest_blocks["late"] = _destruction_rows(
        late_cells, chosen_key, rule, ttl, hold, sessions["late"]
    )
    dest["blocks"] = dest_blocks
    dest["late_added_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    buckets = dict(prior.get("upper_bound_destruction_by_bucket") or {})
    buckets["late"] = bucket_destruction(late_cells, rule, ttl, hold)

    strict = dict(prior.get("strict_axis") or {})
    strict["late"] = strict_axis({"late": late_cells}, sessions)["late"]

    late_cell = late_cells[chosen_key]
    # The verdict must see the late destruction rows, so it reads the patched prior.
    patched = dict(prior)
    patched["upper_bound_destruction"] = dest
    verdict = _verdict_text(patched, late_cell, late_cells, sessions)
    payload = dict(prior)
    payload.update(
        {
            "late_read_performed": True,
            "late_read": {
                "performed": True,
                "read_started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "first_and_only_late_access": True,
                "late_days": len(days_late),
                "price_of_immutability": (
                    "the choice was frozen from validation only; every late number below is "
                    "confirmation of an already-frozen decision, not a re-selection"
                ),
            },
            "sessions": sessions,
            "coverage": {
                **prior.get("coverage", {}),
                "late": coverage_view(late_cov, late_cells, sessions["late"]),
            },
            "grid": {
                "dev": prior["grid"]["dev"],
                "validation": prior["grid"]["validation"],
                "late": grid_table(late_cells, sessions["late"]),
            },
            "chosen_cell_results": {
                "dev": dev_grid[chosen_key],
                "validation": val_grid[chosen_key],
                "late": chosen_cell_block(late_cell, sessions["late"]),
            },
            "upper_bound_destruction": dest,
            "upper_bound_destruction_by_bucket": buckets,
            "strict_axis": strict,
            "verdict": verdict,
            "runtime_s": round(time.time() - t0, 1),
        }
    )
    write_json_atomic(ARTIFACT, payload)
    print("[done] late appended -> %s (%.0fs)" % (ARTIFACT, time.time() - t0), flush=True)


def _destruction_rows(cells: dict, chosen_key: str, rule: str, ttl: int, hold: str, sessions: int) -> dict:
    """The destruction rows of one block (used by the late stage; the dev/validation rows
    come from the frozen artifact)."""
    sel = int(SELECT_RUNG)
    ub = cells[cell_key(rule, ttl, hold, "mid")]
    honest = cells[chosen_key]
    strict = cells[cell_key("strict", ttl, hold, "touch")]
    strict_ub = cells[cell_key("strict", ttl, hold, "mid")]
    ub_bps = cell_bps(ub, sel)
    honest_bps = cell_bps(honest, sel)
    strict_bps = cell_bps(strict, sel)
    row = {
        "upper_bound_mid_exit": {
            "cell_key": ub.key,
            "usd_per_panel_session": ub.known_usd[sel] / sessions,
            "known_fills": ub.fills,
            "placements": ub.placements,
            **ub_bps,
        },
        "honest_touch_exit": {
            "cell_key": honest.key,
            "usd_per_panel_session": honest.known_usd[sel] / sessions,
            "known_fills": honest.fills,
            "placements": honest.placements,
            "unknowns": honest.unknowns,
            **honest_bps,
        },
        "strict_touch_exit": {
            "cell_key": strict.key,
            "usd_per_panel_session": strict.known_usd[sel] / sessions,
            "known_fills": strict.fills,
            "placements": strict.placements,
            "unknowns": strict.unknowns,
            **strict_bps,
        },
        "strict_mid_exit": {
            "cell_key": strict_ub.key,
            "usd_per_panel_session": strict_ub.known_usd[sel] / sessions,
            "known_fills": strict_ub.fills,
            "placements": strict_ub.placements,
            **cell_bps(strict_ub, sel),
        },
    }
    if ub_bps["mean_fill_bps"]:
        for name, bps in (("honest", honest_bps), ("strict", strict_bps)):
            if bps["mean_fill_bps"] is not None:
                row[name + "_share_of_upper_bound_mean_fill_bps"] = (
                    bps["mean_fill_bps"] / ub_bps["mean_fill_bps"]
                )
        ub_sess = row["upper_bound_mid_exit"]["usd_per_panel_session"]
        for name in ("honest", "strict"):
            s = row[name + "_touch_exit"]["usd_per_panel_session"]
            if ub_sess:
                row[name + "_share_of_upper_bound_usd_per_session"] = s / ub_sess
    return row


def _verdict_text(prior: dict, late_cell: CellAcc, late_cells: dict, sessions: dict) -> str:
    sel = int(SELECT_RUNG)
    chosen_meta = prior["preregistration"]["chosen_cell"]
    key = chosen_meta["cell_key"]
    ttl = int(chosen_meta["ttl_s"])
    hold = chosen_meta["hold"]
    dev_grid = {c["cell_key"]: c for c in prior["grid"]["dev"]}
    val_grid = {c["cell_key"]: c for c in prior["grid"]["validation"]}
    val_v = val_grid[key]
    dev_strict_v = dev_grid[cell_key("strict", ttl, hold, "touch")]
    val_strict_v = val_grid[cell_key("strict", ttl, hold, "touch")]
    late_strict = late_cells[cell_key("strict", ttl, hold, "touch")]
    chosen_val = val_v["usd_per_panel_session"]["25"]
    chosen_late = late_cell.known_usd[sel] / sessions["late"]
    strict_val = val_strict_v["usd_per_panel_session"]["25"]
    strict_late = late_strict.known_usd[sel] / sessions["late"]
    val_ub = prior["upper_bound_destruction"]["blocks"]["validation"]
    late_ub = prior["upper_bound_destruction"]["blocks"]["late"]

    def pct(node: dict, name: str) -> float:
        v = node.get(name)
        return 100.0 * v if v is not None else float("nan")

    text = (
        "CHOSEN %s (frozen from validation before any late read): validation %+.2f $/panel "
        "session @25bps over %d known fills of %d placements (%d UNKNOWN); late %+.2f $/panel "
        "session @25bps over %d known fills of %d placements (%d UNKNOWN). "
        "STRICT-fill-rule cells on the chosen TTL/hold: validation %+.2f, late %+.2f, "
        "dev %+.2f $/panel session @25bps. The same cell with the MID exit (UPPER BOUND) "
        "measures %+.2f (validation) and %+.2f (late) $/panel session; the strict touch "
        "rule keeps %.1f%% (validation) / %.1f%% (late) of the upper-bound mean per-fill "
        "bps, the non-strict touch rule keeps %.1f%% / %.1f%%."
        % (
            key,
            chosen_val,
            val_v["fills"],
            val_v["placements"],
            val_v["unknowns"],
            chosen_late,
            late_cell.fills,
            late_cell.placements,
            late_cell.unknowns,
            strict_val,
            strict_late,
            dev_strict_v["usd_per_panel_session"]["25"],
            val_ub["upper_bound_mid_exit"]["usd_per_panel_session"],
            late_ub["upper_bound_mid_exit"]["usd_per_panel_session"],
            pct(val_ub, "strict_share_of_upper_bound_mean_fill_bps"),
            pct(late_ub, "strict_share_of_upper_bound_mean_fill_bps"),
            pct(val_ub, "honest_share_of_upper_bound_mean_fill_bps"),
            pct(late_ub, "honest_share_of_upper_bound_mean_fill_bps"),
        )
    )
    both_positive = chosen_val > 0 and chosen_late > 0
    strict_both_positive = strict_val > 0 and strict_late > 0
    text += (
        " VERDICT: chosen cell positive on BOTH validation and late = %s; the "
        "STRICT-fill-rule cell (same TTL/hold) positive on BOTH validation and late = %s "
        "(dev strict %+.2f $/panel session @25bps)."
        % (
            both_positive,
            strict_both_positive,
            dev_strict_v["usd_per_panel_session"]["25"],
        )
    )
    return text


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--stage", choices=["dev_val", "late"], default="dev_val")
    p.add_argument("--days", nargs="+", default=None, help="smoke subset (debug only)")
    p.add_argument("--smoke", action="store_true", help="replay the given days only, no artifact")
    args = p.parse_args()

    if args.smoke:
        if not args.days:
            raise SystemExit("--smoke requires --days")
        run_smoke(args.days)
        return
    if args.days:
        raise SystemExit("--days is only valid together with --smoke")

    if args.stage == "dev_val":
        run_dev_val()
    else:
        run_late()


if __name__ == "__main__":
    main()
