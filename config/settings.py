"""
Application settings loaded from YAML config and environment variables.

Uses Pydantic BaseSettings for validation. Environment variables override YAML.
"""

import logging
import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import Field, field_validator
from dotenv import load_dotenv
from pydantic_settings import (
    BaseSettings as _PydanticBaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

load_dotenv()


class BaseSettings(_PydanticBaseSettings):
    """Environment variables override YAML init values."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[_PydanticBaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return env_settings, dotenv_settings, init_settings, file_secret_settings

logger = logging.getLogger(__name__)


def _load_yaml_config(config_path: Optional[str] = None) -> dict:
    """Load YAML config file, returning empty dict if not found."""
    env_config = os.getenv("ALPACATRADER_CONFIG")
    if config_path is None:
        config_path = env_config or str(Path(__file__).resolve().parent / "default_config.yaml")

    path = Path(config_path)
    if not path.exists():
        if env_config:
            logger.warning("ALPACATRADER_CONFIG is set but file not found: %s", config_path)
        return {}

    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        logger.error("Failed to parse YAML config %s: %s", config_path, e)
        return {}


class TradingSettings(BaseSettings):
    """Trading-specific settings.

    Unknown env vars with ``TRADING_`` prefix are silently ignored
    (model_config has extra='ignore').  Mis-prefixed keys like
    ``LIVE_TRADING_CONFIRMED`` without the ``TRADING_`` prefix are
    also ignored — only correctly-prefixed keys take effect.
    """

    mode: str = "paper"
    live_trading_confirmed: str = "no"

    # Alpaca credentials — read from ALPACA_API_KEY / ALPACA_SECRET_KEY env vars
    alpaca_api_key: Optional[str] = Field(default=None, validation_alias="ALPACA_API_KEY")
    alpaca_secret_key: Optional[str] = Field(default=None, validation_alias="ALPACA_SECRET_KEY")

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        allowed = {"mock", "paper", "live", "sim"}
        v_lower = v.lower()
        if v_lower not in allowed:
            raise ValueError(
                f"Trading mode must be one of {allowed}, got '{v}'"
            )
        return v_lower

    @field_validator("live_trading_confirmed")
    @classmethod
    def validate_live_confirmed(cls, v: str) -> str:
        if v not in ("no", "yes_i_accept_the_risks"):
            raise ValueError(
                "live_trading_confirmed must be 'no' or 'yes_i_accept_the_risks'"
            )
        return v

    def require_alpaca_credentials(self) -> None:
        """Validate Alpaca credentials are present when a mode requires them."""
        if not self.alpaca_api_key or not self.alpaca_secret_key:
            raise ValueError(
                f"Alpaca credentials required for mode '{self.mode}'. "
                "Set ALPACA_API_KEY and ALPACA_SECRET_KEY in .env"
            )

    model_config = SettingsConfigDict(env_prefix="TRADING_", extra="ignore")


class LoggingSettings(BaseSettings):
    """Logging settings."""

    level: str = "INFO"
    dir: str = "./logs"
    format: str = "json"
    retention_days: int = 90

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in allowed:
            raise ValueError(
                f"logging level must be one of {allowed}, got '{v}'"
            )
        return v.upper()

    model_config = SettingsConfigDict(env_prefix="LOGGING_", extra="ignore")


class Phase1Settings(BaseSettings):
    """Attention-first top-gainer settings.

    Unknown env vars with ``PHASE1_`` prefix are silently ignored
    (model_config has extra='ignore').  Mis-typed or mis-prefixed
    keys do not raise errors — they are dropped.  If stricter
    validation is needed, change to extra='forbid'.

    Removed (unwired, deferred to later phases):
      - fresh_quote_seconds → re-add when wired through pipeline/hard_filters
      - max_quote_age_seconds → re-add when wired through pipeline/hard_filters
      - max_candidates → re-add when ranking is wired

    Env vars (PHASE1_ prefix):
      - PHASE1_MAX_TRADE_RISK_PCT → max_trade_risk_pct (per-trade risk cap, T4.4)
    """

    # Data freshness
    scanner_interval_seconds: int = 30
    monitor_interval_seconds: int = 10

    # Scanner
    focus_price_min: float = 1.0
    focus_price_max: float = 50.0

    # Risk
    starter_risk_pct: float = 0.0025
    max_trade_risk_pct: float = 0.01
    max_daily_loss_pct: float = 0.03
    max_positions: int = 3
    max_open_risk_pct: float = 0.03

    @field_validator("max_positions")
    @classmethod
    def validate_max_positions(cls, v: int) -> int:
        if v < 1:
            raise ValueError("max_positions must be >= 1")
        return v

    model_config = SettingsConfigDict(env_prefix="PHASE1_", extra="ignore")


class Settings(BaseSettings):
    """Root settings aggregating all sub-configurations."""

    trading: TradingSettings = Field(default_factory=TradingSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    phase1: Phase1Settings = Field(default_factory=Phase1Settings)

    model_config = SettingsConfigDict(extra="ignore", env_file=".env", env_file_encoding="utf-8")

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> "Settings":
        """Load settings from YAML config, overridden by environment variables."""
        yaml_data = _load_yaml_config(config_path)

        settings = cls(
            trading=TradingSettings(**yaml_data.get("trading", {})),
            logging=LoggingSettings(**yaml_data.get("logging", {})),
            phase1=Phase1Settings(**yaml_data.get("phase1", {})),
        )
        return settings

    def validate_live_trading(self) -> None:
        """Refuse to start if live trading is requested but not explicitly confirmed."""
        if (
            self.trading.mode.lower() == "live"
            and self.trading.live_trading_confirmed != "yes_i_accept_the_risks"
        ):
            raise ValueError(
                "LIVE TRADING IS DISABLED. To enable live trading, you must:\n"
                "  1. Set TRADING_MODE=live in your .env\n"
                "  2. Set TRADING_LIVE_TRADING_CONFIRMED=yes_i_accept_the_risks\n"
                "The system will not start without both conditions met."
            )

    @property
    def is_live(self) -> bool:
        return self.trading.mode.lower() == "live" and self.trading.live_trading_confirmed == "yes_i_accept_the_risks"

    @property
    def is_paper(self) -> bool:
        return self.trading.mode.lower() == "paper"

    @property
    def is_mock(self) -> bool:
        return self.trading.mode.lower() == "mock"
