"""Focused tests for the declared carry-bar substrate producer (2026-09-24).

Production acquisition is exercised against an injected fake Alpaca client (no
network); the local-parquet path exists only as an explicitly test-only fixture
source that can never certify a production run.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import polars as pl
import pytest

from factory.scripts import basket_carry_bars, basket_sim

DAY = "2022-03-24"          # dev day, normal 16:00 ET close, EDT (13:30 UTC == 09:30 ET)
ET = ZoneInfo("America/New_York")
OVERLAY_SCHEMA = {
    "date": pl.Utf8, "ticker": pl.Utf8, "et": pl.Int64, "open": pl.Float64,
    "high": pl.Float64, "low": pl.Float64, "close": pl.Float64, "volume": pl.Float64,
}


# --------------------------------------------------------------------------- #
# fake Alpaca client
# --------------------------------------------------------------------------- #


class _Resp:
    def __init__(self, df: pd.DataFrame):
        self.df = df


def _bars_df(rows: list[tuple[str, str, int, float]], day: str = DAY) -> pd.DataFrame:
    """rows: (ticker, hh:mm, minute-of-day, price) in ET -> provider frame."""
    out = []
    for ticker, hhmm, _et, px in rows:
        hh, mm = (int(x) for x in hhmm.split(":"))
        ts = datetime.fromisoformat(day).replace(hour=hh, minute=mm, tzinfo=ET)
        out.append({"timestamp": ts, "open": px, "high": px, "low": px, "close": px,
                    "volume": 100.0, "symbol": ticker})
    return pd.DataFrame(out)


class _FakeClient:
    """Deterministic stand-in for StockHistoricalDataClient."""

    def __init__(self, responses: dict[tuple[str, ...], list]):
        self.responses = responses
        self.requests: list = []

    def get_stock_bars(self, req):
        self.requests.append(req)
        key = tuple(req.symbol_or_symbols)
        script = self.responses.get(key)
        if script is None:
            raise AssertionError(f"unexpected request for {key}")
        item = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(item, Exception):
            raise item
        return _Resp(item)


def test_fetch_day_uses_alpaca_sip_raw_and_resolves_omissions():
    batch = _bars_df([
        ("AAA", "09:29", 569, 9.9),      # premarket -> excluded
        ("AAA", "09:30", 570, 10.0),
        ("AAA", "09:31", 571, 10.1),
        ("AAA", "16:05", 965, 10.4),     # post-close -> excluded
        ("BBB", "10:00", 600, 5.0),
    ])
    client = _FakeClient({
        ("AAA", "BBB", "CCC", "DDD", "EEE"): [batch],
        ("CCC",): [_bars_df([])],                                   # no rows -> no_bars
        ("DDD",): [RuntimeError("transient provider failure")],     # -> unavailable
        ("EEE",): [RuntimeError("transient"), _bars_df([("EEE", "11:00", 660, 7.0)])],
    })

    results, meta = basket_carry_bars.fetch_day(
        client, DAY, ["AAA", "BBB", "CCC", "DDD", "EEE"], 959,
        sleep=lambda _s: None, attempts=3)

    from alpaca.data.enums import Adjustment, DataFeed
    for req in client.requests:
        assert req.feed == DataFeed.SIP
        assert req.adjustment == Adjustment.RAW
        assert str(req.timeframe) == "1Min"
    start, end = basket_carry_bars.fetch_window(DAY, 959)
    assert start == datetime(2022, 3, 24, 9, 25, tzinfo=ET)
    assert end == datetime(2022, 3, 24, 16, 0, tzinfo=ET)   # inclusive; et<=session_end filter
    # alpaca-py's request model normalizes aware UTC datetimes to naive UTC.
    assert all(req.start == start.replace(tzinfo=None) and
               req.end == end.replace(tzinfo=None) for req in client.requests)

    assert results["AAA"]["status"] == "bars"
    assert results["AAA"]["rows"]["et"].to_list() == [570, 571]
    assert results["BBB"]["status"] == "bars"
    assert results["CCC"]["status"] == "no_bars" and results["CCC"]["rows"] is None
    assert results["DDD"]["status"] == "unavailable"
    assert results["EEE"]["status"] == "bars"                       # retried, then resolved
    assert results["EEE"]["rows"]["et"].to_list() == [660]
    assert meta["kind"] == "alpaca_sip_raw" and meta["production"] is True
    assert meta["feed"] == "sip" and meta["adjustment"] == "raw"
    assert (meta["requested"], meta["returned"], meta["no_bars"],
            meta["unavailable"]) == (5, 3, 1, 1)
    assert meta["individually_resolved"] == 3


def test_fetch_day_half_session_window_and_batch_failure_marks_unavailable():
    client = _FakeClient({("AAA",): [RuntimeError("down")]})
    results, meta = basket_carry_bars.fetch_day(
        client, DAY, ["AAA"], 779, sleep=lambda _s: None, attempts=2)
    assert results["AAA"]["status"] == "unavailable"                # never no_bars
    assert meta["unavailable"] == 1 and meta["no_bars"] == 0
    start, end = basket_carry_bars.fetch_window(DAY, 779)
    assert start == datetime(2022, 3, 24, 9, 25, tzinfo=ET)
    assert end == datetime(2022, 3, 24, 13, 4, tzinfo=ET)


def test_auth_failure_aborts_and_missing_credentials_refuse(monkeypatch, tmp_path):
    class _AuthFail:
        def get_stock_bars(self, req):
            raise Exception("401 unauthorized")

    with pytest.raises(SystemExit, match="credentials"):
        basket_carry_bars.fetch_day(_AuthFail(), DAY, ["AAA"], 959,
                                    sleep=lambda _s: None, attempts=2)

    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    with pytest.raises(SystemExit, match="credentials missing"):
        basket_carry_bars.build_client(None)
    with pytest.raises(SystemExit, match="--env-file not found"):
        basket_carry_bars.credentials(tmp_path / "nope.env")


# --------------------------------------------------------------------------- #
# test-only fixture source
# --------------------------------------------------------------------------- #


def _fixture_month(tmp_path: Path) -> Path:
    root = tmp_path / "fixture_layer"
    root.mkdir(parents=True, exist_ok=True)
    rows = [
        (datetime(2022, 3, 24, 13, 29), "AAA", 9.9, 9.9, 9.9, 9.9, 10),      # et 569 premarket
        (datetime(2022, 3, 24, 13, 30), "AAA", 10.0, 10.2, 9.9, 10.1, 100),  # et 570
        (datetime(2022, 3, 24, 13, 31), "AAA", 10.1, 10.3, 10.0, 10.2, 100), # et 571
        (datetime(2022, 3, 24, 13, 32), "AAA", 10.2, 10.4, 10.1, 10.3, 100), # et 572
        (datetime(2022, 3, 24, 20, 5), "AAA", 10.4, 10.4, 10.4, 10.4, 50),   # et 965 post-close
        (datetime(2022, 3, 24, 13, 30), "BBB", 5.0, 5.1, 4.9, 5.0, 10),      # et 570
    ]
    df = pl.DataFrame(
        rows,
        schema=["timestamp", "ticker", "open", "high", "low", "close", "volume"],
        orient="row",
    ).with_columns(pl.col("timestamp").dt.replace_time_zone("UTC"))
    df.write_parquet(root / "clean_ohlcv_2022-03.parquet")
    return root


def _run(fixture: Path, out: Path, *extra: str) -> int:
    return basket_carry_bars.main(
        ["--fixture-root", str(fixture), "--out-root", str(out), *extra])


def test_fixture_overlay_is_rth_filtered_and_never_production_certified(tmp_path):
    fixture = _fixture_month(tmp_path)
    out = tmp_path / "carry_bars"
    rc = _run(fixture, out, "--min-symbols", "2",
              "--request", f"{DAY}:AAA", "--request", f"{DAY}:ZZZ")
    assert rc == 0

    df = pl.read_parquet(out / f"{DAY}.parquet")
    assert dict(df.schema) == OVERLAY_SCHEMA
    assert df["et"].to_list() == [570, 571, 572]     # premarket/post-close excluded
    assert not list(out.glob("*.tmp"))

    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["contract"] == basket_sim.CONTRACT_VERSION
    day_entry = manifest["days"][DAY]
    assert day_entry["session_end"] == 959
    assert day_entry["source"]["production"] is False
    assert day_entry["source"]["certification"] == "fixture_test_only"
    assert day_entry["source"]["overlay_sha256"] == basket_sim._sha256_bytes(
        (out / f"{DAY}.parquet").read_bytes())
    assert day_entry["tickers"]["AAA"] == {
        "status": "bars", "certification": "fixture_test_only", "rows": 3,
        "first_et": 570, "last_et": 572}
    assert day_entry["tickers"]["ZZZ"]["status"] == "no_bars"

    # strict production substrate refuses fixture coverage; the explicit test-only
    # relaxation loads it for engine-mechanics tests
    strict = basket_sim.CarrySubstrate(root=out)
    with pytest.raises(basket_sim.MissingCarrySubstrate, match="production-certified"):
        strict.bars(DAY, "AAA", 959)
    fixture_sub = basket_sim.CarrySubstrate(root=out, require_production=False)
    assert fixture_sub.bars(DAY, "AAA", 959)["et"].tolist() == [570, 571, 572]
    assert fixture_sub.bars(DAY, "ZZZ", 959) is None
    with pytest.raises(basket_sim.MissingCarrySubstrate):
        fixture_sub.bars(DAY, "CCC", 959)


def test_fixture_mode_refuses_non_full_market_and_bad_days(tmp_path):
    fixture = _fixture_month(tmp_path)
    out = tmp_path / "carry_bars"
    with pytest.raises(SystemExit, match="non-full-market"):
        _run(fixture, out, "--min-symbols", "500", "--request", f"{DAY}:AAA")
    with pytest.raises(PermissionError, match="sealed"):
        _run(fixture, out, "--min-symbols", "2", "--request", "2024-01-02:AAA")
    with pytest.raises(PermissionError, match="reserved"):
        _run(fixture, out, "--min-symbols", "2", "--request", "2026-06-01:AAA")
    assert not (out / "manifest.json").exists()


def test_fixture_rerun_is_idempotent_and_reads_request_file(tmp_path):
    fixture = _fixture_month(tmp_path)
    out = tmp_path / "carry_bars"
    requests = tmp_path / "requests.json"
    requests.write_text(json.dumps([f"{DAY}:AAA", {"day": DAY, "ticker": "BBB"}]))

    _run(fixture, out, "--min-symbols", "2", "--requests", str(requests))
    first = pl.read_parquet(out / f"{DAY}.parquet")
    manifest_first = json.loads((out / "manifest.json").read_text())
    _run(fixture, out, "--min-symbols", "2", "--requests", str(requests))
    second = pl.read_parquet(out / f"{DAY}.parquet")
    manifest_second = json.loads((out / "manifest.json").read_text())

    assert first.equals(second)
    assert first.height == 4                                   # AAA 3 + BBB 1
    for key in ("AAA", "BBB"):
        assert (manifest_first["days"][DAY]["tickers"][key]
                == manifest_second["days"][DAY]["tickers"][key])
    assert (manifest_first["days"][DAY]["source"]["overlay_sha256"]
            == manifest_second["days"][DAY]["source"]["overlay_sha256"])
    assert set(manifest_second["days"][DAY]["tickers"]) == {"AAA", "BBB"}


def test_engine_rejects_stale_manifest_contract_or_session_end(tmp_path):
    fixture = _fixture_month(tmp_path)
    out = tmp_path / "carry_bars"
    _run(fixture, out, "--min-symbols", "2", "--request", f"{DAY}:AAA")
    manifest = json.loads((out / "manifest.json").read_text())

    stale = dict(manifest, contract="FROZEN-2026-09-22")
    (out / "manifest.json").write_text(json.dumps(stale))
    with pytest.raises(basket_sim.MissingCarrySubstrate, match="regenerate"):
        basket_sim.CarrySubstrate(root=out).bars(DAY, "AAA", 959)

    wrong_se = json.loads(json.dumps(manifest))
    wrong_se["days"][DAY]["session_end"] = 779
    (out / "manifest.json").write_text(json.dumps(wrong_se))
    with pytest.raises(basket_sim.MissingCarrySubstrate, match="session_end"):
        basket_sim.CarrySubstrate(root=out, require_production=False).bars(DAY, "AAA", 959)


def test_scan_enumerates_the_hsdt_halt_request():
    """The candidate-tape census must surface HSDT 2022-03-23 -> 2022-03-24,
    the regression case whose pending exit resolves in the next real session."""
    requests = basket_carry_bars.scan_requests(window=1)
    assert ("2022-03-24", "HSDT") in requests
