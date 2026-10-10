#!/usr/bin/env python3
"""First payoff probe on the causal masked-TCN sequence embeddings.

Reads the frozen, label-free causal embeddings archive
(``data/atlas/sequence/v1/runs/causal/block/embeddings.npz``), keeps ONLY the
last valid latent vector at each episode's ASOF decision clock (``query_t``;
verified ``query_t == start_t + prefix_len`` with no valid frame at/after it),
admits a sample only when the ticker was ALREADY known eligible in the SIP
open-anchored B top10 (score>=0.05, px>=1, px_et>=T-2, pop B) at a checkpoint
<= query_t, and labels every admitted sample with the actual next-minute-open
payoff: entry at the query_t minute-bar open, exit at the first actual open
>= min(query_t+h, session_end) for h in {15, 60}; no bar at query_t stays an
unfilled intent (cash), a missing exit bar stays UNKNOWN (never a silent zero).

Input safety: the archive's dense (N, 32, 120) latents are never materialized
in full.  Members are read from the zip in bounded row chunks and projected to
the last valid ASOF vector on the fly, and the labeled cohort is cached behind
a hash-pinned manifest (raw npz + meta + calendar + producer code + cohort
bytes + day selection).  A cached cohort is adopted only when every pin still
matches the current inputs; an edited source, calendar, code or cohort file
rebuilds it instead of silently reusing stale rows.

Head: one boring regularized ridge on the 32 ASOF latents (alpha=1.0, train-
standardized, no HPO), fit on 2021-02..2022-12 ONLY.  The pred-gross threshold
({.01, .03, .05}) and the scale variant (30/60/120) are chosen on the 2023
validation replay surface (fills>=100, traded days>=50) and frozen before the
late run.  2025-02..2026-05 ("late") is executed EXACTLY ONCE with the frozen
head/scale/threshold.

Replay uses the shared account helper (``alpha_open_sim.replay``): $1,000
order unit, $3,000 research sub-book, 100/150/200 bps round-trip costs, max 3
funded slots, one attempt per ticker/day, same-clock sells precede new buys.

Encoder-fit disclosure: the TCN weights were fit UNSUPERVISED on all 734
fit-block days (2021-02..2023-12), so the 2023 validation window has encoder
fit exposure; the late block (2025-02..2026-05) encoder is out-of-fit and is
the main independent period (previously explored market periods, NOT a pristine
holdout).  Never reads protected outcomes (2024, 2025-01, 2026-06..08).
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import struct
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import joblib
import numpy as np
import polars as pl
from alpha_open_panel import allowed
from alpha_open_sim import replay
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[2]
NPZ_DEFAULT = (
    ROOT / "data" / "atlas" / "sequence" / "v1" / "runs" / "causal" / "block" / "embeddings.npz"
)
CALENDAR = ROOT / "factory" / "artifacts" / "basket" / "sip" / "phase2_session_calendar.json"
SNAPSHOTS = (575, 585, 600, 630, 660, 720)  # open-anchored SIP B checkpoints
SCALES = (30, 60, 120)
HORIZONS = (15, 60)
THRESHOLDS = (0.01, 0.03, 0.05)  # on PREDICTED gross
COSTS = (100, 150, 200)  # bps round trip
MIN_FILLS, MIN_DAYS = 100, 50  # validation feasibility floor
LADDER_BUDGET, LADDER_SLOTS = 1000.0, 3
RIDGE_ALPHA = 1.0  # boring; no HPO
FEATURES = tuple(f"f{i}" for i in range(32))  # last valid latent vector only

CONTRACT = {
    "version": 1,
    "source": (
        "data/atlas/sequence/v1/runs/causal/block/embeddings.npz "
        "(causal mode, unsupervised, label-free)"
    ),
    "feature": (
        "last valid latent vector at ASOF query_t; 32 channels; "
        "no metadata, no future frames"
    ),
    "admission": (
        "SIP B snapshot top10, score>=0.05, px>=1, px_et>=T-2, checkpoint T<=query_t, pop B"
    ),
    "row_execution_filters": (
        "shared-panel row rules: 09:30 opening bar, last actual bar at query_t-1, "
        "close>=1 at query_t-1"
    ),
    "label_entry": "open of the query_t minute bar; no bar => unfilled_expired intent (cash)",
    "label_exit": (
        "first actual open >= min(query_t+h, session_end), h in {15,60}; absent => UNKNOWN, "
        "never a silent zero"
    ),
    "horizons": list(HORIZONS),
    "scales": list(SCALES),
    "scale_freeze": "one scale per horizon, chosen on the 2023 validation full surface before late",
    "thresholds_pred_gross": list(THRESHOLDS),
    "head": f"Ridge(alpha={RIDGE_ALPHA}) on 32 ASOF latents, train-standardized, no HPO",
    "head_fit": "2021-02-01..2022-12-31 only",
    "validation": (
        "2023-01-01..2023-12-31 (threshold + scale choice; "
        "encoder has unsupervised fit exposure)"
    ),
    "late": "2025-02-01..2026-05-31, run once with the frozen head/scale/threshold",
    "replay": (
        "alpha_open_sim.replay; $1,000 order unit, $3,000 sub-book, 3 slots, "
        "one attempt/ticker/day"
    ),
    "costs_bps_round_trip": list(COSTS),
    "execution_proxy": (
        "minute open proxy; equal $1,000 research unit (no integer shares); "
        "quote-side verification not performed here"
    ),
    "protected_unread": ["2024", "2025-01", "2026-06", "2026-07", "2026-08"],
}


def period(day: str) -> str:
    if day < "2023-01-01":
        return "train"
    if day < "2024-01-01":
        return "validation"
    return "late"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# --- bounded causal-archive input ------------------------------------------------
CHANNELS, WIDTH = 32, 120  # v1 causal archive geometry (E, T)
READ_LIMIT = 1 << 20  # max bytes per underlying read (archive lives on /mnt/c)
CHUNK_ROWS = 512  # episodes per streamed chunk (~10 MB of dense latents)
CACHE_VERSION = 1  # cohort cache manifest pin schema

# Every .npy member the v1 causal archive is allowed to carry; anything else
# (e.g. a future-frames dump) is refused instead of extracted.
ARCHIVE_MEMBERS = frozenset(
    {
        "channels",
        "episode_id",
        "object_ids",
        "day",
        "block",
        "scale",
        "start_t",
        "end_t",
        "query_t",
        "prefix_len",
        "fit",
        "mode",
        "values",
        "valid",
    }
)
# name -> (dtype, ndim); row counts and the (32, 120) tail are checked against
# the archive's own object_ids length / the fixed v1 geometry.
MEMBER_SPEC = {
    "object_ids": ("<U160", 1),
    "day": ("<U16", 1),
    "block": ("<U16", 1),
    "scale": ("<i4", 1),
    "start_t": ("<i4", 1),
    "end_t": ("<i4", 1),
    "query_t": ("<i4", 1),
    "prefix_len": ("<i4", 1),
    "fit": ("|b1", 1),
    "mode": ("<U16", 1),
    "channels": ("<U16", 1),
    "episode_id": ("<U160", 1),
    "values": ("<f4", 3),
    "valid": ("|b1", 3),
}
METADATA_MEMBERS = (
    "object_ids",
    "day",
    "block",
    "scale",
    "start_t",
    "end_t",
    "query_t",
    "prefix_len",
    "fit",
    "mode",
)


def _read_exact(stream, size: int) -> bytes:
    """Read exactly `size` bytes; every underlying read stays bounded."""
    parts, got = [], 0
    while got < size:
        piece = stream.read(size - got)
        if not piece:
            raise ValueError("truncated causal archive member")
        parts.append(piece)
        got += len(piece)
    return b"".join(parts)


class _CappedZipReader:
    """Zip member reader whose every underlying read is bounded (C: mount safety)."""

    def __init__(self, archive: zipfile.ZipFile, name: str):
        self._stream = archive.open(name, "r")

    def read(self, size: int) -> bytes:
        return self._stream.read(min(size, READ_LIMIT)) if size > 0 else b""


class _BoundedMember:
    """One known .npy member of the causal archive, streamed in bounded chunks.

    Rows are consumed strictly in order, so a dense (N, 32, 120) member is read
    one bounded chunk at a time and never held whole in memory.
    """

    def __init__(self, archive: zipfile.ZipFile, name: str):
        self.name = name
        try:
            self._reader = _CappedZipReader(archive, f"{name}.npy")
        except KeyError:
            raise ValueError(f"causal archive missing member: {name}") from None
        if _read_exact(self._reader, 6) != b"\x93NUMPY":
            raise ValueError(f"causal archive member is not a .npy array: {name}")
        major, minor = _read_exact(self._reader, 2)
        if major == 1:
            (header_len,) = struct.unpack("<H", _read_exact(self._reader, 2))
        elif major == 2:
            (header_len,) = struct.unpack("<I", _read_exact(self._reader, 4))
        else:
            raise ValueError(f"unsupported .npy version in member {name}: {major}.{minor}")
        try:
            spec = ast.literal_eval(
                _read_exact(self._reader, header_len).rstrip(b"\x00 \t\r\n").decode("latin1")
            )
        except (ValueError, SyntaxError) as exc:
            raise ValueError(f"unparsable .npy header in member {name}") from exc
        if not isinstance(spec, dict) or spec.get("fortran_order") is not False:
            raise ValueError(f"unexpected .npy header in member {name}")
        self.dtype = np.dtype(spec["descr"])
        self.shape = tuple(int(dim) for dim in spec["shape"])
        self._row = 0

    def check(self, dtype: str, shape: tuple) -> None:
        if self.dtype != np.dtype(dtype) or self.shape != tuple(shape):
            raise ValueError(
                f"causal archive member {self.name}: expected "
                f"{dtype} {tuple(shape)}, got {self.dtype} {self.shape}"
            )

    def all(self) -> np.ndarray:
        return self.rows(self.shape[0] - self._row)

    def rows(self, count: int) -> np.ndarray:
        """Read the next `count` rows of this member with bounded reads."""
        stop = self._row + count
        if count < 0 or stop > self.shape[0]:
            raise ValueError(f"row chunk out of range for member {self.name}")
        out = np.empty((count, *self.shape[1:]), dtype=self.dtype)
        flat = out.reshape(-1).view(np.uint8)
        pos = 0
        while pos < flat.size:
            piece = _read_exact(self._reader, min(READ_LIMIT, flat.size - pos))
            flat[pos : pos + len(piece)] = np.frombuffer(piece, dtype=np.uint8)
            pos += len(piece)
        self._row = stop
        return out


def load_embeddings(npz_path: Path) -> dict:
    """Stream the causal archive; verify and publish its clock relations.

    The dense (N, 32, 120) latents are projected to the last valid ASOF vector
    one bounded row chunk at a time; the full arrays are never materialized, so
    peak memory is O(CHUNK_ROWS) regardless of archive size.  Member names,
    dtypes and shapes are validated against the known v1 layout before any
    member data is read, and no member beyond the allowlist is ever opened.
    """
    with zipfile.ZipFile(npz_path) as archive:
        stems = set()
        for member in archive.namelist():
            stem = member[: -len(".npy")] if member.endswith(".npy") else member.rstrip("/")
            if stem:
                stems.add(stem)
        unknown = sorted(stems - ARCHIVE_MEMBERS)
        if unknown:
            raise ValueError(f"unexpected members in causal archive: {unknown}")
        meta: dict[str, np.ndarray] = {}
        for name in METADATA_MEMBERS:
            member = _BoundedMember(archive, name)
            meta[name] = member.all()
        n = int(meta["object_ids"].shape[0])
        for name, value in meta.items():
            member_dtype, ndim = MEMBER_SPEC[name]
            if len(value.shape) != ndim or value.shape[0] != n:
                raise ValueError(f"causal archive row count mismatch: {name}")
            if value.dtype != np.dtype(member_dtype):
                raise ValueError(f"causal archive dtype mismatch: {name}")
        values = _BoundedMember(archive, "values")
        valid = _BoundedMember(archive, "valid")
        values.check(MEMBER_SPEC["values"][0], (n, CHANNELS, WIDTH))
        valid.check(MEMBER_SPEC["valid"][0], (n, CHANNELS, WIDTH))
        width = values.shape[2]
        prefix = meta["prefix_len"]
        pos = np.arange(width)
        latent = np.zeros((n, CHANNELS), dtype=np.float32)
        valid_frames = np.empty(n, dtype=np.int32)
        latent_gap = np.empty(n, dtype=np.int32)
        has_valid_frame = np.zeros(n, dtype=bool)
        value_beyond_prefix = 0.0
        valid_at_or_after_prefix = 0
        finite = True
        for start in range(0, n, CHUNK_ROWS):
            stop = min(start + CHUNK_ROWS, n)
            chunk_values = values.rows(stop - start)
            chunk_valid = valid.rows(stop - start)
            frames = chunk_valid.any(axis=1)
            last_idx = np.where(
                frames.any(axis=1), width - 1 - np.argmax(frames[:, ::-1], axis=1), -1
            )
            chunk_has_valid = last_idx >= 0
            chunk_prefix = prefix[start:stop]
            finite = bool(np.isfinite(chunk_values).all()) and finite
            value_beyond_prefix += float(
                (np.abs(chunk_values) * (pos[None, None, :] >= chunk_prefix[:, None, None])).sum()
            )
            valid_at_or_after_prefix += int(
                (frames & (pos[None, :] >= chunk_prefix[:, None])).sum()
            )
            chunk_latent = np.zeros((stop - start, CHANNELS), dtype=np.float32)
            chunk_latent[chunk_has_valid] = chunk_values[
                np.flatnonzero(chunk_has_valid), :, last_idx[chunk_has_valid]
            ]
            latent[start:stop] = chunk_latent
            valid_frames[start:stop] = frames.sum(axis=1)
            latent_gap[start:stop] = np.maximum(chunk_prefix - 1 - last_idx, 0)
            has_valid_frame[start:stop] = chunk_has_valid

    arrays = dict(meta)
    # the archive carries no ticker column; the object_id geometry is "day|TICKER@scale"
    arrays["ticker"] = np.array(
        [str(o).split("|")[1].split("@")[0] for o in arrays["object_ids"].tolist()]
    )
    arrays["latent"] = latent
    arrays["valid_frames"] = valid_frames
    arrays["latent_gap"] = latent_gap
    arrays["checks"] = {
        "mode_is_causal_only": {str(m) for m in arrays["mode"].tolist()} == {"causal"},
        "query_t_eq_start_plus_prefix": bool(
            np.array_equal(arrays["query_t"], arrays["start_t"] + arrays["prefix_len"])
        ),
        "end_t_eq_query_minus_1": bool(np.array_equal(arrays["end_t"], arrays["query_t"] - 1)),
        "no_valid_frame_at_or_after_prefix": valid_at_or_after_prefix == 0,
        "no_value_beyond_prefix": value_beyond_prefix == 0.0,
        "no_nan_values": finite,
    }
    arrays["rows_without_valid_frame"] = int((~has_valid_frame).sum())
    arrays["values_shape"] = (n, CHANNELS, width)
    return arrays


def admission_map(day: str, data_dir: Path) -> dict:
    """Earliest SIP B checkpoint at which the ticker was eligible (pop B only)."""
    candidate = json.loads((data_dir / "sip" / "candidates" / f"{day}.json").read_text())
    admit = {}
    for snap in candidate["snapshots"]:
        if snap["pop"] != "B" or snap["T"] not in SNAPSHOTS:
            continue
        t = snap["T"]
        for r in snap["top"]:
            if (
                r["score"] >= 0.05
                and r.get(f"px_{t}", 0) >= 1
                and r.get(f"px_{t}_et", -1) >= t - 2
                and (r["symbol"] not in admit or t < admit[r["symbol"]])
            ):
                admit[r["symbol"]] = t
    return admit


def build_day(job: tuple) -> list[dict]:
    """Admission + next-minute-open payoff labels for one day's NPZ episodes."""
    day, data_dir, session_end, rows = job
    if not allowed(day):
        raise ValueError(f"protected/out-of-scope day refused: {day}")
    data = Path(data_dir)
    admit = admission_map(day, data)
    tickers = sorted({r["ticker"] for r in rows})
    tape = (
        pl.scan_parquet(data / "sip" / "net" / "bars" / f"{day}.parquet")
        .filter(pl.col("ticker").is_in(tickers))
        .collect()
    )
    if tape.height and tape.select(pl.struct("ticker", "et").is_duplicated().any()).item():
        raise ValueError(f"duplicate tape bars: {day}")
    bars = {}
    for key, f in tape.partition_by("ticker", as_dict=True).items():
        bars[key[0]] = (f["et"].to_numpy(), f["open"].to_numpy(), f["close"].to_numpy())
    out = []
    for r in rows:
        et, o, c = bars.get(r["ticker"], (np.empty(0, dtype=np.int64), np.empty(0), np.empty(0)))
        q = r["query_t"]
        admitted = r["ticker"] in admit and admit[r["ticker"]] <= q
        i = int(np.searchsorted(et, q))
        k = int(np.searchsorted(et, q - 1))
        filled = i < len(et) and et[i] == q
        # shared-panel row execution filters: 09:30 opening anchor, last actual bar at t-1,
        # $1 floor at t-1 (the snapshot's own px_T>=1 remains the admission floor)
        if len(et) == 0:
            row_drop_reason = "missing_tape"
        elif et[0] != 570:
            row_drop_reason = "no_open_bar"
        elif not (k < len(et) and et[k] == q - 1):
            row_drop_reason = "no_bar_at_t_minus_1"
        elif c[k] < 1:
            row_drop_reason = "price_below_1_at_t_minus_1"
        else:
            row_drop_reason = None
        entry_open = float(o[i]) if filled else None
        row = {
            "day": day,
            "ticker": r["ticker"],
            "scale": int(r["scale"]),
            "query_t": int(q),
            "object_id": r["object_id"],
            "block": r["block"],
            "period": period(day),
            "admit_t": int(admit[r["ticker"]]) if admitted else None,
            "admitted": admitted,
            "row_drop_reason": row_drop_reason,
            "executable": admitted and row_drop_reason is None,
            "entry_status": "filled_proxy" if filled else "unfilled_expired",
            "entry_et": int(q) if filled else None,
            "entry_open": entry_open,
            "session_end": int(session_end),
            "valid_frames": int(r["valid_frames"]),
            "latent_gap": int(r["latent_gap"]),
        }
        for h in HORIZONS:
            target = min(q + h, session_end)
            j = int(np.searchsorted(et, target))
            if not filled:
                row[f"gross_{h}"], row[f"exit_et_{h}"] = 0.0, None
                row[f"exit_status_{h}"] = "unfilled_cash"
            elif j < len(et) and et[j] <= session_end:
                row[f"gross_{h}"] = float(o[j]) / entry_open - 1
                row[f"exit_et_{h}"] = int(et[j])
                row[f"exit_status_{h}"] = "observed_open_proxy"
            else:
                row[f"gross_{h}"], row[f"exit_et_{h}"] = None, None
                row[f"exit_status_{h}"] = "unknown_pending"
        out.append(row)
    return out


COHORT_SCHEMA = {
    "day": pl.Utf8,
    "ticker": pl.Utf8,
    "scale": pl.Int32,
    "query_t": pl.Int32,
    "object_id": pl.Utf8,
    "block": pl.Utf8,
    "period": pl.Utf8,
    "admit_t": pl.Int32,
    "admitted": pl.Boolean,
    "row_drop_reason": pl.Utf8,
    "executable": pl.Boolean,
    "entry_status": pl.Utf8,
    "entry_et": pl.Int32,
    "entry_open": pl.Float64,
    "session_end": pl.Int32,
    "valid_frames": pl.Int32,
    "latent_gap": pl.Int32,
    **{f"gross_{h}": pl.Float64 for h in HORIZONS},
    **{f"exit_et_{h}": pl.Int32 for h in HORIZONS},
    **{f"exit_status_{h}": pl.Utf8 for h in HORIZONS},
    **dict.fromkeys(FEATURES, pl.Float32),
}


def cohort_stats(frame: pl.DataFrame, runtime: float) -> dict:
    out = {"rows": frame.height, "runtime_s": round(runtime, 2), "by_period": {}, "by_scale": {}}
    for p in ("train", "validation", "late"):
        sub = frame.filter(pl.col("period") == p)
        out["by_period"][p] = {
            "npz_samples": sub.height,
            "admitted_snapshot_eligible": int(sub["admitted"].sum()),
            "dropped_not_eligible_at_asof": int((~sub["admitted"]).sum()),
            "dropped_row_filter": int((sub["admitted"] & ~sub["executable"]).sum()),
            "row_filter_reasons": sub.filter(sub["admitted"] & ~sub["executable"])
            .group_by("row_drop_reason")
            .len()
            .sort("row_drop_reason")
            .select(pl.col("row_drop_reason"), pl.col("len"))
            .rows(),
            "executable_cohort": int(sub["executable"].sum()),
            "entry_filled": int(
                (sub["executable"] & (sub["entry_status"] == "filled_proxy")).sum()
            ),
            "entry_unfilled_intent": int(
                (sub["executable"] & (sub["entry_status"] == "unfilled_expired")).sum()
            ),
            "unknown_exit_15": int(
                (sub["executable"] & (sub["exit_status_15"] == "unknown_pending")).sum()
            ),
            "unknown_exit_60": int(
                (sub["executable"] & (sub["exit_status_60"] == "unknown_pending")).sum()
            ),
        }
    for s in SCALES:
        sub = frame.filter(pl.col("scale") == s)
        out["by_scale"][str(s)] = {
            "npz_samples": sub.height,
            "admitted_snapshot_eligible": int(sub["admitted"].sum()),
            "executable_cohort": int(sub["executable"].sum()),
        }
    return out


def _source_hashes(npz_path: Path) -> dict:
    """Hashes pinning a cohort build to its raw source, calendar and producer code."""
    meta_path = Path(str(npz_path) + ".meta.json")
    if not meta_path.is_file():
        raise ValueError(f"missing causal archive meta json: {meta_path}")
    return {
        "npz_path": str(npz_path),
        "npz_sha256": digest(npz_path),
        "npz_meta_sha256": digest(meta_path),
        "calendar_sha256": digest(CALENDAR),
        "code_sha256": digest(Path(__file__)),
    }


def _validated_cohort(out_dir: Path, hashes: dict, days: list[str] | None) -> dict | None:
    """Return the cached cohort manifest only if every recorded pin still matches.

    A hit requires the manifest to pin the current raw source (npz + meta), the
    session calendar, the producer code, the cohort parquet bytes and the exact
    day selection; a merely present parquet (or a legacy manifest without the
    pin block) is never trusted and forces a rebuild.
    """
    manifest_path = out_dir / "cohort.manifest.json"
    cohort_path = out_dir / "cohort.parquet"
    try:
        manifest = json.loads(manifest_path.read_text())
        cache = manifest["cache"]
    except (OSError, ValueError, AttributeError, TypeError, KeyError):
        return None
    if not isinstance(cache, dict) or cache.get("version") != CACHE_VERSION:
        return None
    if not cohort_path.is_file():
        return None
    for key in ("npz_sha256", "npz_meta_sha256", "calendar_sha256", "code_sha256", "cohort_sha256"):
        if not isinstance(cache.get(key), str):
            return None
    if any(
        cache[key] != hashes[key]
        for key in ("npz_sha256", "npz_meta_sha256", "calendar_sha256", "code_sha256")
    ):
        return None
    if cache["cohort_sha256"] != digest(cohort_path):
        return None
    if days is None:
        return manifest if cache.get("days_mode") == "all" else None
    if cache.get("days_mode") != "subset" or cache.get("days") != sorted(days):
        return None
    return manifest


def build_cohort(
    npz_path: Path, data_dir: Path, out_dir: Path, workers: int, force: bool, days: list[str] | None
) -> dict:
    """Build the labeled cohort, or adopt a hash-validated cached one."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cohort_path = out_dir / "cohort.parquet"
    hashes = _source_hashes(npz_path)
    if not force:
        manifest = _validated_cohort(out_dir, hashes, days)
        if manifest is not None:
            print(
                f"cohort: cached {manifest['rows']} rows (validated source+cohort hashes)",
                flush=True,
            )
            return manifest
    arrays = load_embeddings(npz_path)
    if not all(arrays["checks"].values()) or arrays["rows_without_valid_frame"]:
        raise ValueError(f"causality/validity check failed: {arrays['checks']}")
    calendar = json.loads(CALENDAR.read_text())
    all_days = days or sorted({str(d) for d in arrays["day"].tolist()})
    for d in all_days:
        if not allowed(d):
            raise ValueError(f"protected/out-of-scope day refused: {d}")
        if d not in calendar["evidence"]:
            raise ValueError(f"day missing from session calendar: {d}")
    by_day: dict[str, list[dict]] = {}
    day_set = set(all_days)
    for i in range(len(arrays["object_ids"])):
        d = str(arrays["day"][i])
        if d not in day_set:
            continue
        by_day.setdefault(d, []).append(
            {
                "ticker": str(arrays["ticker"][i]),
                "scale": int(arrays["scale"][i]),
                "query_t": int(arrays["query_t"][i]),
                "object_id": str(arrays["object_ids"][i]),
                "block": str(arrays["block"][i]),
                "valid_frames": int(arrays["valid_frames"][i]),
                "latent_gap": int(arrays["latent_gap"][i]),
            }
        )
    jobs = [
        (d, str(data_dir), int(calendar["evidence"][d]["session_end"]), by_day.get(d, []))
        for d in all_days
    ]
    rows: list[dict] = []
    start = time.monotonic()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for n, day_rows in enumerate(pool.map(build_day, jobs), 1):
            rows.extend(day_rows)
            if n % 200 == 0:
                print(f"cohort {n}/{len(jobs)} days", flush=True)
    latent = {
        str(arrays["object_ids"][i]): arrays["latent"][i] for i in range(arrays["latent"].shape[0])
    }
    for r in rows:
        vec = latent[r["object_id"]]
        r.update({f"f{c}": float(vec[c]) for c in range(32)})
    frame = pl.DataFrame(rows, schema=COHORT_SCHEMA).sort(["day", "ticker", "scale"])
    tmp = cohort_path.with_suffix(".parquet.tmp")
    frame.write_parquet(tmp)
    tmp.replace(cohort_path)
    manifest = cohort_stats(frame, time.monotonic() - start)
    manifest["cache"] = {
        "version": CACHE_VERSION,
        **hashes,
        "cohort_sha256": digest(cohort_path),
        "npz_shape": list(arrays["values_shape"]),
        "causality_checks": arrays["checks"],
        "rows_without_valid_frame": arrays["rows_without_valid_frame"],
        "days_mode": "all" if days is None else "subset",
        "days": None if days is None else sorted(days),
    }
    (out_dir / "cohort.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"cohort: {manifest['rows']} rows in {manifest['runtime_s']}s", flush=True)
    return manifest


def fit_heads(frame: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    """One ridge per (scale, horizon), fit on 2021-22 filled, known-exit rows."""
    models, pieces = {}, []
    for scale in SCALES:
        sub = frame.filter((pl.col("scale") == scale) & pl.col("executable"))
        for horizon in HORIZONS:
            train = sub.filter(
                (pl.col("period") == "train")
                & (pl.col("entry_status") == "filled_proxy")
                & pl.col(f"gross_{horizon}").is_not_null()
            )
            if train.height < 50:
                raise ValueError(f"no training rows for scale {scale} horizon {horizon}")
            x = train.select(FEATURES).to_numpy().astype(np.float64)
            y = train[f"gross_{horizon}"].to_numpy().astype(np.float64)
            scaler = StandardScaler().fit(x)
            ridge = Ridge(alpha=RIDGE_ALPHA).fit(scaler.transform(x), y)
            pred = ridge.predict(
                scaler.transform(sub.select(FEATURES).to_numpy().astype(np.float64))
            )
            sub = sub.with_columns(pl.Series(f"pred_{horizon}", pred).cast(pl.Float64))
            models[(scale, horizon)] = {
                "scaler": scaler,
                "ridge": ridge,
                "train_rows": int(train.height),
                "train_window": "2021-02-01..2022-12-31",
                "horizon": horizon,
                "scale": scale,
                "features": list(FEATURES),
                "alpha": RIDGE_ALPHA,
            }
        pieces.append(sub)
    fitted = pl.concat(pieces)
    return frame.join(
        fitted.select(["object_id", "pred_15", "pred_60"]), on="object_id", how="left"
    ), models


def signals_of(frame: pl.DataFrame, threshold: float, horizon: int) -> pl.DataFrame:
    """Convert predicted-gross rows into the shared replay helper schema."""
    cols = {
        "day": "day",
        "query_t": "t",
        "ticker": "ticker",
        f"pred_{horizon}": "score",
        "entry_et": "entry_et",
        "entry_open": "entry_open",
        "entry_status": "entry_status",
        "session_end": "session_end",
        f"gross_{horizon}": f"gross_{horizon}",
        f"exit_et_{horizon}": f"exit_et_{horizon}",
        f"exit_status_{horizon}": f"exit_status_{horizon}",
    }
    return frame.filter(pl.col(f"pred_{horizon}") >= threshold).select(
        [pl.col(k).alias(v) for k, v in cols.items()]
    )


def selected_view(sig: pl.DataFrame, horizon: int, scale: int, threshold: float) -> pl.DataFrame:
    """Persisted (audit) view of selected signals; harmonized across horizons."""
    return sig.with_columns(
        pl.lit(horizon).alias("horizon"),
        pl.lit(scale).alias("scale"),
        pl.lit(threshold).alias("threshold_pred_gross"),
    ).rename(
        {
            f"gross_{horizon}": "gross",
            f"exit_et_{horizon}": "exit_et",
            f"exit_status_{horizon}": "exit_status",
            "t": "t",
        }
    )


def replay_cell(sig: pl.DataFrame, days: list[str], horizon: int, cost: float) -> tuple:
    metrics, trades = replay(
        sig, days, horizon, cost, max_positions=LADDER_SLOTS, order_budget=LADDER_BUDGET
    )
    row = {
        k: metrics[k]
        for k in (
            "days",
            "attempts",
            "fills",
            "known_fills",
            "unknown_fills",
            "cash_or_slot_skips",
            "mean_net_known_fill",
            "mean_daily_lower_bound",
            "daily_se",
            "traded_days",
            "months",
            "known_win_rate",
            "worst_known_fill",
            "positive_months_lower_bound",
        )
    }
    row["cost_bps"] = cost
    row["horizon"] = horizon
    return row, trades, metrics["daily"]


def validation_surface(frame: pl.DataFrame) -> list[dict]:
    surface = []
    for scale in SCALES:
        sub = frame.filter(pl.col("scale") == scale)
        val_days = sorted(sub.filter(pl.col("period") == "validation")["day"].unique().to_list())
        for horizon in HORIZONS:
            for thr in THRESHOLDS:
                sig = signals_of(sub, thr, horizon)
                for cost in COSTS:
                    row, _, _ = replay_cell(sig, val_days, horizon, cost)
                    row.update({"scale": scale, "threshold_pred_gross": thr})
                    row["feasible"] = bool(
                        row["fills"] >= MIN_FILLS and row["traded_days"] >= MIN_DAYS
                    )
                    surface.append(row)
    return surface


def freeze_config(surface: list[dict]) -> dict:
    frozen = {}
    for horizon in HORIZONS:
        best = None
        for scale in SCALES:
            cells = [
                r
                for r in surface
                if r["scale"] == scale
                and r["horizon"] == horizon
                and r["cost_bps"] == 150
                and r["feasible"]
            ]
            if not cells:
                continue
            cell = max(
                cells,
                key=lambda r: (
                    r["mean_net_known_fill"] if r["mean_net_known_fill"] is not None else -9e9,
                    r["fills"],
                    -r["threshold_pred_gross"],
                ),
            )
            cand = {
                "scale": scale,
                "threshold_pred_gross": cell["threshold_pred_gross"],
                "validation_mean_net_known_fill_150bps": cell["mean_net_known_fill"],
                "validation_fills": cell["fills"],
                "validation_traded_days": cell["traded_days"],
            }
            key = (
                cand["validation_mean_net_known_fill_150bps"]
                if cand["validation_mean_net_known_fill_150bps"] is not None
                else -9e9,
                cand["validation_fills"],
                -scale,
            )
            if best is None or key > best[0]:
                best = (key, cand)
        if best is None:
            raise ValueError(f"no feasible validation cell for horizon {horizon}")
        frozen[str(horizon)] = best[1]
    return frozen


def run(args: argparse.Namespace) -> None:
    args.out.mkdir(parents=True, exist_ok=True)
    contract_path = args.out / "contract.json"
    contract = json.dumps(CONTRACT, indent=2) + "\n"
    if contract_path.exists() and contract_path.read_text() != contract:
        raise ValueError("output root contract mismatch; use a new versioned output root")
    contract_path.write_text(contract)

    manifest = build_cohort(args.npz, args.data, args.out, args.workers, args.force, args.days)
    cache = manifest["cache"]
    print(f"embeddings {tuple(cache['npz_shape'])} checks={cache['causality_checks']}", flush=True)
    frame = pl.read_parquet(args.out / "cohort.parquet")
    frame, models = fit_heads(frame)
    print(
        "heads:",
        {f"s{s}_h{h}": m["train_rows"] for (s, h), m in sorted(models.items())},
        flush=True,
    )

    surface = validation_surface(frame)
    pl.DataFrame(surface).write_csv(args.out / "validation_surface.csv")
    (args.out / "validation_surface.json").write_text(json.dumps(surface, indent=2) + "\n")
    frozen = freeze_config(surface)
    (args.out / "frozen_config.json").write_text(json.dumps(frozen, indent=2) + "\n")
    print(f"frozen: {frozen}", flush=True)

    model_dir = args.out / "models"
    model_dir.mkdir(exist_ok=True)
    for (scale, horizon), model in models.items():
        joblib.dump(model, model_dir / f"head_scale{scale}_h{horizon}.joblib")

    late_summary, all_trades, all_daily, late_signals, val_signals = {}, [], [], [], []
    for horizon in HORIZONS:
        cfg = frozen[str(horizon)]
        scale, thr = cfg["scale"], cfg["threshold_pred_gross"]
        late = frame.filter((pl.col("scale") == scale) & (pl.col("period") == "late"))
        sig = signals_of(late, thr, horizon)
        late_signals.append(selected_view(sig, horizon, scale, thr))
        val = frame.filter((pl.col("scale") == scale) & (pl.col("period") == "validation"))
        val_signals.append(selected_view(signals_of(val, thr, horizon), horizon, scale, thr))
        for cost in COSTS:
            row, trades, daily = replay_cell(
                sig, sorted(late["day"].unique().to_list()), horizon, cost
            )
            row.update({"scale": scale, "threshold_pred_gross": thr})
            late_summary[f"h{horizon}_cost{cost}"] = row
            all_trades.extend(
                dict(t, scale=scale, threshold_pred_gross=thr, cost_bps=cost) for t in trades
            )
            all_daily.extend(
                dict(d, horizon=horizon, scale=scale, threshold_pred_gross=thr, cost_bps=cost)
                for d in daily
            )
        print(f"late h{horizon} cost150: {late_summary[f'h{horizon}_cost150']}", flush=True)

    pl.concat(late_signals).write_parquet(args.out / "late_selected_signals.parquet")
    pl.concat(val_signals).write_parquet(args.out / "validation_selected_signals.parquet")
    pl.DataFrame(all_trades).write_csv(args.out / "late_trades.csv")
    pl.DataFrame(all_daily).write_csv(args.out / "late_daily.csv")
    (args.out / "late_summary.json").write_text(json.dumps(late_summary, indent=2) + "\n")

    provenance = {
        "npz_sha256": cache["npz_sha256"],
        "npz_meta_sha256": cache["npz_meta_sha256"],
        "calendar_sha256": cache["calendar_sha256"],
        "code_sha256": cache["code_sha256"],
        "causality_checks": cache["causality_checks"],
        "rows_without_valid_frame": cache["rows_without_valid_frame"],
        "cohort": manifest,
        "feature_cols": list(FEATURES),
        "scale_freeze_before_late": frozen,
        "encoder_fit_disclosure": (
            "TCN weights fit UNSUPERVISED on all 734 fit-block days 2021-02..2023-12; the 2023 "
            "validation window therefore has encoder fit exposure. Late 2025-02..2026-05 encoder "
            "is out-of-fit and is the main independent period."
        ),
        "market_period_note": (
            "2025-02..2026-05 was previously explored in other lanes; "
            "NOT a pristine holdout."
        ),
        "head_spec": {
            "family": "Ridge",
            "alpha": RIDGE_ALPHA,
            "standardized": True,
            "hpo": False,
            "fit_window": "2021-02-01..2022-12-31",
            "features": "last valid latent vector at ASOF query_t only",
        },
        "execution_proxy": (
            "minute open entry/exit proxy; equal $1,000 research unit; "
            "no quote-side fill verification"
        ),
    }
    (args.out / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(f"complete -> {args.out}", flush=True)


def predict(args: argparse.Namespace) -> None:
    """Apply the frozen per-horizon heads to the cached cohort and emit signals."""
    frozen = json.loads((args.out / "frozen_config.json").read_text())
    frame = pl.read_parquet(args.out / "cohort.parquet")
    if args.days:
        frame = frame.filter(pl.col("day").is_in(args.days))
    pieces = []
    for horizon in HORIZONS:
        cfg = frozen[str(horizon)]
        model = joblib.load(args.out / "models" / f"head_scale{cfg['scale']}_h{horizon}.joblib")
        sub = frame.filter((pl.col("scale") == cfg["scale"]) & pl.col("executable"))
        x = sub.select(FEATURES).to_numpy().astype(np.float64)
        sub = sub.with_columns(
            pl.Series("pred", model["ridge"].predict(model["scaler"].transform(x))).cast(pl.Float64)
        )
        keep = sub.filter(pl.col("pred") >= cfg["threshold_pred_gross"]).select(
            "day",
            "ticker",
            "query_t",
            "scale",
            "entry_status",
            "entry_et",
            "entry_open",
            "session_end",
            pl.col(f"gross_{horizon}").alias("gross"),
            pl.col(f"exit_et_{horizon}").alias("exit_et"),
            pl.col(f"exit_status_{horizon}").alias("exit_status"),
            pl.lit(horizon).alias("horizon"),
            pl.lit(cfg["threshold_pred_gross"]).alias("threshold_pred_gross"),
            pl.col("pred").alias("score"),
        )
        pieces.append(keep)
    out = pl.concat(pieces).sort(["day", "query_t", "ticker"])
    args.out.mkdir(parents=True, exist_ok=True)
    out.write_parquet(args.out / "predict_signals.parquet")
    print(
        f"predict: {out.height} selected signal rows -> {args.out / 'predict_signals.parquet'}",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "predict"])
    parser.add_argument("--npz", type=Path, default=NPZ_DEFAULT)
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument(
        "--out", type=Path, default=Path.home() / "alpha-data" / "open-search-v1" / "sequence"
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--days", nargs="+")
    args = parser.parse_args()
    (run if args.command == "run" else predict)(args)


if __name__ == "__main__":
    main()
