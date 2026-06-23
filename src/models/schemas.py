"""
Phase 1 — Attention-First Top-Gainer Models.

Broker-agnostic Pydantic v2 schemas for candidate discovery, attention scoring,
hard filtering, move classification, entry signals, position management,
account risk, exit decisions, and decision logging.

No broker calls, no runtime trading logic.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ──────────────────────────────────────────────────────────────────
#  Enums
# ──────────────────────────────────────────────────────────────────


class MoveState(str, Enum):
    """Order of priority: highest first."""

    HALT_RISK = "halt_risk"
    BACKSIDE = "backside"
    EXTENDED = "extended"
    ACTIVE = "active"
    EARLY = "early"


class PositionState(str, Enum):
    """Lifecycle of a position."""

    NONE = "NONE"
    PENDING_ENTRY = "PENDING_ENTRY"
    OPEN = "OPEN"
    ADDING = "ADDING"
    SCALING_OUT = "SCALING_OUT"
    RUNNER = "RUNNER"
    EXITING = "EXITING"
    UNPROTECTED = "UNPROTECTED"
    CLOSED = "CLOSED"
    ERROR = "ERROR"


class EntrySetupType(str, Enum):
    """Supported price-action entry setups."""

    FIRST_PULLBACK = "first_pullback"
    MICRO_PULLBACK = "micro_pullback"
    HOD_RECLAIM = "hod_reclaim"
    CONSOLIDATION_BREAKOUT = "consolidation_breakout"
    VWAP_RECLAIM = "vwap_reclaim"
    SCALP_RECLAIM = "scalp_reclaim"


class OrderActionType(str, Enum):
    """What a pending order is trying to do."""

    ENTRY = "entry"
    STOP = "stop"
    TARGET = "target"
    ADD = "add"
    SCALE_OUT = "scale_out"
    EXIT = "exit"
    OCO = "oco"



class ModeType(str, Enum):
    """What the bot can do with a symbol right now."""

    WATCH = "watch"
    STARTER_ENTRY = "starter_entry"
    ADD_ON_CONFIRMATION = "add_on_confirmation"
    SCALP_ONLY = "scalp_only"
    AVOID_NEW_LONGS = "avoid_new_longs"


# ──────────────────────────────────────────────────────────────────
#  Models
# ──────────────────────────────────────────────────────────────────


class Candidate(BaseModel):
    """A discovered top-gainer candidate ready for enrichment & attention scoring.

    All fields except ``symbol`` are optional because free-tier data is
    unreliable.  ``data_confidence`` defaults to 1.0 and is reduced by
    the enrichment layer as gaps are found.
    """

    symbol: str
    price: Optional[float] = Field(default=None, ge=0.0)
    percent_gain: Optional[float] = None
    premarket_gap_pct: Optional[float] = None
    current_volume: Optional[int] = Field(default=None, ge=0)
    relative_volume: Optional[float] = Field(default=None, ge=0.0)
    dollar_volume: Optional[float] = Field(default=None, ge=0.0)
    previous_close: Optional[float] = Field(default=None, ge=0.0)
    day_high: Optional[float] = Field(default=None, ge=0.0)
    day_low: Optional[float] = Field(default=None, ge=0.0)
    premarket_high: Optional[float] = Field(default=None, ge=0.0)
    premarket_low: Optional[float] = Field(default=None, ge=0.0)
    float_shares: Optional[int] = Field(default=None, ge=0)
    market_cap: Optional[float] = Field(default=None, ge=0.0)
    sector: Optional[str] = None
    industry: Optional[str] = None
    country: Optional[str] = None
    exchange: Optional[str] = None
    source: Optional[str] = None
    source_timestamp: Optional[datetime] = None
    quote_timestamp: Optional[datetime] = None
    bar_timestamp: Optional[datetime] = None
    data_confidence: float = 1.0

    @field_validator("data_confidence")
    @classmethod
    def _validate_data_confidence(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"data_confidence must be in [0.0, 1.0], got {v}")
        return v

    model_config = ConfigDict(frozen=True)


class AttentionScore(BaseModel):
    """Attention ranking result for a single candidate.

    ``score`` is 0-100.  ``drivers`` explains *why* the score is what it is.
    """

    score: float = Field(ge=0.0, le=100.0)
    drivers: list[str] = Field(default_factory=list)
    raw_components: dict[str, float] = Field(default_factory=dict)
    bonuses_applied: list[str] = Field(default_factory=list)

    @field_validator("score")
    @classmethod
    def _validate_score(cls, v: float) -> float:
        if not 0.0 <= v <= 100.0:
            raise ValueError(f"score must be in [0.0, 100.0], got {v}")
        return v

    model_config = ConfigDict(frozen=True)


class HardFilterResult(BaseModel):
    """Result of running catastrophic mechanical checks.

    ``passed`` is ``True`` only when every hard check clears.
    ``blocks`` lists every mechanical reason the candidate was blocked.
    """

    passed: bool = False
    blocks: list[str] = Field(default_factory=list)

    @property
    def no_hard_blocks(self) -> bool:
        """Convenience: ``True`` when the candidate is mechanically clear."""
        return self.passed and len(self.blocks) == 0


class EntrySignal(BaseModel):
    """A validated entry signal with definable risk.

    Every entry must define entry price, stop price, risk per share,
    proposed size, and invalidation condition.
    """

    symbol: str
    entry_setup: EntrySetupType
    entry_price: float = Field(gt=0.0)
    stop_price: float = Field(gt=0.0)
    risk_per_share: float = Field(gt=0.0)
    target_price: float = Field(gt=0.0)
    proposed_shares: int = Field(ge=1)
    risk_amount: float = Field(gt=0.0)
    invalidation: str = Field(min_length=1)
    state: Optional[MoveState] = None
    state_evidence: list[str] = Field(default_factory=list)
    quote_age_seconds: Optional[float] = Field(default=None, ge=0.0)
    spread_pct: Optional[float] = Field(default=None, ge=0.0)
    data_confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_risk_match(self) -> "EntrySignal":
        expected = abs(self.entry_price - self.stop_price)
        # Allow 1-cent rounding tolerance.
        if abs(self.risk_per_share - expected) > 0.02:
            raise ValueError(
                f"risk_per_share ({self.risk_per_share}) does not match "
                f"abs(entry - stop) ({expected}) within 2-cent tolerance"
            )
        return self


class PositionStateModel(BaseModel):
    """Current position state for a single symbol.

    ``frozen`` would break runtime updates, so it is intentionally mutable.
    """

    symbol: str
    state: PositionState = PositionState.NONE
    entry_price: Optional[float] = None
    current_shares: int = Field(default=0, ge=0)
    average_entry: Optional[float] = None
    stop_price: Optional[float] = None
    highest_price_seen: Optional[float] = None
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    opened_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PendingOrder(BaseModel):
    """A submitted but not-yet-resolved order."""

    symbol: str
    order_id: str = ""
    order_type: OrderActionType
    side: str = ""  # "buy" | "sell"
    qty: int = Field(default=0, ge=0)
    status: str = "pending"
    submitted_at: Optional[datetime] = None
    linked_position_id: Optional[str] = None
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None

    model_config = ConfigDict(frozen=True)


class AccountRiskState(BaseModel):
    """Runtime account risk snapshot used by entry gates."""

    daily_realized_pnl: float = 0.0
    daily_unrealized_pnl: float = 0.0
    total_open_risk: float = Field(default=0.0, ge=0.0)
    open_position_count: int = Field(default=0, ge=0)
    per_symbol_daily_loss: dict[str, float] = Field(default_factory=dict)
    theme_exposure: dict[str, int] = Field(default_factory=dict)
    kill_switch_active: bool = False
    daily_loss_breached: bool = False
    kill_switch_reason: str = ""

    @property
    def daily_pnl(self) -> float:
        return self.daily_realized_pnl + self.daily_unrealized_pnl

    @property
    def is_kill_switch_on(self) -> bool:
        return self.kill_switch_active or self.daily_loss_breached


class ExitDecision(BaseModel):
    """An exit decision for an open position."""

    symbol: str
    should_exit: bool = False
    exit_pct: int = Field(default=100, ge=0, le=100)
    reason: str = ""
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    pnl_r: Optional[float] = None
    remaining_shares: int = 0


class EntryInfo(BaseModel):
    """Nested entry fields for DecisionRecord JSONL output (SPEC §22.15 item 20).

    Every key is nullable — null values appear explicitly in the JSON.
    """

    price: Optional[float] = None
    stop: Optional[float] = None
    risk_per_share: Optional[float] = None
    shares: Optional[int] = None
    risk_amount: Optional[float] = None


class ExitInfo(BaseModel):
    """Nested exit fields for DecisionRecord JSONL output (SPEC §22.15 item 20).

    Every key is nullable — null values appear explicitly in the JSON.
    """

    reason: Optional[str] = None
    pnl: Optional[float] = None
    pnl_r: Optional[float] = None
    remaining_shares: Optional[int] = None


class DecisionRecord(BaseModel):
    """Full audit record for one symbol decision in a single cycle.

    Serializable to a single JSON line for JSONL logging.
    Contains both flat fields (backward compat) and nested ``entry``/``exit``
    objects per SPEC §22.15 item 20.
    """

    symbol: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source: Optional[str] = None
    source_timestamp: Optional[str] = None
    scanner_age_seconds: Optional[float] = None
    quote_age_seconds: Optional[float] = None
    attention_score: Optional[float] = None
    attention_drivers: list[str] = Field(default_factory=list)
    data_confidence: Optional[float] = None
    hard_blocks: list[str] = Field(default_factory=list)
    soft_warnings: list[str] = Field(default_factory=list)
    state: Optional[str] = None
    state_evidence: list[str] = Field(default_factory=list)
    mode: Optional[str] = None
    entry_setup: Optional[str] = None
    entry_price: Optional[float] = None
    entry_stop: Optional[float] = None
    entry_risk_per_share: Optional[float] = None
    entry_shares: Optional[int] = None
    entry_risk_amount: Optional[float] = None
    exit_reason: Optional[str] = None
    exit_pnl: Optional[float] = None
    exit_pnl_r: Optional[float] = None
    exit_remaining_shares: Optional[int] = None
    decision: str = ""
    reason: str = ""
    entry: EntryInfo = Field(default_factory=EntryInfo)
    exit: ExitInfo = Field(default_factory=ExitInfo)

    def to_json_line(self) -> str:
        """Serialize to a single JSON line (no trailing newline)."""
        return self.model_dump_json()

    @classmethod
    def from_json_line(cls, line: str) -> "DecisionRecord":
        """Deserialize from a single JSON line."""
        data = json.loads(line)
        return cls(**data)
