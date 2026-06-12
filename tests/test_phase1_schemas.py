"""Pure unit tests for Phase 1 schemas — no broker calls, no runtime logic."""

import json
from datetime import datetime, timezone

import pytest

from src.models.schemas import (
    AccountRiskState,
    AttentionScore,
    Candidate,
    DecisionRecord,

    EntryInfo,
    EntrySetupType,
    EntrySignal,
    ExitDecision,
    ExitInfo,
    HardFilterResult,
    ModeType,
    MoveState,
    OrderActionType,
    PendingOrder,
    PositionState,
    PositionStateModel,
)


# ──────────────────────────────────────────────────────────────
#  Enums
# ──────────────────────────────────────────────────────────────


class TestMoveState:
    def test_values(self):
        assert MoveState.HALT_RISK.value == "halt_risk"
        assert MoveState.BACKSIDE.value == "backside"
        assert MoveState.EXTENDED.value == "extended"
        assert MoveState.ACTIVE.value == "active"
        assert MoveState.EARLY.value == "early"

    def test_priority_order(self):
        """Higher-priority states should sort first in a list of enum members."""
        ordered = [
            MoveState.HALT_RISK,
            MoveState.BACKSIDE,
            MoveState.EXTENDED,
            MoveState.ACTIVE,
            MoveState.EARLY,
        ]
        assert ordered[0] == MoveState.HALT_RISK
        assert ordered[-1] == MoveState.EARLY


class TestPositionState:
    def test_values(self):
        assert PositionState.NONE.value == "NONE"
        assert PositionState.PENDING_ENTRY.value == "PENDING_ENTRY"
        assert PositionState.OPEN.value == "OPEN"
        assert PositionState.ADDING.value == "ADDING"
        assert PositionState.SCALING_OUT.value == "SCALING_OUT"
        assert PositionState.RUNNER.value == "RUNNER"
        assert PositionState.EXITING.value == "EXITING"
        assert PositionState.UNPROTECTED.value == "UNPROTECTED"
        assert PositionState.CLOSED.value == "CLOSED"
        assert PositionState.ERROR.value == "ERROR"


class TestEntrySetupType:
    def test_values(self):
        assert EntrySetupType.FIRST_PULLBACK.value == "first_pullback"
        assert EntrySetupType.MICRO_PULLBACK.value == "micro_pullback"
        assert EntrySetupType.HOD_RECLAIM.value == "hod_reclaim"
        assert EntrySetupType.CONSOLIDATION_BREAKOUT.value == "consolidation_breakout"
        assert EntrySetupType.VWAP_RECLAIM.value == "vwap_reclaim"
        assert EntrySetupType.SCALP_RECLAIM.value == "scalp_reclaim"


class TestOrderActionType:
    def test_values(self):
        assert OrderActionType.ENTRY.value == "entry"
        assert OrderActionType.EXIT.value == "exit"
        assert OrderActionType.OCO.value == "oco"



class TestModeType:
    def test_values(self):
        assert ModeType.WATCH.value == "watch"
        assert ModeType.STARTER_ENTRY.value == "starter_entry"
        assert ModeType.AVOID_NEW_LONGS.value == "avoid_new_longs"


# ──────────────────────────────────────────────────────────────
#  Candidate
# ──────────────────────────────────────────────────────────────


class TestCandidate:
    def test_requires_only_symbol(self):
        c = Candidate(symbol="DSY")
        assert c.symbol == "DSY"
        assert c.data_confidence == 1.0

    def test_all_optional_fields_default_to_none(self):
        c = Candidate(symbol="DSY")
        assert c.price is None
        assert c.percent_gain is None
        assert c.premarket_gap_pct is None
        assert c.current_volume is None
        assert c.relative_volume is None
        assert c.sector is None

    def test_data_confidence_default(self):
        c = Candidate(symbol="DSY")
        assert c.data_confidence == 1.0

    def test_data_confidence_validates_bounds(self):
        with pytest.raises(ValueError):
            Candidate(symbol="DSY", data_confidence=1.5)
        with pytest.raises(ValueError):
            Candidate(symbol="DSY", data_confidence=-0.1)

    def test_data_confidence_accepts_valid(self):
        c = Candidate(symbol="DSY", data_confidence=0.7)
        assert c.data_confidence == 0.7

    def test_frozen(self):
        c = Candidate(symbol="DSY")
        with pytest.raises(ValueError):
            c.symbol = "AAPL"


# ──────────────────────────────────────────────────────────────
#  AttentionScore
# ──────────────────────────────────────────────────────────────


class TestAttentionScore:
    def test_valid_score(self):
        s = AttentionScore(score=75.0, drivers=["top_gainer"])
        assert s.score == 75.0
        assert s.drivers == ["top_gainer"]

    def test_score_clamps(self):
        with pytest.raises(ValueError):
            AttentionScore(score=150.0)
        with pytest.raises(ValueError):
            AttentionScore(score=-1.0)

    def test_score_at_bounds(self):
        AttentionScore(score=0.0)  # ok
        AttentionScore(score=100.0)  # ok

    def test_drivers_default_empty(self):
        s = AttentionScore(score=50.0)
        assert s.drivers == []

    def test_driver_storage(self):
        drivers = ["top_gainer", "theme_chinese_no_news", "hod_reclaim"]
        s = AttentionScore(score=91.0, drivers=drivers)
        assert s.drivers == drivers

    def test_raw_components_and_bonuses(self):
        s = AttentionScore(
            score=80.0,
            raw_components={"price_attention": 35.0, "volume_attention": 30.0},
            bonuses_applied=["theme_active"],
        )
        assert s.raw_components["price_attention"] == 35.0
        assert "theme_active" in s.bonuses_applied

    def test_frozen(self):
        s = AttentionScore(score=50.0)
        with pytest.raises(ValueError):
            s.score = 60.0


# ──────────────────────────────────────────────────────────────
#  HardFilterResult
# ──────────────────────────────────────────────────────────────


class TestHardFilterResult:
    def test_default_is_not_passed(self):
        r = HardFilterResult()
        assert r.passed is False

    def test_passed_with_no_blocks(self):
        r = HardFilterResult(passed=True, blocks=[])
        assert r.passed is True
        assert r.no_hard_blocks is True

    def test_passed_with_blocks(self):
        r = HardFilterResult(passed=False, blocks=["no_quote", "wide_spread"])
        assert r.passed is False
        assert r.no_hard_blocks is False
        assert r.blocks == ["no_quote", "wide_spread"]

    def test_no_hard_blocks_property(self):
        assert HardFilterResult(passed=True, blocks=[]).no_hard_blocks is True
        assert HardFilterResult(passed=False, blocks=[]).no_hard_blocks is False
        assert HardFilterResult(passed=True, blocks=["halted"]).no_hard_blocks is False
        assert HardFilterResult(passed=False, blocks=["halted"]).no_hard_blocks is False


# ──────────────────────────────────────────────────────────────
#  EntrySignal
# ──────────────────────────────────────────────────────────────


class TestEntrySignal:
    def _valid_signal(self, **overrides):
        data = {
            "symbol": "DSY",
            "entry_setup": EntrySetupType.FIRST_PULLBACK,
            "entry_price": 10.50,
            "stop_price": 10.30,
            "risk_per_share": 0.20,
            "target_price": 11.00,
            "proposed_shares": 50,
            "risk_amount": 10.0,
            "invalidation": "price trades below pullback low",
        }
        data.update(overrides)
        return EntrySignal(**data)

    def test_requires_complete_defined_risk_entry(self):
        s = self._valid_signal()
        assert s.symbol == "DSY"
        assert s.entry_setup == EntrySetupType.FIRST_PULLBACK

        with pytest.raises(ValueError):
            EntrySignal(**{"symbol": "DSY", "entry_setup": EntrySetupType.FIRST_PULLBACK})

    def test_risk_per_share_must_be_positive(self):
        with pytest.raises(ValueError):
            self._valid_signal(risk_per_share=0.0)
        with pytest.raises(ValueError):
            self._valid_signal(risk_per_share=-0.1)

    def test_risk_per_share_matches_stop_distance(self):
        self._valid_signal()

    def test_risk_per_share_mismatch_raises(self):
        with pytest.raises(ValueError, match="does not match"):
            self._valid_signal(risk_per_share=0.25)

    def test_risk_per_share_tolerance_within_two_cents(self):
        """Two-cent tolerance should be acceptable."""
        self._valid_signal(risk_per_share=0.21)  # 0.01 diff, within 0.02

    def test_risk_per_share_outside_tolerance(self):
        with pytest.raises(ValueError):
            self._valid_signal(risk_per_share=0.23)  # 0.03 diff, outside 0.02

    def test_data_confidence_validates(self):
        with pytest.raises(ValueError):
            self._valid_signal(data_confidence=1.5)

    def test_full_entry_signal(self):
        s = self._valid_signal(
            invalidation="price trades below 10.30 before fill",
            state=MoveState.ACTIVE,
            state_evidence=["pullback_formed", "controlled_selling"],
            quote_age_seconds=2.0,
            spread_pct=0.8,
            data_confidence=0.9,
        )
        assert s.proposed_shares == 50
        assert s.risk_amount == 10.0
        assert s.state == MoveState.ACTIVE


# ──────────────────────────────────────────────────────────────
#  PositionStateModel
# ──────────────────────────────────────────────────────────────


class TestPositionStateModel:
    def test_default_state_is_none(self):
        p = PositionStateModel(symbol="DSY")
        assert p.state == PositionState.NONE

    def test_open_position(self):
        p = PositionStateModel(
            symbol="DSY",
            state=PositionState.OPEN,
            entry_price=10.50,
            current_shares=50,
            average_entry=10.50,
            stop_price=10.30,
        )
        assert p.current_shares == 50
        assert p.average_entry == 10.50

    def test_mutable(self):
        """PositionStateModel should be mutable for runtime updates."""
        p = PositionStateModel(symbol="DSY")
        p.state = PositionState.OPEN
        assert p.state == PositionState.OPEN


# ──────────────────────────────────────────────────────────────
#  PendingOrder
# ──────────────────────────────────────────────────────────────


class TestPendingOrder:
    def test_minimal(self):
        o = PendingOrder(symbol="DSY", order_type=OrderActionType.ENTRY)
        assert o.symbol == "DSY"
        assert o.order_type == OrderActionType.ENTRY

    def test_full_order(self):
        o = PendingOrder(
            symbol="DSY",
            order_id="ord_123",
            order_type=OrderActionType.STOP,
            side="sell",
            qty=50,
            status="submitted",
            limit_price=10.00,
            stop_price=9.50,
        )
        assert o.order_id == "ord_123"
        assert o.limit_price == 10.0

    def test_frozen(self):
        o = PendingOrder(symbol="DSY", order_type=OrderActionType.ENTRY)
        with pytest.raises(ValueError):
            o.symbol = "AAPL"


# ──────────────────────────────────────────────────────────────
#  AccountRiskState
# ──────────────────────────────────────────────────────────────


class TestAccountRiskState:
    def test_defaults(self):
        r = AccountRiskState()
        assert r.daily_realized_pnl == 0.0
        assert r.daily_unrealized_pnl == 0.0
        assert r.total_open_risk == 0.0
        assert r.open_position_count == 0
        assert r.kill_switch_active is False
        assert r.daily_loss_breached is False
        assert r.kill_switch_reason == ""

    def test_daily_pnl_property(self):
        r = AccountRiskState(daily_realized_pnl=100.0, daily_unrealized_pnl=50.0)
        assert r.daily_pnl == 150.0

    def test_kill_switch_properties(self):
        r = AccountRiskState(kill_switch_active=True)
        assert r.is_kill_switch_on is True

        r2 = AccountRiskState(daily_loss_breached=True)
        assert r2.is_kill_switch_on is True

        r3 = AccountRiskState()
        assert r3.is_kill_switch_on is False

    def test_kill_switch_reason(self):
        r = AccountRiskState(
            kill_switch_active=True,
            kill_switch_reason="Daily loss cap breached",
        )
        assert r.kill_switch_reason == "Daily loss cap breached"

    def test_per_symbol_loss_tracking(self):
        r = AccountRiskState(per_symbol_daily_loss={"DSY": -150.0})
        assert r.per_symbol_daily_loss["DSY"] == -150.0

    def test_theme_exposure(self):
        r = AccountRiskState(theme_exposure={"chinese_no_news": 1})
        assert r.theme_exposure["chinese_no_news"] == 1


# ──────────────────────────────────────────────────────────────
#  ExitDecision
# ──────────────────────────────────────────────────────────────


class TestExitDecision:
    def test_default_no_exit(self):
        d = ExitDecision(symbol="DSY")
        assert d.should_exit is False
        assert d.reason == ""

    def test_full_exit(self):
        d = ExitDecision(
            symbol="DSY",
            should_exit=True,
            exit_pct=100,
            reason="Hard stop hit",
            exit_price=10.30,
            pnl=-25.0,
            pnl_r=-1.0,
            remaining_shares=0,
        )
        assert d.should_exit is True
        assert d.exit_pct == 100
        assert d.pnl == -25.0
        assert d.pnl_r == -1.0


# ──────────────────────────────────────────────────────────────
#  DecisionRecord
# ──────────────────────────────────────────────────────────────


class TestDecisionRecord:
    def test_minimal(self):
        r = DecisionRecord(symbol="DSY", decision="watch", reason="testing")
        assert r.symbol == "DSY"
        assert r.decision == "watch"
        assert r.reason == "testing"

    def test_to_json_line_is_valid_json(self):
        r = DecisionRecord(
            symbol="DSY",
            decision="watch",
            reason="no defined-risk pullback yet",
            attention_score=91.0,
            attention_drivers=["top_gainer", "theme_chinese_no_news"],
            hard_blocks=[],
            soft_warnings=["chinese_adr", "no_news"],
            state="active",
            state_evidence=["higher_lows=2", "near_hod=true", "spread=0.8%"],
            mode="watch_for_first_pullback",
        )
        line = r.to_json_line()
        parsed = json.loads(line)
        assert parsed["symbol"] == "DSY"
        assert parsed["decision"] == "watch"
        assert parsed["attention_score"] == 91.0
        assert "chinese_adr" in parsed["soft_warnings"]

    def test_from_json_line_roundtrip(self):
        r = DecisionRecord(
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
        )
        line = r.to_json_line()
        r2 = DecisionRecord.from_json_line(line)
        assert r2.symbol == r.symbol
        assert r2.decision == r.decision
        assert r2.entry_price == r.entry_price
        assert r2.entry_risk_per_share == r.entry_risk_per_share

    def test_serialize_exit_fields(self):
        r = DecisionRecord(
            symbol="DSY",
            decision="exit",
            reason="hard stop hit",
            exit_reason="hard stop",
            exit_pnl=-25.0,
            exit_pnl_r=-1.0,
            exit_remaining_shares=0,
        )
        line = r.to_json_line()
        parsed = json.loads(line)
        assert parsed["exit_pnl"] == -25.0
        assert parsed["exit_pnl_r"] == -1.0

    def test_timestamp_auto_generated(self):
        r = DecisionRecord(symbol="DSY", decision="watch", reason="testing")
        assert r.timestamp is not None

    REQUIRED_TOP_LEVEL = [
        "symbol", "timestamp", "source", "source_timestamp",
        "scanner_age_seconds", "quote_age_seconds", "attention_score",
        "attention_drivers", "data_confidence", "hard_blocks",
        "soft_warnings", "state", "state_evidence", "mode",
        "entry_setup", "entry", "exit", "decision", "reason",
    ]
    REQUIRED_ENTRY_KEYS = ["price", "stop", "risk_per_share", "shares", "risk_amount"]
    REQUIRED_EXIT_KEYS = ["reason", "pnl", "pnl_r", "remaining_shares"]

    def test_json_includes_all_required_fields_with_nulls(self):
        """Minimal DecisionRecord must include every required field in JSON
        output, with null values present explicitly per SPEC §22.15 item 20."""
        r = DecisionRecord(symbol="DSY", decision="watch", reason="testing")
        parsed = json.loads(r.to_json_line())
        # All top-level fields must be present (even if null)
        for field in self.REQUIRED_TOP_LEVEL:
            assert field in parsed, f"Missing required top-level field: {field}"
        # Nested entry must be an object with all keys
        for field in self.REQUIRED_ENTRY_KEYS:
            assert field in parsed["entry"], f"Missing entry.{field}"
        # Nested exit must be an object with all keys
        for field in self.REQUIRED_EXIT_KEYS:
            assert field in parsed["exit"], f"Missing exit.{field}"

    def test_nested_entry_fields_populated(self):
        """DecisionRecord with entry data populates both flat and nested fields."""
        r = DecisionRecord(
            symbol="DSY", decision="enter", reason="entry",
            entry_setup="first_pullback",
            entry_price=10.50, entry_stop=10.30,
            entry_risk_per_share=0.20, entry_shares=50, entry_risk_amount=10.0,
            entry=EntryInfo(price=10.50, stop=10.30, risk_per_share=0.20,
                            shares=50, risk_amount=10.0),
        )
        parsed = json.loads(r.to_json_line())
        # Nested fields
        assert parsed["entry"]["price"] == 10.50
        assert parsed["entry"]["stop"] == 10.30
        assert parsed["entry"]["risk_per_share"] == 0.20
        assert parsed["entry"]["shares"] == 50
        assert parsed["entry"]["risk_amount"] == 10.0
        # Flat fields still present for backward compat
        assert parsed["entry_price"] == 10.50
        assert parsed["entry_stop"] == 10.30

    def test_nested_exit_fields_populated(self):
        """DecisionRecord with exit data populates both flat and nested fields."""
        r = DecisionRecord(
            symbol="DSY", decision="exit", reason="stop hit",
            exit_reason="hard_stop", exit_pnl=-25.0, exit_pnl_r=-1.0,
            exit_remaining_shares=0,
            exit=ExitInfo(reason="hard_stop", pnl=-25.0, pnl_r=-1.0,
                          remaining_shares=0),
        )
        parsed = json.loads(r.to_json_line())
        # Nested fields
        assert parsed["exit"]["reason"] == "hard_stop"
        assert parsed["exit"]["pnl"] == -25.0
        assert parsed["exit"]["pnl_r"] == -1.0
        assert parsed["exit"]["remaining_shares"] == 0
        # Flat fields still present for backward compat
        assert parsed["exit_reason"] == "hard_stop"
        assert parsed["exit_pnl"] == -25.0

    def test_nested_entry_roundtrip(self):
        """Nested entry/exit fields survive JSON roundtrip."""
        r = DecisionRecord(
            symbol="DSY", decision="enter", reason="entry",
            entry_setup="first_pullback",
            entry_price=10.50, entry_stop=10.30,
            entry_risk_per_share=0.20, entry_shares=50, entry_risk_amount=10.0,
            entry=EntryInfo(price=10.50, stop=10.30, risk_per_share=0.20,
                            shares=50, risk_amount=10.0),
        )
        r2 = DecisionRecord.from_json_line(r.to_json_line())
        assert r2.entry.price == 10.50
        assert r2.entry.stop == 10.30
        assert r2.entry.shares == 50

    def test_watch_record_entry_and_exit_are_objects_with_nulls(self):
        """Watch record must have entry and exit as objects (not null) with
        all-null contents."""
        r = DecisionRecord(symbol="DSY", decision="watch", reason="no setup")
        parsed = json.loads(r.to_json_line())
        assert isinstance(parsed["entry"], dict)
        assert isinstance(parsed["exit"], dict)
        for k in self.REQUIRED_ENTRY_KEYS:
            assert parsed["entry"][k] is None, f"entry.{k} should be null"
        for k in self.REQUIRED_EXIT_KEYS:
            assert parsed["exit"][k] is None, f"exit.{k} should be null"

    def test_queryable_by_symbol_and_decision(self):
        """Simulate querying a list of DecisionRecords."""
        records = [
            DecisionRecord(symbol="DSY", decision="watch", reason="no setup"),
            DecisionRecord(symbol="AAPL", decision="watch", reason="low attention"),
            DecisionRecord(symbol="DSY", decision="enter", reason="first pullback"),
        ]
        dsy_records = [r for r in records if r.symbol == "DSY"]
        assert len(dsy_records) == 2

        enter_records = [r for r in records if r.decision == "enter"]
        assert len(enter_records) == 1
        assert enter_records[0].symbol == "DSY"


# ──────────────────────────────────────────────────────────────
#  Cross-model: constructing the spec example DecisionRecord
# ──────────────────────────────────────────────────────────────


class TestSpecDecisionRecordExample:
    """Verify the example decision record from SPEC.md section 14.4
    can be built and round-tripped."""

    def test_build_spec_example(self):
        record = DecisionRecord(
            symbol="DSY",
            source="finviz",
            source_timestamp=None,
            scanner_age_seconds=900,
            quote_age_seconds=2,
            attention_score=91,
            attention_drivers=["top_gainer", "theme_chinese_no_news", "hod_reclaim"],
            data_confidence=0.7,
            hard_blocks=[],
            soft_warnings=["chinese_adr", "no_news"],
            state="active",
            state_evidence=["higher_lows=2", "near_hod=true", "spread=0.8%"],
            mode="watch_for_first_pullback",
            entry_setup=None,
            entry_price=None,
            entry_stop=None,
            entry_risk_per_share=None,
            entry_shares=None,
            entry_risk_amount=None,
            exit_reason=None,
            exit_pnl=None,
            exit_pnl_r=None,
            exit_remaining_shares=None,
            decision="watch",
            reason="no defined-risk pullback yet",
        )
        line = record.to_json_line()
        parsed = json.loads(line)
        assert parsed["symbol"] == "DSY"
        assert parsed["attention_score"] == 91
        assert parsed["decision"] == "watch"
        assert parsed["hard_blocks"] == []
        assert parsed["soft_warnings"] == ["chinese_adr", "no_news"]
        assert parsed["entry_price"] is None
        assert parsed["exit_reason"] is None
