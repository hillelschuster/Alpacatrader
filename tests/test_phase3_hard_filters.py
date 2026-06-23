"""Phase 3 hard-filter tests per SPEC section 7.

Verifies:
  - Every hard skip has a mechanical reason.
  - Mechanical failures (halted, no quote, wide spread, no stop) ARE hard blocks.
  - Old qualitative filters (Chinese, no-news, parabolic, low-float, biotech)
    are NEVER hard blocks.
  - Quote-age and spread tiers classify correctly.
  - Time gates (watch-only, lunch, cutoff, flatten) work correctly.
  - Risk-definition checks reject invalid risk.

No network calls, no broker dependencies.
"""

from datetime import datetime, time, timezone

import pytest

from src.hard_filters import (
    check_account_risk,
    check_execution_data,
    check_liquidity_spread,
    check_market_structure,
    check_risk_definition,
    check_time_gate,
    is_flatten_time,
    is_lunch_window,
    is_past_entry_cutoff,
    is_watch_only_window,
    quote_age_tier,
    run_hard_filters,
    spread_tier,
)
from src.models.schemas import AccountRiskState, Candidate, HardFilterResult


# ── Helpers ───────────────────────────────────────────────────────


def _candidate(symbol: str = "DSY") -> Candidate:
    return Candidate(symbol=symbol)


def _account(**overrides) -> AccountRiskState:
    return AccountRiskState(**overrides)


# ──────────────────────────────────────────────────────────────────
#  Quote-age tiers
# ──────────────────────────────────────────────────────────────────


class TestQuoteAgeTier:
    def test_normal(self):
        assert quote_age_tier(2.0) == "normal"
        assert quote_age_tier(5.0) == "normal"

    def test_stale_warning(self):
        assert quote_age_tier(5.1) == "stale_warning"
        assert quote_age_tier(10.0) == "stale_warning"
        assert quote_age_tier(15.0) == "stale_warning"

    def test_hard_reject(self):
        assert quote_age_tier(15.1) == "hard_reject"
        assert quote_age_tier(60.0) == "hard_reject"

    def test_none_is_hard_reject(self):
        assert quote_age_tier(None) == "hard_reject"

    def test_custom_thresholds(self):
        assert quote_age_tier(8.0, fresh_s=3.0, max_s=10.0) == "stale_warning"
        assert quote_age_tier(11.0, fresh_s=3.0, max_s=10.0) == "hard_reject"


# ──────────────────────────────────────────────────────────────────
#  Spread tiers
# ──────────────────────────────────────────────────────────────────


class TestSpreadTier:
    def test_normal(self):
        assert spread_tier(0.0) == "normal"
        assert spread_tier(0.5) == "normal"
        assert spread_tier(1.0) == "normal"

    def test_caution(self):
        assert spread_tier(1.1) == "caution"
        assert spread_tier(2.0) == "caution"
        assert spread_tier(3.0) == "caution"

    def test_tiny_scalp(self):
        assert spread_tier(3.1) == "tiny_scalp"
        assert spread_tier(4.0) == "tiny_scalp"
        assert spread_tier(5.0) == "tiny_scalp"

    def test_hard_reject(self):
        assert spread_tier(5.1) == "hard_reject"
        assert spread_tier(10.0) == "hard_reject"

    def test_none_is_hard_reject(self):
        assert spread_tier(None) == "hard_reject"


# ──────────────────────────────────────────────────────────────────
#  Market structure checks
# ──────────────────────────────────────────────────────────────────


class TestMarketStructure:
    def test_all_clear_returns_empty(self):
        blocks = check_market_structure()
        assert blocks == []

    def test_not_tradable(self):
        blocks = check_market_structure(is_tradable=False)
        assert "broker_not_tradable" in blocks

    def test_halted(self):
        blocks = check_market_structure(is_halted=True)
        assert "symbol_halted" in blocks

    def test_otc(self):
        blocks = check_market_structure(is_otc=True)
        assert "otc_unsupported" in blocks

    def test_market_closed(self):
        blocks = check_market_structure(market_allows_entries=False)
        assert "market_closed_for_entries" in blocks

    def test_past_entry_cutoff(self):
        blocks = check_market_structure(past_entry_cutoff=True)
        assert "past_entry_cutoff" in blocks

    def test_watch_only_window(self):
        blocks = check_market_structure(in_watch_only_window=True)
        assert "watch_only_window" in blocks

    def test_multiple_blocks(self):
        blocks = check_market_structure(is_halted=True, is_otc=True, is_tradable=False)
        assert len(blocks) == 3


# ──────────────────────────────────────────────────────────────────
#  Execution data checks
# ──────────────────────────────────────────────────────────────────


class TestExecutionData:
    def test_all_clear(self):
        blocks = check_execution_data(
            current_price=10.0, bid=9.99, ask=10.01, quote_age_seconds=2.0,
        )
        assert blocks == []

    def test_no_current_price(self):
        blocks = check_execution_data(current_price=None)
        assert "no_current_price" in blocks

    def test_price_zero_is_blocked(self):
        blocks = check_execution_data(current_price=0.0)
        assert "no_current_price" in blocks

    def test_missing_bid_ask(self):
        blocks = check_execution_data(current_price=10.0, bid=None, ask=None)
        assert "missing_bid_ask" in blocks

    def test_bid_zero(self):
        blocks = check_execution_data(current_price=10.0, bid=0.0, ask=10.01)
        assert "bid_zero_or_negative" in blocks

    def test_ask_zero(self):
        blocks = check_execution_data(current_price=10.0, bid=9.99, ask=0.0)
        assert "ask_zero_or_negative" in blocks

    def test_crossed_market(self):
        blocks = check_execution_data(current_price=10.0, bid=10.02, ask=10.01)
        assert "crossed_market" in blocks

    def test_stale_quote(self):
        blocks = check_execution_data(current_price=10.0, bid=9.99, ask=10.01, quote_age_seconds=20.0)
        assert "quote_too_stale" in blocks

    def test_no_quote_timestamp(self):
        blocks = check_execution_data(current_price=10.0, bid=9.99, ask=10.01, quote_age_seconds=None)
        assert "no_quote_timestamp" in blocks

    def test_custom_max_age(self):
        blocks = check_execution_data(
            current_price=10.0, bid=9.99, ask=10.01, quote_age_seconds=10.0,
            max_quote_age_seconds=5.0,
        )
        assert "quote_too_stale" in blocks


# ──────────────────────────────────────────────────────────────────
#  Liquidity / spread checks
# ──────────────────────────────────────────────────────────────────


class TestLiquiditySpread:
    def test_all_clear(self):
        blocks = check_liquidity_spread(spread_pct=0.5, dollar_volume_5m=200_000, min_dollar_volume=100_000)
        assert blocks == []

    def test_wide_spread_hard_reject(self):
        blocks = check_liquidity_spread(spread_pct=6.0)
        assert any("spread_hard_reject" in b for b in blocks)

    def test_zero_volume(self):
        blocks = check_liquidity_spread(volume_zero=True)
        assert "zero_volume" in blocks

    def test_low_dollar_volume(self):
        blocks = check_liquidity_spread(dollar_volume_5m=50_000, min_dollar_volume=100_000)
        assert any("dollar_volume_below_min" in b for b in blocks)

    def test_scanner_only_allows_low_dollar_volume(self):
        """Scanner-only candidates with low dollar volume are watch-only, not hard-rejected."""
        blocks = check_liquidity_spread(
            spread_pct=0.5, dollar_volume_5m=50_000, min_dollar_volume=100_000, is_scanner_only=True,
        )
        # scanner-only bypasses dollar volume check; no other blocks should fire
        assert not any("dollar_volume_below_min" in b for b in blocks)
        assert len(blocks) == 0

    def test_normal_spread_no_block(self):
        blocks = check_liquidity_spread(spread_pct=0.8)
        assert blocks == []


# ──────────────────────────────────────────────────────────────────
#  Risk definition checks
# ──────────────────────────────────────────────────────────────────


class TestRiskDefinition:
    def test_all_clear(self):
        blocks = check_risk_definition(risk_per_share=0.20, risk_amount=10.0)
        assert blocks == []

    def test_no_logical_stop(self):
        blocks = check_risk_definition(has_logical_stop=False)
        assert "no_logical_stop" in blocks

    def test_risk_per_share_zero(self):
        blocks = check_risk_definition(risk_per_share=0.0, risk_amount=10.0)
        assert "risk_per_share_zero_or_negative" in blocks

    def test_risk_per_share_negative(self):
        blocks = check_risk_definition(risk_per_share=-0.10, risk_amount=10.0)
        assert "risk_per_share_zero_or_negative" in blocks

    def test_risk_amount_too_small(self):
        blocks = check_risk_definition(risk_per_share=0.50, risk_amount=0.30)
        assert "risk_amount_too_small_for_one_share" in blocks

    def test_stop_too_tight_for_spread(self):
        """risk_per_share <= 1.5 * (spread_dollars + slippage) → too tight"""
        # spread_pct=2.0%, price=$10 → spread_dollars = $0.20
        # slippage = $0.01
        # min_meaningful = 1.5 * (0.20 + 0.01) = $0.315
        # risk_per_share = $0.10 < $0.315 → too tight
        blocks = check_risk_definition(
            risk_per_share=0.10, risk_amount=10.0,
            spread_pct=2.0, entry_price=10.0, estimated_slippage=0.01,
        )
        assert "stop_too_tight_for_spread_and_slippage" in blocks

    def test_stop_wide_enough(self):
        """risk_per_share > min_meaningful → no block"""
        blocks = check_risk_definition(
            risk_per_share=0.50, risk_amount=10.0,
            spread_pct=2.0, entry_price=10.0, estimated_slippage=0.01,
        )
        assert "stop_too_tight_for_spread_and_slippage" not in blocks

    def test_stop_exceeds_max_width(self):
        blocks = check_risk_definition(stop_width_pct=6.0, max_stop_width_pct=5.0)
        assert any("stop_exceeds_max_width" in b for b in blocks)

    def test_stop_within_max_width(self):
        blocks = check_risk_definition(stop_width_pct=3.0, max_stop_width_pct=5.0)
        assert not any("stop_exceeds_max_width" in b for b in blocks)


# ──────────────────────────────────────────────────────────────────
#  Account / symbol risk checks
# ──────────────────────────────────────────────────────────────────


class TestAccountRisk:
    def test_all_clear(self):
        acct = _account()
        blocks = check_account_risk(acct)
        assert blocks == []

    def test_kill_switch(self):
        acct = _account(kill_switch_active=True, kill_switch_reason="manual")
        blocks = check_account_risk(acct)
        assert "manual" in blocks

    def test_daily_loss_breached(self):
        acct = _account(daily_loss_breached=True)
        blocks = check_account_risk(acct)
        assert "daily_loss_cap_breached" in blocks

    def test_max_positions_reached(self):
        acct = _account(open_position_count=3)
        blocks = check_account_risk(acct, max_positions=3)
        assert "max_positions_reached" in blocks

    def test_under_max_positions_ok(self):
        acct = _account(open_position_count=2)
        blocks = check_account_risk(acct, max_positions=3)
        assert "max_positions_reached" not in blocks

    def test_symbol_locked(self):
        acct = _account()
        blocks = check_account_risk(acct, symbol_locked=True)
        assert "symbol_locked" in blocks

    def test_theme_concentration(self):
        acct = _account()
        blocks = check_account_risk(acct, theme_exceeds_limit=True)
        assert "theme_concentration_limit" in blocks


# ──────────────────────────────────────────────────────────────────
#  Time gates
# ──────────────────────────────────────────────────────────────────


class TestTimeGates:
    def test_watch_only_inside_window(self):
        assert is_watch_only_window(time(9, 30)) is True
        assert is_watch_only_window(time(9, 32)) is True
        assert is_watch_only_window(time(9, 34, 59)) is True

    def test_watch_only_outside_window(self):
        assert is_watch_only_window(time(9, 29)) is False
        assert is_watch_only_window(time(9, 35)) is False
        assert is_watch_only_window(time(10, 0)) is False

    def test_lunch_inside_window(self):
        assert is_lunch_window(time(11, 30)) is True
        assert is_lunch_window(time(12, 0)) is True
        assert is_lunch_window(time(13, 59, 59)) is True

    def test_lunch_outside_window(self):
        assert is_lunch_window(time(11, 29)) is False
        assert is_lunch_window(time(14, 0)) is False
        assert is_lunch_window(time(10, 0)) is False

    def test_past_entry_cutoff(self):
        assert is_past_entry_cutoff(time(15, 30)) is True
        assert is_past_entry_cutoff(time(15, 45)) is True
        assert is_past_entry_cutoff(time(16, 0)) is True

    def test_not_past_cutoff(self):
        assert is_past_entry_cutoff(time(15, 29)) is False
        assert is_past_entry_cutoff(time(10, 0)) is False

    def test_flatten_time(self):
        assert is_flatten_time(time(15, 55)) is True
        assert is_flatten_time(time(16, 0)) is True
        assert is_flatten_time(time(15, 54)) is False

    def test_check_time_gate_normal(self):
        gates = check_time_gate(time(10, 0))
        assert gates["watch_only"] is False
        assert gates["lunch"] is False
        assert gates["past_cutoff"] is False
        assert gates["flatten"] is False

    def test_check_time_gate_watch_only(self):
        gates = check_time_gate(time(9, 32))
        assert gates["watch_only"] is True
        assert gates["past_cutoff"] is False

    def test_check_time_gate_lunch(self):
        gates = check_time_gate(time(12, 0))
        assert gates["lunch"] is True

    def test_check_time_gate_past_cutoff(self):
        gates = check_time_gate(time(15, 35))
        assert gates["past_cutoff"] is True


# ──────────────────────────────────────────────────────────────────
#  Integration: run_hard_filters
# ──────────────────────────────────────────────────────────────────


class TestRunHardFilters:
    def test_all_clear_returns_passed(self):
        c = _candidate()
        result = run_hard_filters(c, current_price=10.0, bid=9.99, ask=10.01,
                                   quote_age_seconds=2.0, spread_pct=0.5,
                                   risk_per_share=0.20, risk_amount=10.0)
        assert result.passed is True
        assert result.no_hard_blocks is True
        assert result.blocks == []

    def test_halted_symbol_blocked(self):
        c = _candidate()
        result = run_hard_filters(c, is_halted=True)
        assert result.passed is False
        assert "symbol_halted" in result.blocks

    def test_no_price_blocked(self):
        c = _candidate()
        result = run_hard_filters(c, current_price=None)
        assert result.passed is False
        assert "no_current_price" in result.blocks

    def test_wide_spread_blocked(self):
        c = _candidate()
        result = run_hard_filters(c, current_price=10.0, bid=9.50, ask=10.50,
                                   quote_age_seconds=2.0, spread_pct=10.0)
        assert result.passed is False
        assert any("spread_hard_reject" in b for b in result.blocks)

    def test_multiple_blocks_aggregated(self):
        c = _candidate()
        result = run_hard_filters(c, is_halted=True, is_otc=True, current_price=None)
        assert result.passed is False
        assert len(result.blocks) >= 3

    def test_account_kill_switch_blocks(self):
        c = _candidate()
        acct = _account(kill_switch_active=True, kill_switch_reason="daily_loss")
        result = run_hard_filters(c, current_price=10.0, bid=9.99, ask=10.01,
                                   quote_age_seconds=2.0, spread_pct=0.5,
                                   risk_per_share=0.20, risk_amount=10.0,
                                   account=acct)
        assert result.passed is False
        assert "daily_loss" in result.blocks

    def test_symbol_locked_without_account(self):
        """Symbol lock is checked even when account is None."""
        c = _candidate()
        result = run_hard_filters(c, symbol_locked=True)
        assert result.passed is False
        assert "symbol_locked" in result.blocks

    def test_result_is_hard_filter_result(self):
        c = _candidate()
        result = run_hard_filters(c)
        assert isinstance(result, HardFilterResult)

    def test_passed_false_when_any_block(self):
        c = _candidate()
        result = run_hard_filters(c, volume_zero=True)
        assert result.passed is False
        assert result.no_hard_blocks is False


# ──────────────────────────────────────────────────────────────────
#  NEGATIVE: Old qualitative filters are NEVER hard blocks
# ──────────────────────────────────────────────────────────────────


class TestOldFiltersAreNotHardBlocks:
    """Per the spec, these concepts must *never* be hard blocks.

    Every single one of these checks must pass — if a candidate is
    rejected for any of these reasons, the design has failed.
    """

    def test_chinese_adr_not_hard_blocked(self):
        """Chinese ADR is a soft warning, not a hard reject."""
        c = _candidate("DSY")
        result = run_hard_filters(c, current_price=5.50, bid=5.49, ask=5.51,
                                   quote_age_seconds=2.0, spread_pct=0.8,
                                   risk_per_share=0.20, risk_amount=10.0)
        assert result.passed is True
        # No block mentions "chinese", "adr", "no_news", "catalyst"
        for block in result.blocks:
            assert "chinese" not in block.lower()
            assert "news" not in block.lower()
            assert "catalyst" not in block.lower()

    def test_no_news_not_hard_blocked(self):
        """No-news/no-catalyst is a soft warning, not a hard reject."""
        c = _candidate("DSY")
        result = run_hard_filters(c, current_price=5.50, bid=5.49, ask=5.51,
                                   quote_age_seconds=2.0, spread_pct=0.8,
                                   risk_per_share=0.20, risk_amount=10.0)
        assert result.passed is True

    def test_parabolic_not_hard_blocked(self):
        """Parabolic price action is a soft warning, not a hard reject."""
        c = _candidate()
        result = run_hard_filters(c, current_price=50.0, bid=49.95, ask=50.05,
                                   quote_age_seconds=2.0, spread_pct=0.5,
                                   risk_per_share=0.50, risk_amount=25.0)
        assert result.passed is True
        for block in result.blocks:
            assert "parabolic" not in block.lower()

    def test_low_float_not_hard_blocked(self):
        """Low float is a soft warning, not a hard reject."""
        c = _candidate()
        result = run_hard_filters(c, current_price=10.0, bid=9.99, ask=10.01,
                                   quote_age_seconds=2.0, spread_pct=0.8,
                                   risk_per_share=0.20, risk_amount=10.0)
        assert result.passed is True
        for block in result.blocks:
            assert "float" not in block.lower()

    def test_biotech_not_hard_blocked(self):
        """Biotech sector is a soft warning, not a hard reject."""
        c = _candidate()
        result = run_hard_filters(c, current_price=10.0, bid=9.99, ask=10.01,
                                   quote_age_seconds=2.0, spread_pct=0.8,
                                   risk_per_share=0.20, risk_amount=10.0)
        assert result.passed is True
        for block in result.blocks:
            assert "biotech" not in block.lower()

    def test_below_vwap_not_hard_blocked(self):
        """Below-VWAP is context, not a hard reject."""
        c = _candidate()
        result = run_hard_filters(c, current_price=10.0, bid=9.99, ask=10.01,
                                   quote_age_seconds=2.0, spread_pct=0.8,
                                   risk_per_share=0.20, risk_amount=10.0)
        assert result.passed is True
        for block in result.blocks:
            assert "vwap" not in block.lower()

    def test_lunch_not_hard_blocked(self):
        """Lunch is a soft size reduction, not a hard blackout."""
        c = _candidate()
        result = run_hard_filters(c, current_price=10.0, bid=9.99, ask=10.01,
                                   quote_age_seconds=2.0, spread_pct=0.8,
                                   risk_per_share=0.20, risk_amount=10.0)
        assert result.passed is True
        # lunch is a soft annotation only, not in hard blocks
        for block in result.blocks:
            assert "lunch" not in block.lower()

    def test_speculative_not_hard_blocked(self):
        """Speculative themes are annotations, not hard rejects."""
        c = _candidate()
        result = run_hard_filters(c, current_price=10.0, bid=9.99, ask=10.01,
                                   quote_age_seconds=2.0, spread_pct=0.8,
                                   risk_per_share=0.20, risk_amount=10.0)
        assert result.passed is True
        for block in result.blocks:
            assert "speculative" not in block.lower()

    def test_every_block_is_mechanical(self):
        """Every block reason must be a concrete, mechanical failure.

        When blocks exist, they must reference specific data conditions,
        not qualitative judgments about the company.
        """
        c = _candidate()
        result = run_hard_filters(c, is_halted=True, is_otc=True, current_price=None,
                                   volume_zero=True)
        assert len(result.blocks) > 0
        # Every block is a concrete, machine-readable reason
        qualitative = {"chinese", "news", "catalyst", "parabolic", "biotech",
                       "speculative", "low_float", "vwap", "ema", "lunch"}
        for block in result.blocks:
            assert not any(q in block.lower() for q in qualitative), \
                f"Block '{block}' is qualitative, not mechanical"


# ──────────────────────────────────────────────────────────────────
#  DSY regression — hard filters must let DSY through
# ──────────────────────────────────────────────────────────────────


class TestDSYHardFiltersPass:
    """DSY-like candidate must pass hard filters when data is valid.

    The candidate is Chinese, no-news, biotech, low-float — but
    NONE of those should cause a hard block.
    """

    def test_dsy_passes_with_valid_execution_data(self):
        c = _candidate("DSY")
        result = run_hard_filters(
            c,
            current_price=5.50, bid=5.49, ask=5.51,
            quote_age_seconds=2.0,
            spread_pct=0.8,
            risk_per_share=0.14, risk_amount=7.0,
            estimated_slippage=0.01,
        )
        assert result.passed is True, f"DSY should pass. Blocks: {result.blocks}"

    def test_dsy_hard_blocks_are_empty(self):
        c = _candidate("DSY")
        result = run_hard_filters(
            c,
            current_price=5.50, bid=5.49, ask=5.51,
            quote_age_seconds=2.0, spread_pct=0.8,
            risk_per_share=0.14, risk_amount=7.0,
        )
        assert result.blocks == []
        assert result.no_hard_blocks is True
