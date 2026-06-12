"""Tests for the Phase 1 DecisionLogger — JSONL write/read/query."""

import json

import pytest

from src.journal.decision_logger import DecisionLogger
from src.models.schemas import DecisionRecord, EntryInfo, ExitInfo


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def logger(tmp_path):
    """DecisionLogger writing to a temporary directory."""
    return DecisionLogger(tmp_path / "decisions.jsonl")


@pytest.fixture
def sample_records():
    """A handful of sample DecisionRecords for testing."""
    return [
        DecisionRecord(
            symbol="DSY",
            decision="watch",
            reason="no defined-risk pullback yet",
            attention_score=91,
            attention_drivers=["top_gainer", "theme_chinese_no_news"],
            soft_warnings=["chinese_adr", "no_news"],
            state="active",
            mode="watch_for_first_pullback",
        ),
        DecisionRecord(
            symbol="DSY",
            decision="enter",
            reason="first pullback formed",
            entry_setup="first_pullback",
            entry_price=10.50,
            entry_stop=10.30,
            entry_risk_per_share=0.20,
            entry_shares=50,
            entry_risk_amount=10.0,
        ),
        DecisionRecord(
            symbol="AAPL",
            decision="watch",
            reason="attention building, waiting for pullback",
            attention_score=65,
        ),
        DecisionRecord(
            symbol="DSY",
            decision="exit",
            reason="hard stop hit",
            exit_reason="stop_loss",
            exit_pnl=-10.0,
            exit_pnl_r=-1.0,
            exit_remaining_shares=0,
        ),
    ]


# ──────────────────────────────────────────────────────────────
#  Write & Read
# ──────────────────────────────────────────────────────────────


class TestDecisionLoggerWriteRead:
    def test_write_one_record(self, logger):
        """Writing one DecisionRecord produces one JSON line."""
        record = DecisionRecord(symbol="DSY", decision="watch", reason="testing")
        logger.write(record)
        lines = list(logger.read())
        assert len(lines) == 1
        assert lines[0].symbol == "DSY"

    def test_write_multiple_records(self, logger, sample_records):
        """Multiple records are each written as separate lines."""
        for rec in sample_records:
            logger.write(rec)
        lines = list(logger.read())
        assert len(lines) == 4

    def test_each_line_is_valid_json(self, logger, sample_records):
        """Each line in the file should be parseable JSON."""
        for rec in sample_records:
            logger.write(rec)
        with open(logger.path) as f:
            for line in f:
                stripped = line.strip()
                assert stripped, "empty line in JSONL"
                parsed = json.loads(stripped)
                assert "symbol" in parsed
                assert "decision" in parsed

    def test_one_json_object_per_line(self, logger, sample_records):
        """There should be exactly one JSON object per line."""
        for rec in sample_records:
            logger.write(rec)
        with open(logger.path) as f:
            lines = [l for l in f if l.strip()]
        assert len(lines) == len(sample_records)

    def test_read_empty_file_returns_nothing(self, logger):
        """Reading an empty file yields no records."""
        lines = list(logger.read())
        assert len(lines) == 0

    def test_read_nonexistent_file(self, tmp_path):
        """Reading a non-existent file yields no records (no crash)."""
        logger = DecisionLogger(tmp_path / "nonexistent.jsonl")
        lines = list(logger.read())
        assert len(lines) == 0

    def test_write_creates_file(self, logger):
        """File should be created after first write."""
        record = DecisionRecord(symbol="DSY", decision="watch", reason="test")
        logger.write(record)
        assert logger.path.exists()

    def test_write_creates_parent_dirs(self, tmp_path):
        """Deep parent directories should be auto-created."""
        path = tmp_path / "subdir" / "nested" / "decisions.jsonl"
        logger = DecisionLogger(path)
        logger.write(DecisionRecord(symbol="DSY", decision="watch", reason="test"))
        assert path.exists()


# ──────────────────────────────────────────────────────────────
#  Filter / Query
# ──────────────────────────────────────────────────────────────


class TestDecisionLoggerFilter:
    def test_filter_by_symbol(self, logger, sample_records):
        for rec in sample_records:
            logger.write(rec)
        dsy_records = logger.filter(symbol="DSY")
        assert len(dsy_records) == 3  # DSY appears 3 times
        assert all(r.symbol == "DSY" for r in dsy_records)

    def test_filter_by_decision(self, logger, sample_records):
        for rec in sample_records:
            logger.write(rec)
        watch_records = logger.filter(decision="watch")
        assert len(watch_records) == 2  # two "watch" decisions
        assert all(r.decision == "watch" for r in watch_records)

    def test_filter_by_symbol_and_decision(self, logger, sample_records):
        for rec in sample_records:
            logger.write(rec)
        dsy_exits = logger.filter(symbol="DSY", decision="exit")
        assert len(dsy_exits) == 1
        assert dsy_exits[0].reason == "hard stop hit"

    def test_filter_no_match(self, logger, sample_records):
        for rec in sample_records:
            logger.write(rec)
        result = logger.filter(symbol="NONEXISTENT")
        assert len(result) == 0

    def test_filter_on_empty_file(self, logger):
        assert logger.filter(symbol="DSY") == []

    def test_records_are_queryable_by_nested_fields(self, logger, sample_records):
        """DecisionRecords should allow in-memory filtering on any field."""
        for rec in sample_records:
            logger.write(rec)
        by_attention = [r for r in logger.read() if r.attention_score is not None]
        assert len(by_attention) == 2  # DSY watch and AAPL watch have scores
        assert all(r.attention_score is not None for r in by_attention)


# ──────────────────────────────────────────────────────────────
#  Roundtrip fidelity
# ──────────────────────────────────────────────────────────────


class TestDecisionLoggerRoundtrip:
    def test_roundtrip_preserves_all_fields(self, logger):
        original = DecisionRecord(
            symbol="DSY",
            decision="enter",
            reason="first pullback formed",
            entry_setup="first_pullback",
            entry_price=10.50,
            entry_stop=10.30,
            entry_risk_per_share=0.20,
            entry_shares=50,
            entry_risk_amount=10.0,
            data_confidence=0.85,
            attention_score=91,
            attention_drivers=["top_gainer"],
            hard_blocks=[],
            soft_warnings=["chinese_adr"],
            state="active",
            state_evidence=["higher_lows=2"],
            mode="starter_entry",
        )
        logger.write(original)

        restored = list(logger.read())
        assert len(restored) == 1
        r = restored[0]

        assert r.symbol == original.symbol
        assert r.decision == original.decision
        assert r.reason == original.reason
        assert r.entry_setup == original.entry_setup
        assert r.entry_price == original.entry_price
        assert r.entry_stop == original.entry_stop
        assert r.entry_risk_per_share == original.entry_risk_per_share
        assert r.entry_shares == original.entry_shares
        assert r.entry_risk_amount == original.entry_risk_amount
        assert r.data_confidence == original.data_confidence
        assert r.attention_score == original.attention_score
        assert r.attention_drivers == original.attention_drivers
        assert r.hard_blocks == original.hard_blocks
        assert r.soft_warnings == original.soft_warnings
        assert r.state == original.state
        assert r.state_evidence == original.state_evidence
        assert r.mode == original.mode

    def test_roundtrip_preserves_nested_entry_exit(self, logger):
        """Logger roundtrip preserves nested ``entry`` and ``exit`` objects."""
        original = DecisionRecord(
            symbol="DSY",
            decision="enter",
            reason="first pullback formed",
            entry_setup="first_pullback",
            entry_price=10.50,
            entry_stop=10.30,
            entry_risk_per_share=0.20,
            entry_shares=50,
            entry_risk_amount=10.0,
            entry=EntryInfo(price=10.50, stop=10.30, risk_per_share=0.20,
                            shares=50, risk_amount=10.0),
        )
        logger.write(original)
        restored = list(logger.read())
        assert len(restored) == 1
        r = restored[0]
        assert r.entry.price == 10.50
        assert r.entry.stop == 10.30
        assert r.entry.shares == 50

    def test_roundtrip_nested_exit(self, logger):
        """Exit nested fields survive roundtrip."""
        original = DecisionRecord(
            symbol="DSY", decision="exit", reason="stop hit",
            exit_reason="hard_stop", exit_pnl=-25.0, exit_pnl_r=-1.0,
            exit_remaining_shares=0,
            exit=ExitInfo(reason="hard_stop", pnl=-25.0, pnl_r=-1.0,
                          remaining_shares=0),
        )
        logger.write(original)
        restored = list(logger.read())
        assert len(restored) == 1
        r = restored[0]
        assert r.exit.reason == "hard_stop"
        assert r.exit.pnl == -25.0
        assert r.exit.remaining_shares == 0

    def test_roundtrip_watch_has_nested_objects_with_nulls(self, logger):
        """Watch record roundtrip preserves entry/exit as objects with nulls."""
        original = DecisionRecord(
            symbol="DSY", decision="watch", reason="no setup",
        )
        logger.write(original)
        restored = list(logger.read())
        assert len(restored) == 1
        r = restored[0]
        assert r.entry is not None
        assert r.exit is not None
        assert r.entry.price is None
        assert r.entry.stop is None
        assert r.exit.reason is None
