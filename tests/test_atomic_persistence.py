"""Atomic persistence round-trip and crash-recovery tests.

Verifies that PositionStore, PendingOrderStore, and PnLLedger all
save/load correctly via the atomic-write helper and that a mid-write
failure leaves the prior file intact with no temp garbage.
"""

from __future__ import annotations

import json
import os

import pytest

from src._atomic import write_json_atomically
from src.pnl_ledger import PnLLedger
from src.state_machine import PendingOrderStore, PositionStore
from src.models.schemas import OrderActionType, PendingOrder, PositionState, PositionStateModel


# ──────────────────────────────────────────────────────────────────
#  Helper: count .tmp files in a directory
# ──────────────────────────────────────────────────────────────────


def _tmp_count(path) -> int:
    return len([e for e in os.listdir(str(path)) if e.endswith(".tmp")])


# ──────────────────────────────────────────────────────────────────
#  Atomic helper itself
# ──────────────────────────────────────────────────────────────────


class TestWriteJsonAtomically:
    def test_round_trip(self, tmp_path):
        data = {"hello": "world", "nested": [1, 2.5, None]}
        p = tmp_path / "test.json"
        write_json_atomically(data, p)
        assert p.exists()
        assert p.read_text(encoding="utf-8") == '{"hello": "world", "nested": [1, 2.5, null]}'
        assert _tmp_count(tmp_path) == 0

    def test_creates_parent_directory(self, tmp_path):
        data = {"x": 1}
        p = tmp_path / "a" / "b" / "c" / "test.json"
        write_json_atomically(data, p)
        assert p.exists()

    def test_overwrites_existing(self, tmp_path):
        p = tmp_path / "test.json"
        p.write_text("old")
        write_json_atomically({"new": True}, p)
        assert json.loads(p.read_text()) == {"new": True}
        assert _tmp_count(tmp_path) == 0

    def test_failure_cleans_up_temp_and_leaves_original(self, tmp_path):
        p = tmp_path / "test.json"
        data = {"original": True}
        write_json_atomically(data, p)
        assert p.read_text() == json.dumps(data)

        orig_content = p.read_text()

        # Simulate os.replace failure
        import src._atomic as _at
        orig_replace = os.replace
        try:
            os.replace = lambda src, dst: (_ for _ in ()).throw(OSError("mock failure"))
            with pytest.raises(OSError, match="mock failure"):
                write_json_atomically({"new": "data"}, p)
        finally:
            os.replace = orig_replace

        # Original file untouched
        assert p.read_text() == orig_content
        # No temp files left
        assert _tmp_count(tmp_path) == 0


# ──────────────────────────────────────────────────────────────────
#  PositionStore
# ──────────────────────────────────────────────────────────────────


class TestPositionStoreAtomicPersistence:
    def _pos(self, symbol="DSY", state=PositionState.OPEN, **kw):
        return PositionStateModel(symbol=symbol, state=state, **kw)

    def test_round_trip(self, tmp_path):
        store = PositionStore()
        store.upsert(self._pos("DSY", current_shares=50, entry_price=10.50))
        store.upsert(self._pos("AAPL", current_shares=100, entry_price=200.0))
        path = tmp_path / "positions.json"
        store.save_to_disk(path)
        assert _tmp_count(tmp_path) == 0

        loaded = PositionStore.load_from_disk(path)
        dsy = loaded.get("DSY")
        aapl = loaded.get("AAPL")
        assert dsy is not None
        assert aapl is not None
        assert dsy.current_shares == 50
        assert dsy.entry_price == 10.50
        assert aapl.current_shares == 100
        assert _tmp_count(tmp_path) == 0

    def test_failure_leaves_prior_intact(self, tmp_path):
        store = PositionStore()
        store.upsert(self._pos("DSY", current_shares=50))
        path = tmp_path / "positions.json"
        store.save_to_disk(path)
        orig = path.read_text()

        import src._atomic as _at
        orig_replace = os.replace
        try:
            os.replace = lambda src, dst: (_ for _ in ()).throw(OSError("mock"))
            with pytest.raises(OSError):
                store.save_to_disk(path)
        finally:
            os.replace = orig_replace

        assert path.read_text() == orig
        assert _tmp_count(tmp_path) == 0


# ──────────────────────────────────────────────────────────────────
#  PendingOrderStore
# ──────────────────────────────────────────────────────────────────


class TestPendingOrderStoreAtomicPersistence:
    def _order(self, symbol="DSY", oid="o1", otype=OrderActionType.ENTRY):
        return PendingOrder(symbol=symbol, order_id=oid, order_type=otype, side="buy")

    def test_round_trip(self, tmp_path):
        store = PendingOrderStore()
        store.add(self._order("DSY", "o1"))
        store.add(self._order("AAPL", "o2"))
        path = tmp_path / "pending.json"
        store.save_to_disk(path)
        assert _tmp_count(tmp_path) == 0

        restored = PendingOrderStore()
        orders = restored.load_from_disk(path)
        assert len(orders) == 2
        assert restored.has_pending_buy("DSY") is True
        assert restored.has_pending_buy("AAPL") is True
        assert _tmp_count(tmp_path) == 0

    def test_failure_leaves_prior_intact(self, tmp_path):
        store = PendingOrderStore()
        store.add(self._order("DSY", "o1"))
        path = tmp_path / "pending.json"
        store.save_to_disk(path)
        orig = path.read_text()

        import src._atomic as _at
        orig_replace = os.replace
        try:
            os.replace = lambda src, dst: (_ for _ in ()).throw(OSError("mock"))
            with pytest.raises(OSError):
                store.save_to_disk(path)
        finally:
            os.replace = orig_replace

        assert path.read_text() == orig
        assert _tmp_count(tmp_path) == 0


# ──────────────────────────────────────────────────────────────────
#  PnLLedger
# ──────────────────────────────────────────────────────────────────


class TestPnLLedgerAtomicPersistence:
    def test_round_trip(self, tmp_path):
        ledger = PnLLedger(
            session_realized_pnl=150.25,
            per_symbol_pnl={"DSY": 100.0, "AAPL": 50.25},
            weekly_realized_pnl=500.0,
            consecutive_losses=1,
            week_id="2026-W29",
        )
        path = tmp_path / "pnl.json"
        ledger.save_to_disk(path)
        assert _tmp_count(tmp_path) == 0

        loaded = PnLLedger.load_from_disk(path)
        assert loaded.session_realized_pnl == 150.25
        assert loaded.per_symbol_pnl == {"DSY": 100.0, "AAPL": 50.25}
        assert loaded.weekly_realized_pnl == 500.0
        assert loaded.consecutive_losses == 1
        assert loaded.week_id == "2026-W29"
        assert _tmp_count(tmp_path) == 0

    def test_round_trip_empty(self, tmp_path):
        ledger = PnLLedger()
        path = tmp_path / "pnl_empty.json"
        ledger.save_to_disk(path)
        loaded = PnLLedger.load_from_disk(path)
        assert loaded.session_realized_pnl == 0.0
        assert loaded.per_symbol_pnl == {}
        assert loaded.weekly_realized_pnl == 0.0
        assert loaded.consecutive_losses == 0
        assert loaded.week_id == ""

    def test_failure_leaves_prior_intact(self, tmp_path):
        ledger = PnLLedger(session_realized_pnl=100.0)
        path = tmp_path / "pnl.json"
        ledger.save_to_disk(path)
        orig = path.read_text()

        import src._atomic as _at
        orig_replace = os.replace
        try:
            os.replace = lambda src, dst: (_ for _ in ()).throw(OSError("mock"))
            with pytest.raises(OSError):
                ledger.save_to_disk(path)
        finally:
            os.replace = orig_replace

        assert path.read_text() == orig
        assert _tmp_count(tmp_path) == 0

    def test_load_nonexistent_returns_empty(self, tmp_path):
        loaded = PnLLedger.load_from_disk(tmp_path / "nonexistent.json")
        assert loaded.session_realized_pnl == 0.0
