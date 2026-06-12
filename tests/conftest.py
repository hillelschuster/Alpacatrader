"""
Shared test fixtures for Alpacatrader rebuild.

Provides common test objects (Candidate, EntrySignal, PaperExecutionGateway)
used across multiple test files to reduce duplication.

Also filters unavoidable third-party warnings.
"""

from __future__ import annotations

import warnings

import pytest

from src.models.schemas import Candidate, EntrySetupType, EntrySignal
from src.paper_execution import PaperExecutionGateway

# ──────────────────────────────────────────────────────────────────
#  Third-party warning filters
# ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _filter_third_party_warnings() -> None:
    """Filter unavoidable third-party DeprecationWarnings.

    - websockets.legacy: triggered by alpaca-py's internal dependency.
      Tracked at https://github.com/alpacahq/alpaca-py/issues (no fix yet).
    """
    warnings.filterwarnings(
        "ignore",
        message="websockets.legacy is deprecated",
        category=DeprecationWarning,
    )


# ──────────────────────────────────────────────────────────────────
#  Shared fixtures
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def candidate() -> Candidate:
    """A basic test candidate — DSY-like mid-price momentum stock."""
    return Candidate(
        symbol="DSY",
        price=10.50,
        percent_gain=25.0,
        source="finviz",
        sector="Technology",
        industry="Software",
    )


@pytest.fixture
def fake_entry_signal() -> EntrySignal:
    """A valid first-pullback EntrySignal for lifecycle/execution tests."""
    return EntrySignal(
        symbol="DSY",
        entry_setup=EntrySetupType.FIRST_PULLBACK,
        entry_price=10.50,
        stop_price=10.30,
        risk_per_share=0.20,
        target_price=10.90,
        proposed_shares=100,
        risk_amount=20.0,
        invalidation="price trades below pullback low",
    )


@pytest.fixture
def paper_gateway() -> PaperExecutionGateway:
    """Fresh PaperExecutionGateway with empty positions and pending orders."""
    return PaperExecutionGateway()
