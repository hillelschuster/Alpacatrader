# Phase Gap Remediation Implementation Plan (Historical)

> **Status:** historical execution plan. This file is **not** the active source of truth and must not be used as a live checklist. The root `SPEC.md` is the single active implementation source of truth; current remaining work is tracked in `SPEC.md` §10.1.
>
> The unchecked checkbox syntax below is preserved from the original pre-implementation plan for auditability. It does **not** represent current task status. Tasks 1-13 were implemented and reconciled into `SPEC.md`; later scanner fallback wiring is also reflected in `SPEC.md`.

**Historical goal:** close all confirmed runtime bugs, finish falsely-completed work, complete the highest-value deferred tasks, and make `SPEC.md`, code, and tests truthful again.

**Architecture:** fix runtime-truth and safety wiring first (`main.py`, `app.py`, `paper_execution.py`) because those gaps can hide or create live risk. Then fix stale and invalid market-data handling in exits and entries. After the safety surface is correct, restore entry-quality behavior (classifier features, float enrichment), then finish parser coverage, weak-test cleanup, low-priority dedup, and a final `SPEC.md` truthfulness pass.

**Tech Stack:** Python, Pydantic v2, alpaca-py, Click, pytest

---

## File Map

- `main.py` — real runtime wiring for `TradingApp` in paper loop mode.
- `src/app.py` — startup reconciliation, monitor policy, scan-entry gating, market-hours behavior, persistence.
- `src/paper_execution.py` — paper/Alpaca order lifecycle, stop cancellation, partial-exit reprotection, reconciliation helpers.
- `src/decision_pipeline.py` — entry preflight, sizing validation, pre-submit quote recheck, classifier feature wiring.
- `src/exits.py` — fresh-data gating for hard-stop and invalidation exits.
- `src/sizing.py` — enforced and configurable `max_trade_risk_pct`.
- `config/settings.py` — `Phase1Settings` runtime risk fields.
- `src/classifier_features.py` — new helper module for runtime-derived classifier inputs.
- `src/move_classifier.py` — state classification from runtime-derived features only.
- `src/market_data.py` / `src/market_data_sim.py` — quote and bar enrichment, later shared-math cleanup.
- `src/scanner/enrichment.py` / `src/scanner/scanner.py` — Finviz parsing, stale detection, yfinance float enrichment.
- `src/annotations.py` — soft-warning truth after float and news cleanup.
- `tests/test_phase10_app.py` — monitor, scan, time-gate, runtime-wiring, persistence, and reconciliation integration tests.
- `tests/test_phase7_execution.py` — Alpaca and paper execution lifecycle tests.
- `tests/test_phase6_risk.py` — open-risk, max-trade-risk, daily-loss, per-symbol-loss tests.
- `tests/test_phase4_classifier.py` — runtime-derived classifier coverage.
- `tests/test_phase9_pipeline.py` — pipeline truthfulness and weak-assertion cleanup.
- `tests/test_settings.py` / `tests/test_cli_rebuild.py` — runtime config and CLI wiring tests.
- `SPEC.md` — final truthfulness update after code and tests land.

---

### Task 1: Wire Real Broker Truth And Persistence Into The Actual Paper Loop

**Files:**
- Modify: `main.py`
- Test: `tests/test_cli_rebuild.py`
- Test: `tests/test_phase10_app.py`

- [ ] **Step 1: Pass `broker_snapshot_fn` into `_run_paper_loop()`**

```python
from functools import partial
from src.paper_execution import build_alpaca_broker_snapshot

app = TradingApp(
    scanner_fn=scan_finviz_candidates,
    market_data_fn=partial(build_market_snapshot, api_key=ak, secret_key=sk),
    logger=logger_inst,
    execution_gw=gw,
    position_store=gw.positions,
    broker_snapshot_fn=partial(build_alpaca_broker_snapshot, gw),
    persist_path="data/positions.json",
    monitor_interval_seconds=p1.monitor_interval_seconds,
    scan_interval_seconds=p1.scanner_interval_seconds,
    **risk_kwargs,
    paper_mode=True,
)
```

- [ ] **Step 2: Keep persistence simple and real**

```python
persist_path="data/positions.json"
```

Use one concrete runtime path now. Do not add config churn in this batch.

- [ ] **Step 3: Add CLI-level wiring tests**

```python
def test_run_paper_loop_wires_broker_snapshot_and_persist_path(monkeypatch, settings):
    captured = {}

    class FakeApp:
        def __init__(self, **kwargs):
            captured.update(kwargs)
        def run(self):
            return None

    monkeypatch.setattr("main.TradingApp", FakeApp)
    _run_paper_loop(settings)

    assert callable(captured["broker_snapshot_fn"])
    assert captured["persist_path"] == "data/positions.json"
```

- [ ] **Step 4: Add a runtime roundtrip test at the app boundary**

```python
def test_startup_reconciliation_runs_when_loop_mode_wires_snapshot(tmp_path):
    gw = PaperExecutionGateway()
    app = TradingApp(
        execution_gw=gw,
        broker_snapshot_fn=lambda: {"DSY": (50, 10.50)},
        persist_path=str(tmp_path / "positions.json"),
    )
    app._reconcile_on_startup()
    assert gw.positions.get("DSY") is not None
```

- [ ] **Step 5: Verify**

Run: `pytest tests/test_cli_rebuild.py tests/test_phase10_app.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_cli_rebuild.py tests/test_phase10_app.py
git commit -m "fix: wire paper loop broker truth"
```

---

### Task 2: Cancel Broker-Side Stops Correctly Before Alpaca Exits

**Files:**
- Modify: `src/paper_execution.py`
- Test: `tests/test_phase7_execution.py`

- [ ] **Step 1: Override Alpaca cancel behavior instead of using local-only cancel**

```python
def cancel_order(self, order_id: str) -> bool:
    for order in list(self._pending.all_pending()):
        if order.order_id == order_id:
            self.client.cancel_order_by_id(order_id)
            self._pending.resolve(order_id, "cancelled")
            return True
    return False
```

- [ ] **Step 2: Keep `cancel_stale_orders()` using dynamic dispatch**

```python
def cancel_stale_orders(self, symbol: str) -> int:
    count = 0
    for order in list(self._pending.get_for_symbol(symbol)):
        if self.cancel_order(order.order_id):
            count += 1
    return count
```

- [ ] **Step 3: Add a broker-cancel assertion test**

```python
def test_submit_exit_cancels_broker_stop_before_market_sell(alpaca_gw, mock_alpaca_client):
    self._open_position(alpaca_gw, mock_alpaca_client)
    alpaca_gw.place_stop("DSY", 10.30, 50)
    mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="exit-1")

    alpaca_gw.submit_exit("DSY", "target hit")

    mock_alpaca_client.cancel_order_by_id.assert_called()
```

- [ ] **Step 4: Add a reconciliation-side broker-cancel assertion**

```python
def test_cancel_stale_order_reaches_gateway_not_just_log(alpaca_gw, mock_alpaca_client):
    self._open_position(alpaca_gw, mock_alpaca_client)
    alpaca_gw.place_stop("DSY", 10.30, 50)
    mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="exit-1")
    alpaca_gw.submit_exit("DSY", "target hit")
    assert mock_alpaca_client.cancel_order_by_id.called
```

- [ ] **Step 5: Verify**

Run: `pytest tests/test_phase7_execution.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/paper_execution.py tests/test_phase7_execution.py
git commit -m "fix: cancel broker stops on alpaca exits"
```

---

### Task 3: Reprotect Remaining Shares And Avoid EXITING Zombies On Exit Submit Failure

**Files:**
- Modify: `src/paper_execution.py`
- Test: `tests/test_phase7_execution.py`
- Test: `tests/test_phase10_app.py`

- [ ] **Step 1: Capture the existing stop before cancelling exit-side protection**

```python
existing_stop = pos.stop_price
existing_qty = pos.current_shares
```

- [ ] **Step 2: Do not leave the position in `EXITING` if Alpaca submit fails**

```python
try:
    self.cancel_stale_orders(symbol)
    alpaca_order = self.client.submit_order(req)
except Exception:
    if existing_stop is not None and existing_qty > 0:
        self.place_stop(symbol, existing_stop, existing_qty)
    raise

transition_position(pos, PositionState.EXITING, force=True)
```

- [ ] **Step 3: Reprotect on partial exits in both paper and Alpaca gateways**

```python
remaining = max(pos.current_shares - filled_qty, 0)
pos.current_shares = remaining
if remaining == 0:
    pos.state = PositionState.CLOSED
elif pos.stop_price is not None:
    self.place_stop(pos.symbol, pos.stop_price, remaining)
```

- [ ] **Step 4: Add explicit tests for partial exit protection**

```python
def test_confirm_exit_partial_fill_replaces_stop_for_remaining_qty(alpaca_gw, mock_alpaca_client):
    self._open_position(alpaca_gw, mock_alpaca_client)
    alpaca_gw.place_stop("DSY", 10.30, 50)
    mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="exit-1")
    exit_order, _ = alpaca_gw.submit_exit("DSY", "partial", exit_pct=100)
    mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
        id="exit-1", status="partially_filled", filled_qty="20", filled_avg_price="11.00",
    )
    pos = alpaca_gw.confirm_exit_fill(exit_order.order_id)
    assert pos.current_shares == 30
    assert pos.state != PositionState.CLOSED
    stop_orders = [o for o in alpaca_gw.pending.get_for_symbol("DSY") if o.order_type == OrderActionType.STOP]
    assert len(stop_orders) == 1
    assert stop_orders[0].qty == 30
```

- [ ] **Step 5: Add API-failure rollback tests**

```python
def test_submit_exit_api_failure_restores_protection_and_keeps_position_open(alpaca_gw, mock_alpaca_client):
    self._open_position(alpaca_gw, mock_alpaca_client)
    alpaca_gw.place_stop("DSY", 10.30, 50)
    mock_alpaca_client.submit_order.side_effect = ConnectionError("API down")
    with pytest.raises(RuntimeError):
        alpaca_gw.submit_exit("DSY", "test")
    pos = alpaca_gw.positions.get("DSY")
    assert pos.state == PositionState.OPEN
    assert alpaca_gw._has_pending_stop("DSY")
```

- [ ] **Step 6: Verify**

Run: `pytest tests/test_phase7_execution.py tests/test_phase10_app.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/paper_execution.py tests/test_phase7_execution.py tests/test_phase10_app.py
git commit -m "fix: reprotect remaining shares after exits"
```

---

### Task 4: Remove Remaining Synthetic Zero-Price Fallthrough And Gate Hard Stops On Fresh Quotes

**Files:**
- Modify: `src/app.py`
- Modify: `src/decision_pipeline.py`
- Modify: `src/exits.py`
- Test: `tests/test_phase10_app.py`
- Test: `tests/test_phase8_exits.py`

- [ ] **Step 1: Stop fabricating `0.0` in monitor mode**

```python
current_price: Optional[float] = None

if snapshot is None:
    if self._execution._has_pending_stop(pos.symbol):
        continue
    self._execution.mark_unprotected(pos.symbol)
else:
    price = snapshot.candidate.price
    if price is not None and price > 0:
        current_price = price
```

- [ ] **Step 2: Pass `None` through the exit path instead of `candidate.price or 0`**

```python
exit_dec = run_exits(
    pos,
    current_price=candidate.price,
    risk_per_share=pos.entry_price - pos.stop_price if pos.entry_price and pos.stop_price else None,
    position_unprotected=position_unprotected,
    spread_pct=spread_pct,
    quote_age_seconds=quote_age_seconds,
    bars=bars,
    vwap=vwap,
    move_state=state,
    entry_setup=result.entry_signal.entry_setup.value if result.entry_signal else None,
    prior_hod=prior_hod,
    daily_loss_breached=daily_loss_breached,
    per_symbol_loss_capped=per_symbol_loss_capped,
    halt_count_today=halt_count_today,
    et_time=et_time,
)
```

- [ ] **Step 3: Gate price-based exits on freshness**

```python
def check_hard_stop(position, current_price: Optional[float], quote_age_seconds: Optional[float], stop_price: Optional[float]):
    if current_price is None:
        return None
    if stop_price is None:
        return None
    if quote_age_seconds is None or quote_age_seconds > 15:
        return None
    if current_price <= stop_price:
        return ExitDecision(should_exit=True, reason="hard_stop", exit_pct=100)
    return None
```

Apply the same rule to invalidation and other price-dependent exit helpers. Keep the existing P1 emergency rule for `quote_age_seconds > 60`.

- [ ] **Step 4: Add monitor tests that prove `None` does not become `0.0`**

```python
def test_protected_data_outage_holds_position_open(tmp_path):
    logger = DecisionLogger(tmp_path / "decisions.jsonl")
    gw = PaperExecutionGateway()
    pos = PositionStateModel(symbol="DSY", state=PositionState.OPEN, entry_price=10.50, stop_price=10.30, current_shares=50, average_entry=10.50)
    gw.positions.upsert(pos)
    gw.place_stop("DSY", 10.30, 50)
    app = TradingApp(execution_gw=gw, logger=logger, market_data_fn=lambda c: None)
    app._monitor_positions()
    pos = gw.positions.get("DSY")
    assert pos.state == PositionState.OPEN
    assert pos.current_shares == 50
```

- [ ] **Step 5: Add stale-but-not-emergency stop tests**

```python
def test_stale_30s_quote_does_not_fire_hard_stop():
    pos = PositionStateModel(symbol="DSY", state=PositionState.OPEN, entry_price=10.50, stop_price=10.30, current_shares=50, average_entry=10.50)
    result = check_exits(
        pos,
        current_price=10.20,
        risk_per_share=0.20,
        position_unprotected=False,
        quote_age_seconds=30.0,
        spread_pct=0.5,
    )
    assert result is None or result.reason != "hard_stop"
```

- [ ] **Step 6: Update the old false-exit test expectations**

```python
def test_monitor_no_network_calls(tmp_path):
    logger = DecisionLogger(tmp_path / "decisions.jsonl")
    gw = PaperExecutionGateway()
    pos = PositionStateModel(symbol="DSY", state=PositionState.OPEN, entry_price=10.50, stop_price=10.30, current_shares=50, average_entry=10.50)
    gw.positions.upsert(pos)
    app = TradingApp(execution_gw=gw, logger=logger)
    app._monitor_positions()
    pos_after = gw.positions.get("DSY")
    assert pos_after.state in (PositionState.OPEN, PositionState.UNPROTECTED)
```

- [ ] **Step 7: Verify**

Run: `pytest tests/test_phase10_app.py tests/test_phase8_exits.py -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/app.py src/decision_pipeline.py src/exits.py tests/test_phase10_app.py tests/test_phase8_exits.py
git commit -m "fix: remove synthetic zero price exits"
```

---

### Task 5: Finish Entry-Side Risk Gates — Per-Symbol Loss Cap And Configurable `max_trade_risk_pct`

**Files:**
- Modify: `config/settings.py`
- Modify: `main.py`
- Modify: `src/app.py`
- Modify: `src/decision_pipeline.py`
- Modify: `src/hard_filters.py`
- Modify: `src/sizing.py`
- Test: `tests/test_phase6_risk.py`
- Test: `tests/test_settings.py`

- [ ] **Step 1: Re-add `max_trade_risk_pct` to `Phase1Settings`**

```python
class Phase1Settings(BaseSettings):
    starter_risk_pct: float = 0.0025
    max_trade_risk_pct: float = 0.01
    max_daily_loss_pct: float = 0.03
    max_positions: int = 3
    max_open_risk_pct: float = 0.03
```

- [ ] **Step 2: Thread `max_trade_risk_pct` through runtime wiring**

```python
risk_kwargs = dict(
    starter_risk_pct=p1.starter_risk_pct,
    max_trade_risk_pct=p1.max_trade_risk_pct,
    max_positions=p1.max_positions,
    max_open_risk_pct=p1.max_open_risk_pct,
    max_daily_loss_pct=p1.max_daily_loss_pct,
    focus_price_min=p1.focus_price_min,
    focus_price_max=p1.focus_price_max,
)
```

- [ ] **Step 3: Pass `per_symbol_loss_capped` on the scan path**

```python
symbol_loss = self._risk_state.per_symbol_daily_loss.get(c.symbol, 0.0)
per_symbol_loss_capped = (
    symbol_loss < 0
    and abs(symbol_loss) >= self._max_daily_loss_pct * self._equity
)

result = run_pipeline(
    snapshot.candidate,
    bars=snapshot.bars,
    vwap=snapshot.vwap,
    ema9=snapshot.ema9,
    day_high=snapshot.day_high,
    prior_hod=snapshot.prior_hod,
    quote_age_seconds=snapshot.quote_age_seconds,
    spread_pct=snapshot.spread_pct,
    rvol=snapshot.rvol,
    dollar_volume_5m=snapshot.dollar_volume_5m,
    halt_count_today=snapshot.halt_count_today,
    execution_gw=self._execution,
    position_store=self._positions,
    logger=self._logger,
    equity=self._equity,
    starter_risk_pct=self._starter_risk_pct,
    max_trade_risk_pct=self._max_trade_risk_pct,
    per_symbol_loss_capped=per_symbol_loss_capped,
)
```

- [ ] **Step 4: Enforce the per-symbol cap as a hard block**

```python
if per_symbol_loss_capped:
    blocks.append("per_symbol_loss_cap_breached")
```

- [ ] **Step 5: Use the configured trade-risk cap in sizing**

```python
shares, starter, adj_risk, risk_amount = entry_sizing(
    equity,
    signal.risk_per_share,
    starter_risk_pct=starter_risk_pct,
    max_trade_risk_pct=max_trade_risk_pct,
    attention_score=result.attention_score,
    soft_multiplier=soft_mult,
    data_confidence=result.data_confidence or 1.0,
)
```

- [ ] **Step 6: Add tests that prove both gates work at runtime**

```python
def test_per_symbol_loss_cap_blocks_scan_entry(force_entry, gw):
    result = run_pipeline(
        _candidate(symbol="DSY", price=10.50),
        bars=_surge_bars(),
        vwap=10.20,
        ema9=10.10,
        day_high=10.55,
        quote_age_seconds=2.0,
        spread_pct=0.5,
        rvol=5.0,
        dollar_volume_5m=500_000,
        equity=100_000,
        execution_gw=gw,
        position_store=gw.positions,
        per_symbol_loss_capped=True,
    )
    assert result.decision == "skip"
    assert "per_symbol_loss_cap_breached" in result.hard_blocks

def test_max_trade_risk_pct_caps_risk_amount(force_entry, gw):
    result = run_pipeline(
        _candidate(symbol="DSY", price=10.50),
        bars=_surge_bars(),
        vwap=10.20,
        ema9=10.10,
        day_high=10.55,
        quote_age_seconds=2.0,
        spread_pct=0.5,
        rvol=5.0,
        dollar_volume_5m=500_000,
        equity=100_000,
        execution_gw=gw,
        position_store=gw.positions,
        max_trade_risk_pct=0.005,
    )
    assert result.entry_risk_amount <= 100_000 * 0.005
```

- [ ] **Step 7: Verify**

Run: `pytest tests/test_phase6_risk.py tests/test_settings.py tests/test_phase10_app.py -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add config/settings.py main.py src/app.py src/decision_pipeline.py src/hard_filters.py src/sizing.py tests/test_phase6_risk.py tests/test_settings.py tests/test_phase10_app.py
git commit -m "fix: complete entry-side risk cap wiring"
```

---

### Task 6: Wire Snapshot Validation, Revalidate Sized Signals, And Recheck Quotes Before Submit

**Files:**
- Modify: `src/decision_pipeline.py`
- Modify: `src/app.py`
- Modify: `src/models/schemas.py` when a helper constructor makes the validation path smaller
- Test: `tests/test_phase9_pipeline.py`
- Test: `tests/test_phase10_app.py`

- [ ] **Step 1: Actually use `MarketSnapshot.validate_for_entry()`**

```python
valid, missing = snapshot.validate_for_entry()
if not valid:
    result = run_pipeline(
        snapshot.candidate,
        bars=snapshot.bars,
        vwap=snapshot.vwap,
        ema9=snapshot.ema9,
        day_high=snapshot.day_high,
        prior_hod=snapshot.prior_hod,
        quote_age_seconds=snapshot.quote_age_seconds,
        spread_pct=snapshot.spread_pct,
        rvol=snapshot.rvol,
        dollar_volume_5m=snapshot.dollar_volume_5m,
        execution_gw=self._execution,
        position_store=self._positions,
        logger=self._logger,
        equity=self._equity,
        starter_risk_pct=self._starter_risk_pct,
        max_trade_risk_pct=self._max_trade_risk_pct,
    )
    assert result.decision == "skip"
    assert all(m in result.hard_blocks or m in result.decision_reason for m in missing)
    continue
```

- [ ] **Step 2: Replace the unvalidated Pydantic copy path with validated reconstruction**

```python
sized_signal = EntrySignal.model_validate({
    **signal.model_dump(),
    "proposed_shares": shares,
    "risk_amount": risk_amount,
})
```

- [ ] **Step 3: Add a pre-submit quote recheck hook for real execution paths**

```python
if shares > 0 and execution_gw is not None and pre_submit_quote_fn is not None:
    refreshed = pre_submit_quote_fn(candidate)
    valid, missing = refreshed.validate_for_entry()
    if not valid or (refreshed.quote_age_seconds is not None and refreshed.quote_age_seconds > 5):
        result.decision = "watch"
        result.decision_reason = "stale_pre_submit_quote"
        return result
```

- [ ] **Step 4: Pass the recheck function from the app scan path**

```python
result = run_pipeline(
    snapshot.candidate,
    bars=snapshot.bars,
    vwap=snapshot.vwap,
    ema9=snapshot.ema9,
    day_high=snapshot.day_high,
    prior_hod=snapshot.prior_hod,
    quote_age_seconds=snapshot.quote_age_seconds,
    spread_pct=snapshot.spread_pct,
    rvol=snapshot.rvol,
    dollar_volume_5m=snapshot.dollar_volume_5m,
    execution_gw=self._execution,
    position_store=self._positions,
    logger=self._logger,
    equity=self._equity,
    starter_risk_pct=self._starter_risk_pct,
    max_trade_risk_pct=self._max_trade_risk_pct,
    pre_submit_quote_fn=self._market_data_fn,
)
```

- [ ] **Step 5: Add tests for all three behaviors**

```python
def test_validate_for_entry_is_used_when_snapshot_missing_price(force_entry, gw):
    snapshot = MarketSnapshot(candidate=_candidate(symbol="DSY", price=None), quote_age_seconds=2.0, spread_pct=0.5)
    valid, missing = snapshot.validate_for_entry()
    assert valid is False
    assert "invalid_or_missing_price" in missing

def test_sized_signal_revalidation_rejects_zero_share_update():
    with pytest.raises(ValidationError):
        EntrySignal.model_validate({
            "symbol": "DSY",
            "entry_setup": EntrySetup.FIRST_PULLBACK,
            "entry_price": 10.50,
            "stop_price": 10.40,
            "risk_per_share": 0.10,
            "invalidation": "below first pullback low",
            "proposed_shares": 0,
            "risk_amount": 0.0,
        })

def test_pre_submit_quote_recheck_blocks_stale_entry(tmp_path):
    logger = DecisionLogger(tmp_path / "decisions.jsonl")
    app = TradingApp(
        scanner_fn=lambda: [_c()],
        enrichment_fn=lambda c: c,
        market_data_fn=lambda c: MarketSnapshot(candidate=c, bars=_surge_bars(), quote_age_seconds=10.0, spread_pct=0.5),
        logger=logger,
    )
    app._scan_and_process()
    record = list(logger.read())[0]
    assert record.decision == "watch"
    assert record.reason == "stale_pre_submit_quote"
```

- [ ] **Step 6: Verify**

Run: `pytest tests/test_phase9_pipeline.py tests/test_phase10_app.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/decision_pipeline.py src/app.py src/models/schemas.py tests/test_phase9_pipeline.py tests/test_phase10_app.py
git commit -m "fix: validate snapshots and pre-submit quotes"
```

---

### Task 7: Finish Runtime Time-Gate And Market-Hours Behavior

**Files:**
- Modify: `src/app.py`
- Test: `tests/test_phase10_app.py`

- [ ] **Step 1: Extract time acquisition into a testable helper**

```python
def _now_et(self) -> datetime:
    return datetime.now(ZoneInfo("US/Eastern"))
```

- [ ] **Step 2: Use the helper everywhere the loop makes time decisions**

```python
now_et = self._now_et()
self._et_time = now_et.time()
```

- [ ] **Step 3: Add off-hours backoff when the market is closed and there are no open positions**

```python
if not self._is_market_open() and not self._positions.all_open():
    time.sleep(60.0)
    continue
```

Use a bounded backoff. Do not sleep through monitor obligations when positions exist.

- [ ] **Step 4: Add integration tests for watch-only, cutoff, flatten, weekend, and off-hours suppression**

```python
def test_watch_only_runtime_blocks_entries(monkeypatch, tmp_path):
    logger = DecisionLogger(tmp_path / "decisions.jsonl")
    app = TradingApp(
        scanner_fn=lambda: [_c()],
        enrichment_fn=lambda c: c,
        market_data_fn=lambda c: MarketSnapshot(candidate=c, bars=_surge_bars(), quote_age_seconds=2.0, spread_pct=0.5),
        logger=logger,
    )
    monkeypatch.setattr(app, "_now_et", lambda: datetime(2026, 6, 22, 9, 32, tzinfo=ZoneInfo("US/Eastern")))
    app._et_time = app._now_et().time()
    app._scan_and_process()
    rec = list(logger.read())[0]
    assert rec.decision == "skip"
    assert "watch_only" in rec.reason or "watch_only" in rec.hard_blocks
```

- [ ] **Step 5: Add a flatten-time monitor test**

```python
def test_flatten_time_runtime_triggers_exit(monkeypatch, tmp_path):
    logger = DecisionLogger(tmp_path / "decisions.jsonl")
    gw = PaperExecutionGateway()
    pos = PositionStateModel(symbol="DSY", state=PositionState.OPEN, entry_price=10.50, stop_price=10.30, current_shares=50, average_entry=10.50)
    gw.positions.upsert(pos)
    gw.place_stop("DSY", 10.30, 50)
    app = TradingApp(
        execution_gw=gw,
        logger=logger,
        market_data_fn=lambda c: MarketSnapshot(candidate=Candidate(symbol=c.symbol, price=10.60), bars=[Bar(10.50, 10.65, 10.45, 10.62, 2000)], quote_age_seconds=2.0, spread_pct=0.5),
    )
    monkeypatch.setattr(app, "_now_et", lambda: datetime(2026, 6, 22, 15, 55, tzinfo=ZoneInfo("US/Eastern")))
    app._et_time = app._now_et().time()
    app._monitor_positions()
    rec = list(logger.read())[0]
    assert rec.decision == "exit"
    assert "flatten_time" in rec.reason
```

- [ ] **Step 6: Verify**

Run: `pytest tests/test_phase10_app.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/app.py tests/test_phase10_app.py
git commit -m "fix: complete runtime time gate coverage"
```

---

### Task 8: Derive Classifier Features From Real Bars — Helper Layer

**Files:**
- Create: `src/classifier_features.py`
- Modify: `src/decision_pipeline.py`
- Test: `tests/test_phase4_classifier.py`

- [ ] **Step 1: Create one helper module for runtime-derived classifier inputs**

```python
@dataclass
class ClassifierFeatures:
    avg_range: float | None = None
    lower_highs_count: int = 0
    consecutive_below_vwap: int = 0
    higher_low_structure: bool = False
    strong_volume: bool = False
    volume_fading: bool = False
    bounces_failing: bool = False
    pullbacks_bought: bool = False
    vertical_move: bool = False
    vertical_without_pullback: bool = False
    price_moved_pct_5m: float | None = None
    pullback_low: float | None = None
    nearest_stop_distance_pct: float | None = None
    failed_hod_reclaim: bool = False
    failed_vwap_reclaim: bool = False
    hod_behavior_repeated: bool = False
    has_pullback_formed: bool = False
```

- [ ] **Step 2: Implement the first deterministic feature cluster**

```python
def derive_classifier_features(bars: list[Bar], *, price: float | None, vwap: float | None, day_high: float | None) -> ClassifierFeatures:
    closes = [bar.close for bar in bars]
    highs = [bar.high for bar in bars]
    lows = [bar.low for bar in bars]
    volumes = [bar.volume for bar in bars]
    features = ClassifierFeatures()
    features.avg_range = sum(bar.high - bar.low for bar in bars) / len(bars)
    features.lower_highs_count = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    features.consecutive_below_vwap = sum(1 for close in reversed(closes) if vwap is not None and close < vwap)
    features.price_moved_pct_5m = ((closes[-1] - closes[max(0, len(closes) - 5)]) / closes[max(0, len(closes) - 5)] * 100) if len(closes) >= 2 else None
    features.pullback_low = min(lows[-5:]) if len(lows) >= 5 else min(lows)
    features.has_pullback_formed = len(lows) >= 3 and min(lows[-3:]) < closes[-1]
    return features
```

- [ ] **Step 3: Implement the second feature cluster**

```python
features.strong_volume = volumes[-1] > max(volumes[:-1])
features.volume_fading = volumes[-1] < volumes[-2] < volumes[-3]
features.higher_low_structure = lows[-1] > lows[-2] > lows[-3]
features.pullbacks_bought = closes[-1] > closes[-2] and lows[-1] >= lows[-2]
```

Also implement: `vertical_move`, `vertical_without_pullback`, `failed_hod_reclaim`, `failed_vwap_reclaim`, `hod_behavior_repeated`, `bounces_failing`, and `nearest_stop_distance_pct` in this same helper.

- [ ] **Step 4: Add helper-unit tests before wiring the pipeline**

```python
def test_derive_classifier_features_detects_lower_highs():
    bars = [Bar(10.4, 10.5, 10.2, 10.3, 1000), Bar(10.2, 10.3, 10.0, 10.1, 900), Bar(10.0, 10.1, 9.8, 9.9, 800)]
    features = derive_classifier_features(bars, price=9.9, vwap=10.2, day_high=10.5)
    assert features.lower_highs_count >= 2
```

- [ ] **Step 5: Verify**

Run: `pytest tests/test_phase4_classifier.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/classifier_features.py src/decision_pipeline.py tests/test_phase4_classifier.py
git commit -m "feat: derive runtime classifier features"
```

---

### Task 9: Wire The Derived Features Into `run_pipeline()` And Replace Fake Runtime Tests

**Files:**
- Modify: `src/decision_pipeline.py`
- Modify: `src/move_classifier.py`
- Test: `tests/test_phase4_classifier.py`
- Test: `tests/test_phase9_pipeline.py`

- [ ] **Step 1: Feed the derived helper output into `classify_move_state()`**

```python
features = derive_classifier_features(
    bars,
    price=candidate.price,
    vwap=vwap,
    day_high=day_high,
)

state, mode, evidence = classify_move_state(
    price=candidate.price,
    day_high=day_high,
    vwap=vwap,
    ema9=ema9,
    spread_pct=spread_pct,
    rvol=rvol,
    avg_range=features.avg_range,
    lower_highs_count=features.lower_highs_count,
    consecutive_below_vwap=features.consecutive_below_vwap,
    higher_low_structure=features.higher_low_structure,
    strong_volume=features.strong_volume,
    volume_fading=features.volume_fading,
    bounces_failing=features.bounces_failing,
    pullbacks_bought=features.pullbacks_bought,
    vertical_move=features.vertical_move,
    vertical_without_pullback=features.vertical_without_pullback,
    price_moved_pct_5m=features.price_moved_pct_5m,
    pullback_low=features.pullback_low,
    nearest_stop_distance_pct=features.nearest_stop_distance_pct,
    failed_hod_reclaim=features.failed_hod_reclaim,
    failed_vwap_reclaim=features.failed_vwap_reclaim,
    hod_behavior_repeated=features.hod_behavior_repeated,
    has_pullback_formed=features.has_pullback_formed,
)
```

- [ ] **Step 2: Keep the VWAP-missing safeguard intact**

```python
if vwap is None:
    # missing VWAP may degrade confidence, but it may not manufacture BACKSIDE
```

- [ ] **Step 3: Replace injected-feature tests with true runtime-path tests**

```python
def test_runtime_path_reaches_active_from_real_bars(force_entry, gw):
    candidate = _candidate(symbol="DSY", price=10.50)
    result = run_pipeline(
        candidate,
        bars=_surge_bars(),
        vwap=10.30,
        ema9=10.20,
        day_high=10.55,
        spread_pct=0.3,
        rvol=5.0,
        dollar_volume_5m=500_000,
        quote_age_seconds=2.0,
        execution_gw=gw,
        position_store=gw.positions,
    )
    assert result.move_state == MoveState.ACTIVE
```

- [ ] **Step 4: Add the missing lockout regression test**

```python
def test_vwap_missing_and_spread_over_one_pct_does_not_force_backside(force_entry, gw):
    candidate = _candidate(symbol="DSY", price=10.50)
    result = run_pipeline(
        candidate,
        bars=_surge_bars(),
        vwap=None,
        ema9=10.20,
        day_high=10.55,
        spread_pct=1.2,
        rvol=5.0,
        dollar_volume_5m=500_000,
        quote_age_seconds=2.0,
        execution_gw=gw,
        position_store=gw.positions,
    )
    assert result.move_state != MoveState.BACKSIDE
```

- [ ] **Step 5: Verify setup reachability, not just raw classifier output**

```python
def test_micro_pullback_becomes_runtime_reachable(force_entry, gw):
    candidate = _candidate(symbol="DSY", price=10.50)
    result = run_pipeline(
        candidate,
        bars=_micro_pullback_bars(),
        vwap=10.30,
        ema9=10.20,
        day_high=10.60,
        spread_pct=0.3,
        rvol=5.0,
        dollar_volume_5m=500_000,
        quote_age_seconds=2.0,
        execution_gw=gw,
        position_store=gw.positions,
    )
    assert result.entry_signal is not None
    assert result.entry_signal.entry_setup == EntrySetup.MICRO_PULLBACK
```

- [ ] **Step 6: Verify**

Run: `pytest tests/test_phase4_classifier.py tests/test_phase9_pipeline.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/decision_pipeline.py src/move_classifier.py tests/test_phase4_classifier.py tests/test_phase9_pipeline.py
git commit -m "feat: wire classifier from runtime bars"
```

---

### Task 10: Populate Float Data And Clean Up Annotation Truth

**Files:**
- Modify: `src/scanner/enrichment.py`
- Modify: `src/scanner/scanner.py`
- Modify: `src/annotations.py`
- Test: `tests/test_phase2_attention.py`
- Test: `tests/test_phase9_pipeline.py`

- [ ] **Step 1: Add bounded float enrichment from yfinance**

```python
def enrich_float_shares(symbol: str) -> int | None:
    import yfinance as yf
    info = yf.Ticker(symbol).info or {}
    raw = info.get("floatShares")
    return int(raw) if raw else None
```

- [ ] **Step 2: Populate `Candidate.float_shares` after Finviz discovery**

```python
candidate = Candidate(
    symbol=row.ticker,
    price=price_val,
    percent_gain=row.change_pct if row.change_pct != 0.0 else None,
    current_volume=row.volume if row.volume > 0 else None,
    sector=row.sector if row.sector else None,
    industry=row.industry if row.industry else None,
    country=row.country if row.country else None,
    exchange=row.exchange if row.exchange else None,
    market_cap=row.market_cap if row.market_cap > 0.0 else None,
    float_shares=enrich_float_shares(row.ticker),
    source="finviz",
    source_timestamp=now,
)
```

- [ ] **Step 3: Decide news and catalyst truth explicitly**

```python
# No real news or catalyst source is wired yet.
# Keep unknowns as annotation-only and remove any implication that a production no_news penalty is active.
```

- [ ] **Step 4: Add tests for populated float and unchanged unknown semantics**

```python
def test_float_enrichment_removes_universal_float_unknown():
    candidate = Candidate(symbol="DSY", price=10.50, float_shares=12_000_000)
    warnings = map_soft_warnings(candidate, price_range_min=1.0, price_range_max=50.0, quote_age_seconds=2.0, spread_pct=0.5, data_confidence=0.9)
    assert "float_unknown" not in warnings

def test_news_unknown_remains_annotation_only_without_source():
    candidate = Candidate(symbol="DSY", price=10.50, float_shares=12_000_000)
    warnings = map_soft_warnings(candidate, has_news=None, has_catalyst=None)
    assert "news_unknown" in warnings
    assert soft_warning_multiplier(warnings, attention_score=80) == 1.0
```

- [ ] **Step 5: Verify**

Run: `pytest tests/test_phase2_attention.py tests/test_phase9_pipeline.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/scanner/enrichment.py src/scanner/scanner.py src/annotations.py tests/test_phase2_attention.py tests/test_phase9_pipeline.py
git commit -m "feat: populate float data for sizing"
```

---

### Task 11: Add Real Parser Coverage And Fix Weak Assertions

**Files:**
- Modify: `tests/test_phase7_execution.py`
- Modify: `tests/test_phase9_pipeline.py`
- Add or Modify: `tests/test_phase2_scanner.py`
- Add or Modify: `tests/test_phase2_attention.py`

- [ ] **Step 1: Add mocked Finviz HTML parser tests**

```python
def test_scrape_finviz_gainers_parses_expected_columns(monkeypatch):
    class FakeResponse:
        def __init__(self, status_code: int, text: str):
            self.status_code = status_code
            self.text = text

    html = """
    <table class='styled-table-new'>
      <tr><td>No</td><td>Ticker</td><td>Company</td><td>Sector</td><td>Industry</td><td>Country</td><td>Market Cap</td><td>P/E</td><td>Price</td><td>Change</td><td>Volume</td></tr>
      <tr><td>1</td><td>DSY</td><td>Demo Sys</td><td>Technology</td><td>Software</td><td>USA</td><td>500M</td><td>-</td><td>10.50</td><td>25.0%</td><td>5.2M</td></tr>
    </table>
    """
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(200, html))
    rows = scrape_finviz_gainers()
    assert "DSY" in rows
    assert rows["DSY"].price == 10.5
```

- [ ] **Step 2: Add `_finviz_is_stale()` boundary tests**

```python
def test_finviz_is_stale_trips_at_eighty_percent_zero_rows():
    rows = {
        "A": FinvizRow(ticker="A", change_pct=0.0, volume=0),
        "B": FinvizRow(ticker="B", change_pct=0.0, volume=0),
        "C": FinvizRow(ticker="C", change_pct=0.0, volume=0),
        "D": FinvizRow(ticker="D", change_pct=0.0, volume=1000),
        "E": FinvizRow(ticker="E", change_pct=10.0, volume=1000),
    }
    assert _finviz_is_stale(rows) is True

def test_finviz_is_stale_skips_small_result_sets():
    rows = {
        "A": FinvizRow(ticker="A", change_pct=0.0, volume=0),
        "B": FinvizRow(ticker="B", change_pct=0.0, volume=0),
    }
    assert _finviz_is_stale(rows) is False
```

- [ ] **Step 3: Add mocked yfinance watchlist tests**

```python
def test_scrape_yfinance_gainers_filters_etfs_and_keeps_equities(monkeypatch):
    rows = scrape_yfinance_gainers()
    assert isinstance(rows, dict)
```

- [ ] **Step 4: Strengthen execution tests instead of state-only checks**

```python
def test_confirm_exit_rejected_sets_error_and_preserves_shares(alpaca_gw, mock_alpaca_client):
    self._open_position(alpaca_gw, mock_alpaca_client)
    mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="exit-1")
    exit_order, _ = alpaca_gw.submit_exit("DSY", "test")
    mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(id="exit-1", status="rejected")
    with pytest.raises(RuntimeError):
        alpaca_gw.confirm_exit_fill(exit_order.order_id)
    pos = alpaca_gw.positions.get("DSY")
    assert pos.state == PositionState.ERROR
    assert pos.current_shares == 50
    assert len(alpaca_gw.pending) == 0
```

- [ ] **Step 5: Remove bogus assertions that always pass**

```python
other_warnings = [warning for warning in result.soft_warnings if warning != "no_news"]
assert mult_with == mult_without

assert "former_runner" in result.attention_drivers
```

Replace:
- `other_warnings = [Ellipsis]` (current behavior produced by a literal list containing `Ellipsis`)
- `assert "former_runner" in result.attention_drivers or True`

- [ ] **Step 6: Verify**

Run: `pytest tests/test_phase2_scanner.py tests/test_phase2_attention.py tests/test_phase7_execution.py tests/test_phase9_pipeline.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add tests/test_phase2_scanner.py tests/test_phase2_attention.py tests/test_phase7_execution.py tests/test_phase9_pipeline.py
git commit -m "test: strengthen parser and lifecycle coverage"
```

---

### Task 12: Extract Shared Enrichment Math Last

**Files:**
- Modify: `src/market_data.py`
- Modify: `src/market_data_sim.py`
- Test: existing market-data and app tests only

- [ ] **Step 1: Extract a shared enrichment helper**

```python
def derive_bar_enrichment(bars: list[Bar]) -> tuple[float | None, float | None, float | None, float | None]:
    total_volume = sum(bar.volume for bar in bars)
    vwap = (sum(bar.close * bar.volume for bar in bars) / total_volume) if total_volume > 0 else None
    day_high = max((bar.high for bar in bars), default=None)
    unique_highs = sorted({bar.high for bar in bars}, reverse=True)
    prior_hod = unique_highs[1] if len(unique_highs) >= 2 else day_high
    dollar_volume_5m = sum(bar.close * bar.volume for bar in bars[-5:]) if bars else None
    return vwap, day_high, prior_hod, dollar_volume_5m
```

- [ ] **Step 2: Call it from both live and sim snapshot builders**

```python
vwap, day_high, prior_hod, dollar_volume_5m = derive_bar_enrichment(bars)
```

- [ ] **Step 3: Keep EMA9 sharing exactly once**

```python
ema9 = _compute_ema(close_prices, 9)
```

- [ ] **Step 4: Verify**

Run: `pytest tests/test_phase10_app.py tests/test_phase9_pipeline.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/market_data.py src/market_data_sim.py
git commit -m "refactor: share market enrichment math"
```

---

### Task 13: Update `SPEC.md` To Match Reality After The Code Is Fixed

**Files:**
- Modify: `SPEC.md`

- [ ] **Step 1: Re-audit every currently false COMPLETED claim against the fixed code**

```markdown
- T4.3 must be true in both monitor and scan paths.
- T5.1 must either be wired or removed.
- T6.3 and T6.6 must be true in real loop mode, not only in tests.
- T8.1 and T8.2 wording must match actual assertion strength.
```

- [ ] **Step 2: Update deferred-task dependencies to match reality**

```markdown
- T5.4 does not actually depend on T5.2.
- T2.7 is no longer blocked by T4.4 after the risk-cap wiring is done.
```

- [ ] **Step 3: Update the acceptance criteria only after proof exists in code and tests**

```markdown
- Do not claim runtime integration when only unit coverage exists.
- Do not claim state, share, and pending-order assertions unless tests actually assert them.
```

- [ ] **Step 4: Verify**

Run: `pytest tests/ -x -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add SPEC.md
git commit -m "docs: make spec completion claims truthful"
```

---

## Ordered Execution Summary

1. `Task 1` — runtime broker snapshot and persistence wiring.
2. `Task 2` — broker-side stop cancellation.
3. `Task 3` — partial-exit reprotection and EXITING failure rollback.
4. `Task 4` — synthetic-zero removal and fresh-data hard-stop gating (`T3.6`).
5. `Task 5` — scan-path per-symbol cap and configurable `max_trade_risk_pct`.
6. `Task 6` — snapshot validation, `T2.7`, and pre-submit quote recheck (`T5.8`).
7. `Task 7` — market-hours and time-gate runtime completion and tests.
8. `Task 8` — classifier feature helper layer.
9. `Task 9` — classifier pipeline wiring and runtime reachability tests (`T5.4`).
10. `Task 10` — float enrichment and annotation truth cleanup (`T5.6`).
11. `Task 11` — parser coverage and weak-test cleanup (`T5.5` plus test-truthfulness repairs).
12. `Task 12` — shared enrichment math cleanup (`T5.2`).
13. `Task 13` — final `SPEC.md` truthfulness pass.

## Final Verification

```bash
pytest tests/ -x -q
```

Expected: `749 passed` baseline or higher, never lower.
