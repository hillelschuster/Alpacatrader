"""Behavioral regressions for the bounded causal-archive input and cohort cache.

The sequence study reads a dense (N, 32, 120) latent archive but only ever needs
the 32-vector at each episode's last valid ASOF frame, so these tests pin the
streaming contract: unknown members and future-shaped arrays are refused, the
chunked last-valid projection (including stale latents and valid-less rows) is
numerically what the dense computation produced, and a cached cohort is adopted
only while every recorded source/calendar/code/cohort-bytes pin matches.
"""

import io
import json
import multiprocessing as mp
import shutil
import subprocess
import sys
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import alpha_sequence_payoff as asp

SCRIPT = Path(__file__).with_name("alpha_sequence_payoff.py")


def _npy_bytes(array: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    np.lib.format.write_array(buffer, array, allow_pickle=False)
    return buffer.getvalue()


def _latent_values(index: int, n_valid: int, width: int) -> np.ndarray:
    """Values identifiable per (row, valid column, channel); zero outside valid."""
    values = np.zeros((asp.CHANNELS, width), dtype=np.float64)
    columns = (
        np.arange(n_valid, dtype=np.float64)
        + 1.0
        + 0.001 * index
        + 0.0001 * np.arange(asp.CHANNELS, dtype=np.float64)[:, None]
    )
    values[:, :n_valid] = columns
    return values.astype(np.float32)


def write_toy_archive(
    path: Path, day_specs, values_fn=None, future_garbage=False, width=asp.WIDTH, extra_members=()
):
    """Minimal causal archive; one episode per (day, ticker, scale).

    day_specs: [(day, [(ticker, start_t, prefix_len, n_valid_frames)])]. Clock
    relations query_t == start_t + prefix_len and end_t == query_t - 1 hold, and
    valid frames / nonzero values live strictly below prefix_len (unless the
    future_garbage fixture deliberately violates that).
    """
    episodes = [
        (day, ticker, scale, start_t, prefix_len, n_valid)
        for day, rows in day_specs
        for ticker, start_t, prefix_len, n_valid in rows
        for scale in asp.SCALES
    ]
    n = len(episodes)
    object_ids = np.array([f"{d}|{t}@{s}" for d, t, s, *_ in episodes], dtype="<U160")
    start = np.array([e[3] for e in episodes], dtype="<i4")
    prefix = np.array([e[4] for e in episodes], dtype="<i4")
    values = np.zeros((n, asp.CHANNELS, width), dtype="<f4")
    valid = np.zeros((n, asp.CHANNELS, width), dtype="|b1")
    for i, (*_, prefix_len, n_valid) in enumerate(episodes):
        n_valid = min(n_valid, prefix_len, width)
        valid[i, :, :n_valid] = True
        values[i] = (values_fn or _latent_values)(i, n_valid, width)
        if future_garbage and prefix_len < width:
            valid[i, :, prefix_len] = True
            values[i, :, prefix_len] = np.float32(7.0)
    members = {
        "object_ids": object_ids,
        "day": np.array([e[0] for e in episodes], dtype="<U16"),
        "block": np.array(["block1"] * n, dtype="<U16"),
        "scale": np.array([e[2] for e in episodes], dtype="<i4"),
        "start_t": start,
        "end_t": start + prefix - 1,
        "query_t": start + prefix,
        "prefix_len": prefix,
        "fit": np.array([i % 2 == 0 for i in range(n)], dtype="|b1"),
        "mode": np.array(["causal"] * n, dtype="<U16"),
        "values": values,
        "valid": valid,
        "channels": np.array([f"c{c}" for c in range(asp.CHANNELS)], dtype="<U16"),
        "episode_id": object_ids.copy(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, array in members.items():
            archive.writestr(f"{name}.npy", _npy_bytes(array))
        for name, payload in extra_members:
            archive.writestr(name, payload)
    return episodes


def write_toy_data_dir(root: Path, day_specs, drift=0.002):
    """SIP candidate snapshots + 1-minute bars so episodes fill at their ASOF clock.

    Bars are written with pyarrow (not polars): build_cohort forks its per-day
    workers, and polars' thread pool is not fork-safe once it has been used.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    root.mkdir(parents=True, exist_ok=True)
    for day, rows in day_specs:
        candidate = root / "sip" / "candidates" / f"{day}.json"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        snapshots = [
            {
                "T": t,
                "pop": "B",
                "top": [
                    {"rank": i + 1, "symbol": ticker, "score": 5.0, f"px_{t}": 5.0, f"px_{t}_et": t}
                    for i, (ticker, *_) in enumerate(rows)
                ],
            }
            for t in (575, 585)
        ]
        candidate.write_text(json.dumps({"day": day, "snapshots": snapshots}))
        bars = root / "sip" / "net" / "bars" / f"{day}.parquet"
        bars.parent.mkdir(parents=True, exist_ok=True)
        ets = np.arange(570, 960)
        tickers, open_px, close_px = [], [], []
        for idx, (ticker, *_) in enumerate(rows):
            px = 10.0 * ((1.0 + drift * (1.0 + 0.37 * (idx % 5))) ** (ets - 570))
            tickers += [ticker] * len(ets)
            open_px.append(px)
            close_px.append(px)
        pq.write_table(
            pa.table(
                {
                    "ticker": tickers,
                    "et": np.tile(ets.astype(np.int64), len(rows)),
                    "open": np.concatenate(open_px),
                    "close": np.concatenate(close_px),
                }
            ),
            bars,
        )


def write_archive_meta(npz_path: Path) -> None:
    Path(str(npz_path) + ".meta.json").write_text(json.dumps({"toy": True}) + "\n")


def test_unknown_member_and_future_shaped_arrays_are_rejected(tmp_path):
    day_specs = [("2021-03-01", [("ZZZ", 570, 30, 30)])]
    good = tmp_path / "good.npz"
    write_toy_archive(good, day_specs)
    assert all(asp.load_embeddings(good)["checks"].values())

    extra = tmp_path / "extra.npz"
    write_toy_archive(
        extra,
        day_specs,
        extra_members=[
            ("future_frames.npy", _npy_bytes(np.zeros((4, asp.CHANNELS, asp.WIDTH), dtype="<f4")))
        ],
    )
    with pytest.raises(ValueError, match="unexpected members"):
        asp.load_embeddings(extra)

    wide = tmp_path / "wide.npz"
    write_toy_archive(wide, day_specs, width=asp.WIDTH + 8)
    with pytest.raises(ValueError, match="expected"):
        asp.load_embeddings(wide)


def test_nonzero_or_valid_future_frames_fail_the_causality_checks(tmp_path):
    day_specs = [("2021-03-01", [("ZZZ", 570, 60, 20)])]
    npz = tmp_path / "garbage.npz"
    write_toy_archive(npz, day_specs, future_garbage=True)
    arrays = asp.load_embeddings(npz)
    assert arrays["checks"]["no_value_beyond_prefix"] is False
    assert arrays["checks"]["no_valid_frame_at_or_after_prefix"] is False
    write_archive_meta(npz)
    with pytest.raises(ValueError, match="causality/validity check failed"):
        asp.build_cohort(npz, tmp_path / "data", tmp_path / "out", 1, False, None)
    assert not (tmp_path / "out" / "cohort.parquet").exists()


def test_streamed_last_valid_projection_matches_expected(tmp_path):
    day_specs = [
        (
            "2021-03-01",
            [
                ("AAA", 570, 30, 30),  # fresh: last valid frame is prefix_len - 1
                ("BBB", 570, 60, 20),  # stale: validity ends 40 minutes before the ASOF clock
                ("CCC", 570, 60, 0),  # no valid frame at all
                ("DDD", 570, 120, 120),  # full window
            ],
        )
    ]
    npz = tmp_path / "toy.npz"
    write_toy_archive(npz, day_specs)
    arrays = asp.load_embeddings(npz)
    assert all(arrays["checks"].values())
    assert arrays["rows_without_valid_frame"] == len(asp.SCALES)  # CCC, once per scale
    expected_frames = {"AAA": 30, "BBB": 20, "CCC": 0, "DDD": 120}
    expected_gap = {"AAA": 0, "BBB": 40, "CCC": 60, "DDD": 0}
    for i, _ in enumerate(arrays["object_ids"].tolist()):
        ticker = arrays["ticker"][i]
        n_valid = expected_frames[ticker]
        assert arrays["valid_frames"][i] == n_valid
        assert arrays["latent_gap"][i] == expected_gap[ticker]
        want = ((n_valid + 0.001 * i) + 0.0001 * np.arange(asp.CHANNELS)).astype(np.float32)
        np.testing.assert_array_equal(
            arrays["latent"][i], want if n_valid else np.zeros(asp.CHANNELS, np.float32)
        )


def test_cache_is_reused_only_while_every_pin_matches(tmp_path, capsys, monkeypatch):
    data, out = tmp_path / "data", tmp_path / "out"
    npz = tmp_path / "toy.npz"
    day_specs = [("2021-03-01", [("ZZZ", 570, 30, 30)])]
    write_toy_archive(npz, day_specs)
    write_toy_data_dir(data, day_specs)
    write_archive_meta(npz)
    def shifted(i, n_valid, width):
        return (
            _latent_values(i, n_valid, width)
            + np.float32(5.0)
            * (
                (np.arange(asp.CHANNELS)[:, None] < n_valid) & (np.arange(width)[None, :] < n_valid)
            ).astype(np.float32)
        )
    # build_cohort forks per-day workers, and polars' thread pool is not fork-safe
    # once this process has used polars; repeated in-process builds therefore run
    # their pool in a spawned child.
    monkeypatch.setattr(
        asp,
        "ProcessPoolExecutor",
        lambda *a, **kw: ProcessPoolExecutor(*a, **{**kw, "mp_context": mp.get_context("spawn")}),
    )

    def build(**kwargs):
        manifest = asp.build_cohort(
            npz, data, out, kwargs.get("workers", 1), kwargs.get("force", False), kwargs.get("days")
        )
        return manifest, capsys.readouterr().out

    manifest, printed = build()
    assert manifest["rows"] == 3
    assert manifest["cache"]["cohort_sha256"] == asp.digest(out / "cohort.parquet")
    assert all(manifest["cache"]["causality_checks"].values())
    first_hash = manifest["cache"]["npz_sha256"]
    assert "cached" not in printed
    cached, printed = build()
    assert cached == manifest
    assert "cached" in printed

    # the pins are the policy: not one of these may reuse the cached cohort
    hashes = asp._source_hashes(npz)
    assert asp._validated_cohort(out, hashes, None) == manifest
    for key in ("npz_sha256", "npz_meta_sha256", "calendar_sha256", "code_sha256"):
        assert asp._validated_cohort(out, {**hashes, key: "0" * 64}, None) is None
    assert asp._validated_cohort(out, hashes, ["2021-03-01"]) is None  # subset vs all-days
    assert asp._validated_cohort(out, hashes, ["2021-03-02"]) is None  # different day
    legacy_out = tmp_path / "legacy_out"
    shutil.copytree(out, legacy_out)
    legacy = json.loads((legacy_out / "cohort.manifest.json").read_text())
    legacy.pop("cache")  # a producer that predates the pin block is not certified
    (legacy_out / "cohort.manifest.json").write_text(json.dumps(legacy))
    assert asp._validated_cohort(legacy_out, hashes, None) is None

    # a changed raw source must repopulate the cohort, never re-stamp the old rows
    write_toy_archive(npz, day_specs, values_fn=shifted)
    rebuilt, printed = build()
    assert "cached" not in printed
    assert rebuilt["cache"]["npz_sha256"] != first_hash
    assert rebuilt["cache"]["npz_sha256"] == asp.digest(npz)
    assert rebuilt["cache"]["cohort_sha256"] == asp.digest(out / "cohort.parquet")

    # tampering with the cached cohort bytes invalidates it just as thoroughly
    with open(out / "cohort.parquet", "ab") as handle:
        handle.write(b"junk")
    tampered, printed = build()
    assert "cached" not in printed
    assert tampered["cache"]["cohort_sha256"] == asp.digest(out / "cohort.parquet")
    assert tampered["cache"]["npz_sha256"] == rebuilt["cache"]["npz_sha256"]

    # the cohort the cache produced carries the edited source latents
    rows = pl.read_parquet(out / "cohort.parquet")
    assert [r["object_id"] for r in rows.iter_rows(named=True)] == [
        f"2021-03-01|ZZZ@{s}" for s in asp.SCALES
    ]
    assert rows["f0"].to_list() == [pytest.approx(35.0 + 0.001 * i) for i in range(3)]


def _calendar_windows():
    evidence = json.loads(asp.CALENDAR.read_text())["evidence"]
    full = [d for d in sorted(evidence) if evidence[d]["session_end"] == 959]
    return (
        [d for d in full if d < "2023-01-01"][:12],
        [d for d in full if "2023-01-01" <= d < "2024-01-01"][:50],
        [d for d in full if d >= "2025-02-01"][:2],
    )


def test_cli_run_and_predict_replay_end_to_end(tmp_path):
    train, val, late = _calendar_windows()
    tickers = [f"T{i:02d}" for i in range(8)]
    day_specs = [(day, [(t, 570, 30, 30) for t in tickers]) for day in train + val + late]
    npz, data, out = tmp_path / "toy.npz", tmp_path / "data", tmp_path / "out"
    episodes = write_toy_archive(npz, day_specs)
    write_toy_data_dir(data, day_specs)
    write_archive_meta(npz)

    command = [
        sys.executable,
        str(SCRIPT),
        "run",
        "--npz",
        str(npz),
        "--data",
        str(data),
        "--out",
        str(out),
        "--workers",
        "2",
    ]
    first = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    assert (out / "cohort.manifest.json").exists()
    manifest = json.loads((out / "cohort.manifest.json").read_text())
    assert manifest["cache"]["npz_shape"] == [len(episodes), asp.CHANNELS, asp.WIDTH]
    assert manifest["cache"]["npz_sha256"] == asp.digest(npz)
    assert all(manifest["cache"]["causality_checks"].values())
    assert manifest["rows"] == len(episodes)
    frozen = json.loads((out / "frozen_config.json").read_text())
    assert set(frozen) == {"15", "60"}
    for horizon, cfg in frozen.items():
        assert cfg["validation_fills"] >= asp.MIN_FILLS
        assert cfg["validation_traded_days"] >= asp.MIN_DAYS
        assert (out / "models" / f"head_scale{cfg['scale']}_h{horizon}.joblib").exists()
    summary = json.loads((out / "late_summary.json").read_text())
    assert set(summary) == {f"h{h}_cost{c}" for h in asp.HORIZONS for c in asp.COSTS}
    assert summary["h60_cost100"]["fills"] > 0 and summary["h60_cost100"]["days"] == len(late)
    provenance = json.loads((out / "provenance.json").read_text())
    assert provenance["npz_sha256"] == asp.digest(npz)
    assert provenance["rows_without_valid_frame"] == 0
    assert provenance["cohort"]["cache"]["cohort_sha256"] == asp.digest(out / "cohort.parquet")
    before = (out / "late_summary.json").read_bytes()

    predict = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "predict",
            "--npz",
            str(npz),
            "--data",
            str(data),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
    )
    assert predict.returncode == 0, predict.stderr
    signals = pl.read_parquet(out / "predict_signals.parquet")
    assert signals.height > 0
    assert set(late).issubset(set(signals["day"].unique().to_list()))

    # a validated cache hit replays the exact same study outputs
    replay = subprocess.run(command, capture_output=True, text=True)
    assert replay.returncode == 0, replay.stderr
    assert "cached" in replay.stdout
    assert (out / "late_summary.json").read_bytes() == before
