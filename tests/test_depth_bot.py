from __future__ import annotations

import json
from pathlib import Path

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


def make_engine(tmp_path: Path, *, broker=None, kill_path=None, open_positions=None, risk=None):
    cache = depth_bot.QuoteCache(DAY)
    sim = broker if broker is not None else depth_bot.SimBroker(
        DAY, cache, depth_bot.minute_us(DAY, SESSION_END)
    )
    engine = depth_bot.DepthEngine(
        DAY,
        SESSION_END,
        None,
        sim,
        tmp_path / "journal.jsonl",
        quotes=cache,
        kill_path=kill_path,
        open_positions=open_positions,
        risk=risk,
        fresh=True,
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
    assert journal_file.read_text() == before, "a restart must not rewrite the day journal"
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
@pytest.mark.parametrize("day,block", PARITY_DAYS)
def test_replay_parity_against_frozen_research_outputs(day: str, block: str, tmp_path: Path):
    research = depth_bot.research
    expected = research.pl.read_parquet(
        RESEARCH_OUTPUT / "replay" / block / f"{day}.trades.parquet"
    ).filter(research.pl.col("arm_key") == depth_bot.ARM.key)
    journal_root = tmp_path / "journal"
    daily, trades = depth_bot.replay_day(day, RESEARCH_OUTPUT, research.DATA_ROOT, journal_root)
    assert daily["attempts"] == expected.height
    assert len(trades) == expected.height
    references = expected.select(*PARITY_FIELDS).to_dicts()
    for actual, reference in zip(trades, references, strict=True):
        for key in PARITY_FIELDS:
            want = reference[key]
            got = actual.get(key)
            if key == "net_usd_25" or isinstance(want, float) or isinstance(got, float):
                if want is None or got is None:
                    assert got == want, key
                else:
                    assert abs(float(got) - float(want)) <= 1e-6, key
            else:
                assert got == want, (key, got, want)
    recorded = events(journal_root / f"{day}.jsonl")
    assert sum(event["event"] == "decision_tick" for event in recorded) > 0
    assert sum(event["event"] == "signal" for event in recorded) >= expected.height
    assert sum(event["event"] == "intent" for event in recorded) >= expected.height
    assert sum(event["event"] == "trade_result" for event in recorded) == expected.height


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
