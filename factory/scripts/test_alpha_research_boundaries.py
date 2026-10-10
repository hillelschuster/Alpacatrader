"""Real economic regressions for quote pricing and the three-other-peers gate."""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from alpha_open_events import FAMILY_SPECS
from alpha_proven_push import qualifies
from alpha_quote_audit import clock_us


def test_three_positive_peers_excludes_the_subject():
    frame = pl.DataFrame(
        {
            "gain_open": [0.4, 0.4, 0.4],
            "dd_high15": [-0.001] * 3,
            "peer_positive3": [3, 4, 3],
            "ret3": [0.04, 0.04, -0.001],
            "dv_accel": [2.0] * 3,
            "log_cum_dv": [np.log1p(2_000_000)] * 3,
            "bars15": [15.0] * 3,
            "case": ["self_and_two", "self_and_three", "three_others"],
        }
    )
    assert frame.filter(qualifies())["case"].to_list() == ["self_and_three", "three_others"]
    assert frame.filter(FAMILY_SPECS["broadtape"]["qual"])["case"].to_list() == ["self_and_three"]


def test_unquoted_prefix_partial_positions_and_fee_budget(tmp_path):
    day = "2021-03-01"
    qdir = tmp_path / "data" / "sip" / "net" / "quotes"
    qdir.mkdir(parents=True)
    stamps = [clock_us(day, 600, 250) - 100, clock_us(day, 615, 250) - 100]
    quotes = pl.DataFrame(
        {
            "symbol": ["XYZ", "XYZ"],
            "ts_utc": pl.Series(stamps).cast(pl.Datetime("us", "UTC")),
            "bid_price": [9.99, 11.0],
            "ask_price": [10.0, 11.01],
            "bid_size": [5.0, 5.0],
            "ask_size": [5.0, 5.0],
            "conditions": [["R"], ["R"]],
        }
    )
    quotes.write_parquet(qdir / f"{day}.parquet")
    missing = {"day": day, "ticker": "XYZ", "entry_et": 600, "exit_et": None, "net": None}
    full = {"day": day, "ticker": "XYZ", "entry_et": 600, "exit_et": 615, "net": 0.10}
    partial = {**full, "exit_quantity": 50}
    path = tmp_path / "trades.json"
    path.write_text(json.dumps([missing] * 101 + [full, partial]))
    script = Path(__file__).with_name("alpha_quote_audit.py")
    subprocess.run(
        [
            sys.executable,
            str(script),
            "--trades",
            str(path),
            "--data",
            str(tmp_path / "data"),
            "--out",
            str(tmp_path / "out"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    rows = pl.read_parquet(tmp_path / "out" / "rows.parquet")
    priced, unresolved = rows.row(101, named=True), rows.row(102, named=True)
    assert priced["quoted_net_0"] == pytest.approx(0.10)
    assert priced["quantity_100"] == 99
    assert priced["quoted_net_100"] == pytest.approx((99 * 11 * 0.995 - 99 * 10 * 1.005) / 1000)
    assert unresolved["status"] == "unknown_partial_or_unexplained_position"
    assert unresolved["quoted_net_0"] is None
