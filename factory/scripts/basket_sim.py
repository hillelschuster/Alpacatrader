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
# Declared full-market carry substrate (2026-09-24 correction): per-day RTH bars
# for tickers under cross-session carry, certified in ``carry_bars/manifest.json``.
CARRY_BARS_ROOT = Path(os.environ.get("BASKET_CARRY_BARS_ROOT", SIP_ROOT / "carry_bars"))
CARRY_MANIFEST_NAME = "manifest.json"
CARRY_BARS_SCHEMA = ("date", "ticker", "et", "open", "high", "low", "close", "volume")

CONTRACT_VERSION = "FROZEN-2026-09-22+SUBSTRATE-CORRECTION-2026-09-24"

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
# A calendar gap larger than this between consecutive dev days is a dev-block
# boundary (2021-02..2023-12 | 2025-02..2026-05).  Carries never bridge it.
DEV_BLOCK_GAP_DAYS = 30

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
# Declared full-market carry substrate (2026-09-24 correction)
# --------------------------------------------------------------------------- #


class MissingCarrySubstrate(RuntimeError):
    """A cross-session carry needs full-market bars that are not certified.

    Raised instead of silently treating the absence of candidate bars as "no
    trade".  Produce the missing overlay entries with::

        python factory/scripts/basket_carry_bars.py --request DAY:TICKER [...]

    or enumerate every request from the candidate-bar artifact census with
    ``--scan`` (see the producer docstring).
    """

    def __init__(self, requests: list[tuple[str, str]], detail: str = ""):
        self.requests = sorted(set(requests))
        req = ", ".join(f"{d}:{t}" for d, t in self.requests) or "<none>"
        msg = (f"missing carry substrate for {len(self.requests)} (day, ticker) "
               f"request(s): {req}")
        if detail:
            msg += f" [{detail}]"
        super().__init__(msg)


class CarrySubstrate:
    """Per-day full-RTH carry overlay plus its certification manifest.

    Layout (root defaults to ``BASKET_ART_ROOT/sip/carry_bars``):

    * ``<day>.parquet`` — schema ``(date, ticker, et, open, high, low, close,
      volume)``, regular session only, sorted by ``(ticker, et)``.
    * ``manifest.json`` — ``{"contract": <contract_version>,
      "days": {day: {"session_end": int, "source": {"production": bool,
      "certification": str, "overlay_sha256": str, ...},
      "tickers": {ticker: {"status": "bars"|"no_bars"|"unavailable", ...}}}}}``.

    ``bars(day, ticker, session_end)`` returns the per-ticker numpy arrays (same
    shape as ``Bars.ticker``) for certified ``bars`` entries, ``None`` for
    certified ``no_bars`` entries, and raises :class:`MissingCarrySubstrate`
    when the day/ticker is not certified, the manifest contract does not match
    the engine contract, the recorded session end disagrees, or (in production
    mode) the entry is not production-certified / its overlay hash is missing
    or mismatched.  Absence of data is therefore never silently equivalent to
    "no trade".

    ``require_production`` (default True) is the production rule; test-only
    fixture overlays must opt out explicitly (``require_production=False``) and
    can never certify a production run.
    """

    def __init__(self, root: Optional[Path] = None, require_production: bool = True):
        self.root = Path(root) if root is not None else CARRY_BARS_ROOT
        self.require_production = bool(require_production)
        self._manifest: Optional[dict] = None
        self._day_cache: dict[str, Optional[Bars]] = {}
        self._sha_cache: dict[str, Optional[str]] = {}

    # -- loading ---------------------------------------------------------- #
    def manifest(self) -> dict:
        if self._manifest is None:
            path = self.root / CARRY_MANIFEST_NAME
            if not path.exists():
                raise MissingCarrySubstrate(
                    [], f"carry manifest missing at {path}; run the carry-bar producer")
            doc = json.loads(path.read_text())
            if self.require_production and doc.get("contract") != CONTRACT_VERSION:
                raise MissingCarrySubstrate(
                    [], f"carry manifest contract {doc.get('contract')!r} != engine "
                        f"contract {CONTRACT_VERSION!r} at {path}; regenerate the overlay")
            self._manifest = doc
        return self._manifest

    def _day_entry(self, day: str) -> Optional[dict]:
        return self.manifest().get("days", {}).get(day)

    def _reject_reason(self, day: str, entry: Optional[dict],
                       session_end: Optional[int]) -> Optional[str]:
        """Why this day's overlay may not be used, or None."""
        if entry is None:
            return "day not certified in carry manifest"
        source = entry.get("source", {})
        if self.require_production and not source.get("production", False):
            return ("overlay is not production-certified (fixture/test-only); "
                    "produce it with factory/scripts/basket_carry_bars.py --env-file ...")
        recorded_se = entry.get("session_end")
        if self.require_production and recorded_se is None:
            return "production overlay is missing its session_end in the manifest"
        if session_end is not None and recorded_se is not None and int(recorded_se) != int(session_end):
            return (f"manifest session_end {recorded_se} != run session_end {session_end}; "
                    f"overlay was produced for a different calendar")
        if self.require_production:
            missing = [key for key in ("overlay_sha256", "feed", "adjustment", "timeframe")
                       if not source.get(key)]
            if missing:
                return (f"production overlay manifest is missing {sorted(missing)}; "
                        f"regenerate it with the declared Alpaca SIP/RAW producer")
            if (source.get("feed") != "sip" or source.get("adjustment") != "raw"
                    or source.get("timeframe") != "1Min"):
                return ("production overlay source is not Alpaca SIP/RAW 1-minute "
                        f"(feed={source.get('feed')!r}, adjustment={source.get('adjustment')!r}, "
                        f"timeframe={source.get('timeframe')!r})")
        return None

    def certified(self, day: str, ticker: str,
                  session_end: Optional[int] = None) -> Optional[str]:
        """``"bars"``, ``"no_bars"``, ``"unavailable"`` or ``None`` (not usable).

        A missing/invalid manifest is reported as ``None`` here so callers can
        collect every unusable request for a day before failing; :meth:`bars`
        is the strict accessor that raises with the precise reason.
        """
        try:
            entry = self._day_entry(day)
            reason = self._reject_reason(day, entry, session_end)
        except MissingCarrySubstrate:
            return None
        if reason is not None:
            return None
        info = entry.get("tickers", {}).get(ticker)
        return None if info is None else str(info.get("status"))

    def bars(self, day: str, ticker: str,
             session_end: Optional[int] = None) -> Optional[dict[str, np.ndarray]]:
        guard_day(day)
        entry = self._day_entry(day)
        reason = self._reject_reason(day, entry, session_end)
        if reason is not None:
            raise MissingCarrySubstrate([(day, ticker)], reason)
        info = entry.get("tickers", {}).get(ticker)
        if info is None:
            raise MissingCarrySubstrate([(day, ticker)], "ticker not certified in carry manifest")
        status = str(info.get("status"))
        expected_sha = entry.get("source", {}).get("overlay_sha256")
        if self.require_production:
            # Integrity is verified BEFORE the status branch: a missing or
            # tampered day file must never be accepted as "genuine no trade".
            # One refresh is allowed because the producer may be extending the
            # overlay while a run is in flight (it rewrites the day file and then
            # republishes the manifest); a second mismatch is a hard failure.
            for attempt in (1, 2):
                actual_sha = self._day_sha(day)
                if expected_sha is not None and actual_sha == expected_sha:
                    break
                if attempt == 1:
                    self._refresh(day)
                    entry = self._day_entry(day)
                    reason = self._reject_reason(day, entry, session_end)
                    if reason is not None:
                        raise MissingCarrySubstrate([(day, ticker)], reason)
                    info = entry.get("tickers", {}).get(ticker)
                    if info is None:
                        raise MissingCarrySubstrate([(day, ticker)],
                                                    "ticker not certified in carry manifest")
                    status = str(info.get("status"))
                    expected_sha = entry.get("source", {}).get("overlay_sha256")
                    continue
                if expected_sha is None or actual_sha is None:
                    raise MissingCarrySubstrate(
                        [(day, ticker)],
                        "production overlay day file is missing (or has no recorded sha256)")
                raise MissingCarrySubstrate([(day, ticker)],
                                            "overlay parquet sha256 does not match the manifest")
        elif expected_sha and self._day_sha(day) != expected_sha:
            raise MissingCarrySubstrate([(day, ticker)],
                                        "overlay parquet sha256 does not match the manifest")
        if status == "no_bars":
            return None
        if status != "bars":
            raise MissingCarrySubstrate([(day, ticker)],
                                        f"coverage status '{status}' is not usable")
        frame = self._day_frame(day)
        if frame is None:
            raise MissingCarrySubstrate([(day, ticker)], "certified 'bars' but day file missing")
        tk = frame.ticker(ticker)
        if tk is None:
            raise MissingCarrySubstrate([(day, ticker)], "certified 'bars' but ticker rows missing")
        return tk

    def _refresh(self, day: str) -> None:
        """Drop cached manifest/day state so a rewritten overlay is re-read."""
        self._manifest = None
        self._sha_cache.pop(day, None)
        self._day_cache.pop(day, None)

    def _day_sha(self, day: str) -> Optional[str]:
        if day not in self._sha_cache:
            path = self.root / f"{day}.parquet"
            self._sha_cache[day] = (
                _sha256_bytes(path.read_bytes()) if path.exists() else None)
        return self._sha_cache[day]

    def _day_frame(self, day: str) -> Optional[Bars]:
        if day not in self._day_cache:
            path = self.root / f"{day}.parquet"
            if not path.exists():
                self._day_cache[day] = None
            else:
                df = pl.read_parquet(path, columns=list(CARRY_BARS_SCHEMA))
                df = df.filter((pl.col("et") >= FIRST_ET) &
                               (pl.col("et") <= SESSION_END_NORMAL))
                self._day_cache[day] = Bars(day, df)
        return self._day_cache[day]


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

    def signature(self) -> str:
        """Run-identity token for the resume/complete fingerprint.

        A rule whose behaviour depends on constructor parameters MUST override
        this (default: ``name``); otherwise two runs sharing a run id but
        differing in parameters share a fingerprint and the second silently
        reuses the first one's artifacts.
        """
        return self.name

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

    def signature(self) -> str:
        """Run-identity token; see ``ReleaseRule.signature``."""
        return self.name

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
# Buy-side entry hooks (EXT-2 — factory/BASKET-SIM-EXTENSIONS.md)
#
# Additive and opt-in.  With no ``entry_veto`` / ``mid_entry`` / ``reentry`` declared the
# engine never reaches this code and stays byte-identical to the EXT-1 engine.  Every new
# entry is an ordinary ``ENTER`` (slot budget, per-side friction, no leverage) whose
# decision is taken on a *completed* bar and executed at the open of that ticker's next
# bar (§3 / §7 step 0).  Nothing here may reorder, weaken or re-define an existing rule.
# --------------------------------------------------------------------------- #


#: Scalar entry-moment fields an :class:`EntryVetoRule` may read.  Every field is causal
#: at ``EntryCausal.cutoff_et``; anything else (close, mfe, ladders, day_high, ...) is not
#: handed to the rule at all.
VETO_FIELDS = ("gap_pct", "pre_run_pct", "sel", "decision_px", "prev_close", "pre_high",
               "open0930", "bar_close", "rank")

#: Reference prices an :class:`EntryTrigger` may cross, and the hook kinds allowed to use
#: them.  ``exit_px`` / ``entry_px`` exist only for a name that already traded today.
TRIGGER_REF_KINDS = {
    "px_decision": ("mid", "reentry"),
    "open0930": ("mid", "reentry"),
    "pre_high": ("mid", "reentry"),
    "prev_close": ("mid", "reentry"),
    "session_open": ("mid", "reentry"),
    "exit_px": ("reentry",),
    "entry_px": ("reentry",),
}

#: Ticket flag written on every ticket opened by an EXT-2 hook (also the day-level flag).
MID_ENTRY_FLAG = "mid_entry"
REENTRY_FLAG = "reentry"


@dataclass(frozen=True)
class EntryCausal:
    """Entry-moment causal view handed to an :class:`EntryVetoRule`.

    The engine builds this itself so a veto can never reach future tape through the raw
    anatomy record.  The entry decision is made from the anatomy snapshot plus completed
    bars with ``et <= cutoff_et``, where ``cutoff_et = fill.et - 1`` -- the contract's
    "a bar at ``et=t`` is complete at ``t+1``, and a decision from completed bars executes
    at the next open" (§3).  For ``A_pm``/``A_pm31`` (fill at ET 570) that is premarket
    state only; for ``A_open`` (fill 571) the completed 09:30 bar; for the ``B`` families
    the completed bars up to ``T-1``.

    A field the engine cannot fill causally is ``None`` -- never fabricated.  ``bar_*`` /
    ``n_bars`` describe the last completed bar at or before the cutoff (for ``A_pm`` that
    is no bar at all).
    """

    day: str
    ticker: str
    rank: int
    entry_pop: str
    entry_T: int
    cutoff_et: int
    prev_close: Optional[float] = None
    px_decision: Optional[float] = None
    open0930: Optional[float] = None
    pre_high: Optional[float] = None
    sel: Optional[float] = None
    gap_pct: Optional[float] = None          # 100 * (px_decision / prev_close - 1)
    pre_run_pct: Optional[float] = None      # 100 * (pre_high / px_decision - 1)
    bar_open: Optional[float] = None         # last completed bar at or before cutoff_et
    bar_high: Optional[float] = None
    bar_low: Optional[float] = None
    bar_close: Optional[float] = None
    n_bars: int = 0                          # completed bars for this ticker <= cutoff

    def field_value(self, field: str) -> Optional[float]:
        if field == "decision_px":
            return self.px_decision
        if field not in VETO_FIELDS:
            raise ValueError(f"unknown veto field {field!r}")
        return getattr(self, field)


class EntryVetoRule:
    """Per-name entry veto, evaluated at the entry moment.

    ``veto(ctx) -> True`` skips that name's slot for the day, before any ticket exists.
    A vetoed slot is counted (``Strategy.n_vetoed_slots``, metrics ``buy_side``), flagged
    on the day (``veto_skip``) and **never substituted** -- no backfill, exactly like a
    blocked fill.  The veto is evaluated once per candidate in the entry batch, in
    canonical rank order.
    """

    name = "V?"

    def veto(self, ctx: EntryCausal) -> bool:
        raise NotImplementedError

    def signature(self) -> str:
        """Run-identity token; MUST bind every constructor parameter.

        ``_run_fingerprint`` calls this, so a rule that returns a constant for different
        parameters would let a resumed run silently mix semantics.  Deliberately raising
        in the base class: an unbound veto must fail at run start, not pass silently.
        """
        raise NotImplementedError


class V0(EntryVetoRule):
    """Never veto (explicit no-op, so a declarative spec can be explicit)."""

    name = "V0"

    def veto(self, ctx: EntryCausal) -> bool:
        return False

    def signature(self) -> str:
        return "V0()"


class VGate(EntryVetoRule):
    """Veto when a causal entry-moment field crosses a threshold.

    ``on_missing`` decides what an unavailable field means (``None``): ``"veto"`` (the
    default, fail closed: no capital on a name the declared rule cannot evaluate) or
    ``"pass"``.  A gate on ``open0930``/``bar_close`` is therefore a *veto-everything*
    rule for ``A_pm``/``A_pm31`` (premarket cutoff has no bars) -- deliberate and loud.
    """

    def __init__(self, field: str, op: str, value: float, on_missing: str = "veto"):
        if field not in VETO_FIELDS:
            raise ValueError(f"unknown veto field {field!r}; known {VETO_FIELDS}")
        if op not in ("above", "below"):
            raise ValueError(f"veto op must be 'above' or 'below', got {op!r}")
        if on_missing not in ("veto", "pass"):
            raise ValueError(f"on_missing must be 'veto' or 'pass', got {on_missing!r}")
        self.field = str(field)
        self.op = str(op)
        self.value = float(value)
        self.on_missing = str(on_missing)
        self.name = f"VGate({self.field}{'>' if op == 'above' else '<'}{self.value:g})"

    def veto(self, ctx: EntryCausal) -> bool:
        v = ctx.field_value(self.field)
        if v is None:
            return self.on_missing == "veto"
        return v > self.value if self.op == "above" else v < self.value

    def signature(self) -> str:
        return (f"VGate(field={self.field!r},op={self.op!r},value={self.value!r},"
                f"on_missing={self.on_missing!r})")


class EntryTrigger:
    """Completed-bar trigger for a new entry (mid-session entry or re-entry).

    ``fires(close_px, ref_px)`` is called on the candidate's **own completed bar** ``et``
    with that bar's close and the declared reference price; the engine never invents a
    bar and never evaluates a trigger on a bar the ticker does not have.  ``ref`` names
    the reference-price source (see :data:`TRIGGER_REF_KINDS`); the decision executes at
    the open of that ticker's first bar strictly after ``et`` (§3).
    """

    name = "T?"
    ref: Optional[str] = None
    kind = "any"

    def fires(self, close_px: float, ref_px: float) -> bool:
        raise NotImplementedError

    def signature(self) -> str:
        """Run-identity token; MUST bind every constructor parameter (see
        :meth:`EntryVetoRule.signature`)."""
        raise NotImplementedError


class TAlways(EntryTrigger):
    """Fire on every completed bar inside the policy window (no reference price)."""

    name = "TAlways"
    ref = None

    def fires(self, close_px: float, ref_px: float) -> bool:
        return True

    def signature(self) -> str:
        return "TAlways()"


class TCross(EntryTrigger):
    """Fire at a completed bar whose close crosses a declared reference price.

    ``above``: ``close >= ref * (1 + pct/100)``; ``below``: ``close <= ...``.  ``pct`` is
    a percent of the reference price, so ``TCross("exit_px", 0.0)`` is "reclaim the exit
    price" and ``TCross("px_decision", -5.0, "below")`` is "still 5% under the decision
    print".
    """

    kind = "any"

    def __init__(self, ref: str, pct: float = 0.0, side: str = "above"):
        if ref not in TRIGGER_REF_KINDS:
            raise ValueError(f"unknown trigger ref {ref!r}; known {tuple(TRIGGER_REF_KINDS)}")
        if side not in ("above", "below"):
            raise ValueError(f"trigger side must be 'above' or 'below', got {side!r}")
        self.ref = str(ref)
        self.pct = float(pct)
        self.side = str(side)
        self.name = f"TCross({self.ref},{self.side},{self.pct:g})"

    def fires(self, close_px: float, ref_px: float) -> bool:
        thr = ref_px * (1.0 + self.pct / 100.0)
        return close_px >= thr - 1e-12 if self.side == "above" else close_px <= thr + 1e-12

    def signature(self) -> str:
        return (f"TCross(ref={self.ref!r},pct={self.pct!r},side={self.side!r})")


class BuySidePolicy:
    """Common base for the two EXT-2 opt-in entry hooks.

    ``window`` is an inclusive ``(lo, hi)`` decision-et range in ET minutes; either end
    may be ``None`` for "open at the start of the session" / "open at the engine's hard
    limit".  The engine intersects every declared window with ``[FIRST_ET,
    session_end - 2]``: a new entry is never scheduled on the last two completed session
    bars, so an execution bar can never be the forced-flat bar (or fall outside the
    session).
    """

    kind = "any"

    def __init__(self, trigger: EntryTrigger, window=None, label: str = "ENTRY",
                 allow_reserve: bool = False):
        if not isinstance(trigger, EntryTrigger):
            raise TypeError("trigger must be an EntryTrigger")
        ref = getattr(trigger, "ref", None)
        if ref is not None and self.kind not in TRIGGER_REF_KINDS[ref]:
            raise ValueError(f"{type(self).__name__} cannot use trigger ref {ref!r} "
                             f"(allowed: {TRIGGER_REF_KINDS[ref]})")
        self.trigger = trigger
        self.window = None if window is None else tuple(window)
        self.label = str(label)
        # False (default): a new entry may only use the capital of a slot that stayed in
        # cash, i.e. the sleeve's deployed cost basis may never exceed
        # ``C0 * reserve_frac``.  True: the parked reserve (``1 - reserve_frac``) is
        # deployable too, still bounded by ``C0`` and by sleeve cash.
        self.allow_reserve = bool(allow_reserve)

    def window_bounds(self, session_end: int) -> tuple[int, int]:
        hi = session_end - 2
        if self.window is None:
            return FIRST_ET, hi
        lo = FIRST_ET if self.window[0] is None else int(self.window[0])
        if len(self.window) > 1 and self.window[1] is not None:
            hi = min(hi, int(self.window[1]))
        return max(FIRST_ET, lo), hi


class MidEntryPolicy(BuySidePolicy):
    """OPT-IN mid-session entry for a slot that is still in cash.

    A candidate is a snapshot name in canonical rank order that today has **no** ticket
    and no pending entry (so: a blocked fill, a vetoed name, a name absent from the batch,
    or a name beyond the filled slots).  ``include_blocked_fills=False`` restricts the
    candidates to names the batch did not take for any other reason.  The declared
    ``trigger`` is evaluated on the candidate's own completed bar inside the window; the
    first candidate that fires is entered at the open of its next bar with the ordinary
    slot budget ``C0 * reserve_frac / n_slots``, and only when sleeve cash can fund it
    (never partially, never with leverage).
    """

    kind = "mid"

    def __init__(self, trigger: EntryTrigger, window=None, max_per_day: int = 1,
                 max_per_bar: int = 1, include_blocked_fills: bool = True,
                 label: str = "MID_ENTRY", allow_reserve: bool = False):
        super().__init__(trigger, window, label, allow_reserve)
        if int(max_per_day) < 1 or int(max_per_bar) < 1:
            raise ValueError("mid_entry max_per_day/max_per_bar must be >= 1")
        self.max_per_day = int(max_per_day)
        self.max_per_bar = int(max_per_bar)
        self.include_blocked_fills = bool(include_blocked_fills)

    def signature(self) -> str:
        return (f"MidEntryPolicy(trigger={self.trigger.signature()},window={self.window!r},"
                f"max_per_day={self.max_per_day},max_per_bar={self.max_per_bar},"
                f"include_blocked_fills={self.include_blocked_fills},"
                f"allow_reserve={self.allow_reserve},label={self.label!r})")


class ReentryPolicy(BuySidePolicy):
    """OPT-IN re-entry for a name that already exited today (same sleeve).

    Budget/cap is declared, not implicit: ``max_per_ticker`` re-entries per (day, ticker),
    ``max_per_day`` re-entries per sleeve-day, and ``max_notional_per_day_frac`` of ``C0``
    as the ceiling on notional committed by re-entries that day.  ``cooldown_bars`` bars
    must have completed since the ticket's exit.  The declared ``trigger`` is evaluated on
    the candidate's own completed bar inside the window; the entry is the ordinary slot
    budget and must be fundable from sleeve cash.  Only tickets that exited **today** and
    only the same session are eligible: re-entry never carries into a later session.
    """

    kind = "reentry"

    def __init__(self, trigger: EntryTrigger, max_per_ticker: int = 1, max_per_day: int = 1,
                 max_notional_per_day_frac: float = 1.0, cooldown_bars: int = 0,
                 max_per_bar: int = 1, window=None, label: str = "REENTRY",
                 allow_reserve: bool = False):
        super().__init__(trigger, window, label, allow_reserve)
        if int(max_per_ticker) < 1 or int(max_per_day) < 1 or int(max_per_bar) < 1:
            raise ValueError("reentry max_per_ticker/max_per_day/max_per_bar must be >= 1")
        if float(max_notional_per_day_frac) <= 0.0:
            raise ValueError("reentry max_notional_per_day_frac must be > 0")
        if int(cooldown_bars) < 0:
            raise ValueError("reentry cooldown_bars must be >= 0")
        self.max_per_ticker = int(max_per_ticker)
        self.max_per_day = int(max_per_day)
        self.max_notional_per_day_frac = float(max_notional_per_day_frac)
        self.cooldown_bars = int(cooldown_bars)
        self.max_per_bar = int(max_per_bar)

    def signature(self) -> str:
        return (f"ReentryPolicy(trigger={self.trigger.signature()},window={self.window!r},"
                f"max_per_ticker={self.max_per_ticker},max_per_day={self.max_per_day},"
                f"max_per_bar={self.max_per_bar},"
                f"max_notional_per_day_frac={self.max_notional_per_day_frac!r},"
                f"cooldown_bars={self.cooldown_bars},allow_reserve={self.allow_reserve},"
                f"label={self.label!r})")


def _veto_from_json(obj: dict) -> EntryVetoRule:
    kind = obj.get("rule", "V0")
    if kind == "V0":
        return V0()
    if kind == "VGate":
        return VGate(obj["field"], obj.get("op", "above"), obj["value"],
                     obj.get("on_missing", "veto"))
    raise ValueError(f"unknown veto rule {kind}")


def _trigger_from_json(obj: dict) -> EntryTrigger:
    kind = obj.get("rule", "TAlways")
    if kind == "TAlways":
        return TAlways()
    if kind == "TCross":
        return TCross(obj["ref"], obj.get("pct", 0.0), obj.get("side", "above"))
    raise ValueError(f"unknown entry trigger {kind}")


def _window_from_json(obj) -> Optional[tuple]:
    if obj is None:
        return None
    if not isinstance(obj, (list, tuple)) or len(obj) != 2:
        raise ValueError("window must be [lo, hi] with either end null")
    return tuple(obj)


def _canonical(obj: Any) -> Any:
    """Deterministic JSON-ready normalization for fingerprint payloads."""
    if obj is None or isinstance(obj, (str, bool, int, float)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_canonical(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted((_canonical(v) for v in obj), key=repr)
    if isinstance(obj, dict):
        return {str(k): _canonical(v)
                for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    return f"<opaque:{type(obj).__name__}>"


def _policy_signature(policy) -> Optional[Any]:
    """Fingerprint token for an EXT-1/EXT-2 policy object.

    ``signature()`` when the policy declares one; otherwise a structural token derived
    from its class, name, checkpoint ets and its own attributes (opaque attribute values
    degrade to ``"<opaque:Type>"``, which is honest but *not* binding -- a policy with
    behaviour-carrying opaque parameters must declare ``signature()``).
    """
    if policy is None:
        return None
    sig = getattr(policy, "signature", None)
    if callable(sig):
        return str(sig())
    return {
        "class": type(policy).__name__,
        "name": str(getattr(policy, "name", "")),
        "checkpoint_ets": _canonical(list(getattr(policy, "checkpoint_ets", ()) or ())),
        "attrs": _canonical({k: v for k, v in vars(policy).items()
                             if k != "checkpoint_ets"}),
    }


@dataclass
class _BuySideDay:
    """Per-session EXT-2 bookkeeping (never persisted, never read by §7 rules)."""

    day: str
    session_end: int
    budget: float
    entered: set = field(default_factory=set)          # tickers with a ticket today
    pending: list = field(default_factory=list)        # scheduled-but-unexecuted entries
    pending_notional: float = 0.0                      # cash reserved by those entries
    mid_count: int = 0
    reentry_count: int = 0
    reentry_notional: float = 0.0
    reentry_by_ticker: dict = field(default_factory=dict)
    capped: int = 0                                    # budget refusals after a firing trigger
    marked: set = field(default_factory=set)           # (hook, refusal kind) counted today

    def pending_tickers(self) -> set:
        return {p["ticker"] for p in self.pending}


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
    exit_day: Optional[str] = None  # session date of the closing EXIT (cross-session safe)
    exit_px: Optional[float] = None
    exit_reason: Optional[str] = None
    terminal_value: float = 0.0     # data-boundary/data-end mark (not a trade)
    terminal_kind: Optional[str] = None
    n_adds: int = 0
    n_reduces: int = 0
    shares_entry: float = 0.0
    # H -> session date / minute-of-day of the first bar with
    # high >= (1+H/100)*fill; (h_touch_day[H], h_touch_et[H]) is the total order.
    h_touch_et: dict = field(default_factory=dict)
    h_touch_day: dict = field(default_factory=dict)
    # Canonical snapshot rank of this ticket's entry (EXT-1, descriptive only: no
    # engine rule reads it; batch policies use it for deterministic ordering).
    entry_rank: int = -1

    def net(self) -> float:
        """Total net P&L in sleeve units (incl. open mark or boundary mark)."""
        return (self.proceeds - self.cash_in
                + (self.mark if self.open else 0.0)
                + self.terminal_value)

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
    # EXT-1 opt-in whole-sleeve capital allocator (factory/BASKET-SIM-EXTENSIONS.md).
    # None (the default) leaves the engine byte-identical to the pre-extension engine.
    batch_policy: Optional["BatchAllocationPolicy"] = None
    # EXT-2 opt-in buy side (factory/BASKET-SIM-EXTENSIONS.md).  All four default to the
    # pre-extension behaviour: no veto, no mid-session entry, no re-entry, no extra
    # fingerprint payload.
    entry_veto: list = field(default_factory=list)             # list[EntryVetoRule]
    mid_entry: Optional["MidEntryPolicy"] = None
    reentry: Optional["ReentryPolicy"] = None
    # Canonical, JSON-serializable identity a family pins into the run fingerprint for
    # behaviour that is not expressible as a declared field (e.g. the parameters of a
    # Python predicate).  MUST be deterministic across processes: no ids, no reprs of
    # objects whose repr carries an address, no timestamps.
    fingerprint_extra: dict = field(default_factory=dict)

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
        vetoes = [_veto_from_json(v) for v in obj.get("entry_veto", [])]
        mid_obj = obj.get("mid_entry")
        mid = None
        if mid_obj:
            mid = MidEntryPolicy(
                trigger=_trigger_from_json(mid_obj.get("trigger", {"rule": "TAlways"})),
                window=_window_from_json(mid_obj.get("window")),
                max_per_day=int(mid_obj.get("max_per_day", 1)),
                max_per_bar=int(mid_obj.get("max_per_bar", 1)),
                include_blocked_fills=bool(mid_obj.get("include_blocked_fills", True)),
                label=str(mid_obj.get("label", "MID_ENTRY")),
                allow_reserve=bool(mid_obj.get("allow_reserve", False)),
            )
        re_obj = obj.get("reentry")
        reentry = None
        if re_obj:
            reentry = ReentryPolicy(
                trigger=_trigger_from_json(re_obj.get("trigger", {"rule": "TAlways"})),
                max_per_ticker=int(re_obj.get("max_per_ticker", 1)),
                max_per_day=int(re_obj.get("max_per_day", 1)),
                max_per_bar=int(re_obj.get("max_per_bar", 1)),
                max_notional_per_day_frac=float(re_obj.get("max_notional_per_day_frac", 1.0)),
                cooldown_bars=int(re_obj.get("cooldown_bars", 0)),
                window=_window_from_json(re_obj.get("window")),
                label=str(re_obj.get("label", "REENTRY")),
                allow_reserve=bool(re_obj.get("allow_reserve", False)),
            )
        extra = obj.get("fingerprint_extra", {})
        if not isinstance(extra, dict):
            raise ValueError("fingerprint_extra must be a JSON object")
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
            entry_veto=vetoes,
            mid_entry=mid,
            reentry=reentry,
            fingerprint_extra=extra,
        )


class Strategy:
    """Base class: one basket-day sleeve, independent accounting.

    Families subclass and may override :meth:`on_checkpoint`, or supply
    ``spec.release`` / ``spec.scale_in`` modules.  Core engine semantics
    (the event order of contract §7) are not overridable.
    """

    def __init__(self, spec: StrategySpec):
        self.spec = spec
        # Independent ticket identity is (sleeve_day, ticker): the same ticker
        # may be held simultaneously by two basket-day sleeves (2026-09-24
        # correction).  Never key by bare ticker.
        self.open_tickets: dict[tuple[str, str], Ticket] = {}
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
        # EXT-2 buy-side counters and day flags (all inert unless a hook is declared).
        self.n_vetoed_slots = 0
        self.n_entry_unfunded = 0
        self.n_entry_unfilled = 0
        self.n_entry_capped = 0
        self.buy_side_day_flags: dict[str, set] = {}
        # EXT-1: a declared batch policy may only use the contract's golden-window
        # points.  Inert when no policy is declared.
        policy = getattr(spec, "batch_policy", None)
        if policy is not None:
            declared = tuple(getattr(policy, "checkpoint_ets", ()) or ())
            if not declared:
                raise ValueError("batch_policy must declare at least one checkpoint et")
            outside = [et for et in declared if et not in CHECKPOINTS]
            if outside:
                raise ValueError(
                    f"batch_policy checkpoint ets {outside} are outside CHECKPOINTS "
                    f"{CHECKPOINTS}")

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
# Batch checkpoint hook (EXT-1 — factory/BASKET-SIM-EXTENSIONS.md)
#
# Additive and opt-in: a strategy that declares no ``batch_policy`` never reaches this
# code, and the contract version, the §7 event order for existing strategies, and every
# engine-owned rule (ADD cap, cash/no-leverage, carries, friction) are unchanged.
# --------------------------------------------------------------------------- #


@dataclass
class BatchAllocationIntent:
    """One ``ADD`` requested by a batch checkpoint policy for one ticket.

    ``frac`` is the ADD notional as a fraction of *that ticket's* unit notional, so the
    existing ADD cap and the cash/no-leverage invariants apply unchanged.  ``reason`` is
    recorded on the pending action and on the executed action; ``alloc_id`` is the
    policy's own identity for its evidence tables.
    """

    sleeve_day: str
    ticker: str
    frac: float
    reason: str
    alloc_id: str = ""


@dataclass
class BatchCheckpointContext:
    """Read-only context handed to :meth:`BatchAllocationPolicy.plan` once per completed
    checkpoint bar, after every ticket decision for that bar."""

    day: str                       # session date of the completed bar
    et: int                        # the completed checkpoint bar (decision minute)
    session_end: int
    strategy: "Strategy"
    spec: "StrategySpec"
    rec: dict                      # anatomy record for ``day`` (canonical ranking)
    bars: "Bars"                   # candidate tape for ``day``
    side: float                    # per-side friction fraction
    # eligible: ordered by (entry_rank, ticker); open, same sleeve, no pending action,
    # and a completed bar at ``et``.
    eligible: list
    # excluded: [(Ticket, "pending_action" | "halted_at_checkpoint")].
    excluded: list


class BatchAllocationPolicy:
    """Opt-in whole-sleeve capital allocator (EXT-1).

    ``checkpoint_ets`` must be a non-empty subset of ``CHECKPOINTS``; ``plan`` is called
    only on those bars.  The engine schedules each returned intent as an ordinary pending
    action with decision et = the checkpoint bar, so it executes at the recipient's first
    later bar open and is funded, capped, carried and accounted for by the existing rules.
    """

    name = "batch_policy"
    checkpoint_ets: tuple = ()

    def plan(self, ctx: BatchCheckpointContext) -> list:
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Engine (§5-§8)
# --------------------------------------------------------------------------- #


def _track_raw_path(tk: "Ticket", tkb: Optional[dict], session_end: int, day: str) -> None:
    """Strategy-independent raw post-fill path tracking for one session.

    Updates ``mfe``/``mae`` and first-touch ``(day, et)`` for +30/+50/+100/+200
    over the ticket's full session tape, starting at the entry bar's open on the
    entry day and at the session start on later (carry) days.  Runs after the
    event loop, so bars after an exit still count: the tail classes
    (``path_contrib``, ``tail_retained``, ``false_release_rate``,
    ``half_release_rate``) are properties of the path, not of the holding period.
    """
    if tkb is None:
        return
    fill = tk.entry_px
    start = int(tk.entry_et) if day == tk.sleeve_day else FIRST_ET
    ets = tkb["et"]
    first = int(np.searchsorted(ets, start, side="left"))
    for i in range(first, len(ets)):
        e = int(ets[i])
        if e > session_end:
            break
        high = float(tkb["high"][i])
        low = float(tkb["low"][i])
        if high / fill - 1.0 > tk.mfe:
            tk.mfe = high / fill - 1.0
        if low / fill - 1.0 < tk.mae:
            tk.mae = low / fill - 1.0
        for H in (30, 50, 100, 200):
            if H not in tk.h_touch_et and high >= fill * (1 + H / 100.0):
                tk.h_touch_et[H] = e
                tk.h_touch_day[H] = day


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


def _execute(strat: Strategy, tk: Ticket, action: str, px: float, et: int,
             reason: str, side: float, frac: Optional[float] = None,
             day: Optional[str] = None) -> None:
    """Apply one action.  ``day`` is the execution session date; it is recorded
    on the action/exit so chronology is a total order across sessions (a later
    session's 09:30 must never compare as "before" an earlier session's 10:00).
    """
    sd = tk.sleeve_day
    day = day or sd
    qty = 0.0
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
        qty = shares
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
        qty = shares
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
        qty = sh
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
        tk.exit_day = day
        tk.exit_px = px
        tk.exit_reason = reason
        qty = shares
    else:
        raise ValueError(f"unknown action {action}")
    tk.actions.append({"day": day, "et": et, "action": action, "px": px,
                       "reason": reason, "shares": qty, "shares_after": tk.shares})
    strat._check_invariants(tk, action)
    if not tk.open:
        strat.closed.append(tk)
        strat.open_tickets.pop((tk.sleeve_day, tk.ticker), None)


def _schedule(strat: Strategy, tk: Ticket, action: str, reason: str,
              decision_et: int, level: Optional[float], frac: Optional[float] = None,
              decision_day: Optional[str] = None) -> None:
    tk.pending = {"action": action, "reason": reason, "after_et": decision_et,
                  "decision_day": decision_day or tk.sleeve_day,
                  "level": level, "frac": frac, "carry": False}


def _run_batch_checkpoint(strat: Strategy, day: str, et: int, session_end: int,
                          rec: dict, bars: Bars, side: float,
                          eligible: list, excluded: list) -> int:
    """EXT-1 call site: ask the declared policy for ADD intents and schedule them.

    Called after every ticket decision for the completed bar ``et`` and before that bar's
    §7 step-4 state updates.  Each intent becomes an ordinary pending action with
    ``decision_et = et``, so execution happens at the recipient's first later bar open
    (never same-bar) and the engine's cap/cash/carry rules decide funding.  Returns the
    number of scheduled intents; a strategy with no declared policy never reaches the
    body.
    """
    policy = strat.spec.batch_policy
    if policy is None:
        return 0
    if et not in tuple(getattr(policy, "checkpoint_ets", ()) or ()):
        return 0
    ctx = BatchCheckpointContext(
        day=day, et=et, session_end=session_end, strategy=strat, spec=strat.spec,
        rec=rec, bars=bars, side=side,
        eligible=list(eligible), excluded=list(excluded),
    )
    scheduled = 0
    for intent in policy.plan(ctx) or ():
        tk = strat.open_tickets.get((intent.sleeve_day, intent.ticker))
        if tk is None or not tk.open or tk.pending is not None:
            continue                      # a pending action is never overwritten
        frac = float(intent.frac)
        if frac <= 0.0:
            continue
        _schedule(strat, tk, "ADD", intent.reason, et, None, frac=frac, decision_day=day)
        scheduled += 1
    return scheduled


# --------------------------------------------------------------------------- #
# Buy-side call sites (EXT-2 — factory/BASKET-SIM-EXTENSIONS.md)
#
# Reached only from ``simulate_day`` and only when a hook is declared; see the EXT-2
# section header for the semantics and ``factory/BASKET-SIM-EXTENSIONS.md`` for the
# declared contract.
# --------------------------------------------------------------------------- #


def _entry_causal(day: str, snap: dict, nm: dict, bars: Bars, fill_et: int) -> EntryCausal:
    """Entry-moment causal view for one batch candidate.

    ``cutoff_et = fill_et - 1`` is the last minute that is complete before the entry
    fill's own bar (§3).  ``open0930`` and the bar fields are filled only when the
    relevant bar is complete at that cutoff; anything else stays ``None``.
    """
    cutoff = int(fill_et) - 1
    tkb = bars.ticker(nm["ticker"])
    bar = None
    n_bars = 0
    if tkb is not None and len(tkb["et"]):
        pos = int(np.searchsorted(tkb["et"], cutoff, side="right")) - 1
        n_bars = pos + 1
        if pos >= 0:
            bar = _bar(tkb, pos)
    prev_close = nm.get("prev_close")
    px_dec = nm.get("px_decision")
    pre_high = nm.get("pre_high")
    sel = nm.get("sel")
    open0930 = nm.get("open0930") if cutoff >= FIRST_ET else None
    gap_pct = (100.0 * (float(px_dec) / float(prev_close) - 1.0)
               if (px_dec and prev_close and float(prev_close) > 0) else None)
    pre_run_pct = (100.0 * (float(pre_high) / float(px_dec) - 1.0)
                   if (pre_high is not None and px_dec) else None)
    return EntryCausal(
        day=day, ticker=str(nm["ticker"]), rank=int(nm.get("rank", -1)),
        entry_pop=str(snap["pop"]), entry_T=int(snap["T"]), cutoff_et=cutoff,
        prev_close=None if prev_close is None else float(prev_close),
        px_decision=None if px_dec is None else float(px_dec),
        open0930=None if open0930 is None else float(open0930),
        pre_high=None if pre_high is None else float(pre_high),
        sel=None if sel is None else float(sel),
        gap_pct=gap_pct, pre_run_pct=pre_run_pct,
        bar_open=None if bar is None else bar["open"],
        bar_high=None if bar is None else bar["high"],
        bar_low=None if bar is None else bar["low"],
        bar_close=None if bar is None else bar["close"],
        n_bars=n_bars,
    )


def _trigger_ref_px(ref: Optional[str], nm: Optional[dict], tk: Optional[Ticket],
                    tkb: Optional[dict], cutoff_et: int) -> Optional[float]:
    """Resolve a declared trigger reference price causally, or ``None``.

    ``nm`` is the candidate's anatomy name record (premarket fields), ``tk`` the closed
    ticket for a re-entry candidate.  A reference that is not knowable at ``cutoff_et``
    returns ``None`` and the trigger never fires on it -- the engine never substitutes a
    different price.
    """
    if ref is None:
        return 0.0                                  # TAlways needs no reference
    if ref in ("px_decision", "open0930", "pre_high", "prev_close"):
        if nm is None:
            return None
        if ref == "open0930" and cutoff_et < FIRST_ET:
            return None
        v = nm.get(ref)
        return None if v is None else float(v)
    if ref == "session_open":
        if tkb is None or not len(tkb["et"]) or int(tkb["et"][0]) > cutoff_et:
            return None
        return float(tkb["open"][0])
    if ref == "exit_px":
        return None if (tk is None or tk.exit_px is None) else float(tk.exit_px)
    if ref == "entry_px":
        return None if tk is None else float(tk.entry_px)
    raise ValueError(f"unknown trigger ref {ref!r}")


def _slot_budget_free(strat: Strategy, st: _BuySideDay, allow_reserve: bool) -> bool:
    """Can this sleeve fund one more full slot budget right now?

    ``cash >= budget`` (net of entries already scheduled but not executed) **and** the
    deployed cost basis stays inside its limit: ``C0 * reserve_frac`` by default (a new
    entry may only use the capital of a slot that stayed in cash), or ``C0`` when the
    policy explicitly allows the parked reserve to be deployed.  Never partial.
    """
    b = st.budget
    limit = C0 if allow_reserve else C0 * strat.spec.reserve_frac
    return (strat._cash(st.day) - st.pending_notional >= b - 1e-12
            and strat._deployed(st.day) + st.pending_notional + b <= limit + 1e-12)


def _note_buy_side_refusal(strat: Strategy, st: _BuySideDay, hook: str, kind: str) -> None:
    """Record a refusal once per (day, hook, kind).

    A firing trigger that cannot act is counted on the day it happened, not once per
    scanned bar: the counters in metrics ``buy_side`` are *days* on which a policy wanted
    in and was refused, and the day flag carries the same information in ``daily.parquet``.
    """
    key = (hook, kind)
    if key in st.marked:
        return
    st.marked.add(key)
    strat.buy_side_day_flags.setdefault(st.day, set()).add(f"entry_{kind}")
    if kind == "unfunded":
        strat.n_entry_unfunded += 1
    elif kind == "capped":
        st.capped += 1


def _push_pending_entry(strat: Strategy, st: _BuySideDay, ticker: str, rank: int,
                        et: int, flag: str, reason: str) -> None:
    st.pending.append({"ticker": str(ticker), "after_et": int(et), "rank": int(rank),
                       "budget": float(st.budget), "flag": flag, "reason": reason})
    st.pending_notional += st.budget


def _drop_pending_entry(strat: Strategy, st: _BuySideDay, pe: dict, why: str) -> None:
    """Cancel a scheduled-but-unexecuted new entry.

    A new entry is a same-session intent: unlike a pending *exit* (§6) it is never carried
    into a later session, because the entry decision belongs to the day's selection and a
    carried entry would not be a decision at all.  Nothing was committed (no cash moved,
    no ticket existed), so the slot simply stays in cash.
    """
    st.pending.remove(pe)
    st.pending_notional -= pe["budget"]
    strat.n_entry_unfilled += 1
    strat.buy_side_day_flags.setdefault(st.day, set()).add(f"entry_unfilled_{why}")


def _buy_side_decide(strat: Strategy, st: _BuySideDay, et: int, snap: Optional[dict],
                     bars: Bars) -> int:
    """EXT-2 decision pass on the completed bar ``et``: schedule new ENTERs.

    Order inside one bar: mid-session candidates in canonical snapshot rank order, then
    re-entry candidates in ``(exit_et, ticker)`` order, each policy capped by
    ``max_per_bar``.  A window is intersected with the engine's hard limit
    ``[FIRST_ET, session_end - 2]``, so an execution bar can never be the forced-flat bar.
    Returns the number of scheduled entries.
    """
    spec = strat.spec
    mid = spec.mid_entry
    re_entry = spec.reentry
    if mid is None and re_entry is None:
        return 0
    scheduled = 0
    nm_by_ticker = {nm["ticker"]: nm for nm in (snap or {}).get("names", [])}

    if mid is not None and st.mid_count < mid.max_per_day:
        lo, hi = mid.window_bounds(st.session_end)
        taken = 0
        if lo <= et <= hi:
            for rank_index, nm in enumerate((snap or {}).get("names", [])[: spec.top_n]):
                if taken >= mid.max_per_bar:
                    break
                t = nm["ticker"]
                if (st.day, t) in strat.open_tickets or t in st.entered:
                    continue
                if t in st.pending_tickers():
                    continue
                fl = nm.get("fill")
                if (not fl or fl.get("blocked")) and not mid.include_blocked_fills:
                    continue
                tkb = bars.ticker(t)
                pos = _pos_of_et(tkb, et) if tkb is not None else -1
                if pos < 0:
                    continue
                ref = _trigger_ref_px(mid.trigger.ref, nm, None, tkb, et)
                if ref is None or not mid.trigger.fires(float(tkb["close"][pos]), ref):
                    continue
                if not _slot_budget_free(strat, st, mid.allow_reserve):
                    _note_buy_side_refusal(strat, st, "mid_entry", "unfunded")
                    break
                _push_pending_entry(strat, st, t, int(nm.get("rank", rank_index)), et,
                                    MID_ENTRY_FLAG,
                                    f"{mid.label}:{mid.trigger.signature()}")
                st.mid_count += 1
                taken += 1
                scheduled += 1

    if re_entry is not None and st.reentry_count < re_entry.max_per_day:
        lo, hi = re_entry.window_bounds(st.session_end)
        taken = 0
        if lo <= et <= hi:
            closed_today = sorted(
                (c for c in strat.closed
                 if c.sleeve_day == st.day and c.exit_et is not None),
                key=lambda c: (int(c.exit_et), c.ticker))
            for tk in closed_today:
                if taken >= re_entry.max_per_bar:
                    break
                t = tk.ticker
                if (st.day, t) in strat.open_tickets or t in st.pending_tickers():
                    continue
                if et - int(tk.exit_et) < re_entry.cooldown_bars:
                    continue                      # timing gate: resolves itself
                nm = nm_by_ticker.get(t)
                tkb = bars.ticker(t)
                pos = _pos_of_et(tkb, et) if tkb is not None else -1
                if pos < 0:
                    continue
                ref = _trigger_ref_px(re_entry.trigger.ref, nm, tk, tkb, et)
                if ref is None or not re_entry.trigger.fires(float(tkb["close"][pos]), ref):
                    continue
                # The name wanted to come back: a declared budget refusing it is
                # recorded, so a policy read never confuses "the cap said no" with
                # "the rule never fired".
                if (st.reentry_by_ticker.get(t, 0) >= re_entry.max_per_ticker
                        or st.reentry_notional + st.budget > (re_entry.max_notional_per_day_frac
                                                              * C0 + 1e-12)):
                    _note_buy_side_refusal(strat, st, "reentry", "capped")
                    continue
                if not _slot_budget_free(strat, st, re_entry.allow_reserve):
                    _note_buy_side_refusal(strat, st, "reentry", "unfunded")
                    break
                _push_pending_entry(strat, st, t, int(tk.entry_rank), et, REENTRY_FLAG,
                                    f"{re_entry.label}:{re_entry.trigger.signature()}")
                st.reentry_count += 1
                st.reentry_notional += st.budget
                st.reentry_by_ticker[t] = st.reentry_by_ticker.get(t, 0) + 1
                taken += 1
                scheduled += 1

    return scheduled


def _apply_pending_entries(strat: Strategy, st: _BuySideDay, bar_et: int, bars: Bars,
                           side: float, tapes: dict, active: list, exec_order: list,
                           actions_today: list) -> float:
    """EXT-2 execution pass for one bar: apply every scheduled entry whose execution bar
    has arrived.

    Runs after the bar's ordinary pending executions (so capital released on this bar can
    fund an entry) and before the bar's decision pass, so the new ticket is a normal open
    ticket from its entry bar onwards.  The execution price is the open of the ticker's
    first bar strictly after the decision minute (§3), recorded at that minute.  A declared
    hook puts the day's candidate bars on the event grid, so the execution bar is itself a
    scheduled minute and this resolves exactly; the ``>=`` test below is a robustness net
    for a caller that hands the engine a custom bar set.  Returns the cashflow delta of the
    entries applied here.
    """
    if not st.pending:
        return 0.0
    delta = 0.0
    for pe in list(st.pending):
        t = pe["ticker"]
        tkb = bars.ticker(t)
        pos = -1
        if tkb is not None and len(tkb["et"]):
            p = int(np.searchsorted(tkb["et"], pe["after_et"], side="right"))
            pos = p if p < len(tkb["et"]) else -1
        if pos < 0:
            _drop_pending_entry(strat, st, pe, "no_later_bar")
            continue
        exec_et = int(tkb["et"][pos])
        if exec_et > st.session_end - 1:
            _drop_pending_entry(strat, st, pe, "too_late")
            continue
        if exec_et > bar_et:
            continue
        px = float(tkb["open"][pos])
        tk = Ticket(ticker=t, sleeve_day=st.day, entry_et=exec_et, entry_px=px,
                    unit_notional=pe["budget"], entry_rank=pe["rank"])
        tk.flags.append(pe["flag"])
        strat.open_tickets[(st.day, t)] = tk
        cash_before = strat._cash(st.day)
        _execute(strat, tk, "ENTER", px, exec_et, pe["reason"], side, day=st.day)
        delta += strat._cash(st.day) - cash_before
        st.pending.remove(pe)
        st.pending_notional -= pe["budget"]
        st.entered.add(t)
        active.append(tk)
        tapes[(st.day, t)] = tkb
        exec_order.append(tk)
        exec_order.sort(key=lambda x: (x.ticker, x.sleeve_day))
        actions_today.append({"day": st.day, "et": exec_et, "ticker": t,
                              "action": "ENTER", "px": px, "reason": pe["reason"]})
    return delta


def simulate_day(strat: Strategy, day: str, rec: dict, bars: Bars,
                 session_end: int, bps_total: float,
                 do_entries: bool = True,
                 carry: Optional["CarrySubstrate"] = None) -> dict:
    """Run one strategy on one day.  Mutates strategy state; returns day row."""
    side = bps_total / 2.0 / 10000.0
    snap = snapshot_of(rec, strat.spec.entry_pop, strat.spec.entry_T)
    bs = _BuySideDay(day=day, session_end=int(session_end),
                     budget=C0 * strat.spec.reserve_frac / max(1, strat.spec.n_slots))
    # --- 1. entries ------------------------------------------------------ #
    if do_entries:
        if snap is not None:
            N = max(1, strat.spec.n_slots)
            budget = C0 * strat.spec.reserve_frac / N
            seen: set[str] = set()
            n_new = 0
            veto_rules = strat.spec.entry_veto
            for rank_index, nm in enumerate(snap["names"][: strat.spec.top_n]):
                t = nm["ticker"]
                if t in seen:
                    continue
                seen.add(t)
                fl = nm.get("fill")
                if not fl or fl.get("blocked"):
                    strat.n_blocked_slots += 1
                    continue
                key = (day, t)
                if key in strat.open_tickets:
                    continue
                # EXT-2 entry veto: per-name, evaluated on causal entry-moment state,
                # before any ticket exists.  Skipped in favour of the extension being
                # declared at all -- the default engine never builds this view.
                if veto_rules:
                    ctx = _entry_causal(day, snap, nm, bars, int(fl["et"]))
                    if any(r.veto(ctx) for r in veto_rules):
                        strat.n_vetoed_slots += 1
                        strat.buy_side_day_flags.setdefault(day, set()).add("veto_skip")
                        continue
                tk = Ticket(ticker=t, sleeve_day=day, entry_et=int(fl["et"]),
                            entry_px=float(fl["px"]), unit_notional=budget,
                            entry_rank=int(nm.get("rank", rank_index)))
                tk.pending = {"action": "ENTER", "reason": "ENTRY", "after_et": int(fl["et"]) - 1,
                              "decision_day": day, "level": None, "frac": None, "carry": False}
                strat._cash(day)
                strat.open_tickets[key] = tk
                bs.entered.add(t)
                n_new += 1
            if n_new:
                strat.filled_days += 1

    # --- active tickets: carries inherit their pending ------------------- #
    active = list(strat.open_tickets.values())

    # --- tapes: candidate bars are canonical; the carry substrate covers
    #     later sessions where the ticker is not a candidate.  Collect every
    #     uncertified/unavailable carry (day, ticker) before failing, so the
    #     producer can be run once per day with the full request list. ------ #
    tapes: dict[tuple[str, str], Optional[dict]] = {}
    uncertified: list[tuple[str, str]] = []
    for tk in active:
        key = (tk.sleeve_day, tk.ticker)
        tkb = bars.ticker(tk.ticker)
        if tkb is None and tk.sleeve_day != day:
            status = None if carry is None else carry.certified(day, tk.ticker, session_end)
            if status not in ("bars", "no_bars"):
                uncertified.append((day, tk.ticker))
            else:
                tkb = carry.bars(day, tk.ticker, session_end)   # None == certified no_bars
                if tkb is None:
                    tk.flags.append("no_resumption")
        tapes[key] = tkb
    if uncertified:
        detail = f"day {day}"
        if carry is not None:
            detail += f"; substrate root {carry.root}"
        raise MissingCarrySubstrate(uncertified, detail)

    # --- bar set: union of active ticket ets within session + session_end-1 #
    et_set: set[int] = set()
    for tk in active:
        tkb = tapes[(tk.sleeve_day, tk.ticker)]
        if tkb is None:
            continue
        for e in tkb["et"]:
            e = int(e)
            if e <= session_end:
                et_set.add(e)
    et_set.add(session_end - 1)
    et_set.add(session_end)
    # EXT-2: a declared buy-side hook needs the day's *candidate* bars on the event grid,
    # otherwise its trigger minute might not be a scheduled minute and a slot whose entry
    # family filled nothing would have no grid at all.  Inert without a hook, and the
    # extension is opt-in, so no existing run's grid (hence ``deployed_avg``) moves.
    if do_entries and snap is not None and (strat.spec.mid_entry is not None
                                            or strat.spec.reentry is not None):
        for nm in snap["names"][: strat.spec.top_n]:
            ctkb = bars.ticker(nm["ticker"])
            if ctkb is None:
                continue
            for e in ctkb["et"]:
                e = int(e)
                if e <= session_end:
                    et_set.add(e)
    ordered = sorted(e for e in et_set if e >= FIRST_ET)

    day_cashflow = 0.0
    actions_today: list[dict] = []
    deployed_sum = 0.0
    deployed_bars = 0

    # Contract §7 cross-ticket order: same-et executions apply in ticker order
    # (one pending per ticket, so ticker asc is the complete tie-break).  This
    # fixes the cash-allocation order when two same-sleeve tickets execute on
    # the same bar; insertion order was rank order, not ticker order.
    exec_order = sorted(active, key=lambda tk: (tk.ticker, tk.sleeve_day))

    for i_t, t in enumerate(ordered):
        # -- A. execute pendings scheduled for this et --------------------- #
        for tk in exec_order:
            if not tk.open:
                continue
            pend = tk.pending
            if pend is None:
                continue
            tkb = tapes[(tk.sleeve_day, tk.ticker)]
            pos = _pos_of_et(tkb, t) if tkb is not None else -1
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
                         frac=pend.get("frac"), day=day)
                cash_after = strat._cash(tk.sleeve_day)
                day_cashflow += cash_after - cash_before
                actions_today.append({"day": day, "et": t, "ticker": tk.ticker,
                                      "action": action, "px": px,
                                      "reason": pend.get("reason", action)})
                if not tk.open:
                    continue
                if t == session_end:
                    # The pending action executed on the forced-flat execution
                    # bar and left shares open (e.g. a staged REDUCE).  The
                    # position must still be flat, so carry a forced EXIT: the
                    # EOD block marks it carry=True and it executes at the next
                    # session's first executable bar.
                    _schedule(strat, tk, "EXIT", "FORCED_FLAT", session_end, None,
                              decision_day=day)

        # -- A2. EXT-2 new entries whose execution bar has arrived ----------- #
        #     After the bar's ordinary pending executions, so capital released on
        #     this bar can fund them; inert without a declared buy-side hook.
        if do_entries and bs.pending:
            day_cashflow += _apply_pending_entries(
                strat, bs, t, bars, side, tapes, active, exec_order, actions_today)

        # -- B. decisions on the completed bar t ---------------------------- #
        # EXT-1: when a batch policy declares this bar, collect the sleeve's decision
        # outcome so the policy can run between the decisions and the state updates.
        policy = strat.spec.batch_policy
        checkpoint_bar = (policy is not None and t in CHECKPOINTS
                          and t in tuple(getattr(policy, "checkpoint_ets", ()) or ()))
        eligible: list = []
        excluded: list = []
        deferred: list = []
        for tk in list(active):
            if not tk.open or tk.pending is not None:
                if checkpoint_bar and tk.open and tk.sleeve_day == day:
                    excluded.append((tk, "pending_action"))
                continue
            tkb = tapes[(tk.sleeve_day, tk.ticker)]
            pos = _pos_of_et(tkb, t) if tkb is not None else -1
            bar = _bar(tkb, pos) if pos >= 0 else None

            # 1. forced flat: a market-clock decision at the last completed
            #    session bar; applies to every open ticket (halted names carry).
            if t == session_end - 1:
                _schedule(strat, tk, "EXIT", "FORCED_FLAT", t, None, decision_day=day)
                continue

            if bar is None:
                if checkpoint_bar and tk.sleeve_day == day:
                    excluded.append((tk, "halted_at_checkpoint"))
                continue

            # 2. release modules (first firing consumes the bar)
            fired = False
            for rule in strat.spec.release:
                act = rule.evaluate(tk, bar, pos)
                if act is not None:
                    if act["action"] == "EXIT":
                        _schedule(strat, tk, "EXIT", act.get("reason", rule.name), t,
                                  act.get("level"), decision_day=day)
                    else:  # REDUCE scale-out variant
                        _schedule(strat, tk, "REDUCE", act.get("reason", rule.name), t,
                                  act.get("level"), frac=act.get("frac", 0.5),
                                  decision_day=day)
                    fired = True
                    break

            # 3. scale-in modules (only if nothing released)
            if not fired:
                for sc in strat.spec.scale_in:
                    act = sc.evaluate(tk, bar, pos)
                    if act is not None:
                        _schedule(strat, tk, "ADD", act.get("reason", sc.name), t,
                                  None, frac=act.get("frac"), decision_day=day)
                        break

            # 3b. golden-window checkpoint hook
            if t in CHECKPOINTS:
                strat.on_checkpoint(t, tk, bar)

            if checkpoint_bar and tk.sleeve_day == day:
                # a release/reduce/scale-in scheduled on the checkpoint bar itself
                # occupies that bar: it is a pending action and cannot also receive
                # reserve capital (pending actions are never overwritten).
                if tk.pending is not None:
                    excluded.append((tk, "pending_action"))
                else:
                    eligible.append(tk)
            deferred.append((tk, bar))

        # 3c. batch checkpoint hook (EXT-1): after every decision for this completed
        #     bar, before this bar's state updates.  Inert without a declared policy.
        if checkpoint_bar:
            eligible.sort(key=lambda item: (item.entry_rank, item.ticker))
            _run_batch_checkpoint(strat, day, t, session_end, rec, bars, side,
                                  eligible, excluded)

        # 3d. buy-side entry hooks (EXT-2): mid-session entry / re-entry decisions on
        #     this completed bar.  Inert without a declared hook.  Scheduling only --
        #     execution happens in step A of the bar the entry's own price belongs to.
        if do_entries:
            _buy_side_decide(strat, bs, t, snap, bars)

        # 4. state updates AFTER checks
        for tk, bar in deferred:
            if bar["high"] > tk.peak:
                tk.peak = bar["high"]
            tk.last_close = bar["close"]
            tk.last_et = t

        # -- deployed time-weight (minutes) -------------------------------- #
        dep = sum(tk.cost_open for tk in active if tk.open)
        nxt = ordered[i_t + 1] if i_t + 1 < len(ordered) else session_end
        span = max(0, nxt - t)
        deployed_sum += dep * span
        deployed_bars += span

    # --- EXT-2: a new entry is a same-session intent; nothing carries ------- #
    # (Only reachable when a hook is declared: without one ``bs.pending`` is empty.)
    for pe in list(bs.pending):
        _drop_pending_entry(strat, bs, pe, "session_end")
    strat.n_entry_capped += bs.capped

    # --- strategy-independent raw path tracking (C1 implementation fix) ---- #
    # MFE/MAE and first-touch of +30/+50/+100/+200 are properties of the
    # ticker's post-fill session path, not of the position's lifetime: a ticket
    # released at 10:00 must keep accruing touches from the rest of the tape,
    # otherwise false_release_rate / tail cohorts are truncated at the exit bar.
    for tk in active:
        _track_raw_path(tk, tapes.get((tk.sleeve_day, tk.ticker)), session_end, day)

    # --- end of day: mark open tickets, finalize marks, carry flags ------- #
    mark_end_total = 0.0
    mark_prev_total = 0.0
    for tk in active:
        mark_prev_total += tk.mark
        if tk.open:
            tkb = tapes[(tk.sleeve_day, tk.ticker)]
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
                # The tape ended before the forced-flat decision bar (a halted
                # name still held): an involuntary carry with no pending action.
                # It is re-evaluated on its next session's tape.
                tk.flags.append("open_carry_no_pending")
                strat.n_carries += 1
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
        # current open cost basis (deployed), not lifetime cash_in: a REDUCE
        # releases capital and must lower deployed_end (2026-09-24 correction).
        "deployed_end": sum(tk.cost_open for tk in active if tk.open),
        "deployed_avg": (deployed_sum / deployed_bars) if deployed_bars else 0.0,
        "n_actions": len(actions_today),
        "actions": json.dumps(actions_today, sort_keys=True),
        "n_open_end": sum(1 for tk in active if tk.open),
        # Ticket flags plus EXT-2 day-level flags (veto skips, unfunded/unfilled new
        # entries).  The day-level set is empty unless a buy-side hook is declared.
        "flags": sorted({f for tk in active for f in tk.flags}
                        | strat.buy_side_day_flags.get(day, set())),
    }
    strat.day_rows.append(row)
    return row


# --------------------------------------------------------------------------- #
# Dev-block data boundary (C1)
# --------------------------------------------------------------------------- #


def _gap_days(day_a: str, day_b: str) -> int:
    return (_date.fromisoformat(day_b) - _date.fromisoformat(day_a)).days


def _block_end_after(days: list[str], day: str) -> bool:
    """True when ``day`` is the last dev day before a declared dev-block gap."""
    i = days.index(day)
    return i + 1 < len(days) and _gap_days(day, days[i + 1]) > DEV_BLOCK_GAP_DAYS


_DEV_DAYS_LAST: Optional[str] = None


def _dev_days_last() -> str:
    """Last canonical development day (memoized; avoids re-globbing per day)."""
    global _DEV_DAYS_LAST
    if _DEV_DAYS_LAST is None:
        _DEV_DAYS_LAST = dev_days()[-1]
    return _DEV_DAYS_LAST


def terminalize(strat: Strategy, kind: str = "BLOCK_BOUNDARY_MARK") -> list[Ticket]:
    """Data-boundary terminalization: a carry may not bridge a dev-block gap or
    outlive the end of the development data.

    The ticket's last mark becomes a *terminal value* (no friction, no trade):
    it is recognized in :meth:`Ticket.net` but is not booked as realized
    proceeds, does not count as an EXIT and does not enter turnover.  The mark
    was already included in the boundary day's P&L, so nothing is double-counted.
    """
    done: list[Ticket] = []
    for key, tk in list(strat.open_tickets.items()):
        terminal = (float(tk.mark) if tk.mark
                    else float(tk.shares) * float(tk.last_close or tk.entry_px))
        sd = tk.sleeve_day
        tk.terminal_value += terminal
        tk.terminal_kind = kind
        strat.deployed[sd] = max(0.0, strat._deployed(sd) - tk.cost_open)
        tk.cost_open = 0.0
        tk.shares = 0.0
        tk.mark = 0.0
        tk.mark_prev = 0.0
        tk.open = False
        tk.exit_day = None
        tk.exit_et = None
        tk.exit_px = tk.last_close
        tk.exit_reason = kind
        tk.flags.append("block_boundary_carry" if kind == "BLOCK_BOUNDARY_MARK"
                        else "data_end_carry")
        strat.closed.append(tk)
        del strat.open_tickets[key]
        done.append(tk)
    return done


# Backwards-compatible alias (pre-fix name used by the runner and tests).
terminalize_block = terminalize


# --------------------------------------------------------------------------- #
# Metrics (§9)
# --------------------------------------------------------------------------- #


def _pctile(a: np.ndarray, q: float) -> float:
    return float(np.percentile(a, q)) if len(a) else float("nan")


def compute_metrics(days: list[dict], tickets: list[Ticket], n_dev_days: int,
                    n_blocked_slots: int, n_pending: int, n_carries: int,
                    seed: int = BOOT_SEED, draws: int = BOOT_DRAWS,
                    buy_side: Optional[dict] = None) -> dict:
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

    # false / half release rates (2026-09-24 correction)
    # Chronology is a total order over (session_day, et): a later session's
    # 09:30 (570) must never compare as "before" an earlier session's 10:00.
    # half_release_rate[H] requires the *cumulative* REDUCE share quantity
    # executed strictly before the first H-touch bar to be >= 50% of the
    # pre-touch peak share count (entry shares plus pre-touch ADDs); a single
    # token reduction no longer counts.
    false_release, half_release = {}, {}
    for H in (30, 50, 100):
        touch = [tk for tk in tickets if H in tk.h_touch_et]
        fr = hr = None
        if touch:
            f = 0
            h = 0
            for tk in touch:
                tet = tk.h_touch_et[H]
                tday = tk.h_touch_day.get(H, tk.sleeve_day)
                # fully flat before the touch bar?
                flat_key = (tk.exit_day, tk.exit_et) if (
                    not tk.open and tk.shares == 0 and tk.exit_day is not None) else None
                if flat_key is not None and flat_key < (tday, tet):
                    f += 1
                # cumulative pre-touch reduction >= 50% of pre-touch peak shares?
                peak = tk.shares_entry
                reduced = 0.0
                for a in tk.actions:
                    if (a.get("day", tk.sleeve_day), a["et"]) >= (tday, tet):
                        continue
                    if a["action"] == "REDUCE":
                        reduced += float(a.get("shares", 0.0))
                    after = a.get("shares_after")
                    if after is not None and float(after) > peak:
                        peak = float(after)
                if peak > 0 and reduced >= 0.5 * peak - 1e-12:
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
    # An EXIT is an executed trade (exit_day set); data-boundary/data-end
    # terminal marks close a ticket without trading, so they are not exits.
    n_exits = sum(1 for tk in tickets if not tk.open and tk.exit_day is not None)
    n_terminal_marks = sum(1 for tk in tickets if tk.terminal_kind is not None)

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
        "n_terminal_marks": n_terminal_marks,
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
        # EXT-2 buy side.  Executed hook entries are identified by their ticket flag
        # (the ticket, not a counter, is the record of what happened); the strategy-side
        # numbers are decisions that produced no ticket.
        "buy_side": {
            "n_mid_entries": sum(1 for tk in tickets if MID_ENTRY_FLAG in tk.flags),
            "n_reentries": sum(1 for tk in tickets if REENTRY_FLAG in tk.flags),
            "n_vetoed_slots": int((buy_side or {}).get("n_vetoed_slots", 0)),
            "n_entry_unfunded": int((buy_side or {}).get("n_entry_unfunded", 0)),
            "n_entry_unfilled": int((buy_side or {}).get("n_entry_unfilled", 0)),
            "n_entry_capped": int((buy_side or {}).get("n_entry_capped", 0)),
        },
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
        "exit_day": tk.exit_day,
        "exit_et": tk.exit_et,
        "exit_px": tk.exit_px,
        "exit_reason": tk.exit_reason,
        "terminal_value": tk.terminal_value,
        "terminal_kind": tk.terminal_kind,
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


def _engine_hash() -> str:
    """sha256 of the engine + carry producer sources.

    Part of the run fingerprint: resuming or trusting a stored run after any
    engine-code change would silently mix semantics (the contract hash only
    covers the contract document).
    """
    h = hashlib.sha256()
    for path in (HERE, HERE.parent / "basket_carry_bars.py"):
        if path.exists():
            h.update(path.name.encode())
            h.update(path.read_bytes())
    return h.hexdigest()[:16]


def _run_fingerprint(cfg: "RunConfig", days: list[str]) -> str:
    """Contract + engine + run-identity fingerprint for resume/complete trust.

    Binds every trusted-output path to the current contract version/hash, the
    engine source and the run's identity, so a resumed or "already complete"
    run id can never silently return outputs produced under different semantics.

    Every declared policy object is bound too (EXT-1 ``batch_policy``, EXT-2
    ``entry_veto``/``mid_entry``/``reentry``): a rule whose behaviour depends on its
    parameters must expose them through ``signature()``, and the base classes of the
    EXT-2 rules raise rather than return a constant, so an unbound rule fails at run
    start instead of silently changing what a stored run id means.  ``fingerprint_extra``
    carries any identity that is not expressible as a declared field.
    """
    payload = {
        "contract_version": CONTRACT_VERSION,
        "contract_hash": _contract_hash(),
        "engine_hash": _engine_hash(),
        "family_id": cfg.family_id,
        "run_id": cfg.run_id,
        "spec": cfg.spec.name,
        "entry_pop": cfg.spec.entry_pop,
        "entry_T": cfg.spec.entry_T,
        "top_n": cfg.spec.top_n,
        "n_slots": cfg.spec.n_slots,
        "reserve_frac": cfg.spec.reserve_frac,
        "release": [r.signature() for r in cfg.spec.release],
        "scale_in": [s.signature() for s in cfg.spec.scale_in],
        "batch_policy": _policy_signature(cfg.spec.batch_policy),
        "entry_veto": [v.signature() for v in cfg.spec.entry_veto],
        "mid_entry": _policy_signature(cfg.spec.mid_entry),
        "reentry": _policy_signature(cfg.spec.reentry),
        "fingerprint_extra": _canonical(cfg.spec.fingerprint_extra),
        "bps_total": cfg.bps_total,
        "days": [days[0], days[-1], len(days)] if days else [],
    }
    return _sha256_bytes(json.dumps(payload, sort_keys=True).encode())[:16]


def run(cfg: RunConfig, progress: bool = True) -> dict:
    """Day-major run.  Writes month parts incrementally; returns summary."""
    days = cfg.days or dev_days()
    if cfg.max_days:
        days = days[: cfg.max_days]
    fingerprint = _run_fingerprint(cfg, days)
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
            raw = json.loads(progress_path.read_text())
            if (raw.get("fingerprint") != fingerprint
                    or raw.get("contract_version") != CONTRACT_VERSION):
                raise ValueError(
                    "progress was written under a different contract/run fingerprint")
            raw_done = raw.get("done")
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
                (row["sleeve_day"], row["ticker"], row["entry_et"])
                for path in part_tick.glob("*.parquet")
                for row in pl.read_parquet(
                    path, columns=["sleeve_day", "ticker", "entry_et"]).iter_rows(named=True)
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
            if (saved.get("fingerprint") != fingerprint
                    or saved.get("contract_version") != CONTRACT_VERSION):
                raise ValueError(
                    "completed summary does not match the current contract/run fingerprint")
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
    carry = CarrySubstrate()

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
        simulate_day(strat, day, rec, bars, se, cfg.bps_total, carry=carry)
        if _block_end_after(days, day):
            # state reconstruction only: the terminalized rows were already
            # flushed into the parts when this day was first completed
            terminalize_block(strat)

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
        row = simulate_day(strat, day, rec, bars, se, cfg.bps_total, carry=carry)
        buf_daily.setdefault(day[:7], []).append(row)
        if _block_end_after(days, day):
            terminalize(strat, "BLOCK_BOUNDARY_MARK")
        elif days and day == days[-1] and day == _dev_days_last():
            # The run covers the end of the development data: an unresolved
            # carry is terminally marked at its last close (not traded).
            terminalize(strat, "DATA_END_MARK")
        for tk in strat.closed[prev_closed:]:
            buf_tickets.setdefault(day[:7], []).append(_ticket_row(tk))
        n_done += 1
        done.add(day)
        if (idx + 1) % 25 == 0 or idx == len(todo) - 1:
            _flush_parts(part_daily, part_tick, buf_daily, buf_tickets)
            _atomic_write_json(progress_path, {"done": sorted(done),
                                               "fingerprint": fingerprint,
                                               "contract_version": CONTRACT_VERSION})
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
                              strat.n_carries,
                              buy_side={"n_vetoed_slots": strat.n_vetoed_slots,
                                        "n_entry_unfunded": strat.n_entry_unfunded,
                                        "n_entry_unfilled": strat.n_entry_unfilled,
                                        "n_entry_capped": strat.n_entry_capped})
    cfg_obj = {
        "family_id": cfg.family_id,
        "run_id": cfg.run_id,
        "strategy": cfg.spec.name,
        "entry_pop": cfg.spec.entry_pop,
        "entry_T": cfg.spec.entry_T,
        "top_n": cfg.spec.top_n,
        "n_slots": cfg.spec.n_slots,
        "reserve_frac": cfg.spec.reserve_frac,
        "release": [r.signature() for r in cfg.spec.release],
        "scale_in": [s.signature() for s in cfg.spec.scale_in],
        "entry_veto": [v.signature() for v in cfg.spec.entry_veto],
        "mid_entry": _policy_signature(cfg.spec.mid_entry),
        "reentry": _policy_signature(cfg.spec.reentry),
        "fingerprint_extra": _canonical(cfg.spec.fingerprint_extra),
        "fingerprint": fingerprint,
        "bps_total": cfg.bps_total,
        "days": [days[0], days[-1]] if days else [],
        "n_days": len(days),
        "seed": BOOT_SEED,
        "git_head": _git_head(),
        "sim_contract_hash": _contract_hash(),
        "engine_hash": _engine_hash(),
        "contract_version": CONTRACT_VERSION,
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
        "fingerprint": fingerprint,
        "contract_version": CONTRACT_VERSION,
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
    "n_actions": pl.Int64, "actions": pl.Utf8, "n_open_end": pl.Int64, "flags": pl.Utf8,
}
_TICKET_SCHEMA = {
    "ticker": pl.Utf8, "sleeve_day": pl.Utf8, "entry_et": pl.Int64, "entry_px": pl.Float64,
    "unit_notional": pl.Float64, "shares_entry": pl.Float64, "n_adds": pl.Int64,
    "n_reduces": pl.Int64, "exit_day": pl.Utf8, "exit_et": pl.Int64, "exit_px": pl.Float64,
    "exit_reason": pl.Utf8, "terminal_value": pl.Float64, "terminal_kind": pl.Utf8,
    "open_end": pl.Boolean, "net": pl.Float64,
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
    carry = CarrySubstrate()
    report: dict = {"contract_version": CONTRACT_VERSION, "seed": BOOT_SEED}

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
    c5 = _canary_forced_flat(all_days, sem, carry)
    c5["seconds"] = round(time.time() - t0, 1)
    report["canary5_forced_flat"] = c5

    # ---- canary 4: R1(-10) independent scan ------------------------------ #
    t0 = time.time()
    c4 = _canary_r1(all_days, sem, carry)
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


def _canary_forced_flat(days: list[str], sem: dict[str, int],
                        carry: Optional[CarrySubstrate] = None) -> dict:
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
        simulate_day(strat, day, rec, sbars, se, 100.0, carry=carry)
        for tk in strat.closed:
            if tk.exit_day != day:
                continue
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


def _canary_r1(days: list[str], sem: dict[str, int],
               carry: Optional[CarrySubstrate] = None, n_target: int = 20) -> dict:
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
        simulate_day(strat, day, rec, bars, se, 100.0, carry=carry)
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
            if tk.exit_day == day and tk.exit_et is not None and tk.exit_et <= se \
                    and ref_et is not None:
                checked += 1
                if abs(tk.exit_et - ref_et) > 0 or abs(tk.exit_px - ref_px) > 1e-9:
                    mismatches.append({"day": day, "ticker": tk.ticker,
                                       "engine": [tk.exit_et, tk.exit_px],
                                       "scan": [ref_et, ref_px]})
            elif pend:
                saw_pending = True
    # a dedicated pending/halt case (may be on any day)
    pend_case = _find_pending_case(days, sem, carry)
    return {"ok": len(mismatches) == 0 and checked >= n_target and saw_gap and
            bool(pend_case and pend_case.get("ok")),
            "tickets_checked": checked, "mismatches": len(mismatches),
            "examples": mismatches[:10], "saw_gap_through": saw_gap,
            "pending_halt_case": pend_case,
            "note": "engine R1(-10): level=fill*0.90; exit next bar open, price=min(open,level)"}


def _find_pending_case(days: list[str], sem: dict[str, int],
                       carry: Optional[CarrySubstrate] = None) -> Optional[dict]:
    """Real pending/halt carry case.

    Across 1,066 dev days R1(-10) never first-breaches exactly on a halt bar, so
    the engine's R1 level is pinned to the halt bar's low (first breach is that
    bar) on a *real* halt ticker-day.  The exit must then carry to the first
    genuinely executable later-session bar from the declared full-market carry
    substrate -- never to the next candidate-neighborhood appearance.
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
                simulate_day(strat, day, rec, bars, se, 100.0, carry=carry)
                carried = strat.open_tickets.get((day, nm["ticker"]))
                if carried is None or carried.pending is None or \
                        not str(carried.pending.get("reason", "")).startswith("R1"):
                    continue
                pend_reason = carried.pending.get("reason")
                # resolve in the first later dev session that genuinely has a bar
                resolved = None
                for j in range(idx + 1, min(idx + 250, len(days))):
                    d2 = days[j]
                    se2 = sem.get(d2, SESSION_END_NORMAL)
                    b2 = load_bars(d2, se2)
                    tb2 = b2.ticker(nm["ticker"])
                    if tb2 is None:
                        status = None if carry is None else carry.certified(d2, nm["ticker"])
                        if status == "no_bars":
                            continue               # certified: still no bars, carry on
                        if status != "bars":
                            return {"day": day, "ticker": nm["ticker"],
                                    "halt_bar_et": int(tkb["et"][n - 1]), "level": level,
                                    "ok": False,
                                    "blocked_on": {"day": d2, "ticker": nm["ticker"],
                                                   "certified_status": status},
                                    "note": "carry resolution blocked: carry substrate "
                                            "does not certify this later session"}
                    simulate_day(strat, d2, load_anatomy(d2), b2, se2, 100.0, carry=carry)
                    if not carried.open:
                        resolved = {"session_day": d2, "exit_day": carried.exit_day,
                                    "exit_et": carried.exit_et, "exit_px": carried.exit_px}
                        break
                if resolved is None:
                    continue
                # independent check: first bar of the resolving session, min(open, level)
                b2 = load_bars(resolved["session_day"], sem.get(resolved["session_day"], 959))
                tb2 = b2.ticker(nm["ticker"])
                if tb2 is None and carry is not None:
                    tb2 = carry.bars(resolved["session_day"], nm["ticker"])
                exp_et = int(tb2["et"][0])
                exp_px = min(float(tb2["open"][0]), level)
                ok = (int(resolved["exit_et"]) == exp_et and
                      abs(float(resolved["exit_px"]) - exp_px) <= 1e-9)
                return {"day": day, "ticker": nm["ticker"], "fill_et": int(fl["et"]),
                        "halt_bar_et": int(tkb["et"][n - 1]), "level": level,
                        "L_pct": L_pct, "engine_carried_pending": True,
                        "engine_pending_reason": pend_reason,
                        "resolved": resolved, "independent_exit": [exp_et, exp_px],
                        "ok": ok,
                        "note": "real halt: R1 first-breach on the halt bar -> carry to the "
                                "first later-session bar from the declared carry substrate, "
                                "exit at min(open, level)"}
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
    installed = days and (summary["daily_rows"] == len(days))
    return {"ok": bool(installed), "days_run": summary["days_run"],
            "days_span": len(days), "daily_rows": summary["daily_rows"],
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
    check("pending exit carried (not closed today)",
          not st3.closed and ("2021-03-02", "BB") in st3.open_tickets)
    check("carry pending flagged",
          st3.open_tickets[("2021-03-02", "BB")].pending is not None)
    check("executed actions carry their session date",
          all(a.get("day") == "2021-03-02" for a in st3.open_tickets[("2021-03-02", "BB")].actions))

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

    # ------------------------------------------------------------------ #
    # EXT-2 buy side: veto / mid-session entry / re-entry
    # ------------------------------------------------------------------ #
    DAY = "2021-04-01"
    SE = 580

    def _flat_bars(tkr, base=10.0, lo=9.5, hi=10.5, start=570, end=SE):
        return (tkr, [(e, base, hi, lo, base, 100) for e in range(start, end + 1)])

    def _rec(names, pop="A_pm", T=570):
        return {"date": DAY, "snapshots": [{"pop": pop, "T": T, "names": names}]}

    def _nm(t, rank, px=10.0, prev=9.5, blocked=False, et=570, extra=None):
        nm = {"ticker": t, "rank": rank, "sel": 0.05, "px_decision": px, "prev_close": prev,
              "open0930": 10.0, "pre_high": px * 1.02,
              "fill": {"et": et, "px": px, "blocked": blocked}}
        if extra:
            nm.update(extra)
        return nm

    two = ["AA", "BB"]
    flat = _synthetic_bars(DAY, {t: _flat_bars(t)[1] for t in two})
    rec2 = _rec([_nm("AA", 1), _nm("BB", 2)])
    base_spec = dict(family_id="bs", entry_pop="A_pm", entry_T=570, top_n=2, n_slots=2)

    # inertness: no hook declared -> no day flag, no extra ticket, no counter
    st_base = Strategy(StrategySpec(name="bs_base", **base_spec))
    row_base = simulate_day(st_base, DAY, rec2, flat, SE, 100.0)
    check("EXT-2 inert: two batch tickets, no buy-side flag",
          len(st_base.closed) == 2 and row_base["flags"] == []
          and st_base.n_vetoed_slots == 0 and st_base.n_entry_unfunded == 0
          and st_base.n_entry_unfilled == 0)

    # entry veto: rank gate skips the second name and nothing else moves
    st_v = Strategy(StrategySpec(name="bs_veto", entry_veto=[VGate("rank", "above", 1)],
                                 **base_spec))
    row_v = simulate_day(st_v, DAY, rec2, flat, SE, 100.0)
    aa_v = [tk for tk in st_v.closed if tk.ticker == "AA"]
    aa_b = [tk for tk in st_base.closed if tk.ticker == "AA"]
    check("EXT-2 veto skips the name (one ticket, counter, day flag)",
          len(st_v.closed) == 1 and st_v.n_vetoed_slots == 1
          and row_v["flags"] == ["veto_skip"])
    check("EXT-2 veto leaves the surviving ticket untouched",
          len(aa_v) == 1 and aa_v[0].net() == aa_b[0].net()
          and aa_v[0].exit_px == aa_b[0].exit_px)

    # veto causality: A_pm cutoff (569) has no bar and no 09:30 open; A_open (fill 571) does
    nma, nmb = _nm("AA", 1), _nm("AA", 1, et=571)
    c_pm = _entry_causal(DAY, rec2["snapshots"][0], nma, flat, 570)
    c_open = _entry_causal(DAY, rec2["snapshots"][0], nmb, flat, 571)
    check("EXT-2 veto view is causal (A_pm: no bar, no open0930)",
          c_pm.bar_close is None and c_pm.n_bars == 0 and c_pm.open0930 is None
          and c_pm.cutoff_et == 569)
    check("EXT-2 veto view fills the completed 09:30 bar for A_open",
          c_open.bar_open == 10.0 and c_open.n_bars == 1 and c_open.open0930 == 10.0
          and abs(c_open.gap_pct - (10.0 / 9.5 - 1) * 100) < 1e-9)
    check("EXT-2 veto fails closed on a missing field by default",
          VGate("open0930", "above", 0.0).veto(c_pm)
          and not VGate("open0930", "above", 0.0, on_missing="pass").veto(c_pm))

    # mid-session entry for a blocked slot, on the candidate's own completed bar
    bb_bars = [(570, 10.0, 10.1, 9.7, 9.8, 100),
               (575, 9.9, 10.4, 9.8, 10.3, 100),
               (576, 10.1, 10.2, 10.0, 10.1, 100),
               (577, 9.9, 10.0, 9.8, 9.9, 100),
               (578, 9.9, 10.0, 9.8, 9.9, 100),
               (579, 9.9, 10.0, 9.8, 9.9, 100),
               (580, 9.8, 9.9, 9.7, 9.8, 100)]
    bars_mid = _synthetic_bars(DAY, {**{t: _flat_bars(t)[1] for t in two}, "BB": bb_bars})
    rec_mid = _rec([_nm("AA", 1), _nm("BB", 2, blocked=True)])
    mid = MidEntryPolicy(TCross("px_decision"), window=[575, 576], label="MID")
    st_m = Strategy(StrategySpec(name="bs_mid", mid_entry=mid, **base_spec))
    row_m = simulate_day(st_m, DAY, rec_mid, bars_mid, SE, 100.0)
    bb_m = [tk for tk in st_m.closed if tk.ticker == "BB"]
    exp_px = 10.1
    exp_sh = 0.5 / (exp_px * (1 + 100.0 / 2 / 10000.0))
    check("EXT-2 mid entry fires on the candidate's completed bar and fills next open",
          len(bb_m) == 1 and bb_m[0].entry_et == 576 and abs(bb_m[0].entry_px - exp_px) < 1e-12
          and abs(bb_m[0].shares_entry - exp_sh) < 1e-15)
    check("EXT-2 mid entry carries the slot budget and the mid_entry flag",
          abs(bb_m[0].unit_notional - 0.5) < 1e-12 and MID_ENTRY_FLAG in bb_m[0].flags
          and any(a["action"] == "ENTER" and a["et"] == 576 and a["reason"].startswith("MID:")
                  for a in bb_m[0].actions))
    check("EXT-2 mid entry keeps the no-leverage invariant (both slots deployed, both flat)",
          abs(st_m.deployed[DAY]) < 1e-12 and st_m.sleeve_cash[DAY] > 0.9
          and row_m["n_actions"] == 4 and row_m["n_open_end"] == 0)
    check("EXT-2 mid entry is a no-op when entries are disabled",
          simulate_day(Strategy(StrategySpec(name="bs_mid_off", mid_entry=mid, **base_spec)),
                       DAY, rec_mid, bars_mid, SE, 100.0, False)["n_actions"] == 0)

    st_mc = Strategy(StrategySpec(name="bs_mid_cap2", entry_pop="A_pm", entry_T=570, top_n=2,
                                  n_slots=1,
                                  mid_entry=MidEntryPolicy(TCross("px_decision"),
                                                           window=[575, 575])))
    row_mc = simulate_day(st_mc, DAY, rec_mid, bars_mid, SE, 100.0)
    check("EXT-2 unfunded new entry is skipped, flagged, never leveraged",
          len(st_mc.closed) == 1 and st_mc.n_entry_unfunded == 1
          and row_mc["flags"] == ["entry_unfunded"])
    st_mw = Strategy(StrategySpec(name="bs_mid_win", mid_entry=MidEntryPolicy(
        TCross("px_decision"), window=[900, 950]), **base_spec))
    row_mw = simulate_day(st_mw, DAY, rec_mid, bars_mid, SE, 100.0)
    check("EXT-2 a window outside the session never fires",
          len(st_mw.closed) == 1 and row_mw["flags"] == [])

    # a scheduled entry with no bar left before the forced-flat bar is cancelled
    bb_late = [(570, 10.0, 10.1, 9.7, 9.8, 100), (578, 9.9, 10.4, 9.8, 10.3, 100),
               (580, 10.2, 10.3, 10.1, 10.2, 100)]
    bars_late = _synthetic_bars(DAY, {**{t: _flat_bars(t)[1] for t in two}, "BB": bb_late})
    st_late = Strategy(StrategySpec(name="bs_late", mid_entry=MidEntryPolicy(
        TCross("px_decision"), window=[578, 578]), **base_spec))
    row_late = simulate_day(st_late, DAY, rec_mid, bars_late, SE, 100.0)
    check("EXT-2 entry that cannot execute before the flat bar is cancelled, not carried",
          len(st_late.closed) == 1 and st_late.n_entry_unfilled == 1
          and row_late["flags"] == ["entry_unfilled_too_late"]
          and all(tk.sleeve_day == DAY for tk in st_late.closed))

    # re-entry after an exit, with cooldown + per-ticker cap
    cc_bars = [(570, 10.0, 10.1, 9.9, 10.0, 100),
               (571, 10.0, 10.0, 9.40, 9.60, 100),    # low breaches the -5% level (9.5)
               (572, 9.55, 9.70, 9.50, 9.60, 100),    # EXIT executes here at min(open, 9.5)
               (573, 9.60, 9.65, 9.55, 9.60, 100),
               (574, 9.60, 10.30, 9.60, 10.20, 100),  # close reclaims the exit price
               (575, 10.30, 10.40, 10.10, 10.20, 100),
               (576, 10.20, 10.30, 10.00, 10.10, 100),
               (577, 10.10, 10.20, 9.90, 10.00, 100),
               (578, 10.00, 10.10, 9.90, 10.00, 100),
               (579, 10.00, 10.10, 9.90, 10.00, 100),
               (580, 9.95, 10.05, 9.90, 10.00, 100)]
    bars_re = _synthetic_bars(DAY, {"CC": cc_bars})
    rec_re = _rec([_nm("CC", 1, px=10.0, prev=9.5)])
    re_pol = ReentryPolicy(TCross("exit_px"), max_per_ticker=1, cooldown_bars=2, label="RE")
    st_re = Strategy(StrategySpec(name="bs_re", top_n=1, n_slots=1, reserve_frac=0.5,
                                  release=[R1(-5)], reentry=re_pol))
    simulate_day(st_re, DAY, rec_re, bars_re, SE, 100.0)
    cc = [tk for tk in st_re.closed if tk.ticker == "CC"]
    check("EXT-2 re-entry: first ticket stops out at min(open, level)",
          len(cc) == 2 and cc[0].exit_et == 572 and abs(cc[0].exit_px - 9.5) < 1e-12
          and cc[1].unit_notional == 0.5 and st_re.n_entry_unfunded == 0)
    check("EXT-2 re-entry executes at the next open after the cooldown-limited reclaim",
          cc[1].entry_et == 575 and abs(cc[1].entry_px - 10.30) < 1e-12
          and REENTRY_FLAG in cc[1].flags
          and sum(1 for tk in st_re.closed if REENTRY_FLAG in tk.flags) == 1)
    check("EXT-2 re-entry opens a fresh full-budget ticket after the exit",
          abs(cc[1].unit_notional - 0.5) < 1e-12 and cc[1].shares_entry > 0
          and cc[1].n_adds == 0 and cc[0].open is False and cc[0].shares == 0.0)
    check("EXT-2 re-entry respects max_per_ticker (two tickets, no third)",
          len(st_re.closed) == 2
          and sum(1 for tk in st_re.closed if REENTRY_FLAG in tk.flags) == 1)

    # a declared notional budget refusing a fired trigger is recorded, not silent
    st_cap = Strategy(StrategySpec(
        name="bs_re_cap", top_n=1, n_slots=1, reserve_frac=0.5, release=[R1(-5)],
        reentry=ReentryPolicy(TCross("exit_px"), max_per_ticker=1,
                              max_notional_per_day_frac=0.25)))
    row_cap = simulate_day(st_cap, DAY, rec_re, bars_re, SE, 100.0)
    check("EXT-2 a re-entry budget refusal is counted and flagged, never traded",
          len(st_cap.closed) == 1 and st_cap.n_entry_capped >= 1
          and "entry_capped" in row_cap["flags"])

    st_re2 = Strategy(StrategySpec(name="bs_re_off", top_n=1, n_slots=1, reserve_frac=0.5,
                                   release=[R1(-5)]))
    simulate_day(st_re2, DAY, rec_re, bars_re, SE, 100.0)
    check("EXT-2 without a reentry policy the name never comes back",
          len([tk for tk in st_re2.closed if tk.ticker == "CC"]) == 1)

    st_re3 = Strategy(StrategySpec(name="bs_re_tight", top_n=1, n_slots=1,
                                   release=[R1(-5)],
                                   reentry=ReentryPolicy(TCross("exit_px"),
                                                         max_per_ticker=1)))
    simulate_day(st_re3, DAY, rec_re, bars_re, SE, 100.0)
    check("EXT-2 a stopped-out slot cannot refill itself after the loss (no top-up)",
          len([tk for tk in st_re3.closed if tk.ticker == "CC"]) == 1
          and st_re3.n_entry_unfunded >= 1)

    # fingerprint binds every declared field
    def _fp(**kw):
        spec = StrategySpec(**{**{"family_id": "bs", "name": "fp", "entry_pop": "A_pm",
                                  "entry_T": 570, "top_n": 2, "n_slots": 2}, **kw})
        return _run_fingerprint(RunConfig("bs", "fp", spec, 100.0), [DAY, DAY])

    f0 = _fp()
    check("EXT-2 fingerprint is stable for identical specs", f0 == _fp())
    check("EXT-2 fingerprint binds the veto parameters",
          f0 != _fp(entry_veto=[VGate("gap_pct", "above", 25.0)])
          != _fp(entry_veto=[VGate("gap_pct", "above", 30.0)]))
    check("EXT-2 fingerprint binds the mid-entry and re-entry parameters",
          f0 != _fp(mid_entry=mid)
          and _fp(mid_entry=mid) != _fp(mid_entry=MidEntryPolicy(TCross("px_decision"),
                                                                 window=[600, 660]))
          and _fp(reentry=re_pol) != _fp(reentry=ReentryPolicy(TCross("exit_px"),
                                                               max_per_ticker=2))
          and f0 != _fp(reentry=re_pol))
    check("EXT-2 fingerprint binds fingerprint_extra",
          f0 != _fp(fingerprint_extra={"predicate": "v1"}))

    class _UnboundVeto(EntryVetoRule):
        name = "VUnbound"

        def veto(self, ctx):
            return True

    try:
        _fp(entry_veto=[_UnboundVeto()])
        unbound_raised = False
    except NotImplementedError:
        unbound_raised = True
    check("EXT-2 an unbound rule fails at run start, not silently", unbound_raised)

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
