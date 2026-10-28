#!/usr/bin/env python3
"""LIFECYCLE-01 v2 — causal tape enrichment for the fixed v2 roster (measured, never modeled).

Population: **only** `<data>/harvest01/lifecycle/v2/roster/<day>.parquet` (the corrected v2
roster). The old `sip/candidates` snapshots and the v1 roster are never read. The roster is
clock-agnostic: every `(clock, rank, ticker)` row is consumed as given, so the tape does not
hard-code decision clocks.

Sources (read exactly ONCE per day each, then sorted by `(symbol, ts_utc)`):
  * trades: `<data>/sip/net/trades/<day>.parquet`   (fallback `<data>/sip/trades/...`)
  * quotes: `<data>/sip/net/quotes/<day>.parquet`   (fallback `<data>/sip/quotes/...`)
  * per-symbol quality classes: `<data>/sip/net/coverage/<day>.json`
The local SIP net archive is the 09:25–16:04 ET window: minutes `>= 565`. Everything earlier
(including 09:00/09:20 premarket clocks) has **no tape at all** — those minute rows are emitted
with `tape_covered == false`, and no 5/10-second "premarket path" is fabricated.

Exchange-condition semantics — REUSED, not invented
--------------------------------------------------
The condition truth is `sip_bars.combine / RULES_M / AUCTION_CODES` (the frozen bar-rule table
already consumed by `basket_t5_rawpaths.price_updating` and
`basket_tape_atlas_observation.condition_axes`). Per print `(oc, hl, v)` with
`2 = updates, 1 = first-trade-only, 0 = no update`:
  * `hl == 2` -> print can update high/low -> price path (`px_upd`);
  * `v  == 2` -> print counts toward reported volume (`vol_ok`). `v == 0` prints are the
    non-volume-changing ones (`M`, `Q`, `9`, and `B` average-price on tapes A/B): counted in
    `tr_nonvol_shares` and excluded from `tr_shares` / `tr_dvol`.
  * ATLAS-Observation's "bar-eligible print" test `(oc >= 1) | (hl == 2)` is *not* used; the tape
    stores the finer axes so consumers can reconstruct it.
Flagged print classes (kept in the `*_all` counters):
  `I` odd lot · `F` intermarket sweep · {`Q`,`M`,`O`,`5`,`6`} auction/official · {`L`,`Z`,`U`}
  late/out-of-sequence · `9` corrected/error · `P` prior reference price · `4` derivatively priced
  · {`7`,`V`,`W`} contingent · {`B`,`G`} bunched · `T` extended hours. Odd lots count volume
  under the frozen table and are *not* round-lot eligible for capacity.

Quote size units — investigated, not assumed
--------------------------------------------
`bid_size`/`ask_size` are **round lots of 100** through 2025-10-31 and **shares** from 2025-11-03
(measured per day over the archive: median 2–17 with `%100 == 0` share ~0.001 before, median
200–2300 with `%100 == 0` share ~1.0 after). Any capacity figure built from the pre-cutoff field
without `x100` is understated by 100x. The script uses the documented cutoff as the primary unit,
re-derives the unit from the day's own quotes, and records both plus `size_unit_conflict` in the
manifest and the coverage table. Every quote keeps the raw field and the share-converted value.

Causality
---------
  * minute rows: one row per `(day, ticker, t)` where `t` is the ET minute at which the row is
    usable. Features aggregate the single **completed** minute `[t-1, t)`; `*_5m` / `*_15m`
    windows aggregate completed minutes `[t-k, t)`.
  * subminute rows: bucket `b` (ET seconds-of-day, start) covers `[b, b+div)`, first usable at
    `avail_et_s = b + div`; `tape5` uses `div=5`, `tape10` `div=10`.
  * `entry` rows: the NBBO reference is the last **strictly earlier** valid quote before the
    execution instant; when the execution falls before the tape window opens the print/quote
    fields stay null and `exec_ts_basis == "no_tape_window"` (a 09:25 print is never substituted
    for a premarket fill). A print is never called a proven fill
    (`fillability_basis = "quote_proxy"`).

Local coverage limitation (measured, not assumed)
-------------------------------------------------
`sip/net/{trades,quotes}` are per-day *symbol subsets* fetched by the earlier snapshot logic
(~50-100 symbols in 2021, ~7-21 later), not the whole market. A v2 roster ticker that is not in
that day's subset has **no tape at all**; the tape still emits its minute rows (all-zero trade
features, `tape_covered == false`) and the coverage layer records `n_prints == 0`,
`quality == "unknown"`. Over the 22 real v2 roster days available on 2026-10-02: 89.0% of roster
ticker-days appear in the trade lane, 55.2% in the quote lane, and 2025-02-03 has zero trade
coverage for the whole roster. Quote-based costs/capacity are therefore *missing* for about half
of the members — never interpolated.

Outputs (atomic, per-day resumable, guarded by a manifest)
----------------------------------------------------------
  <data>/harvest01/lifecycle/v2/tape/<day>.parquet      minute, lagged one completed minute
  <data>/harvest01/lifecycle/v2/tape5/<day>.parquet     5-second buckets
  <data>/harvest01/lifecycle/v2/tape10/<day>.parquet    10-second buckets
  <data>/harvest01/lifecycle/v2/entry/<day>.parquet     per roster member: quote/exec/capacity
  <data>/harvest01/lifecycle/v2/coverage/<day>.parquet  per ticker coverage + quality
  <data>/harvest01/lifecycle/v2/manifest/<day>.json     source sha/size/mtime + schema (reuse key)

Usage:
  .venv/bin/python factory/scripts/lifecycle_tape.py --days 2021-02-01
  .venv/bin/python factory/scripts/lifecycle_tape.py --dev-days --workers 6
  .venv/bin/python factory/scripts/lifecycle_tape.py --split validation --days 2023-03-15 --force

API for parent consumers:
  load_manifest(day) · load_tape(day, layer=...) · load_tape_days(days, layer=...) · read_roster(day)
  asof_quote(day, ticker, ts_us) -> dict   # exact last NBBO strictly before any timestamp
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import sys
import time
from pathlib import Path

# --------------------------------------------------------------- thread bounding
# POLARS_MAX_THREADS is read when polars initialises its pool, so set it before the import.
# Peek at --workers / --threads in sys.argv (spawn children inherit sys.argv and os.environ), and
# setdefault so an explicit environment override always wins.
def _argv_int(flag: str) -> int | None:
    for i, a in enumerate(sys.argv):
        if a == flag and i + 1 < len(sys.argv):
            try:
                return int(sys.argv[i + 1])
            except ValueError:
                return None
        if a.startswith(flag + "="):
            try:
                return int(a.split("=", 1)[1])
            except ValueError:
                return None
    return None


_CPUS = os.cpu_count() or 4
_W_PEEK = max(1, _argv_int("--workers") or 1)
_T_PEEK = _argv_int("--threads")
# Default bound: total native threads per worker stay in [1, 3] (the full run shares the box
# with sibling jobs), so the whole job cannot oversubscribe the machine.
os.environ.setdefault("POLARS_MAX_THREADS",
                      str(min(3, max(1, _T_PEEK if _T_PEEK else _CPUS // _W_PEEK))))

import polars as pl  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import basket_pm_snapshots as bps  # noqa: E402
import sip_bars as sbar  # noqa: E402

PRODUCER = "lifecycle_tape"
PRODUCER_VERSION = "2.0.1"
SCHEMA_VERSION = "v2"
LAYERS = ("tape", "tape5", "tape10", "entry", "coverage")

TRADE_COLS = ("symbol", "ts_utc", "price", "size", "conditions", "tape")
QUOTE_COLS = ("symbol", "ts_utc", "bid_price", "bid_size", "ask_price", "ask_size")
ROSTER_KEYS = ("clock", "rank", "ticker")
ROSTER_OPT = ("day", "decision_et", "decision_px", "fill_et", "fill_px", "prev_close_adj",
              "gain_adj", "status", "session_end", "source", "quality")
ROSTER_ALIASES = {"symbol": "ticker"}

G = sbar.G  # 2 = updates; Y=1 first-trade-only, R=0 no-update
Y = sbar.Y
AUCTION_CODES = frozenset(sbar.AUCTION_CODES)
LATE_CODES = frozenset({"L", "Z", "U"})
CONTINGENT_CODES = frozenset({"7", "V", "W"})
BUNCHED_CODES = frozenset({"B", "G"})

TAPE_LO_ET_MIN = 565             # local SIP window opens 09:25 ET; earlier minutes have no tape
RTH_OPEN_ET_MIN = 570
Q_MAX_AGE_S = 1.0                # repo ruler (PLAN-ATLAS-E3-MICROSCOPE §anti-drift)
BURST_PRINTS_PER_S = 2.0         # ruler, not fitted
SIZE_UNIT_CUTOFF = "2025-11-03"  # quote sizes: round lots before, shares from here on
MIN_SIZE_UNIT_N = 200            # rows needed before data-driven unit detection is trusted


# ------------------------------------------------------------------------ plumbing
def v2_dir(data_root: Path) -> Path:
    return data_root / "harvest01" / "lifecycle" / "v2"


def layer_dir(data_root: Path, layer: str) -> Path:
    return v2_dir(data_root) / layer


def roster_path(day: str, data_root: Path) -> Path:
    return layer_dir(data_root, "roster") / f"{day}.parquet"


def manifest_path(day: str, data_root: Path) -> Path:
    return layer_dir(data_root, "manifest") / f"{day}.json"


def _sha256_file(p: Path, block: int = 1 << 20) -> str | None:
    if p is None or not p.exists():
        return None
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(block), b""):
            h.update(chunk)
    return h.hexdigest()


def _src_stamp(p: Path | None) -> dict:
    if p is None or not p.exists():
        return {"path": None if p is None else str(p), "exists": False}
    st = p.stat()
    return {"path": str(p), "exists": True, "size": st.st_size, "mtime_ns": st.st_mtime_ns,
            "sha256": _sha256_file(p)}


def _atomic_parquet(df: pl.DataFrame, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    df.write_parquet(tmp, compression="zstd")
    os.replace(tmp, path)
    return path.stat().st_size


def _atomic_json(obj: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1))
    os.replace(tmp, path)


def _tape_source(data_root: Path, kind: str, day: str) -> Path | None:
    for base in (data_root / "sip" / "net" / kind, data_root / "sip" / kind):
        p = base / f"{day}.parquet"
        if p.exists():
            return p
    return None


# ------------------------------------------------------------------- split/roster
def load_split(data_root: Path) -> dict:
    """Immutable 533/533 split; refuses to run if the calendar hash no longer matches."""
    p = v2_dir(data_root) / "split.json"
    if not p.exists():
        return {}
    j = json.loads(p.read_text())
    got = _sha256_file(bps.CALENDAR)
    if j.get("calendar_sha256") and got and j["calendar_sha256"] != got:
        raise SystemExit(f"split.json calendar hash {j['calendar_sha256'][:12]} != calendar "
                         f"{got[:12]}; refusing to run against a moved calendar")
    return j


def split_member(day: str, split: dict) -> str:
    if day in set(split.get("discovery_days") or ()):
        return "discovery"
    if day in set(split.get("validation_days") or ()):
        return "validation"
    return "other"


def read_roster(day: str, data_root: Path) -> tuple[pl.DataFrame, str | None, list[str]]:
    """v2 roster for `day` normalised to the contract names. Empty frame when absent."""
    p = roster_path(day, data_root)
    if not p.exists():
        return pl.DataFrame(), None, []
    df = pl.read_parquet(p)
    renamed = {c: ROSTER_ALIASES[c] for c in df.columns if c in ROSTER_ALIASES}
    if renamed:
        df = df.rename(renamed)
    present = list(df.columns)
    missing = [c for c in ROSTER_KEYS if c not in df.columns]
    if missing:
        raise SystemExit(f"{p}: roster missing required column(s) {missing}; has {present}")
    for c in ROSTER_OPT:
        if c not in df.columns:
            df = df.with_columns(pl.lit(None).alias(c))
    df = df.with_columns([
        pl.col("clock").cast(pl.Int32),
        pl.col("rank").cast(pl.Int32),
        pl.col("ticker").cast(pl.Utf8),
        *[pl.col(c).cast(pl.Int32) for c in ("decision_et", "fill_et", "session_end")],
        *[pl.col(c).cast(pl.Float64) for c in ("decision_px", "fill_px", "prev_close_adj",
                                               "gain_adj")],
        pl.col("status").cast(pl.Utf8),
    ]).sort(["clock", "rank"])
    return df, _sha256_file(p), present


# ------------------------------------------------------------- source decoration
def _clock_exprs() -> list[pl.Expr]:
    t = pl.col("ts_utc").dt.convert_time_zone("America/New_York")
    return [
        (t.dt.hour().cast(pl.Int32) * 60 + t.dt.minute().cast(pl.Int32)).alias("et_min"),
        (t.dt.hour().cast(pl.Int32) * 3600 + t.dt.minute().cast(pl.Int32) * 60
         + t.dt.second().cast(pl.Int32)
         + t.dt.microsecond().cast(pl.Float64) / 1e6).alias("et_s"),
    ]


def _axes(ck: str, tape: str) -> dict:
    """Frozen `sip_bars.combine` axes + documented print-class flags."""
    conds = ck.split("|") if ck else []
    oc, hl, v, unknown = sbar.combine(conds, tape)
    cs = set(conds)
    return {
        "_ck": ck, "tape": tape, "ax_oc": oc, "ax_hl": hl, "ax_v": v,
        "ax_unknown": "|".join(sorted(unknown)),
        "px_upd": hl == G,
        # ATLAS-Observation's frozen bar-eligible print test: the print can move the bar's
        # open/close or its high/low. Used ONLY for execution matching (an odd lot cannot set
        # the bar open, so it is not exec-eligible).
        "px_elig": (oc >= Y) or (hl == G),
        "vol_ok": v == G,
        "is_odd": "I" in cs,
        "is_sweep": "F" in cs,
        "is_auction": bool(cs & AUCTION_CODES),
        "is_late": bool(cs & LATE_CODES),
        "is_corrected": "9" in cs,
        "is_prior_ref": "P" in cs,
        "is_deriv": "4" in cs,
        "is_contingent": bool(cs & CONTINGENT_CODES),
        "is_bunched": bool(cs & BUNCHED_CODES),
        "is_extended": "T" in cs,
    }


def _decorate_trades(tr: pl.DataFrame) -> pl.DataFrame:
    if tr.height == 0:
        return tr
    tr = tr.with_columns(pl.col("conditions").list.join("|").fill_null("").alias("_ck"))
    key = tr.select(["_ck", "tape"]).unique()
    axes = pl.DataFrame([_axes(ck, tp) for ck, tp in key.iter_rows()],
                        infer_schema_length=None)
    return tr.join(axes, on=["_ck", "tape"], how="left").drop("_ck")


def detect_size_unit(q: pl.DataFrame | None, day: str) -> dict:
    """Round lots (x100) vs shares: day's own quote sizes + the documented cutoff."""
    by_cutoff = 100 if day < SIZE_UNIT_CUTOFF else 1
    frac = med = None
    if q is not None and q.height:
        s = pl.concat([q["bid_size"], q["ask_size"]]).drop_nulls()
        s = s.filter(s > 0)
        if s.len() >= MIN_SIZE_UNIT_N:
            frac = float((s % 100 == 0).mean())
            med = float(s.median())
    detected = None
    if frac is not None and med is not None:
        detected = 1 if (frac >= 0.5 or med >= 1000) else 100
    return {"unit": by_cutoff, "basis": "cutoff",
            "cutoff": SIZE_UNIT_CUTOFF,
            "detected_unit": detected, "frac_mult_100": frac, "median_raw": med,
            "conflict": bool(detected is not None and detected != by_cutoff)}


# ------------------------------------------------------------------ aggregations
TR_MIN_COUNT = ["tr_n_all", "tr_shares_all", "tr_dvol_all", "tr_n", "tr_shares", "tr_dvol",
                "tr_odd_shares", "tr_sweep_shares", "tr_auction_shares", "tr_late_shares",
                "tr_corrected_shares", "tr_deriv_shares", "tr_ext_shares", "tr_nonvol_shares",
                "tr_n_px", "q_n"]
SUB_COUNT = TR_MIN_COUNT


def _minute_agg(tr: pl.DataFrame | None, q: pl.DataFrame | None, unit: int
                ) -> tuple[pl.DataFrame | None, pl.DataFrame | None]:
    ta = qa = None
    if tr is not None and tr.height:
        ta = tr.group_by(["symbol", "et_min"], maintain_order=True).agg([
            pl.len().alias("tr_n_all"),
            pl.col("size").sum().alias("tr_shares_all"),
            (pl.col("size") * pl.col("price")).sum().alias("tr_dvol_all"),
            pl.col("vol_ok").sum().alias("tr_n"),
            pl.col("size").filter(pl.col("vol_ok")).sum().alias("tr_shares"),
            (pl.col("size") * pl.col("price")).filter(pl.col("vol_ok")).sum().alias("tr_dvol"),
            pl.col("size").filter(pl.col("px_upd")).median().alias("tr_med_sz_px"),
            pl.col("size").filter(pl.col("is_odd")).sum().alias("tr_odd_shares"),
            pl.col("size").filter(pl.col("is_sweep")).sum().alias("tr_sweep_shares"),
            pl.col("size").filter(pl.col("is_auction")).sum().alias("tr_auction_shares"),
            pl.col("size").filter(pl.col("is_late")).sum().alias("tr_late_shares"),
            pl.col("size").filter(pl.col("is_corrected")).sum().alias("tr_corrected_shares"),
            pl.col("size").filter(pl.col("is_deriv")).sum().alias("tr_deriv_shares"),
            pl.col("size").filter(pl.col("is_extended")).sum().alias("tr_ext_shares"),
            pl.col("size").filter(~pl.col("vol_ok")).sum().alias("tr_nonvol_shares"),
            pl.col("px_upd").sum().alias("tr_n_px"),
            pl.col("price").filter(pl.col("px_upd")).first().alias("tr_first_px"),
            pl.col("price").filter(pl.col("px_upd")).last().alias("tr_last_px"),
            pl.col("price").filter(pl.col("px_upd")).max().alias("tr_max_px"),
            pl.col("price").filter(pl.col("px_upd")).min().alias("tr_min_px"),
            pl.col("et_s").first().alias("tr_first_et_s"),
            pl.col("et_s").last().alias("tr_last_et_s"),
            pl.col("dt_s").max().alias("tr_max_interarrival_s"),
            pl.col("dt_s").mean().alias("tr_mean_interarrival_s"),
        ])
    if q is not None and q.height:
        valid = q.filter((pl.col("bid_price") > 0) & (pl.col("ask_price") > 0))
        if valid.height:
            qa = valid.group_by(["symbol", "et_min"], maintain_order=True).agg([
                pl.len().alias("q_n"),
                pl.col("spread_bps").median().alias("q_spread_bps_med"),
                pl.col("spread_bps").last().alias("q_spread_bps_last"),
                pl.col("spread_bps").min().alias("q_spread_bps_min"),
                pl.col("spread_bps").max().alias("q_spread_bps_max"),
                (pl.col("bid_size") * unit).median().alias("q_bid_shares_med"),
                (pl.col("ask_size") * unit).median().alias("q_ask_shares_med"),
                (pl.col("bid_size") * unit).last().alias("q_bid_shares_last"),
                (pl.col("ask_size") * unit).last().alias("q_ask_shares_last"),
                pl.col("imb").median().alias("q_imb_med"),
                pl.col("imb").last().alias("q_imb_last"),
                pl.col("mid").last().alias("q_mid_last"),
                pl.col("locked").mean().alias("q_locked_frac"),
                pl.col("crossed").mean().alias("q_crossed_frac"),
                pl.col("dt_s").max().alias("q_max_gap_s"),
                pl.col("et_s").last().alias("q_last_et_s"),
            ])
    return ta, qa


def _bucket_agg(tr: pl.DataFrame | None, q: pl.DataFrame | None, unit: int, div: int,
                lo_b: int, hi_b: int) -> tuple[pl.DataFrame | None, pl.DataFrame | None]:
    ta = qa = None
    if tr is not None and tr.height:
        t = tr.filter((pl.col("b") >= lo_b) & (pl.col("b") < hi_b))
        if t.height:
            ta = t.group_by(["symbol", "b"], maintain_order=True).agg([
                pl.len().alias("tr_n_all"),
                pl.col("size").sum().alias("tr_shares_all"),
                (pl.col("size") * pl.col("price")).sum().alias("tr_dvol_all"),
                pl.col("vol_ok").sum().alias("tr_n"),
                pl.col("size").filter(pl.col("vol_ok")).sum().alias("tr_shares"),
                (pl.col("size") * pl.col("price")).filter(pl.col("vol_ok")).sum().alias("tr_dvol"),
                pl.col("size").filter(pl.col("is_odd")).sum().alias("tr_odd_shares"),
                pl.col("size").filter(pl.col("is_sweep")).sum().alias("tr_sweep_shares"),
                pl.col("size").filter(pl.col("is_auction")).sum().alias("tr_auction_shares"),
                pl.col("size").filter(pl.col("is_late")).sum().alias("tr_late_shares"),
                pl.col("size").filter(pl.col("is_corrected")).sum().alias("tr_corrected_shares"),
                pl.col("size").filter(pl.col("is_deriv")).sum().alias("tr_deriv_shares"),
                pl.col("size").filter(pl.col("is_extended")).sum().alias("tr_ext_shares"),
                pl.col("size").filter(~pl.col("vol_ok")).sum().alias("tr_nonvol_shares"),
                pl.col("size").filter(pl.col("px_upd")).median().alias("tr_med_sz_px"),
                pl.col("px_upd").sum().alias("tr_n_px"),
                pl.col("price").filter(pl.col("px_upd")).first().alias("tr_first_px"),
                pl.col("price").filter(pl.col("px_upd")).last().alias("tr_last_px"),
                pl.col("price").filter(pl.col("px_upd")).max().alias("tr_max_px"),
                pl.col("price").filter(pl.col("px_upd")).min().alias("tr_min_px"),
                pl.col("et_s").first().alias("tr_first_et_s"),
                pl.col("et_s").last().alias("tr_last_et_s"),
                pl.col("dt_s").first().alias("tr_gap_prev_s"),
                pl.col("dt_s").max().alias("tr_max_interarrival_s"),
                pl.col("dt_s").mean().alias("tr_mean_interarrival_s"),
            ])
    if q is not None and q.height:
        qq = q.filter((pl.col("b") >= lo_b) & (pl.col("b") < hi_b)
                      & (pl.col("bid_price") > 0) & (pl.col("ask_price") > 0))
        if qq.height:
            qa = qq.group_by(["symbol", "b"], maintain_order=True).agg([
                pl.len().alias("q_n"),
                pl.col("spread_bps").last().alias("q_spread_bps_last"),
                pl.col("spread_bps").min().alias("q_spread_bps_min"),
                pl.col("spread_bps").max().alias("q_spread_bps_max"),
                pl.col("spread_bps").median().alias("q_spread_bps_med"),
                (pl.col("bid_size") * unit).last().alias("q_bid_shares_last"),
                (pl.col("ask_size") * unit).last().alias("q_ask_shares_last"),
                (pl.col("bid_size") * unit).median().alias("q_bid_shares_med"),
                (pl.col("ask_size") * unit).median().alias("q_ask_shares_med"),
                pl.col("imb").last().alias("q_imb_last"),
                pl.col("imb").median().alias("q_imb_med"),
                pl.col("mid").last().alias("q_mid_last"),
                pl.col("locked").last().alias("q_locked"),
                pl.col("crossed").last().alias("q_crossed"),
                pl.col("locked").mean().alias("q_locked_frac"),
                pl.col("crossed").mean().alias("q_crossed_frac"),
                pl.col("dt_s").first().alias("q_gap_prev_s"),
                pl.col("dt_s").max().alias("q_max_gap_s"),
                pl.col("et_s").last().alias("q_last_et_s"),
            ])
    return ta, qa


def _grid(tickers: list[str], lo: int, hi_excl: int) -> pl.DataFrame:
    ks = list(range(lo, hi_excl))
    if not tickers or not ks:
        return pl.DataFrame(schema={"ticker": pl.Utf8, "k": pl.Int64})
    return pl.DataFrame({"ticker": [s for s in tickers for _ in ks], "k": ks * len(tickers)})


def _fill0(df: pl.DataFrame, cols: list[str]) -> pl.DataFrame:
    return df.with_columns([pl.col(c).fill_null(0) for c in cols if c in df.columns])


# Default value per feature when the day has no trades/quotes at all. Keeping every layer's
# schema identical across days is what lets the panel owner read the corpus with one reader.
MIN_DEFAULTS: dict[str, object] = {
    "tr_n_all": 0, "tr_shares_all": 0.0, "tr_dvol_all": 0.0, "tr_n": 0, "tr_shares": 0.0,
    "tr_dvol": 0.0, "tr_med_sz_px": None, "tr_odd_shares": 0.0, "tr_sweep_shares": 0.0,
    "tr_auction_shares": 0.0, "tr_late_shares": 0.0, "tr_corrected_shares": 0.0,
    "tr_deriv_shares": 0.0, "tr_ext_shares": 0.0, "tr_nonvol_shares": 0.0, "tr_n_px": 0,
    "tr_first_px": None, "tr_last_px": None, "tr_max_px": None, "tr_min_px": None,
    "tr_first_et_s": None, "tr_last_et_s": None, "tr_max_interarrival_s": None,
    "tr_mean_interarrival_s": None,
    "q_n": 0, "q_spread_bps_med": None, "q_spread_bps_last": None, "q_spread_bps_min": None,
    "q_spread_bps_max": None, "q_bid_shares_med": None, "q_ask_shares_med": None,
    "q_bid_shares_last": None, "q_ask_shares_last": None, "q_imb_med": None, "q_imb_last": None,
    "q_mid_last": None, "q_locked_frac": None, "q_crossed_frac": None, "q_max_gap_s": None,
    "q_last_et_s": None,
}
SUB_EXTRA_DEFAULTS: dict[str, object] = {
    "tr_gap_prev_s": None, "q_locked": None, "q_crossed": None, "q_gap_prev_s": None,
}


def _cast(df: pl.DataFrame, dtypes: dict) -> pl.DataFrame:
    """Pin the layer schema so every day's parquet carries identical dtypes."""
    return df.with_columns([pl.col(c).cast(t) for c, t in dtypes.items() if c in df.columns])


_Q_FIELDS = {"et_us": pl.Int64, "age_s": pl.Float64, "bid": pl.Float64, "ask": pl.Float64,
             "bid_lots_raw": pl.Float64, "ask_lots_raw": pl.Float64, "bid_shares": pl.Float64,
             "ask_shares": pl.Float64, "spread_bps": pl.Float64, "mid": pl.Float64,
             "imb": pl.Float64, "locked": pl.Boolean, "crossed": pl.Boolean,
             "age_ok": pl.Boolean}
ENTRY_DTYPES: dict[str, pl.DataType] = {
    "day": pl.Utf8, "clock": pl.Int64, "rank": pl.Int64, "ticker": pl.Utf8, "status": pl.Utf8,
    "decision_et": pl.Int64, "decision_px": pl.Float64, "fill_et": pl.Int64,
    "fill_px": pl.Float64, "prev_close_adj": pl.Float64, "gain_adj": pl.Float64,
    "session_end": pl.Int64, "trades_present": pl.Boolean, "quotes_present": pl.Boolean,
    "fillability_basis": pl.Utf8, "roster_missing_cols": pl.Utf8, "size_unit": pl.Int64,
    "exec_ts_us": pl.Int64, "exec_ts_basis": pl.Utf8, "print_ts_us": pl.Int64,
    "print_px": pl.Float64, "print_size": pl.Float64, "print_is_odd": pl.Boolean,
    "print_age_s": pl.Float64, "cost_buy_at_ask_bps": pl.Float64,
    "cost_exit_at_bid_bps": pl.Float64, "cost_roundtrip_bps": pl.Float64,
    "fill_vs_mid_bps": pl.Float64, "pos_in_quote": pl.Float64,
    "cap_ask_usd_displayed": pl.Float64, "cap_bid_usd_displayed": pl.Float64,
    "tape_covers_ref": pl.Boolean, "vol_ref_min_shares": pl.Float64,
    "vol_ref_min_usd": pl.Float64, "vol_ref_min_n": pl.Int64,
    "ref_et_min": pl.Int64, "print_delay_min": pl.Int64, "exec_min_n": pl.Int64,
    "print_px_vs_fill_bps": pl.Float64,
    **{f"q_{tag}_{f}": t for tag in ("exec", "dec") for f, t in
       {**_Q_FIELDS, "ref_basis": pl.Utf8}.items()},
}
COVERAGE_DTYPES: dict[str, pl.DataType] = {
    "day": pl.Utf8, "ticker": pl.Utf8, "n_prints": pl.Int64, "n_prints_vol": pl.Int64,
    "n_quotes_valid": pl.Int64, "first_trade_et_s": pl.Float64, "last_trade_et_s": pl.Float64,
    "first_trade_et_min": pl.Int64, "last_trade_et_min": pl.Int64,
    "first_quote_et_min": pl.Int64, "last_quote_et_min": pl.Int64, "pm_present": pl.Boolean,
    "rth_covered_from_open": pl.Boolean, "max_print_gap_s": pl.Float64,
    "print_minutes": pl.Int64, "size_unit": pl.Int64, "size_unit_basis": pl.Utf8,
    "size_unit_detected": pl.Int64, "size_unit_conflict": pl.Boolean,
    "frac_mult_100": pl.Float64, "median_quote_size_raw": pl.Float64, "quality": pl.Utf8,
    "tape_lo_et_min": pl.Int64, "cov_lo_et_min": pl.Int64, "sub_lo_s": pl.Int64,
    "sub_hi_s": pl.Int64, "session_end": pl.Int64,
}


def _ensure(df: pl.DataFrame, defaults: dict) -> pl.DataFrame:
    add = []
    for c, v in defaults.items():
        if c in df.columns:
            continue
        if v is None:
            add.append(pl.lit(None, dtype=pl.Float64).alias(c))
        elif isinstance(v, float):
            add.append(pl.lit(v, dtype=pl.Float64).alias(c))
        else:
            add.append(pl.lit(v, dtype=pl.UInt32).alias(c))
    return df.with_columns(add) if add else df


def _windows(df: pl.DataFrame, key: str, k: int,
             specs: dict[str, tuple[str, str]]) -> pl.DataFrame:
    """Rolling windows on the dense ordered series, never crossing tickers.

    specs: new_name -> (mode, source); mode 'sum' treats null as 0, 'mean' skips nulls.
    """
    if not specs:
        return df
    parts = []
    for part in df.sort(["ticker", key]).partition_by("ticker", maintain_order=True):
        exprs = []
        for new, (mode, src) in specs.items():
            if src not in part.columns:
                continue
            s = part[src].cast(pl.Float64)
            if mode == "sum":
                exprs.append(s.fill_null(0).rolling_sum(k, min_samples=k).alias(new))
            else:
                num = s.fill_null(0).rolling_sum(k, min_samples=k)
                den = s.is_not_null().cast(pl.Float64).rolling_sum(k, min_samples=k)
                exprs.append(pl.when(den == k).then(num / den).otherwise(None).alias(new))
        parts.append(part.with_columns(exprs))
    return pl.concat(parts) if parts else df


def build_minute(day: str, tickers: list[str], t0: dict[str, int], cov_lo_min: int, se: int,
                 ta: pl.DataFrame | None, qa: pl.DataFrame | None, quality: dict[str, str]
                 ) -> pl.DataFrame:
    """Rows t in [t0_ticker, se+1]; every feature is the completed minute [t-1, t)."""
    out = []
    for tk in tickers:
        lo_m = min(t0.get(tk, RTH_OPEN_ET_MIN), cov_lo_min) - 1
        g = _grid([tk], lo_m, se)
        if g.height == 0:
            continue
        m = g.rename({"k": "m"})
        if ta is not None:
            m = m.join(ta.rename({"symbol": "ticker", "et_min": "m"}), on=["ticker", "m"],
                       how="left")
        if qa is not None:
            m = m.join(qa.rename({"symbol": "ticker", "et_min": "m"}), on=["ticker", "m"],
                       how="left")
        m = _ensure(m, MIN_DEFAULTS)
        m = _fill0(m, TR_MIN_COUNT)
        m = m.with_columns([
            pl.when(pl.col("tr_shares") > 0)
            .then(pl.col("tr_dvol") / pl.col("tr_shares")).otherwise(None).alias("tr_vwap"),
            pl.when(pl.col("tr_n") > 0)
            .then(pl.col("tr_shares") / pl.col("tr_n")).otherwise(None).alias("tr_avg_sz"),
            pl.when(pl.col("tr_shares_all") > 0)
            .then(pl.col("tr_odd_shares") / pl.col("tr_shares_all")).otherwise(None)
            .alias("tr_odd_share"),
            pl.when(pl.col("tr_shares_all") > 0)
            .then(pl.col("tr_sweep_shares") / pl.col("tr_shares_all")).otherwise(None)
            .alias("tr_sweep_share"),
            pl.when(pl.col("tr_shares_all") > 0)
            .then(pl.col("tr_auction_shares") / pl.col("tr_shares_all")).otherwise(None)
            .alias("tr_auction_share"),
            pl.when(pl.col("tr_first_px") > 0)
            .then((pl.col("tr_last_px") / pl.col("tr_first_px") - 1) * 1e4).otherwise(None)
            .alias("tr_px_chg_bps"),
        ])
        m = _windows(m, "m", 5, {
            "tr_dvol_5m": ("sum", "tr_dvol"), "tr_n_5m": ("sum", "tr_n"),
            "tr_shares_5m": ("sum", "tr_shares"),
            "q_spread_bps_mean_minmed_5m": ("mean", "q_spread_bps_med"),
            "q_imb_mean_minmed_5m": ("mean", "q_imb_med"),
        })
        m = _windows(m, "m", 15, {
            "tr_dvol_15m": ("sum", "tr_dvol"), "tr_n_15m": ("sum", "tr_n"),
            "tr_shares_15m": ("sum", "tr_shares"),
            "q_spread_bps_mean_minmed_15m": ("mean", "q_spread_bps_med"),
        })
        m = m.with_columns([
            (pl.col("tr_n_5m") / 300.0).alias("tr_intensity_5m"),
            (pl.col("tr_n_15m") / 900.0).alias("tr_intensity_15m"),
            pl.col("tr_last_et_s").forward_fill().alias("_last_print_et_s"),
        ])
        m = m.with_columns([
            pl.lit(day, dtype=pl.Utf8).alias("day"),
            (pl.col("m") + 1).cast(pl.Int32).alias("t"),
            (pl.col("m") + 1 >= cov_lo_min + 1).alias("tape_covered"),
            (pl.col("tr_n_all") > 0).alias("minute_present"),
            (pl.col("q_n") > 0).alias("quote_present"),
            ((pl.col("m") + 1) * 60 - pl.col("_last_print_et_s")).alias("gap_s"),
            ((pl.col("m") + 1) - RTH_OPEN_ET_MIN).cast(pl.Int32).alias("t_since_open"),
            pl.lit(se, dtype=pl.Int32).alias("session_end"),
            pl.lit(quality.get(tk, "unknown")).alias("quality"),
        ])
        m = m.with_columns(
            pl.when(pl.col("t") < RTH_OPEN_ET_MIN).then(pl.lit("pre"))
            .when(pl.col("t") <= se + 1).then(pl.lit("rth"))
            .otherwise(pl.lit("post")).alias("session_state"))
        out.append(m.drop(["_last_print_et_s", "m"]))
    if not out:
        return pl.DataFrame()
    df = pl.concat(out)
    lead = ["day", "ticker", "t", "tape_covered", "minute_present", "quote_present",
            "session_state", "t_since_open", "gap_s", "session_end", "quality"]
    return df.select(lead + [c for c in df.columns if c not in lead])


def build_subminute(day: str, tickers: list[str], div: int, lo_b: int, hi_b: int,
                    tr: pl.DataFrame | None, q: pl.DataFrame | None, unit: int,
                    quality: dict[str, str]) -> pl.DataFrame:
    """Dense buckets over the day's observed tape window; bucket b covers [b, b+div)."""
    ta, qa = _bucket_agg(tr, q, unit, div, lo_b, hi_b)
    g = _grid(tickers, lo_b // div, hi_b // div).rename({"k": "bk"})
    if g.height == 0:
        return pl.DataFrame()
    g = g.with_columns((pl.col("bk") * div).cast(pl.Int64).alias("b"))
    if ta is not None:
        g = g.join(ta.rename({"symbol": "ticker"}), on=["ticker", "b"], how="left")
    if qa is not None:
        g = g.join(qa.rename({"symbol": "ticker"}), on=["ticker", "b"], how="left")
    g = _ensure(g, {**MIN_DEFAULTS, **SUB_EXTRA_DEFAULTS})
    g = _fill0(g, SUB_COUNT)
    g = g.with_columns([
        pl.when(pl.col("tr_shares") > 0)
        .then(pl.col("tr_dvol") / pl.col("tr_shares")).otherwise(None).alias("tr_vwap"),
        pl.lit(day, dtype=pl.Utf8).alias("day"),
        (pl.col("b") + div).cast(pl.Int32).alias("avail_et_s"),
        (pl.col("b") // 60).cast(pl.Int32).alias("b_min"),
        ((pl.col("b") % 60) // div).cast(pl.Int32).alias("b_in_minute"),
        (pl.col("tr_n_all") > 0).alias("has_print"),
        (pl.col("q_n") > 0).alias("has_quote"),
        ((pl.col("tr_n_all") == 0) & (pl.col("q_n") == 0)).alias("no_data"),
        (pl.col("tr_n_all") >= int(BURST_PRINTS_PER_S * div)).alias("burst"),
        pl.col("ticker").replace_strict(
            {tk: quality.get(tk, "unknown") for tk in tickers},
            default="unknown", return_dtype=pl.Utf8).alias("quality"),
    ])
    lead = ["day", "ticker", "b", "avail_et_s", "b_min", "b_in_minute", "no_data", "has_print",
            "has_quote", "burst", "quality"]
    return g.select(lead + [c for c in g.columns if c not in lead and c != "bk"])


# --------------------------------------------------------------------- entry layer
def _exec_match(d: dict | None, ref_min: int | None, fill_px: float | None,
                tol: float = 1e-9) -> dict:
    """Match the execution print STRICTLY inside the fill minute.

    Rules (parent audit 2026-10-02):
      * only prints whose ET minute equals `ref_min` are candidates - a repeated price hours
        later is never substituted;
      * the FIRST exec-eligible print in that minute is taken (exec_eligible = bar-eligible:
        can update open/close or high/low), never an arbitrary later price match;
      * `fill_px` is compared to that print; inequality is reported as `cross_feed_mismatch`
        with the bps gap, and is NOT called an exact fill;
      * no tape for the ticker, no ref minute, or no eligible print in the minute -> an explicit
        UNKNOWN basis and no exec timestamp (never a later-day/later-hour stand-in).
      * a print is never a proven fill: `fillability_basis` stays "quote_proxy" upstream.
    """
    out = {"exec_ts_us": None, "exec_ts_basis": None, "print_ts_us": None, "print_px": None,
           "print_size": None, "print_is_odd": None, "print_age_s": None,
           "print_delay_min": None, "exec_min_n": None, "print_px_vs_fill_bps": None}
    if d is None or not d.get("ts"):
        out["exec_ts_basis"] = "no_ticker_tape"
        return out
    if ref_min is None:
        out["exec_ts_basis"] = "no_ref_minute"
        return out
    etmin, elig = d["etmin"], d["elig"]
    ref = int(ref_min)
    if ref < etmin[0]:
        out["exec_ts_basis"] = "before_tape_window"
        return out
    if ref > etmin[-1]:
        out["exec_ts_basis"] = "after_tape_span"
        return out
    cand = [i for i in range(len(etmin)) if etmin[i] == ref and elig[i]]
    out["exec_min_n"] = len(cand)
    if not cand:
        out["exec_ts_basis"] = "no_print_in_ref_minute"
        return out
    pick = cand[0]
    px = float(d["px"][pick])
    out.update({"exec_ts_us": d["ts"][pick], "print_ts_us": d["ts"][pick], "print_px": px,
                "print_size": float(d["sz"][pick]), "print_is_odd": bool(d["odd"][pick]),
                "print_age_s": 0.0, "print_delay_min": int(etmin[pick]) - ref})
    if fill_px is None:
        out["exec_ts_basis"] = "print_first_in_ref_minute"
    elif abs(px - float(fill_px)) <= tol:
        out["exec_ts_basis"] = "print_px_match"
    else:
        out["exec_ts_basis"] = "cross_feed_mismatch"
        out["print_px_vs_fill_bps"] = (px / float(fill_px) - 1) * 1e4
    return out


def _self_test() -> int:
    """Regression for the fill-minute execution rule (same price repeated an hour later)."""
    def tick(ts, etm, px, sz=100.0, elig=True, odd=False):
        return {"ts": ts, "etmin": etm, "px": px, "sz": sz, "elig": elig, "odd": odd}
    fails = []

    def check(name, got, want):
        if got != want:
            fails.append(f"{name}: got {got!r} want {want!r}")

    ref = 700
    # a same-price print at the reference minute and again an hour later
    d = {"ts": [1_000, 4_600_000, 8_200_000], "etmin": [700, 701, 760],
         "px": [2.50, 2.60, 2.50], "sz": [100.0, 200.0, 300.0],
         "elig": [True, True, True], "odd": [False, False, False]}
    m = _exec_match(d, ref, 2.50)
    check("repeat-hour-later not selected", m["exec_ts_us"], 1_000)
    check("basis", m["exec_ts_basis"], "print_px_match")
    check("delay 0", m["print_delay_min"], 0)
    check("candidates only in ref minute", m["exec_min_n"], 1)
    # cross-feed mismatch inside the ref minute (print price differs from the provider price)
    m = _exec_match(d, ref, 2.75)
    check("mismatch basis", m["exec_ts_basis"], "cross_feed_mismatch")
    check("mismatch bps", round(m["print_px_vs_fill_bps"], 3), round((2.5 / 2.75 - 1) * 1e4, 3))
    # no eligible print in the ref minute (odd lot / non-price print only)
    d2 = {"ts": [1_000, 2_000], "etmin": [700, 700], "px": [2.50, 2.51], "sz": [100.0, 100.0],
          "elig": [False, False], "odd": [True, True]}
    m = _exec_match(d2, ref, 2.50)
    check("no eligible print", m["exec_ts_basis"], "no_print_in_ref_minute")
    check("no exec ts", m["exec_ts_us"], None)
    # first eligible print wins even when a later different price matches fill_px
    d3 = {"ts": [1_000, 2_000], "etmin": [700, 700], "px": [2.60, 2.50], "sz": [100.0, 100.0],
          "elig": [True, True], "odd": [False, False]}
    m = _exec_match(d3, ref, 2.50)
    check("first eligible wins", m["exec_ts_us"], 1_000)
    check("first eligible is a mismatch", m["exec_ts_basis"], "cross_feed_mismatch")
    # premarket / no-tape / missing ref-minute
    check("before window", _exec_match(d, 540, 2.50)["exec_ts_basis"], "before_tape_window")
    check("after span", _exec_match(d, 960, 2.50)["exec_ts_basis"], "after_tape_span")
    check("no ref", _exec_match(d, None, 2.50)["exec_ts_basis"], "no_ref_minute")
    check("no ticker tape", _exec_match(None, 700, 2.50)["exec_ts_basis"], "no_ticker_tape")
    check("no exec on no-print", _exec_match(d2, 700, 2.50)["print_delay_min"], None)
    for f in fails:
        print(f"[self-test FAIL] {f}")
    print(f"lifecycle_tape self-test: {'OK' if not fails else str(len(fails)) + ' FAILURES'}")
    return 1 if fails else 0


def build_entry(day: str, ros: pl.DataFrame, tr: pl.DataFrame | None, q: pl.DataFrame | None,
                unit: int, se_default: int, roster_missing: list[str]) -> pl.DataFrame:
    trm: dict[str, dict] = {}
    if tr is not None and tr.height:
        for key, sub in tr.partition_by("symbol", maintain_order=True, as_dict=True).items():
            s = key[0] if isinstance(key, tuple) else key
            trm[s] = {"ts": sub["ts_utc"].cast(pl.Int64).to_list(),
                      "etmin": sub["et_min"].to_list(), "et_s": sub["et_s"].to_list(),
                      "px": sub["price"].to_list(), "sz": sub["size"].to_list(),
                      "elig": sub["px_elig"].to_list(), "odd": sub["is_odd"].to_list(),
                      "vol": sub["vol_ok"].to_list()}
    qm: dict[str, dict] = {}
    if q is not None and q.height:
        qv = q.filter((pl.col("bid_price") > 0) & (pl.col("ask_price") > 0))
        for key, sub in qv.partition_by("symbol", maintain_order=True, as_dict=True).items():
            s = key[0] if isinstance(key, tuple) else key
            qm[s] = {"ts": sub["ts_utc"].cast(pl.Int64).to_list(),
                     "et_s": sub["et_s"].to_list(),
                     "bid": sub["bid_price"].to_list(), "ask": sub["ask_price"].to_list(),
                     "bl": sub["bid_size"].to_list(), "al": sub["ask_size"].to_list()}
    rows = []
    for r in ros.iter_rows(named=True):
        tk = r["ticker"]
        dec_et, fill_et = r.get("decision_et"), r.get("fill_et")
        ref_min = fill_et if fill_et is not None else dec_et
        px_ref = r.get("fill_px") if r.get("fill_px") is not None else r.get("decision_px")
        row = {
            "day": day, "clock": r["clock"], "rank": r["rank"], "ticker": tk,
            "status": r.get("status"), "decision_et": dec_et, "decision_px": r.get("decision_px"),
            "fill_et": fill_et, "fill_px": r.get("fill_px"),
            "prev_close_adj": r.get("prev_close_adj"), "gain_adj": r.get("gain_adj"),
            "session_end": r.get("session_end") if r.get("session_end") is not None else se_default,
            "trades_present": bool(tr is not None and tr.height and tk in trm),
            "quotes_present": tk in qm, "fillability_basis": "quote_proxy",
            "roster_missing_cols": ",".join(roster_missing), "size_unit": unit,
            "ref_et_min": ref_min, "print_delay_min": None,
        }
        d = trm.get(tk)
        match = _exec_match(d, ref_min, r.get("fill_px"))
        row.update(match)
        exec_ts = match["exec_ts_us"]
        qd = qm.get(tk)
        qref: list[tuple[str, int | None, float | None, str]] = []
        if qd is not None:
            if exec_ts is not None:
                i = bisect.bisect_left(qd["ts"], exec_ts) - 1
                qref.append(("exec", i if i >= 0 else None,
                             (exec_ts - qd["ts"][i]) / 1e6 if i >= 0 else None, "exec_print"))
            elif ref_min is not None:
                i = bisect.bisect_left(qd["et_s"], int(ref_min) * 60) - 1
                qref.append(("exec", i if i >= 0 else None,
                             (int(ref_min) * 60 - qd["et_s"][i]) if i >= 0 else None,
                             "ref_minute_start" if i >= 0 else "none"))
            else:
                qref.append(("exec", None, None, "none"))
            if dec_et is not None:
                i = bisect.bisect_left(qd["et_s"], int(dec_et) * 60) - 1
                qref.append(("dec", i if i >= 0 else None,
                             (int(dec_et) * 60 - qd["et_s"][i]) if i >= 0 else None,
                             "decision_minute_start" if i >= 0 else "none"))
            else:
                qref.append(("dec", None, None, "none"))
        else:
            qref = [("exec", None, None, "none"), ("dec", None, None, "none")]
        for tag, idx, age, _rb in qref:
            base = {f"q_{tag}_{f}": None for f in
                    ("et_us", "age_s", "bid", "ask", "bid_lots_raw", "ask_lots_raw",
                     "bid_shares", "ask_shares", "spread_bps", "mid", "imb", "locked",
                     "crossed", "age_ok")}
            if idx is not None and qd is not None:
                b, a = qd["bid"][idx], qd["ask"][idx]
                bl, al = qd["bl"][idx], qd["al"][idx]
                mid = (a + b) / 2
                bs, as_ = bl * unit, al * unit
                base.update({
                    f"q_{tag}_et_us": qd["ts"][idx], f"q_{tag}_age_s": age,
                    f"q_{tag}_bid": b, f"q_{tag}_ask": a,
                    f"q_{tag}_bid_lots_raw": bl, f"q_{tag}_ask_lots_raw": al,
                    f"q_{tag}_bid_shares": bs, f"q_{tag}_ask_shares": as_,
                    f"q_{tag}_spread_bps": (a - b) / mid * 1e4 if mid > 0 else None,
                    f"q_{tag}_mid": mid,
                    f"q_{tag}_imb": (bs - as_) / (bs + as_) if (bs + as_) > 0 else None,
                    f"q_{tag}_locked": bool(a == b), f"q_{tag}_crossed": bool(a < b),
                    f"q_{tag}_age_ok": age is not None and age <= Q_MAX_AGE_S,
                })
            base[f"q_{tag}_ref_basis"] = _rb
            row.update(base)
        ask, bid, mid = row.get("q_exec_ask"), row.get("q_exec_bid"), row.get("q_exec_mid")
        row.update({
            "cost_buy_at_ask_bps": (ask / px_ref - 1) * 1e4 if (ask and px_ref) else None,
            "cost_exit_at_bid_bps": (1 - bid / px_ref) * 1e4 if (bid and px_ref) else None,
            "cost_roundtrip_bps": (ask - bid) / mid * 1e4 if (ask and bid and mid) else None,
            "fill_vs_mid_bps": (px_ref / mid - 1) * 1e4 if (px_ref and mid) else None,
            "pos_in_quote": ((px_ref - bid) / (ask - bid)) if (ask and bid and ask > bid and px_ref)
            else None,
            "cap_ask_usd_displayed": (ask * row["q_exec_ask_shares"])
            if (ask and row.get("q_exec_ask_shares")) else None,
            "cap_bid_usd_displayed": (bid * row["q_exec_bid_shares"])
            if (bid and row.get("q_exec_bid_shares")) else None,
        })
        vsh = vusd = vn = None
        tape_covers = False
        if d is not None and ref_min is not None:
            etmin = d["etmin"]
            ets = d["et_s"]
            lo = bisect.bisect_left(etmin, int(ref_min))
            hi = bisect.bisect_right(etmin, int(ref_min))
            sz, px, vol = d["sz"], d["px"], d["vol"]
            vsh = float(sum(sz[i] for i in range(lo, hi) if vol[i]))
            vusd = float(sum(sz[i] * px[i] for i in range(lo, hi) if vol[i]))
            vn = hi - lo
            # the tape had started by the end of the reference minute
            tape_covers = bool(ets and ets[0] <= int(ref_min) * 60 + 60)
        row["tape_covers_ref"] = tape_covers
        row.update({"vol_ref_min_shares": vsh, "vol_ref_min_usd": vusd, "vol_ref_min_n": vn})
        rows.append(row)
    return _cast(pl.DataFrame(rows, infer_schema_length=None), ENTRY_DTYPES)


# ----------------------------------------------------------------- coverage layer
def build_coverage(day: str, tickers: list[str], tr: pl.DataFrame | None, q: pl.DataFrame | None,
                   unit_meta: dict, quality: dict[str, str], prov: dict) -> pl.DataFrame:
    rows = []
    for tk in tickers:
        d = tr.filter(pl.col("symbol") == tk) if (tr is not None and tr.height) else None
        qq = q.filter(pl.col("symbol") == tk) if (q is not None and q.height) else None
        ft = float(d["et_s"].min()) if (d is not None and d.height) else None
        lt = float(d["et_s"].max()) if (d is not None and d.height) else None
        fq = float(qq["et_s"].min()) if (qq is not None and qq.height) else None
        lq = float(qq["et_s"].max()) if (qq is not None and qq.height) else None
        r = {"day": day, "ticker": tk,
             "n_prints": d.height if d is not None else 0,
             "n_prints_vol": int(d["vol_ok"].sum()) if (d is not None and d.height) else 0,
             "n_quotes_valid": int(((qq["bid_price"] > 0) & (qq["ask_price"] > 0)).sum())
             if (qq is not None and qq.height) else 0,
             "first_trade_et_s": ft, "last_trade_et_s": lt,
             "first_trade_et_min": int(ft // 60) if ft is not None else None,
             "last_trade_et_min": int(lt // 60) if lt is not None else None,
             "first_quote_et_min": int(fq // 60) if fq is not None else None,
             "last_quote_et_min": int(lq // 60) if lq is not None else None,
             "pm_present": bool(ft is not None and ft < RTH_OPEN_ET_MIN * 60),
             "rth_covered_from_open": bool(ft is not None and ft <= RTH_OPEN_ET_MIN * 60),
             "max_print_gap_s": float(d["dt_s"].max()) if (d is not None and d.height) else None,
             "print_minutes": int(d["et_min"].n_unique()) if (d is not None and d.height) else 0,
             "size_unit": unit_meta["unit"], "size_unit_basis": unit_meta["basis"],
             "size_unit_detected": unit_meta.get("detected_unit"),
             "size_unit_conflict": unit_meta.get("conflict"),
             "frac_mult_100": unit_meta.get("frac_mult_100"),
             "median_quote_size_raw": unit_meta.get("median_raw"),
             "quality": quality.get(tk, "unknown")}
        r.update(prov)
        rows.append(r)
    return _cast(pl.DataFrame(rows, infer_schema_length=None), COVERAGE_DTYPES)


# ------------------------------------------------------------------- day pipeline
def _reusable(man: dict, layers: tuple, day: str, split: dict) -> bool:
    if man.get("producer_version") != PRODUCER_VERSION:
        return False
    if man.get("schema_version") != SCHEMA_VERSION or man.get("status") != "ok":
        return False
    if man.get("split_calendar_sha256") != split.get("calendar_sha256"):
        return False
    if man.get("split_set") != split_member(day, split):
        return False
    for layer in layers:
        info = (man.get("layers") or {}).get(layer) or {}
        p = info.get("path")
        if not p or not Path(p).exists() or Path(p).stat().st_size != info.get("bytes"):
            return False
    for k in ("trades", "quotes", "roster"):
        st = (man.get("sources") or {}).get(k) or {}
        if st.get("exists") and st.get("path"):
            p = Path(st["path"])
            if not p.exists():
                return False
            cur = p.stat()
            if cur.st_size != st.get("size") or cur.st_mtime_ns != st.get("mtime_ns"):
                return False
    return True


def process_day(day: str, data_root: Path, split: dict, force: bool = False,
                layers: tuple = LAYERS) -> dict:
    t0 = time.time()
    man_p = manifest_path(day, data_root)
    if not force and man_p.exists():
        try:
            if _reusable(json.loads(man_p.read_text()), layers, day, split):
                return {"day": day, "status": "skip", "rows": 0}
        except Exception:
            pass
    ros, roster_sha, roster_cols = read_roster(day, data_root)
    tr_p = _tape_source(data_root, "trades", day)
    q_p = _tape_source(data_root, "quotes", day)
    src = {"trades": _src_stamp(tr_p), "quotes": _src_stamp(q_p),
           "roster": _src_stamp(roster_path(day, data_root)),
           "net_coverage": _src_stamp(data_root / "sip" / "net" / "coverage" / f"{day}.json")}
    base = {"day": day, "study": "LIFECYCLE-01", "producer": PRODUCER,
            "producer_version": PRODUCER_VERSION, "schema_version": SCHEMA_VERSION,
            "roster_sha256": roster_sha, "roster_columns": roster_cols,
            "split_set": split_member(day, split),
            "split_calendar_sha256": split.get("calendar_sha256"),
            "sources": src, "layers": {}}
    if ros.height == 0:
        base.update({"status": "no_roster", "elapsed_s": round(time.time() - t0, 2)})
        _atomic_json(base, man_p)
        return {"day": day, "status": "no_roster", "rows": 0}
    if tr_p is None and q_p is None:
        base.update({"status": "no_sources", "elapsed_s": round(time.time() - t0, 2)})
        _atomic_json(base, man_p)
        return {"day": day, "status": "no_sources", "rows": 0}

    cal = json.loads(bps.CALENDAR.read_text())["evidence"]
    se = int(cal.get(day, {}).get("session_end") or
             (ros["session_end"].drop_nulls().max() or 959))
    tickers = sorted(set(ros["ticker"].to_list()))

    tr = q = None
    if tr_p is not None:
        tr = (pl.scan_parquet(tr_p).select(TRADE_COLS)
              .filter(pl.col("symbol").is_in(tickers)).collect())
        if tr.height:
            tr = (tr.with_columns(_clock_exprs()).sort(["symbol", "ts_utc"], maintain_order=True))
            tr = _decorate_trades(tr)
            tr = tr.with_columns(
                (pl.col("ts_utc").cast(pl.Int64).diff().over("symbol") / 1e6).alias("dt_s"))
    if q_p is not None:
        q = (pl.scan_parquet(q_p).select(QUOTE_COLS)
             .filter(pl.col("symbol").is_in(tickers)).collect())
        if q.height:
            q = q.with_columns(_clock_exprs()).sort(["symbol", "ts_utc"], maintain_order=True)
            q = q.with_columns([
                (pl.col("ts_utc").cast(pl.Int64).diff().over("symbol") / 1e6).alias("dt_s"),
                ((pl.col("bid_price") + pl.col("ask_price")) / 2).alias("mid"),
                (pl.col("bid_price") == pl.col("ask_price")).alias("locked"),
                (pl.col("bid_price") > pl.col("ask_price")).alias("crossed"),
                ((pl.col("bid_size") - pl.col("ask_size"))
                 / (pl.col("bid_size") + pl.col("ask_size"))).alias("imb"),
                ((pl.col("ask_price") - pl.col("bid_price"))
                 / ((pl.col("ask_price") + pl.col("bid_price")) / 2) * 1e4).alias("spread_bps"),
            ])
    unit_meta = detect_size_unit(q, day)
    unit = unit_meta["unit"]
    quality: dict[str, str] = {}
    cov_p = data_root / "sip" / "net" / "coverage" / f"{day}.json"
    if cov_p.exists():
        try:
            cj = json.loads(cov_p.read_text())
            quality = {s: v.get("cls") for s, v in (cj.get("per_symbol") or {}).items()}
        except Exception:
            quality = {}
    quality = {tk: quality.get(tk, "unknown") for tk in tickers}

    if tr is not None and tr.height:
        cov_lo_min = int(tr["et_min"].min())
    elif q is not None and q.height:
        cov_lo_min = int(q["et_min"].min())
    else:
        cov_lo_min = TAPE_LO_ET_MIN
    cov_lo_min = min(cov_lo_min, TAPE_LO_ET_MIN)
    sub_lo_s = (cov_lo_min * 60) // 5 * 5
    sub_hi_s = (se + 1) * 60
    t5 = tr.with_columns(((pl.col("et_s") // 5) * 5).cast(pl.Int64).alias("b")) \
        if (tr is not None and tr.height) else None
    q5 = q.with_columns(((pl.col("et_s") // 5) * 5).cast(pl.Int64).alias("b")) \
        if (q is not None and q.height) else None

    t0map: dict[str, int] = {}
    for r in ros.iter_rows(named=True):
        for c in ("decision_et", "fill_et"):
            v = r.get(c)
            if v is not None:
                t0map[r["ticker"]] = min(t0map.get(r["ticker"], 10 ** 9), int(v))

    ta, qa = _minute_agg(tr, q, unit)
    written: dict[str, dict] = {}
    if "tape" in layers:
        mt = build_minute(day, tickers, t0map, cov_lo_min, se, ta, qa, quality)
        p = layer_dir(data_root, "tape") / f"{day}.parquet"
        written["tape"] = {"path": str(p), "rows": mt.height, "bytes": _atomic_parquet(mt, p)}
    if "tape5" in layers:
        s5 = build_subminute(day, tickers, 5, sub_lo_s, sub_hi_s, t5, q5, unit, quality)
        p = layer_dir(data_root, "tape5") / f"{day}.parquet"
        written["tape5"] = {"path": str(p), "rows": s5.height, "bytes": _atomic_parquet(s5, p)}
    if "tape10" in layers:
        t10 = tr.with_columns(((pl.col("et_s") // 10) * 10).cast(pl.Int64).alias("b")) \
            if (tr is not None and tr.height) else None
        q10 = q.with_columns(((pl.col("et_s") // 10) * 10).cast(pl.Int64).alias("b")) \
            if (q is not None and q.height) else None
        s10 = build_subminute(day, tickers, 10, sub_lo_s, sub_hi_s, t10, q10, unit, quality)
        p = layer_dir(data_root, "tape10") / f"{day}.parquet"
        written["tape10"] = {"path": str(p), "rows": s10.height, "bytes": _atomic_parquet(s10, p)}
    if "entry" in layers:
        ent = build_entry(day, ros, tr, q, unit, se,
                          [c for c in ROSTER_OPT if c not in roster_cols])
        p = layer_dir(data_root, "entry") / f"{day}.parquet"
        written["entry"] = {"path": str(p), "rows": ent.height, "bytes": _atomic_parquet(ent, p)}
    if "coverage" in layers:
        cov = build_coverage(day, tickers, tr, q, unit_meta, quality,
                             {"tape_lo_et_min": TAPE_LO_ET_MIN, "cov_lo_et_min": cov_lo_min,
                              "sub_lo_s": sub_lo_s, "sub_hi_s": sub_hi_s, "session_end": se})
        p = layer_dir(data_root, "coverage") / f"{day}.parquet"
        written["coverage"] = {"path": str(p), "rows": cov.height, "bytes": _atomic_parquet(cov, p)}
    base.update({
        "status": "ok", "layers": written, "session_end": se, "size_unit": unit_meta,
        "coverage_window": {"cov_lo_et_min": cov_lo_min, "sub_lo_s": sub_lo_s,
                            "sub_hi_s": sub_hi_s, "tape_lo_et_min": TAPE_LO_ET_MIN},
        "counts": {"tickers": len(tickers), "roster_rows": ros.height,
                   "prints": int(tr.height) if tr is not None else 0,
                   "quotes": int(q.height) if q is not None else 0},
        "params": {"q_max_age_s": Q_MAX_AGE_S, "burst_prints_per_s": BURST_PRINTS_PER_S,
                   "size_unit_cutoff": SIZE_UNIT_CUTOFF},
        "elapsed_s": round(time.time() - t0, 2),
    })
    _atomic_json(base, man_p)
    return {"day": day, "status": "ok", "rows": sum(v["rows"] for v in written.values()),
            "tickers": len(tickers)}


def _worker(a):
    day, data_root, force, layers = a
    try:
        return process_day(day, Path(data_root), load_split(Path(data_root)), force, tuple(layers))
    except Exception as e:  # noqa: BLE001 - surfaced in the result, never swallowed
        return {"day": day, "status": f"error: {type(e).__name__}: {e}", "rows": 0}


# ------------------------------------------------------------------- public API
def load_manifest(day: str, data_root: str | Path | None = None) -> dict:
    dr = Path(data_root) if data_root else bps.resolve_data_root(None)
    p = manifest_path(day, dr)
    return json.loads(p.read_text()) if p.exists() else {}


def load_tape(day: str, layer: str = "tape", data_root: str | Path | None = None) -> pl.DataFrame:
    if layer not in LAYERS:
        raise ValueError(f"layer must be one of {LAYERS}")
    dr = Path(data_root) if data_root else bps.resolve_data_root(None)
    p = layer_dir(dr, layer) / f"{day}.parquet"
    return pl.read_parquet(p) if p.exists() else pl.DataFrame()


def load_tape_days(days, layer: str = "tape", data_root: str | Path | None = None) -> pl.DataFrame:
    dr = Path(data_root) if data_root else bps.resolve_data_root(None)
    fs = [f for d in days if (f := layer_dir(dr, layer) / f"{d}.parquet").exists()]
    return pl.read_parquet(fs) if fs else pl.DataFrame()


def asof_quote(day: str, ticker: str, ts_us: int, data_root: str | Path | None = None,
               unit: int | None = None) -> dict:
    """Last valid NBBO **strictly before** `ts_us` (UTC microseconds) for one ticker.

    Returns None-valued fields when no such quote exists. `size_unit`/`*_shares` follow the
    documented round-lot cutoff unless `unit` is given.
    """
    dr = Path(data_root) if data_root else bps.resolve_data_root(None)
    p = _tape_source(dr, "quotes", day)
    out = {"day": day, "ticker": ticker, "asof_ts_us": ts_us, "quote_ts_us": None,
           "age_s": None, "bid": None, "ask": None, "bid_lots_raw": None, "ask_lots_raw": None,
           "bid_shares": None, "ask_shares": None, "spread_bps": None, "mid": None, "imb": None,
           "locked": None, "crossed": None, "age_ok": None,
           "size_unit": unit if unit is not None else (100 if day < SIZE_UNIT_CUTOFF else 1)}
    if p is None:
        return out
    q = (pl.scan_parquet(p).select(QUOTE_COLS).filter(pl.col("symbol") == ticker)
         .filter((pl.col("bid_price") > 0) & (pl.col("ask_price") > 0)).collect())
    if not q.height:
        return out
    ts = q["ts_utc"].cast(pl.Int64).to_list()
    i = bisect.bisect_left(ts, int(ts_us)) - 1
    if i < 0:
        return out
    u = out["size_unit"]
    b, a = float(q["bid_price"][i]), float(q["ask_price"][i])
    bl, al = float(q["bid_size"][i]), float(q["ask_size"][i])
    mid = (a + b) / 2
    bs, as_ = bl * u, al * u
    age = (int(ts_us) - ts[i]) / 1e6
    out.update({"quote_ts_us": ts[i], "age_s": age, "bid": b, "ask": a, "bid_lots_raw": bl,
                "ask_lots_raw": al, "bid_shares": bs, "ask_shares": as_,
                "spread_bps": (a - b) / mid * 1e4 if mid > 0 else None, "mid": mid,
                "imb": (bs - as_) / (bs + as_) if (bs + as_) > 0 else None,
                "locked": bool(a == b), "crossed": bool(a < b), "age_ok": age <= Q_MAX_AGE_S})
    return out


def resolve_days(args, split: dict) -> list[str]:
    days: list[str] = []
    for tok in args.days:
        days.extend(d for d in tok.split(",") if d)
    if days:
        return days
    disc = split.get("discovery_days") or []
    val = split.get("validation_days") or []
    want = "discovery" if getattr(args, "dev_days", False) else args.split
    if want == "validation":
        days = val
    elif want == "all":
        days = sorted(disc + val)
    else:
        days = disc
    return days


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", nargs="+", default=[], help="explicit days (space or comma list)")
    ap.add_argument("--dev-days", action="store_true", help="discovery split (first 533 days)")
    ap.add_argument("--split", default="discovery", choices=("discovery", "validation", "all"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--threads", type=int, default=0, help="polars threads per worker")
    ap.add_argument("--layers", default=",".join(LAYERS))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--self-test", action="store_true",
                    help="run the fill-minute execution-matching regression and exit")
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()
    if args.self_test:
        return _self_test()
    data_root = bps.resolve_data_root(args.data_root)
    split = load_split(data_root)
    layers = tuple(x for x in args.layers.split(",") if x)
    bad = [x for x in layers if x not in LAYERS]
    if bad:
        raise SystemExit(f"unknown layer(s) {bad}; known {LAYERS}")
    days = resolve_days(args, split)
    if args.limit:
        days = days[:args.limit]
    if not days:
        ap.print_help()
        return 2
    t0 = time.time()
    done = skipped = 0
    if args.workers <= 1:
        for i, d in enumerate(days, 1):
            res = _worker((d, str(data_root), args.force, layers))
            skipped += res["status"] == "skip"
            done += res["status"] != "skip"
            if res["status"] not in ("skip",):
                print(f"  {i}/{len(days)} {res['day']} {res['status']} rows={res.get('rows', 0)}",
                      flush=True)
    else:
        import concurrent.futures as cf
        with cf.ProcessPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(_worker, (d, str(data_root), args.force, layers)) for d in days]
            for i, fut in enumerate(cf.as_completed(futs), 1):
                res = fut.result()
                skipped += res["status"] == "skip"
                done += res["status"] != "skip"
                if res["status"] not in ("skip",):
                    print(f"  {i}/{len(days)} {res['day']} {res['status']} rows={res.get('rows', 0)}",
                          flush=True)
    print(f"lifecycle_tape {PRODUCER_VERSION}: days={len(days)} built={done} reused={skipped} "
          f"el={time.time() - t0:.0f}s polars_threads={pl.thread_pool_size()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
