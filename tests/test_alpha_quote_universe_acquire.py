"""Behavioral tests for the quote-universe acquirer's acquisition contract.

These cover the consumer-visible edges the harvester turns on and that no sibling
producer asserts today:

* the candidate list is the causal-liquidity-qualified tickers with NO stream in the
  ranked cache or any supplement root - no forecast threshold, no label filter, no
  outcome conditioning: the manifest is written BEFORE any fetch;
* ``--universe panel`` widens that list to EVERY ticker in the day's panel file (the
  admitted names that fail causal_liquidity included), leaves the qualified-mode default
  and its manifests alone, and enters the resume hash so the two modes never share a plan;
* the protected-day guard refuses BEFORE any quote or API read;
* the fetch window is 30s before the day's EARLIEST intent clock through the session
  close, with feed=sip / asof=<day> / limit=10000 and every next_page_token;
* every page is stored verbatim (JSON) and normalized (parquet part) and the cursor
  makes an interrupted run continue exactly where it stopped (terminal statuses are
  never re-fetched);
* the per-day merge is PRIMARY_KEEPFIRST over the ranked cache and the supplements, and
  the ORIGINAL source file is never written;
* a 200 with zero events is a KNOWN acquisition empty, and an HTTP error is a DATA
  UNKNOWN for that (day, ticker) - never fabricated, never cash.

Stubbed session only: no data file of the study corpus, no network, no credentials.

Run:
  uv run --no-sync python -m pytest tests/test_alpha_quote_universe_acquire.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
import pytest

sys.path.insert(0, ".")
sys.path.insert(0, "factory/scripts")

import alpha_quote_universe_acquire as aqa  # noqa: E402
from alpha_quote_audit import clock_us  # noqa: E402

DAY = "2023-03-15"
ET = ZoneInfo("America/New_York")


class _Resp:
    def __init__(self, status_code: int, payload: dict, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class _Session:
    """Stub of the requests session: a scripted page sequence, no network."""

    def __init__(self, responses, then_raise: bool = False):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.then_raise = then_raise

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": headers})
        if not self.responses:
            if self.then_raise:
                raise ConnectionError("simulated mid-pagination process death")
            raise IndexError("no scripted response left")
        return self.responses.pop(0)


def _page(events, token=None):
    return {"quotes": {"TEST": events}, "next_page_token": token}


def _event(us: int, *, bid=10.0, ask=10.02, bs=100.0, as_=100.0):
    return {
        "t": f"2023-03-15T13:46:00.{us:06d}Z",
        "bx": "P",
        "bp": bid,
        "bs": bs,
        "ax": "Q",
        "ap": ask,
        "as": as_,
        "c": ["R"],
        "z": "C",
    }


# ----- candidate planning -----------------------------------------------------
def _panel_day(tmp_path: Path, day: str, rows, monkeypatch):
    """A minimal panel day file plus the acquirer's panel root pointing at it."""
    root = tmp_path / "panel"
    (root / "days").mkdir(parents=True)
    frame = pl.DataFrame(rows)
    frame.write_parquet(root / "days" / f"{day}.parquet")
    monkeypatch.setattr(aqa, "PANEL_DAYS", root / "days")
    return root


def _qualified_row(ticker: str, t: int):
    return {
        "day": DAY,
        "ticker": ticker,
        "t": t,
        "admit_t": t - 5,
        "session_end": 959,
        "ret3": 0.01,
        "gain_open": 0.06,
        "range5": 0.02,
        "log_cum_dv": 15.0,
        "bars15": 20,
    }


def _thin_row(ticker: str, t: int):
    """A panel state that FAILS causal_liquidity (DV well under 1M, few 15m bars)."""
    return {
        "day": DAY,
        "ticker": ticker,
        "t": t,
        "admit_t": t - 5,
        "session_end": 959,
        "ret3": 0.01,
        "gain_open": 0.06,
        "range5": 0.02,
        "log_cum_dv": 9.0,
        "bars15": 3,
    }


def test_candidates_are_the_qualified_tickers_with_no_stream(tmp_path, monkeypatch):
    _panel_day(
        tmp_path,
        DAY,
        [_qualified_row("PRESENT", 585), _qualified_row("MISSING", 590)],
        monkeypatch,
    )
    data_root = tmp_path / "data"
    _write_quote_parquet(data_root, DAY, [("PRESENT", 10.0, 10.02, 100, 100, [])])
    plan = aqa.day_plan(DAY, data_root, ())
    assert plan.qualified == ["MISSING", "PRESENT"]
    assert plan.missing == ["MISSING"]
    # the window is 30s before the EARLIEST intent clock (586) through the close
    assert plan.earliest_intent_us == clock_us(DAY, 586, 0)
    assert plan.close_us == clock_us(DAY, 960, 0)
    assert plan.plan_hash("producer", 0.35) == plan.plan_hash("producer", 0.35)
    assert plan.plan_hash("producer", 0.35) != aqa.DayPlan(
        DAY, plan.qualified, [], plan.session_end, plan.earliest_intent_us, plan.close_us
    ).plan_hash("producer", 0.35)


def test_supplement_prints_count_as_present_but_never_rewrite_the_original(tmp_path, monkeypatch):
    _panel_day(tmp_path, DAY, [_qualified_row("SUPP", 585)], monkeypatch)
    data_root = tmp_path / "data"
    _write_quote_parquet(data_root, DAY, [("OTHER", 10.0, 10.02, 100, 100, [])])
    supp = tmp_path / "supp"
    _write_quote_parquet(supp, DAY, [("SUPP", 10.0, 10.02, 100, 100, [])], root_is_supp=True)
    plan = aqa.day_plan(DAY, data_root, (supp,))
    assert plan.missing == []  # a supplement-only stream is already present


def test_panel_universe_plans_a_ticker_that_fails_causal_liquidity(tmp_path, monkeypatch):
    _panel_day(
        tmp_path,
        DAY,
        [_qualified_row("LIQUID", 585), _thin_row("THIN", 582)],
        monkeypatch,
    )
    data_root = tmp_path / "data"
    _write_quote_parquet(data_root, DAY, [("LIQUID", 10.0, 10.02, 100, 100, [])])

    qualified = aqa.day_plan(DAY, data_root, (), "qualified")
    assert qualified.universe == "qualified"
    assert "THIN" not in qualified.qualified
    assert qualified.missing == []  # the only qualified name already has a stream
    assert qualified.earliest_intent_us == clock_us(DAY, 586, 0)  # default unchanged

    panel = aqa.day_plan(DAY, data_root, (), "panel")
    assert panel.universe == "panel"
    assert "THIN" in panel.qualified  # the whole panel ticker set is the universe
    assert panel.missing == ["THIN"]  # planned although causal_liquidity rejects it
    # the window still covers 30s before the earliest intent clock of the WIDER universe
    assert panel.earliest_intent_us == clock_us(DAY, 583, 0)
    assert panel.close_us == clock_us(DAY, 960, 0)
    # the universe enters the resume hash: a panel plan can never reuse a qualified manifest
    assert panel.plan_hash("producer", 0.35) != qualified.plan_hash("producer", 0.35)

    # the CLI wires the mode through to the manifest (plan-only: no network, no credentials)
    out = tmp_path / "out_panel"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--days",
            DAY,
            "--out",
            str(out),
            "--data-root",
            str(data_root),
            "--universe",
            "panel",
            "--plan-only",
        ],
    )
    aqa.main()
    manifest = json.loads((out / "manifest" / f"{DAY}.json").read_text())
    assert manifest["universe"] == "panel"
    assert manifest["candidates"] == ["THIN"]
    assert manifest["resume_hash"] == panel.plan_hash(
        aqa.producer_sha256(), aqa.DEFAULT_REQ_SLEEP_S
    )


def test_protected_day_is_refused_before_any_read():
    with pytest.raises(SystemExit):
        aqa.guard_days([DAY, "2024-05-01"])
    aqa.guard_days([DAY, "2023-12-29"])


def test_calendar_days_covers_exactly_the_three_study_blocks():
    days = aqa.calendar_days(None, ["train", "validation", "confirmation"])
    assert len(days) == 1003
    assert all(aqa.allowed(d) for d in days)
    assert days[0] == "2021-05-03"
    assert "2023-01-03" in days and "2025-02-03" in days
    assert not any(d.startswith(("2024", "2025-01", "2026-06")) for d in days)


# ----- pagination, cursor and honest outcome ----------------------------------
def test_full_pagination_writes_pages_parts_and_completes_the_cursor(tmp_path):
    out = tmp_path / "out"
    plan = aqa.DayPlan(DAY, ["TEST"], ["TEST"], 959, clock_us(DAY, 586, 0), clock_us(DAY, 960, 0))
    session = _Session(
        [
            _Resp(200, _page([_event(0)], token="tok1")),
            _Resp(200, _page([_event(1)])),
        ]
    )
    rec = aqa.fetch_symbol_day(session, {"APCA-API-KEY-ID": "x"}, out, plan, "TEST", 0.0)
    assert rec["status"] == "complete"
    assert rec["pages"] == 2 and rec["quote_events"] == 2
    # both pages were fetched with the day window, sip feed, asof and 10000 limit
    first = session.calls[0]["params"]
    assert first["feed"] == "sip" and first["asof"] == DAY and first["limit"] == 10000
    assert first["symbols"] == "TEST"
    assert "start" in first and "end" in first
    assert session.calls[1]["params"]["page_token"] == "tok1"
    # verbatim JSON per page + a normalized parquet part per page
    raw = sorted((out / "raw" / DAY / "TEST").glob("page_*.json"))
    parts = sorted((out / "parts" / DAY / "TEST").glob("page_*.parquet"))
    assert [p.name for p in raw] == ["page_0.json", "page_1.json"]
    assert [p.name for p in parts] == ["page_0.parquet", "page_1.parquet"]
    stored = json.loads(raw[0].read_text())
    assert stored["quotes"]["TEST"][0]["c"] == ["R"]  # verbatim, no condition synthesis
    frame = aqa.load_parts(out, DAY, "TEST")
    assert frame.height == 2 and frame.schema == pl.DataFrame(schema=aqa.QUOTE_SCHEMA_LOCAL).schema
    # the cursor is terminal, so a resume never re-fetches
    cursor = aqa.read_cursor(out, DAY, "TEST")
    assert cursor["status"] == "complete" and cursor["next_token"] is None
    rec2 = aqa.fetch_symbol_day(None, None, out, plan, "TEST", 0.0)  # session unused
    assert rec2["status"] == "complete" and rec2["resumed"] is True


def test_empty_200_is_a_known_acquisition_empty_not_cash(tmp_path):
    out = tmp_path / "out"
    plan = aqa.DayPlan(DAY, ["TEST"], ["TEST"], 959, clock_us(DAY, 586, 0), clock_us(DAY, 960, 0))
    session = _Session([_Resp(200, _page([]))])
    rec = aqa.fetch_symbol_day(session, {}, out, plan, "TEST", 0.0)
    assert rec["status"] == "no_quotes_in_window"
    assert rec["quote_events"] == 0
    assert aqa.read_cursor(out, DAY, "TEST")["status"] == "no_quotes_in_window"


def test_http_error_is_a_data_unknown_and_nothing_is_fabricated(tmp_path):
    out = tmp_path / "out"
    plan = aqa.DayPlan(DAY, ["TEST"], ["TEST"], 959, clock_us(DAY, 586, 0), clock_us(DAY, 960, 0))
    session = _Session([_Resp(502, {}, text="bad gateway")])
    rec = aqa.fetch_symbol_day(session, {}, out, plan, "TEST", 0.0)
    assert rec["status"] == "unknown_http_502"
    assert rec["quote_events"] == 0
    cursor = aqa.read_cursor(out, DAY, "TEST")
    assert cursor["status"] == "unknown_http_502"
    # a JSON error body is never parsed into events
    assert not list((out / "raw" / DAY / "TEST").glob("page_*.json"))


def test_interrupted_pagination_resumes_from_the_cursor(tmp_path):
    out = tmp_path / "out"
    plan = aqa.DayPlan(DAY, ["TEST"], ["TEST"], 959, clock_us(DAY, 586, 0), clock_us(DAY, 960, 0))
    # first run: page 0 arrives WITH a next-page token, then the process dies mid-call
    session = _Session([_Resp(200, _page([_event(0)], token="tok1"))], then_raise=True)
    rec = aqa.fetch_symbol_day(session, {}, out, plan, "TEST", 0.0)
    assert rec["status"] == "unknown_request_exception"
    assert rec["pages"] == 1 and rec["quote_events"] == 1
    # the cursor kept the page and the token, so the resume continues exactly there
    cursor = aqa.read_cursor(out, DAY, "TEST")
    assert cursor["pages"] == 1 and cursor["next_token"] == "tok1"
    session2 = _Session([_Resp(200, _page([_event(1)]))])
    rec2 = aqa.fetch_symbol_day(session2, {}, out, plan, "TEST", 0.0)
    assert rec2["status"] == "complete"
    assert session2.calls[0]["params"]["page_token"] == "tok1"
    assert aqa.load_parts(out, DAY, "TEST").height == 2  # page 0 kept, page 1 added


# ----- PRIMARY_KEEPFIRST merge ------------------------------------------------
def _write_quote_parquet(root: Path, day: str, rows, root_is_supp: bool = False):
    """Rows: (symbol, bid, ask, bid_size, ask_size, conditions)."""
    base = root if root_is_supp else root / "sip" / "net" / "quotes"
    base.mkdir(parents=True, exist_ok=True)
    ts = [clock_us(day, 600, 0) + i * 1_000_000 for i in range(len(rows))]
    pl.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "ts_utc": pl.Series(ts, dtype=pl.Datetime("us", "UTC")),
            "bid_price": pl.Series([r[1] for r in rows], dtype=pl.Float64),
            "bid_size": pl.Series([r[3] for r in rows], dtype=pl.Float64),
            "ask_price": pl.Series([r[2] for r in rows], dtype=pl.Float64),
            "ask_size": pl.Series([r[4] for r in rows], dtype=pl.Float64),
            "bid_exchange": ["P"] * len(rows),
            "ask_exchange": ["Q"] * len(rows),
            "conditions": pl.Series([list(r[5]) for r in rows], dtype=pl.List(pl.Utf8)),
            "tape": ["C"] * len(rows),
        }
    ).write_parquet(base / f"{day}.parquet")
    return base / f"{day}.parquet"


def test_merge_is_primary_keep_first_and_never_writes_the_source(tmp_path):
    data_root = tmp_path / "data"
    primary_path = _write_quote_parquet(data_root, DAY, [("AAA", 10.0, 10.02, 100, 100, ["R"])])
    primary_before = primary_path.read_bytes()
    supp = tmp_path / "supp"
    _write_quote_parquet(supp, DAY, [("BBB", 11.0, 11.02, 100, 100, ["R"])], root_is_supp=True)
    out = tmp_path / "out"
    parts = aqa.parts_dir(out, DAY, "CCC")
    parts.mkdir(parents=True)
    # a new print for AAA at the SAME timestamp with a different price: must NOT win
    ts = clock_us(DAY, 600, 0)
    pl.DataFrame(
        {
            "symbol": ["AAA", "CCC"],
            "ts_utc": pl.Series([ts, ts + 1_000_000], dtype=pl.Datetime("us", "UTC")),
            "bid_price": [99.0, 12.0],
            "bid_size": [100.0, 100.0],
            "ask_price": [99.5, 12.02],
            "ask_size": [100.0, 100.0],
            "bid_exchange": ["P", "P"],
            "ask_exchange": ["Q", "Q"],
            "conditions": pl.Series([["R"], ["R"]], dtype=pl.List(pl.Utf8)),
            "tape": ["C", "C"],
        }
    ).write_parquet(parts / "page_0.parquet")
    plan = aqa.DayPlan(
        DAY, ["AAA", "CCC"], ["CCC"], 959, clock_us(DAY, 586, 0), clock_us(DAY, 960, 0)
    )
    # merge_day only folds THIS run's parts; the supplements it already contains would
    # be re-read by the labels stage, so the source files stay untouched either way.
    result = aqa.merge_day(out, plan, data_root, (supp,))
    assert result["merged"] is True
    assert result["new_rows"] == 2
    merged = pl.read_parquet(result["path"])
    by_key = {(r["symbol"], int(r["ts_utc"].timestamp() * 1e6)): r for r in merged.to_dicts()}
    assert by_key[("AAA", ts)]["bid_price"] == 10.0  # the ORIGINAL print wins
    assert by_key[("BBB", ts)]["bid_price"] == 11.0  # supplement-only prints are kept
    assert by_key[("CCC", ts + 1_000_000)]["bid_price"] == 12.0  # newly acquired kept
    assert primary_path.read_bytes() == primary_before  # the 3.9GB source is untouched
    # an acquisition that added nothing writes no combined file (known empty)
    empty = aqa.DayPlan(DAY, ["AAA"], ["ZZZ"], 959, clock_us(DAY, 586, 0), clock_us(DAY, 960, 0))
    assert aqa.merge_day(tmp_path / "out2", empty, data_root, (supp,))["merged"] is False


# ----- end-to-end run loop (stubbed network, stubbed credentials) -------------
def test_run_loops_candidates_fetches_merges_and_records_the_manifest(tmp_path, monkeypatch):
    _panel_day(
        tmp_path, DAY, [_qualified_row("PRESENT", 585), _qualified_row("MISSING", 590)], monkeypatch
    )
    data_root = tmp_path / "data"
    _write_quote_parquet(data_root, DAY, [("PRESENT", 10.0, 10.02, 100, 100, ["R"])])
    out = tmp_path / "out"

    calls: list[dict] = []

    def _fake_get(session, url, params=None, headers=None):
        calls.append({"params": dict(params or {}), "headers": headers})
        symbol = params["symbols"]
        return _Resp(
            200,
            {
                "quotes": {symbol: [_event(0), _event(1)]},
                "next_page_token": None,
            },
        )

    monkeypatch.setattr(aqa, "_get", _fake_get)
    monkeypatch.setattr(aqa, "_alpaca_headers", lambda env_path: {"APCA-API-KEY-ID": "stub"})
    argv = [
        "prog",
        "--days",
        DAY,
        "--out",
        str(out),
        "--data-root",
        str(data_root),
        "--req-sleep",
        "0",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    aqa.main()

    assert len(calls) == 1  # exactly one request for the one missing ticker
    assert calls[0]["params"]["symbols"] == "MISSING"
    assert calls[0]["params"]["feed"] == "sip" and calls[0]["params"]["asof"] == DAY
    manifest = json.loads((out / "manifest" / f"{DAY}.json").read_text())
    assert manifest["planned_before_any_quote_or_api_read"] is True
    assert [r["ticker"] for r in manifest["records"]] == ["MISSING"]
    assert manifest["records"][0]["status"] == "complete"
    assert manifest["records"][0]["quote_events"] == 2
    assert manifest["acquisition_outcome"] == "acquired"
    assert manifest["unknown_tickers"] == []
    merged = pl.read_parquet(out / "quotes" / f"{DAY}.parquet")
    assert {"PRESENT", "MISSING"} <= set(merged["symbol"].unique().to_list())
    summary = json.loads((out / "acquire_summary.json").read_text())
    assert summary["candidate_day_ticker_pairs"] == 1 and summary["resolved"] == 1
    # a second identical run with --resume issues NO new request
    monkeypatch.setattr(sys, "argv", argv + ["--resume"])
    aqa.main()
    assert len(calls) == 1
