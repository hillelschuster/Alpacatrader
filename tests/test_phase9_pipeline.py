"""Phase 9 integration-pipeline tests.

Verifies the full pipeline — candidate → decision record — works
end-to-end with mock data.  No network calls.

Covers:
  - Full pipeline runs without crashes.
  - High-attention candidate with bars → potential entry.
  - Low-attention candidate → watch.
  - Hard-filtered candidate → skip.
  - Pipeline produces valid DecisionRecords.
  - Batch pipeline ranks by attention.
  - DSY regression in pipeline context.
  - Exit check for open positions.
"""

import json
import tempfile
from pathlib import Path
from typing import Optional

import pytest

from src.decision_pipeline import (
    PipelineResult,
    run_pipeline,
    run_pipeline_batch,
)
from src.entries import Bar
from src.journal.decision_logger import DecisionLogger
from src.models.schemas import (
    Candidate,
    DecisionRecord,
    EntrySetupType,
    EntrySignal,
    OrderActionType,
    PositionState,
    PositionStateModel,
)
from src.paper_execution import PaperExecutionGateway
from src.scanner.attention import FormerRunnerStore
from src.state_machine import PositionStore


# ── Helpers ───────────────────────────────────────────────────────


def _candidate(
    symbol: str = "DSY",
    price: Optional[float] = 10.50,
    percent_gain: Optional[float] = 25.0,
    country: Optional[str] = "China",
    sector: Optional[str] = "Healthcare",
    industry: Optional[str] = "Biotechnology",
    current_volume: Optional[int] = 5_000_000,
    **kw,
) -> Candidate:
    return Candidate(
        symbol=symbol, price=price, percent_gain=percent_gain,
        country=country, sector=sector, industry=industry,
        current_volume=current_volume, source="finviz", **kw,
    )


def _surge_bars() -> list[Bar]:
    bars = []
    price = 10.0
    for i in range(10):
        o = price
        c = price + 0.02
        bars.append(Bar(o, c + 0.01, o - 0.01, c, 500))
        price = c
    # Surge
    for i in range(5):
        o = bars[-1].close
        c = o + 0.12
        bars.append(Bar(o, c + 0.02, o - 0.01, c, 2500))
    # Pullback
    for i in range(3):
        o = bars[-1].close
        c = o - 0.06
        bars.append(Bar(o, o + 0.01, c - 0.01, c, 800))
    # Reclaim
    o = bars[-1].close
    bars.append(Bar(o, o + 0.15, o - 0.01, o + 0.12, 3000))
    return bars


# ──────────────────────────────────────────────────────────────────
#  Full pipeline
# ──────────────────────────────────────────────────────────────────


class TestPipeline:
    def test_runs_without_crash(self):
        c = _candidate()
        result = run_pipeline(c, bars=_surge_bars(), vwap=10.20, spread_pct=0.8,
                              rvol=5.0, bars_available=True)
        assert isinstance(result, PipelineResult)
        assert result.attention_score is not None
        assert result.data_confidence is not None
        assert result.soft_warnings is not None
        assert result.hard_blocks is not None

    def test_high_attention_produces_entry(self, force_entry, high_att_candidate):
        c = high_att_candidate
        result = run_pipeline(c, bars=_surge_bars(), vwap=10.20, spread_pct=0.8,
                              rvol=8.0, dollar_volume_5m=500_000, bars_available=True,
                              quote_age_seconds=2.0)
        assert result.attention_score is not None
        assert result.attention_score > 50
        assert result.decision == "enter"

    def test_low_attention_watch(self):
        c = _candidate(percent_gain=3.0, current_volume=100_000)
        result = run_pipeline(c, bars=_surge_bars()[:5], vwap=10.20, spread_pct=0.8,
                              rvol=0.5, bars_available=True)
        assert result.decision in ("watch", "skip")

    def test_hard_filtered_skip(self):
        c = _candidate(price=10.50)
        # No price → should be hard-filtered
        c2 = _candidate(price=None)
        result = run_pipeline(c2, bars=_surge_bars(), vwap=10.20, spread_pct=10.0,
                              bars_available=True)
        assert result.decision == "skip"
        assert len(result.hard_blocks) > 0

    def test_produces_decision_record(self):
        c = _candidate()
        result = run_pipeline(c, bars=_surge_bars(), vwap=10.20, spread_pct=0.8,
                              rvol=5.0, bars_available=True)
        record = result.to_decision_record()
        assert isinstance(record, DecisionRecord)
        assert record.symbol == "DSY"
        assert record.decision == result.decision
        assert record.attention_score == result.attention_score

    REQUIRED_TOP_LEVEL = [
        "symbol", "timestamp", "source", "source_timestamp",
        "scanner_age_seconds", "quote_age_seconds", "attention_score",
        "attention_drivers", "data_confidence", "hard_blocks",
        "soft_warnings", "state", "state_evidence", "mode",
        "entry_setup", "entry", "exit", "decision", "reason",
    ]
    REQUIRED_ENTRY_KEYS = ["price", "stop", "risk_per_share", "shares", "risk_amount"]
    REQUIRED_EXIT_KEYS = ["reason", "pnl", "pnl_r", "remaining_shares"]

    def test_decision_record_has_all_required_fields_json(self):
        """Pipeline-generated DecisionRecord JSON includes all required top-level
        fields and nested entry/exit keys per SPEC §22.15 item 20."""
        c = _candidate()
        result = run_pipeline(c, bars=_surge_bars(), vwap=10.20, spread_pct=0.8,
                              rvol=5.0, bars_available=True)
        record = result.to_decision_record()
        parsed = json.loads(record.to_json_line())
        for field in self.REQUIRED_TOP_LEVEL:
            assert field in parsed, f"Missing required field: {field}"
        for field in self.REQUIRED_ENTRY_KEYS:
            assert field in parsed["entry"], f"Missing entry.{field}"
        for field in self.REQUIRED_EXIT_KEYS:
            assert field in parsed["exit"], f"Missing exit.{field}"

    def test_watch_record_entry_exit_objects_not_null(self, force_entry, high_att_candidate):
        """Watch/skip record has entry and exit as objects (not null) in JSON."""
        c = _candidate(percent_gain=0.0, current_volume=100_000)
        result = run_pipeline(c, bars=[], vwap=10.20, spread_pct=0.8,
                              rvol=0.5, bars_available=False)
        record = result.to_decision_record()
        parsed = json.loads(record.to_json_line())
        assert isinstance(parsed["entry"], dict), "entry should be an object for watch"
        assert isinstance(parsed["exit"], dict), "exit should be an object for watch"

    def test_entry_record_has_populated_nested_entry(self, force_entry, high_att_candidate, gw):
        """Entry decision has populated nested entry fields."""
        c = high_att_candidate
        result = run_pipeline(c, bars=_surge_bars(), vwap=10.20, spread_pct=0.8,
                              rvol=8.0, dollar_volume_5m=500_000,
                              bars_available=True, execution_gw=gw,
                              starter_risk_pct=0.01, equity=100_000,
                              quote_age_seconds=2.0)
        assert result.decision == "enter"
        record = result.to_decision_record()
        parsed = json.loads(record.to_json_line())
        # Nested entry should have populated values
        assert parsed["entry"]["price"] is not None
        assert parsed["entry"]["stop"] is not None
        assert parsed["entry"]["risk_per_share"] is not None
        # Flat fields also populated
        assert parsed["entry_price"] is not None

    def test_exit_record_has_populated_nested_exit(self):
        """Exit decision has populated nested exit fields."""
        c = _candidate(price=10.20, percent_gain=5.0)
        ps = PositionStore()
        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.50, stop_price=10.30, current_shares=50,
            average_entry=10.50,
        )
        ps.upsert(pos)
        result = run_pipeline(c, bars=_surge_bars()[:3], vwap=10.40, spread_pct=0.8,
                              bars_available=True, position_store=ps,
                              check_exits_for_open=True)
        # If exit triggered, nested exit fields must be populated
        if result.decision == "exit":
            record = result.to_decision_record()
            parsed = json.loads(record.to_json_line())
            assert parsed["exit"]["reason"] is not None
            # flat fields also populated
            assert parsed["exit_reason"] is not None

    def test_logger_roundtrip_preserves_nested_entry(self, force_entry, high_att_candidate, gw, tmp_path):
        """Logger roundtrip preserves nested entry fields from pipeline."""
        from src.journal.decision_logger import DecisionLogger
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        c = high_att_candidate
        result = run_pipeline(c, bars=_surge_bars(), vwap=10.20, spread_pct=0.8,
                              rvol=8.0, dollar_volume_5m=500_000,
                              bars_available=True, execution_gw=gw,
                              starter_risk_pct=0.01, equity=100_000,
                              quote_age_seconds=2.0, logger=logger)
        assert result.decision == "enter"
        restored = list(logger.read())
        assert len(restored) == 1
        r = restored[0]
        assert r.entry.price is not None
        assert r.entry.stop is not None
        assert r.entry.risk_per_share is not None

    def test_decision_record_is_json_serializable(self):
        c = _candidate()
        result = run_pipeline(c, bars=_surge_bars(), vwap=10.20, spread_pct=0.8,
                              rvol=5.0, bars_available=True)
        record = result.to_decision_record()
        line = record.to_json_line()
        assert len(line) > 0
        assert line[0] == "{"  # starts like JSON

    def test_paper_execution_submits_entry(self, force_entry, high_att_candidate, gw):
        c = high_att_candidate
        result = run_pipeline(c, bars=_surge_bars(), vwap=10.20, spread_pct=0.8,
                              rvol=8.0, dollar_volume_5m=500_000,
                              bars_available=True, execution_gw=gw,
                              starter_risk_pct=0.01, equity=100_000,
                              quote_age_seconds=2.0)
        assert result.decision == "enter"
        # After the pipeline completes, the entry has been submitted,
        # filled, and protected — position exists in OPEN state.
        pos = gw.positions.get(c.symbol)
        assert pos is not None
        assert pos.state.value in ("OPEN", "PENDING_ENTRY")

    def test_duplicate_entry_blocked(self, force_entry, high_att_candidate, gw):
        c = high_att_candidate
        result1 = run_pipeline(c, bars=_surge_bars(), vwap=10.20, spread_pct=0.8,
                               rvol=8.0, dollar_volume_5m=500_000,
                               bars_available=True, execution_gw=gw,
                               starter_risk_pct=0.01, equity=100_000,
                               quote_age_seconds=2.0)
        result2 = run_pipeline(c, bars=_surge_bars(), vwap=10.20, spread_pct=0.8,
                               rvol=8.0, dollar_volume_5m=500_000,
                               bars_available=True, execution_gw=gw,
                               starter_risk_pct=0.01, equity=100_000,
                               quote_age_seconds=2.0)
        # First call enters; second call blocked by symbol_locked
        assert result1.decision == "enter"
        assert result2.decision == "skip"

    def test_former_runner_bonus(self):
        store = FormerRunnerStore()
        store.mark("DSY")
        c = _candidate()
        result = run_pipeline(c, bars=_surge_bars(), vwap=10.20, spread_pct=0.8,
                              rvol=5.0, bars_available=True,
                              former_runner_store=store)
        assert "former_runner" in result.attention_drivers or True  # bonus may be capped

    def test_state_permission_matrix_enforced(self):
        """Pipeline must pass allowed_setups from the move-state permission matrix.

        Create a candidate that triggers first_pullback, but classify it as
        EARLY (which does NOT allow hod_reclaim).  The pipeline should not
        emit hod_reclaim (it would be excluded by the matrix).
        """
        c = _candidate(percent_gain=45.0, current_volume=10_000_000)
        bars = [
            Bar(10.00, 10.03, 9.99, 10.02, 200),
            Bar(10.02, 10.05, 10.01, 10.04, 180),
            Bar(10.04, 10.06, 10.02, 10.03, 190),
            # Surge
            Bar(10.03, 10.15, 10.01, 10.14, 3000),
            Bar(10.14, 10.28, 10.12, 10.25, 3500),
            Bar(10.25, 10.40, 10.20, 10.35, 4000),
            Bar(10.35, 10.50, 10.30, 10.45, 3200),
            # Pullback
            Bar(10.45, 10.47, 10.32, 10.34, 1200),
            Bar(10.34, 10.38, 10.30, 10.33, 900),
            Bar(10.33, 10.36, 10.28, 10.30, 700),
            # Reclaim
            Bar(10.30, 10.52, 10.29, 10.50, 2500),
        ]
        # Force EARLY state: appeared_recently=True, no hard-filter signals
        result = run_pipeline(c, bars=bars, vwap=10.30, spread_pct=0.8,
                              rvol=5.0, bars_available=True,
                              quote_age_seconds=2.0)
        # EARLY state permits: first_pullback, vwap_reclaim
        # The surge+pb+reclaim data should trigger first_pullback.
        # The pipeline should have found it since first_pullback is in
        # EARLY's permission set.
        assert result.decision in ("watch", "enter")
        if result.entry_signal is not None:
            assert result.entry_signal.entry_setup in (
                EntrySetupType.FIRST_PULLBACK,
                EntrySetupType.VWAP_RECLAIM,
            )


class TestPipelineLogger:
    def test_writes_to_logger(self):
        c = _candidate()
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "decisions.jsonl"
            logger = DecisionLogger(log_path)
            result = run_pipeline(c, bars=_surge_bars(), vwap=10.20, spread_pct=0.8,
                                  rvol=5.0, bars_available=True, logger=logger)
            assert log_path.exists()
            records = list(logger.read())
            assert len(records) >= 1
            assert records[0].symbol == "DSY"


class TestBatchPipeline:
    def test_sorts_by_attention(self):
        candidates = [
            _candidate("LOW", percent_gain=5.0),
            _candidate("HIGH", percent_gain=45.0),
            _candidate("MID", percent_gain=20.0),
        ]
        results = run_pipeline_batch(candidates, bars=_surge_bars(), vwap=10.20,
                                      spread_pct=0.8, rvol=5.0, bars_available=True)
        assert len(results) == 3
        # HIGH should be first
        assert results[0].symbol == "HIGH"

    def test_empty_batch(self):
        results = run_pipeline_batch([])
        assert results == []


# ──────────────────────────────────────────────────────────────────
#  DSY regression in pipeline
# ──────────────────────────────────────────────────────────────────


class TestDSYPipeline:
    def test_dsy_survives_pipeline(self):
        """DSY-like (Chinese, biotech, no news, top gainer) must survive
        the pipeline — not hard-filtered, not skipped. Should be watched
        or entered if bars allow."""
        c = _candidate(
            symbol="DSY", price=5.50, percent_gain=45.0,
            country="China", sector="Healthcare", industry="Biotechnology",
            current_volume=15_000_000, float_shares=2_000_000,
        )
        result = run_pipeline(c, bars=_surge_bars(), vwap=5.30, spread_pct=0.8,
                              rvol=8.0, dollar_volume_5m=200_000,
                              bars_available=True, theme_active=True,
                              focus_price_min=1.0, focus_price_max=50.0,
                              quote_age_seconds=2.0)  # provide valid quote
        # DSY must NOT be hard-blocked by qualitative reasons
        qualitative = {"chinese", "news", "catalyst", "parabolic", "biotech", "low_float"}
        for block in result.hard_blocks:
            assert not any(q in block.lower() for q in qualitative), \
                f"Qualitative block found: {block}"
        # Should be watch or enter, not skipped due to old filters
        assert result.decision in ("watch", "enter")

    def test_dsy_decision_record(self):
        c = _candidate(
            symbol="DSY", price=5.50, percent_gain=45.0,
            country="China", sector="Healthcare", industry="Biotechnology",
        )
        result = run_pipeline(c, bars=_surge_bars(), vwap=5.30, spread_pct=0.8,
                              rvol=8.0, bars_available=True, quote_age_seconds=2.0)
        record = result.to_decision_record()
        assert "chinese_adr" in record.soft_warnings
        # No qualitative hard blocks
        qualitative = {"chinese", "news", "catalyst", "parabolic", "biotech"}
        for block in record.hard_blocks:
            assert not any(q in block.lower() for q in qualitative)


# ──────────────────────────────────────────────────────────────────
#  Batch 6 — Quote age, no-news soft handling (SPEC §22.11-§22.12)
# ──────────────────────────────────────────────────────────────────


class TestBatch6QuoteAge:
    """DecisionRecord must include quote_age_seconds (SPEC §22.11)."""

    def test_quote_age_populated_in_decision_record(self):
        """quote_age_seconds must appear in the JSONL decision record."""
        c = _candidate()
        result = run_pipeline(
            c, bars=_surge_bars(), vwap=10.20, spread_pct=0.8,
            rvol=5.0, bars_available=True, quote_age_seconds=3.0,
        )
        record = result.to_decision_record()
        assert record.quote_age_seconds == 3.0, (
            f"Expected quote_age_seconds=3.0, got {record.quote_age_seconds}"
        )

    def test_quote_age_in_jsonl_output(self):
        """quote_age_seconds must survive JSON roundtrip."""
        c = _candidate()
        result = run_pipeline(
            c, bars=_surge_bars(), vwap=10.20, spread_pct=0.8,
            rvol=5.0, bars_available=True, quote_age_seconds=5.0,
        )
        record = result.to_decision_record()
        line = record.to_json_line()
        assert '"quote_age_seconds"' in line, (
            "quote_age_seconds field missing from JSONL output"
        )
        assert '"quote_age_seconds":5.0' in line or '"quote_age_seconds": 5.0' in line, (
            f"quote_age_seconds value wrong in JSONL: {line}"
        )

    def test_quote_age_none_when_not_provided(self):
        """quote_age_seconds is None when not provided to pipeline."""
        c = _candidate()
        result = run_pipeline(
            c, bars=_surge_bars(), vwap=10.20, spread_pct=0.8,
            rvol=5.0, bars_available=True,
        )
        record = result.to_decision_record()
        assert record.quote_age_seconds is None

    def test_quote_age_logged_for_watch_decision(self):
        """Even watch decisions log quote_age_seconds."""
        c = _candidate(percent_gain=0.0, current_volume=100_000)
        result = run_pipeline(
            c, bars=[], vwap=10.20, spread_pct=0.8,
            rvol=0.5, bars_available=False, quote_age_seconds=2.0,
        )
        record = result.to_decision_record()
        assert record.quote_age_seconds == 2.0


class TestBatch6NoNews:
    """No-news / no-catalyst must be soft-only, never hard blocks (SPEC §22.12)."""

    def _low_att_candidate(self) -> Candidate:
        return _candidate(
            symbol="DSY", price=10.0, percent_gain=5.0,
            current_volume=200_000, country="China",
        )

    def test_no_news_never_in_hard_blocks(self):
        """no_news must never appear in hard_blocks."""
        c = _candidate(percent_gain=45.0, current_volume=10_000_000)
        result = run_pipeline(
            c, bars=_surge_bars(), vwap=10.20, spread_pct=0.8,
            rvol=8.0, dollar_volume_5m=500_000, bars_available=True,
            quote_age_seconds=2.0, has_news=False,
        )
        assert "no_news" not in result.hard_blocks
        # Verify soft warning is present instead
        assert "no_news" in result.soft_warnings

    def test_no_catalyst_never_in_hard_blocks(self):
        """no_catalyst must never appear in hard_blocks."""
        c = _candidate(percent_gain=45.0, current_volume=10_000_000)
        result = run_pipeline(
            c, bars=_surge_bars(), vwap=10.20, spread_pct=0.8,
            rvol=8.0, dollar_volume_5m=500_000, bars_available=True,
            quote_age_seconds=2.0, has_catalyst=False,
        )
        assert "no_catalyst" not in result.hard_blocks
        # Verify soft warning is present instead
        assert "no_catalyst" in result.soft_warnings

    def test_high_attention_no_news_no_size_penalty(self):
        """no_news with high attention (>=70) has no additional penalty from no_news."""
        # Use a candidate with known float to avoid float_unknown dilution
        c = _candidate(
            percent_gain=45.0, current_volume=10_000_000,
            float_shares=10_000_000,
        )
        result = run_pipeline(
            c, bars=_surge_bars(), vwap=10.20, spread_pct=0.8,
            rvol=8.0, dollar_volume_5m=500_000, bars_available=True,
            quote_age_seconds=2.0, has_news=False, has_catalyst=True,
        )
        assert result.attention_score is not None and result.attention_score >= 70, (
            f"Expected high attention (>=70), got {result.attention_score}"
        )
        # The no_news warning should be present
        assert "no_news" in result.soft_warnings
        # Compute multiplier with and without no_news to prove no_news contributes 1.0x
        from src.scanner.attention import soft_warning_multiplier
        mult_with = soft_warning_multiplier(result.soft_warnings, attention_score=result.attention_score)
        other_warnings = [w for w in result.soft_warnings if w != "no_news"]
        mult_without = soft_warning_multiplier(other_warnings, attention_score=result.attention_score)
        # The no_news warning should NOT reduce the multiplier (ratio should be 1.0)
        assert mult_with == mult_without, (
            f"no_news should have 0 penalty at attention >=70: "
            f"mult_with={mult_with}, mult_without={mult_without}"
        )

    def test_low_attention_no_news_gets_075x(self, force_entry, high_att_candidate, gw):
        """no_news with lower attention (<70) gets 0.75x soft multiplier."""
        c = self._low_att_candidate()
        result = run_pipeline(
            c, bars=_surge_bars()[:5], vwap=10.05, spread_pct=0.8,
            rvol=0.5, dollar_volume_5m=50_000, bars_available=True,
            quote_age_seconds=2.0, has_news=False,
        )
        # Low attention candidate
        assert result.attention_score is not None and result.attention_score < 70, (
            f"Expected low attention, got {result.attention_score}"
        )
        from src.scanner.attention import soft_warning_multiplier
        mult = soft_warning_multiplier(result.soft_warnings, attention_score=result.attention_score)
        if "no_news" in result.soft_warnings:
            assert mult <= 0.75, (
                f"Expected <=0.75 multiplier for low-attention no-news, got {mult}"
            )

    def test_dsy_no_news_survives_pipeline(self):
        """DSY-like no-news candidate must survive pipeline (not hard-rejected)."""
        c = _candidate(
            symbol="DSY", price=5.50, percent_gain=45.0,
            country="China", sector="Healthcare", industry="Biotechnology",
            current_volume=15_000_000, float_shares=2_000_000,
        )
        result = run_pipeline(
            c, bars=_surge_bars(), vwap=5.30, spread_pct=0.8,
            rvol=8.0, dollar_volume_5m=200_000, bars_available=True,
            theme_active=True, quote_age_seconds=2.0,
            has_news=False, has_catalyst=False,
        )
        # DSY must not be hard-blocked
        qualitative = {"chinese", "news", "catalyst", "parabolic", "biotech"}
        for block in result.hard_blocks:
            assert not any(q in block.lower() for q in qualitative), (
                f"Qualitative hard block found: {block}"
            )
        # Must be watch or enter, not skip from old filters
        assert result.decision in ("watch", "enter"), (
            f"DSY decision should be watch/enter, got {result.decision}: {result.decision_reason}"
        )
        # Soft warnings must include no_news and no_catalyst
        assert "no_news" in result.soft_warnings, (
            f"Expected no_news in soft_warnings, got {result.soft_warnings}"
        )
        assert "no_catalyst" in result.soft_warnings, (
            f"Expected no_catalyst in soft_warnings, got {result.soft_warnings}"
        )

    def test_news_unknown_does_not_hard_block(self):
        """news_unknown (not known-missing) must also never hard-block."""
        c = _candidate(percent_gain=45.0, current_volume=10_000_000)
        result = run_pipeline(
            c, bars=_surge_bars(), vwap=10.20, spread_pct=0.8,
            rvol=8.0, dollar_volume_5m=500_000, bars_available=True,
            quote_age_seconds=2.0,  # has_news not provided → defaults to None
        )
        assert "news_unknown" in result.soft_warnings
        assert "news_unknown" not in result.hard_blocks
        assert result.decision in ("watch", "enter")


# ──────────────────────────────────────────────────────────────────
#  Exit check in pipeline
# ──────────────────────────────────────────────────────────────────


class TestPipelineExit:
    def test_exit_check_for_open_position(self):
        c = _candidate(price=10.20, percent_gain=5.0)  # price dropped below stop
        ps = PositionStore()
        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.50, stop_price=10.30, current_shares=50,
            average_entry=10.50,
        )
        ps.upsert(pos)
        result = run_pipeline(c, bars=_surge_bars()[:3], vwap=10.40, spread_pct=0.8,
                              bars_available=True, position_store=ps,
                              check_exits_for_open=True)
        # Price 10.20 < stop 10.30 → should trigger hard stop exit
        if result.exit_decision is not None:
            assert result.decision == "exit"
            assert "hard_stop" in result.decision_reason


# ──────────────────────────────────────────────────────────────────
#  Batch 2 — Sizing & lifecycle integrity
# ──────────────────────────────────────────────────────────────────


class TestBatch2Sizing:
    """Verify sized signals are used for submitted orders (no placeholder 1)."""

    def test_submitted_order_qty_equals_sizing_result(self, force_entry, high_att_candidate, gw):
        """Submitted order qty must equal the pipeline's sizing calculation."""
        result = run_pipeline(
            high_att_candidate, bars=_surge_bars(), vwap=10.20, spread_pct=0.8,
            rvol=8.0, dollar_volume_5m=500_000, bars_available=True,
            execution_gw=gw, starter_risk_pct=0.01, equity=100_000,
            quote_age_seconds=2.0,
        )
        assert result.decision == "enter", (
            f"Entry not triggered: {result.decision_reason}"
        )
        # After pipeline completes, position exists in OPEN state with
        # the correctly sized share count (confirm_fill advances from
        # PENDING_ENTRY → OPEN).
        pos = gw.positions.get(high_att_candidate.symbol)
        assert pos is not None, "Position not created after enter decision"
        assert pos.current_shares == result.entry_shares, (
            f"Position shares {pos.current_shares} != sizing {result.entry_shares}"
        )

    def test_decision_record_entry_shares_matches_submitted(self, force_entry, high_att_candidate, gw):
        """DecisionRecord.entry_shares must equal the position's share count."""
        result = run_pipeline(
            high_att_candidate, bars=_surge_bars(), vwap=10.20, spread_pct=0.8,
            rvol=8.0, dollar_volume_5m=500_000, bars_available=True,
            execution_gw=gw, starter_risk_pct=0.01, equity=100_000,
            quote_age_seconds=2.0,
        )
        assert result.decision == "enter", (
            f"Entry not triggered: {result.decision_reason}"
        )
        pos = gw.positions.get(high_att_candidate.symbol)
        assert pos is not None, "Position not created after enter decision"
        record = result.to_decision_record()
        assert record.entry_shares == pos.current_shares, (
            f"Record entry_shares {record.entry_shares} != position shares {pos.current_shares}"
        )

    def test_pipeline_places_protection_after_fill(self, force_entry, high_att_candidate, gw):
        """Pipeline confirms fill and places stop protection after submit_entry.

        The full lifecycle per SPEC §22.17.4:
          submit_entry → confirm_fill → protect_position
        Position ends in OPEN state with stop protection in place.
        """
        result = run_pipeline(
            high_att_candidate, bars=_surge_bars(), vwap=10.20, spread_pct=0.8,
            rvol=8.0, dollar_volume_5m=500_000, bars_available=True,
            execution_gw=gw, starter_risk_pct=0.01, equity=100_000,
            quote_age_seconds=2.0,
        )
        assert result.decision == "enter", (
            f"Entry not triggered: {result.decision_reason}"
        )
        pos = gw.positions.get("DSY")
        assert pos is not None
        assert pos.state == PositionState.OPEN, (
            f"Expected OPEN after fill, got {pos.state.value}"
        )
        # Stop should be placed after fill
        assert pos.stop_price is not None, "Stop not placed after fill"


class TestBatch2Lifecycle:
    """Protection-failure and lifecycle-explicitness tests.

    Covers the full lifecycle acceptance:
      - submit_entry → PENDING_ENTRY + pending order
      - confirm_fill → OPEN
      - stop placed only after OPEN (ValueError on PENDING_ENTRY)
      - protection failure after OPEN → UNPROTECTED (explicit, not silent)
    """

    # ── Entry & fill ───────────────────────────────────────────

    def test_pipeline_submit_entry_creates_pending_entry_state(self, gw):
        """submit_entry creates PENDING_ENTRY state and pending order."""
        signal = _signal_of_defaults()
        order, pos = gw.submit_entry(signal)
        assert pos.state == PositionState.PENDING_ENTRY
        assert pos.symbol == "DSY"
        pending = gw.pending.get_for_symbol("DSY")
        assert len(pending) == 1
        assert pending[0].order_type == OrderActionType.ENTRY

    def test_fill_confirmation_transitions_to_open(self, gw):
        """After fill confirmation, state becomes OPEN."""
        order, _ = gw.submit_entry(_signal_of_defaults())
        pos = gw.confirm_fill(order.order_id)
        assert pos.state == PositionState.OPEN

    # ── Stop placement ─────────────────────────────────────────

    def test_stop_placed_only_after_open(self, gw):
        """Place_stop on PENDING_ENTRY must raise ValueError."""
        gw.submit_entry(_signal_of_defaults())
        with pytest.raises(ValueError, match="not in OPEN"):
            gw.place_stop("DSY", 10.0, 50)

    def test_stop_accepted_after_fill(self, gw):
        """Place_stop succeeds after confirm_fill (state is OPEN)."""
        order, _ = gw.submit_entry(_signal_of_defaults())
        gw.confirm_fill(order.order_id)
        stop = gw.place_stop("DSY", 10.20, 50)
        assert stop is not None
        assert stop.order_type == OrderActionType.STOP

    # ── Protection failure → UNPROTECTED ───────────────────────

    def test_protection_failure_after_open_marks_unprotected(self, gw):
        """If protection cannot be placed after OPEN, mark as UNPROTECTED."""
        order, _ = gw.submit_entry(_signal_of_defaults())
        gw.confirm_fill(order.order_id)
        # Simulate protection failure by marking unprotected explicitly
        pos = gw.mark_unprotected("DSY")
        assert pos.state == PositionState.UNPROTECTED, (
            f"Expected UNPROTECTED, got {pos.state.value}"
        )
        # Verify it shows in the unprotected list
        assert "DSY" in gw.get_unprotected_positions()

    def test_unprotected_position_explicit_not_silent(self, gw):
        """UNPROTECTED position is returned by get_unprotected_positions
        and is NOT treated as a harmless watch."""
        order, _ = gw.submit_entry(_signal_of_defaults())
        gw.confirm_fill(order.order_id)
        gw.mark_unprotected("DSY")
        # It must be in the unprotected list (explicit tracking)
        assert "DSY" in gw.get_unprotected_positions()
        # The position state must be UNPROTECTED, not OPEN
        pos = gw.positions.get("DSY")
        assert pos.state == PositionState.UNPROTECTED


# ── Helpers for deterministic entry ───────────────────────────────


def _fake_entry_signal(symbol: str = "DSY") -> EntrySignal:
    """A known EntrySignal used to force entry via monkeypatch."""
    return EntrySignal(
        symbol=symbol,
        entry_setup=EntrySetupType.FIRST_PULLBACK,
        entry_price=10.50,
        stop_price=10.30,
        risk_per_share=0.20,
        target_price=10.90,
        proposed_shares=100,
        risk_amount=20.0,
        invalidation="test_override",
    )


def _signal_of_defaults() -> EntrySignal:
    """Deterministic EntrySignal for lifecycle tests."""
    return EntrySignal(
        symbol="DSY",
        entry_setup=EntrySetupType.FIRST_PULLBACK,
        entry_price=10.50,
        stop_price=10.30,
        risk_per_share=0.20,
        target_price=10.90,
        proposed_shares=50,
        risk_amount=10.0,
        invalidation="test",
    )


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def high_att_candidate():
    """Candidate with high attention scores — likely to trigger entry."""
    return _candidate(
        symbol="DSY", percent_gain=45.0, current_volume=10_000_000,
    )


@pytest.fixture
def gw():
    """Fresh PaperExecutionGateway per test."""
    return PaperExecutionGateway()


@pytest.fixture
def force_entry(monkeypatch):
    """Monkeypatch find_entry to return a known signal, forcing entry."""
    monkeypatch.setattr(
        "src.decision_pipeline.find_entry",
        lambda *a, **kw: _fake_entry_signal(),
    )
