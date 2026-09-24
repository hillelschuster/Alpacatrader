#!/usr/bin/env python
"""BASKET-01 Phase-2 canonical event-driven simulator.

Single source of truth for every Phase-2 strategy family.  Implements
``factory/BASKET-SIM-CONTRACT.md`` literally.  Family modules compose the
action / release-rule / scale-in vocabulary declared here; they never edit
core semantics.

Design
------
* Day-major multi-strategy runner: each dev day's anatomy JSON + bars parquet
  are loaded once, then every strategy cell is evaluated on that day.
* Fills are read from the anatomy snapshot (never recomputed).
* No leverage: per basket-day sleeve ``cash >= 0`` and deployed cost basis
  <= ``C0`` are asserted at every event.
* Deterministic: no RNG except the month-resampled metrics bootstrap.

See ``factory/artifacts/basket/phase2/sim_core/README.md`` for the API, run
recipes, canary numbers and every contract ambiguity + resolution.

Python 3.11, polars + numpy only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date as _date
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import numpy as np
import polars as pl

# --------------------------------------------------------------------------- #
# Paths / constants
# --------------------------------------------------------------------------- #

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]                      # factory/scripts/x.py -> repo root
FACTORY = ROOT / "factory"

BASKET_ART_ROOT = Path(os.environ.get("BASKET_ART_ROOT", FACTORY / "artifacts" / "basket"))
SIP_ROOT = BASKET_ART_ROOT / "sip"
ANAT_DIR = SIP_ROOT / "anatomy"
BARS_DIR = SIP_ROOT / "bars"
CAL_PATH = SIP_ROOT / "phase2_session_calendar.json"
SIM_CORE = BASKET_ART_ROOT / "phase2" / "sim_core"

C0 = 1.0                                    # one sleeve unit per basket-day
FIRST_ET = 570                              # 09:30 ET
SESSION_END_NORMAL = 959                    # last regular minute (16:00 ET)
SESSION_END_EARLY = 779                     # last regular minute (13:00 ET)
EARLY_AUCTION_ET = 780                      # post-close auction print on half days
CHECKPOINTS = (580, 585, 590, 600, 615)     # golden-window evaluation points
BOOT_SEED = 20260922
BOOT_DRAWS = 10_000

# Phase-1 frozen ladder grid (must match basket_anatomy.py)
UP_LADDER = (5, 10, 20, 30, 50, 100)
DN_LADDER = (3, 5, 8, 10, 15)

# Hard evidence boundary: never open these.
SEALED_PREFIXES = ("2024-", "2025-01")
RESERVED_MONTHS = ("2026-06", "2026-07", "2026-08")

CONTRACT_PATH = FACTORY / "BASKET-SIM-CONTRACT.md"


def _contract_hash() -> str:
    """sha256 of the frozen contract file (for config provenance)."""
    try:
        return hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest()[:16]
    except FileNotFoundError:
        return "missing"


# --------------------------------------------------------------------------- #
# Guards
# --------------------------------------------------------------------------- #


def guard_day(day: str) -> None:
    """Refuse sealed/reserved days.  Called before any path is opened."""
    if not isinstance(day, str) or len(day) != 10:
        raise ValueError(f"bad day id: {day!r}")
    for p in SEALED_PREFIXES:
        if day.startswith(p):
            raise PermissionError(f"REFUSED sealed day {day}")
    if day[:7] in RESERVED_MONTHS:
        raise PermissionError(f"REFUSED reserved month {day}")


def dev_days() -> list[str]:
    """Canonical dev days = date stems present in the anatomy directory."""
    days = sorted(p.name[:-6] for p in ANAT_DIR.glob("*.jsonl"))
    if not days:
        raise FileNotFoundError(f"no anatomy days under {ANAT_DIR}")
    for d in days:
        guard_day(d)
    return days


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _atomic_write_json(path: Path, obj: Any) -> None:
    _atomic_write_bytes(path, json.dumps(obj, indent=1, sort_keys=True).encode())


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode())


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# --------------------------------------------------------------------------- #
# Substrate loaders
# --------------------------------------------------------------------------- #


def load_anatomy(day: str) -> dict:
    """Load the single JSON record for ``day`` (one line per file)."""
    guard_day(day)
    path = ANAT_DIR / f"{day}.jsonl"
    with path.open("rb") as fh:
        raw = fh.read()
    rec = json.loads(raw)
    if rec.get("date") != day:
        raise RuntimeError(f"anatomy date mismatch {path}: {rec.get('date')}")
    return rec


def snapshot_of(rec: dict, pop: str, T: int) -> Optional[dict]:
    """Return the ``{pop,T,name}`` snapshot, or None."""
    for s in rec["snapshots"]:
        if s["pop"] == pop and int(s["T"]) == int(T):
            return s
    return None


class Bars:
    """Per-ticker numpy arrays for one day (optionally session-filtered)."""

    __slots__ = ("date", "by_ticker", "all_tickers")

    def __init__(self, day: str, df: pl.DataFrame):
        self.date = day
        self.by_ticker: dict[str, dict[str, np.ndarray]] = {}
        self.all_tickers: list[str] = []
        if df.height == 0:
            return
        df = df.sort(["ticker", "et"])
        tk = df["ticker"].to_numpy()
        et = df["et"].to_numpy().astype(np.int64)
        op = df["open"].to_numpy().astype(np.float64)
        hi = df["high"].to_numpy().astype(np.float64)
        lo = df["low"].to_numpy().astype(np.float64)
        cl = df["close"].to_numpy().astype(np.float64)
        vo = df["volume"].to_numpy().astype(np.float64)
        starts = np.flatnonzero(np.r_[True, tk[1:] != tk[:-1]])
        ends = np.r_[starts[1:], len(tk)]
        for s, e in zip(starts, ends):
            t = str(tk[s])
            self.by_ticker[t] = {
                "et": et[s:e],
                "open": op[s:e],
                "high": hi[s:e],
                "low": lo[s:e],
                "close": cl[s:e],
                "volume": vo[s:e],
            }
        self.all_tickers = list(self.by_ticker.keys())

    def ticker(self, t: str) -> Optional[dict[str, np.ndarray]]:
        return self.by_ticker.get(t)

    def n_bars(self) -> int:
        return sum(len(v["et"]) for v in self.by_ticker.values())


def load_bars(day: str, session_end: Optional[int] = None) -> Bars:
    """Load ``bars/YYYY-MM-DD.parquet``.

    If ``session_end`` is given, bars with ``et > session_end`` are dropped
    (contract: no Phase-2 run may trade past the close).  First bar is 570.
    """
    guard_day(day)
    path = BARS_DIR / f"{day}.parquet"
    df = pl.read_parquet(
        path, columns=["ticker", "et", "open", "high", "low", "close", "volume"]
    )
    if session_end is not None:
        df = df.filter(pl.col("et") <= session_end)
    return Bars(day, df)


# --------------------------------------------------------------------------- #
# Session calendar (§2)
# --------------------------------------------------------------------------- #


def _calendar_evidence_for_day(day: str) -> dict:
    """Volume-falloff + post-close-print evidence for one day."""
    df = pl.read_parquet(BARS_DIR / f"{day}.parquet", columns=["et", "volume"])
    per = df.group_by("et").agg(pl.col("volume").sum().alias("v")).sort("et")
    et = per["et"].to_numpy()
    v = per["v"].to_numpy()
    tot = float(v.sum())
    post780 = float(v[et > EARLY_AUCTION_ET].sum())
    return {
        "date": day,
        "total_volume": tot,
        "post780_volume_share": (post780 / tot) if tot > 0 else 0.0,
        "max_et": int(et.max()) if len(et) else None,
        "has_bars_past_800": bool(len(et) and et.max() > 800),
        "n_minutes_past_900": int(((et > 900)).sum()),
        "volume_at_779": float(v[et == 779].sum()) if (et == 779).any() else 0.0,
        "volume_at_780": float(v[et == EARLY_AUCTION_ET].sum()) if (et == EARLY_AUCTION_ET).any() else 0.0,
        "volume_at_959": float(v[et == 959].sum()) if (et == 959).any() else 0.0,
    }


def build_session_calendar(days: Optional[list[str]] = None) -> dict:
    """Detect early closes mechanically and write the committed calendar.

    Rule: a day is an early close when its post-780 regular-session volume
    share collapses (< 10%) while after-hours prints extend past ~800.  The
    known NYSE half-day candidates are verified, not trusted, and all dev days
    are scanned for anomalies.
    """
    days = days or dev_days()
    known_candidates = {
        "2021-11-26", "2022-11-25", "2023-07-03", "2023-11-24",
        "2025-07-03", "2025-11-28", "2025-12-24",
    }
    per_day: dict[str, int] = {}
    evidence: dict[str, dict] = {}
    anomalies: list[dict] = []
    t0 = time.time()
    for i, d in enumerate(days):
        ev = _calendar_evidence_for_day(d)
        early = bool(ev["post780_volume_share"] < 0.10 and ev["has_bars_past_800"])
        per_day[d] = SESSION_END_EARLY if early else SESSION_END_NORMAL
        ev["session_end"] = per_day[d]
        ev["verdict"] = "early_close" if early else "normal"
        ev["in_known_candidate_list"] = d in known_candidates
        evidence[d] = ev
        if early and d not in known_candidates:
            anomalies.append({"date": d, "reason": "early-close signature, not in candidate list",
                              "post780_volume_share": ev["post780_volume_share"]})
        if (not early) and d in known_candidates:
            anomalies.append({"date": d, "reason": "candidate lacks early-close signature",
                              "post780_volume_share": ev["post780_volume_share"]})
        if (i + 1) % 200 == 0:
            print(f"  calendar scan {i+1}/{len(days)} ({time.time()-t0:.0f}s)", flush=True)

    detected = sorted(d for d, v in per_day.items() if v == SESSION_END_EARLY)
    doc = {
        "created": "2026-09-22",
        "method": (
            "mechanical volume-falloff: session_end=779 when post-780 ET volume share < 0.10 "
            "AND bars extend past et=800 (after-hours prints prove the filter is needed); "
            "otherwise 959. Verified by per-minute volume tables (auction spike at et=780, "
            "collapse at et>=781)."
        ),
        "session_end_normal": SESSION_END_NORMAL,
        "session_end_early": SESSION_END_EARLY,
        "known_candidates_checked": sorted(known_candidates),
        "detected_early_closes": detected,
        "n_days": len(per_day),
        "session_end_by_day": per_day,
        "anomalies": anomalies,
        "evidence": evidence,
        "scan_seconds": round(time.time() - t0, 1),
    }
    _atomic_write_json(CAL_PATH, doc)
    return doc


def load_calendar(rebuild: bool = False) -> dict:
    if rebuild or not CAL_PATH.exists():
        return build_session_calendar()
    with CAL_PATH.open("rb") as fh:
        return json.loads(fh.read())


def session_end_map(cal: Optional[dict] = None) -> dict[str, int]:
    cal = cal or load_calendar()
    return {k: int(v) for k, v in cal["session_end_by_day"].items()}


# --------------------------------------------------------------------------- #
# Reference release-rule library (§7)
# --------------------------------------------------------------------------- #


class ReleaseRule:
    """Completed-bar release rule.  ``evaluate`` returns an action dict or None.

    Returning ``{"action": "EXIT", "level": <price|None>, "reason": ...}``
    schedules an exit at the first later bar; ``level`` not None uses the
    adverse ``min(open, level)`` price convention.
    """

    name = "R?"

    def evaluate(self, tk: "Ticket", bar: dict, idx: int) -> Optional[dict]:
        raise NotImplementedError

    def state(self, tk: "Ticket") -> dict:
        return tk.rule_state.setdefault(self.name, {})


class R0(ReleaseRule):
    """Hold to forced flat (never releases)."""

    name = "R0"

    def evaluate(self, tk: "Ticket", bar: dict, idx: int) -> Optional[dict]:
        return None


class R1(ReleaseRule):
    """``low <= (1-L)*fill`` on a completed bar -> EXIT next open ``min(open, level)``.

    ``L_pct`` is signed percent: R1(-10) => level = fill*0.90 (stop -10%).
    """

    def __init__(self, L_pct: float):
        self.L = float(L_pct) / 100.0
        self.name = f"R1({L_pct:g})"

    def evaluate(self, tk: "Ticket", bar: dict, idx: int) -> Optional[dict]:
        st = self.state(tk)
        level = st.get("level")
        if level is None:
            level = tk.entry_px * (1.0 + self.L)
            st["level"] = level
        if bar["low"] <= level + 1e-12:
            return {"action": "EXIT", "level": level, "reason": self.name}
        return None


class R2(ReleaseRule):
    """Grace-window release.

    First breach of ``(1-L)*fill`` opens a window of ``w`` completed bars;
    release only if ``close`` at the window end is still ``<= level``
    (adverse ``min(open, level)`` next-bar exit), else disarm and re-arm.
    """

    def __init__(self, L_pct: float, w: int):
        self.L = float(L_pct) / 100.0
        self.w = int(w)
        self.name = f"R2({L_pct:g},{w})"

    def evaluate(self, tk: "Ticket", bar: dict, idx: int) -> Optional[dict]:
        st = self.state(tk)
        level = st.get("level")
        if level is None:
            level = tk.entry_px * (1.0 + self.L)
            st["level"] = level
        start = st.get("start")
        if start is None:
            if bar["low"] <= level + 1e-12:
                st["start"] = idx
            return None
        if idx - start >= self.w:
            st.pop("start", None)
            if bar["close"] <= level + 1e-12:
                return {"action": "EXIT", "level": level, "reason": self.name}
        return None


class R3(ReleaseRule):
    """Peak-relative trailing release.

    ``close <= (1-g)*running_peak`` -> EXIT next open (no level clamp).
    ``peak`` starts at the fill price and is updated *after* checks, so a
    bar's own high can never trigger its own rule.
    """

    def __init__(self, g_pct: float):
        self.g = float(g_pct) / 100.0
        self.name = f"R3({g_pct:g})"

    def evaluate(self, tk: "Ticket", bar: dict, idx: int) -> Optional[dict]:
        if bar["close"] <= (1.0 - self.g) * tk.peak + 1e-12:
            return {"action": "EXIT", "level": None, "reason": self.name}
        return None


class ScaleInRule:
    """Scale-in (ADD) module.  Emits ``{"action":"ADD","frac":...}`` or None."""

    name = "S?"

    def evaluate(self, tk: "Ticket", bar: dict, idx: int) -> Optional[dict]:
        raise NotImplementedError

    def state(self, tk: "Ticket") -> dict:
        return tk.rule_state.setdefault(self.name, {})


class ScaleInOnGain(ScaleInRule):
    """ADD ``size_pct`` of unit notional when completed close >= fill*(1+trig%)."""

    def __init__(self, trig_pct: float, size_pct: float, once: bool = True):
        self.trig = float(trig_pct) / 100.0
        self.size = float(size_pct) / 100.0
        self.once = bool(once)
        self.name = f"Add({trig_pct:g},{size_pct:g}{',once' if once else ''})"

    def evaluate(self, tk: "Ticket", bar: dict, idx: int) -> Optional[dict]:
        st = self.state(tk)
        if self.once and st.get("done"):
            return None
        if bar["close"] >= tk.entry_px * (1.0 + self.trig) - 1e-12:
            st["done"] = True
            return {"action": "ADD", "frac": self.size, "reason": self.name}
        return None


# --------------------------------------------------------------------------- #
# Ticket / strategy
# --------------------------------------------------------------------------- #


@dataclass
class Ticket:
    ticker: str
    sleeve_day: str                 # originating basket-day
    entry_et: int
    entry_px: float
    unit_notional: float
    shares: float = 0.0
    cost_open: float = 0.0          # cost basis of currently-open shares (deployed)
    cash_in: float = 0.0            # money spent on ENTER+ADD
    proceeds: float = 0.0           # money received on REDUCE+EXIT
    realized: float = 0.0           # realized cash P&L component
    peak: float = 0.0
    mfe: float = 0.0
    mae: float = 0.0
    add_notional: float = 0.0
    open: bool = True
    pending: Optional[dict] = None
    last_close: Optional[float] = None
    last_et: Optional[int] = None
    mark: float = 0.0               # end-of-session mark (shares * close)
    mark_prev: float = 0.0
    actions: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    rule_state: dict = field(default_factory=dict)
    exit_et: Optional[int] = None
    exit_px: Optional[float] = None
    exit_reason: Optional[str] = None
    n_adds: int = 0
    n_reduces: int = 0
    shares_entry: float = 0.0
    h_touch_et: dict = field(default_factory=dict)  # H -> first bar et with high >= (1+H/100)*fill
    reduced_before: dict = field(default_factory=dict)

    def net(self) -> float:
        """Total net P&L in sleeve units (incl. terminal mark if still open)."""
        return self.proceeds - self.cash_in + (self.mark if self.open else 0.0)

    def net_return(self) -> float:
        return self.net() / self.unit_notional if self.unit_notional else 0.0


@dataclass
class StrategySpec:
    """Declarative strategy cell (families may subclass ``Strategy`` instead)."""

    family_id: str = "baseline"
    entry_pop: str = "A_pm"
    entry_T: int = 570
    top_n: int = 10
    n_slots: int = 10
    reserve_frac: float = 1.0
    release: list = field(default_factory=list)      # list[ReleaseRule]
    scale_in: list = field(default_factory=list)     # list[ScaleInRule]
    name: str = "cell"

    @staticmethod
    def from_json(obj: dict) -> "StrategySpec":
        rules = []
        for r in obj.get("release", []):
            kind = r["rule"]
            if kind == "R0":
                rules.append(R0())
            elif kind == "R1":
                rules.append(R1(r["L"]))
            elif kind == "R2":
                rules.append(R2(r["L"], r["w"]))
            elif kind == "R3":
                rules.append(R3(r["g"]))
            else:
                raise ValueError(f"unknown release rule {kind}")
        scales = []
        for s in obj.get("scale_in", []):
            scales.append(ScaleInOnGain(s.get("trig", 10), s.get("size", 25),
                                        s.get("once", True)))
        return StrategySpec(
            family_id=obj.get("family_id", "cell"),
            entry_pop=obj.get("entry_pop", "A_pm"),
            entry_T=int(obj.get("entry_T", 570)),
            top_n=int(obj.get("top_n", 10)),
            n_slots=int(obj.get("n_slots", 10)),
            reserve_frac=float(obj.get("reserve_frac", 1.0)),
            release=rules,
            scale_in=scales,
            name=obj.get("name", "cell"),
        )


class Strategy:
    """Base class: one basket-day sleeve, independent accounting.

    Families subclass and may override :meth:`on_checkpoint`, or supply
    ``spec.release`` / ``spec.scale_in`` modules.  Core engine semantics
    (the event order of contract §7) are not overridable.
    """

    def __init__(self, spec: StrategySpec):
        self.spec = spec
        self.open_tickets: dict[str, Ticket] = {}
        self.closed: list[Ticket] = []
        self.sleeve_cash: dict[str, float] = {}
        self.deployed: dict[str, float] = {}     # sleeve_day -> open cost basis
        self.day_rows: list[dict] = []
        self.filled_days = 0
        self.n_blocked_slots = 0
        self.n_pending = 0
        self.n_carries = 0
        self.n_skipped_adds = 0
        self.flags: list[str] = []

    # -- hooks families may override ------------------------------------- #
    def on_checkpoint(self, et: int, ticket: Ticket, bar: dict) -> None:
        """Golden-window callback, evaluated on the completed checkpoint bar."""
        return None

    # -------------------------------------------------------------------- #
    def _cash(self, sleeve_day: str) -> float:
        return self.sleeve_cash.setdefault(sleeve_day, C0)

    def _deployed(self, sleeve_day: str) -> float:
        return self.deployed.setdefault(sleeve_day, 0.0)

    def _check_invariants(self, tk: Ticket, side: str) -> None:
        sd = tk.sleeve_day
        cash = self._cash(sd)
        dep = self._deployed(sd)
        if cash < -1e-9:
            raise AssertionError(f"leverage: cash<0 ({cash}) sleeve={sd} at {side}")
        if dep > C0 + 1e-9:
            raise AssertionError(f"leverage: deployed>{C0} ({dep}) sleeve={sd} at {side}")


# --------------------------------------------------------------------------- #
# Engine (§5-§8)
# --------------------------------------------------------------------------- #


def _bar(tk_bars: dict, pos: int) -> dict:
    return {
        "et": int(tk_bars["et"][pos]),
        "open": float(tk_bars["open"][pos]),
        "high": float(tk_bars["high"][pos]),
        "low": float(tk_bars["low"][pos]),
        "close": float(tk_bars["close"][pos]),
    }


def _pos_of_et(tk_bars: dict, et: int) -> int:
    a = tk_bars["et"]
    lo = int(np.searchsorted(a, et, side="left"))
    return lo if lo < len(a) and int(a[lo]) == et else -1


def _fill_mismatches(rec: dict, bars_full: Bars, out: list) -> int:
    """Canary 1 helper: assert fill.px == bars.open[fill.et] for all names."""
    n = 0
    for snap in rec["snapshots"]:
        for nm in snap["names"]:
            fl = nm.get("fill")
            if not fl:
                continue
            n += 1
            tkb = bars_full.ticker(nm["ticker"])
            if tkb is None:
                out.append({"day": rec["date"], "ticker": nm["ticker"], "error": "no_bars"})
                continue
            p = _pos_of_et(tkb, int(fl["et"]))
            if p < 0:
                out.append({"day": rec["date"], "ticker": nm["ticker"], "error": "no_bar_at_fill_et"})
                continue
            d = abs(float(tkb["open"][p]) - float(fl["px"]))
            if d > 1e-9:
                out.append({"day": rec["date"], "ticker": nm["ticker"], "et": int(fl["et"]),
                            "bar_open": float(tkb["open"][p]), "fill_px": float(fl["px"]), "diff": d})
    return n


def _apply_pending(strat: Strategy, tk: Ticket, bars: Bars, fix_side: float) -> Optional[dict]:
    """Execute ``tk.pending`` at its scheduled et if that bar exists today.

    Returns the executed action record, or None if it must carry.
    """
    pend = tk.pending
    if pend is None:
        return None
    tkb = bars.ticker(tk.ticker)
    if tkb is None:
        # no bars at all today -> involuntary carry, mark at last known close
        tk.flags.append("no_resumption")
        strat.n_carries += 1
        return None
    ets = tkb["et"]
    after = int(pend.get("after_et", -1))
    if pend.get("carry"):
        pos = 0
    else:
        pos = int(np.searchsorted(ets, after, side="right"))
    if pos >= len(ets):
        return None
    exec_et = int(ets[pos])
    open_px = float(tkb["open"][pos])
    level = pend.get("level")
    px = min(open_px, level) if level is not None else open_px
    act = pend["action"]
    tk.pending = None
    _execute(strat, tk, act, px, exec_et, pend.get("reason", act), fix_side,
             frac=pend.get("frac"))
    return {"action": act, "et": exec_et, "px": px, "reason": pend.get("reason", act)}


def _execute(strat: Strategy, tk: Ticket, action: str, px: float, et: int,
             reason: str, side: float, frac: Optional[float] = None) -> None:
    sd = tk.sleeve_day
    if action == "ENTER":
        budget = tk.unit_notional
        shares = budget / (px * (1.0 + side))
        tk.shares = shares
        tk.shares_entry = shares
        tk.cash_in += budget
        tk.cost_open += budget
        strat.sleeve_cash[sd] = strat._cash(sd) - budget
        strat.deployed[sd] = strat._deployed(sd) + budget
        tk.peak = px
    elif action == "ADD":
        notional = float(frac) * tk.unit_notional
        # cap: total ADD notional per ticket <= unit_notional (+100%)
        if tk.add_notional + notional > tk.unit_notional + 1e-12:
            tk.flags.append("add_cap_exceeded")
            strat.n_skipped_adds += 1
            strat.flags.append("add_cap_exceeded")
            strat._check_invariants(tk, "ADD-skip")
            return
        # no leverage: fund from sleeve cash AND keep deployed <= C0
        if strat._cash(sd) < notional - 1e-12 or strat._deployed(sd) + notional > C0 + 1e-12:
            tk.flags.append("add_unfunded")
            strat.n_skipped_adds += 1
            strat.flags.append("add_unfunded")
            strat._check_invariants(tk, "ADD-skip")
            return
        shares = notional / (px * (1.0 + side))
        tk.shares += shares
        tk.cash_in += notional
        tk.cost_open += notional
        tk.add_notional += notional
        strat.sleeve_cash[sd] = strat._cash(sd) - notional
        strat.deployed[sd] = strat._deployed(sd) + notional
        tk.n_adds += 1
    elif action == "REDUCE":
        old_shares = tk.shares
        sh = float(frac) * old_shares
        if sh > old_shares:
            sh = old_shares
        proceeds = sh * px * (1.0 - side)
        cost_removed = tk.cost_open * (sh / old_shares) if old_shares > 0 else 0.0
        tk.shares = old_shares - sh
        tk.cost_open = max(0.0, tk.cost_open - cost_removed)
        tk.proceeds += proceeds
        tk.realized += proceeds
        strat.sleeve_cash[sd] = strat._cash(sd) + proceeds
        strat.deployed[sd] = max(0.0, strat._deployed(sd) - cost_removed)
        tk.n_reduces += 1
    elif action == "EXIT":
        shares = tk.shares
        proceeds = shares * px * (1.0 - side)
        tk.shares = 0.0
        tk.proceeds += proceeds
        tk.realized += proceeds
        strat.sleeve_cash[sd] = strat._cash(sd) + proceeds
        strat.deployed[sd] = max(0.0, strat._deployed(sd) - tk.cost_open)
        tk.cost_open = 0.0
        tk.open = False
        tk.exit_et = et
        tk.exit_px = px
        tk.exit_reason = reason
    else:
        raise ValueError(f"unknown action {action}")
    tk.actions.append({"et": et, "action": action, "px": px, "reason": reason})
    strat._check_invariants(tk, action)
    if not tk.open:
        strat.closed.append(tk)
        strat.open_tickets.pop(tk.ticker, None)


def _schedule(strat: Strategy, tk: Ticket, action: str, reason: str,
              decision_et: int, level: Optional[float], frac: Optional[float] = None) -> None:
    tk.pending = {"action": action, "reason": reason, "after_et": decision_et,
                  "level": level, "frac": frac, "carry": False}


def simulate_day(strat: Strategy, day: str, rec: dict, bars: Bars,
                 session_end: int, bps_total: float,
                 do_entries: bool = True) -> dict:
    """Run one strategy on one day.  Mutates strategy state; returns day row."""
    side = bps_total / 2.0 / 10000.0
    # --- 1. entries ------------------------------------------------------ #
    if do_entries:
        snap = snapshot_of(rec, strat.spec.entry_pop, strat.spec.entry_T)
        if snap is not None:
            N = max(1, strat.spec.n_slots)
            budget = C0 * strat.spec.reserve_frac / N
            seen: set[str] = set()
            n_new = 0
            for nm in snap["names"][: strat.spec.top_n]:
                t = nm["ticker"]
                if t in seen:
                    continue
                seen.add(t)
                fl = nm.get("fill")
                if not fl or fl.get("blocked"):
                    strat.n_blocked_slots += 1
                    continue
                if t in strat.open_tickets:
                    continue
                tk = Ticket(ticker=t, sleeve_day=day, entry_et=int(fl["et"]),
                            entry_px=float(fl["px"]), unit_notional=budget)
                tk.pending = {"action": "ENTER", "reason": "ENTRY", "after_et": int(fl["et"]) - 1,
                              "level": None, "frac": None, "carry": False}
                strat._cash(day)
                strat.open_tickets[t] = tk
                n_new += 1
            if n_new:
                strat.filled_days += 1

    # --- active tickets: carries inherit their pending ------------------- #
    active = list(strat.open_tickets.values())

    # --- bar set: union of active ticket ets within session + session_end-1 #
    et_set: set[int] = set()
    for tk in active:
        tkb = bars.ticker(tk.ticker)
        if tkb is None:
            continue
        for e in tkb["et"]:
            e = int(e)
            if e <= session_end:
                et_set.add(e)
    et_set.add(session_end - 1)
    et_set.add(session_end)
    ordered = sorted(e for e in et_set if e >= FIRST_ET)

    day_cashflow = 0.0
    actions_today: list[dict] = []
    deployed_sum = 0.0
    deployed_bars = 0

    for t in ordered:
        # -- A. execute pendings scheduled for this et --------------------- #
        for tk in list(active):
            if not tk.open:
                continue
            pend = tk.pending
            if pend is None:
                continue
            tkb = bars.ticker(tk.ticker)
            if tkb is not None:
                pos = _pos_of_et(tkb, t)
            else:
                pos = -1
            # a pending executes at the first bar with et > decision_et, or -- for
            # cross-session carries -- at the first available bar of the new session.
            exec_here = (pos >= 0 and
                         (bool(pend.get("carry")) or t > int(pend["after_et"])))
            if exec_here:
                open_px = float(tkb["open"][pos])
                lvl = pend.get("level")
                px = min(open_px, lvl) if lvl is not None else open_px
                cash_before = strat._cash(tk.sleeve_day)
                action = pend["action"]
                tk.pending = None
                _execute(strat, tk, action, px, t, pend.get("reason", action), side,
                         frac=pend.get("frac"))
                cash_after = strat._cash(tk.sleeve_day)
                day_cashflow += cash_after - cash_before
                actions_today.append({"et": t, "ticker": tk.ticker, "action": action,
                                      "px": px, "reason": pend.get("reason", action)})
                if not tk.open:
                    continue

        # -- B. decisions on the completed bar t ---------------------------- #
        for tk in list(active):
            if not tk.open or tk.pending is not None:
                continue
            tkb = bars.ticker(tk.ticker)
            pos = _pos_of_et(tkb, t) if tkb is not None else -1
            bar = _bar(tkb, pos) if pos >= 0 else None

            # 1. forced flat: a market-clock decision at the last completed
            #    session bar; applies to every open ticket (halted names carry).
            if t == session_end - 1:
                _schedule(strat, tk, "EXIT", "FORCED_FLAT", t, None)
                continue

            if bar is None:
                continue

            # 2. release modules (first firing consumes the bar)
            fired = False
            for rule in strat.spec.release:
                act = rule.evaluate(tk, bar, pos)
                if act is not None:
                    if act["action"] == "EXIT":
                        _schedule(strat, tk, "EXIT", act.get("reason", rule.name), t,
                                  act.get("level"))
                    else:  # REDUCE scale-out variant
                        _schedule(strat, tk, "REDUCE", act.get("reason", rule.name), t,
                                  act.get("level"), frac=act.get("frac", 0.5))
                    fired = True
                    break

            # 3. scale-in modules (only if nothing released)
            if not fired:
                for sc in strat.spec.scale_in:
                    act = sc.evaluate(tk, bar, pos)
                    if act is not None:
                        _schedule(strat, tk, "ADD", act.get("reason", sc.name), t,
                                  None, frac=act.get("frac"))
                        break

            # 3b. golden-window checkpoint hook
            if t in CHECKPOINTS:
                strat.on_checkpoint(t, tk, bar)

            # 4. state updates AFTER checks
            if bar["high"] > tk.peak:
                tk.peak = bar["high"]
            tk.mfe = max(tk.mfe, bar["high"] / tk.entry_px - 1.0)
            tk.mae = min(tk.mae, bar["low"] / tk.entry_px - 1.0)
            tk.last_close = bar["close"]
            tk.last_et = t
            for H in (30, 50, 100, 200):
                if H not in tk.h_touch_et and bar["high"] >= tk.entry_px * (1 + H / 100.0):
                    tk.h_touch_et[H] = t
                    tk.reduced_before[H] = False

        # -- deployed time-weight ------------------------------------------ #
        dep = sum(tk.cost_open for tk in active if tk.open)
        deployed_sum += dep
        deployed_bars += 1

    # --- end of day: mark open tickets, finalize marks, carry flags ------- #
    mark_end_total = 0.0
    mark_prev_total = 0.0
    for tk in active:
        mark_prev_total += tk.mark
        if tk.open:
            tkb = bars.ticker(tk.ticker)
            if tkb is not None:
                in_sess = np.flatnonzero(tkb["et"] <= session_end)
                if len(in_sess):
                    tk.last_close = float(tkb["close"][in_sess[-1]])
                    tk.last_et = int(tkb["et"][in_sess[-1]])
            mark = tk.shares * (tk.last_close if tk.last_close else tk.entry_px)
        else:
            mark = 0.0
        tk.mark_prev = tk.mark
        tk.mark = mark
        mark_end_total += mark
        if tk.open:
            if tk.pending is None:
                tk.flags.append("open_no_pending")
            else:
                tk.pending["carry"] = True
                strat.n_pending += 1
                strat.n_carries += 1

    pnl = day_cashflow + (mark_end_total - mark_prev_total)
    r_day = pnl / C0
    row = {
        "date": day,
        "r_day": r_day,
        "pnl": pnl,
        "deployed_end": sum(tk.cash_in for tk in active if tk.open),
        "deployed_avg": (deployed_sum / deployed_bars) if deployed_bars else 0.0,
        "n_actions": len(actions_today),
        "n_open_end": sum(1 for tk in active if tk.open),
        "flags": sorted({f for tk in active for f in tk.flags}),
    }
    strat.day_rows.append(row)
    return row


# --------------------------------------------------------------------------- #
# Metrics (§9)
# --------------------------------------------------------------------------- #


def _pctile(a: np.ndarray, q: float) -> float:
    return float(np.percentile(a, q)) if len(a) else float("nan")


def compute_metrics(days: list[dict], tickets: list[Ticket], n_dev_days: int,
                    n_blocked_slots: int, n_pending: int, n_carries: int,
                    seed: int = BOOT_SEED, draws: int = BOOT_DRAWS) -> dict:
    """All §9 metrics.  Returns a JSON-safe dict."""
    r = np.array([d["r_day"] for d in days], dtype=np.float64)
    dates = [d["date"] for d in days]
    n = len(r)
    mean = float(r.mean()) if n else float("nan")
    med = float(np.median(r)) if n else float("nan")
    std = float(r.std(ddof=1)) if n > 1 else 0.0

    # month-blocked day-clustered bootstrap
    months: dict[str, list[float]] = {}
    for dt, x in zip(dates, r):
        months.setdefault(dt[:7], []).append(float(x))
    month_arrs = [np.array(v) for v in months.values()]
    rng = np.random.default_rng(seed)
    if month_arrs:
        M = len(month_arrs)
        means = np.empty(draws)
        for i in range(draws):
            idx = rng.integers(0, M, size=M)
            means[i] = np.concatenate([month_arrs[j] for j in idx]).mean()
        ci = [_pctile(means, 2.5), _pctile(means, 97.5)]
    else:
        ci = [float("nan"), float("nan")]

    # compounded equity (secondary convention)
    eq = np.cumprod(1.0 + r) if n else np.array([1.0])
    peak = np.maximum.accumulate(eq)
    dd = (eq / peak - 1.0)
    compounded_growth = float(eq[-1]) if n else 1.0
    compounded_max_dd = float(dd.min()) if n else 0.0

    # calendar aggregates
    def key_sums(kf):
        agg: dict[str, float] = {}
        for dt, x in zip(dates, r):
            agg[kf(dt)] = agg.get(kf(dt), 0.0) + float(x)
        return agg

    wk = key_sums(lambda d: f"{_date.fromisoformat(d).isocalendar()[0]}-W{_date.fromisoformat(d).isocalendar()[1]:02d}")
    mo = key_sums(lambda d: d[:7])
    worst_week = min(wk.values()) if wk else float("nan")
    worst_month = min(mo.values()) if mo else float("nan")

    # per-year / per-quarter headline block
    def block(rows: list[float]) -> dict:
        a = np.array(rows)
        eqb = np.cumprod(1 + a) if len(a) else np.array([1.0])
        pkb = np.maximum.accumulate(eqb)
        return {
            "days_n": len(a),
            "mean": float(a.mean()) if len(a) else None,
            "median": float(np.median(a)) if len(a) else None,
            "std": float(a.std(ddof=1)) if len(a) > 1 else 0.0,
            "sum": float(a.sum()) if len(a) else 0.0,
            "worst_day": float(a.min()) if len(a) else None,
            "positive_share": float((a > 0).mean()) if len(a) else None,
            "compounded_growth": float(eqb[-1]) if len(a) else 1.0,
            "compounded_max_dd": float((eqb / pkb - 1).min()) if len(a) else 0.0,
        }

    per_year, per_quarter = {}, {}
    for dt, x in zip(dates, r):
        per_year.setdefault(dt[:4], []).append(float(x))
        y, q = dt[:4], (int(dt[5:7]) - 1) // 3 + 1
        per_quarter.setdefault(f"{y}-Q{q}", []).append(float(x))
    per_year = {k: block(v) for k, v in per_year.items()}
    per_quarter = {k: block(v) for k, v in per_quarter.items()}

    # ticket-level metrics
    nets = np.array([tk.net() for tk in tickets], dtype=np.float64)
    rets = np.array([tk.net_return() for tk in tickets], dtype=np.float64)
    total_net = float(nets.sum()) if len(nets) else 0.0
    pos = rets[rets > 0]
    neg = rets[rets < 0]
    avg_failed = float(neg.mean()) if len(neg) else None
    survivor_share = (float(nets[nets > 0].sum() / total_net)
                      if total_net > 0 and len(nets) else None)

    # path contribution by raw post-fill MFE
    mfe = np.array([tk.mfe for tk in tickets], dtype=np.float64)
    path_contrib = {}
    for H in (50, 100, 200):
        mask = mfe >= H / 100.0
        path_contrib[str(H)] = (float(nets[mask].sum() / total_net)
                                if total_net > 0 and mask.any() else None)

    # false / half release rates
    false_release, half_release = {}, {}
    for H in (30, 50, 100):
        touch = [tk for tk in tickets if H in tk.h_touch_et]
        fr = hr = None
        if touch:
            f = 0
            h = 0
            for tk in touch:
                tet = tk.h_touch_et[H]
                # fully flat before touch?
                flat_et = tk.exit_et if (not tk.open and tk.shares == 0) else None
                if flat_et is not None and flat_et < tet:
                    f += 1
                # reduced >=50% before touch?
                red = 0.0
                for a in tk.actions:
                    if a["action"] == "REDUCE" and a["et"] < tet:
                        red += 0.0  # counted below via n_reduces timing
                early_red = any(a["action"] == "REDUCE" and a["et"] < tet for a in tk.actions)
                if early_red:
                    h += 1
            fr = f / len(touch)
            hr = h / len(touch)
        false_release[str(H)] = fr
        half_release[str(H)] = hr

    # tail retained
    tail = [tk.net() / tk.unit_notional / tk.mfe for tk in tickets
            if tk.mfe >= 0.50 and tk.mfe > 0]
    tail_retained = {"mean": float(np.mean(tail)) if tail else None,
                     "median": float(np.median(tail)) if tail else None,
                     "n": len(tail)}

    # top-k day share of total net P&L
    day_pnl = np.array([d["pnl"] for d in days], dtype=np.float64)
    tot_day = float(day_pnl.sum())
    def top_share(k):
        if tot_day <= 0 or len(day_pnl) == 0:
            return None
        return float(np.sort(day_pnl)[::-1][:k].sum() / tot_day)
    top_share_1, top_share_5, top_share_10 = top_share(1), top_share(5), top_share(10)

    dep = np.array([d.get("deployed_avg", 0.0) for d in days], dtype=np.float64)
    executed_notional = float(sum(abs(tk.cash_in) + abs(tk.proceeds) for tk in tickets))
    n_entries = len(tickets)
    n_adds = sum(tk.n_adds for tk in tickets)
    n_reduces = sum(tk.n_reduces for tk in tickets)
    n_exits = sum(1 for tk in tickets if not tk.open)

    m = {
        "days_n": n,
        "dev_days_n": n_dev_days,
        "filled_days": sum(1 for d in days if d["n_actions"] > 0),
        "mean_basket_day": mean,
        "median_basket_day": med,
        "std_basket_day": std,
        "bootstrap_ci95": ci,
        "bootstrap_draws": draws,
        "bootstrap_seed": seed,
        "bootstrap_method": "month-resample with replacement, day-clustered",
        "compounded_growth": compounded_growth,
        "compounded_max_dd": compounded_max_dd,
        "worst_day": float(r.min()) if n else None,
        "worst_week": worst_week,
        "worst_month": worst_month,
        "positive_day_share": float((r > 0).mean()) if n else None,
        "daily_percentiles": {f"p{q}": _pctile(r, q) for q in (1, 5, 10, 25, 50, 75, 90, 95, 99)},
        "avg_deployed_capital": float(dep.mean()) if n else 0.0,
        "turnover_per_day": (executed_notional / C0 / n) if n else 0.0,
        "turnover_annualized": (executed_notional / C0 / n * 252) if n else 0.0,
        "n_entries": n_entries,
        "n_adds": n_adds,
        "n_reduces": n_reduces,
        "n_exits": n_exits,
        "n_blocked_slots": n_blocked_slots,
        "n_pending": n_pending,
        "n_carries": n_carries,
        "avg_failed_ticket_cost": avg_failed,
        "avg_survivor_contribution": {
            "mean_net_return": float(pos.mean()) if len(pos) else None,
            "share_of_total_net": survivor_share,
        },
        "top1_day_share": top_share_1,
        "top5_day_share": top_share_5,
        "top10_day_share": top_share_10,
        "path_contrib": path_contrib,
        "false_release_rate": false_release,
        "half_release_rate": half_release,
        "tail_retained": tail_retained,
        "per_year": per_year,
        "per_quarter": per_quarter,
    }
    return m


# --------------------------------------------------------------------------- #
# Runner + outputs (§10)
# --------------------------------------------------------------------------- #


def _ticket_row(tk: Ticket) -> dict:
    return {
        "ticker": tk.ticker,
        "sleeve_day": tk.sleeve_day,
        "entry_et": tk.entry_et,
        "entry_px": tk.entry_px,
        "unit_notional": tk.unit_notional,
        "shares_entry": tk.shares_entry,
        "n_adds": tk.n_adds,
        "n_reduces": tk.n_reduces,
        "exit_et": tk.exit_et,
        "exit_px": tk.exit_px,
        "exit_reason": tk.exit_reason,
        "open_end": tk.open,
        "net": tk.net(),
        "net_return": tk.net_return(),
        "mfe_raw": tk.mfe,
        "mae_raw": tk.mae,
        "peak": tk.peak,
        "flags": ",".join(sorted(set(tk.flags))),
    }


@dataclass
class RunConfig:
    family_id: str
    run_id: str
    spec: StrategySpec
    bps_total: float = 100.0
    out_root: Path = SIM_CORE
    max_days: Optional[int] = None
    days: Optional[list[str]] = None
    workers: int = 4

    @property
    def run_dir(self) -> Path:
        return self.out_root / self.family_id / self.run_id


def _read_day_pair(day: str, session_end: int, filter_session: bool) -> tuple[dict, Bars]:
    rec = load_anatomy(day)
    bars = load_bars(day, session_end if filter_session else None)
    return rec, bars


def run(cfg: RunConfig, progress: bool = True) -> dict:
    """Day-major run.  Writes month parts incrementally; returns summary."""
    days = cfg.days or dev_days()
    if cfg.max_days:
        days = days[: cfg.max_days]
    run_dir = cfg.run_dir
    part_daily = run_dir / "parts" / "daily"
    part_tick = run_dir / "parts" / "tickets"
    part_daily.mkdir(parents=True, exist_ok=True)
    part_tick.mkdir(parents=True, exist_ok=True)
    progress_path = run_dir / "_progress.json"
    done: set[str] = set()
    part_files = [p for d in (part_daily, part_tick) for p in d.iterdir() if p.is_file()]
    progress_valid = not progress_path.exists() and not part_files
    if progress_path.exists():
        try:
            raw_done = json.loads(progress_path.read_text()).get("done")
            if (not isinstance(raw_done, list)
                    or any(not isinstance(day, str) for day in raw_done)
                    or len(set(raw_done)) != len(raw_done)):
                raise ValueError("invalid progress dates")
            done = set(raw_done)
            part_day_rows = [
                day
                for path in part_daily.glob("*.parquet")
                for day in pl.read_parquet(path, columns=["date"])["date"].to_list()
            ]
            part_days = set(part_day_rows)
            ticket_days = {
                day
                for path in part_tick.glob("*.parquet")
                for day in pl.read_parquet(path, columns=["sleeve_day"])["sleeve_day"].to_list()
            }
            ticket_keys = [
                (row["sleeve_day"], row["ticker"])
                for path in part_tick.glob("*.parquet")
                for row in pl.read_parquet(path, columns=["sleeve_day", "ticker"]).iter_rows(named=True)
            ]
            progress_valid = (
                done == part_days
                and len(part_day_rows) == len(part_days)
                and ticket_days <= part_days
                and len(ticket_keys) == len(set(ticket_keys))
                and done <= set(days)
                and all(path.suffix == ".parquet" for path in part_files)
            )
        except Exception:
            done = set()
            progress_valid = False

    if not progress_valid:
        for directory in (part_daily, part_tick):
            for path in directory.iterdir():
                if path.is_file():
                    path.unlink()
        for name in (
            "daily.parquet", "tickets.parquet", "daily.tmp", "tickets.tmp",
            "run_summary.json", "run_summary.json.tmp", "config.json",
            "config.json.tmp", "metrics.json", "metrics.json.tmp",
            "surface.json", "surface.json.tmp", "_progress.json",
            "_progress.json.tmp",
        ):
            (run_dir / name).unlink(missing_ok=True)
        done.clear()

    summary_path = run_dir / "run_summary.json"
    if days and set(days) <= done and summary_path.exists():
        try:
            saved = json.loads(summary_path.read_text())
            for kind, hash_key, rows_key in (
                ("daily", "daily_sha256", "daily_rows"),
                ("tickets", "tickets_sha256", "ticket_rows"),
            ):
                path = run_dir / f"{kind}.parquet"
                if not path.exists():
                    raise ValueError(f"missing {kind} output")
                data = path.read_bytes()
                if (saved.get(hash_key) != _sha256_bytes(data)
                        or pl.read_parquet(path).height != saved.get(rows_key)):
                    raise ValueError(f"invalid {kind} output")
            return saved
        except (OSError, ValueError, json.JSONDecodeError, TypeError):
            pass

    sem = session_end_map()
    strat = Strategy(cfg.spec)

    # month buffers
    buf_daily: dict[str, list[dict]] = {}
    buf_tickets: dict[str, list[dict]] = {}
    t0 = time.time()
    load_s = 0.0
    n_done = 0

    for day in sorted(done.intersection(days)):
        se = sem.get(day, SESSION_END_NORMAL)
        tl = time.time()
        rec, bars = _read_day_pair(day, se, True)
        load_s += time.time() - tl
        simulate_day(strat, day, rec, bars, se, cfg.bps_total)

    # Sliding-window threaded prefetch: overlap parquet/JSON reads with compute.
    todo = [d for d in days if d not in done]
    ex = ThreadPoolExecutor(max_workers=max(1, cfg.workers))
    window = max(1, cfg.workers) * 2
    pending: dict[str, Any] = {}
    for d in todo[:window]:
        pending[d] = ex.submit(_read_day_pair, d, sem.get(d, SESSION_END_NORMAL), True)
    for idx, day in enumerate(todo):
        se = sem.get(day, SESSION_END_NORMAL)
        tl = time.time()
        fut = pending.pop(day, None)
        if fut is None:
            fut = ex.submit(_read_day_pair, day, se, True)
        rec, bars = fut.result()
        load_s += time.time() - tl
        nxt = idx + window
        if nxt < len(todo):
            nd = todo[nxt]
            pending[nd] = ex.submit(_read_day_pair, nd, sem.get(nd, SESSION_END_NORMAL), True)
        prev_closed = len(strat.closed)
        row = simulate_day(strat, day, rec, bars, se, cfg.bps_total)
        buf_daily.setdefault(day[:7], []).append(row)
        for tk in strat.closed[prev_closed:]:
            buf_tickets.setdefault(day[:7], []).append(_ticket_row(tk))
        n_done += 1
        done.add(day)
        if (idx + 1) % 25 == 0 or idx == len(todo) - 1:
            _flush_parts(part_daily, part_tick, buf_daily, buf_tickets)
            _atomic_write_json(progress_path, {"done": sorted(done)})
            if progress:
                el = time.time() - t0
                print(f"  [{idx+1}/{len(todo)}] days, {el:.0f}s, load {load_s:.0f}s, "
                      f"open={len(strat.open_tickets)} closed={len(strat.closed)}", flush=True)
    ex.shutdown(wait=True)

    _flush_parts(part_daily, part_tick, buf_daily, buf_tickets)

    term_rows = [_ticket_row(tk) for tk in strat.open_tickets.values()]
    daily_df = merge_parts(run_dir, "daily")
    tick_df = merge_parts(run_dir, "tickets")
    if term_rows:
        terminal_df = pl.DataFrame(
            [_normalize_row(row, _TICKET_SCHEMA) for row in term_rows],
            schema=_TICKET_SCHEMA,
        )
        tick_df = pl.concat([tick_df, terminal_df], how="vertical_relaxed")
        tick_path = run_dir / "tickets.parquet"
        tick_tmp = tick_path.with_suffix(".tmp")
        tick_df.write_parquet(tick_tmp)
        os.replace(tick_tmp, tick_path)
    metrics = compute_metrics(strat.day_rows, strat.closed + list(strat.open_tickets.values()),
                              len(days), strat.n_blocked_slots, strat.n_pending,
                              strat.n_carries)
    cfg_obj = {
        "family_id": cfg.family_id,
        "run_id": cfg.run_id,
        "strategy": cfg.spec.name,
        "entry_pop": cfg.spec.entry_pop,
        "entry_T": cfg.spec.entry_T,
        "top_n": cfg.spec.top_n,
        "n_slots": cfg.spec.n_slots,
        "reserve_frac": cfg.spec.reserve_frac,
        "release": [r.name for r in cfg.spec.release],
        "scale_in": [s.name for s in cfg.spec.scale_in],
        "bps_total": cfg.bps_total,
        "days": [days[0], days[-1]] if days else [],
        "n_days": len(days),
        "seed": BOOT_SEED,
        "git_head": _git_head(),
        "sim_contract_hash": _contract_hash(),
        "contract_version": "FROZEN-2026-09-22",
    }
    _atomic_write_json(run_dir / "config.json", cfg_obj)
    _atomic_write_json(run_dir / "metrics.json", metrics)
    _atomic_write_json(run_dir / "surface.json",
                       {"family_id": cfg.family_id, "cells": [cfg.spec.name]})
    elapsed = time.time() - t0
    summary = {
        "run_dir": str(run_dir),
        "days_run": n_done,
        "wall_seconds": elapsed,
        "per_day_load_seconds": (load_s / n_done) if n_done else 0.0,
        "load_seconds_total": load_s,
        "daily_rows": daily_df.height,
        "ticket_rows": tick_df.height,
        "daily_sha256": _sha256_bytes((run_dir / "daily.parquet").read_bytes()),
        "tickets_sha256": _sha256_bytes((run_dir / "tickets.parquet").read_bytes()),
        "metrics": metrics,
    }
    _atomic_write_json(run_dir / "run_summary.json", summary)
    return summary


def _git_head() -> str:
    try:
        import subprocess
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return "unknown"


_DAILY_SCHEMA = {
    "date": pl.Utf8, "r_day": pl.Float64, "pnl": pl.Float64,
    "deployed_end": pl.Float64, "deployed_avg": pl.Float64,
    "n_actions": pl.Int64, "n_open_end": pl.Int64, "flags": pl.Utf8,
}
_TICKET_SCHEMA = {
    "ticker": pl.Utf8, "sleeve_day": pl.Utf8, "entry_et": pl.Int64, "entry_px": pl.Float64,
    "unit_notional": pl.Float64, "shares_entry": pl.Float64, "n_adds": pl.Int64,
    "n_reduces": pl.Int64, "exit_et": pl.Int64, "exit_px": pl.Float64,
    "exit_reason": pl.Utf8, "open_end": pl.Boolean, "net": pl.Float64,
    "net_return": pl.Float64, "mfe_raw": pl.Float64, "mae_raw": pl.Float64,
    "peak": pl.Float64, "flags": pl.Utf8,
}


def _normalize_row(row: dict, schema: dict) -> dict:
    out = {}
    for k, tp in schema.items():
        v = row.get(k)
        if tp == pl.Utf8:
            out[k] = None if v is None else ",".join(v) if isinstance(v, list) else str(v)
        elif tp == pl.Boolean:
            out[k] = bool(v) if v is not None else False
        elif tp == pl.Int64:
            out[k] = None if v is None else int(v)
        else:
            out[k] = None if v is None else float(v)
    return out


def _append_parquet(path: Path, rows: list[dict], schema: str = "daily") -> None:
    sch = _DAILY_SCHEMA if schema == "daily" else _TICKET_SCHEMA
    new = pl.DataFrame([_normalize_row(r, sch) for r in rows], schema=sch)
    if path.exists():
        old = pl.read_parquet(path)
        new = pl.concat([old, new], how="vertical_relaxed")
    tmp = path.with_suffix(".tmp")
    new.write_parquet(tmp)
    os.replace(tmp, path)


def _flush_parts(part_daily: Path, part_tick: Path, buf_daily: dict, buf_tickets: dict) -> None:
    for m, rows in buf_daily.items():
        if rows:
            _append_parquet(part_daily / f"{m}.parquet", rows, "daily")
            buf_daily[m] = []
    for m, rows in buf_tickets.items():
        if rows:
            _append_parquet(part_tick / f"{m}.parquet", rows, "tickets")
            buf_tickets[m] = []


def merge_parts(run_dir: Path, kind: str) -> pl.DataFrame:
    """Concatenate month parts into ``<run_dir>/<kind>.parquet``."""
    d = run_dir / "parts" / kind
    files = sorted(d.glob("*.parquet")) if d.exists() else []
    if not files:
        df = pl.DataFrame(schema=_DAILY_SCHEMA if kind == "daily" else _TICKET_SCHEMA)
    else:
        df = pl.concat([pl.read_parquet(f) for f in files], how="vertical_relaxed")
        if kind == "daily":
            df = df.sort("date")
    out = run_dir / f"{kind}.parquet"
    tmp = out.with_suffix(".tmp")
    df.write_parquet(tmp)
    os.replace(tmp, out)
    return df


# --------------------------------------------------------------------------- #
# Canaries (§11)
# --------------------------------------------------------------------------- #


def _canary_sample_days(days: list[str], step: int = 35) -> list[str]:
    return days[::step]


def _recompute_mfe_mae(tkb: dict, fi: int, fill: float) -> tuple[float, float, int, int]:
    hs = tkb["high"][fi:]
    ls = tkb["low"][fi:]
    im = int(np.argmax(hs))
    ia = int(np.argmin(ls))
    return (float(hs[im] / fill - 1.0), float(ls[ia] / fill - 1.0), im, ia)


def _recompute_ladder(tkb: dict, fi: int, fill: float, H: int, up: bool) -> Optional[dict]:
    n = len(tkb["et"])
    if up:
        thr = fill * (1 + H / 100.0)
        arr = tkb["high"]
        cond = arr >= thr
    else:
        thr = fill * (1 - H / 100.0)
        arr = tkb["low"]
        cond = arr <= thr
    for i in range(fi, n):
        if cond[i]:
            return {"i": i - fi, "et": int(tkb["et"][i]),
                    "touch": float(arr[i]),
                    "exec": float(tkb["open"][i + 1]) if i + 1 < n else None}
    return None


def _fill_index(tkb: dict, pop: str, T: int) -> int:
    target = 570 if pop == "A_pm" else (571 if pop == "A_open" else T)
    ets = tkb["et"]
    for i in range(len(ets)):
        if int(ets[i]) >= target:
            return i
    return -1


def run_canaries(out_dir: Optional[Path] = None, full_days: Optional[list[str]] = None) -> dict:
    """Execute all seven mandatory canaries and write JSON + MD reports."""
    out_dir = out_dir or SIM_CORE
    out_dir.mkdir(parents=True, exist_ok=True)
    all_days = full_days or dev_days()
    sample = _canary_sample_days(all_days)
    sem = session_end_map()
    report: dict = {"contract_version": "FROZEN-2026-09-22", "seed": BOOT_SEED}

    # ---- canary 1: fill.px == bars.open[fill.et] over all dev days -------- #
    t0 = time.time()
    mism1: list = []
    names1 = 0
    for day in all_days:
        rec = load_anatomy(day)
        bars = load_bars(day, None)
        names1 += _fill_mismatches(rec, bars, mism1)
    report["canary1_fill_open"] = {
        "days": len(all_days), "filled_names_checked": names1,
        "mismatches": len(mism1), "examples": mism1[:10],
        "seconds": round(time.time() - t0, 1),
    }

    # ---- canary 2 & 3: recompute mfe/mae and ladders --------------------- #
    t0 = time.time()
    mism2: list = []
    mism3: list = []
    n2 = 0
    n3 = 0
    for day in sample:
        rec = load_anatomy(day)
        bars = load_bars(day, None)
        for snap in rec["snapshots"]:
            for nm in snap["names"]:
                fl = nm.get("fill")
                if not fl:
                    continue
                tkb = bars.ticker(nm["ticker"])
                if tkb is None:
                    continue
                fi = _pos_of_et(tkb, int(fl["et"]))
                if fi < 0:
                    continue
                fill = float(fl["px"])
                if abs(float(tkb["open"][fi]) - fill) > 1e-9:
                    continue
                mfe, mae, im, ia = _recompute_mfe_mae(tkb, fi, fill)
                n2 += 1
                if (abs(round(mfe, 6) - (nm["mfe"] if nm["mfe"] is not None else 1e9)) > 1e-9 or
                        abs(round(mae, 6) - (nm["mae"] if nm["mae"] is not None else 1e9)) > 1e-9 or
                        (im != nm["i_mfe"]) or (ia != nm["i_mae"])):
                    mism2.append({"day": day, "ticker": nm["ticker"], "pop": snap["pop"],
                                  "mfe": [mfe, nm["mfe"]], "mae": [mae, nm["mae"]],
                                  "i": [im, nm["i_mfe"], ia, nm["i_mae"]]})
                for H in UP_LADDER:
                    got = _recompute_ladder(tkb, fi, fill, H, True)
                    ref = nm["ladders"]["up"].get(str(H))
                    n3 += 1
                    if not _ladder_eq(got, ref):
                        mism3.append({"day": day, "ticker": nm["ticker"], "pop": snap["pop"],
                                      "side": "up", "H": H, "got": got, "ref": ref})
                for L in DN_LADDER:
                    got = _recompute_ladder(tkb, fi, fill, L, False)
                    ref = nm["ladders"]["dn"].get(str(L))
                    n3 += 1
                    if not _ladder_eq(got, ref):
                        mism3.append({"day": day, "ticker": nm["ticker"], "pop": snap["pop"],
                                      "side": "dn", "L": L, "got": got, "ref": ref})
    report["canary2_mfe_mae"] = {
        "sample_days": len(sample), "names_checked": n2, "mismatches": len(mism2),
        "examples": mism2[:10], "seconds": round(time.time() - t0, 1),
        "formula": "mfe=max(high[fi:])/fill-1; mae=min(low[fi:])/fill-1; i=argmax/argmin offset from fi; "
                   "full bar array (no session filter), fi=first et>=target",
    }
    report["canary3_ladders"] = {
        "sample_days": len(sample), "cells_checked": n3, "mismatches": len(mism3),
        "examples": mism3[:10],
        "formula": "first i>=fi with high>=fill*(1+H/100) or low<=fill*(1-L/100); "
                   "touch=high/low at i; exec=open[i+1]",
    }

    # ---- canary 5: forced-flat plumbing over full span ------------------- #
    t0 = time.time()
    c5 = _canary_forced_flat(all_days, sem)
    c5["seconds"] = round(time.time() - t0, 1)
    report["canary5_forced_flat"] = c5

    # ---- canary 4: R1(-10) independent scan ------------------------------ #
    t0 = time.time()
    c4 = _canary_r1(all_days, sem)
    c4["seconds"] = round(time.time() - t0, 1)
    report["canary4_r1_scan"] = c4

    # ---- canary 6: determinism ------------------------------------------- #
    t0 = time.time()
    c6 = _canary_determinism(all_days[:60])
    c6["seconds"] = round(time.time() - t0, 1)
    report["canary6_determinism"] = c6

    # ---- canary 7: invariants over full 1066-day baseline ---------------- #
    t0 = time.time()
    c7 = _canary_full_baseline(all_days, sem)
    c7["seconds"] = round(time.time() - t0, 1)
    report["canary7_invariants"] = c7

    checks = [
        ("1", report["canary1_fill_open"]["mismatches"] == 0),
        ("2", report["canary2_mfe_mae"]["mismatches"] == 0),
        ("3", report["canary3_ladders"]["mismatches"] == 0),
        ("4", bool(c4.get("ok"))),
        ("5", bool(c5.get("ok"))),
        ("6", bool(c6.get("ok"))),
        ("7", bool(c7.get("ok"))),
    ]
    report["canary_verdicts"] = {k: ("PASS" if v else "FAIL") for k, v in checks}
    report["all_pass"] = all(v for _, v in checks)
    report["generated"] = datetime.now().isoformat(timespec="seconds")

    _atomic_write_json(out_dir / "canary_report.json", report)
    _atomic_write_text(out_dir / "canary_report.md", _canary_markdown(report))
    return report


def _ladder_eq(got: Optional[dict], ref: Optional[dict]) -> bool:
    if got is None and ref is None:
        return True
    if (got is None) != (ref is None):
        return False
    return (int(got["i"]) == int(ref["i"]) and int(got["et"]) == int(ref["et"]) and
            abs(round(got["touch"], 6) - ref["touch"]) <= 1e-9 and
            ((got["exec"] is None and ref["exec"] is None) or
             (got["exec"] is not None and ref["exec"] is not None and
              abs(round(got["exec"], 6) - ref["exec"]) <= 1e-9)))


def _canary_forced_flat(days: list[str], sem: dict[str, int]) -> dict:
    """Canary 5: all-hold baseline; exit price == open[session_end]; mean match."""
    spec = StrategySpec(family_id="canary", name="canary5_allhold",
                        entry_pop="A_pm", entry_T=570, top_n=10, n_slots=10,
                        release=[R0()])
    eng_pts: list[float] = []
    dir_pts: list[float] = []
    bad: list = []
    n_held = 0
    for day in days:
        rec = load_anatomy(day)
        se = sem.get(day, SESSION_END_NORMAL)
        sbars = load_bars(day, se)
        # independent direct scan over filled A_pm names with a session_end bar
        snap = snapshot_of(rec, "A_pm", 570)
        if snap:
            for nm in snap["names"]:
                fl = nm.get("fill")
                if not fl or fl.get("blocked"):
                    continue
                tkb = sbars.ticker(nm["ticker"])
                if tkb is None:
                    continue
                pos = _pos_of_et(tkb, se)
                if pos < 0:
                    continue
                dir_pts.append(float(tkb["open"][pos]) / float(fl["px"]) - 1.0)
        strat = Strategy(spec)
        simulate_day(strat, day, rec, sbars, se, 100.0)
        for tk in strat.closed:
            n_held += 1
            if tk.exit_reason == "FORCED_FLAT":
                tkb = sbars.ticker(tk.ticker)
                pos = _pos_of_et(tkb, se) if tkb else -1
                if pos < 0:
                    bad.append({"day": day, "ticker": tk.ticker, "err": "no_session_end_bar"})
                    continue
                if abs(tk.exit_px - float(tkb["open"][pos])) > 1e-9:
                    bad.append({"day": day, "ticker": tk.ticker, "exit_px": tk.exit_px,
                                "open_se": float(tkb["open"][pos])})
                eng_pts.append(tk.exit_px / tk.entry_px - 1.0)
    em = float(np.mean(eng_pts)) if eng_pts else None
    dm = float(np.mean(dir_pts)) if dir_pts else None
    ok = (not bad) and em is not None and dm is not None and abs(em - dm) <= 1e-12
    return {"ok": ok, "n_held_exits": n_held, "price_mismatches": len(bad),
            "engine_mean_open_se_over_fill": em, "direct_scan_mean": dm,
            "examples": bad[:10],
            "note": "eod_ret (close-based) is a documented different convention; "
                    "this canary compares open[session_end]-based exit."}


def _canary_r1(days: list[str], sem: dict[str, int], n_target: int = 20) -> dict:
    """Canary 4: engine R1(-10) exit et/px vs independent scan."""
    spec = StrategySpec(family_id="canary", name="canary4_r1",
                        entry_pop="A_pm", entry_T=570, top_n=10, n_slots=10,
                        release=[R1(-10)])
    checked = 0
    mismatches: list = []
    saw_gap = False
    saw_pending = False
    for day in days:
        rec = load_anatomy(day)
        se = sem.get(day, SESSION_END_NORMAL)
        bars = load_bars(day, se)
        strat = Strategy(spec)
        simulate_day(strat, day, rec, bars, se, 100.0)
        for tk in strat.closed:
            if tk.exit_reason is None or not tk.exit_reason.startswith("R1"):
                continue
            if checked >= n_target:
                break
            # independent scan: first bar low <= 0.9*fill after entry; exit next open min(open, level)
            tkb = bars.ticker(tk.ticker)   # session-filtered (engine substrate)
            # include a possible later session for pending? keep single-day for scan
            level = tk.entry_px * 0.90
            fi = _pos_of_et(tkb, tk.entry_et) if tkb else -1
            ref_et = ref_px = None
            pend = False
            if fi >= 0:
                n = len(tkb["et"])
                hit = -1
                for i in range(fi, n):
                    if tkb["low"][i] <= level + 1e-12:
                        hit = i
                        break
                if hit >= 0:
                    if hit + 1 < n:
                        ref_et = int(tkb["et"][hit + 1])
                        ref_px = min(float(tkb["open"][hit + 1]), level)
                        if float(tkb["open"][hit + 1]) < level:
                            saw_gap = True
                    else:
                        pend = True
            if tk.exit_et is not None and tk.exit_et <= se and ref_et is not None:
                checked += 1
                if abs(tk.exit_et - ref_et) > 0 or abs(tk.exit_px - ref_px) > 1e-9:
                    mismatches.append({"day": day, "ticker": tk.ticker,
                                       "engine": [tk.exit_et, tk.exit_px],
                                       "scan": [ref_et, ref_px]})
            elif pend:
                saw_pending = True
    # a dedicated pending/halt case (may be on any day)
    pend_case = _find_pending_case(days, sem)
    return {"ok": len(mismatches) == 0 and checked >= n_target and saw_gap and
            bool(pend_case and pend_case.get("ok")),
            "tickets_checked": checked, "mismatches": len(mismatches),
            "examples": mismatches[:10], "saw_gap_through": saw_gap,
            "pending_halt_case": pend_case,
            "note": "engine R1(-10): level=fill*0.90; exit next bar open, price=min(open,level)"}


def _find_pending_case(days: list[str], sem: dict[str, int]) -> Optional[dict]:
    """Real pending/halt carry case.

    Across 1,066 dev days R1(-10) never first-breaches exactly on a halt bar (all
    candidate tickers have bars through session end).  We therefore demonstrate the
    pending-through-halt plumbing on a *real* halt ticker-day by using the engine's
    R1 with the level pinned to the halt bar's low (so the first breach is that bar),
    then verify the engine carries the exit to the next session that has bars and
    executes it at ``min(open, level)``.
    """
    for idx, day in enumerate(days):
        se = sem.get(day, SESSION_END_NORMAL)
        rec = load_anatomy(day)
        bars = load_bars(day, se)
        for snap in rec["snapshots"]:
            if snap["pop"] != "A_pm":
                continue
            for nm in snap["names"]:
                fl = nm.get("fill")
                if not fl or fl.get("blocked"):
                    continue
                tkb = bars.ticker(nm["ticker"])
                if tkb is None:
                    continue
                n = len(tkb["et"])
                if n < 2 or int(tkb["et"][n - 1]) >= se - 1:
                    continue                       # not a halt (bars reach session end)
                fi = _pos_of_et(tkb, int(fl["et"]))
                if fi < 0 or fi >= n - 1:
                    continue
                level = float(tkb["low"][n - 1])
                if level <= 0 or np.min(tkb["low"][fi:n - 1]) <= level + 1e-12:
                    continue                       # an earlier bar would breach first
                fill = float(fl["px"])
                L_pct = (level / fill - 1.0) * 100.0
                spec = StrategySpec(family_id="canary", name="canary4_pending",
                                    entry_pop="A_pm", entry_T=570, top_n=10, n_slots=10,
                                    release=[R1(L_pct)])
                strat = Strategy(spec)
                simulate_day(strat, day, rec, bars, se, 100.0)
                carried = strat.open_tickets.get(nm["ticker"])
                if carried is None or carried.pending is None or \
                        not str(carried.pending.get("reason", "")).startswith("R1"):
                    continue
                pend_reason = carried.pending.get("reason")
                # resolve in the next session(s) that carry bars for the ticker
                resolved = None
                for j in range(idx + 1, min(idx + 61, len(days))):
                    se2 = sem.get(days[j], SESSION_END_NORMAL)
                    b2 = load_bars(days[j], se2)
                    if b2.ticker(nm["ticker"]) is None:
                        continue
                    simulate_day(strat, days[j], load_anatomy(days[j]), b2, se2, 100.0)
                    if not carried.open:
                        resolved = {"session_day": days[j], "exit_et": carried.exit_et,
                                    "exit_px": carried.exit_px}
                        break
                if resolved is None:
                    continue
                # independent check: first bar of the resolving session, min(open, level)
                b2 = load_bars(resolved["session_day"], sem.get(resolved["session_day"], 959))
                tb2 = b2.ticker(nm["ticker"])
                exp_et = int(tb2["et"][0])
                exp_px = min(float(tb2["open"][0]), level)
                ok = (int(resolved["exit_et"]) == exp_et and
                      abs(float(resolved["exit_px"]) - exp_px) <= 1e-9)
                return {"day": day, "ticker": nm["ticker"], "fill_et": int(fl["et"]),
                        "halt_bar_et": int(tkb["et"][n - 1]), "level": level,
                        "L_pct": L_pct,                         "engine_carried_pending": True,
                        "engine_pending_reason": pend_reason,
                        "resolved": resolved, "independent_exit": [exp_et, exp_px],
                        "ok": ok,
                        "note": "real halt: R1 first-breach on the halt bar -> carry to next "
                                "session, exit at min(open, level)"}
    return None


def _canary_determinism(days: list[str]) -> dict:
    spec = StrategySpec(family_id="canary", name="det", entry_pop="A_pm", entry_T=570,
                        top_n=10, n_slots=10, release=[R0()])
    shas = []
    for k in (1, 2):
        cfg = RunConfig(family_id="canary", run_id=f"det{k}", spec=spec,
                        bps_total=100.0, out_root=SIM_CORE / "canary", days=days)
        run(cfg, progress=False)
        shas.append(_sha256_bytes((cfg.run_dir / "daily.parquet").read_bytes()))
    return {"ok": shas[0] == shas[1], "sha256_run1": shas[0], "sha256_run2": shas[1],
            "days": len(days)}


def _canary_full_baseline(days: list[str], sem: dict[str, int]) -> dict:
    spec = StrategySpec(family_id="canary", name="baseline_allhold", entry_pop="A_pm",
                        entry_T=570, top_n=10, n_slots=10, release=[R0()])
    cfg = RunConfig(family_id="canary", run_id="baseline_full", spec=spec,
                    bps_total=100.0, out_root=SIM_CORE / "canary", days=days)
    summary = run(cfg, progress=True)
    return {"ok": summary["days_run"] == len(days), "days_run": summary["days_run"],
            "wall_seconds": round(summary["wall_seconds"], 1),
            "per_day_load_seconds": round(summary["per_day_load_seconds"], 4),
            "daily_sha256": summary["daily_sha256"],
            "n_entries": summary["metrics"]["n_entries"],
            "note": "invariants enforced by _check_invariants at every event; "
                    "run completed without AssertionError => cash>=0 and deployed<=C0."}


def _canary_markdown(rep: dict) -> str:
    lines = ["# BASKET-01 Phase-2 canary report",
             "",
             f"Contract: {rep['contract_version']}  |  bootstrap seed: {rep['seed']}",
             f"Generated: {rep['generated']}",
             "",
             "## Verdicts", ""]
    for k, v in rep["canary_verdicts"].items():
        lines.append(f"- Canary {k}: **{v}**")
    lines.append(f"- **All pass: {rep['all_pass']}**")
    lines += ["", "## Numbers", ""]
    c1 = rep["canary1_fill_open"]
    lines.append(f"1. fill.px==open[fill.et]: {c1['filled_names_checked']} names, "
                 f"{c1['mismatches']} mismatches, {c1['seconds']}s")
    c2 = rep["canary2_mfe_mae"]
    lines.append(f"2. MFE/MAE recompute: {c2['sample_days']} days, {c2['names_checked']} names, "
                 f"{c2['mismatches']} mismatches")
    c3 = rep["canary3_ladders"]
    lines.append(f"3. ladder first-touch: {c3['cells_checked']} cells, {c3['mismatches']} mismatches")
    c4 = rep["canary4_r1_scan"]
    lines.append(f"4. R1(-10) independent scan: {c4['tickets_checked']} tickets, "
                 f"{c4['mismatches']} mismatches, gap_through={c4['saw_gap_through']}, "
                 f"pending={bool(c4['pending_halt_case'])}")
    c5 = rep["canary5_forced_flat"]
    lines.append(f"5. forced flat: {c5['n_held_exits']} exits, {c5['price_mismatches']} mismatches, "
                 f"engine_mean={c5['engine_mean_open_se_over_fill']}, direct_mean={c5['direct_scan_mean']}")
    c6 = rep["canary6_determinism"]
    lines.append(f"6. determinism: run1={c6['sha256_run1'][:16]} run2={c6['sha256_run2'][:16]} ok={c6['ok']}")
    c7 = rep["canary7_invariants"]
    lines.append(f"7. full baseline invariants: {c7['days_run']} days, {c7['wall_seconds']}s, "
                 f"entries={c7['n_entries']}, load/day={c7['per_day_load_seconds']}s")
    lines += ["", "## Formulas matched", ""]
    lines.append(f"- canary 2: {c2['formula']}")
    lines.append(f"- canary 3: {c3['formula']}")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Self-test (synthetic fixtures)
# --------------------------------------------------------------------------- #


def _synthetic_bars(day: str, series: dict[str, list[tuple]]) -> Bars:
    rows = []
    for t, bars in series.items():
        for (et, o, h, l, c, v) in bars:
            rows.append((day, t, et, o, h, l, c, v))
    df = pl.DataFrame(rows, schema=["date", "ticker", "et", "open", "high", "low",
                                    "close", "volume"], orient="row")
    df = df.with_columns(pl.col("date").str.to_date())
    return Bars(day, df)


def self_test() -> None:
    """Unit tests for friction, precedence, invariants, carries, metrics."""
    fails = []

    def check(name, cond):
        if not cond:
            fails.append(name)
        print(("ok   " if cond else "FAIL ") + name)

    # --- friction math --- #
    spec = StrategySpec(name="fric", entry_pop="A_pm", entry_T=570, top_n=1, n_slots=1)
    strat = Strategy(spec)
    tk = Ticket(ticker="X", sleeve_day="2021-02-01", entry_et=570, entry_px=10.0,
                unit_notional=1.0)
    bps = 100.0
    side = bps / 2 / 10000
    _execute(strat, tk, "ENTER", 10.0, 570, "ENTRY", side)
    check("ENTER shares = budget/(px*(1+side))", abs(tk.shares - 1.0 / (10.0 * 1.005)) < 1e-12)
    check("ENTER cash == budget (no leverage)", abs(strat.sleeve_cash["2021-02-01"] - 0.0) < 1e-12)
    strat2 = Strategy(StrategySpec(name="fric2", entry_pop="A_pm", entry_T=570, top_n=1, n_slots=4))
    tk2 = Ticket(ticker="Y", sleeve_day="2021-02-01", entry_et=570, entry_px=10.0,
                 unit_notional=0.25)
    _execute(strat2, tk2, "ENTER", 10.0, 570, "ENTRY", side)
    _execute(strat2, tk2, "EXIT", 11.0, 600, "R1", side)
    exp = tk2.shares_entry * 11.0 * (1 - side) - 0.25
    check("EXIT proceeds = shares*px*(1-side)", abs(tk2.proceeds - tk2.shares_entry * 11.0 * (1 - side)) < 1e-12)
    check("ticket net formula", abs(tk2.net() - exp) < 1e-12)

    # --- ADD cap and funding --- #
    s3 = Strategy(StrategySpec(name="add", entry_pop="A_pm", entry_T=570, top_n=1, n_slots=1,
                               reserve_frac=1.0))
    t3 = Ticket(ticker="Z", sleeve_day="d", entry_et=570, entry_px=10.0, unit_notional=1.0)
    _execute(s3, t3, "ENTER", 10.0, 570, "E", side)
    check("sleeve cash full after entry", abs(s3.sleeve_cash["d"]) < 1e-12)
    _execute(s3, t3, "ADD", 11.0, 580, "A", side, frac=1.0)
    check("unfunded ADD skipped + flagged", "add_unfunded" in t3.flags and t3.n_adds == 0)
    s4 = Strategy(StrategySpec(name="add2", entry_pop="A_pm", entry_T=570, top_n=1, n_slots=4,
                               reserve_frac=0.5))
    t4 = Ticket(ticker="W", sleeve_day="d", entry_et=570, entry_px=10.0, unit_notional=0.125)
    _execute(s4, t4, "ENTER", 10.0, 570, "E", side)
    _execute(s4, t4, "ADD", 11.0, 580, "A", side, frac=1.0)   # +100%
    check("funded ADD applies", t4.n_adds == 1)
    _execute(s4, t4, "ADD", 12.0, 590, "A", side, frac=0.25)  # exceeds +100% cap
    check("ADD cap exceeded skipped", "add_cap_exceeded" in t4.flags and t4.n_adds == 1)
    check("deployed <= C0", s4.deployed["d"] <= C0 + 1e-12)

    # --- precedence + R1 gap-through + forced flat --- #
    series = {"AAPL": [
        (570, 10.0, 10.1, 9.95, 10.0, 100),
        (571, 10.0, 10.2, 9.90, 10.1, 100),   # low 9.90 > 9.0
        (572, 10.1, 10.3, 8.80, 9.50, 100),   # low <= 9.0 triggers R1
        (573, 8.50, 8.60, 8.40, 8.55, 100),   # gap-through: open 8.5 < level 9.0
        (574, 8.40, 8.45, 8.30, 8.42, 100),
    ]}
    bars = _synthetic_bars("2021-03-01", series)
    rec = {"date": "2021-03-01", "snapshots": [
        {"pop": "A_pm", "T": 570, "names": [
            {"ticker": "AAPL", "rank": 1, "fill": {"et": 570, "px": 10.0, "blocked": False}}]}]}
    spec = StrategySpec(name="r1", entry_pop="A_pm", entry_T=570, top_n=1, n_slots=1,
                        release=[R1(-10)])
    st = Strategy(spec)
    simulate_day(st, "2021-03-01", rec, bars, 574, 100.0)
    tk = st.closed[0]
    check("R1 exit et = next bar after trigger", tk.exit_et == 573)
    check("R1 gap-through uses min(open,level)", abs(tk.exit_px - 8.50) < 1e-12)
    check("R1 exit reason", tk.exit_reason.startswith("R1"))

    # forced flat: hold, session_end 574 -> decision at 573, exit open[574]
    spec2 = StrategySpec(name="flat", entry_pop="A_pm", entry_T=570, top_n=1, n_slots=1,
                         release=[R0()])
    st2 = Strategy(spec2)
    simulate_day(st2, "2021-03-01", rec, bars, 574, 100.0)
    tk2 = st2.closed[0]
    check("forced flat exit et == session_end", tk2.exit_et == 574)
    check("forced flat price == open[session_end]", abs(tk2.exit_px - 8.40) < 1e-12)
    check("forced flat reason", tk2.exit_reason == "FORCED_FLAT")

    # --- carry / pending through halt: ticker stops at 571, session_end 960 --- #
    series2 = {"BB": [
        (570, 10.0, 10.1, 9.95, 10.0, 100),
        (571, 10.0, 10.2, 8.90, 9.50, 100),   # triggers R1 at 571; no later bar ever
    ]}
    bars_a = _synthetic_bars("2021-03-02", series2)
    rec_a = {"date": "2021-03-02", "snapshots": [
        {"pop": "A_pm", "T": 570, "names": [
            {"ticker": "BB", "rank": 1, "fill": {"et": 570, "px": 10.0, "blocked": False}}]}]}
    spec3 = StrategySpec(name="carry", entry_pop="A_pm", entry_T=570, top_n=1, n_slots=1,
                         release=[R1(-10)])
    st3 = Strategy(spec3)
    simulate_day(st3, "2021-03-02", rec_a, bars_a, 960, 100.0)
    check("pending exit carried (not closed today)", not st3.closed and "BB" in st3.open_tickets)
    check("carry pending flagged", st3.open_tickets["BB"].pending is not None)

    # --- R3 same-bar high cannot trigger its own rule --- #
    series3 = {"CC": [
        (570, 10.0, 10.05, 9.95, 10.0, 100),
        (571, 10.0, 13.0, 10.0, 11.0, 100),   # high 13 then close 11 -> peak update after, close 11 > 0.9*13=11.7? 11<=11.7 -> would fire if peak updated first
    ]}
    bars3 = _synthetic_bars("2021-03-03", series3)
    rec3 = {"date": "2021-03-03", "snapshots": [
        {"pop": "A_pm", "T": 570, "names": [
            {"ticker": "CC", "rank": 1, "fill": {"et": 570, "px": 10.0, "blocked": False}}]}]}
    spec4 = StrategySpec(name="r3", entry_pop="A_pm", entry_T=570, top_n=1, n_slots=1,
                         release=[R3(10)])
    st4 = Strategy(spec4)
    simulate_day(st4, "2021-03-03", rec3, bars3, 572, 100.0)
    check("R3 does not trigger on the peak-setting bar itself",
          all(not a["reason"].startswith("R3") for tk in st4.closed for a in tk.actions))

    # --- metrics smoke --- #
    days = [{"date": "2021-01-04", "r_day": 0.01, "pnl": 0.01, "deployed_avg": 1.0,
             "n_actions": 1, "n_open_end": 0, "flags": []},
            {"date": "2021-01-05", "r_day": -0.02, "pnl": -0.02, "deployed_avg": 1.0,
             "n_actions": 1, "n_open_end": 0, "flags": []}]
    tkd = Ticket(ticker="A", sleeve_day="2021-01-04", entry_et=570, entry_px=10.0,
                 unit_notional=1.0, shares_entry=0.1, cash_in=1.0, proceeds=1.1,
                 open=False, mfe=0.5, exit_et=580, exit_px=11.0)
    mm = compute_metrics(days, [tkd], 2, 0, 0, 0, draws=50)
    check("metrics mean", abs(mm["mean_basket_day"] - (-0.005)) < 1e-12)
    check("metrics worst_day", abs(mm["worst_day"] - (-0.02)) < 1e-12)
    check("metrics has all fields",
          all(k in mm for k in ("days_n", "bootstrap_ci95", "path_contrib",
                                "false_release_rate", "half_release_rate", "tail_retained",
                                "turnover_annualized", "avg_deployed_capital")))
    check("metrics bootstrap reproducible",
          compute_metrics(days, [tkd], 2, 0, 0, 0, draws=50)["bootstrap_ci95"] == mm["bootstrap_ci95"])

    if fails:
        raise AssertionError(f"self-test failures: {fails}")
    print(f"\nself-test: ALL PASS ({len(fails)} failures)")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="BASKET-01 Phase-2 canonical simulator")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--canary", action="store_true")
    ap.add_argument("--build-calendar", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--merge-only", action="store_true")
    ap.add_argument("--family-id", default="sim_core")
    ap.add_argument("--run-id", default="run")
    ap.add_argument("--spec", default=None, help="JSON string or path to strategy spec")
    ap.add_argument("--strategy", default=None, help="builtin: baseline|r1demo")
    ap.add_argument("--bps", type=float, default=100.0)
    ap.add_argument("--max-days", type=int, default=None)
    ap.add_argument("--workers", type=int, default=4, help="I/O prefetch threads")
    ap.add_argument("--out-root", default=str(SIM_CORE))
    ap.add_argument("--canary-out", default=None)
    args = ap.parse_args(argv)

    if args.self_test:
        self_test()
        return 0
    if args.build_calendar:
        doc = build_session_calendar()
        print(f"calendar written: {CAL_PATH}")
        print(f"early closes: {doc['detected_early_closes']}")
        print(f"anomalies: {doc['anomalies']}")
        return 0
    if args.canary:
        rep = run_canaries(Path(args.canary_out) if args.canary_out else None)
        print(json.dumps(rep["canary_verdicts"], indent=1))
        print("all_pass =", rep["all_pass"])
        return 0 if rep["all_pass"] else 1
    if args.run or args.merge_only:
        spec_obj: dict = {}
        if args.strategy == "baseline":
            spec_obj = {"name": "baseline_allhold", "entry_pop": "A_pm", "entry_T": 570,
                        "top_n": 10, "n_slots": 10, "release": [{"rule": "R0"}],
                        "family_id": args.family_id}
        elif args.strategy == "r1demo":
            spec_obj = {"name": "r1_demo", "entry_pop": "A_pm", "entry_T": 570,
                        "top_n": 10, "n_slots": 10, "release": [{"rule": "R1", "L": -10}],
                        "family_id": args.family_id}
        elif args.spec:
            p = Path(args.spec)
            spec_obj = json.loads(p.read_text()) if p.exists() else json.loads(args.spec)
        else:
            raise SystemExit("--run requires --strategy or --spec")
        spec = StrategySpec.from_json(spec_obj)
        cfg = RunConfig(family_id=args.family_id, run_id=args.run_id, spec=spec,
                        bps_total=args.bps, out_root=Path(args.out_root),
                        max_days=args.max_days, workers=args.workers)
        cfg.run_dir.mkdir(parents=True, exist_ok=True)
        if args.merge_only:
            d = merge_parts(cfg.run_dir, "daily")
            t = merge_parts(cfg.run_dir, "tickets")
            print(f"merged: daily={d.height} tickets={t.height} -> {cfg.run_dir}")
            return 0
        summary = run(cfg)
        print(json.dumps({k: v for k, v in summary.items() if k != "metrics"}, indent=1))
        print("mean_basket_day:", summary["metrics"]["mean_basket_day"])
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
