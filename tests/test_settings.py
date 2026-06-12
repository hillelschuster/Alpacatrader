"""
Lightweight tests for Settings.load after Batch E config simplification.

Verifies:
  1. Settings.load() loads trading, logging, phase1 from YAML.
  2. Missing config file returns defaults.
  3. validate_live_trading() blocks live without confirmation.
  4. Legacy settings classes are no longer available.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml


# ──────────────────────────────────────────────────────────────────
#  Fixtures
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def minimal_yaml() -> str:
    return yaml.dump({
        "trading": {"mode": "mock", "live_trading_confirmed": "no"},
        "logging": {"level": "DEBUG", "dir": "/tmp/logs", "retention_days": 7},
        "phase1": {"max_candidates": 50, "focus_price_min": 2.0},
    })


@pytest.fixture
def config_path(minimal_yaml: str) -> Path:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(minimal_yaml)
        return Path(f.name)


# ──────────────────────────────────────────────────────────────────
#  Tests
# ──────────────────────────────────────────────────────────────────


class TestSettingsLoad:
    """Settings.load from YAML returns correct values."""

    def test_load_defaults(self):
        """Loading with missing config file returns all defaults."""
        from config.settings import Settings

        s = Settings.load("/nonexistent/config.yaml")
        assert s.trading.mode == "paper"
        assert s.trading.live_trading_confirmed == "no"
        assert s.logging.level == "INFO"
        assert s.logging.dir == "./logs"
        assert s.logging.retention_days == 90
        assert s.phase1.max_candidates == 30

    def test_load_from_yaml(self, config_path: Path):
        """Loading from YAML overrides defaults (env-var sections may be .env-overridden).

        NOTE: .env has TRADING_MODE=paper, LOGGING_LEVEL=INFO etc. — those env
        vars take precedence over YAML per the source ordering (env > dotenv > init).
        Phase1 settings have no .env overrides, so they reflect YAML values directly.
        """
        from config.settings import Settings

        s = Settings.load(str(config_path))
        # Phase1 settings have no .env overrides — show YAML values
        assert s.phase1.max_candidates == 50
        assert s.phase1.focus_price_min == 2.0
        # Logging.retention_days and format are not in .env — show YAML values
        assert s.logging.format == "json"  # default (not in YAML, not in .env)
        assert s.logging.retention_days == 7

    def test_kept_sections(self):
        """Only trading, logging, phase1 sections exist."""
        from config.settings import Settings

        s = Settings.load("/nonexistent/config.yaml")
        # These should exist
        assert hasattr(s, "trading")
        assert hasattr(s, "logging")
        assert hasattr(s, "phase1")
        # These should NOT exist (removed legacy sections)
        assert not hasattr(s, "broker")
        assert not hasattr(s, "alpaca")
        assert not hasattr(s, "llm")
        assert not hasattr(s, "scanner")
        assert not hasattr(s, "strategy")
        assert not hasattr(s, "risk")
        assert not hasattr(s, "execution")
        assert not hasattr(s, "data")
        assert not hasattr(s, "database")


class TestValidateLiveTrading:
    """validate_live_trading blocks live without confirmation."""

    def test_live_blocked_by_default(self):
        from config.settings import Settings

        s = Settings.load("/nonexistent/config.yaml")
        s.trading.mode = "live"
        s.trading.live_trading_confirmed = "no"
        with pytest.raises(ValueError, match="LIVE TRADING IS DISABLED"):
            s.validate_live_trading()

    def test_live_confirmed_passes(self):
        from config.settings import Settings

        s = Settings.load("/nonexistent/config.yaml")
        s.trading.mode = "live"
        s.trading.live_trading_confirmed = "yes_i_accept_the_risks"
        s.validate_live_trading()  # should not raise

    def test_mock_does_not_raise(self):
        from config.settings import Settings

        s = Settings.load("/nonexistent/config.yaml")
        s.trading.mode = "mock"
        s.validate_live_trading()  # should not raise


class TestProperties:
    """is_live, is_paper, is_mock properties."""

    def test_is_mock(self):
        from config.settings import Settings

        s = Settings.load("/nonexistent/config.yaml")
        s.trading.mode = "mock"
        assert s.is_mock
        assert not s.is_paper
        assert not s.is_live

    def test_is_paper(self):
        from config.settings import Settings

        s = Settings.load("/nonexistent/config.yaml")
        s.trading.mode = "paper"
        assert s.is_paper
        assert not s.is_mock
        assert not s.is_live

    def test_is_live_when_confirmed(self):
        from config.settings import Settings

        s = Settings.load("/nonexistent/config.yaml")
        s.trading.mode = "live"
        s.trading.live_trading_confirmed = "yes_i_accept_the_risks"
        assert s.is_live
        assert not s.is_mock
        assert not s.is_paper


class TestModeValidation:
    """TradingSettings.mode field_validator accepts valid modes and rejects invalid ones."""

    def test_valid_modes(self, monkeypatch):
        from config.settings import TradingSettings

        monkeypatch.setenv("TRADING_MODE", "mock")
        assert TradingSettings().mode == "mock"
        monkeypatch.setenv("TRADING_MODE", "paper")
        assert TradingSettings().mode == "paper"
        monkeypatch.setenv("TRADING_MODE", "live")
        assert TradingSettings().mode == "live"

    def test_mode_case_insensitive(self, monkeypatch):
        from config.settings import TradingSettings

        monkeypatch.setenv("TRADING_MODE", "Mock")
        assert TradingSettings().mode == "mock"
        monkeypatch.setenv("TRADING_MODE", "PAPER")
        assert TradingSettings().mode == "paper"
        monkeypatch.setenv("TRADING_MODE", "Live")
        assert TradingSettings().mode == "live"

    def test_invalid_mode_raises(self, monkeypatch):
        from pydantic import ValidationError
        from config.settings import TradingSettings

        monkeypatch.setenv("TRADING_MODE", "banana")
        with pytest.raises(ValidationError, match="Trading mode must be one of"):
            TradingSettings()

    def test_invalid_mode_from_yaml_load(self, monkeypatch):
        """Settings.load with invalid trading mode in YAML raises ValidationError."""
        from pydantic import ValidationError
        from config.settings import Settings

        monkeypatch.delenv("TRADING_MODE", raising=False)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml.dump({"trading": {"mode": "not-a-real-mode"}}))
            path = Path(f.name)

        with pytest.raises(ValidationError, match="Trading mode must be one of"):
            Settings.load(str(path))
        path.unlink(missing_ok=True)


class TestLegacyGone:
    """Legacy settings classes are no longer importable from config.settings."""

    def test_validate_config_warnings_gone(self):
        with pytest.raises(ImportError):
            from config.settings import validate_config_warnings  # noqa

    def test_broker_settings_gone(self):
        with pytest.raises(ImportError):
            from config.settings import BrokerSettings  # noqa

    def test_llm_settings_gone(self):
        with pytest.raises(ImportError):
            from config.settings import LLMSettings  # noqa

    def test_scanner_settings_gone(self):
        with pytest.raises(ImportError):
            from config.settings import ScannerSettings  # noqa

    def test_strategy_settings_gone(self):
        with pytest.raises(ImportError):
            from config.settings import StrategySettings  # noqa

    def test_risk_settings_gone(self):
        with pytest.raises(ImportError):
            from config.settings import RiskSettings  # noqa

    def test_execution_settings_gone(self):
        with pytest.raises(ImportError):
            from config.settings import ExecutionSettings  # noqa

    def test_database_settings_gone(self):
        with pytest.raises(ImportError):
            from config.settings import DatabaseSettings  # noqa
