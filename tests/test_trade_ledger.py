import json
from unittest.mock import MagicMock

from src.models.schemas import EntrySetupType, EntrySignal, PositionState
from src.paper_execution import AlpacaExecutionGateway, PaperExecutionGateway
from src.trade_ledger import TradeLedger


def _signal(symbol: str = "DSY", entry: float = 10.0, stop: float = 9.0, shares: int = 10) -> EntrySignal:
    return EntrySignal(
        symbol=symbol,
        entry_setup=EntrySetupType.FIRST_PULLBACK,
        entry_price=entry,
        stop_price=stop,
        risk_per_share=abs(entry - stop),
        target_price=entry + 2 * abs(entry - stop),
        proposed_shares=shares,
        risk_amount=abs(entry - stop) * shares,
        invalidation="test",
    )


def _records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


class _AlpacaOrder:
    def __init__(self, id: str, status: str = "new", *, filled_qty: str = "0", filled_avg_price: str | None = None):
        self.id = id
        self.status = status
        self.filled_qty = filled_qty
        self.filled_avg_price = filled_avg_price


def test_entry_and_partial_exit_fills_append_concise_trade_records(tmp_path):
    ledger_path = tmp_path / "executed_trades.jsonl"
    gw = PaperExecutionGateway(trade_ledger=TradeLedger(ledger_path))

    entry_order, _ = gw.submit_entry(_signal())
    gw.confirm_fill(entry_order.order_id)
    exit_order, _ = gw.submit_exit("DSY", "scale_out", exit_pct=50, exit_price=12.0)
    pos = gw.confirm_exit_fill(exit_order.order_id)

    records = _records(ledger_path)
    assert [record["event"] for record in records] == ["entry_fill", "exit_fill"]

    entry = records[0]
    assert entry["symbol"] == "DSY"
    assert entry["side"] == "buy"
    assert entry["entry_order_id"] == entry_order.order_id
    assert entry["entry_fill_price"] == 10.0
    assert entry["quantity"] == 10
    assert entry["current_shares"] == 10
    assert entry["entry_setup"] == "first_pullback"
    assert entry["intended_risk"] == 10.0

    exit_record = records[1]
    assert exit_record["side"] == "sell"
    assert exit_record["exit_order_id"] == exit_order.order_id
    assert exit_record["exit_reason"] == "scale_out"
    assert exit_record["exit_fill_price"] == 12.0
    assert exit_record["quantity"] == 5
    assert exit_record["remaining_shares"] == 5
    assert exit_record["realized_pnl"] == 10.0
    assert exit_record["win_loss"] == "win"
    assert exit_record["r_multiple"] == 2.0
    assert pos.current_shares == 5


def test_full_losing_exit_logs_loss_without_corrupting_remaining_shares(tmp_path):
    ledger_path = tmp_path / "executed_trades.jsonl"
    gw = PaperExecutionGateway(trade_ledger=TradeLedger(ledger_path))

    entry_order, _ = gw.submit_entry(_signal(shares=4))
    gw.confirm_fill(entry_order.order_id)
    exit_order, _ = gw.submit_exit("DSY", "hard_stop", exit_price=8.5)
    pos = gw.confirm_exit_fill(exit_order.order_id)

    exit_record = _records(ledger_path)[1]
    assert exit_record["quantity"] == 4
    assert exit_record["remaining_shares"] == 0
    assert exit_record["realized_pnl"] == -6.0
    assert exit_record["win_loss"] == "loss"
    assert exit_record["r_multiple"] == -1.5
    assert pos.current_shares == 0


def test_main_runtime_components_wire_executed_trade_ledger():
    from config.settings import Settings
    from main import _build_components

    gw, _, _, _ = _build_components(Settings())

    assert gw._trade_ledger is not None


def test_alpaca_confirmed_fills_use_broker_fill_price_in_trade_ledger(tmp_path):
    ledger_path = tmp_path / "executed_trades.jsonl"
    gw = AlpacaExecutionGateway(
        api_key="test_key",
        secret_key="test_secret",
        trade_ledger=TradeLedger(ledger_path),
    )
    gw._client = MagicMock()

    gw._client.submit_order.return_value = _AlpacaOrder("entry-1")
    entry_order, _ = gw.submit_entry(_signal(entry=10.0, stop=9.0, shares=7))
    gw._client.get_order_by_id.return_value = _AlpacaOrder(
        "entry-1",
        status="filled",
        filled_qty="7",
        filled_avg_price="10.25",
    )
    gw.confirm_fill(entry_order.order_id)

    gw._client.submit_order.return_value = _AlpacaOrder("exit-1")
    exit_order, _ = gw.submit_exit("DSY", "target")
    gw._client.get_order_by_id.return_value = _AlpacaOrder(
        "exit-1",
        status="filled",
        filled_qty="7",
        filled_avg_price="11.00",
    )
    gw.confirm_exit_fill(exit_order.order_id)

    entry, exit_record = _records(ledger_path)
    assert entry["entry_order_id"] == "entry-1"
    assert entry["entry_fill_price"] == 10.25
    assert entry["quantity"] == 7
    assert exit_record["exit_order_id"] == "exit-1"
    assert exit_record["exit_fill_price"] == 11.0
    assert exit_record["realized_pnl"] == 5.25
    assert exit_record["win_loss"] == "win"
    assert exit_record["r_multiple"] == 0.75


def test_add_fill_is_logged_and_combined_position_exit_defers_r_multiple(tmp_path):
    ledger_path = tmp_path / "executed_trades.jsonl"
    gw = PaperExecutionGateway(trade_ledger=TradeLedger(ledger_path))

    entry_order, _ = gw.submit_entry(_signal(shares=10))
    pos = gw.confirm_fill(entry_order.order_id)
    pos.state = PositionState.RUNNER
    gw.positions.upsert(pos)

    add_order, _ = gw.submit_add("DSY", qty=5, entry_price=11.0, stop_price=10.0)
    gw.confirm_fill(add_order.order_id)
    exit_order, _ = gw.submit_exit("DSY", "runner_exit", exit_price=12.0)
    gw.confirm_exit_fill(exit_order.order_id)

    _, add_record, exit_record = _records(ledger_path)
    assert add_record["event"] == "add_fill"
    assert add_record["side"] == "buy"
    assert add_record["entry_order_id"] == add_order.order_id
    assert add_record["entry_fill_price"] == 11.0
    assert add_record["quantity"] == 5
    assert add_record["current_shares"] == 15
    assert exit_record["realized_pnl"] == 25.05
    assert exit_record["r_multiple"] is None
