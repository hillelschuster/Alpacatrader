"""
Negative-import tests: verify legacy source modules have been deleted.

Each test cleans sys.modules before importing to avoid cache false positives.
Only modules that are NOT imported by any rebuild module are tested here.
"""

import importlib
import sys
from typing import Callable

import pytest


# Modules that should raise ModuleNotFoundError after Batch B cleanup.
# These are all legacy-only — not imported by any rebuild module.
_DELETED_MODULES: list[str] = [
    # Top-level legacy modules
    "src.pipeline.v3_pipeline",
    "src.anti_patterns",
    "src.pillars",
    "src.regime",
    # Legacy packages
    "src.agents",
    "src.analysis.deep_analysis",
    "src.entry.pattern_detector",
    "src.exit.rules",
    "src.execution.engine",
    "src.risk.manager",
    "src.strategy.momentum",
    # Legacy journal
    "src.journal.dimensions",
    "src.journal.trade_journal",
    # Other legacy modules
    "src.halts",
    "src.session",
    "src.premarket",
    "src.data_validator",
    "src.indicators",
    "src.persistence",
    # Legacy models (Batch B)
    "src.models.funnel",
    "src.models.thesis",
    # Legacy providers (Batch B)
    "src.providers",
    "src.providers.mock",
    "src.providers.alpaca",
]


def _clean_module(key: str) -> None:
    """Remove *key* and all its sub-modules from sys.modules."""
    prefixes = (key, key + ".")
    to_delete = [k for k in sys.modules if k.startswith(prefixes)]
    for k in to_delete:
        del sys.modules[k]


@pytest.mark.parametrize("module_path", _DELETED_MODULES)
def test_legacy_module_deleted(module_path: str) -> None:
    """Assert that the given legacy module path cannot be imported."""
    _clean_module(module_path)
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_path)


# ── Explicit top-level smoke test for the most critical path ──────

def test_v3_pipeline_import_raises() -> None:
    """Highest-risk path — confirm v3_pipeline is gone."""
    _clean_module("src.pipeline")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("src.pipeline.v3_pipeline")


def test_legacy_packages_absent() -> None:
    """Verify entire legacy package directories are not importable."""
    for pkg in ("pipeline", "agents", "analysis", "entry", "exit", "execution", "risk", "strategy", "providers"):
        full = f"src.{pkg}"
        _clean_module(full)
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(full)
