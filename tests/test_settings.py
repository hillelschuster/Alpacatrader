"""
Lightweight tests for Settings.load after Batch E config simplification.

Verifies:
  1. Settings.load() loads trading, logging, phase1 from YAML.
  2. Missing config file returns defaults.
  3. validate_live_trading() blocks live without confirmation.
   4. Historical settings classes removed.
   5. LLM advisor config is opt-in and disabled by default.
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
        "phase1": {"max_positions": 5, "focus_price_min": 2.0},
        "llm_advisor": {"enabled": True},
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

    def test_load_defaults(self, monkeypatch):
        """Loading with missing config file returns all defaults."""
        monkeypatch.setenv("TRADING_MODE", "paper")
        monkeypatch.setenv("TRADING_LIVE_TRADING_CONFIRMED", "no")
        from config.settings import Settings

        s = Settings.load("/nonexistent/config.yaml")
        assert s.trading.mode == "paper"
        assert s.trading.live_trading_confirmed == "no"
        assert s.logging.level == "INFO"
        assert s.logging.dir == "./logs"
        assert s.logging.retention_days == 90
        assert s.phase1.max_positions == 3

    def test_load_from_yaml(self, config_path: Path):
        """Loading from YAML overrides defaults (env-var sections may be .env-overridden).

        NOTE: .env has TRADING_MODE=paper, LOGGING_LEVEL=INFO etc. — those env
        vars take precedence over YAML per the source ordering (env > dotenv > init).
        Phase1 settings have no .env overrides, so they reflect YAML values directly.
        """
        from config.settings import Settings

        s = Settings.load(str(config_path))
        # Phase1 settings have no .env overrides — show YAML values
        assert s.phase1.max_positions == 5
        assert s.phase1.focus_price_min == 2.0
        # Logging.retention_days and format are not in .env — show YAML values
        assert s.logging.format == "json"  # default (not in YAML, not in .env)
        assert s.logging.retention_days == 7

    def test_kept_sections(self):
        """Only active settings sections exist."""
        from config.settings import Settings

        s = Settings.load("/nonexistent/config.yaml")
        # These should exist
        assert hasattr(s, "trading")
        assert hasattr(s, "logging")
        assert hasattr(s, "phase1")
        assert hasattr(s, "llm_advisor")
        # These should NOT exist (removed sections)
        assert not hasattr(s, "broker")
        assert not hasattr(s, "alpaca")
        assert not hasattr(s, "llm")
        assert not hasattr(s, "scanner")
        assert not hasattr(s, "strategy")
        assert not hasattr(s, "risk")
        assert not hasattr(s, "execution")
        assert not hasattr(s, "data")
        assert not hasattr(s, "database")


class TestLLMAdvisorSettings:
    """Phase 7 LLM advisor config is disabled by default and opt-in only."""

    def test_llm_disabled_by_default(self):
        from config.settings import Settings

        s = Settings.load("/nonexistent/config.yaml")
        assert s.llm_advisor.enabled is False

    def test_llm_yaml_can_enable(self, config_path: Path, monkeypatch):
        from config.settings import Settings

        monkeypatch.delenv("LLM_ADVISOR_ENABLED", raising=False)
        s = Settings.load(str(config_path))
        assert s.llm_advisor.enabled is True

    def test_llm_env_overrides_yaml(self, config_path: Path, monkeypatch):
        from config.settings import Settings

        monkeypatch.setenv("LLM_ADVISOR_ENABLED", "false")
        s = Settings.load(str(config_path))
        assert s.llm_advisor.enabled is False


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


class TestPhase1EnvVarIngestion:
    """PHASE1_* env vars override defaults and are visible in Settings."""

    def test_env_overrides_default(self, monkeypatch):
        """PHASE1_MAX_POSITIONS=5 in env overrides the default of 3."""
        from config.settings import Settings

        monkeypatch.setenv("PHASE1_MAX_POSITIONS", "5")
        s = Settings.load("/nonexistent/config.yaml")
        assert s.phase1.max_positions == 5

    def test_env_overrides_yaml(self, monkeypatch):
        """PHASE1_MAX_POSITIONS env var takes precedence over YAML value."""
        from config.settings import Settings
        import tempfile
        from pathlib import Path

        monkeypatch.setenv("PHASE1_MAX_POSITIONS", "7")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml.dump({"phase1": {"max_positions": 3}}))
            path = Path(f.name)

        s = Settings.load(str(path))
        assert s.phase1.max_positions == 7  # env wins over YAML
        path.unlink(missing_ok=True)

    def test_invalid_max_positions_raises(self, monkeypatch):
        """PHASE1_MAX_POSITIONS=0 is rejected by the field validator."""
        from pydantic import ValidationError
        from config.settings import Phase1Settings

        monkeypatch.setenv("PHASE1_MAX_POSITIONS", "0")
        with pytest.raises(ValidationError, match="max_positions must be >= 1"):
            Phase1Settings()


class TestMisPrefixedKeySafety:
    """Mis-prefixed env vars are silently ignored (extra='ignore' policy).

    This is by design — pydantic-settings with extra='ignore' drops
    unrecognized keys. If stricter validation is needed, change to
    extra='forbid' in the model config.
    """

    def test_unprefixed_live_trading_not_read(self, monkeypatch):
        """LIVE_TRADING_CONFIRMED (no TRADING_ prefix) is silently ignored."""
        from config.settings import Settings

        # Set the WRONG key (missing TRADING_ prefix)
        monkeypatch.setenv("LIVE_TRADING_CONFIRMED", "yes_i_accept_the_risks")
        # Remove the CORRECT key
        monkeypatch.delenv("TRADING_LIVE_TRADING_CONFIRMED", raising=False)

        s = Settings.load("/nonexistent/config.yaml")
        assert s.trading.live_trading_confirmed == "no"  # still default

    def test_misspelled_phase1_key_ignored(self, monkeypatch):
        """PHASEX_MAX_POSITIONS (typo in prefix) is silently ignored."""
        from config.settings import Settings

        monkeypatch.setenv("PHASEX_MAX_POSITIONS", "8")
        s = Settings.load("/nonexistent/config.yaml")
        assert s.phase1.max_positions == 3  # default, not 8

    def test_wrong_prefix_trading_key_ignored(self, monkeypatch):
        """TRADE_MODE (typo, should be TRADING_MODE) is silently ignored."""
        from config.settings import Settings

        monkeypatch.setenv("TRADE_MODE", "live")
        monkeypatch.delenv("TRADING_MODE", raising=False)
        s = Settings.load("/nonexistent/config.yaml")
        assert s.trading.mode == "paper"  # default, not "live"


class TestAlpacaCredentialValidation:
    """Startup validation rejects missing Alpaca credentials in paper/sim/live."""

    def test_validation_passes_with_creds(self, monkeypatch):
        """require_alpaca_credentials() passes when both keys are set."""
        from config.settings import TradingSettings

        monkeypatch.setenv("ALPACA_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
        ts = TradingSettings()
        ts.require_alpaca_credentials()  # should not raise

    def test_validation_raises_without_creds(self, monkeypatch):
        """require_alpaca_credentials() raises when keys are missing."""
        from config.settings import TradingSettings

        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        ts = TradingSettings()
        with pytest.raises(ValueError, match="Alpaca credentials required"):
            ts.require_alpaca_credentials()

    def test_settings_picks_up_alpaca_keys(self, monkeypatch):
        """TradingSettings reads ALPACA_API_KEY / ALPACA_SECRET_KEY from env."""
        from config.settings import Settings

        monkeypatch.setenv("ALPACA_API_KEY", "pk-test")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "sk-test")
        s = Settings.load("/nonexistent/config.yaml")
        assert s.trading.alpaca_api_key == "pk-test"
        assert s.trading.alpaca_secret_key == "sk-test"





class TestMaxTradeRiskPctEnv:
    """T5: PHASE1_MAX_TRADE_RISK_PCT env var loads into Phase1Settings."""

    def test_default_max_trade_risk_pct(self):
        from config.settings import Settings

        s = Settings.load("/nonexistent/config.yaml")
        assert s.phase1.max_trade_risk_pct == 0.01

    def test_env_overrides_default(self, monkeypatch):
        from config.settings import Settings

        monkeypatch.setenv("PHASE1_MAX_TRADE_RISK_PCT", "0.005")
        s = Settings.load("/nonexistent/config.yaml")
        assert s.phase1.max_trade_risk_pct == 0.005

    def test_env_overrides_yaml(self, monkeypatch):
        import tempfile
        from pathlib import Path
        from config.settings import Settings

        monkeypatch.setenv("PHASE1_MAX_TRADE_RISK_PCT", "0.002")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml.dump({"phase1": {"max_trade_risk_pct": 0.01}}))
            path = Path(f.name)

        s = Settings.load(str(path))
        assert s.phase1.max_trade_risk_pct == 0.002  # env wins
        path.unlink(missing_ok=True)


class TestRunnerSettings:
    def test_runner_defaults(self):
        from config.settings import Settings

        s = Settings.load("/nonexistent/config.yaml")
        assert s.runner.activation_r == 1.5
        assert s.runner.atr_period == 5
        assert s.runner.trail_multiplier == 2.5

    def test_runner_yaml_loads(self):
        import tempfile
        from pathlib import Path
        from config.settings import Settings

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml.dump({"runner": {"activation_r": 2.0, "atr_period": 7}}))
            path = Path(f.name)

        s = Settings.load(str(path))
        assert s.runner.activation_r == 2.0
        assert s.runner.atr_period == 7
        assert s.runner.trail_multiplier == 2.5
        path.unlink(missing_ok=True)

    def test_runner_env_overrides_yaml(self, monkeypatch):
        import tempfile
        from pathlib import Path
        from config.settings import Settings

        monkeypatch.setenv("RUNNER_TRAIL_MULTIPLIER", "3.0")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml.dump({"runner": {"trail_multiplier": 2.5}}))
            path = Path(f.name)

        s = Settings.load(str(path))
        assert s.runner.trail_multiplier == 3.0
        path.unlink(missing_ok=True)
