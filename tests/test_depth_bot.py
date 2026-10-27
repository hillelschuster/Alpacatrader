from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import polars as pl
import pytest

from factory.scripts import depth_bot

DAY = "2023-01-19"
SESSION_END = 959
PARITY_DAYS = (
    ("2023-01-19", "validation"),
    ("2023-01-24", "validation"),
    ("2025-03-31", "confirmation"),
    ("2025-04-01", "confirmation"),
    ("2025-07-31", "confirmation"),  # t == admit_t (quote-tracking lag)
    ("2026-03-24", "confirmation"),
    ("2026-04-20", "confirmation"),
)
PARITY_FIELDS = (
    "ticker",
    "t",
    "entry_minute",
    "intent_us",
    "limit_price",
    "qty",
    "qty_capital",
    "entry_status",
    "entry_ask",
    "entry_bid",
    "entry_age_s",
    "entry_quote_us",
    "entry_arrival_us",
    "exit_minute",
    "exit_bid",
    "exit_bid_shares",
    "exit_status",
    "actual_exit_us",
    "fill_status",
    "net_usd_25",
    "attempt_index",
    "positions_at_entry",
)
RESEARCH_OUTPUT = depth_bot.research.OUTPUT


def minute_us(day: str, minute: int) -> int:
    return depth_bot.minute_us(day, minute)


def quote(symbol: str, stamp_us: int, bid: float, ask: float, size: float = 1000.0) -> dict:
    return {
        "symbol": symbol,
        "ts_utc": int(stamp_us),
        "bid_price": bid,
        "ask_price": ask,
        "bid_size": size,
        "ask_size": size,
        "conditions": ["R"],
    }


def intent_row(ticker: str = "AAA", t: int = 600, pred: float = 0.05) -> dict:
    """A scored candidate row in the shape ``IncrementalDepthFeatures.decision`` emits."""
    return {
        "ticker": ticker,
        "t": int(t),
        "pred_60": float(pred),
        "gross_60": 0.0,
        "entry_status": "filled_proxy",
    }


def bar(ticker: str, minute: int, close: float = 10.0, volume: float = 1000.0) -> dict:
    return {
        "ticker": ticker,
        "et": int(minute),
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": volume,
    }


def make_engine(
    tmp_path: Path,
    *,
    broker=None,
    kill_path=None,
    open_positions=None,
    risk=None,
    history=None,
    universe=None,
    alert_path=None,
    states_path=None,
    fresh=True,
    model=None,
):
    cache = depth_bot.QuoteCache(DAY)
    sim = broker if broker is not None else depth_bot.SimBroker(
        DAY, cache, depth_bot.minute_us(DAY, SESSION_END)
    )
    engine = depth_bot.DepthEngine(
        DAY,
        SESSION_END,
        model,
        sim,
        tmp_path / "journal.jsonl",
        quotes=cache,
        kill_path=kill_path,
        open_positions=open_positions,
        risk=risk,
        history=history,
        universe=universe,
        alert_path=alert_path,
        states_path=states_path,
        fresh=fresh,
        mode=str(getattr(sim, "mode", "sim")),
    )
    return engine, sim


def push_quote(engine, ticker: str, stamp_us: int, bid: float, ask: float, size: float = 1000.0):
    engine.quotes.add(quote(ticker, stamp_us, bid, ask, size))
    engine.advance(int(stamp_us))


def events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run_one_trade(engine, *, minute: int = 601, ticker: str = "AAA") -> None:
    """Fund one synthetic intent, then let its simulated entry settle."""
    intent_us = minute_us(DAY, minute)
    push_quote(engine, ticker, intent_us - 500_000, 10.00, 10.05)
    engine.pending_intents[minute] = [intent_row(ticker, t=minute - 1)]
    engine._process_intent_clock(minute)
    engine.advance(intent_us + 300_000)


# ----- operational controls ---------------------------------------------------
def test_armed_gate_requires_flag_and_file(tmp_path: Path):
    armed = tmp_path / "ARMED"
    assert not depth_bot.paper_orders_allowed(True, armed)
    armed.touch()
    assert not depth_bot.paper_orders_allowed(False, armed)
    assert depth_bot.paper_orders_allowed(True, armed)


def test_kill_file_blocks_new_entries(tmp_path: Path):
    kill = tmp_path / "KILL"
    assert depth_bot.entry_control_allowed(kill, False)
    kill.touch()
    assert not depth_bot.entry_control_allowed(kill, False)
    assert depth_bot.entry_control_allowed(kill, True)  # exits remain enabled


def test_daily_loss_stop_blocks_entries_only():
    risk = depth_bot.DailyRiskStop(limit_usd=90)
    assert risk.entries_allowed
    assert not risk.record_realized(-89.99)
    assert risk.record_realized(-0.01)
    assert risk.stopped
    assert not risk.entries_allowed
    assert risk.exits_allowed


def test_flatten_time_is_hard_stop():
    assert depth_bot.entries_allowed_at_minute(954)
    assert not depth_bot.entries_allowed_at_minute(955)
    assert depth_bot.flatten_due(955)
    assert not depth_bot.flatten_due(954)


# ----- arming / kill / risk ---------------------------------------------------
def test_unarmed_paper_fails_closed_before_any_broker_use(monkeypatch, tmp_path: Path):
    armed = tmp_path / "ARMED"
    broker = depth_bot.FakePaperBroker()
    monkeypatch.setattr(depth_bot, "ARMED_PATH", armed)
    with pytest.raises(RuntimeError, match="ARMED"):
        depth_bot.run_live(
            "paper", True, DAY, broker=broker, journal_root=tmp_path / "journal"
        )
    # The flag alone is equally insufficient.
    with pytest.raises(RuntimeError, match="ARMED"):
        depth_bot.run_live(
            "paper", False, DAY, broker=broker, journal_root=tmp_path / "journal"
        )
    assert broker.submitted == []


def test_armed_paper_with_kill_places_no_entry(monkeypatch, tmp_path: Path):
    kill = tmp_path / "KILL"
    kill.touch()
    broker = depth_bot.FakePaperBroker()
    engine, sim = make_engine(tmp_path, broker=broker, kill_path=kill)
    run_one_trade(engine)
    intents = [event for event in events(tmp_path / "journal.jsonl") if event["event"] == "intent"]
    assert intents and intents[0]["skip_reason"] == "kill_stop"
    assert broker.submitted == []


def test_kill_blocks_entries_but_never_exits(tmp_path: Path):
    kill = tmp_path / "KILL"
    engine, sim = make_engine(tmp_path, kill_path=kill)
    run_one_trade(engine)
    assert "AAA" in engine.positions, "the pre-KILL entry must be live before the test"
    kill.touch()
    due = engine.positions["AAA"].due_us
    push_quote(engine, "AAA", due - 200_000, 10.10, 10.15)
    push_quote(engine, "AAA", due + 300_000, 10.10, 10.15)
    assert engine.positions == {}
    sells = [order for order in sim.orders if order["side"] == "sell"]
    assert len(sells) == 1


def test_daily_loss_stop_blocks_new_entries_but_not_the_open_position(tmp_path: Path):
    engine, _sim = make_engine(tmp_path)
    run_one_trade(engine)
    engine.risk.record_realized(-depth_bot.LOSS_LIMIT_USD)
    assert engine.risk.stopped
    due = engine.positions["AAA"].due_us
    push_quote(engine, "AAA", due - 200_000, 10.10, 10.15)
    push_quote(engine, "AAA", due + 300_000, 10.10, 10.15)
    assert engine.positions == {}, "an open position must still exit under the loss stop"
    # A later intent for a different ticker is refused by the stop, not by the book.
    later = minute_us(DAY, 800)
    push_quote(engine, "BBB", later - 500_000, 5.00, 5.05)
    engine.pending_intents[800] = [intent_row("BBB", t=799)]
    engine._process_intent_clock(800)
    last = [event for event in events(tmp_path / "journal.jsonl") if event["event"] == "intent"][-1]
    assert last["ticker"] == "BBB"
    assert last["skip_reason"] == "daily_loss_stop"


def test_hard_flatten_at_1555_closes_a_position_that_could_not_price(tmp_path: Path):
    engine, sim = make_engine(tmp_path)
    run_one_trade(engine)
    position = engine.positions["AAA"]
    # No print anywhere near the scheduled exit: the working order never prices.
    engine.advance(minute_us(DAY, depth_bot.FLATTEN_MINUTE) + 1)
    assert position.forced, "the 15:55 flatten must force the exit"
    sells = [order for order in sim.orders if order["side"] == "sell"]
    assert len(sells) == 1


def test_restart_rebuilds_attempts_and_cooldown_from_the_journal(tmp_path: Path):
    engine, _sim = make_engine(tmp_path)
    run_one_trade(engine)
    due = engine.positions["AAA"].due_us
    push_quote(engine, "AAA", due - 200_000, 10.10, 10.15)
    push_quote(engine, "AAA", due + 300_000, 10.10, 10.15)
    assert engine.positions == {}
    exit_us = engine.cooldown_until["AAA"] - depth_bot.research.COOLDOWN_MIN * 60_000_000
    journal_file = tmp_path / "journal.jsonl"
    before = journal_file.read_text()

    cache = depth_bot.QuoteCache(DAY)
    sim = depth_bot.SimBroker(DAY, cache, minute_us(DAY, SESSION_END))
    restarted = depth_bot.DepthEngine(
        DAY,
        SESSION_END,
        None,
        sim,
        journal_file,
        quotes=cache,
        kill_path=None,
        fresh=False,
    )
    after = journal_file.read_text()
    assert after.startswith(before), "a restart must never rewrite the day journal"
    assert restarted.attempts["AAA"] == 1
    assert restarted.cooldown_until["AAA"] == exit_us + 15 * 60_000_000
    # Inside the cooldown window the same ticker cannot re-enter.
    inside = depth_bot.et_minute_of(exit_us) + 5
    push_quote(restarted, "AAA", minute_us(DAY, inside) - 500_000, 10.10, 10.15)
    restarted.pending_intents[inside] = [intent_row("AAA", t=inside - 1)]
    restarted._process_intent_clock(inside)
    assert [order for order in sim.orders if order["side"] == "buy"] == []


def test_restart_never_double_enters_an_adopted_broker_position(tmp_path: Path):
    adopted = {"AAA": {"qty": 50, "entry_price": 10.0, "entry_minute": 600, "exit_minute": 660}}
    broker = depth_bot.FakePaperBroker()
    engine, _sim = make_engine(tmp_path, broker=broker, open_positions=adopted)
    assert "AAA" in engine.positions
    push_quote(engine, "AAA", minute_us(DAY, 700) - 500_000, 10.0, 10.05)
    engine.pending_intents[700] = [intent_row("AAA", t=699)]
    engine._process_intent_clock(700)
    assert [order for order in broker.submitted if order["side"] == "buy"] == []
    last = [event for event in events(tmp_path / "journal.jsonl") if event["event"] == "intent"][-1]
    assert last["skip_reason"] == "overlap_skips"


# ----- parity against the frozen research replay ------------------------------
def assert_replay_parity(day: str, block: str, journal_root: Path) -> None:
    """The engine's own trades must equal the frozen chosen-arm trades, attempt by attempt."""
    research = depth_bot.research
    expected = research.pl.read_parquet(
        RESEARCH_OUTPUT / "replay" / block / f"{day}.trades.parquet"
    ).filter(research.pl.col("arm_key") == depth_bot.ARM.key)
    daily, trades = depth_bot.replay_day(day, RESEARCH_OUTPUT, research.DATA_ROOT, journal_root)
    assert daily["attempts"] == expected.height, (day, daily["attempts"], expected.height)
    assert len(trades) == expected.height, day
    references = expected.select(*PARITY_FIELDS).to_dicts()
    for actual, reference in zip(trades, references, strict=True):
        for key in PARITY_FIELDS:
            want = reference[key]
            got = actual.get(key)
            if key == "net_usd_25" or isinstance(want, float) or isinstance(got, float):
                if want is None or got is None:
                    assert got == want, (day, key, got, want)
                else:
                    assert abs(float(got) - float(want)) <= 1e-6, (day, key, got, want)
            else:
                assert got == want, (day, key, got, want)
    recorded = events(journal_root / f"{day}.jsonl")
    assert sum(event["event"] == "decision_tick" for event in recorded) > 0
    assert sum(event["event"] == "signal" for event in recorded) >= expected.height
    assert sum(event["event"] == "intent" for event in recorded) >= expected.height
    assert sum(event["event"] == "trade_result" for event in recorded) == expected.height


@pytest.mark.parametrize("day,block", PARITY_DAYS)
def test_replay_parity_against_frozen_research_outputs(day: str, block: str, tmp_path: Path):
    assert_replay_parity(day, block, tmp_path / "journal")


def traded_days() -> list[tuple[str, str]]:
    """Every day the chosen arm has >= 1 attempt on (the full-coverage law)."""
    days = []
    for block in ("validation", "confirmation"):
        for path in sorted((RESEARCH_OUTPUT / "replay" / block).glob("*.trades.parquet")):
            arm = pl.read_parquet(path, columns=["arm_key"])["arm_key"]
            if (arm == depth_bot.ARM.key).any():
                days.append((path.name.split(".")[0], block))
    shard = os.environ.get("DEPTH_PARITY_SHARD")
    if shard:
        index, count = (int(part) for part in shard.split("/"))
        days = [day for position, day in enumerate(days) if position % count == index]
    return days


SLOW_PARITY = os.environ.get("DEPTH_PARITY_SLOW") == "1"


@pytest.mark.slow
@pytest.mark.skipif(
    not SLOW_PARITY,
    reason="full-coverage parity sweep (~170 sessions): set DEPTH_PARITY_SLOW=1",
)
@pytest.mark.parametrize("day,block", traded_days(), ids=lambda value: str(value))
def test_replay_parity_every_traded_day(day: str, block: str, tmp_path: Path):
    assert_replay_parity(day, block, tmp_path / "journal")


# ----- shadow: the injected stream must reproduce the replay journal ----------
class FakeSIPStream:
    """Fake Alpaca stream: the same stored day, one bar/print per callback."""

    def __init__(self, bars: pl.DataFrame, quotes: pl.DataFrame, day: str):
        self.bars = bars
        self.quotes = quotes
        self.day = day

    def run(self, on_bar, on_quote) -> None:
        bar_rows = self.bars.sort(["et", "ticker"]).to_dicts()
        quote_rows = (
            self.quotes.with_columns(pl.col("ts_utc").cast(pl.Int64).alias("ts_us"))
            .sort(["ts_us", "symbol"])
            .to_dicts()
        )
        bar_i = quote_i = 0
        while bar_i < len(bar_rows) or quote_i < len(quote_rows):
            bar_clock = (
                depth_bot.minute_us(self.day, int(bar_rows[bar_i]["et"]) + 1)
                if bar_i < len(bar_rows)
                else None
            )
            quote_clock = int(quote_rows[quote_i]["ts_us"]) if quote_i < len(quote_rows) else None
            if bar_clock is not None and (quote_clock is None or bar_clock <= quote_clock):
                on_bar(pl.DataFrame([bar_rows[bar_i]]))
                bar_i += 1
            else:
                on_quote(quote_rows[quote_i])
                quote_i += 1


@pytest.fixture(scope="module")
def stored_day() -> tuple[pl.DataFrame, pl.DataFrame]:
    research = depth_bot.research
    data_root = research.DATA_ROOT
    bars = pl.read_parquet(data_root / "sip" / "net" / "bars" / f"{DAY}.parquet").select(
        "ticker", "et", "open", "high", "low", "close", "volume"
    )
    quotes = depth_bot.quote_day_frame(
        data_root, DAY, set(bars["ticker"].to_list()), research.SUPPLEMENT_ROOTS
    )
    return bars, quotes


@pytest.fixture(scope="module")
def replay_journal(tmp_path_factory, stored_day) -> tuple[Path, dict, list]:
    root = tmp_path_factory.mktemp("replay")
    daily, trades = depth_bot.replay_day(
        DAY, RESEARCH_OUTPUT, depth_bot.research.DATA_ROOT, root / "journal"
    )
    return root / "journal" / f"{DAY}.jsonl", daily, trades


def test_fake_stream_shadow_journal_equals_replay_journal(
    monkeypatch, tmp_path: Path, stored_day, replay_journal
):
    journal_file, replay_daily, replay_trades = replay_journal
    bars, quotes = stored_day
    monkeypatch.setattr(
        depth_bot, "AlpacaSIPStream", lambda *args, **kwargs: pytest.fail("no network")
    )
    # The offline replay is a what-if with no ops controls; shadow honours a real KILL.
    monkeypatch.setattr(depth_bot, "KILL_PATH", tmp_path / "KILL")
    shadow_root = tmp_path / "shadow"
    daily, trades = depth_bot.run_live(
        "shadow",
        False,
        DAY,
        source=FakeSIPStream(bars, quotes, DAY),
        journal_root=shadow_root,
    )
    assert daily["attempts"] == replay_daily["attempts"]
    assert len(trades) == len(replay_trades)
    shadow = [
        event
        for event in events(shadow_root / f"{DAY}.jsonl")
        if event["event"] != "live_session_complete"
    ]
    assert shadow == events(journal_file)


# ----- paper: the broker boundary is exercised with a fake broker -------------
def test_fake_paper_broker_receives_ioc_entry_and_market_exit(
    monkeypatch, tmp_path: Path, stored_day
):
    armed = tmp_path / "ARMED"
    armed.touch()
    monkeypatch.setattr(depth_bot, "ARMED_PATH", armed)
    monkeypatch.setattr(depth_bot, "KILL_PATH", tmp_path / "KILL")  # absent
    bars, quotes = stored_day
    broker = depth_bot.FakePaperBroker()
    daily, trades = depth_bot.run_live(
        "paper",
        True,
        DAY,
        source=FakeSIPStream(bars, quotes, DAY),
        broker=broker,
        journal_root=tmp_path / "paper",
    )
    entries = [order for order in broker.submitted if order["side"] == "buy"]
    assert len(entries) == daily["attempts"] == len(trades)
    assert all(order["client_order_id"].startswith("depthbot-b-") for order in entries)
    assert all(order["limit_price"] > 0 for order in entries)
    for order in entries:
        assert order["client_order_id"] == f"depthbot-b-{order['ticker']}-{order['submit_us']}"
    journal = events(tmp_path / "paper" / f"{DAY}.jsonl")
    assert any(
        event["event"] == "order_submitted" and event["side"] == "entry" for event in journal
    )
    assert any(event["event"] == "daily_summary" for event in journal)
    trades = [event for event in journal if event["event"] == "trade_result"]
    assert trades and all(trade["parity_excluded"] for trade in trades)
    assert all("mode_paper" in trade["parity_excluded_reason"] for trade in trades)
    assert trades[0]["broker_order_id"], "a paper fill records the broker order id"
    # PRE-REG-DEPTH-01 C(i)-(iv): the sim's would-be verdict sits beside the broker fields.
    for trade in trades:
        assert trade["sim_entry_status"] is not None
        assert trade["sim_fill_status"] is not None
        assert trade["sim_entry_ask"] is not None
        assert trade["sim_would_be_unknown"] is not None
        if trade["sim_fill_status"] == "conditional_fill":
            assert trade["sim_exit_bid"] is not None
            assert trade["sim_net_usd_25"] is not None
        else:
            assert trade["sim_would_be_unknown"] is True


# ----- (1) universe sweep: REST repair + refusal ------------------------------
class FakeHistory:
    """HistorySource stand-in: serves in-memory rows, counts calls, can fail."""

    def __init__(self, bar_rows=None, quote_rows=None, fail=False):
        self.bar_rows = list(bar_rows or [])
        self.quote_rows = list(quote_rows or [])
        self.fail = fail
        self.bar_calls = []
        self.quote_calls = []

    def bars(self, day, tickers, start_us, end_us):
        self.bar_calls.append((tuple(tickers), int(start_us), int(end_us)))
        if self.fail:
            raise RuntimeError("rest down")
        return [dict(row) for row in self.bar_rows]

    def quotes(self, day, tickers, start_us, end_us):
        self.quote_calls.append((tuple(tickers), int(start_us), int(end_us)))
        if self.fail:
            raise RuntimeError("rest down")
        return [dict(row) for row in self.quote_rows]


def test_token_bucket_limits_rest_requests():
    clock = {"t": 0.0}
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["t"] += seconds

    bucket = depth_bot.TokenBucket(per_minute=60, clock=lambda: clock["t"], sleep=fake_sleep)
    for _ in range(60):
        bucket.acquire()
    assert sleeps == []
    bucket.acquire()
    assert sleeps and sleeps[0] > 0, "the 61st request in a minute must wait for a token"


def test_stale_universe_sweep_refuses_the_session(tmp_path: Path):
    alerts = tmp_path / "ALERT"
    engine, sim = make_engine(tmp_path, alert_path=alerts, universe=["AAA", "BBB"])
    engine.on_bar(pl.DataFrame([bar("AAA", 570), bar("AAA", 571), bar("BBB", 571)]))
    engine.advance(minute_us(DAY, depth_bot.LATTICE_START) + 3_000_000)
    assert engine.refusal == "universe_stale"
    journal = events(tmp_path / "journal.jsonl")
    assert any(event["event"] == "universe_refused" for event in journal)
    alert = json.loads(alerts.read_text().splitlines()[-1])
    assert alert["kind"] == "universe_refused"
    # A refused session keeps its exits but never opens a new position.
    push_quote(engine, "AAA", minute_us(DAY, 600) - 500_000, 10.0, 10.05)
    engine.pending_intents[600] = [intent_row("AAA", t=599)]
    engine._process_intent_clock(600)
    last = [event for event in events(tmp_path / "journal.jsonl") if event["event"] == "intent"][-1]
    assert last["skip_reason"] == "universe_refused"
    assert sim.orders == []


def test_rest_repair_avoids_a_refusal(tmp_path: Path):
    history = FakeHistory(bar_rows=[bar("AAA", 573), bar("AAA", 574)])
    engine, _sim = make_engine(tmp_path, history=history, universe=["AAA", "BBB"])
    engine.on_bar(
        pl.DataFrame(
            [
                bar("AAA", 570),
                bar("AAA", 571),
                bar("AAA", 572),
                bar("BBB", 570),
                bar("BBB", 571),
                bar("BBB", 572),
            ]
        )
    )
    engine.advance(minute_us(DAY, depth_bot.LATTICE_START) + 3_000_000)
    assert engine.refusal is None
    assert engine.repairs == 1
    assert history.bar_calls[0][0] == ("AAA", "BBB"), "the repair must batch the universe"
    assert any(
        event["event"] == "snapshot_coverage"
        for event in events(tmp_path / "journal.jsonl")
    )


def test_failed_repair_raises_an_alert_and_refuses(tmp_path: Path):
    alerts = tmp_path / "ALERT"
    history = FakeHistory(fail=True)
    engine, _sim = make_engine(
        tmp_path, history=history, universe=["AAA"], alert_path=alerts
    )
    engine.on_bar(pl.DataFrame([bar("AAA", 570), bar("AAA", 571)]))
    engine.advance(minute_us(DAY, depth_bot.LATTICE_START) + 3_000_000)
    assert engine.refusal == "universe_stale"
    kinds = [json.loads(line)["kind"] for line in alerts.read_text().splitlines()]
    assert "backfill_failed" in kinds and "universe_refused" in kinds


# ----- (3) partial exit policy ------------------------------------------------
class PartialExitBroker(depth_bot.FakePaperBroker):
    """Fills the exit in two tranches, then the rest (the live-only partial state)."""

    def __init__(self, first_fill: int):
        super().__init__()
        self.first_fill = int(first_fill)
        self.exit_calls = 0

    def poll_exit(self, order, now_us):
        self.exit_calls += 1
        quantity = int(order["qty"])
        if self.exit_calls == 1:
            return {
                "state": "closed",
                "exit_status": "conditional_market_fill",
                "exit_submit_us": int(order["submit_us"]),
                "exit_arrival_us": int(order["submit_us"]),
                "actual_exit_us": int(order["submit_us"]) + 60_000,
                "filled_qty": self.first_fill,
                "filled_avg_price": 10.20,
            }
        return {
            "state": "closed",
            "exit_status": "conditional_market_fill",
            "exit_submit_us": int(order["submit_us"]),
            "exit_arrival_us": int(order["submit_us"]),
            "actual_exit_us": int(order["submit_us"]) + 60_000,
            "filled_qty": quantity,
            "filled_avg_price": 10.20,
        }


def test_partial_exit_keeps_working_the_remainder(tmp_path: Path):
    broker = PartialExitBroker(first_fill=40)
    engine, _sim = make_engine(tmp_path, broker=broker)
    run_one_trade(engine)
    position = engine.positions["AAA"]
    quantity = position.qty
    due = position.due_us
    push_quote(engine, "AAA", due - 200_000, 10.10, 10.15)
    push_quote(engine, "AAA", due + 300_000, 10.10, 10.15)  # submits at due, fills 40
    assert engine.positions["AAA"].qty == quantity - 40
    push_quote(engine, "AAA", due + 900_000, 10.10, 10.15)  # works the remaining 57
    journal = events(tmp_path / "journal.jsonl")
    partials = [event for event in journal if event["event"] == "exit_partial"]
    assert len(partials) == 1
    assert partials[0]["qty"] == 40
    assert partials[0]["remaining"] == quantity - 40
    sells = [order["qty"] for order in broker.submitted if order["side"] == "sell"]
    assert sells == [quantity, quantity - 40], "the remainder needs its own marketable order"
    assert engine.positions == {}
    closed = [event for event in journal if event["event"] == "position_closed"][-1]
    assert closed["qty"] == quantity
    engine.finish()
    journal = events(tmp_path / "journal.jsonl")
    trade = [event for event in journal if event["event"] == "trade_result"][-1]
    assert trade["parity_excluded"] is True
    assert "partial_exit" in trade["parity_excluded_reason"]
    assert trade["realized_pnl_usd"] == pytest.approx(
        quantity * (10.20 - float(position.entry_price))
    )


# ----- (4) alerting + supervisor ---------------------------------------------
def test_loss_stop_alerts_to_the_alert_file(tmp_path: Path):
    alerts = tmp_path / "ALERT"
    engine, _sim = make_engine(
        tmp_path, alert_path=alerts, risk=depth_bot.DailyRiskStop(limit_usd=1.0)
    )
    run_one_trade(engine)
    due = engine.positions["AAA"].due_us
    push_quote(engine, "AAA", due - 200_000, 9.00, 9.05)
    push_quote(engine, "AAA", due + 300_000, 9.00, 9.05)
    kinds = [json.loads(line)["kind"] for line in alerts.read_text().splitlines()]
    assert "loss_stop" in kinds
    assert any(event["event"] == "risk_stop" for event in events(tmp_path / "journal.jsonl"))


def test_supervisor_restarts_on_crash_and_stops_on_kill(tmp_path: Path):
    kill = tmp_path / "KILL"
    alerts = tmp_path / "ALERT"
    calls = []

    def runner():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("boom")

    code = depth_bot.supervise(
        runner, kill_path=kill, alert_path=alerts, sleep=lambda _s: None, max_sessions=1
    )
    assert code == 0
    assert len(calls) == 3, "two crashes must be restarted, then the run completes"
    kinds = [json.loads(line)["kind"] for line in alerts.read_text().splitlines()]
    assert kinds.count("crash") == 2
    kill.touch()
    calls.clear()
    depth_bot.supervise(runner, kill_path=kill, alert_path=alerts, sleep=lambda _s: None)
    assert calls == [], "data/KILL must stop the supervisor"


def test_supervisor_gives_up_after_max_restarts(tmp_path: Path):
    def runner():
        raise RuntimeError("always")

    alerts = tmp_path / "ALERT"
    code = depth_bot.supervise(
        runner,
        kill_path=tmp_path / "KILL",
        alert_path=alerts,
        sleep=lambda _s: None,
        max_restarts=1,
    )
    assert code == 2
    kinds = [json.loads(line)["kind"] for line in alerts.read_text().splitlines()]
    assert kinds[-1] == "supervisor_giving_up"


def test_unexpected_broker_state_alerts(tmp_path: Path):
    class RejectedEntryBroker(depth_bot.FakePaperBroker):
        def poll_entry(self, order, now_us):
            return {
                "state": "unfilled",
                "entry_status": "rejected",
                "release_us": int(order["submit_us"]),
            }

    alerts = tmp_path / "ALERT"
    engine, _sim = make_engine(tmp_path, broker=RejectedEntryBroker(), alert_path=alerts)
    run_one_trade(engine)
    kinds = [json.loads(line)["kind"] for line in alerts.read_text().splitlines()]
    assert "broker_unexpected" in kinds


# ----- (5) day roll + states persistence -------------------------------------
def test_day_roll_closes_a_session_a_crash_left_open(tmp_path: Path):
    journal = tmp_path / "journal.jsonl"
    depth_bot.append_event(journal, {"event": "session_start", "day": DAY})
    alerts = tmp_path / "ALERT"
    make_engine(tmp_path, alert_path=alerts, fresh=False)
    journal_events = events(journal)
    rolled = [event for event in journal_events if event.get("rolled_over")]
    assert rolled and rolled[0]["event"] == "session_end"
    assert journal_events[-1]["event"] == "session_start"
    assert "previous_session_incomplete" in [
        json.loads(line)["kind"] for line in alerts.read_text().splitlines()
    ]


def test_replay_persists_the_states_parquet(replay_journal, tmp_path: Path):
    journal_file, _daily, trades = replay_journal
    states_path = journal_file.parent.parent / "states" / f"{DAY}.parquet"
    assert states_path.is_file()
    frame = pl.read_parquet(states_path)
    assert tuple(frame.columns) == tuple(depth_bot.research.STATE_COLUMNS)
    assert frame.height >= len(trades)
    journaled = {event["ticker"] for event in events(journal_file) if event["event"] == "signal"}
    assert set(frame["ticker"].to_list()) <= journaled


# ----- (2) restart backfill: identical later decisions ------------------------
class FrameHistory:
    """HistorySource over the stored day's frames (no network, no copies per call)."""

    def __init__(self, bars: pl.DataFrame, quotes: pl.DataFrame):
        self.bars_frame = bars
        self.quotes_frame = quotes.with_columns(
            pl.col("ts_utc").cast(pl.Int64).alias("ts_us")
        )
        self.bar_calls = 0
        self.quote_calls = 0

    def bars(self, day, tickers, start_us, end_us):
        self.bar_calls += 1
        lo = depth_bot.et_minute_of(start_us)
        hi = depth_bot.et_minute_of(end_us)
        return self.bars_frame.filter(pl.col("et").is_between(lo, hi)).to_dicts()

    def quotes(self, day, tickers, start_us, end_us):
        self.quote_calls += 1
        return (
            self.quotes_frame.filter(
                pl.col("symbol").is_in(list(tickers)), pl.col("ts_us") <= int(end_us)
            )
            .drop("ts_us")
            .to_dicts()
        )


@pytest.fixture(scope="module")
def h60_model():
    return depth_bot.depth_bot_features.load_model()


def test_restart_at_11_reproduces_later_decisions(
    tmp_path: Path, stored_day, replay_journal, h60_model
):
    reference_journal, _daily, _trades = replay_journal
    bars, quotes = stored_day
    resume_us = depth_bot.minute_us(DAY, 660)  # 11:00 ET
    cache = depth_bot.QuoteCache(DAY)
    sim = depth_bot.SimBroker(DAY, cache, depth_bot.minute_us(DAY, SESSION_END))
    journal = tmp_path / "restart" / f"{DAY}.jsonl"
    engine = depth_bot.DepthEngine(
        DAY,
        SESSION_END,
        h60_model,
        sim,
        journal,
        quotes=cache,
        kill_path=None,
        fresh=True,
    )
    history = FrameHistory(bars, quotes)
    engine.history = history
    engine.universe = sorted(set(bars["ticker"].to_list()))
    engine.bootstrap(resume_us=resume_us)
    assert history.bar_calls == 1 and history.quote_calls == 1
    # Feed the remainder of the same day through the normal stream callbacks.
    remainder = bars.filter(pl.col("et") >= 659)
    depth_bot.ReplayStreamSource(
        remainder,
        quotes.filter(pl.col("ts_utc").cast(pl.Int64) > resume_us),
        DAY,
    ).run(engine.on_bar, engine.on_quote)
    engine.finish()

    def after_resume(path: Path) -> list[dict]:
        kept = []
        for event in events(path):
            if event["event"] in {"decision_tick", "signal"}:
                fresh = depth_bot.minute_us(DAY, int(event["t"])) >= resume_us
            elif event["event"] in {"intent", "trade_result"}:
                fresh = int(event.get("intent_us", 0)) > resume_us
            else:
                continue
            if fresh:
                kept.append(event)
        return kept

    restarted = after_resume(journal)
    reference = after_resume(reference_journal)
    assert [event for event in restarted if event["event"] == "decision_tick"] == [
        event for event in reference if event["event"] == "decision_tick"
    ]
    assert [event for event in restarted if event["event"] == "signal"] == [
        event for event in reference if event["event"] == "signal"
    ]
    restarted_trades = [event for event in restarted if event["event"] == "trade_result"]
    reference_trades = [event for event in reference if event["event"] == "trade_result"]
    assert restarted_trades == reference_trades
    assert any(event["event"] == "session_resumed" for event in events(journal))


# ----- live stream lifecycle (worker thread, queued subscriptions) -----------
class FakeSocket:
    """alpaca-py DataStream stand-in: dispatches once, then blocks until stop()."""

    def __init__(self, key, secret):
        self.key = key
        self.stopped = False
        self.subscriptions = []
        self.run_thread = None

    def subscribe_bars(self, handler, *symbols):
        self.bar_handler = handler
        self.universe = symbols

    def subscribe_quotes(self, handler, *symbols):
        self.subscriptions.append((tuple(symbols), threading.get_ident()))
        self.quote_handler = handler

    def run(self):
        self.run_thread = threading.get_ident()
        stamp = datetime.now(ZoneInfo("UTC"))
        asyncio.run(
            self.bar_handler(
                SimpleNamespace(
                    symbol="AAA",
                    timestamp=stamp,
                    open=10.0,
                    high=10.0,
                    low=10.0,
                    close=10.0,
                    volume=100.0,
                )
            )
        )
        while not self.stopped and not hasattr(self, "quote_handler"):
            time.sleep(0.005)
        if hasattr(self, "quote_handler"):
            asyncio.run(
                self.quote_handler(
                    SimpleNamespace(
                        symbol="BBB",
                        timestamp=stamp,
                        bid_price=10.0,
                        ask_price=10.05,
                        bid_size=100.0,
                        ask_size=100.0,
                        bid_exchange="Q",
                        ask_exchange="Q",
                        conditions=["R"],
                    )
                )
            )
        while not self.stopped:
            time.sleep(0.005)

    def stop(self):
        self.stopped = True


def test_stream_subscribes_off_thread_and_stops_at_session_end():
    caller = threading.get_ident()
    sockets = []

    def factory(key, secret):
        socket = FakeSocket(key, secret)
        sockets.append(socket)
        return socket

    source = depth_bot.AlpacaSIPStream(
        "key",
        "secret",
        ["UNIV"],
        session_end_us=int(time.time() * 1_000_000) + 400_000,
        stream_factory=factory,
        poll_s=0.01,
    )
    source.run(lambda _frame: {"AAA"}, lambda _quote: {"BBB"})
    socket = sockets[0]
    assert socket.run_thread != caller, "the socket must run on a worker thread"
    assert socket.subscriptions, "admissions must be subscribed"
    assert all(
        thread == caller for _names, thread in socket.subscriptions
    ), "subscribe_quotes must never be called from a stream handler thread"
    assert {name for names, _ in socket.subscriptions for name in names} == {"AAA", "BBB"}
    assert source.subscribed == {"AAA", "BBB"}
    assert source.stop_reason == "session_end"
    assert socket.stopped, "the socket must be stopped at session_end"


def test_quote_driven_admission_is_returned_for_subscription(tmp_path: Path, h60_model):
    engine, _sim = make_engine(tmp_path, universe=["AAA"], model=h60_model)
    rows = [
        bar("AAA", minute, close=10.0 + max(0, minute - 576) * 0.5) for minute in range(570, 585)
    ]
    assert engine.on_bar(pl.DataFrame(rows)) == set(), "no admission before the 585 snapshot"
    # The 09:45 snapshot decision is computed on a QUOTE advance, not on a bar.
    added = engine.on_quote(quote("AAA", minute_us(DAY, 586) + 2_000_000, 13.4, 13.5))
    assert "AAA" in added
    assert "AAA" in engine.admitted


# ----- paper exit lifecycle ---------------------------------------------------
class CanceledExitBroker(depth_bot.FakePaperBroker):
    """The venue cancels the first exit order; the retry is marketable and fills."""

    def __init__(self):
        super().__init__()
        self.exit_polls = 0

    def poll_exit(self, order, now_us):
        self.exit_polls += 1
        if self.exit_polls == 1:
            return {
                "state": "retry",
                "exit_status": "exit_canceled",
                "broker_status": "canceled",
                "exit_submit_us": int(order["submit_us"]),
                "actual_exit_us": int(now_us),
            }
        return super().poll_exit(order, now_us)


class StuckExitBroker(depth_bot.FakePaperBroker):
    """The exit never fills: the position must stay open and alert, never be dropped."""

    def poll_exit(self, order, now_us):
        return None

    def close_exit(self, order, session_end_us):
        return {
            "state": "open",
            "exit_status": "exit_order_still_working",
            "actual_exit_us": int(session_end_us) + 1,
        }


def test_canceled_exit_is_retried_as_marketable(tmp_path: Path):
    alerts = tmp_path / "ALERT"
    broker = CanceledExitBroker()
    engine, _sim = make_engine(tmp_path, broker=broker, alert_path=alerts)
    run_one_trade(engine)
    due = engine.positions["AAA"].due_us
    push_quote(engine, "AAA", due - 200_000, 10.10, 10.15)
    push_quote(engine, "AAA", due + 300_000, 10.10, 10.15)  # submitted, canceled at the venue
    # The retry waits for the exponential backoff (2 s), then fills.
    push_quote(engine, "AAA", due + 2_500_000, 10.10, 10.15)
    push_quote(engine, "AAA", due + 3_000_000, 10.10, 10.15)
    journal = events(tmp_path / "journal.jsonl")
    assert [event["event"] for event in journal].count("exit_retry") == 1
    sells = [order for order in broker.submitted if order["side"] == "sell"]
    assert len(sells) == 2, "a canceled exit must be worked again"
    assert engine.positions == {}
    kinds = [json.loads(line)["kind"] for line in alerts.read_text().splitlines()]
    assert "exit_retry" in kinds


def test_unfilled_exit_keeps_the_position_and_alerts(tmp_path: Path):
    alerts = tmp_path / "ALERT"
    broker = StuckExitBroker()
    engine, _sim = make_engine(tmp_path, broker=broker, alert_path=alerts)
    run_one_trade(engine)
    due = engine.positions["AAA"].due_us
    push_quote(engine, "AAA", due - 200_000, 10.10, 10.15)
    push_quote(engine, "AAA", due + 300_000, 10.10, 10.15)
    engine.finish()
    assert engine.positions, "an unfilled real exit must not be booked flat"
    journal = events(tmp_path / "journal.jsonl")
    assert not any(event["event"] == "position_closed" for event in journal)
    kinds = [json.loads(line)["kind"] for line in alerts.read_text().splitlines()]
    assert "position_not_flattened" in kinds


# ----- round 3: stream early return, sim independence, retries, adoption ------
class DyingSocket(FakeSocket):
    """The venue closes the socket immediately (subscription error path)."""

    def run(self):
        self.run_thread = threading.get_ident()


def test_socket_return_before_session_end_alerts_and_reconnects(tmp_path: Path):
    alerts = tmp_path / "ALERT"
    sockets = []

    def factory(key, secret):
        socket = DyingSocket(key, secret) if not sockets else FakeSocket(key, secret)
        sockets.append(socket)
        return socket

    source = depth_bot.AlpacaSIPStream(
        "k",
        "s",
        ["UNIV"],
        session_end_us=int(time.time() * 1_000_000) + 600_000,
        stream_factory=factory,
        poll_s=0.01,
        sleep=lambda _s: None,
        alert_path=alerts,
        max_reconnects=3,
        backoff_s=0.01,
    )
    source.run(lambda _frame: {"AAA"}, lambda _quote: set())
    assert len(sockets) >= 2, "an early return must be retried on a fresh socket"
    assert source.stop_reason == "session_end"
    kinds = [json.loads(line)["kind"] for line in alerts.read_text().splitlines()]
    assert "stream_ended_early" in kinds


def test_socket_reconnect_exhaustion_raises(tmp_path: Path):
    alerts = tmp_path / "ALERT"
    sockets = []

    def factory(key, secret):
        socket = DyingSocket(key, secret)
        sockets.append(socket)
        return socket

    source = depth_bot.AlpacaSIPStream(
        "k",
        "s",
        ["UNIV"],
        session_end_us=int(time.time() * 1_000_000) + 5_000_000,
        stream_factory=factory,
        poll_s=0.005,
        sleep=lambda _s: None,
        alert_path=alerts,
        max_reconnects=2,
        backoff_s=0.0,
    )
    with pytest.raises(depth_bot.StreamEndedEarlyError):
        source.run(lambda _frame: set(), lambda _quote: set())
    assert source.stop_reason == "reconnect_exhausted"
    assert len(sockets) == 3  # initial + max_reconnects
    kinds = [json.loads(line)["kind"] for line in alerts.read_text().splitlines()]
    assert kinds.count("stream_ended_early") == 3
    assert kinds[-1] == "stream_reconnect_exhausted"


def test_run_live_never_writes_a_completed_day_after_an_early_end(tmp_path: Path):
    class DeadSource:
        stop_reason = "reconnect_exhausted"

        def __init__(self, bars):
            self.bars = bars

        def run(self, on_bar, on_quote):
            return None

    alerts = tmp_path / "ALERT"
    (tmp_path / "ARMED").touch()
    with pytest.raises(depth_bot.StreamEndedEarlyError):
        depth_bot.run_live(
            "shadow",
            False,
            DAY,
            source=DeadSource(pl.DataFrame({"ticker": ["AAA"]})),
            journal_root=tmp_path / "shadow",
            alert_path=alerts,
        )
    journal = events(tmp_path / "shadow" / f"{DAY}.jsonl")
    kinds = {event["event"] for event in journal}
    assert "session_incomplete" in kinds
    assert "session_end" not in kinds and "daily_summary" not in kinds
    assert "session_incomplete" in [
        json.loads(line)["kind"] for line in alerts.read_text().splitlines()
    ]


def test_stream_subscribes_every_admission_from_its_thread(tmp_path: Path):
    class BurstSocket(FakeSocket):
        """Many admissions dispatched from the loop thread while the caller drains."""

        def run(self):
            self.run_thread = threading.get_ident()
            stamp = datetime.now(ZoneInfo("UTC"))
            for index in range(60):
                asyncio.run(
                    self.bar_handler(
                        SimpleNamespace(
                            symbol=f"S{index:02d}",
                            timestamp=stamp,
                            open=1.0,
                            high=1.0,
                            low=1.0,
                            close=1.0,
                            volume=1.0,
                        )
                    )
                )
            while not self.stopped:
                time.sleep(0.005)

    sockets = []

    def factory(key, secret):
        socket = BurstSocket(key, secret)
        sockets.append(socket)
        return socket

    source = depth_bot.AlpacaSIPStream(
        "k",
        "s",
        ["UNIV"],
        session_end_us=int(time.time() * 1_000_000) + 500_000,
        stream_factory=factory,
        poll_s=0.005,
    )
    source.run(lambda frame: {str(frame["ticker"][0])}, lambda _quote: set())
    subscribed = {name for names, _ in sockets[0].subscriptions for name in names}
    assert subscribed == {f"S{index:02d}" for index in range(60)}


def test_loss_stop_default_is_the_amended_300():
    assert depth_bot.DailyRiskStop().limit_usd == 300.0


class FillingPaperBroker(depth_bot.FakePaperBroker):
    """Fills whatever the sim says (or nothing), to force the divergence cases."""

    def __init__(self, fill: bool = True):
        super().__init__()
        self.fill = fill

    def poll_entry(self, order, now_us):
        if not self.fill:
            return {
                "state": "unfilled",
                "entry_status": "canceled",
                "release_us": int(order["submit_us"]),
            }
        return super().poll_entry(order, now_us)


def test_sim_no_match_with_a_real_fill_keeps_the_sim_verdict(tmp_path: Path):
    engine, _sim = make_engine(tmp_path, broker=FillingPaperBroker(fill=True))
    intent_us = minute_us(DAY, 601)
    push_quote(engine, "AAA", intent_us - 500_000, 10.00, 10.05)
    # The arrival book is 30 cents wide of the limit: the simulated IOC cannot execute.
    push_quote(engine, "AAA", intent_us + 200_000, 10.30, 10.40)
    engine.pending_intents[601] = [intent_row("AAA", t=600)]
    engine._process_intent_clock(601)
    engine.advance(intent_us + 400_000)
    engine.finish()
    results = list(events(tmp_path / "journal.jsonl"))
    trade = [event for event in results if event["event"] == "trade_result"][-1]
    assert trade["sim_fill_status"] == "no_match_at_l1_unfilled_cash"
    assert trade["sim_would_be_unknown"] is False
    assert trade["sim_exit_bid"] is None and trade["sim_net_usd_25"] is None
    assert trade["broker_fill_price"] is not None, "the real fill is recorded separately"
    assert trade["broker_exit_price"] != trade["broker_fill_price"]


def test_sim_fill_with_a_real_no_fill_still_gets_the_sim_exit(tmp_path: Path):
    engine, _sim = make_engine(tmp_path, broker=FillingPaperBroker(fill=False))
    intent_us = minute_us(DAY, 601)
    push_quote(engine, "AAA", intent_us - 500_000, 10.00, 10.05)
    engine.pending_intents[601] = [intent_row("AAA", t=600)]
    engine._process_intent_clock(601)
    engine.advance(intent_us + 400_000)
    # The simulated arm holds a position to 661: drive it and settle at the close.
    sim_due = minute_us(DAY, 661)
    push_quote(engine, "AAA", sim_due - 200_000, 10.10, 10.15)
    push_quote(engine, "AAA", sim_due + 300_000, 10.10, 10.15)
    engine.finish()
    results = list(events(tmp_path / "journal.jsonl"))
    trade = [event for event in results if event["event"] == "trade_result"][-1]
    assert trade["fill_status"] == "no_match_at_l1_unfilled_cash"
    assert trade["sim_fill_status"] == "conditional_fill"
    assert trade["sim_exit_bid"] == pytest.approx(10.10)
    assert trade["sim_net_usd_25"] is not None
    assert trade["broker_fill_price"] is None


def test_position_open_at_the_close_still_gets_a_trade_result(tmp_path: Path):
    engine, _sim = make_engine(tmp_path, broker=StuckExitBroker())
    run_one_trade(engine)
    due = engine.positions["AAA"].due_us
    push_quote(engine, "AAA", due - 200_000, 10.10, 10.15)
    push_quote(engine, "AAA", due + 300_000, 10.10, 10.15)
    engine.finish()
    journal = list(events(tmp_path / "journal.jsonl"))
    results = [event for event in journal if event["event"] == "trade_result"]
    assert results[-1]["fill_status"] == "unresolved_open"
    assert "unresolved_open" in results[-1]["parity_excluded_reason"]


class AlwaysRetryBroker(depth_bot.FakePaperBroker):
    """Every exit order dies without a fill."""

    def poll_exit(self, order, now_us):
        return {
            "state": "retry",
            "exit_status": "exit_rejected",
            "broker_status": "rejected",
            "exit_submit_us": int(order["submit_us"]),
            "actual_exit_us": int(now_us),
        }


def test_exit_retry_is_capped_with_backoff(tmp_path: Path):
    alerts = tmp_path / "ALERT"
    broker = AlwaysRetryBroker()
    engine, _sim = make_engine(tmp_path, broker=broker, alert_path=alerts)
    run_one_trade(engine)
    due = engine.positions["AAA"].due_us
    push_quote(engine, "AAA", due - 200_000, 10.10, 10.15)
    for step in range(1, 90):  # doubling backoff, so a long clock is needed to hit the cap
        push_quote(engine, "AAA", due + step * 8_000_000 - 100_000, 10.10, 10.15)
        engine.advance(due + step * 8_000_000)
    sells = [order for order in broker.submitted if order["side"] == "sell"]
    assert len(sells) <= depth_bot.EXIT_RETRY_MAX + 1, "a dead exit must not loop on orders"
    assert engine.positions["AAA"].retry_blocked
    kinds = [json.loads(line)["kind"] for line in alerts.read_text().splitlines()]
    assert "exit_retry_exhausted" in kinds
    journal = events(tmp_path / "journal.jsonl")
    assert any(event["event"] == "exit_retry_exhausted" for event in journal)


def test_reconcile_uses_the_broker_fill_clock_and_price(tmp_path: Path):
    journal = tmp_path / "journal.jsonl"
    fills = {
        "OVNT": {
            "entry_minute": 640,
            "entry_price": 4.25,
            "client_order_id": "depthbot-b-OVNT-1",
            "filled_at_us": minute_us(DAY, 640),
        }
    }
    adopted = depth_bot.reconcile_positions(
        journal, {"OVNT": {"qty": 100, "entry_price": 4.20}}, fills
    )
    assert adopted["OVNT"]["entry_minute"] == 640
    assert adopted["OVNT"]["entry_price"] == 4.25
    assert adopted["OVNT"]["entry_clock_known"] is True
    # Adopted into a live engine, the position sells at once with a real entry clock.
    engine, _sim = make_engine(tmp_path, open_positions=adopted)
    assert engine.positions["OVNT"].entry_minute == 640
    assert engine.positions["OVNT"].record["broker_fill_price"] == 4.25


def test_adopted_position_without_a_broker_clock_is_flagged(tmp_path: Path, h60_model):
    engine, _sim = make_engine(
        tmp_path, open_positions={"MYST": {"qty": 10, "entry_price": 7.0}}
    )
    engine.history = FakeHistory()
    engine.bootstrap(resume_us=minute_us(DAY, 660))
    position = engine.positions["MYST"]
    assert position.entry_minute == 660, "no 01:00 skeleton"
    assert position.record["late_exit"] is True
    assert "entry_clock_unknown" in position.record["parity_excluded_reason"]


# ----- round 4: overdue adoption, closed-event prices, supervisor, checklist --
def test_adopted_position_past_due_exits_immediately_and_alerts(tmp_path: Path):
    alerts = tmp_path / "ALERT"
    broker = depth_bot.FakePaperBroker()
    adopted = {"OVNT": {"qty": 50, "entry_price": 10.0, "entry_minute": 600, "exit_minute": 660}}
    engine, _sim = make_engine(tmp_path, broker=broker, open_positions=adopted, alert_path=alerts)
    push_quote(engine, "OVNT", minute_us(DAY, 700), 10.0, 10.05)
    position = engine.positions["OVNT"]
    assert position.forced is True
    assert position.record["late_exit"] is True
    assert [order["side"] for order in broker.submitted] == ["sell"]
    kinds = [json.loads(line)["kind"] for line in alerts.read_text().splitlines()]
    assert "late_exit" in kinds


def test_position_closed_reports_exit_price_and_sim_slippage(tmp_path: Path):
    broker = depth_bot.FakePaperBroker(exit_price=10.12)
    engine, _sim = make_engine(tmp_path, broker=broker)
    run_one_trade(engine)
    due = engine.positions["AAA"].due_us
    push_quote(engine, "AAA", due - 200_000, 10.10, 10.15)
    push_quote(engine, "AAA", due + 300_000, 10.10, 10.15)
    engine.finish()
    journal = list(events(tmp_path / "journal.jsonl"))
    closed = [event for event in journal if event["event"] == "position_closed"][-1]
    assert closed["broker_exit_price"] == pytest.approx(10.12)
    assert closed["sim_exit_bid"] == pytest.approx(10.10)
    assert closed["exit_slippage"] == pytest.approx(10.12 - 10.10)
    assert closed["entry_slippage"] == pytest.approx(10.16 - 10.05)


def test_supervisor_backoff_doubles_and_gives_up(tmp_path: Path):
    delays = []

    def runner():
        raise RuntimeError("always")

    alerts = tmp_path / "ALERT"
    code = depth_bot.supervise(
        runner,
        kill_path=tmp_path / "KILL",
        alert_path=alerts,
        sleep=delays.append,
        backoff_s=5.0,
        max_restarts=3,
    )
    assert code == 2
    assert delays == [5.0, 10.0, 20.0]
    kinds = [json.loads(line)["kind"] for line in alerts.read_text().splitlines()]
    assert kinds[-1] == "supervisor_giving_up"


def test_checklist_passes_a_clean_shadow_day(tmp_path: Path, stored_day, monkeypatch):
    bars, quotes = stored_day
    monkeypatch.setattr(depth_bot, "KILL_PATH", tmp_path / "KILL")
    monkeypatch.setattr(depth_bot, "ARMED_PATH", tmp_path / "ARMED")
    shadow_root = tmp_path / "shadow"
    depth_bot.run_live(
        "shadow",
        False,
        DAY,
        source=FakeSIPStream(bars, quotes, DAY),
        journal_root=shadow_root,
    )
    checks = depth_bot.build_checklist(
        DAY, journal_root=shadow_root, bot_root=tmp_path, states_root=None
    )
    failed = [(check["item"], check["detail"]) for check in checks if not check["ok"]]
    assert not failed, failed
    assert len(checks) == 6


def test_checklist_fails_an_incomplete_day(tmp_path: Path):
    journal = tmp_path / "journal" / f"{DAY}.jsonl"
    depth_bot.append_event(journal, {"event": "session_start", "day": DAY, "mode": "sim"})
    depth_bot.append_event(journal, {"event": "session_incomplete", "day": DAY, "reason": "x"})
    checks = depth_bot.build_checklist(
        DAY, journal_root=tmp_path / "journal", bot_root=tmp_path, compare_replay=False
    )
    lifecycle = next(check for check in checks if check["item"].startswith("1 "))
    assert lifecycle["ok"] is False


# ----- this lane's own operational switches (never the flush bot's) ----------
def test_depth_bot_has_its_own_kill_file():
    assert depth_bot.KILL_PATH == depth_bot.BOT_ROOT / "KILL"
    assert depth_bot.KILL_PATH != depth_bot.DATA_ROOT / "KILL"
    assert str(depth_bot.KILL_PATH).endswith("data/forward/depth_bot/KILL")
    assert depth_bot.ARMED_PATH == depth_bot.BOT_ROOT / "ARMED"


def test_kill_file_cli_override():
    parser = depth_bot.make_parser()
    assert parser.parse_args(["shadow"]).kill_file is None
    assert parser.parse_args(["shadow", "--kill-file", "/tmp/k"]).kill_file == Path("/tmp/k")
    assert parser.parse_args(["paper", "--kill-file", "/tmp/k"]).kill_file == Path("/tmp/k")
    assert (
        parser.parse_args(["supervise", "--kill-file", "/tmp/k"]).kill_file == Path("/tmp/k")
    )


def test_entries_ignore_the_flush_bots_kill_file(tmp_path: Path):
    # The retired flush bot's data/KILL stays in place; this lane must not read it.
    assert (depth_bot.DATA_ROOT / "KILL").exists()
    engine, sim = make_engine(tmp_path, kill_path=tmp_path / "depthbot-KILL")
    run_one_trade(engine)
    assert engine.positions, "the depth bot must not be stopped by the flush bot's kill file"
    assert [order["side"] for order in sim.orders] == ["buy"]


def test_pit_universe_drops_tickers_alpaca_cannot_subscribe(tmp_path: Path):
    pit = tmp_path / "pit"
    pit.mkdir(parents=True)
    pl.DataFrame(
        {
            "vintage": ["2026-01-01"] * 6,
            "symbol": ["AAPL", "BRK/A", "BAC^E", "ZZZ", "BRK-B", "aapl"],
        }
    ).write_parquet(pit / "pit_symbols.parquet")
    # A non-alphabetic symbol makes the SIP bar subscription fail as a whole (400).
    assert depth_bot.load_pit_universe("2026-04-20", data_root=tmp_path) == ["AAPL", "ZZZ"]
